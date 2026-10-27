"""Barrido por ventana de los buzones M365: la recogida de correo de TicketFlow.

Sustituye al delta query de Graph, que perdió correo en silencio varias veces
(29/09–02/10/2026): su estado vive en Graph y no sobrevive a vueltas
concurrentes ni a un proceso que muere entre pedir un enlace y guardarlo.

Principios:

- **Sin estado en Graph.** Cada vuelta lista la carpeta por receivedDateTime,
  con solape sobre lo ya barrido. Repetir una vuelta es inocuo.
- **Descubrir y procesar por separado.** Todo mensaje listado se registra en
  ``InboundEmailLog`` (``pending``) *antes* de descargar su cuerpo. La marca de
  agua (``swept_until``) solo avanza sobre lo ya registrado, así que un fallo al
  procesar nunca hace perder un mensaje ni frena a los demás.
- **Un solo barrido por buzón a la vez**, con un lease en la fila del cursor, y
  un reclamo atómico por mensaje: dos procesos no pueden ingerir el mismo
  correo aunque se solapen o uno muera a mitad.
- **Mensajes veneno a cuarentena** tras ``INBOUND_SWEEP_MAX_ATTEMPTS`` intentos,
  con alerta, en lugar de bloquear la ingesta.
- **Barridos de seguridad** (48 h cada hora, 14 días cada día) que buscan
  huecos sin descargar cuerpos; si encuentran algo, lo ingieren y avisan.
"""

import hashlib
import logging
import uuid
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import Brand, InboundEmailLog, MailboxSyncCursor, Ticket, User
from app.services import msgraph_mail
from app.services.email_ingestion import EmailIngestionService
from app.services.inbound_classifier import AUTOMATED, REVIEW, classify
from app.services.metrics import record_metric
from app.services.msgraph import get_graph_token
from app.services.msgraph_mail import GraphNotFound, GraphThrottled

logger = logging.getLogger(__name__)

TERMINAL = InboundEmailLog.TERMINAL_STATUSES


def sha256(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


folder_key = sha256


def log_id(msg_id):
    """Parte distintiva de un id de Graph: todos comparten el prefijo del buzón."""
    return f'…{msg_id[-16:]}' if msg_id else '-'


def _setting(name, default):
    return getattr(settings, name, default)


def _parse(value):
    return parse_datetime(value) if value else None


def graph_headers(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


class LeaseLost(Exception):
    """Otro proceso ha tomado el buzón: esta vuelta debe parar."""


def _sender_is_valid(message):
    sender = ((message.get('from') or {}).get('emailAddress') or {}).get('address') or ''
    sender = sender.strip().lower()
    if not sender or len(sender) > User._meta.get_field('email').max_length:
        return False
    try:
        validate_email(sender)
    except ValidationError:
        return False
    return True


# ---------------------------------------------------------------------------
# Destinos y cursores
# ---------------------------------------------------------------------------

def resolve_targets(brands, headers):
    """Agrupa las marcas por carpeta física. Devuelve ``(destinos, todas_resueltas)``.

    Las direcciones de soporte son alias del mismo buzón: varias marcas
    resuelven a la misma carpeta y se barre una sola vez, como la marca fijada
    en su cursor. Si un alias no resuelve, el resto sigue; lo único que no se
    hace en esa vuelta es crear cursores nuevos, para que la marca de un buzón
    no dependa de qué alias respondió.
    """
    targets = {}
    all_resolved = True
    for brand in brands:
        try:
            folder_id = msgraph_mail.inbox_folder_id(brand.support_email, headers)
        except Exception as exc:
            all_resolved = False
            logger.error('inbound_sweep [%s]: no se pudo resolver la carpeta Inbox: %s',
                         brand.support_email, exc)
            record_metric('email.inbound.folder_unresolved', labels={'mailbox': brand.support_email})
            continue
        key = folder_key(folder_id)
        if key in targets:
            targets[key]['shared_with'].append(brand.name)
            continue
        targets[key] = {
            'folder_key': key, 'folder_id': folder_id, 'mailbox': brand.support_email,
            'brand': brand, 'shared_with': [],
        }
    return list(targets.values()), all_resolved


def get_cursor(target, environment, allow_create=True):
    cursor = MailboxSyncCursor.objects.filter(
        environment=environment, folder_key=target['folder_key'],
    ).select_related('brand').first()
    if cursor is None:
        if not allow_create:
            logger.warning('inbound_sweep [%s]: cursor nuevo aplazado hasta resolver todos los alias',
                           target['mailbox'])
            return None
        try:
            cursor = MailboxSyncCursor.objects.create(
                environment=environment, folder_key=target['folder_key'],
                folder_id=target['folder_id'], mailbox=target['mailbox'],
                brand=target['brand'], ingest_mode=MailboxSyncCursor.MODE_SWEEP,
            )
        except IntegrityError:
            cursor = MailboxSyncCursor.objects.get(
                environment=environment, folder_key=target['folder_key'],
            )
    if cursor.brand_id is None:
        # Cursor del delta: se fija la misma marca que usaba (la primera por id).
        cursor.brand = target['brand']
        cursor.save(update_fields=['brand', 'updated_at'])
    return cursor


# ---------------------------------------------------------------------------
# Barrido de un buzón
# ---------------------------------------------------------------------------

class MailboxSweep:
    """Una vuelta sobre un buzón. ``clock`` es inyectable para los tests."""

    def __init__(self, cursor, target, headers, environment, clock=timezone.now, owner=None):
        self.cursor = cursor
        self.target = target
        self.mailbox = cursor.mailbox or target['mailbox']
        self.folder_id = target['folder_id']
        self.folder_key = cursor.folder_key
        self.brand = cursor.brand or target['brand']
        self.headers = headers
        self.environment = environment
        self.clock = clock
        self.owner = owner
        self.stats = Counter()
        self.results = Counter()
        self.new_received = []
        self.alerts = []

    # --- lease -------------------------------------------------------------

    def acquire_lease(self):
        now = self.clock()
        self.owner = self.owner or uuid.uuid4().hex
        free = Q(lease_until__isnull=True) | Q(lease_until__lt=now) | Q(lease_owner=self.owner)
        updated = MailboxSyncCursor.objects.filter(pk=self.cursor.pk).filter(free).update(
            lease_owner=self.owner,
            lease_until=now + timedelta(seconds=_setting('INBOUND_SWEEP_LEASE_SECONDS', 180)),
        )
        return updated == 1

    def renew_lease(self):
        if not self.owner:
            return
        now = self.clock()
        updated = MailboxSyncCursor.objects.filter(pk=self.cursor.pk, lease_owner=self.owner).update(
            lease_until=now + timedelta(seconds=_setting('INBOUND_SWEEP_LEASE_SECONDS', 180)),
        )
        if updated != 1:
            raise LeaseLost(self.mailbox)

    def release_lease(self):
        if self.owner:
            MailboxSyncCursor.objects.filter(pk=self.cursor.pk, lease_owner=self.owner).update(
                lease_owner=None, lease_until=None,
            )

    # --- descubrimiento ----------------------------------------------------

    def discover(self, since, until=None, source=InboundEmailLog.SOURCE_LIVE, gap_check=False,
                 classifier=None, include_review=False, dry_run=False, report=None,
                 max_pages=None, fields=None):
        """Lista la carpeta desde ``since`` y registra lo desconocido.

        Pagina por keyset sobre receivedDateTime. Si una página entera comparte
        el mismo segundo, se desempata con $skip solo para ese segundo.
        Devuelve ``(completo, max_received)``.
        """
        top = _setting('INBOUND_SWEEP_PAGE_SIZE', 200)
        max_pages = max_pages or _setting('INBOUND_SWEEP_MAX_PAGES', 25)
        cursor_ts = since
        seen = set()
        max_received = None
        for _ in range(max_pages):
            self.renew_lease()
            page = self._list(cursor_ts, until, top, 0, fields)
            self.stats['pages'] += 1
            batch = list(page)
            skip = 0
            while len(page) == top and page[0].get('receivedDateTime') == page[-1].get('receivedDateTime'):
                skip += top
                page = self._list(cursor_ts, until, top, skip, fields)
                self.stats['pages'] += 1
                batch.extend(page)
            fresh = [m for m in batch if m.get('id') and '@removed' not in m and m['id'] not in seen]
            seen.update(m['id'] for m in fresh)
            self._register(fresh, source, gap_check, classifier, include_review, dry_run, report)
            stamps = [_parse(m.get('receivedDateTime')) for m in batch if m.get('receivedDateTime')]
            if stamps:
                last = max(stamps)
                max_received = max(max_received, last) if max_received else last
                cursor_ts = last if last > cursor_ts else cursor_ts + timedelta(seconds=1)
            if len(page) < top:
                return True, max_received
        logger.warning('inbound_sweep [%s]: tope de %s páginas; sigue en la próxima vuelta',
                       self.mailbox, max_pages)
        return False, max_received

    def _list(self, since, until, top, skip, fields):
        kwargs = {'headers': self.headers, 'top': top, 'skip': skip}
        if fields:
            kwargs['fields'] = fields
        return msgraph_mail.list_window(self.mailbox, self.folder_id, since, until, **kwargs)

    def _register(self, messages, source, gap_check, classifier, include_review, dry_run, report):
        if not messages:
            return
        self.stats['listed'] += len(messages)
        by_key = {}
        for msg in messages:
            if len(msg['id']) > InboundEmailLog._meta.get_field('message_id').max_length:
                logger.error('inbound_sweep [%s]: id de mensaje demasiado largo (%s)',
                             self.mailbox, log_id(msg['id']))
                self.stats['id_too_long'] += 1
                continue
            by_key[sha256(msg['id'])] = msg

        known = {
            row['message_key']: row
            for row in InboundEmailLog.objects.filter(
                folder_key=self.folder_key, message_key__in=list(by_key),
            ).values('message_key', 'status', 'ticket_id')
        }
        # Filas anteriores al barrido (sin message_key). La columna no distingue
        # mayúsculas, así que se compara el id exacto en Python.
        legacy = {}
        for row in InboundEmailLog.objects.filter(
            folder_key__isnull=True, message_id__in=[m['id'] for m in by_key.values()],
        ).values('id', 'message_id', 'status', 'ticket_id'):
            legacy.setdefault(row['message_id'], row)
        imsg_keys = {
            sha256(m['internetMessageId']): key
            for key, m in by_key.items() if m.get('internetMessageId')
        }
        moved = set(
            InboundEmailLog.objects.filter(
                internet_message_key__in=list(imsg_keys), status__in=TERMINAL,
            ).values_list('internet_message_key', flat=True)
        ) if imsg_keys else set()
        conversations = set()
        if classifier:
            conv_ids = {m.get('conversationId') for m in by_key.values() if m.get('conversationId')}
            conversations = set(
                Ticket.objects.filter(email_conversation_id__in=conv_ids)
                .values_list('email_conversation_id', flat=True)
            ) if conv_ids else set()

        now = self.clock()
        rows = []
        for key, msg in by_key.items():
            if key in known:
                self.stats['known'] += 1
                self._report(report, 'already_ingested', msg, ticket=known[key]['ticket_id'])
                continue
            old = legacy.get(msg['id'])
            if old and old['status'] in (InboundEmailLog.STATUS_PROCESSED, InboundEmailLog.STATUS_DUPLICATE):
                self.stats['known'] += 1
                group = 'already_ingested' if old['ticket_id'] else 'logged_no_ticket'
                self._report(report, group, msg, ticket=old['ticket_id'])
                continue

            status, rule = InboundEmailLog.STATUS_PENDING, None
            imsg_key = sha256(msg['internetMessageId']) if msg.get('internetMessageId') else None
            if imsg_key and imsg_key in moved:
                status, rule = InboundEmailLog.STATUS_DUPLICATE, 'moved_duplicate'
            elif classifier:
                verdict, rule = classifier(msg, known_conversation=msg.get('conversationId') in conversations)
                if verdict == AUTOMATED or (verdict == REVIEW and not include_review):
                    status = InboundEmailLog.STATUS_SKIPPED
                self._report(report, {AUTOMATED: 'excluded', REVIEW: 'review'}.get(verdict, 'new'),
                             msg, rule=rule)
            else:
                # En vivo el clasificador solo deja constancia: no excluye nada.
                verdict, rule = classify(msg)
                rule = rule if verdict != 'human' else None
                self._report(report, 'new', msg, rule=rule)

            if gap_check and status == InboundEmailLog.STATUS_PENDING:
                self._flag_gap(msg)
            self.stats['new' if status == InboundEmailLog.STATUS_PENDING else status] += 1
            if status == InboundEmailLog.STATUS_PENDING and msg.get('receivedDateTime'):
                self.new_received.append(msg['receivedDateTime'])

            if dry_run:
                continue
            fields = {
                'status': status, 'automated_rule': rule, 'source': source,
                'folder_key': self.folder_key, 'message_key': key,
                'graph_received_at': _parse(msg.get('receivedDateTime')),
                'internet_message_id': msg.get('internetMessageId'),
                'internet_message_key': imsg_key,
                'conversation_id': (msg.get('conversationId') or '')[:255] or None,
            }
            if status != InboundEmailLog.STATUS_PENDING:
                fields.update(result=f'skip_{rule}' if rule else 'skip', processed_at=now)
            if old:
                # Fila del delta sin terminar (received/failed): se adopta para
                # que el barrido la reintente en lugar de crear otra.
                InboundEmailLog.objects.filter(pk=old['id']).update(
                    attempts=0, next_attempt_at=None, **fields,
                )
                self.stats['adopted_legacy'] += 1
                continue
            rows.append(InboundEmailLog(
                brand=self.brand, message_id=msg['id'], payload=_summary_payload(msg), **fields,
            ))
        if rows:
            InboundEmailLog.objects.bulk_create(rows, ignore_conflicts=True)
            # ignore_conflicts también se traga un choque inesperado (p. ej. con
            # la unicidad antigua por marca, cuya colación no distingue
            # mayúsculas). Un mensaje sin fila sería una pérdida silenciosa.
            wanted = {row.message_key for row in rows}
            stored = set(InboundEmailLog.objects.filter(
                folder_key=self.folder_key, message_key__in=wanted,
            ).values_list('message_key', flat=True))
            for missing in wanted - stored:
                msg = by_key[missing]
                self.stats['not_registered'] += 1
                self.alerts.append('not_registered')
                logger.error(
                    'inbound_sweep [%s]: %s no se pudo registrar (choque de unicidad); revisar a mano',
                    self.mailbox, log_id(msg['id']),
                    extra={'mailbox': self.mailbox, 'environment': self.environment,
                           'graph_message_id': msg['id'], 'inbound_alert': 'not_registered'},
                )
                record_metric('email.inbound.not_registered', labels={'mailbox': self.mailbox})

    def _flag_gap(self, msg):
        received = _parse(msg.get('receivedDateTime'))
        modified = _parse(msg.get('lastModifiedDateTime'))
        overlap = timedelta(minutes=_setting('INBOUND_SWEEP_OVERLAP_MINUTES', 20))
        late = bool(received and modified and modified - received > overlap)
        kind = 'late_arrival' if late else 'gap'
        self.stats[kind] += 1
        self.alerts.append(kind)
        level = logging.WARNING if late else logging.ERROR
        logger.log(
            level,
            'inbound_sweep [%s]: el barrido de seguridad encontró %s sin registrar (%s, recibido %s)',
            self.mailbox, 'un mensaje movido tarde a la carpeta' if late else 'UN HUECO: un mensaje',
            log_id(msg['id']), msg.get('receivedDateTime'),
            extra={'mailbox': self.mailbox, 'environment': self.environment,
                   'graph_message_id': msg['id'], 'inbound_alert': kind},
        )
        record_metric(f'email.inbound.{kind}', labels={'mailbox': self.mailbox, 'environment': self.environment})

    @staticmethod
    def _report(report, group, msg, ticket=None, rule=None):
        if report is None:
            return
        sender = ((msg.get('from') or {}).get('emailAddress') or {}).get('address') or ''
        report.setdefault(group, []).append({
            'received': msg.get('receivedDateTime'), 'sender': sender,
            'subject': (msg.get('subject') or '')[:90], 'rule': rule, 'ticket': ticket,
        })

    # --- procesado ---------------------------------------------------------

    def _claimable(self, now):
        reclaim = now - timedelta(minutes=_setting('INBOUND_SWEEP_RECLAIM_MINUTES', 10))
        return (
            Q(status=InboundEmailLog.STATUS_PENDING)
            | Q(status=InboundEmailLog.STATUS_FAILED, next_attempt_at__lte=now)
            | Q(status=InboundEmailLog.STATUS_FAILED, next_attempt_at__isnull=True)
            | Q(status=InboundEmailLog.STATUS_PROCESSING, claimed_at__lt=reclaim)
        )

    def claim_next(self):
        """Reclama atómicamente el siguiente pendiente (primero el correo en vivo)."""
        max_attempts = _setting('INBOUND_SWEEP_MAX_ATTEMPTS', 8)
        while True:
            now = self.clock()
            candidates = list(
                InboundEmailLog.objects.filter(folder_key=self.folder_key)
                .filter(self._claimable(now))
                .annotate(_backfill=Case(
                    When(source=InboundEmailLog.SOURCE_BACKFILL, then=Value(1)),
                    default=Value(0), output_field=IntegerField(),
                ))
                .order_by('_backfill', 'graph_received_at', 'id')
                .values('id', 'attempts', 'message_id')[:5]
            )
            if not candidates:
                return None
            for candidate in candidates:
                if candidate['attempts'] >= max_attempts:
                    self._quarantine(candidate, now)
                    continue
                token = uuid.uuid4().hex
                updated = InboundEmailLog.objects.filter(pk=candidate['id']).filter(
                    self._claimable(now),
                ).update(
                    status=InboundEmailLog.STATUS_PROCESSING, claimed_at=now, claim_token=token,
                    # Se cuenta al reclamar: un mensaje que tumba el proceso
                    # (OOM, SIGKILL) también consume intentos y acaba apartado.
                    attempts=F('attempts') + 1,
                )
                if updated == 1:
                    return InboundEmailLog.objects.get(pk=candidate['id'])

    def _quarantine(self, candidate, now):
        updated = InboundEmailLog.objects.filter(pk=candidate['id']).filter(self._claimable(now)).update(
            status=InboundEmailLog.STATUS_QUARANTINED, processed_at=now, result='quarantined',
        )
        if updated:
            self.results['quarantined'] += 1
            self.alerts.append('quarantined')
            logger.error(
                'inbound_sweep [%s]: %s apartado en cuarentena tras %s intentos; revisar en el admin '
                '(InboundEmailLog) y reprocesar a mano', self.mailbox, log_id(candidate['message_id']),
                candidate['attempts'],
                extra={'mailbox': self.mailbox, 'environment': self.environment,
                       'graph_message_id': candidate['message_id'], 'inbound_alert': 'quarantined'},
            )
            record_metric('email.inbound.quarantined', labels={'mailbox': self.mailbox})

    def process_pending(self, deadline):
        while self.clock() < deadline:
            self.renew_lease()
            row = self.claim_next()
            if row is None:
                return
            self.process_row(row)

    def process_row(self, row):
        if row.internet_message_key and InboundEmailLog.objects.filter(
            internet_message_key=row.internet_message_key,
            status__in=(InboundEmailLog.STATUS_PROCESSED, InboundEmailLog.STATUS_DUPLICATE),
        ).exclude(pk=row.pk).exists():
            # La misma pieza de correo ya entró con otro id (se movió de carpeta
            # mientras esperaba): ingerirla otra vez duplicaría el ticket.
            self._finish(row, InboundEmailLog.STATUS_DUPLICATE, 'skip_moved_duplicate')
            self.results['skip_moved_duplicate'] += 1
            return
        try:
            try:
                message = msgraph_mail.get_message(self.mailbox, row.message_id, self.headers)
            except GraphNotFound:
                # Movido de carpeta (el id cambia) o borrado: se busca por Message-ID.
                message = msgraph_mail.find_by_internet_message_id(
                    self.mailbox, row.internet_message_id, self.headers,
                )
                if message is None:
                    self._finish(row, InboundEmailLog.STATUS_SKIPPED, 'skip_gone')
                    logger.warning('inbound_sweep [%s]: %s ya no existe en el buzón; se descarta',
                                   self.mailbox, log_id(row.message_id))
                    return
            attachments = []
            has_inline = 'cid:' in (message.get('body', {}).get('content') or '').lower()
            if (message.get('hasAttachments') or has_inline) and _sender_is_valid(message):
                attachments = msgraph_mail.fetch_attachments(self.mailbox, message['id'], self.headers)
            self.renew_lease()
            row.payload = message
            row.save(update_fields=['payload'])
            result, ticket = EmailIngestionService._process_message(
                message, self.brand, attachments=attachments,
                suppress_confirmation=row.source == InboundEmailLog.SOURCE_BACKFILL,
            )
        except GraphThrottled as exc:
            # No es culpa del mensaje: se devuelve sin gastar intento y se para.
            InboundEmailLog.objects.filter(pk=row.pk, claim_token=row.claim_token).update(
                status=InboundEmailLog.STATUS_PENDING, attempts=F('attempts') - 1,
                next_attempt_at=self.clock() + timedelta(seconds=exc.retry_after), claim_token=None,
            )
            raise
        except LeaseLost:
            raise
        except Exception as exc:
            self._fail(row, exc)
            return

        status = InboundEmailLog.STATUS_DUPLICATE if result == 'skip_dup' else InboundEmailLog.STATUS_PROCESSED
        self._finish(row, status, result, ticket)
        self.results[result] += 1
        logger.info(
            'inbound_sweep [%s] %s (recibido %s) → %s', self.mailbox, log_id(row.message_id),
            message.get('receivedDateTime') or '-', result,
            extra={'mailbox': self.mailbox, 'graph_message_id': row.message_id, 'result': result,
                   'ticket_id': ticket.id if ticket else None, 'inbound_source': row.source},
        )
        if status == InboundEmailLog.STATUS_PROCESSED and _setting('INBOUND_MARK_READ_ENABLED', False):
            msgraph_mail.mark_read(self.mailbox, message['id'], self.headers)

    def _finish(self, row, status, result, ticket=None):
        updated = InboundEmailLog.objects.filter(pk=row.pk, claim_token=row.claim_token).update(
            status=status, result=result, ticket=ticket, processed_at=self.clock(),
            error=None, claim_token=None,
        )
        if not updated:
            logger.warning('inbound_sweep [%s]: %s lo reclamó otro proceso antes de terminar',
                           self.mailbox, log_id(row.message_id))

    def _fail(self, row, exc):
        attempts = InboundEmailLog.objects.filter(pk=row.pk).values_list('attempts', flat=True).first() or 1
        delay = timedelta(minutes=min(2 ** attempts, 60))
        InboundEmailLog.objects.filter(pk=row.pk, claim_token=row.claim_token).update(
            status=InboundEmailLog.STATUS_FAILED, error=str(exc)[:4000],
            next_attempt_at=self.clock() + delay, claim_token=None, processed_at=self.clock(),
        )
        self.results['failed'] += 1
        logger.error(
            'inbound_sweep [%s] %s error (intento %s, siguiente en %s min): %s', self.mailbox,
            log_id(row.message_id), attempts, int(delay.total_seconds() // 60), exc,
            extra={'mailbox': self.mailbox, 'graph_message_id': row.message_id, 'result': 'error',
                   'attempts': attempts},
        )
        record_metric('email.inbound.message_failed', labels={'mailbox': self.mailbox})

    # --- vuelta completa ---------------------------------------------------

    def run(self):
        started = self.clock()
        if not self.acquire_lease():
            logger.info('inbound_sweep [%s]: otra vuelta en curso; se omite', self.mailbox)
            return 'busy'
        outcome, error = 'ok', None
        try:
            self._ensure_floor(started)
            overlap = timedelta(minutes=_setting('INBOUND_SWEEP_OVERLAP_MINUTES', 20))
            cursor = self.cursor
            since = max(cursor.deep_floor, (cursor.swept_until or cursor.deep_floor) - overlap)
            complete, max_received = self.discover(since)
            if self.stats['not_registered']:
                raise RuntimeError('Hay mensajes sin registrar; no se avanza el cursor del barrido')
            # Con el listado completo, todo lo recibido antes de empezar la
            # vuelta y ya visible está registrado: la marca avanza hasta ahí
            # aunque no haya llegado nada. Lo que aparezca después con fecha
            # anterior lo cubre el solape, y si no, el barrido de seguridad.
            mark = max(filter(None, [cursor.swept_until, max_received, started if complete else None]))
            if mark != cursor.swept_until:
                cursor.swept_until = mark
                MailboxSyncCursor.objects.filter(pk=cursor.pk).update(swept_until=mark)
            self._deep_sweep_if_due(started)
            budget = timedelta(seconds=_setting('INBOUND_SWEEP_PROCESS_BUDGET_SECONDS', 40))
            self.process_pending(started + budget)
        except LeaseLost:
            outcome = 'lease_lost'
            logger.warning('inbound_sweep [%s]: otro proceso tomó el buzón; se para la vuelta', self.mailbox)
        except GraphThrottled as exc:
            outcome, error = 'throttled', exc
            logger.warning('inbound_sweep [%s]: Graph pide esperar (%s)', self.mailbox, exc)
        except Exception as exc:
            outcome, error = 'error', exc
            logger.exception('inbound_sweep [%s]: vuelta fallida', self.mailbox)
            record_metric('email.inbound.sweep_failed',
                          labels={'mailbox': self.mailbox, 'environment': self.environment})
        finally:
            self.release_lease()
        self._record_outcome(started, outcome, error)
        return outcome

    def _ensure_floor(self, now):
        if self.cursor.deep_floor:
            return
        lookback = timedelta(minutes=_setting('INBOUND_SWEEP_INITIAL_LOOKBACK_MINUTES', 60))
        self.cursor.deep_floor = now - lookback
        MailboxSyncCursor.objects.filter(pk=self.cursor.pk).update(deep_floor=self.cursor.deep_floor)

    def _deep_sweep_if_due(self, now):
        cursor = self.cursor
        daily_due = not cursor.last_deep_daily_at or now - cursor.last_deep_daily_at >= timedelta(days=1)
        hourly_due = not cursor.last_deep_hourly_at or now - cursor.last_deep_hourly_at >= timedelta(hours=1)
        if not (daily_due or hourly_due):
            return
        window = (timedelta(days=_setting('INBOUND_SWEEP_DEEP_DAILY_DAYS', 14)) if daily_due
                  else timedelta(hours=_setting('INBOUND_SWEEP_DEEP_HOURLY_HOURS', 48)))
        since = max(cursor.deep_floor, now - window)
        complete, _ = self.discover(since, gap_check=True, max_pages=100)
        self.stats['deep_sweeps'] += 1
        if self.stats['not_registered']:
            raise RuntimeError('Hay mensajes sin registrar en el barrido de seguridad')
        if not complete:
            return
        stamp = {'last_deep_hourly_at': now}
        if daily_due:
            stamp['last_deep_daily_at'] = now
        for name, value in stamp.items():
            setattr(cursor, name, value)
        MailboxSyncCursor.objects.filter(pk=cursor.pk).update(**stamp)

    def _record_outcome(self, started, outcome, error):
        now = self.clock()
        backlog = InboundEmailLog.objects.filter(
            folder_key=self.folder_key,
            status__in=(InboundEmailLog.STATUS_PENDING, InboundEmailLog.STATUS_FAILED,
                        InboundEmailLog.STATUS_PROCESSING),
        )
        pending = backlog.count()
        oldest = backlog.order_by('graph_received_at').values_list('graph_received_at', flat=True).first()
        oldest_minutes = int((now - oldest).total_seconds() // 60) if oldest else None
        stats = {
            'outcome': outcome, 'listed': self.stats['listed'], 'new': self.stats['new'],
            'not_registered': self.stats['not_registered'],
            'known': self.stats['known'], 'skipped': self.stats['skipped'],
            'moved_duplicates': self.stats['duplicate'], 'pages': self.stats['pages'],
            'deep_sweeps': self.stats['deep_sweeps'], 'gaps': self.stats['gap'],
            'late_arrivals': self.stats['late_arrival'], 'results': dict(self.results),
            'backlog': pending, 'oldest_pending_minutes': oldest_minutes,
            'swept_until': self.cursor.swept_until.isoformat() if self.cursor.swept_until else None,
            'duration_ms': int((now - started).total_seconds() * 1000),
        }
        fields = {'last_sweep_stats': stats, 'last_message_count': self.stats['listed'],
                  'last_result': f'sweep_{outcome}'}
        if outcome == 'ok':
            fields.update(last_sweep_ok_at=now, last_synced_at=now, last_error=None)
            self.cursor.last_sweep_ok_at = now
        elif error is not None:
            fields['last_error'] = str(error)[:2000]
        MailboxSyncCursor.objects.filter(pk=self.cursor.pk).update(**fields)

        detail = ' '.join(f'{k}={v}' for k, v in sorted(self.results.items())) or 'sin procesados'
        received = sorted(self.new_received)
        logger.log(
            logging.INFO if outcome in ('ok', 'busy') else logging.WARNING,
            'inbound_sweep [%s]: vuelta %s · %s listados · %s nuevos · %s conocidos · %s · '
            'pendientes %s (más antiguo %s min) · barrido hasta %s',
            self.mailbox, outcome, stats['listed'], stats['new'], stats['known'], detail,
            pending, oldest_minutes if oldest_minutes is not None else '-', stats['swept_until'] or '-',
            extra={'mailbox': self.mailbox, 'environment': self.environment,
                   'inbound_sweep_summary': stats,
                   'new_received_from': received[0] if received else None,
                   'new_received_to': received[-1] if received else None},
        )
        pending_alert = _setting('INBOUND_SWEEP_PENDING_ALERT_MINUTES', 10)
        if oldest_minutes is not None and oldest_minutes >= pending_alert:
            logger.error(
                'inbound_sweep [%s]: hay %s mensajes sin ingerir; el más antiguo lleva %s min',
                self.mailbox, pending, oldest_minutes,
                extra={'mailbox': self.mailbox, 'environment': self.environment,
                       'inbound_alert': 'pending_too_old', 'oldest_pending_minutes': oldest_minutes},
            )
            record_metric('email.inbound.pending_too_old', oldest_minutes, labels={'mailbox': self.mailbox})
        self._warn_if_stale(now)

    def _warn_if_stale(self, now):
        threshold = _setting('INBOUND_CURSOR_STALE_MINUTES', 5)
        # Un cursor que nunca ha tenido una vuelta buena cuenta desde que se
        # activó (deep_floor es el arranque menos la ventana inicial).
        last_ok = self.cursor.last_sweep_ok_at or (
            self.cursor.deep_floor
            + timedelta(minutes=_setting('INBOUND_SWEEP_INITIAL_LOOKBACK_MINUTES', 60))
            if self.cursor.deep_floor else None
        )
        if not last_ok or threshold <= 0:
            return
        minutes = int((now - last_ok).total_seconds() // 60)
        if minutes < threshold:
            return
        logger.error(
            'inbound_sweep [%s]: sin una vuelta buena desde %s (%s min); el correo nuevo no entra. '
            'Último error: %s', self.mailbox, last_ok.isoformat(timespec='seconds'), minutes,
            (self.cursor.last_error or '')[:300],
            extra={'mailbox': self.mailbox, 'environment': self.environment,
                   'inbound_alert': 'sweep_stale', 'cursor_stale_minutes': minutes},
        )
        record_metric('email.inbound.cursor_stale', minutes,
                      labels={'mailbox': self.mailbox, 'environment': self.environment})


def _summary_payload(msg):
    """Lo que se guarda al descubrir: metadatos, nunca el cuerpo."""
    return {k: msg.get(k) for k in ('id', 'subject', 'from', 'receivedDateTime', 'conversationId',
                                    'internetMessageId', 'hasAttachments') if k in msg}


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def run_sweep(environment=None, clock=timezone.now):
    environment = environment or _setting('ENVIRONMENT_NAME', 'local')
    brands = list(Brand.objects.filter(mailbox_type='m365').exclude(support_email='').order_by('id'))
    if not brands:
        logger.info('inbound_sweep: no hay marcas con buzón M365')
        return 'no_brands'
    try:
        headers = graph_headers(get_graph_token())
    except Exception as exc:
        logger.error('inbound_sweep: no se pudo obtener el token de Graph: %s', exc,
                     extra={'inbound_alert': 'token_error', 'environment': environment})
        record_metric('email.inbound.token_error', labels={'environment': environment})
        return 'token_error'

    targets, all_resolved = resolve_targets(brands, headers)
    outcomes = []
    for target in targets:
        try:
            cursor = get_cursor(target, environment, allow_create=all_resolved)
            if cursor is None:
                continue
            if cursor.ingest_mode != MailboxSyncCursor.MODE_SWEEP:
                logger.warning(
                    'inbound_sweep [%s]: cursor en pausa (ingest_mode=%s); no se recoge correo',
                    cursor.mailbox, cursor.ingest_mode,
                    extra={'mailbox': cursor.mailbox, 'environment': environment,
                           'inbound_alert': 'paused'},
                )
                outcomes.append(f'{cursor.mailbox}:paused')
                continue
            outcome = MailboxSweep(cursor, target, headers, environment, clock=clock).run()
            outcomes.append(f'{cursor.mailbox}:{outcome}')
        except Exception:
            logger.exception('inbound_sweep [%s]: vuelta abortada', target['mailbox'])
            record_metric('email.inbound.sweep_aborted',
                          labels={'mailbox': target['mailbox'], 'environment': environment})
            outcomes.append(f'{target["mailbox"]}:aborted')
    return ', '.join(outcomes) or 'no_targets'
