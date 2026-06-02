"""
Groq AI integration — extracts payment information from proof-of-payment documents.
Supports images (vision), PDFs, and plain text. Includes exponential-backoff retry.
"""

import base64
import json
import logging
import re
import time

_logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a payment document analyzer. Extract key payment information and return ONLY a valid JSON object — no markdown, no explanation:

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
- date must be ISO YYYY-MM-DD; null if not found
- If a field is not found, use null
- Do NOT wrap in markdown code fences
"""

# Errors that are worth retrying (rate limit, server error)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def extract_payment_info(
    file_content: bytes,
    filename: str,
    api_key: str,
    model: str,
    max_retries: int = 3,
) -> dict:
    """
    Main entry. Returns extracted payment dict.
    Retries on transient Groq errors with exponential backoff.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return _do_extract(file_content, filename, api_key, model)
        except _RetryableError as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = 2 ** attempt
                _logger.warning('Groq transient error (attempt %d/%d), retrying in %ds: %s',
                                attempt, max_retries, delay, exc)
                time.sleep(delay)
        except Exception:
            raise
    raise RuntimeError(f'Groq failed after {max_retries} attempts: {last_exc}')


# ──────────────────────────────────────────────────────────────────────────────
# Internal
# ──────────────────────────────────────────────────────────────────────────────

class _RetryableError(Exception):
    pass


def _do_extract(file_content: bytes, filename: str, api_key: str, model: str) -> dict:
    try:
        from groq import Groq
        from groq import RateLimitError, APIStatusError
    except ImportError:
        raise RuntimeError(
            'The "groq" Python package is not installed.\n'
            'Run inside the container: pip install groq'
        )

    client = Groq(api_key=api_key)
    mime = _guess_mime(filename)

    try:
        if mime.startswith('image/'):
            raw = _call_vision(client, model, file_content, mime)
        elif mime == 'application/pdf':
            text = _extract_pdf_text(file_content)
            raw = _call_text(client, model, text)
        else:
            try:
                text = file_content.decode('utf-8', errors='replace')
            except Exception:
                text = repr(file_content[:4000])
            raw = _call_text(client, model, text)
    except RateLimitError as exc:
        raise _RetryableError(str(exc)) from exc
    except APIStatusError as exc:
        if exc.status_code in _RETRYABLE_STATUS:
            raise _RetryableError(str(exc)) from exc
        raise

    return _parse_response(raw)


def _call_vision(client, model: str, image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
                {'type': 'text', 'text': 'Extract all payment information from this document.'},
            ]},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ''


def _call_text(client, model: str, text: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': f'Extract payment information from:\n\n{text[:8000]}'},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ''


def _extract_pdf_text(pdf_bytes: bytes) -> str:
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

    readable = re.sub(rb'[^\x20-\x7E\n\r\t]', b' ', pdf_bytes)
    return readable.decode('ascii', errors='replace')[:8000]


def _parse_response(raw: str) -> dict:
    if not raw:
        raise RuntimeError('Groq returned an empty response.')
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f'Groq response was not valid JSON.\nRaw:\n{raw[:400]}\nError: {exc}'
        )
    if 'amount' in data and data['amount'] is not None:
        try:
            data['amount'] = float(str(data['amount']).replace(',', '').strip())
        except (ValueError, TypeError):
            data['amount'] = 0.0
    return data


def _guess_mime(filename: str) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(filename or '')
    if mime:
        return mime
    ext = (filename or '').rsplit('.', 1)[-1].lower()
    return {
        'pdf': 'application/pdf', 'png': 'image/png',
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'webp': 'image/webp', 'gif': 'image/gif',
    }.get(ext, 'text/plain')
