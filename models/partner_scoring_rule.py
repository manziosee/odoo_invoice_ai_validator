from odoo import models, fields, api


class PaymentPartnerRule(models.Model):
    _name = 'payment.partner.rule'
    _description = 'Per-Partner Invoice Matching Rules'
    _order = 'partner_id'

    partner_id = fields.Many2one('res.partner', required=True, string='Client', index=True, ondelete='cascade')
    active = fields.Boolean(default=True)

    # Score weights (override module defaults)
    reference_weight = fields.Float(string='Reference Score Weight', default=50.0,
        help='Points awarded when payment reference matches invoice. Default: 50.')
    amount_weight = fields.Float(string='Amount Score Weight', default=30.0,
        help='Max points awarded when amount is within tolerance. Default: 30.')
    amount_tolerance = fields.Float(string='Amount Tolerance %', default=2.0,
        help='Max % difference between extracted and invoice amount to count as match.')
    name_weight = fields.Float(string='Name Score Weight', default=15.0,
        help='Max points for payer name matching partner name. Default: 15.')

    # Reference pattern for this client
    reference_pattern = fields.Char(
        string='Reference Pattern (regex)',
        help='Optional regex. If the extracted reference matches this, award full reference score. '
             'Example for UNICEF: r"UNICEF|PROG-\\d+"',
    )

    # Stats
    correction_count = fields.Integer(compute='_compute_correction_count', string='Corrections Made')
    notes = fields.Text(string='Notes')

    @api.depends('partner_id')
    def _compute_correction_count(self):
        for rec in self:
            rec.correction_count = self.env['payment.proof.correction'].search_count([
                ('partner_id', '=', rec.partner_id.id),
            ])

    def name_get(self):
        return [(r.id, f'Rules for {r.partner_id.name}') for r in self]
