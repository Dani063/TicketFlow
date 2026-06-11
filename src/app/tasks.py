import logging
import requests
from django.conf import settings
from celery import shared_task
from app.services.assignment import AssignmentService
from app.services.email_ingestion import EmailIngestionService
from app.services.metrics import record_metric
from app.services.msgraph import GRAPH_BASE, get_graph_token
from app.services.sla import SLAService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Microsoft Graph helpers
# ---------------------------------------------------------------------------

def _get_graph_token():
    # Delegado a app.services.msgraph para compartirlo con el envío saliente.
    return get_graph_token()


def _process_message(message, brand):
    return EmailIngestionService.process_message(message, brand)


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
            '&$select=id,subject,from,body,receivedDateTime,conversationId,internetMessageHeaders'
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
                result = _process_message(msg, brand)
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
