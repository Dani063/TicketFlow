import hashlib
import logging
import os
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone
from celery import shared_task
from app.services.assignment import AssignmentService
from app.services.attachments import AttachmentService
from app.services.email_ingestion import EmailIngestionService
from app.services.metrics import record_metric
from app.services.msgraph import GRAPH_BASE, get_graph_token
from app.services.reporting_facts import refresh_stale_facts
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
# Sincronización incremental de buzones (delta query)
# ---------------------------------------------------------------------------

def _folder_key(folder_id):
    """Clave indexable de una carpeta: los id de Graph no caben en un índice."""
    return hashlib.sha256(folder_id.encode('utf-8')).hexdigest()


def _inbox_folder_id(mailbox, headers):
    """Id de la carpeta Inbox *física* que hay detrás de una dirección."""
    response = requests.get(
        f'{GRAPH_BASE}/users/{quote(mailbox, safe="@")}/mailFolders/Inbox?$select=id',
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()['id']


def _mailbox_targets(brands, headers):
    """Agrupa las marcas por carpeta física: un destino a sincronizar por carpeta.

    Las direcciones de soporte son alias de un mismo buzón, así que varias marcas
    resuelven a la MISMA carpeta y hay que consultarla una sola vez. Sin agrupar,
    cada marca ingeriría el mismo mensaje y saldrían tantos tickets como marcas
    compartan buzón: hasta ahora lo tapaba isRead (la primera marca marcaba leído
    y las demás ya no lo veían), y al dejar de usarlo la agrupación pasa a ser lo
    que sostiene la atribución de marca. Se mantiene el criterio anterior — la
    primera marca por id —, que sigue siendo arbitrario y sigue pendiente de una
    decisión de negocio (ver CLAUDE.md).
    """
    targets = {}
    for brand in brands:
        mailbox = brand.support_email
        try:
            folder_id = _inbox_folder_id(mailbox, headers)
        except Exception as exc:
            logger.error('poll_m365 [%s]: no se pudo resolver la carpeta Inbox: %s', mailbox, exc)
            continue
        key = _folder_key(folder_id)
        if key in targets:
            targets[key]['shared_with'].append(brand.name)
            continue
        targets[key] = {
            'folder_key': key,
            'folder_id': folder_id,
            'mailbox': mailbox,
            'brand': brand,
            'shared_with': [],
        }
    return list(targets.values())


def _initial_delta_url(target):
    """Primera vuelta de un cursor recién estrenado.

    El filtro por fecha es imprescindible: un delta sin acotar devuelve la
    carpeta ENTERA y se ingeriría todo el histórico del buzón. Graph solo admite
    receivedDateTime ge/gt en delta, y codifica los parámetros dentro del token,
    así que basta con ponerlo una vez: las vueltas siguientes lo heredan.
    """
    lookback = getattr(settings, 'INBOUND_DELTA_LOOKBACK_MINUTES', 60)
    since = (timezone.now() - timedelta(minutes=lookback)).astimezone(dt_timezone.utc)
    return (
        f'{GRAPH_BASE}/users/{quote(target["mailbox"], safe="@")}'
        f'/mailFolders/{quote(target["folder_id"], safe="")}/messages/delta'
        '?$select=id,subject,from,body,receivedDateTime,conversationId,internetMessageHeaders,hasAttachments'
        f'&$filter=receivedDateTime ge {since.strftime("%Y-%m-%dT%H:%M:%SZ")}'
    )


def _already_ingested(brand, message_id):
    """Evita descargar adjuntos de algo que la deduplicación va a descartar.

    Sin isRead como filtro, un mensaje ya procesado puede reaparecer en el delta
    (cualquier actualización lo devuelve, incluido nuestro propio marcado como
    leído). process_message lo descartaría igual, pero después de habernos traído
    los adjuntos.
    """
    from app.models import InboundEmailLog

    return InboundEmailLog.objects.filter(
        brand=brand,
        message_id=message_id,
        status__in=(InboundEmailLog.STATUS_PROCESSED, InboundEmailLog.STATUS_DUPLICATE),
    ).exists()


def _ingest_message(msg, brand, mailbox, headers, mark_read):
    """Procesa un mensaje; un fallo impide avanzar el cursor de esta vuelta."""
    msg_id = msg['id']
    if _already_ingested(brand, msg_id):
        return 'skip_dup'

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
        # Con delta, avanzar tras un fallo perdería el mensaje para siempre:
        # ya no existe el filtro de no leídos que lo devolvía en otra pasada.
        raise

    # Marcar como leído ya NO es el cursor: es cortesía para quien mire el buzón,
    # y por eso puede apagarse en los entornos que no son dueños del buzón.
    if mark_read:
        try:
            requests.patch(
                f'{GRAPH_BASE}/users/{quote(mailbox, safe="@")}/messages/{quote(msg_id, safe="")}',
                headers=headers,
                json={'isRead': True},
                timeout=10,
            )
        except Exception as exc:
            logger.warning('poll_m365 [%s] mark-read failed %s…: %s', mailbox, msg_id[:16], exc)
    return result


class _ResyncRequired(Exception):
    """El enlace guardado ya no sirve y hay que reiniciar la sincronización."""


# Para entidades de Outlook el token de delta NO tiene caducidad fija: vive en una
# caché interna de Graph y se desaloja sin aviso. Cuando pasa, la respuesta es un
# 410 Gone o un 4xx con uno de estos códigos. Sin tratarlo, el buzón se quedaría
# sin ingerir indefinidamente y en silencio, que es justo el fallo que este
# cambio viene a eliminar.
_RESYNC_ERROR_CODES = {'syncstatenotfound', 'resyncrequired', 'syncstatereset'}


def _needs_resync(response):
    if response.status_code == 410:
        return True
    if not 400 <= response.status_code < 500:
        return False
    try:
        code = (response.json().get('error') or {}).get('code') or ''
    except Exception:
        return False
    return code.lower() in _RESYNC_ERROR_CODES


def _delta_round(url, target, headers, mark_read, max_pages, counter):
    """Recorre una vuelta de delta. Devuelve el enlace con el que continuar.

    Tanto el deltaLink (ronda cerrada) como el nextLink (ronda a medias por el
    tope de páginas) son reanudables, así que los dos valen como cursor.
    """
    mailbox = target['mailbox']
    link, kind = '', ''
    for _ in range(max_pages):
        response = requests.get(url, headers=headers, timeout=30)
        if _needs_resync(response):
            raise _ResyncRequired(f'HTTP {response.status_code}')
        response.raise_for_status()
        page = response.json()
        for msg in page.get('value', []):
            # Bajas y movimientos llegan como tombstones, sin cuerpo.
            if '@removed' in msg or not msg.get('id'):
                continue
            counter['seen'] += 1
            _ingest_message(msg, target['brand'], mailbox, headers, mark_read)

        delta_link = page.get('@odata.deltaLink')
        if delta_link:
            return delta_link, 'delta'
        next_link = page.get('@odata.nextLink')
        if not next_link:
            return link, kind
        url, link, kind = next_link, next_link, 'partial'

    logger.warning(
        'poll_m365 [%s]: tope de %s páginas alcanzado; se continúa en la siguiente vuelta',
        mailbox, max_pages,
    )
    return link, kind


def _sync_mailbox(target, headers, environment):
    """Avanza el cursor de un buzón para ESTE entorno. Devuelve mensajes vistos."""
    from app.models import MailboxSyncCursor

    cursor, _ = MailboxSyncCursor.objects.get_or_create(
        environment=environment,
        folder_key=target['folder_key'],
        defaults={'folder_id': target['folder_id'], 'mailbox': target['mailbox']},
    )
    mailbox = target['mailbox']
    max_pages = getattr(settings, 'INBOUND_DELTA_MAX_PAGES', 20)
    mark_read = getattr(settings, 'INBOUND_MARK_READ_ENABLED', True)
    counter = {'seen': 0}

    try:
        try:
            link, kind = _delta_round(
                cursor.delta_link or _initial_delta_url(target),
                target, headers, mark_read, max_pages, counter,
            )
        except _ResyncRequired as exc:
            logger.warning(
                'poll_m365 [%s]: cursor caducado (%s); se reinicia la sincronización', mailbox, exc,
            )
            record_metric('email.inbound.delta_resync',
                          labels={'mailbox': mailbox, 'environment': environment})
            cursor.delta_link = ''
            link, kind = _delta_round(
                _initial_delta_url(target), target, headers, mark_read, max_pages, counter,
            )
    except Exception as exc:
        # El cursor NO avanza: la próxima vuelta reintenta desde el último enlace
        # bueno. La reentrega es inocua — la absorbe InboundEmailLog.
        logger.error('poll_m365 [%s]: Graph delta error: %s', mailbox, exc)
        cursor.last_result = 'error'
        cursor.last_error = str(exc)[:2000]
        cursor.save(update_fields=['last_result', 'last_error', 'updated_at'])
        record_metric('email.inbound.delta_failed',
                      labels={'mailbox': mailbox, 'environment': environment})
        return counter['seen']

    cursor.folder_id = target['folder_id']
    cursor.mailbox = mailbox
    if link:
        cursor.delta_link = link
        cursor.link_kind = kind
    cursor.last_synced_at = timezone.now()
    cursor.last_result = kind or 'empty'
    cursor.last_error = None
    cursor.last_message_count = counter['seen']
    cursor.save()
    return counter['seen']


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(name='app.tasks.poll_m365_mailboxes')
def poll_m365_mailboxes():
    """Ingesta incremental de los buzones M365 configurados.

    Cada entorno lleva su propio cursor (MailboxSyncCursor), así que varios
    entornos pueden leer el mismo buzón sin quitarse el correo unos a otros.
    """
    from app.models import Brand

    brands = Brand.objects.filter(mailbox_type='m365').exclude(support_email='').order_by('id')
    if not brands.exists():
        logger.info('poll_m365: no brands with M365 mailbox configured')
        return 'no_brands'

    try:
        token = _get_graph_token()
    except Exception as exc:
        logger.error('poll_m365: token acquisition failed: %s', exc)
        return 'token_error'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    environment = getattr(settings, 'ENVIRONMENT_NAME', 'local')
    seen = 0
    for target in _mailbox_targets(brands, headers):
        if target['shared_with']:
            logger.info(
                'poll_m365 [%s]: buzón compartido con %s; se ingiere una sola vez como %s',
                target['mailbox'], ', '.join(target['shared_with']), target['brand'].name,
            )
        try:
            seen += _sync_mailbox(target, headers, environment)
        except Exception:
            # Un buzón roto (incluida su propia fila de cursor) no puede dejar sin
            # ingerir a los demás: el bucle por marcas de antes hacía `continue`.
            logger.exception('poll_m365 [%s]: sincronización abortada', target['mailbox'])
            record_metric('email.inbound.sync_aborted',
                          labels={'mailbox': target['mailbox'], 'environment': environment})
    return f'{seen} message(s)'


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
