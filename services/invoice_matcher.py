"""
Invoice matching logic.

Scoring (0-100):
  50 pts  — extracted reference appears in invoice name, payment reference, or narration
  30 pts  — extracted amount is within tolerance of invoice's amount_residual
  15 pts  — partner name fuzzy-matches extracted payer name
   5 pts  — extracted date is within 30 days of invoice date

Returns the best match above the threshold (≥ 25 pts).
"""

import logging
import re
from typing import Optional

_logger = logging.getLogger(__name__)
_SCORE_THRESHOLD = 25  # minimum confidence to consider a match


def find_matching_invoice(env, extracted: dict, partner_id: Optional[int] = None):
    """
    Returns (account.move | empty_recordset, score: float, notes: str).
    """
    payer_name = (extracted.get('payer_name') or '').strip()
    amount = _to_float(extracted.get('amount'))
    reference = (extracted.get('reference') or '').strip()
    pay_date = extracted.get('date')

    tolerance_pct = float(
        env['ir.config_parameter'].sudo().get_param(
            'odoo_invoice_ai_validator.match_amount_tolerance', '2.0'
        )
    )

    # ── Build base domain: only unpaid customer invoices ──────────────────
    domain = [
        ('move_type', 'in', ('out_invoice', 'out_receipt')),
        ('payment_state', 'in', ('not_paid', 'partial')),
        ('state', '=', 'posted'),
    ]
    if partner_id:
        domain.append(('partner_id', '=', partner_id))

    invoices = env['account.move'].search(domain, limit=200)
    if not invoices:
        return env['account.move'], 0.0, 'No unpaid customer invoices found.'

    # ── Score each invoice ────────────────────────────────────────────────
    scored = []
    for inv in invoices:
        score = 0
        reasons = []

        # Reference match (highest weight)
        if reference:
            haystack = ' '.join(filter(None, [inv.name, inv.ref, inv.narration or ''])).lower()
            ref_clean = re.sub(r'\s+', ' ', reference.lower())
            if ref_clean in haystack:
                score += 50
                reasons.append(f'Reference "{reference}" found in invoice.')
            elif _partial_ref_match(ref_clean, haystack):
                score += 25
                reasons.append(f'Partial reference match on "{reference}".')

        # Amount match
        if amount and inv.amount_residual > 0:
            diff_pct = abs(amount - inv.amount_residual) / inv.amount_residual * 100
            if diff_pct <= tolerance_pct:
                pts = int(30 * (1 - diff_pct / tolerance_pct)) + 1
                score += pts
                reasons.append(
                    f'Amount {amount:,.2f} ≈ invoice balance {inv.amount_residual:,.2f} '
                    f'({diff_pct:.1f}% diff).'
                )

        # Partner / payer name match
        if payer_name and inv.partner_id:
            sim = _name_similarity(payer_name, inv.partner_id.name or '')
            if sim >= 0.7:
                pts = int(15 * sim)
                score += pts
                reasons.append(
                    f'Payer "{payer_name}" matches partner "{inv.partner_id.name}" '
                    f'({sim * 100:.0f}% similarity).'
                )

        # Date proximity
        if pay_date and inv.invoice_date:
            import datetime
            try:
                if isinstance(pay_date, str):
                    pay_date_obj = datetime.date.fromisoformat(pay_date)
                else:
                    pay_date_obj = pay_date
                delta = abs((pay_date_obj - inv.invoice_date).days)
                if delta <= 30:
                    pts = max(1, 5 - delta // 6)
                    score += pts
                    reasons.append(f'Payment date is {delta} day(s) from invoice date.')
            except Exception:
                pass

        if score >= _SCORE_THRESHOLD:
            scored.append((inv, score, ' '.join(reasons)))

    if not scored:
        return (
            env['account.move'],
            0.0,
            f'No invoice reached the confidence threshold ({_SCORE_THRESHOLD} pts). '
            f'Checked {len(invoices)} unpaid invoice(s).',
        )

    scored.sort(key=lambda x: x[1], reverse=True)
    best_inv, best_score, best_notes = scored[0]
    _logger.info(
        'invoice_matcher: best match is %s with score %.0f/100',
        best_inv.name, best_score,
    )
    return best_inv, min(best_score, 100.0), best_notes


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _to_float(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _partial_ref_match(ref: str, haystack: str) -> bool:
    # Strip non-alphanumeric and check substring
    r = re.sub(r'\W', '', ref)
    h = re.sub(r'\W', '', haystack)
    return bool(r) and r in h


def _name_similarity(a: str, b: str) -> float:
    """Simple token overlap similarity (0-1), works without external libs."""
    def tokens(s):
        return set(re.sub(r'[^\w\s]', '', s.lower()).split())
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
