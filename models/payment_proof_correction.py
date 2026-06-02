from odoo import models, fields, api


class PaymentProofCorrection(models.Model):
    """Stores every time an accountant overrides the AI's invoice match.
    The invoice_matcher uses this history to boost scores for similar future proofs.
    """
    _name = 'payment.proof.correction'
    _description = 'AI Match Correction History'
    _order = 'create_date desc'

    proof_id = fields.Many2one('payment.proof', string='Proof', ondelete='set null')
    partner_id = fields.Many2one('res.partner', string='Client', required=True, index=True)
    original_invoice_id = fields.Many2one('account.move', string='Original AI Match')
    correct_invoice_id = fields.Many2one('account.move', string='Correct Invoice', required=True)
    extracted_reference = fields.Char(string='Reference Extracted by AI')
    extracted_amount = fields.Float(string='Amount Extracted by AI', digits=(16, 2))
    corrected_by = fields.Many2one('res.users', string='Corrected By', default=lambda self: self.env.user)
    notes = fields.Text(string='Reason for Correction')
    create_date = fields.Datetime(string='Date', readonly=True)

    @api.model
    def record_correction(self, proof, correct_invoice):
        """Call this when an accountant picks a different invoice than the AI suggested."""
        if not proof.partner_id:
            return
        self.create({
            'proof_id': proof.id,
            'partner_id': proof.partner_id.id,
            'original_invoice_id': proof.matched_invoice_id.id if proof.matched_invoice_id else False,
            'correct_invoice_id': correct_invoice.id,
            'extracted_reference': proof.extracted_reference,
            'extracted_amount': proof.extracted_amount,
        })
