"""
Invoice matching engine — v2.

Scoring per invoice (0–100 pts):
  50  Reference found in invoice name / ref / narration (per-partner weight overridable)
  30  Amount within tolerance after currency conversion (per-partner weight overridable)
  15  Payer name fuzzy-matches partner name
   5  Payment date within 30 days of invoice date
  +10 Correction bonus: this invoice was previously corrected for the same partner+ref

Min threshold to propose a match: 25 pts.
Returns list of (account.move, score, notes) sorted by score desc.
"""

import logging
import re
from typing import Optional, List, Tuple

_logger = logging.getLogger(__name__)
_SCORE_THRESHOLD = 25


def find_matching_invoices(
    env,
    extracted: dict,
    partner_id: Optional[int] = None,
    proof_type: str = 'customer_payment',
) -> List[Tuple]:
    """Returns list of (account.move, score, notes), best first. Empty list if none."""

    payer_name = (extracted.get('payer_name') or '').strip()
    amount_raw = _to_float(extracted.get('amount'))
    reference = (extracted.get('reference') or '').strip()
    pay_date = extracted.get('date')
    extracted_currency_name = (extracted.get('currency') or '').strip().upper()

    # Resolve extracted currency
    extracted_curr = None
    if extracted_currency_name:
        extracted_curr = env['res.currency'].search(
            [('name', '=', extracted_currency_name)], limit=1
        )

    # Tolerance from settings
    default_tolerance = float(
        env['ir.config_parameter'].sudo().get_param(
            'odoo_invoice_ai_validator.match_amount_tolerance', '2.0'
        )
    )

    # ── Invoice domain ────────────────────────────────────────────────────
    if proof_type == 'vendor_bill':
        move_types = ('in_invoice', 'in_receipt')
    else:
        move_types = ('out_invoice', 'out_receipt')

    domain = [
        ('move_type', 'in', move_types),
        ('payment_state', 'in', ('not_paid', 'partial')),
        ('state', '=', 'posted'),
    ]
    if partner_id:
        domain.append(('partner_id', '=', partner_id))

    invoices = env['account.move'].search(domain, limit=300)
    if not invoices:
        return []

    # ── Per-partner rules ─────────────────────────────────────────────────
    partner_rules = {}
    rule_partner_ids = list({inv.partner_id.id for inv in invoices if inv.partner_id})
    if rule_partner_ids:
        rules = env['payment.partner.rule'].search([
            ('partner_id', 'in', rule_partner_ids),
            ('active', '=', True),
        ])
        for r in rules:
            partner_rules[r.partner_id.id] = r

    # ── Correction history ────────────────────────────────────────────────
    # Map: (partner_id, correct_invoice_id) -> count of corrections
    correction_boost = {}
    if partner_id or payer_name:
        correction_domain = []
        if partner_id:
            correction_domain = [('partner_id', '=', partner_id)]
        corrections = env['payment.proof.correction'].search(correction_domain, limit=200)
        for c in corrections:
            key = (c.partner_id.id, c.correct_invoice_id.id)
            correction_boost[key] = correction_boost.get(key, 0) + 1

    # ── Score each invoice ────────────────────────────────────────────────
    scored = []
    for inv in invoices:
        rule = partner_rules.get(inv.partner_id.id)
        ref_weight = rule.reference_weight if rule else 50.0
        amt_weight = rule.amount_weight if rule else 30.0
        name_weight = rule.name_weight if rule else 15.0
        tolerance = rule.amount_tolerance if rule else default_tolerance

        score = 0
        reasons = []

        # 1. Reference match
        if reference:
            haystack = ' '.join(filter(None, [inv.name, inv.ref, inv.narration or ''])).lower()
            ref_lo = re.sub(r'\s+', ' ', reference.lower())

            # Check per-partner regex pattern first
            pattern_matched = False
            if rule and rule.reference_pattern:
                try:
                    if re.search(rule.reference_pattern, reference, re.IGNORECASE):
                        score += ref_weight
                        reasons.append(f'Reference matched partner pattern.')
                        pattern_matched = True
                except re.error:
                    pass

            if not pattern_matched:
                if ref_lo in haystack:
                    score += ref_weight
                    reasons.append(f'Reference "{reference}" found in invoice.')
                elif _partial_ref_match(ref_lo, haystack):
                    score += ref_weight * 0.5
                    reasons.append(f'Partial reference match on "{reference}".')

        # 2. Amount match with currency conversion
        if amount_raw and inv.amount_residual > 0:
            converted = _convert_amount(env, amount_raw, extracted_curr, inv.currency_id)
            diff_pct = abs(converted - inv.amount_residual) / inv.amount_residual * 100
            if diff_pct <= tolerance:
                pts = int(amt_weight * (1 - diff_pct / max(tolerance, 0.01))) + 1
                score += pts
                curr_note = f' (converted from {extracted_currency_name})' if extracted_curr and extracted_curr != inv.currency_id else ''
                reasons.append(
                    f'Amount {converted:,.2f}{curr_note} ≈ balance {inv.amount_residual:,.2f} ({diff_pct:.1f}% diff).'
                )

        # 3. Partner name similarity
        if payer_name and inv.partner_id:
            sim = _name_similarity(payer_name, inv.partner_id.name or '')
            if sim >= 0.6:
                pts = int(name_weight * sim)
                score += pts
                reasons.append(f'Payer "{payer_name}" ~ partner "{inv.partner_id.name}" ({sim*100:.0f}%).')

        # 4. Date proximity
        if pay_date and inv.invoice_date:
            import datetime
            try:
                d = datetime.date.fromisoformat(str(pay_date)) if isinstance(pay_date, str) else pay_date
                delta = abs((d - inv.invoice_date).days)
                if delta <= 30:
                    pts = max(1, 5 - delta // 6)
                    score += pts
                    reasons.append(f'Payment date {delta} day(s) from invoice date.')
            except Exception:
                pass

        # 5. Correction history bonus
        boost_key = (inv.partner_id.id, inv.id)
        if correction_boost.get(boost_key, 0) > 0:
            score += 10
            reasons.append(f'Correction history: previously matched {correction_boost[boost_key]}x.')

        if score >= _SCORE_THRESHOLD:
            scored.append((inv, score, ' '.join(reasons)))

    scored.sort(key=lambda x: x[1], reverse=True)
    _logger.info('invoice_matcher: %d invoices checked, %d above threshold', len(invoices), len(scored))
    return scored


# ──────────────────────────────────────────────────────────────────────────────

def _convert_amount(env, amount: float, from_currency, to_currency) -> float:
    """Convert amount from from_currency to to_currency using today's rate."""
    if not from_currency or not to_currency or from_currency == to_currency:
        return amount
    try:
        return from_currency._convert(
            amount, to_currency, env.company,
            env['fields'].Date.today() if hasattr(env, 'fields') else __import__('odoo').fields.Date.today(),
        )
    except Exception as exc:
        _logger.debug('Currency conversion failed: %s', exc)
        return amount


def _to_float(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _partial_ref_match(ref: str, haystack: str) -> bool:
    r = re.sub(r'\W', '', ref)
    h = re.sub(r'\W', '', haystack)
    return bool(r) and len(r) >= 4 and r in h


def _name_similarity(a: str, b: str) -> float:
    def tokens(s):
        return set(re.sub(r'[^\w\s]', '', s.lower()).split())
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
