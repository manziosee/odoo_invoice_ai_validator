from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='odoo_invoice_ai_validator.groq_api_key',
        help='Get a free key at https://console.groq.com',
    )
    groq_model = fields.Selection([
        ('llama-3.2-11b-vision-preview', 'Llama 3.2 11B Vision (recommended)'),
        ('llama-3.2-90b-vision-preview', 'Llama 3.2 90B Vision (highest accuracy)'),
        ('llama-3.1-70b-versatile', 'Llama 3.1 70B Versatile (text only)'),
        ('mixtral-8x7b-32768', 'Mixtral 8x7B (text only)'),
    ], string='Groq Model',
        config_parameter='odoo_invoice_ai_validator.groq_model',
        default='llama-3.2-11b-vision-preview',
    )
    groq_max_retries = fields.Integer(
        string='Max Retries on Groq Error',
        config_parameter='odoo_invoice_ai_validator.groq_max_retries',
        default=3,
    )
    match_amount_tolerance = fields.Float(
        string='Amount Match Tolerance (%)',
        config_parameter='odoo_invoice_ai_validator.match_amount_tolerance',
        default=2.0,
    )
    telegram_token = fields.Char(
        string='Telegram Bot Token',
        config_parameter='odoo_invoice_ai_validator.telegram_token',
    )
    twilio_sid = fields.Char(
        string='Twilio Account SID',
        config_parameter='odoo_invoice_ai_validator.twilio_sid',
    )
    twilio_token = fields.Char(
        string='Twilio Auth Token',
        config_parameter='odoo_invoice_ai_validator.twilio_token',
    )
