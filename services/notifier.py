"""
Notification service — sends emails/chatter messages after payment validation.
"""
import logging

_logger = logging.getLogger(__name__)


def notify_payment_validated(proof, invoice):
    """
    Notify the salesperson and any followers when an invoice is paid.
    Called after payment is registered.
    """
    env = proof.env

    # 1. Post on the invoice chatter
    invoice.message_post(
        body=_(
            'Payment validated via AI Payment Validator.<br/>'
            'Proof reference: <b>%s</b><br/>'
            'Validated by: %s'
        ) % (proof.name, env.user.name),
        subtype_xmlid='mail.mt_note',
    )

    # 2. Notify the invoice's salesperson by email (if configured)
    salesperson = invoice.invoice_user_id or invoice.user_id
    if salesperson and salesperson.email:
        try:
            template = env.ref(
                'odoo_invoice_ai_validator.mail_template_payment_validated',
                raise_if_not_found=False,
            )
            if template:
                template.with_context(
                    salesperson=salesperson,
                    invoice=invoice,
                    proof=proof,
                ).send_mail(proof.id, force_send=True, raise_exception=False)
            else:
                # Fallback: plain email
                env['mail.mail'].create({
                    'subject': f'Invoice {invoice.name} has been paid',
                    'body_html': (
                        f'<p>Hello {salesperson.name},</p>'
                        f'<p>Invoice <b>{invoice.name}</b> for client '
                        f'<b>{invoice.partner_id.name}</b> has been paid.</p>'
                        f'<p>Amount: {invoice.currency_id.symbol} '
                        f'{invoice.amount_total:,.2f}</p>'
                        f'<p>Proof ref: {proof.name}</p>'
                        f'<p>Validated by: {env.user.name}</p>'
                    ),
                    'email_to': salesperson.email,
                    'auto_delete': True,
                }).send()
        except Exception as exc:
            _logger.warning('Failed to send payment notification email: %s', exc)


def _(text):
    """Minimal translation shim — real translation handled by Odoo at call site."""
    return text
