from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ValidatePaymentWizard(models.TransientModel):
    _name = 'validate.payment.wizard'
    _description = 'Confirm and Register Payment'

    proof_id = fields.Many2one('payment.proof', required=True, ondelete='cascade')

    # Summary (read-only)
    match_confidence = fields.Float(related='proof_id.match_confidence', readonly=True)
    match_notes = fields.Text(related='proof_id.match_notes', readonly=True)
    matched_invoice_count = fields.Integer(related='proof_id.matched_invoice_count', readonly=True)

    # Invoices to pay — pre-filled with all AI matches; accountant can remove or add
    invoice_ids = fields.Many2many(
        'account.move',
        'validate_wizard_invoice_rel', 'wizard_id', 'invoice_id',
        string='Invoices to Pay',
        domain=[('payment_state', 'in', ('not_paid', 'partial')), ('state', '=', 'posted')],
    )

    # Payment details
    payment_date = fields.Date(string='Payment Date', default=fields.Date.today, required=True)
    payment_reference = fields.Char(string='Payment Reference / Memo')
    journal_id = fields.Many2one(
        'account.journal', string='Payment Journal',
        domain=[('type', 'in', ('bank', 'cash'))],
        required=True,
    )
    currency_id = fields.Many2one('res.currency', string='Currency')

    # Partial payment
    use_extracted_amount = fields.Boolean(
        string='Use Extracted Amount',
        default=False,
        help='Pay the amount extracted from the proof instead of the full invoice balance.',
    )
    extracted_amount = fields.Float(related='proof_id.extracted_amount', readonly=True)

    @api.onchange('proof_id')
    def _onchange_proof(self):
        if not self.proof_id:
            return
        proof = self.proof_id
        self.invoice_ids = proof.matched_invoice_ids or (proof.matched_invoice_id if proof.matched_invoice_id else False)
        self.payment_date = proof.extracted_date or fields.Date.today()
        self.payment_reference = proof.extracted_reference or (proof.matched_invoice_id.name if proof.matched_invoice_id else False)
        journal = self.env['account.journal'].search([
            ('type', 'in', ('bank', 'cash')),
            ('company_id', '=', proof.company_id.id),
        ], limit=1)
        self.journal_id = journal
        if proof.matched_invoice_id:
            self.currency_id = proof.matched_invoice_id.currency_id

        # Auto-detect partial payment:
        # if AI extracted an amount AND it is less than the invoice balance → partial
        inv = proof.matched_invoice_id
        if inv and proof.extracted_amount and proof.extracted_amount > 0:
            tolerance = inv.amount_residual * 0.01  # 1% rounding tolerance
            if proof.extracted_amount < (inv.amount_residual - tolerance):
                self.use_extracted_amount = True

    @api.onchange('invoice_ids')
    def _onchange_invoice_ids(self):
        # Record correction if accountant changed the AI selection
        proof = self.proof_id
        if proof and proof.matched_invoice_id and self.invoice_ids:
            ai_ids = set((proof.matched_invoice_ids or proof.matched_invoice_id).ids)
            chosen_ids = set(self.invoice_ids.ids)
            new_ids = chosen_ids - ai_ids
            for inv_id in new_ids:
                inv = self.env['account.move'].browse(inv_id)
                self.env['payment.proof.correction'].record_correction(proof, inv)

    def action_confirm(self):
        self.ensure_one()
        if not self.invoice_ids:
            raise UserError(_('Select at least one invoice to pay.'))
        if not self.journal_id:
            raise UserError(_('Select a payment journal.'))

        paid_invoices = []
        for invoice in self.invoice_ids:
            if invoice.payment_state == 'paid':
                continue

            amount = invoice.amount_residual
            extracted = self.extracted_amount or 0.0
            # Use extracted amount when: toggle is on, single invoice, and amount is a valid partial
            if self.use_extracted_amount and len(self.invoice_ids) == 1 and 0 < extracted < invoice.amount_residual:
                amount = extracted
            elif self.use_extracted_amount and len(self.invoice_ids) == 1 and extracted >= invoice.amount_residual:
                amount = invoice.amount_residual  # cap at full balance — can't overpay

            ctx = {
                'active_model': 'account.move',
                'active_ids': [invoice.id],
            }
            wizard_vals = {
                'amount': amount,
                'payment_date': self.payment_date,
                'communication': self.payment_reference or invoice.name,
                'journal_id': self.journal_id.id,
            }
            if self.currency_id:
                wizard_vals['currency_id'] = self.currency_id.id

            reg_wizard = self.env['account.payment.register'].with_context(**ctx).create(wizard_vals)
            reg_wizard.action_create_payments()
            paid_invoices.append(invoice)

        self.proof_id.write({'state': 'validated'})
        inv_names = ', '.join(i.name for i in paid_invoices)
        self.proof_id.message_post(body=_(
            'Payment registered on %d invoice(s): %s<br/>'
            'Journal: %s | Date: %s | Ref: %s'
        ) % (
            len(paid_invoices), inv_names,
            self.journal_id.name, self.payment_date, self.payment_reference or '—',
        ))

        # Send notifications to salesperson
        from ..services.notifier import notify_payment_validated
        for invoice in paid_invoices:
            try:
                notify_payment_validated(self.proof_id, invoice)
            except Exception:
                pass

        # Audit log
        import json
        self.env['payment.proof.audit'].create({
            'proof_id': self.proof_id.id,
            'action': 'validated',
            'actor_id': self.env.user.id,
            'invoices_considered': json.dumps([i.name for i in paid_invoices]),
            'result': f'Paid via {self.journal_id.name}',
        })

        return {'type': 'ir.actions.act_window_close'}
