from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='odoo_invoice_ai_validator.groq_api_key',
        help='Get a free key at https://console.groq.com',
    )
    groq_model = fields.Selection([
        ('meta-llama/llama-4-scout-17b-16e-instruct', 'Llama 4 Scout 17B Vision (recommended)'),
        ('meta-llama/llama-4-maverick-17b-128e-instruct', 'Llama 4 Maverick 17B Vision'),
        ('llama-3.3-70b-versatile', 'Llama 3.3 70B Versatile (text only)'),
        ('llama-3.1-8b-instant', 'Llama 3.1 8B Instant (fast, text only)'),
        ('qwen/qwen3-32b', 'Qwen3 32B (text only)'),
    ], string='Groq Model',
        config_parameter='odoo_invoice_ai_validator.groq_model',
        default='meta-llama/llama-4-scout-17b-16e-instruct',
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
