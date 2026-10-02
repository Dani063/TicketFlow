"""Clasifica un correo entrante como humano o automático.

**Solo lo aplica el backfill** (manage.py reingest_mailbox): al recuperar correo
atrasado no se quieren crear de golpe cientos de tickets de alarmas que ya no
tienen sentido. En vivo el resultado solo se guarda en
``InboundEmailLog.automated_rule`` para poder decidir más adelante con datos:
hoy las alarmas crean ticket a propósito.

El error no es simétrico. Descartar un correo humano repite el incidente que
esto viene a cerrar, así que ante la duda el veredicto es ``review`` (no se
excluye salvo que se pida) y cualquier referencia a un ticket existente gana.
"""

import re

from django.conf import settings

from app.services.email_ingestion import TICKET_REF_RE

HUMAN = 'human'
AUTOMATED = 'automated'
REVIEW = 'review'

_SENDER_RULES = [
    ('sender_aws_sns', re.compile(r'@sns\.amazonaws\.com$')),
    ('sender_aws_noreply', re.compile(r'^no-?reply@.*amazonaws\.com$')),
    ('sender_mailer_daemon', re.compile(r'^(mailer-daemon|postmaster)@')),
    ('sender_noreply', re.compile(r'^(no-?reply|donotreply|do-not-reply|notifications?)@')),
]

_SUBJECT_RULES = [
    ('subject_cloudwatch_alarm', re.compile(r'^(ALARM|OK|INSUFFICIENT_DATA): ".+" in ')),
    ('subject_resolved_firing', re.compile(r'^\[(FIRING|RESOLVED)(:\d+)?\]', re.IGNORECASE)),
    ('subject_aws_notification', re.compile(r'^AWS Notification', re.IGNORECASE)),
    ('subject_bounce', re.compile(
        r'^(undeliverable|undelivered mail|delivery status notification|mail delivery failed|'
        r'no se puede entregar|returned mail|delivery has failed)', re.IGNORECASE)),
    ('subject_out_of_office', re.compile(
        r'^(automatic reply|respuesta autom[aá]tica|out of office|fuera de la oficina|'
        r'autoreply|auto-reply)', re.IGNORECASE)),
]

# Patrones que en el buzón real son más bien boletines que correo de cliente:
# no se excluyen por defecto, se marcan para revisarlos.
_REVIEW_HEADERS = ('precedence', 'list-unsubscribe', 'list-id', 'x-auto-response-suppress')

# Aunque citen un ticket, un rebote o una respuesta de fuera de oficina no son
# correo útil para el agente.
_NEVER_HUMAN = {'subject_bounce', 'subject_out_of_office', 'header_bounce'}


def _headers(message):
    return {
        (h.get('name') or '').strip().lower(): (h.get('value') or '').strip()
        for h in (message.get('internetMessageHeaders') or [])
    }


def _extra_patterns(setting_name):
    return [re.compile(p, re.IGNORECASE) for p in getattr(settings, setting_name, []) or []]


def classify(message, known_conversation=False, own_addresses=()):
    """Devuelve ``(veredicto, regla)``. ``regla`` es None para correo humano limpio."""
    sender = ((message.get('from') or {}).get('emailAddress') or {}).get('address') or ''
    sender = sender.strip().lower()
    subject = (message.get('subject') or '').strip()
    headers = _headers(message)

    automated_rule = None
    auto_submitted = headers.get('auto-submitted', '').lower()
    if auto_submitted and auto_submitted != 'no':
        automated_rule = 'header_auto_submitted'
    elif 'multipart/report' in headers.get('content-type', '').lower() or headers.get('return-path') == '<>':
        automated_rule = 'header_bounce'
    elif 'x-amz-sns-message-id' in headers or 'x-amz-sns-topic-arn' in headers:
        automated_rule = 'header_aws_sns'
    elif sender and sender in {a.lower() for a in own_addresses}:
        automated_rule = 'sender_own_mailbox'
    if automated_rule is None:
        for name, pattern in _SENDER_RULES:
            if sender and pattern.search(sender):
                automated_rule = name
                break
    if automated_rule is None:
        for pattern in _extra_patterns('INBOUND_AUTOMATED_SENDER_PATTERNS'):
            if sender and pattern.search(sender):
                automated_rule = 'sender_configured'
                break
    if automated_rule is None:
        for name, pattern in _SUBJECT_RULES:
            if pattern.search(subject):
                automated_rule = name
                break
    if automated_rule is None:
        for pattern in _extra_patterns('INBOUND_AUTOMATED_SUBJECT_PATTERNS'):
            if pattern.search(subject):
                automated_rule = 'subject_configured'
                break

    references_ticket = bool(TICKET_REF_RE.search(subject)) or known_conversation
    if references_ticket and automated_rule not in _NEVER_HUMAN:
        return HUMAN, ('ticket_reference' if automated_rule else None)
    if automated_rule:
        return AUTOMATED, automated_rule

    for name in _REVIEW_HEADERS:
        value = headers.get(name, '').lower()
        if not value:
            continue
        if name == 'precedence' and value not in ('bulk', 'list', 'junk'):
            continue
        return REVIEW, f'header_{name.replace("-", "_")}'
    return HUMAN, None
