import logging
import os
from urllib.parse import quote

import requests
from django.conf import settings
from celery import shared_task
from app.services.assignment import AssignmentService
from app.services.attachments import AttachmentService
from app.services.email_ingestion import EmailIngestionService
from app.services.metrics import record_metric
from app.services.msgraph import GRAPH_BASE, get_graph_token
from app.services.satisfaction import SatisfactionService
from app.services.sla import SLAService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Microsoft Graph helpers
# ---------------------------------------------------------------------------

def _get_graph_token():
    # Delegado a app.services.msgraph para compartirlo con el envío saliente.
    return get_graph_token()


def _process_message(message, brand, attachments=None):
    return EmailIngestionService.process_message(message, brand, attachments=attachments)


def _fetch_m365_attachments(mailbox, message_id, headers):
    """Descarga adjuntos de archivo de Graph antes de procesar el mensaje.

    Las imágenes inline suelen ser logotipos de firma y se omiten. Los tipos o
    tamaños no admitidos tampoco bloquean la creación del ticket.
    """
    mailbox_path = quote(mailbox, safe="@")
    message_path = quote(message_id, safe="")
    base = f'{GRAPH_BASE}/users/{mailbox_path}/messages/{message_path}/attachments'
    response = requests.get(
        f'{base}?$select=id,name,contentType,size,isInline&$top=25',
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    max_files = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_FILES', 5)
    max_bytes = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_BYTES', 15 * 1024 * 1024)
    max_total = getattr(settings, 'PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES', 25 * 1024 * 1024)
    allowed = getattr(settings, 'PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS', set())
    accepted = []
    total = 0
    for item in response.json().get('value', []):
        if item.get('isInline') or item.get('@odata.type') not in (
            None, '#microsoft.graph.fileAttachment',
        ):
            continue
        name = os.path.basename((item.get('name') or 'archivo').replace('\\', '/'))
        extension = os.path.splitext(name)[1].lower().lstrip('.')
        declared_size = int(item.get('size') or 0)
        if (
            len(accepted) >= max_files
            or not extension
            or extension not in allowed
            or declared_size > max_bytes
            or total + declared_size > max_total
        ):
            logger.warning(
                'poll_m365 attachment skipped by policy',
                extra={'message_id': message_id, 'attachment_name': name, 'size': declared_size},
            )
            continue

        attachment_id = quote(item['id'], safe='')
        content_response = requests.get(
            f'{base}/{attachment_id}/$value',
            headers=headers,
            timeout=60,
        )
        content_response.raise_for_status()
        content = content_response.content
        if len(content) > max_bytes or total + len(content) > max_total:
            logger.warning(
                'poll_m365 attachment skipped after download',
                extra={'message_id': message_id, 'attachment_name': name, 'size': len(content)},
            )
            continue
        accepted.append({
            'name': name,
            'content_type': item.get('contentType') or 'application/octet-stream',
            'content': content,
        })
        total += len(content)
    return accepted


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(name='app.tasks.poll_m365_mailboxes')
def poll_m365_mailboxes():
    from app.models import Brand

    brands = Brand.objects.filter(mailbox_type='m365').exclude(support_email='')
    if not brands.exists():
        logger.info('poll_m365: no brands with M365 mailbox configured')
        return 'no_brands'

    try:
        token = _get_graph_token()
    except Exception as exc:
        logger.error('poll_m365: token acquisition failed: %s', exc)
        return

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    for brand in brands:
        mailbox = brand.support_email
        url = (
            f'{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages'
            '?$filter=isRead eq false'
            '&$select=id,subject,from,body,receivedDateTime,conversationId,internetMessageHeaders,hasAttachments'
            '&$top=50'
            '&$orderby=receivedDateTime asc'
        )
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error('poll_m365 [%s]: Graph read error: %s', mailbox, exc)
            continue

        for msg in resp.json().get('value', []):
            msg_id = msg['id']
            try:
                attachments = (
                    _fetch_m365_attachments(mailbox, msg_id, headers)
                    if msg.get('hasAttachments')
                    else []
                )
                result = _process_message(msg, brand, attachments=attachments)
                logger.info('poll_m365 [%s] %s… → %s', mailbox, msg_id[:16], result)
            except Exception as exc:
                logger.error('poll_m365 [%s] %s… error: %s', mailbox, msg_id[:16], exc)
                continue

            # Mark as read (best-effort; even on skip_dup so we don't keep seeing it)
            try:
                requests.patch(
                    f'{GRAPH_BASE}/users/{mailbox}/messages/{msg_id}',
                    headers=headers,
                    json={'isRead': True},
                    timeout=10,
                )
            except Exception as exc:
                logger.warning('poll_m365 [%s] mark-read failed %s…: %s', mailbox, msg_id[:16], exc)


@shared_task(name='app.tasks.cleanup_orphan_attachments')
def cleanup_orphan_attachments():
    """Retira subidas abandonadas que nunca llegaron a enviarse en un comentario."""
    totals = {"deleted": 0, "failed": 0}
    while True:
        result = AttachmentService.cleanup_orphans(batch_size=250)
        totals["deleted"] += result["deleted"]
        totals["failed"] += result["failed"]
        if result["deleted"] < 250:
            break
    logger.info("attachment_orphan_cleanup_completed", extra=totals)
    return totals


@shared_task(name='app.tasks.auto_assign_unassigned_tickets')
def auto_assign_unassigned_tickets():
    """Auto-assign unassigned tickets using database assignment rules."""
    assigned_count = AssignmentService.auto_assign_unassigned()
    return f'assigned_count: {assigned_count}'


@shared_task(name='app.tasks.mark_sla_breaches')
def mark_sla_breaches():
    breached = SLAService.mark_breaches()
    return f'breached_count: {len(breached)}'


@shared_task(name='app.tasks.notify_sla_at_risk')
def notify_sla_at_risk():
    notified = SLAService.notify_at_risk()
    return f'at_risk_count: {len(notified)}'


@shared_task(name='app.tasks.offer_satisfaction_surveys')
def offer_satisfaction_surveys():
    """Ofrece la encuesta de satisfacción de los tickets resueltos hace suficiente.

    Equivalente a la automatización de Zendesk. No hace nada si
    SATISFACTION_SURVEY_ENABLED está apagado (que es el default).
    """
    offered = SatisfactionService.offer_pending_surveys()
    return f'offered_count: {offered}'


@shared_task(
    name='app.tasks.classify_ticket_ai',
    bind=True,
    max_retries=3,
)
def classify_ticket_ai(self, ticket_id, force=False):
    """Clasificación ITIL por IA de un ticket recién creado.

    Un fallo aquí JAMÁS afecta al ticket (ya está creado). Errores transitorios
    reintentan con backoff; los permanentes se registran y se tragan para no
    envenenar la cola.
    """
    from app.models import Ticket
    from app.services.ai import AIRetryableError
    from app.services.ticket_ai import TicketAIService

    if not getattr(settings, 'AI_CLASSIFICATION_ENABLED', False):
        return 'disabled'
    try:
        ticket = Ticket.objects.select_related('brand', 'requester').get(
            id=ticket_id, is_deleted=False, merged_into__isnull=True
        )
    except Ticket.DoesNotExist:
        return 'ticket_missing'

    try:
        return TicketAIService.classify_and_apply(ticket, force=force)
    except AIRetryableError as exc:
        if self.request.retries >= self.max_retries:
            TicketAIService.mark_failed(ticket, exc)
            record_metric('ai.classification.failed', labels={'ticket_id': ticket_id, 'error': 'retries_exhausted'})
            logger.error('classify_ticket_ai agotó reintentos [ticket=%s]: %s', ticket_id, exc)
            return 'failed'
        # Backoff: 30s, 60s, 120s
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 600))
    except Exception as exc:
        logger.exception('classify_ticket_ai failed [ticket=%s]', ticket_id)
        TicketAIService.mark_failed(ticket, exc)
        record_metric('ai.classification.failed', labels={'ticket_id': ticket_id, 'error': type(exc).__name__})
        return 'failed'


@shared_task(name='app.tasks.send_outbound_email', bind=True, max_retries=5)
def send_outbound_email(self, log_id):
    """Entrega un OutboundEmailLog encolado. Idempotente: SQS es at-least-once."""
    from django.utils import timezone
    from app.models import OutboundEmailLog
    from app.services.email_outbound import OutboundEmailService

    log = OutboundEmailLog.objects.select_related('brand').filter(id=log_id).first()
    if log is None:
        return 'log_missing'
    if log.status == OutboundEmailLog.STATUS_SENT:
        return 'already_sent'
    if log.status == OutboundEmailLog.STATUS_SUPPRESSED:
        return 'suppressed'

    blocked = OutboundEmailService.blocking_reason_for_log(log)
    if blocked:
        log.status = OutboundEmailLog.STATUS_SUPPRESSED
        log.result = blocked
        log.error = None
        log.save(update_fields=['status', 'result', 'error'])
        record_metric('email.outbound.suppressed', labels={'log_id': log.id, 'reason': blocked})
        return 'suppressed'

    log.attempts += 1
    log.save(update_fields=['attempts'])

    try:
        message_id = OutboundEmailService.deliver(log)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            log.status = OutboundEmailLog.STATUS_FAILED
            log.error = str(exc)[:2000]
            log.save(update_fields=['status', 'error'])
            record_metric('email.outbound.failed', labels={'log_id': log.id, 'error': type(exc).__name__})
            logger.error('send_outbound_email failed permanently [log=%s]: %s', log.id, exc)
            return 'failed'
        # Backoff exponencial: 60s, 120s, 240s, 480s, 600s (cap)
        raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 600))

    log.status = OutboundEmailLog.STATUS_SENT
    log.sent_at = timezone.now()
    log.provider_message_id = message_id or None
    log.error = None
    log.save(update_fields=['status', 'sent_at', 'provider_message_id', 'error'])
    record_metric('email.outbound.sent', labels={'log_id': log.id, 'provider': log.provider})
    return 'sent'
