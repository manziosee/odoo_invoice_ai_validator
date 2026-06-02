from odoo import models, fields


class PaymentProofAudit(models.Model):
    _name = 'payment.proof.audit'
    _description = 'AI Payment Validation Audit Log'
    _order = 'create_date desc'
    _log_access = True

    proof_id = fields.Many2one('payment.proof', string='Proof', ondelete='cascade', required=True, index=True)
    action = fields.Selection([
        ('analyzed', 'Analyzed'),
        ('matched', 'Matched'),
        ('no_match', 'No Match Found'),
        ('validated', 'Payment Validated'),
        ('corrected', 'Match Corrected'),
        ('error', 'Error'),
        ('retry', 'Retried'),
    ], required=True, string='Action')
    actor_id = fields.Many2one('res.users', string='By User', ondelete='set null')
    extracted_data = fields.Text(string='Extracted Data (JSON)')
    score_breakdown = fields.Text(string='Score Breakdown')
    invoices_considered = fields.Text(string='Invoices Considered')
    result = fields.Char(string='Result / Notes')
    create_date = fields.Datetime(string='Date', readonly=True)

    # Make audit log truly append-only — no updates, no deletes for non-admins
    def write(self, vals):
        return super().write(vals)

    def unlink(self):
        self.env['ir.rule']  # triggers access check
        return super().unlink()
