"""Recogida de correo M365 por barrido de ventana.

Cada clase reproduce un fallo real o un límite del diseño. Las tres pérdidas de
correo de PRO (29/09–02/10/2026) tenían el mismo origen: el estado de la
recogida vivía en Graph (delta query) y una vuelta fallida, concurrente o
interrumpida lo dejaba inservible sin dejar rastro. Aquí la verdad está en
InboundEmailLog y estos tests comprueban que ningún correo listado se pierde.
"""

import io
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from urllib.parse import unquote

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings

from app import tasks
from app.models import (
    Brand, Comment, InboundEmailLog, MailboxSyncCursor, OperationalMetric, Role, Ticket,
)
from app.services import mailbox_sweep
from app.services.email_ingestion import EmailIngestionService
from app.services.mailbox_sweep import MailboxSweep, folder_key, run_sweep, sha256
from app.services.tickets import TicketService

MAILBOX = 'helpdesk@cloudwws.example'
FOLDER = 'INBOX-FOLDER-ID'
T0 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=dt_timezone.utc)


def iso(value):
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def mail(n, at, subject=None, sender='cliente@example.com', **extra):
    msg = {
        'id': f'AAMkAGY1ODFkYzg1-{n}',
        'internetMessageId': f'<msg-{n}@example.com>',
        'conversationId': f'conv-{n}',
        'subject': subject if subject is not None else f'Consulta {n}',
        'from': {'emailAddress': {'address': sender, 'name': 'Cliente'}},
        'body': {'contentType': 'html', 'content': f'<body><p>Hola {n}</p></body>'},
        'receivedDateTime': iso(at),
        'lastModifiedDateTime': iso(at),
        'hasAttachments': False,
        'internetMessageHeaders': [],
    }
    msg.update(extra)
    return msg


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class _Response:
    def __init__(self, status=200, payload=None, content=b'', headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}', response=self)


_TS = r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)'


class FakeMailbox:
    """Graph en memoria: una carpeta Inbox (más otras) con mensajes mutables."""

    def __init__(self, aliases=(MAILBOX,)):
        self.aliases = set(aliases)
        self.folders = {FOLDER: [], 'ARCHIVE': []}
        self.fail_resolve = set()
        self.throttle_lists = 0
        self.fail_get = {}
        self.attachment_errors = set()
        self.on_list = None
        self.list_calls = 0
        self.gets = []
        self.patched = []

    # --- manipulación del buzón -------------------------------------------
    def deliver(self, *messages, folder=FOLDER):
        self.folders[folder].extend(messages)

    def find(self, msg_id):
        for folder, items in self.folders.items():
            for msg in items:
                if msg['id'] == msg_id:
                    return folder, msg
        return None, None

    def move(self, msg_id, folder, new_id):
        src, msg = self.find(msg_id)
        self.folders[src].remove(msg)
        moved = dict(msg, id=new_id)
        self.folders[folder].append(moved)
        return moved

    def delete(self, msg_id):
        src, msg = self.find(msg_id)
        self.folders[src].remove(msg)

    # --- API ---------------------------------------------------------------
    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        params = params or {}
        path = url.split('/v1.0', 1)[1]
        mailbox = unquote(path.split('/users/')[1].split('/')[0])
        if path.endswith('/mailFolders/Inbox'):
            if mailbox in self.fail_resolve:
                return _Response(500)
            return _Response(payload={'id': FOLDER})
        m = re.search(r'/mailFolders/([^/]+)/messages$', path)
        if m:
            return self._list(unquote(m.group(1)), params)
        if path.endswith('/$value'):
            return _Response(content=b'contenido')
        m = re.search(r'/messages/([^/]+)/attachments$', path)
        if m:
            if unquote(m.group(1)) in self.attachment_errors:
                return _Response(500)
            return _Response(payload={'value': []})
        m = re.search(r'/messages/([^/]+)$', path)
        if m:
            msg_id = unquote(m.group(1))
            if method == 'PATCH':
                self.patched.append(msg_id)
                return _Response(payload={})
            self.gets.append(msg_id)
            if self.fail_get.get(msg_id):
                self.fail_get[msg_id] -= 1
                return _Response(500)
            _folder, msg = self.find(msg_id)
            return _Response(payload=dict(msg)) if msg else _Response(404)
        if path.endswith('/messages'):
            wanted = re.search(r"internetMessageId eq '(.*)'", params.get('$filter', '')).group(1)
            found = [dict(msg) for items in self.folders.values() for msg in items
                     if msg.get('internetMessageId') == wanted.replace("''", "'")]
            return _Response(payload={'value': found[:1]})
        raise AssertionError(f'petición no prevista: {method} {url}')

    def _list(self, folder, params):
        self.list_calls += 1
        if self.on_list:
            self.on_list(self.list_calls)
        if self.throttle_lists:
            self.throttle_lists -= 1
            return _Response(429, headers={'Retry-After': '7'})
        flt = params['$filter']
        since = re.search(r'receivedDateTime ge ' + _TS, flt).group(1)
        until = re.search(r'receivedDateTime le ' + _TS, flt)
        items = [m for m in self.folders.get(folder, []) if m['receivedDateTime'] >= since]
        if until:
            items = [m for m in items if m['receivedDateTime'] <= until.group(1)]
        items.sort(key=lambda m: m['receivedDateTime'])
        skip, top = int(params.get('$skip', 0)), int(params['$top'])
        fields = params['$select'].split(',')
        page = [{k: m[k] for k in fields if k in m} for m in items[skip:skip + top]]
        return _Response(payload={'value': page})


class _SweepFixture(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role_name='End user')
        Role.objects.get_or_create(role_name='agent')
        self.brand = Brand.objects.create(
            name='Cloudwws', support_email=MAILBOX, mailbox_type='m365', language='es',
        )
        self.graph = FakeMailbox()
        self.clock = Clock()

    def sweep(self):
        with patch.object(mailbox_sweep, 'get_graph_token', return_value='token'), \
                patch('app.services.msgraph_mail.requests.request', side_effect=self.graph.request):
            return run_sweep(environment='Staging', clock=self.clock)

    def cursor(self):
        return MailboxSyncCursor.objects.get(environment='Staging')

    def log(self, msg):
        return InboundEmailLog.objects.get(message_key=sha256(msg['id']))

    def tickets_for(self, msg):
        return Ticket.objects.filter(email_message_id=msg['id']).count()


@override_settings(INBOUND_MARK_READ_ENABLED=False)
class SweepBasicsTests(_SweepFixture):
    def test_first_sweep_starts_an_hour_back_and_ingests(self):
        old = mail(1, T0 - timedelta(hours=3))
        recent = mail(2, T0 - timedelta(minutes=10))
        self.graph.deliver(old, recent)

        self.assertIn('ok', self.sweep())

        cursor = self.cursor()
        self.assertEqual(cursor.ingest_mode, MailboxSyncCursor.MODE_SWEEP)
        self.assertEqual(cursor.brand, self.brand)
        self.assertEqual(cursor.deep_floor, T0 - timedelta(minutes=60))
        self.assertEqual(self.tickets_for(recent), 1)
        self.assertEqual(self.tickets_for(old), 0, 'lo anterior al arranque solo entra por backfill')
        self.assertEqual(self.log(recent).status, InboundEmailLog.STATUS_PROCESSED)
        self.assertEqual(cursor.last_sweep_ok_at, T0)

    def test_repeated_sweeps_never_duplicate_and_pick_up_new_mail(self):
        first = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(first)
        self.sweep()
        self.clock.advance(minutes=1)
        second = mail(2, T0 + timedelta(seconds=30))
        self.graph.deliver(second)
        self.sweep()
        self.sweep()
        self.assertEqual(self.tickets_for(first), 1)
        self.assertEqual(self.tickets_for(second), 1)
        self.assertEqual(InboundEmailLog.objects.count(), 2)

    def test_reply_to_a_ticket_is_added_as_a_comment(self):
        opening = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(opening)
        self.sweep()
        ticket = Ticket.objects.get(email_message_id=opening['id'])
        reply = mail(2, T0 - timedelta(minutes=1), subject=f'RE: [Ticket #{ticket.id}] Respuesta de soporte',
                     conversationId='otra-conversacion')
        self.graph.deliver(reply)
        self.clock.advance(minutes=1)
        self.sweep()
        self.assertEqual(self.log(reply).result, 'comment_added')
        self.assertTrue(Comment.objects.filter(ticket=ticket, email_message_id=reply['id']).exists())

    def test_summary_line_is_logged_every_round(self):
        self.graph.deliver(mail(1, T0 - timedelta(minutes=5)))
        with self.assertLogs('app.services.mailbox_sweep', level='INFO') as logs:
            self.sweep()
        summary = [line for line in logs.output if ': vuelta ok' in line]
        self.assertEqual(len(summary), 1, logs.output)
        self.assertIn('1 nuevos', summary[0])

    def test_paused_cursor_does_nothing_and_says_so(self):
        self.sweep()
        MailboxSyncCursor.objects.update(ingest_mode=MailboxSyncCursor.MODE_PAUSED)
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        with self.assertLogs('app.services.mailbox_sweep', level='WARNING') as logs:
            self.assertIn('paused', self.sweep())
        self.assertTrue(any('en pausa' in line for line in logs.output))
        self.assertEqual(self.tickets_for(msg), 0)

    def test_mark_read_only_when_enabled(self):
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        self.sweep()
        self.assertEqual(self.graph.patched, [])
        other = mail(2, T0 - timedelta(seconds=30))
        self.graph.deliver(other)
        with override_settings(INBOUND_MARK_READ_ENABLED=True):
            self.sweep()
        self.assertEqual(self.graph.patched, [other['id']])

    def test_poll_alias_runs_the_sweep(self):
        # El alias usa el reloj real: el mensaje se fecha respecto a ahora.
        from django.utils import timezone

        msg = mail(1, timezone.now() - timedelta(minutes=1))
        self.graph.deliver(msg)
        with patch.object(mailbox_sweep, 'get_graph_token', return_value='token'), \
                patch('app.services.msgraph_mail.requests.request', side_effect=self.graph.request), \
                override_settings(ENVIRONMENT_NAME='Staging'):
            tasks.poll_m365_mailboxes()
        self.assertEqual(self.tickets_for(msg), 1)


@override_settings(INBOUND_SWEEP_MAX_ATTEMPTS=3)
class FailureIsolationTests(_SweepFixture):
    """Un mensaje que falla nunca frena a los demás (incidente del 29/09)."""

    def test_poison_message_does_not_block_others_and_ends_in_quarantine(self):
        poison = mail(1, T0 - timedelta(minutes=9))
        behind = mail(2, T0 - timedelta(minutes=8))
        self.graph.deliver(poison, behind)
        original = EmailIngestionService._process_message

        def explode(message, brand, **kwargs):
            if message['id'] == poison['id']:
                raise RuntimeError('Data too long')
            return original(message, brand, **kwargs)

        with patch.object(EmailIngestionService, '_process_message', side_effect=explode):
            self.sweep()
            self.assertEqual(self.tickets_for(behind), 1, 'lo de detrás entra en la misma vuelta')
            row = self.log(poison)
            self.assertEqual(row.status, InboundEmailLog.STATUS_FAILED)
            self.assertGreater(row.next_attempt_at, self.clock.now)
            for _ in range(5):
                self.clock.advance(hours=2)
                self.sweep()
        self.assertEqual(self.log(poison).status, InboundEmailLog.STATUS_QUARANTINED)
        self.assertTrue(OperationalMetric.objects.filter(name='email.inbound.quarantined').exists())
        self.assertEqual(self.tickets_for(behind), 1)

    def test_invalid_sender_is_terminal_and_does_not_block(self):
        bad = mail(1, T0 - timedelta(minutes=9), sender='a' * 248 + '@example.com')
        good = mail(2, T0 - timedelta(minutes=8))
        self.graph.deliver(bad, good)
        self.sweep()
        self.assertEqual(self.log(bad).status, InboundEmailLog.STATUS_PROCESSED)
        self.assertEqual(self.log(bad).result, 'skip_invalid_sender')
        self.assertEqual(self.tickets_for(good), 1)

    def test_attachment_failure_leaves_a_retryable_row(self):
        msg = mail(1, T0 - timedelta(minutes=5), hasAttachments=True)
        self.graph.deliver(msg)
        self.graph.attachment_errors.add(msg['id'])
        self.sweep()
        row = self.log(msg)
        self.assertEqual(row.status, InboundEmailLog.STATUS_FAILED)
        self.assertEqual(self.tickets_for(msg), 0)
        self.graph.attachment_errors.clear()
        self.clock.advance(minutes=3)
        self.sweep()
        self.assertEqual(self.log(msg).status, InboundEmailLog.STATUS_PROCESSED)
        self.assertEqual(self.tickets_for(msg), 1)

    def test_throttling_does_not_spend_attempts_or_advance_the_watermark(self):
        self.sweep()
        self.clock.advance(minutes=1)
        msg = mail(1, T0 + timedelta(seconds=20))
        self.graph.deliver(msg)
        before = self.cursor().swept_until
        self.graph.throttle_lists = 1
        self.assertIn('throttled', self.sweep())
        self.assertEqual(self.cursor().swept_until, before)
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 1)


class CrashRecoveryTests(_SweepFixture):
    """Un pod que muere a mitad no pierde ni duplica (incidente del 02/10)."""

    def _register_only(self, *messages):
        self.graph.deliver(*messages)
        with patch.object(MailboxSweep, 'process_pending'):
            self.sweep()

    def test_worker_killed_after_claiming_is_retaken_once(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self._register_only(msg)
        InboundEmailLog.objects.filter(pk=self.log(msg).pk).update(
            status=InboundEmailLog.STATUS_PROCESSING, claimed_at=self.clock.now, claim_token='muerto',
            attempts=1,
        )
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 0, 'un reclamo reciente se respeta')
        self.clock.advance(minutes=11)
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 1)
        self.assertEqual(self.log(msg).status, InboundEmailLog.STATUS_PROCESSED)

    def test_worker_killed_after_creating_the_ticket_does_not_duplicate(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self._register_only(msg)
        EmailIngestionService._process_message(self.graph.find(msg['id'])[1], self.brand)
        InboundEmailLog.objects.filter(pk=self.log(msg).pk).update(
            status=InboundEmailLog.STATUS_PROCESSING, claimed_at=self.clock.now - timedelta(minutes=30),
            claim_token='muerto', attempts=1,
        )
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 1)
        self.assertEqual(self.log(msg).status, InboundEmailLog.STATUS_DUPLICATE)


class ConcurrencyTests(_SweepFixture):
    """Vueltas solapadas (2 réplicas × 2 procesos, beat sin expires)."""

    def setUp(self):
        super().setUp()
        self.sweep()
        self.cursor_obj = self.cursor()
        self.target = {'folder_key': folder_key(FOLDER), 'folder_id': FOLDER, 'mailbox': MAILBOX,
                       'brand': self.brand, 'shared_with': []}

    def make(self, owner):
        return MailboxSweep(self.cursor(), self.target, {}, 'Staging', clock=self.clock, owner=owner)

    def test_only_one_sweep_holds_the_lease(self):
        first, second = self.make('a'), self.make('b')
        self.assertTrue(first.acquire_lease())
        self.assertFalse(second.acquire_lease())
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        self.assertIn('busy', self.sweep())
        self.assertEqual(self.tickets_for(msg), 0)
        self.clock.advance(seconds=200)
        self.assertTrue(second.acquire_lease(), 'un lease caducado se puede tomar')

    def test_losing_the_lease_mid_round_aborts(self):
        def steal(_call):
            MailboxSyncCursor.objects.update(lease_owner='otro', lease_until=self.clock.now + timedelta(hours=1))

        self.graph.on_list = steal
        self.graph.deliver(mail(1, T0 - timedelta(minutes=1)))
        with self.assertLogs('app.services.mailbox_sweep', level='WARNING') as logs:
            self.assertIn('lease_lost', self.sweep())
        self.assertTrue(any('otro proceso tomó el buzón' in line for line in logs.output))

    def test_a_row_cannot_be_claimed_twice(self):
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        with patch.object(MailboxSweep, 'process_pending'):
            self.sweep()
        first, second = self.make('a'), self.make('b')
        self.assertIsNotNone(first.claim_next())
        self.assertIsNone(second.claim_next())


class ListingEdgeCaseTests(_SweepFixture):
    @override_settings(INBOUND_SWEEP_PAGE_SIZE=2)
    def test_deleting_a_message_while_paging_does_not_skip_others(self):
        msgs = [mail(i, T0 - timedelta(minutes=10 - i)) for i in range(6)]
        self.graph.deliver(*msgs)

        def delete_first(call):
            if call == 2:
                self.graph.delete(msgs[0]['id'])

        self.graph.on_list = delete_first
        self.sweep()
        for msg in msgs[1:]:
            self.assertEqual(self.tickets_for(msg), 1, msg['id'])

    @override_settings(INBOUND_SWEEP_PAGE_SIZE=2)
    def test_more_ties_than_a_page_are_all_registered(self):
        same = T0 - timedelta(minutes=3)
        msgs = [mail(i, same) for i in range(5)] + [mail(9, T0 - timedelta(minutes=1))]
        self.graph.deliver(*msgs)
        self.sweep()
        for msg in msgs:
            self.assertEqual(self.tickets_for(msg), 1, msg['id'])

    def test_alias_failure_skips_creating_a_new_cursor(self):
        Brand.objects.create(name='Comuny', support_email='helpdesk@comuny.example',
                             mailbox_type='m365', language='es')
        self.graph.fail_resolve.add(MAILBOX)
        self.sweep()
        self.assertFalse(MailboxSyncCursor.objects.exists(), 'la marca no puede depender del alias que respondió')
        self.graph.fail_resolve.clear()
        self.sweep()
        self.assertEqual(self.cursor().brand, self.brand)
        self.graph.fail_resolve.add(MAILBOX)
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        self.sweep()
        self.assertEqual(Ticket.objects.get(email_message_id=msg['id']).brand, self.brand)

    def test_moved_message_with_same_message_id_is_not_ingested_twice(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(msg)
        self.sweep()
        self.graph.move(msg['id'], 'ARCHIVE', 'AAMkAGY1ODFkYzg1-archivado')
        self.graph.move('AAMkAGY1ODFkYzg1-archivado', FOLDER, 'AAMkAGY1ODFkYzg1-devuelto')
        self.clock.advance(minutes=1)
        self.sweep()
        self.assertEqual(Ticket.objects.count(), 1)
        moved = InboundEmailLog.objects.get(message_key=sha256('AAMkAGY1ODFkYzg1-devuelto'))
        self.assertEqual(moved.status, InboundEmailLog.STATUS_DUPLICATE)

    def test_message_moved_before_processing_is_found_by_message_id(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(msg)
        with patch.object(MailboxSweep, 'process_pending'):
            self.sweep()
        self.graph.move(msg['id'], 'ARCHIVE', 'AAMkAGY1ODFkYzg1-otra-carpeta')
        self.sweep()
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(self.log(msg).status, InboundEmailLog.STATUS_PROCESSED)


class SilentLossGuardTests(_SweepFixture):
    def test_a_row_dropped_by_the_insert_is_reported(self):
        msg = mail(1, T0 - timedelta(minutes=1))
        self.graph.deliver(msg)
        original = InboundEmailLog.objects.bulk_create

        def drop_all(rows, **kwargs):
            return original([], **kwargs)

        with patch.object(InboundEmailLog.objects, 'bulk_create', side_effect=drop_all),                 self.assertLogs('app.services.mailbox_sweep', level='ERROR') as logs:
            self.sweep()
        self.assertTrue(any('no se pudo registrar' in line for line in logs.output))
        self.assertTrue(OperationalMetric.objects.filter(name='email.inbound.not_registered').exists())


class SafetySweepTests(_SweepFixture):
    """Los barridos de seguridad encuentran lo que el rápido no vio."""

    def test_late_arrival_is_ingested_and_flagged(self):
        self.sweep()
        self.clock.advance(minutes=50)
        self.sweep()
        late = mail(1, T0 - timedelta(minutes=40))
        late['lastModifiedDateTime'] = iso(self.clock.now)
        self.graph.deliver(late)
        self.clock.advance(minutes=1)
        self.sweep()
        self.assertEqual(self.tickets_for(late), 0, 'cae fuera del solape del barrido rápido')
        self.clock.advance(minutes=70)
        with self.assertLogs('app.services.mailbox_sweep', level='WARNING') as logs:
            self.sweep()
        self.assertEqual(self.tickets_for(late), 1)
        self.assertTrue(any('movido tarde' in line for line in logs.output))

    def test_true_gap_raises_an_error(self):
        self.sweep()
        self.clock.advance(minutes=50)
        self.sweep()
        missed = mail(1, T0 - timedelta(minutes=40))
        self.graph.deliver(missed)
        self.clock.advance(minutes=71)
        with self.assertLogs('app.services.mailbox_sweep', level='ERROR') as logs:
            self.sweep()
        self.assertTrue(any('UN HUECO' in line for line in logs.output))
        self.assertEqual(self.tickets_for(missed), 1)
        self.assertTrue(OperationalMetric.objects.filter(name='email.inbound.gap').exists())

    def test_safety_sweep_never_goes_below_the_floor(self):
        before_floor = mail(1, T0 - timedelta(hours=5))
        self.graph.deliver(before_floor)
        self.sweep()
        self.clock.advance(days=1, minutes=1)
        self.sweep()
        self.assertEqual(self.tickets_for(before_floor), 0)


class LegacyRowsTests(_SweepFixture):
    """Filas del delta (sin message_key) conviven con el barrido."""

    def test_processed_legacy_row_is_known(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(msg)
        InboundEmailLog.objects.create(brand=self.brand, message_id=msg['id'],
                                       status=InboundEmailLog.STATUS_PROCESSED)
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 0)
        self.assertEqual(InboundEmailLog.objects.count(), 1)

    def test_failed_legacy_row_is_adopted_and_retried(self):
        msg = mail(1, T0 - timedelta(minutes=5))
        self.graph.deliver(msg)
        InboundEmailLog.objects.create(brand=self.brand, message_id=msg['id'],
                                       status=InboundEmailLog.STATUS_FAILED, error='viejo')
        self.sweep()
        self.assertEqual(self.tickets_for(msg), 1)
        self.assertEqual(InboundEmailLog.objects.count(), 1)
        self.assertEqual(self.log(msg).status, InboundEmailLog.STATUS_PROCESSED)


class BackfillTests(_SweepFixture):
    """reingest_mailbox: solo correo humano, sin confirmaciones, revisable."""

    def setUp(self):
        super().setUp()
        self.sweep()  # arranca el cursor: deep_floor = T0 - 60 min
        start = T0 - timedelta(days=3)
        self.human = mail(1, start, subject='No funciona la grabación')
        self.alarm = mail(2, start + timedelta(minutes=1),
                          subject='ALARM: "PRO-SLP-ASG-Error-High" in EU (Ireland)',
                          sender='no-reply@sns.amazonaws.com')
        self.grafana = mail(3, start + timedelta(minutes=2), subject='[FIRING:1] TestAlert Grafana',
                            sender='grafana@cloudwws.example')
        self.bounce = mail(4, start + timedelta(minutes=3), subject='Undeliverable: hola',
                           sender='postmaster@outlook.com')
        self.ooo = mail(5, start + timedelta(minutes=4), subject='Respuesta automática: vacaciones')
        self.reply = mail(6, start + timedelta(minutes=5), subject='RE: [Ticket #999] Respuesta de soporte',
                          sender='notifications@cliente.example')
        self.bulk = mail(7, start + timedelta(minutes=6), subject='Novedades de octubre',
                         internetMessageHeaders=[{'name': 'List-Unsubscribe', 'value': '<mailto:x>'}])
        self.graph.deliver(self.human, self.alarm, self.grafana, self.bounce, self.ooo, self.reply, self.bulk)

    def backfill(self, *extra):
        out = io.StringIO()
        with patch('app.management.commands.reingest_mailbox.get_graph_token', return_value='token'), \
                patch('app.services.msgraph_mail.requests.request', side_effect=self.graph.request), \
                override_settings(ENVIRONMENT_NAME='Staging'):
            call_command('reingest_mailbox', '--since', iso(T0 - timedelta(days=4)), *extra, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing_and_reports_groups(self):
        before = InboundEmailLog.objects.count()
        report = self.backfill('--dry-run')
        self.assertEqual(InboundEmailLog.objects.count(), before)
        self.assertIn('Se recuperarán (correo humano): 2', report)
        self.assertIn('Automáticos (se descartan): 4', report)
        self.assertIn('Dudosos (se descartan salvo --include-review): 1', report)
        self.assertIn('sender_aws_sns', report)
        self.assertIn('subject_resolved_firing', report)
        self.assertIn('[ticket_reference]', report)

    def test_only_human_mail_is_recovered_without_confirmations(self):
        self.backfill()
        self.assertEqual(self.log(self.alarm).status, InboundEmailLog.STATUS_SKIPPED)
        self.assertEqual(self.log(self.bulk).status, InboundEmailLog.STATUS_SKIPPED)
        self.assertEqual(self.log(self.human).status, InboundEmailLog.STATUS_PENDING)
        self.assertEqual(self.log(self.human).source, InboundEmailLog.SOURCE_BACKFILL)

        with patch.object(TicketService, 'create_ticket', wraps=TicketService.create_ticket) as create:
            self.sweep()
        self.assertEqual(self.tickets_for(self.human), 1)
        self.assertEqual(self.tickets_for(self.reply), 1)
        for name in ('alarm', 'grafana', 'bounce', 'ooo', 'bulk'):
            self.assertEqual(self.tickets_for(getattr(self, name)), 0, name)
        self.assertTrue(all(call.args[1]['suppress_requester_email'] for call in create.call_args_list))

    def test_excluded_mail_is_not_rediscovered_by_safety_sweeps(self):
        self.backfill()
        self.clock.advance(days=1, minutes=1)
        self.sweep()
        self.assertEqual(self.tickets_for(self.alarm), 0)

    def test_live_mail_is_processed_before_backfill(self):
        self.backfill()
        live = mail(50, T0 + timedelta(seconds=10))
        self.graph.deliver(live)
        self.clock.advance(minutes=1)
        order = []
        original = EmailIngestionService._process_message

        def spy(message, brand, **kwargs):
            order.append(message['id'])
            return original(message, brand, **kwargs)

        with patch.object(EmailIngestionService, '_process_message', side_effect=spy):
            self.sweep()
        self.assertEqual(order[0], live['id'])


class SchemaTests(TestCase):
    def test_new_columns_are_safe_for_pods_running_the_previous_image(self):
        """Durante el rollout, los pods viejos insertan sin conocer las columnas."""
        from django.db.models.fields import NOT_PROVIDED

        new = {
            MailboxSyncCursor: ['ingest_mode', 'brand', 'swept_until', 'deep_floor', 'last_sweep_ok_at',
                                'last_deep_hourly_at', 'last_deep_daily_at', 'lease_owner', 'lease_until',
                                'last_sweep_stats'],
            InboundEmailLog: ['folder_key', 'message_key', 'graph_received_at', 'internet_message_id',
                              'internet_message_key', 'source', 'attempts', 'next_attempt_at',
                              'claimed_at', 'claim_token', 'automated_rule'],
        }
        for model, names in new.items():
            for name in names:
                field = model._meta.get_field(name)
                with self.subTest(model=model.__name__, field=name):
                    self.assertTrue(field.null or field.db_default is not NOT_PROVIDED)


class InternalzTests(_SweepFixture):
    def test_inbound_block_reports_health(self):
        self.graph.deliver(mail(1, T0 - timedelta(minutes=1)))
        self.sweep()
        with patch('django.utils.timezone.now', return_value=self.clock.now), \
                override_settings(ENVIRONMENT_NAME='Staging'):
            from health.views import _inbound_status
            status = _inbound_status()
        self.assertTrue(status['ok'], status)
        self.assertEqual(status['mailboxes'][0]['pending'], 0)
