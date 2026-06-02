import base64
import datetime
import hashlib
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentProof(models.Model):
    _name = 'payment.proof'
    _description = 'AI Invoice Payment Validator'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    # ── Identity ───────────────────────────────────────────────────────────
    name = fields.Char(string='Reference', readonly=True, default='New', copy=False, tracking=True)
    proof_type = fields.Selection([
        ('customer_payment', 'Customer Payment'),
        ('vendor_bill', 'Vendor Bill'),
    ], string='Type', default='customer_payment', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Client', tracking=True,
        help='Optional — narrows invoice search to this client.')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    user_id = fields.Many2one('res.users', string='Assigned to', default=lambda self: self.env.user)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('queued', 'Queued'),
        ('analyzing', 'Analyzing'),
        ('matched', 'Match Found'),
        ('validated', 'Validated'),
        ('error', 'Error'),
    ], default='draft', string='Status', tracking=True, copy=False)

    # ── Proof document ─────────────────────────────────────────────────────
    proof_file = fields.Binary(string='Proof of Payment', attachment=True)
    proof_filename = fields.Char(string='Filename')
    proof_mimetype = fields.Char(string='MIME type', compute='_compute_mimetype', store=True)
    proof_hash = fields.Char(string='File Hash (MD5)', compute='_compute_hash', store=True, index=True)
    is_duplicate = fields.Boolean(string='Possible Duplicate', compute='_compute_is_duplicate', store=True)
    duplicate_of_id = fields.Many2one('payment.proof', string='Duplicate of', readonly=True)

    # ── AI extraction results ──────────────────────────────────────────────
    extracted_payer = fields.Char(string='Extracted Payer', readonly=True)
    extracted_amount = fields.Float(string='Extracted Amount', readonly=True, digits=(16, 2))
    extracted_currency = fields.Char(string='Extracted Currency', readonly=True)
    extracted_date = fields.Date(string='Extracted Payment Date', readonly=True)
    extracted_reference = fields.Char(string='Extracted Reference', readonly=True)
    extracted_bank = fields.Char(string='Extracted Bank / Account', readonly=True)
    extracted_raw = fields.Text(string='AI Raw Response', readonly=True)

    # ── Multi-invoice matching ─────────────────────────────────────────────
    matched_invoice_ids = fields.Many2many(
        'account.move', 'payment_proof_invoice_rel', 'proof_id', 'invoice_id',
        string='All Matched Invoices', readonly=True,
    )
    matched_invoice_id = fields.Many2one(
        'account.move', string='Primary Invoice', tracking=True,
        help='Highest-scoring match. Accountant can change this before validating.',
    )
    match_confidence = fields.Float(string='AI Confidence %', readonly=True)
    match_notes = fields.Text(string='Match Reasoning', readonly=True)
    match_score_breakdown = fields.Text(string='Score Breakdown', readonly=True)
    matched_invoice_count = fields.Integer(compute='_compute_matched_count', string='Matches Found')

    # ── Retry / error ──────────────────────────────────────────────────────
    validation_error = fields.Text(string='Error Details', readonly=True)
    retry_count = fields.Integer(string='Retry Count', default=0, readonly=True)
    retry_at = fields.Datetime(string='Next Retry At', readonly=True)
    notes = fields.Text(string='Notes')
    process_async = fields.Boolean(string='Process in Background', default=True)

    # ── Computed helpers shown in form ─────────────────────────────────────
    invoice_amount_residual = fields.Monetary(
        string='Invoice Balance Due', related='matched_invoice_id.amount_residual',
        currency_field='invoice_currency_id', readonly=True,
    )
    invoice_currency_id = fields.Many2one(related='matched_invoice_id.currency_id', readonly=True)
    invoice_partner_id = fields.Many2one(related='matched_invoice_id.partner_id', readonly=True)

    # ──────────────────────────────────────────────────────────────────────
    # Computes
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

    @api.depends('proof_file')
    def _compute_hash(self):
        for rec in self:
            if rec.proof_file:
                try:
                    raw = base64.b64decode(rec.proof_file)
                    rec.proof_hash = hashlib.md5(raw).hexdigest()
                except Exception:
                    rec.proof_hash = False
            else:
                rec.proof_hash = False

    @api.depends('proof_hash')
    def _compute_is_duplicate(self):
        for rec in self:
            if rec.proof_hash:
                existing = self.search([
                    ('proof_hash', '=', rec.proof_hash),
                    ('id', '!=', rec.id or 0),
                    ('state', 'not in', ('draft',)),
                ], limit=1)
                rec.is_duplicate = bool(existing)
                rec.duplicate_of_id = existing.id if existing else False
            else:
                rec.is_duplicate = False
                rec.duplicate_of_id = False

    @api.depends('matched_invoice_ids')
    def _compute_matched_count(self):
        for rec in self:
            rec.matched_invoice_count = len(rec.matched_invoice_ids)

    # ──────────────────────────────────────────────────────────────────────
    # ORM
    # ──────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('payment.proof') or 'New'
        return super().create(vals_list)

    # Email alias — create proof from incoming email attachment
    @api.model
    def message_new(self, msg_dict, custom_values=None):
        custom_values = custom_values or {}
        attachments = msg_dict.get('attachments', [])
        if not attachments:
            return super().message_new(msg_dict, custom_values)

        # First attachment becomes the proof file
        att = attachments[0]
        fname = att[0] if att else 'proof.pdf'
        fcontent = att[1] if len(att) > 1 else b''
        if isinstance(fcontent, str):
            fcontent = fcontent.encode()

        vals = dict(custom_values)
        vals.update({
            'proof_file': base64.b64encode(fcontent).decode(),
            'proof_filename': fname,
            'process_async': True,
            'state': 'queued',
        })
        record = super().message_new(msg_dict, vals)
        record.message_post(body=_('Proof received via email from %s.') % msg_dict.get('email_from', '?'))
        return record

    # ──────────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────────

    def action_analyze(self):
        self.ensure_one()
        if not self.proof_file:
            raise UserError(_('Please upload a proof of payment document first.'))
        if self.is_duplicate:
            raise UserError(_(
                'This file looks like a duplicate of %s. '
                'Click "Mark as Not Duplicate" if you want to proceed.'
            ) % self.duplicate_of_id.name)
        if self.process_async:
            self.write({'state': 'queued', 'validation_error': False, 'retry_count': 0})
            self.message_post(body=_('Queued for background AI processing.'))
        else:
            self._do_analyze()

    def _do_analyze(self):
        self.write({'state': 'analyzing', 'validation_error': False})
        try:
            extracted = self._extract_with_groq()
            self._write_extracted(extracted)

            # Auto-fill partner from payer name if client field is empty
            if not self.partner_id and extracted.get('payer_name'):
                partner = self._find_partner(extracted['payer_name'])
                if partner:
                    self.write({'partner_id': partner.id})
                    self.message_post(
                        body=_('Client auto-detected from document: <b>%s</b>') % partner.name
                    )

            matches = self._match_invoices(extracted)

            if matches:
                best = matches[0]
                self.write({
                    'matched_invoice_id': best[0].id,
                    'matched_invoice_ids': [(6, 0, [m[0].id for m in matches])],
                    'match_confidence': min(best[1], 100.0),
                    'match_notes': best[2],
                    'match_score_breakdown': self._format_score_breakdown(matches),
                    'state': 'matched',
                    'validation_error': False,
                    'retry_count': 0,
                    'retry_at': False,
                })
                self.message_post(body=_(
                    'AI found <b>%d</b> matching invoice(s). '
                    'Best: <b>%s</b> (%.0f%% confidence).'
                ) % (len(matches), best[0].name, best[1]))
                self._log_audit('matched', matches)
            else:
                msg = _(
                    'No unpaid invoice matched.\n'
                    'Payer: %s | Amount: %s %s | Date: %s | Ref: %s\n'
                    'Select the invoice manually below.'
                ) % (
                    extracted.get('payer_name', '?'), extracted.get('amount', '?'),
                    extracted.get('currency', ''), extracted.get('date', '?'),
                    extracted.get('reference', '?'),
                )
                self.write({'state': 'error', 'validation_error': msg})
                self.message_post(body=_('No invoice match found. Please select manually.'))
                self._log_audit('no_match', [])

        except Exception as exc:
            _logger.exception('AI analysis failed for payment.proof %s', self.id)
            retry_count = self.retry_count + 1
            retry_at = fields.Datetime.now() + datetime.timedelta(minutes=15 * retry_count)
            self.write({
                'state': 'error',
                'validation_error': str(exc),
                'retry_count': retry_count,
                'retry_at': retry_at if retry_count <= 3 else False,
            })
            self.message_post(body=_('Analysis failed (attempt %d): %s') % (retry_count, exc))
            self._log_audit('error', [])

    def action_open_validate_wizard(self):
        self.ensure_one()
        if not self.matched_invoice_id and not self.matched_invoice_ids:
            raise UserError(_('No matched invoice. Select one manually first.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Validate Payment'),
            'res_model': 'validate.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_proof_id': self.id},
        }

    def action_reset(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'extracted_payer': False, 'extracted_amount': 0,
            'extracted_currency': False, 'extracted_date': False,
            'extracted_reference': False, 'extracted_bank': False,
            'extracted_raw': False,
            'matched_invoice_id': False,
            'matched_invoice_ids': [(5,)],
            'match_confidence': 0, 'match_notes': False,
            'match_score_breakdown': False,
            'validation_error': False,
            'retry_count': 0, 'retry_at': False,
        })

    def action_process_now(self):
        """Bypass the queue — run AI analysis immediately."""
        self.ensure_one()
        if not self.proof_file:
            raise UserError(_('No file uploaded.'))
        self._do_analyze()

    def action_mark_not_duplicate(self):
        self.ensure_one()
        self.write({'is_duplicate': False, 'duplicate_of_id': False})
        self.message_post(body=_('Duplicate warning dismissed by %s.') % self.env.user.name)

    def action_open_bulk_upload(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bulk Upload Proofs'),
            'res_model': 'bulk.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    # ── Cron jobs ──────────────────────────────────────────────────────────

    @api.model
    def cron_process_queue(self):
        records = self.search([('state', '=', 'queued'), ('proof_file', '!=', False)], limit=20)
        for rec in records:
            try:
                rec._do_analyze()
                self.env.cr.commit()
            except Exception as exc:
                _logger.error('cron_process_queue: %s failed: %s', rec.name, exc)
                self.env.cr.rollback()

    @api.model
    def cron_retry_failed(self):
        now = fields.Datetime.now()
        records = self.search([
            ('state', '=', 'error'),
            ('retry_count', '>', 0),
            ('retry_count', '<=', 3),
            ('retry_at', '<=', now),
            ('proof_file', '!=', False),
        ], limit=10)
        for rec in records:
            try:
                rec.message_post(body=_('Auto-retry attempt %d…') % (rec.retry_count + 1))
                rec._do_analyze()
                self.env.cr.commit()
            except Exception as exc:
                _logger.error('cron_retry_failed: %s failed: %s', rec.name, exc)
                self.env.cr.rollback()

    # ── Private helpers ────────────────────────────────────────────────────

    def _get_groq_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param(
            'odoo_invoice_ai_validator.groq_api_key', '')
        if not key:
            raise UserError(_(
                'Groq API key not configured.\n'
                'Go to Accounting → Configuration → Settings → AI Payment Validator.'
            ))
        return key

    def _extract_with_groq(self):
        from ..services.groq_service import extract_payment_info
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = self._get_groq_api_key()
        model = ICP.get_param('odoo_invoice_ai_validator.groq_model', 'llama-3.2-11b-vision-preview')
        max_retries = int(ICP.get_param('odoo_invoice_ai_validator.groq_max_retries', '3'))
        file_bytes = base64.b64decode(self.proof_file)
        return extract_payment_info(
            file_content=file_bytes,
            filename=self.proof_filename or 'proof.pdf',
            api_key=api_key,
            model=model,
            max_retries=max_retries,
        )

    def _find_partner(self, payer_name):
        """Try to find a matching res.partner from the extracted payer name."""
        if not payer_name:
            return False
        # Exact match first
        partner = self.env['res.partner'].search(
            [('name', '=ilike', payer_name.strip())], limit=1
        )
        if partner:
            return partner
        # Partial word match — split payer name and search for any word
        words = [w for w in payer_name.split() if len(w) > 3]
        for word in words:
            partner = self.env['res.partner'].search(
                [('name', 'ilike', word), ('active', '=', True)], limit=1
            )
            if partner:
                return partner
        return False

    def _write_extracted(self, data):
        # Combine reference + invoice numbers for display
        ref = data.get('reference') or ''
        inv_nums = data.get('invoice_numbers') or []
        if isinstance(inv_nums, list) and inv_nums:
            extra = ' | '.join(n for n in inv_nums if n not in ref)
            if extra:
                ref = (ref + ' | ' + extra).strip(' |')

        # Combine description + notes for bank field display
        desc = ' | '.join(filter(None, [
            data.get('description'),
            data.get('payment_method'),
            data.get('bank_info'),
        ]))

        self.write({
            'extracted_payer': data.get('payer_name') or False,
            'extracted_amount': data.get('amount') or 0.0,
            'extracted_currency': data.get('currency') or False,
            'extracted_date': self._parse_date(data.get('date')),
            'extracted_reference': ref or False,
            'extracted_bank': desc or False,
            'extracted_raw': json.dumps(data, indent=2, default=str),
        })

    def _parse_date(self, value):
        if not value:
            return False
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d %b %Y', '%B %d, %Y'):
            try:
                return datetime.datetime.strptime(str(value).strip(), fmt).date()
            except ValueError:
                continue
        return False

    def _match_invoices(self, extracted):
        from ..services.invoice_matcher import find_matching_invoices
        return find_matching_invoices(
            env=self.env,
            extracted=extracted,
            partner_id=self.partner_id.id if self.partner_id else None,
            proof_type=self.proof_type,
        )

    def _format_score_breakdown(self, matches):
        lines = [f'{m[0].name}: {m[1]:.0f}pts — {m[2]}' for m in matches[:5]]
        return '\n'.join(lines)

    def _log_audit(self, action, matches):
        self.env['payment.proof.audit'].create({
            'proof_id': self.id,
            'action': action,
            'actor_id': self.env.user.id,
            'extracted_data': self.extracted_raw,
            'score_breakdown': self.match_score_breakdown,
            'invoices_considered': json.dumps([m[0].name for m in matches]),
            'result': matches[0][0].name if matches else 'none',
        })
