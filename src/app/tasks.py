"""Tareas Celery: recogida de correo M365, asignación/SLA periódicos y envíos event-driven."""

import logging

from django.conf import settings
from celery import shared_task
from app.services.assignment import AssignmentService
from app.services.attachments import AttachmentService
from app.services.metrics import record_metric
from app.services.reporting_facts import refresh_stale_facts
from app.services.satisfaction import SatisfactionService
from app.services.sla import SLAService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recogida de correo M365 (barrido por ventana)
# ---------------------------------------------------------------------------
# La lógica vive en app.services.mailbox_sweep. Aquí solo las tareas Celery.
#
# Límites de tiempo por encima de la vuelta normal (presupuesto de proceso de
# INBOUND_SWEEP_PROCESS_BUDGET_SECONDS más el descubrimiento): un adjunto
# colgado no puede retener el worker indefinidamente. El lease del cursor
# caduca solo si el proceso muere.

@shared_task(name='app.tasks.sweep_m365_mailboxes', soft_time_limit=240, time_limit=300)
def sweep_m365_mailboxes():
    """Recoge el correo de los buzones M365 configurados (cada 60 s en beat)."""
    from app.services.mailbox_sweep import run_sweep

    return run_sweep()


@shared_task(name='app.tasks.poll_m365_mailboxes', soft_time_limit=240, time_limit=300)
def poll_m365_mailboxes():
    """Alias del nombre anterior (delta query, retirado).

    Durante un despliegue pueden quedar en SQS ejecuciones encoladas por el beat
    viejo, o reaparecer hasta una hora después las que reservó un pod que murió.
    Ejecutan el barrido nuevo, protegido por el mismo lease, así que no pueden
    solaparse con él.
    """
    from app.services.mailbox_sweep import run_sweep

    return run_sweep()


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


@shared_task(name='app.tasks.refresh_reporting_facts')
def refresh_reporting_facts():
    """Mantiene al día las tablas de hechos que alimentan /reporting/.

    Es lo que permite que un informe no reconstruya el histórico en cada
    petición: aquí se paga el coste de releer auditorías, una sola vez por
    ticket y fuera del pool web.

    La selección es autorreparable (compara updated_at contra el de los hechos),
    así que una pasada perdida se recupera en la siguiente sin estado adicional.
    """
    processed = refresh_stale_facts()
    return f'facts_refreshed: {processed}'


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
