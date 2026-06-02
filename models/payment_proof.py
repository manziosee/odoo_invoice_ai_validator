import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentProof(models.Model):
    _name = 'payment.proof'
    _description = 'AI Invoice Payment Validator'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        readonly=True,
        default='New',
        copy=False,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Client',
        tracking=True,
        help='Optional — narrow the invoice search to this client.',
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        required=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Assigned to',
        default=lambda self: self.env.user,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('analyzing', 'Analyzing…'),
        ('matched', 'Match Found'),
        ('validated', 'Validated'),
        ('error', 'Error'),
    ], default='draft', string='Status', tracking=True, copy=False)

    # ── Proof of payment document ──────────────────────────────────────────
    proof_file = fields.Binary(string='Proof of Payment', attachment=True)
    proof_filename = fields.Char(string='Filename')
    proof_mimetype = fields.Char(string='MIME type', compute='_compute_mimetype', store=True)

    # ── AI extraction results ──────────────────────────────────────────────
    extracted_payer = fields.Char(string='Extracted Payer', readonly=True)
    extracted_amount = fields.Float(string='Extracted Amount', readonly=True, digits=(16, 2))
    extracted_currency = fields.Char(string='Extracted Currency', readonly=True)
    extracted_date = fields.Date(string='Extracted Payment Date', readonly=True)
    extracted_reference = fields.Char(string='Extracted Reference', readonly=True)
    extracted_bank = fields.Char(string='Extracted Bank / Account', readonly=True)
    extracted_raw = fields.Text(string='AI Raw Response', readonly=True)

    # ── Matched invoice ────────────────────────────────────────────────────
    matched_invoice_id = fields.Many2one(
        'account.move',
        string='Matched Invoice',
        domain=[('move_type', 'in', ('out_invoice', 'out_receipt')),
                ('payment_state', 'in', ('not_paid', 'partial'))],
        tracking=True,
    )
    match_confidence = fields.Float(string='Match Confidence %', readonly=True)
    match_notes = fields.Text(string='Match Reasoning', readonly=True)

    # ── Error / notes ──────────────────────────────────────────────────────
    validation_error = fields.Text(string='Error Details', readonly=True)
    notes = fields.Text(string='Notes')

    # ── Computed helpers shown in form view ────────────────────────────────
    invoice_amount_residual = fields.Monetary(
        string='Invoice Balance Due',
        related='matched_invoice_id.amount_residual',
        currency_field='invoice_currency_id',
        readonly=True,
    )
    invoice_currency_id = fields.Many2one(
        related='matched_invoice_id.currency_id',
        readonly=True,
    )
    invoice_partner_id = fields.Many2one(
        related='matched_invoice_id.partner_id',
        readonly=True,
    )

    # ──────────────────────────────────────────────────────────────────────
    # Compute
    # ──────────────────────────────────────────────────────────────────────

    @api.depends('proof_filename')
    def _compute_mimetype(self):
        import mimetypes
        for rec in self:
            if rec.proof_filename:
                mime, _ = mimetypes.guess_type(rec.proof_filename)
                rec.proof_mimetype = mime or 'application/octet-stream'
            else:
                rec.proof_mimetype = False

    # ──────────────────────────────────────────────────────────────────────
    # ORM overrides
    # ──────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('payment.proof') or 'New'
        return super().create(vals_list)

    # ──────────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────────

    def action_analyze(self):
        """Send the proof to Groq, extract payment info, find the best invoice match."""
        self.ensure_one()
        if not self.proof_file:
            raise UserError(_('Please upload a proof of payment document first.'))

        self.write({'state': 'analyzing', 'validation_error': False})
        self.message_post(body=_('AI analysis started…'))

        try:
            extracted = self._extract_with_groq()
            self._write_extracted(extracted)
            invoice, score, notes = self._match_invoice(extracted)
            self.write({
                'matched_invoice_id': invoice.id if invoice else False,
                'match_confidence': score,
                'match_notes': notes,
                'state': 'matched' if invoice else 'error',
                'validation_error': False if invoice else _(
                    'No unpaid invoice matched the extracted payment details.\n\n'
                    'Extracted: payer=%s, amount=%s %s, date=%s, ref=%s\n\n'
                    'You can manually select the correct invoice below.'
                ) % (
                    extracted.get('payer_name', '?'),
                    extracted.get('amount', '?'),
                    extracted.get('currency', ''),
                    extracted.get('date', '?'),
                    extracted.get('reference', '?'),
                ),
            })
            if invoice:
                self.message_post(body=_(
                    'AI matched invoice <b>%s</b> (%.0f%% confidence).<br/>%s'
                ) % (invoice.name, score, notes or ''))
            else:
                self.message_post(body=_('No invoice match found. Please select manually.'))
        except Exception as exc:
            _logger.exception('AI analysis failed for payment.proof %s', self.id)
            self.write({'state': 'error', 'validation_error': str(exc)})
            self.message_post(body=_('Analysis failed: %s') % exc)

    def action_open_validate_wizard(self):
        """Open the confirmation wizard before registering the payment."""
        self.ensure_one()
        if not self.matched_invoice_id:
            raise UserError(_('Please select or confirm the matched invoice first.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Validate Payment'),
            'res_model': 'validate.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_proof_id': self.id},
        }

    def action_validate_payment(self):
        """Register the payment directly on the matched invoice using Odoo's standard wizard."""
        self.ensure_one()
        invoice = self.matched_invoice_id
        if not invoice:
            raise UserError(_('No invoice matched. Cannot validate.'))
        if invoice.payment_state == 'paid':
            raise UserError(_('Invoice %s is already fully paid.') % invoice.name)

        ctx = {
            'active_model': 'account.move',
            'active_ids': [invoice.id],
        }
        amount = self.extracted_amount or invoice.amount_residual
        date = self.extracted_date or fields.Date.today()
        ref = self.extracted_reference or invoice.name

        wizard = self.env['account.payment.register'].with_context(**ctx).create({
            'amount': amount,
            'payment_date': date,
            'communication': ref,
        })
        wizard.action_create_payments()

        self.write({'state': 'validated'})
        self.message_post(body=_(
            'Payment of <b>%.2f</b> registered on invoice <b>%s</b> (ref: %s, date: %s).'
        ) % (amount, invoice.name, ref, date))

    def action_reset(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'extracted_payer': False,
            'extracted_amount': 0,
            'extracted_currency': False,
            'extracted_date': False,
            'extracted_reference': False,
            'extracted_bank': False,
            'extracted_raw': False,
            'matched_invoice_id': False,
            'match_confidence': 0,
            'match_notes': False,
            'validation_error': False,
        })

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _get_groq_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param(
            'odoo_invoice_ai_validator.groq_api_key', ''
        )
        if not key:
            raise UserError(_(
                'Groq API key is not configured.\n'
                'Go to Accounting → Configuration → Settings → AI Payment Validator.'
            ))
        return key

    def _extract_with_groq(self):
        from ..services.groq_service import extract_payment_info
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = self._get_groq_api_key()
        model = ICP.get_param(
            'odoo_invoice_ai_validator.groq_model',
            'llama-3.2-11b-vision-preview',
        )
        import base64
        file_bytes = base64.b64decode(self.proof_file)
        result = extract_payment_info(
            file_content=file_bytes,
            filename=self.proof_filename or 'proof.pdf',
            api_key=api_key,
            model=model,
        )
        return result

    def _write_extracted(self, data):
        import json
        self.write({
            'extracted_payer': data.get('payer_name') or False,
            'extracted_amount': data.get('amount') or 0.0,
            'extracted_currency': data.get('currency') or False,
            'extracted_date': self._parse_date(data.get('date')),
            'extracted_reference': data.get('reference') or False,
            'extracted_bank': data.get('bank_info') or False,
            'extracted_raw': json.dumps(data, indent=2, default=str),
        })

    def _parse_date(self, value):
        if not value:
            return False
        import datetime
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d %b %Y', '%B %d, %Y'):
            try:
                return datetime.datetime.strptime(str(value).strip(), fmt).date()
            except ValueError:
                continue
        return False

    def _match_invoice(self, extracted):
        from ..services.invoice_matcher import find_matching_invoice
        return find_matching_invoice(
            env=self.env,
            extracted=extracted,
            partner_id=self.partner_id.id if self.partner_id else None,
        )
