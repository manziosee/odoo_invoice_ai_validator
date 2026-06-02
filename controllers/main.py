"""
Public endpoints:
  GET/POST /payment-proof/upload   — mobile-friendly public upload form
  POST     /payment-proof/webhook/telegram   — Telegram bot webhook
  POST     /payment-proof/webhook/whatsapp   — WhatsApp webhook (Twilio)
"""

import base64
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_PUBLIC_FORM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Upload Proof of Payment</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f4f6f9; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; padding: 16px; }}
    .card {{ background: #fff; border-radius: 12px; padding: 32px;
             box-shadow: 0 4px 24px rgba(0,0,0,.08); max-width: 480px; width: 100%; }}
    h1 {{ font-size: 1.4rem; color: #1a1a2e; margin-bottom: 8px; }}
    p  {{ color: #666; font-size: .9rem; margin-bottom: 24px; line-height: 1.5; }}
    label {{ display: block; font-size: .85rem; font-weight: 600;
             color: #444; margin-bottom: 6px; }}
    input, select {{ width: 100%; padding: 10px 12px; border: 1px solid #ddd;
                     border-radius: 8px; font-size: .95rem; margin-bottom: 16px; }}
    input[type=file] {{ padding: 8px; }}
    button {{ width: 100%; background: #714B67; color: #fff; border: none;
              padding: 12px; border-radius: 8px; font-size: 1rem;
              cursor: pointer; font-weight: 600; }}
    button:hover {{ background: #5c3d55; }}
    .success {{ color: #2e7d32; background: #e8f5e9; border-radius: 8px;
                padding: 16px; text-align: center; }}
    .error   {{ color: #c62828; background: #ffebee; border-radius: 8px;
                padding: 16px; text-align: center; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Upload Proof of Payment</h1>
    <p>Send your payment receipt or bank transfer confirmation.
       Our system will match it to your invoice automatically.</p>
    {message}
    <form method="POST" enctype="multipart/form-data" action="/payment-proof/upload">
      <label for="partner_name">Your Name / Company</label>
      <input type="text" id="partner_name" name="partner_name"
             placeholder="e.g. UNICEF Rwanda" required/>

      <label for="proof_file">Proof of Payment (PDF, PNG, JPG)</label>
      <input type="file" id="proof_file" name="proof_file"
             accept=".pdf,.png,.jpg,.jpeg,.webp" required/>

      <label for="notes">Notes (optional)</label>
      <input type="text" id="notes" name="notes"
             placeholder="Invoice number, reference, etc."/>

      <button type="submit">Submit Proof of Payment</button>
    </form>
  </div>
</body>
</html>
"""


class PaymentProofController(http.Controller):

    # ── Public upload ─────────────────────────────────────────────────────

    @http.route('/payment-proof/upload', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, website=False)
    def public_upload(self, **kwargs):
        if request.httprequest.method == 'GET':
            return request.make_response(
                _PUBLIC_FORM_HTML.format(message=''),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )

        # POST
        try:
            file_obj = request.httprequest.files.get('proof_file')
            if not file_obj:
                return self._html_response(_PUBLIC_FORM_HTML, 'No file was uploaded.', error=True)

            file_bytes = file_obj.read()
            if not file_bytes:
                return self._html_response(_PUBLIC_FORM_HTML, 'The uploaded file is empty.', error=True)

            partner_name = kwargs.get('partner_name', '').strip()
            notes = kwargs.get('notes', '').strip()

            env = request.env(user=request.env.ref('base.user_root').id)
            partner = env['res.partner'].search([('name', 'ilike', partner_name)], limit=1)

            proof = env['payment.proof'].sudo().create({
                'proof_file': base64.b64encode(file_bytes).decode(),
                'proof_filename': file_obj.filename,
                'partner_id': partner.id if partner else False,
                'notes': notes or False,
                'process_async': True,
                'state': 'queued',
            })
            proof.sudo().message_post(
                body=f'Proof uploaded via public form by: {partner_name or "anonymous"}'
            )

            msg = (
                f'<div class="success">'
                f'Thank you! Your proof of payment has been received.<br/>'
                f'Reference: <strong>{proof.name}</strong><br/>'
                f'We will process it shortly and update your invoice.'
                f'</div>'
            )
            return request.make_response(
                _PUBLIC_FORM_HTML.format(message=msg),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )
        except Exception as exc:
            _logger.exception('Public upload failed')
            return self._html_response(_PUBLIC_FORM_HTML, str(exc), error=True)

    # ── Telegram webhook ──────────────────────────────────────────────────

    @http.route('/payment-proof/webhook/telegram', type='json', auth='public',
                methods=['POST'], csrf=False)
    def telegram_webhook(self, **kwargs):
        try:
            data = json.loads(request.httprequest.data.decode())
            self._handle_telegram_update(data)
        except Exception as exc:
            _logger.exception('Telegram webhook error: %s', exc)
        return {'ok': True}

    def _handle_telegram_update(self, data):
        import requests as req
        ICP = request.env['ir.config_parameter'].sudo()
        token = ICP.get_param('odoo_invoice_ai_validator.telegram_token', '')
        if not token:
            return

        message = data.get('message', {})
        chat_id = message.get('chat', {}).get('id')
        if not chat_id:
            return

        def send(text):
            req.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                timeout=10,
            )

        # Check for photo or document attachment
        file_id = None
        filename = 'proof.jpg'
        if message.get('photo'):
            file_id = message['photo'][-1]['file_id']
        elif message.get('document'):
            doc = message['document']
            file_id = doc['file_id']
            filename = doc.get('file_name', 'proof.pdf')

        if not file_id:
            send('Please send a photo or PDF of your proof of payment.')
            return

        # Download file from Telegram
        file_info_resp = req.get(
            f'https://api.telegram.org/bot{token}/getFile',
            params={'file_id': file_id}, timeout=10,
        )
        file_path = file_info_resp.json().get('result', {}).get('file_path', '')
        if not file_path:
            send('Could not retrieve the file. Please try again.')
            return

        file_resp = req.get(
            f'https://api.telegram.org/file/bot{token}/{file_path}', timeout=30
        )
        file_bytes = file_resp.content

        env = request.env(user=request.env.ref('base.user_root').id)
        proof = env['payment.proof'].sudo().create({
            'proof_file': base64.b64encode(file_bytes).decode(),
            'proof_filename': filename,
            'notes': f'Received via Telegram chat {chat_id}',
            'process_async': True,
            'state': 'queued',
        })
        send(
            f'✅ Proof received! Reference: <b>{proof.name}</b>\n'
            f'We are processing it. You will be notified once matched.'
        )

    # ── WhatsApp webhook (Twilio) ─────────────────────────────────────────

    @http.route('/payment-proof/webhook/whatsapp', type='http', auth='public',
                methods=['POST'], csrf=False)
    def whatsapp_webhook(self, **kwargs):
        try:
            form = request.httprequest.form
            num_media = int(form.get('NumMedia', 0))
            from_number = form.get('From', 'unknown')

            if num_media == 0:
                return self._twilio_reply('Please send a photo or PDF of your proof of payment.')

            import requests as req
            ICP = request.env['ir.config_parameter'].sudo()
            twilio_sid = ICP.get_param('odoo_invoice_ai_validator.twilio_sid', '')
            twilio_token = ICP.get_param('odoo_invoice_ai_validator.twilio_token', '')

            media_url = form.get('MediaUrl0', '')
            media_type = form.get('MediaContentType0', 'image/jpeg')
            filename = f'whatsapp_proof.{"pdf" if "pdf" in media_type else "jpg"}'

            file_resp = req.get(media_url, auth=(twilio_sid, twilio_token), timeout=30)
            file_bytes = file_resp.content

            env = request.env(user=request.env.ref('base.user_root').id)
            proof = env['payment.proof'].sudo().create({
                'proof_file': base64.b64encode(file_bytes).decode(),
                'proof_filename': filename,
                'notes': f'Received via WhatsApp from {from_number}',
                'process_async': True,
                'state': 'queued',
            })
            return self._twilio_reply(
                f'✅ Proof received! Ref: {proof.name}\n'
                f'We are processing it and will update your invoice shortly.'
            )
        except Exception as exc:
            _logger.exception('WhatsApp webhook error')
            return self._twilio_reply('An error occurred. Please try again or contact us directly.')

    # ── Helpers ───────────────────────────────────────────────────────────

    def _html_response(self, template, message, error=False):
        css = 'error' if error else 'success'
        msg = f'<div class="{css}">{message}</div>'
        return request.make_response(
            template.format(message=msg),
            headers=[('Content-Type', 'text/html; charset=utf-8')],
        )

    def _twilio_reply(self, text):
        xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{text}</Message></Response>'
        return request.make_response(
            xml, headers=[('Content-Type', 'text/xml')]
        )
