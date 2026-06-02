"""
Groq AI integration for extracting payment information from proof-of-payment documents.

Supports:
  - Images (PNG, JPG, WEBP) — sent directly as base64 vision input
  - PDFs — text extracted first, then sent as text prompt
  - Text files — sent directly
"""

import base64
import json
import logging
import re

_logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a payment document analyzer. Your job is to extract key payment information from a proof of payment document.

Extract the following fields and return ONLY a valid JSON object — no markdown, no explanation, just raw JSON:

{
  "payer_name": "full name or company of the person/entity who made the payment",
  "amount": 12345.67,
  "currency": "USD",
  "date": "YYYY-MM-DD",
  "reference": "payment reference, transaction ID, or invoice number mentioned",
  "bank_info": "bank name, account number, or SWIFT/IBAN if visible",
  "beneficiary": "name of the recipient / payee if visible",
  "notes": "any other relevant details"
}

Rules:
- amount must be a number (float), not a string
- date must be ISO format YYYY-MM-DD if possible; null if not found
- If a field is not found, use null
- Do NOT wrap in ```json``` or any markdown
- Extract exactly what is written in the document — do not infer or guess
"""


def extract_payment_info(file_content: bytes, filename: str, api_key: str, model: str) -> dict:
    """
    Main entry point. Returns a dict with extracted payment fields.
    Raises RuntimeError with a human-readable message on failure.
    """
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError(
            'The "groq" Python package is not installed.\n'
            'Run inside the container: pip install groq'
        )

    client = Groq(api_key=api_key)
    mime = _guess_mime(filename)

    if mime.startswith('image/'):
        raw = _call_vision(client, model, file_content, mime)
    elif mime == 'application/pdf':
        text = _extract_pdf_text(file_content)
        raw = _call_text(client, model, text)
    else:
        # Plain text / CSV / unknown → treat as text
        try:
            text = file_content.decode('utf-8', errors='replace')
        except Exception:
            text = repr(file_content[:4000])
        raw = _call_text(client, model, text)

    return _parse_response(raw)


# ──────────────────────────────────────────────────────────────────────────────
# Groq API calls
# ──────────────────────────────────────────────────────────────────────────────

def _call_vision(client, model: str, image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f'data:{mime};base64,{b64}'
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'image_url',
                        'image_url': {'url': data_url},
                    },
                    {
                        'type': 'text',
                        'text': 'Extract all payment information from this proof of payment document.',
                    },
                ],
            },
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ''


def _call_text(client, model: str, text: str) -> str:
    # Truncate to avoid token limits — keep the first 8000 chars (plenty for a payment slip)
    text = text[:8000]
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': f'Extract all payment information from the following document text:\n\n{text}',
            },
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ''


# ──────────────────────────────────────────────────────────────────────────────
# PDF text extraction — graceful fallback chain
# ──────────────────────────────────────────────────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    # Try pdfminer.six first
    try:
        from pdfminer.high_level import extract_text
        import io
        text = extract_text(io.BytesIO(pdf_bytes))
        if text and text.strip():
            return text
    except ImportError:
        _logger.debug('pdfminer.six not installed, trying PyMuPDF')
    except Exception as exc:
        _logger.debug('pdfminer failed: %s', exc)

    # Fallback: PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        text = '\n'.join(page.get_text() for page in doc)
        if text and text.strip():
            return text
    except ImportError:
        _logger.debug('PyMuPDF not installed')
    except Exception as exc:
        _logger.debug('PyMuPDF failed: %s', exc)

    # Last resort: raw byte scan for readable ASCII
    readable = re.sub(rb'[^\x20-\x7E\n\r\t]', b' ', pdf_bytes)
    return readable.decode('ascii', errors='replace')[:8000]


# ──────────────────────────────────────────────────────────────────────────────
# Response parsing
# ──────────────────────────────────────────────────────────────────────────────

def _parse_response(raw: str) -> dict:
    if not raw:
        raise RuntimeError('Groq returned an empty response.')

    # Strip markdown code fences if the model added them anyway
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # Find the first { ... } block
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f'Groq response was not valid JSON.\nRaw response:\n{raw[:500]}\nError: {exc}'
        )

    # Normalise amount to float
    if 'amount' in data and data['amount'] is not None:
        try:
            data['amount'] = float(str(data['amount']).replace(',', '').strip())
        except (ValueError, TypeError):
            data['amount'] = 0.0

    return data


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _guess_mime(filename: str) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(filename or '')
    if mime:
        return mime
    ext = (filename or '').rsplit('.', 1)[-1].lower()
    return {
        'pdf': 'application/pdf',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'webp': 'image/webp',
        'gif': 'image/gif',
    }.get(ext, 'text/plain')
