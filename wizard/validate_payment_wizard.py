from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ValidatePaymentWizard(models.TransientModel):
    _name = 'validate.payment.wizard'
    _description = 'Confirm and Register Payment'

    proof_id = fields.Many2one('payment.proof', required=True, ondelete='cascade')

    # Read-only summary fields shown in the wizard
    invoice_id = fields.Many2one(related='proof_id.matched_invoice_id', readonly=True)
    invoice_name = fields.Char(related='proof_id.matched_invoice_id.name', readonly=True)
    invoice_partner = fields.Char(related='proof_id.matched_invoice_id.partner_id.name', readonly=True)
    invoice_amount_residual = fields.Monetary(
        related='proof_id.matched_invoice_id.amount_residual',
        currency_field='currency_id',
        readonly=True,
    )
    currency_id = fields.Many2one(related='proof_id.matched_invoice_id.currency_id', readonly=True)
    match_confidence = fields.Float(related='proof_id.match_confidence', readonly=True)
    match_notes = fields.Text(related='proof_id.match_notes', readonly=True)

    # Editable payment details (pre-filled from extraction, user can adjust)
    payment_amount = fields.Float(string='Payment Amount', digits=(16, 2))
    payment_date = fields.Date(string='Payment Date', default=fields.Date.today)
    payment_reference = fields.Char(string='Payment Reference / Memo')
    journal_id = fields.Many2one(
        'account.journal',
        string='Payment Journal',
        domain=[('type', 'in', ('bank', 'cash'))],
    )

    @api.onchange('proof_id')
    def _onchange_proof(self):
        if self.proof_id:
            self.payment_amount = self.proof_id.extracted_amount or self.proof_id.matched_invoice_id.amount_residual
            self.payment_date = self.proof_id.extracted_date or fields.Date.today()
            self.payment_reference = self.proof_id.extracted_reference or self.proof_id.matched_invoice_id.name
            # Default to first bank/cash journal of the company
            journal = self.env['account.journal'].search([
                ('type', 'in', ('bank', 'cash')),
                ('company_id', '=', self.proof_id.company_id.id),
            ], limit=1)
            self.journal_id = journal

    def action_confirm(self):
        self.ensure_one()
        invoice = self.proof_id.matched_invoice_id
        if not invoice:
            raise UserError(_('No invoice linked.'))
        if not self.payment_amount or self.payment_amount <= 0:
            raise UserError(_('Payment amount must be greater than zero.'))

        ctx = {
            'active_model': 'account.move',
            'active_ids': [invoice.id],
        }
        wizard = self.env['account.payment.register'].with_context(**ctx).create({
            'amount': self.payment_amount,
            'payment_date': self.payment_date,
            'communication': self.payment_reference or invoice.name,
            'journal_id': self.journal_id.id if self.journal_id else False,
        })
        wizard.action_create_payments()

        self.proof_id.write({'state': 'validated'})
        self.proof_id.message_post(body=_(
            'Payment of <b>%.2f</b> registered on invoice <b>%s</b> via journal "%s" (ref: %s, date: %s).'
        ) % (
            self.payment_amount,
            invoice.name,
            self.journal_id.name if self.journal_id else '—',
            self.payment_reference or '—',
            self.payment_date,
        ))
        return {'type': 'ir.actions.act_window_close'}
