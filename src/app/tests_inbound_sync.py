"""Sincronizacion incremental de buzones M365 con cursor propio por entorno.

Lo que se protege aqui es la propiedad que motivo el cambio: el puntero de
lectura vive en la base de datos de CADA entorno, no en el flag isRead del
buzon. Con isRead, DEV y STA leian el mismo buzon fisico y el primero que
pasaba se llevaba el mensaje; el otro no volvia a verlo nunca.
"""

from unittest.mock import patch
from urllib.parse import unquote

from django.test import TestCase, override_settings

from app import tasks
from app.models import Brand, InboundEmailLog, MailboxSyncCursor, Role, Ticket, User
from app.services.email_ingestion import EmailIngestionService, InvalidEmailSender


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


def graph_message(msg_id, **extra):
    payload = {
        'id': msg_id,
        'conversationId': f'conv-{msg_id}',
        'subject': f'Asunto {msg_id}',
        'from': {'emailAddress': {'address': 'cliente@example.com', 'name': 'Cliente'}},
        'body': {'contentType': 'html', 'content': '<body><p>Hola</p></body>'},
    }
    payload.update(extra)
    return payload


def page(messages, delta_link=None, next_link=None):
    body = {'value': list(messages)}
    if delta_link:
        body['@odata.deltaLink'] = delta_link
    if next_link:
        body['@odata.nextLink'] = next_link
    return body


class FakeGraph:
    """Graph de mentira: resuelve Inbox y sirve rondas de delta.

    Una ronda es una lista de paginas; la ultima debe llevar deltaLink. Cuando se
    agotan las rondas, `repeat` decide si se repite la ultima (para simular dos
    consumidores independientes viendo lo mismo) o si se sirve una ronda vacia.
    """

    def __init__(self, folder_by_mailbox, rounds, repeat=False):
        self.folder_by_mailbox = folder_by_mailbox
        self.rounds = rounds
        self.repeat = repeat
        self.delta_urls = []
        self.served_message_ids = []
        self.patched = []
        self._round = 0
        self._pending = []

    def get(self, url, headers=None, timeout=None):
        if '/mailFolders/Inbox?' in url:
            mailbox = unquote(url.split('/users/')[1].split('/')[0])
            return _Response({'id': self.folder_by_mailbox[mailbox]})

        if not self._pending:
            self.delta_urls.append(url)
            if self._round < len(self.rounds):
                self._pending = list(self.rounds[self._round])
                self._round += 1
            elif self.repeat and self.rounds:
                self._pending = list(self.rounds[-1])
            else:
                self._pending = [page([], delta_link='https://graph.test/delta/empty')]
        body = self._pending.pop(0)
        self.served_message_ids.extend(
            msg['id'] for msg in body.get('value', []) if '@removed' not in msg
        )
        return _Response(body)

    def patch_(self, url, headers=None, json=None, timeout=None):
        self.patched.append(url)
        return _Response({})


class _SyncFixture(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role_name='End user')
        Role.objects.get_or_create(role_name='agent')
        self.brand = Brand.objects.create(
            name='Cloudwws', support_email='helpdesk@cloudwws.example',
            mailbox_type='m365', language='es',
        )

    def run_poll(self, graph):
        with patch.object(tasks, '_get_graph_token', return_value='token'), \
                patch.object(tasks.requests, 'get', side_effect=graph.get), \
                patch.object(tasks.requests, 'patch', side_effect=graph.patch_):
            return tasks.poll_m365_mailboxes()


@override_settings(ENVIRONMENT_NAME='Staging')
class MailboxCursorTests(_SyncFixture):
    def test_first_poll_creates_cursor_and_stores_delta_link(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        self.assertTrue(Ticket.objects.filter(email_message_id='msg-1').exists())
        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')
        self.assertEqual(cursor.link_kind, 'delta')
        self.assertEqual(cursor.last_result, 'delta')
        self.assertEqual(cursor.last_message_count, 1)
        self.assertEqual(tasks._folder_key('FOLDER-1'), cursor.folder_key)

    def test_initial_round_is_bounded_by_a_date_filter(self):
        # Sin cota, un delta recien estrenado devuelve la carpeta entera y se
        # ingeriria todo el historico del buzon.
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        self.assertIn('receivedDateTime ge', graph.delta_urls[0])
        self.assertIn('/messages/delta', graph.delta_urls[0])

    def test_second_poll_resumes_from_the_stored_delta_link(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [
                [page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')],
                [page([graph_message('msg-2')], delta_link='https://graph.test/delta/2')],
            ],
        )

        self.run_poll(graph)
        self.run_poll(graph)

        self.assertEqual(graph.delta_urls[1], 'https://graph.test/delta/1')
        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/2')
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-2').exists())

    def test_removed_entries_are_skipped(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page(
                [{'id': 'msg-gone', '@removed': {'reason': 'deleted'}}],
                delta_link='https://graph.test/delta/1',
            )]],
        )

        self.run_poll(graph)

        self.assertFalse(Ticket.objects.exists())
        self.assertEqual(MailboxSyncCursor.objects.get(environment='Staging').last_message_count, 0)

    def test_failed_round_leaves_the_cursor_where_it_was(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )
        self.run_poll(graph)

        def explode(url, headers=None, timeout=None):
            if '/mailFolders/Inbox?' in url:
                return _Response({'id': 'FOLDER-1'})
            raise RuntimeError('Graph 503')

        with patch.object(tasks, '_get_graph_token', return_value='token'), \
                patch.object(tasks.requests, 'get', side_effect=explode):
            tasks.poll_m365_mailboxes()

        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        # Se conserva el ultimo enlace bueno: la reentrega la absorbe el dedup.
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')
        self.assertEqual(cursor.last_result, 'error')
        self.assertIn('Graph 503', cursor.last_error)

    def test_failed_message_is_retried_without_reingesting_previous_messages(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [
                [page([], delta_link='https://graph.test/delta/0')],
                [page([graph_message('msg-ok'), graph_message('msg-retry')],
                      delta_link='https://graph.test/delta/1')],
            ],
            repeat=True,
        )
        self.run_poll(graph)
        process = tasks._process_message

        def fail_second(message, brand, attachments=None):
            if message['id'] == 'msg-retry':
                raise RuntimeError('Temporary ingestion failure')
            return process(message, brand, attachments=attachments)

        with patch.object(tasks, '_process_message', side_effect=fail_second):
            self.run_poll(graph)

        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/0')
        self.assertEqual(cursor.last_result, 'error')
        self.assertEqual(Ticket.objects.count(), 1)
        with patch.object(tasks, '_process_message', wraps=process) as retry:
            self.run_poll(graph)
        self.assertEqual(retry.call_count, 1)
        self.assertEqual(retry.call_args.args[0]['id'], 'msg-retry')
        self.assertEqual(Ticket.objects.count(), 2)
        cursor.refresh_from_db()
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')
        self.assertIsNone(cursor.last_error)

    def test_attachment_download_failure_keeps_the_cursor_for_retry(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [
                [page([], delta_link='https://graph.test/delta/0')],
                [page([graph_message('msg-attachment', hasAttachments=True)],
                      delta_link='https://graph.test/delta/1')],
            ],
            repeat=True,
        )
        self.run_poll(graph)
        with patch.object(tasks, '_fetch_m365_attachments', side_effect=RuntimeError('Graph 503')):
            self.run_poll(graph)
        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/0')
        self.assertEqual(cursor.last_result, 'error')
        self.assertFalse(Ticket.objects.exists())
        with patch.object(tasks, '_fetch_m365_attachments', return_value=[]) as fetch:
            self.run_poll(graph)
        fetch.assert_called_once()
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-attachment').exists())
        cursor.refresh_from_db()
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')

    def test_expired_cursor_restarts_the_sync_instead_of_getting_stuck(self):
        # Los tokens de delta de Outlook se desalojan sin aviso. Si un 410 no
        # reiniciara la sincronizacion, el buzon dejaria de ingerir en silencio.
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )
        self.run_poll(graph)

        gone = {'served': False}

        def expire_once(url, headers=None, timeout=None):
            if '/mailFolders/Inbox?' in url:
                return _Response({'id': 'FOLDER-1'})
            if url == 'https://graph.test/delta/1' and not gone['served']:
                gone['served'] = True
                return _Response({'error': {'code': 'syncStateNotFound'}}, status=404)
            return _Response(page([graph_message('msg-2')], delta_link='https://graph.test/delta/2'))

        with patch.object(tasks, '_get_graph_token', return_value='token'), \
                patch.object(tasks.requests, 'get', side_effect=expire_once), \
                patch.object(tasks.requests, 'patch', side_effect=graph.patch_):
            tasks.poll_m365_mailboxes()

        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/2')
        self.assertEqual(cursor.last_result, 'delta')
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-2').exists())

    def test_gone_status_also_triggers_a_resync(self):
        self.assertTrue(tasks._needs_resync(_Response({}, status=410)))
        self.assertTrue(tasks._needs_resync(_Response({'error': {'code': 'resyncRequired'}}, status=400)))
        self.assertFalse(tasks._needs_resync(_Response({'error': {'code': 'throttled'}}, status=429)))
        self.assertFalse(tasks._needs_resync(_Response({}, status=200)))

    @override_settings(INBOUND_DELTA_MAX_PAGES=1)
    def test_page_cap_stores_the_next_link_so_the_round_continues(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[
                page([graph_message('msg-1')], next_link='https://graph.test/next/1'),
                page([graph_message('msg-2')], delta_link='https://graph.test/delta/1'),
            ]],
        )

        self.run_poll(graph)

        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/next/1')
        self.assertEqual(cursor.link_kind, 'partial')
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-1').exists())
        self.assertFalse(Ticket.objects.filter(email_message_id='msg-2').exists())

    @override_settings(INBOUND_MARK_READ_ENABLED=False)
    def test_mark_read_can_be_turned_off(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        self.assertEqual(graph.patched, [])
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-1').exists())

    @override_settings(INBOUND_MARK_READ_ENABLED=True)
    def test_mark_read_still_happens_when_enabled(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        self.assertEqual(len(graph.patched), 1)

    def test_known_message_does_not_download_attachments_again(self):
        InboundEmailLog.objects.create(
            brand=self.brand, message_id='msg-1',
            status=InboundEmailLog.STATUS_PROCESSED, result='ticket_created',
        )
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page(
                [graph_message('msg-1', hasAttachments=True)],
                delta_link='https://graph.test/delta/1',
            )]],
        )

        with patch.object(tasks, '_fetch_m365_attachments') as fetch:
            self.run_poll(graph)

        fetch.assert_not_called()
        self.assertFalse(Ticket.objects.exists())


@override_settings(ENVIRONMENT_NAME='Staging', INBOUND_MARK_READ_ENABLED=True)
class InvalidEmailSenderTests(_SyncFixture):
    """Un remitente no procesable se conserva sin detener su buzon."""

    def rejected_message(self, msg_id='msg-rejected', address=None, name='Cliente', **extra):
        if address is None:
            address = 'a' * 248 + '@example.com'  # 260 caracteres, como el fallo real.
        return graph_message(
            msg_id,
            **{'from': {'emailAddress': {'address': address, 'name': name}}, **extra},
        )

    def assert_rejected(self, message, reason):
        log = InboundEmailLog.objects.get(brand=self.brand, message_id=message['id'])
        self.assertEqual(log.status, InboundEmailLog.STATUS_FAILED)
        self.assertEqual(log.result, 'reject_invalid_sender')
        self.assertIn(reason, log.error)
        self.assertEqual(log.payload, message)
        self.assertIsNotNone(log.processed_at)
        self.assertIsNone(log.ticket_id)
        self.assertFalse(Ticket.objects.filter(email_message_id=message['id']).exists())
        return log

    def test_overlong_email_is_retained_and_next_message_advances_the_cursor(self):
        rejected = self.rejected_message()
        valid = graph_message('msg-next')
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([rejected, valid], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        log = self.assert_rejected(rejected, 'sender_email_too_long')
        self.assertNotIn(rejected['from']['emailAddress']['address'], log.error)
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-next').exists())
        self.assertEqual(User.objects.count(), 1)
        cursor = MailboxSyncCursor.objects.get(environment='Staging')
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')
        self.assertEqual(cursor.last_result, 'delta')
        self.assertIsNone(cursor.last_error)
        self.assertEqual(cursor.last_message_count, 2)
        # El mensaje rechazado queda sin marcar como leido para su revision.
        self.assertEqual(len(graph.patched), 1)
        self.assertTrue(graph.patched[0].endswith('/messages/msg-next'))

    def test_existing_length_failure_is_reclassified_without_a_second_log(self):
        message = self.rejected_message()
        previous = InboundEmailLog.objects.create(
            brand=self.brand, message_id=message['id'], payload=message,
            status=InboundEmailLog.STATUS_FAILED,
            error="(1406, Data too long for column 'email' at row 1)",
        )
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([message], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        log = self.assert_rejected(message, 'sender_email_too_long')
        self.assertEqual(log.pk, previous.pk)
        self.assertEqual(log.received_at, previous.received_at)
        self.assertEqual(InboundEmailLog.objects.count(), 1)
        self.assertEqual(MailboxSyncCursor.objects.get().last_result, 'delta')
        self.assertEqual(graph.patched, [])

    def test_invalid_sender_fields_are_retained_without_creating_users(self):
        cases = [
            ('invalid-email', 'not-an-email', 'Cliente', 'sender_email_invalid'),
            ('invalid-type', 123, 'Cliente', 'sender_email_invalid'),
            ('long-name', 'cliente@example.com', 'n' * 260, 'sender_name_too_long'),
            ('invalid-name', 'cliente@example.com', {'name': 'Cliente'}, 'sender_name_invalid'),
        ]
        for msg_id, address, name, reason in cases:
            with self.subTest(msg_id=msg_id):
                message = self.rejected_message(msg_id, address, name)
                graph = FakeGraph(
                    {'helpdesk@cloudwws.example': 'FOLDER-1'},
                    [[page([message], delta_link=f'https://graph.test/delta/{msg_id}')]],
                )
                self.run_poll(graph)
                self.assert_rejected(message, reason)
                self.assertEqual(graph.patched, [])
                self.assertEqual(MailboxSyncCursor.objects.get().last_result, 'delta')
        self.assertFalse(User.objects.exists())

    def test_rejected_sender_does_not_download_attachments(self):
        message = self.rejected_message(hasAttachments=True)
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([message], delta_link='https://graph.test/delta/1')]],
        )

        with patch.object(tasks, '_fetch_m365_attachments', side_effect=RuntimeError('Graph 503')) as fetch:
            self.run_poll(graph)

        fetch.assert_not_called()
        self.assert_rejected(message, 'sender_email_too_long')
        self.assertEqual(MailboxSyncCursor.objects.get().last_result, 'delta')

    def test_later_transient_failure_still_retries_without_losing_the_rejection(self):
        rejected = self.rejected_message()
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [
                [page([], delta_link='https://graph.test/delta/0')],
                [page([rejected, graph_message('msg-retry')], delta_link='https://graph.test/delta/1')],
            ],
            repeat=True,
        )
        self.run_poll(graph)
        process = tasks._process_message

        def fail_valid(message, brand, attachments=None):
            if message['id'] == 'msg-retry':
                raise RuntimeError('Temporary ingestion failure')
            return process(message, brand, attachments=attachments)

        with patch.object(tasks, '_process_message', side_effect=fail_valid):
            self.run_poll(graph)

        log = self.assert_rejected(rejected, 'sender_email_too_long')
        cursor = MailboxSyncCursor.objects.get()
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/0')
        self.assertEqual(cursor.last_result, 'error')
        self.assertIn('Temporary ingestion failure', cursor.last_error)
        self.run_poll(graph)
        self.assertEqual(self.assert_rejected(rejected, 'sender_email_too_long').pk, log.pk)
        self.assertTrue(Ticket.objects.filter(email_message_id='msg-retry').exists())
        cursor.refresh_from_db()
        self.assertEqual(cursor.delta_link, 'https://graph.test/delta/1')
        self.assertIsNone(cursor.last_error)

    def test_exact_field_limits_and_multibyte_names_are_not_rejected(self):
        address = 'customer@' + '.'.join(('a' * 63, 'b' * 63, 'c' * 63, 'd' * 54))
        self.assertEqual(len(address), User._meta.get_field('email').max_length)
        name = 'ñ' * User._meta.get_field('name').max_length
        message = self.rejected_message(address=address, name=name)
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([message], delta_link='https://graph.test/delta/1')]],
        )

        self.run_poll(graph)

        user = User.objects.get(email=address)
        self.assertEqual(user.name, name)
        self.assertEqual(InboundEmailLog.objects.get().status, InboundEmailLog.STATUS_PROCESSED)
        self.assertTrue(Ticket.objects.filter(email_message_id=message['id']).exists())

    def test_normalization_does_not_modify_the_original_payload(self):
        message = self.rejected_message(address='  CLIENTE@EXAMPLE.COM  ', name='  Cliente  ')

        result = EmailIngestionService.process_message(message, self.brand)

        self.assertEqual(result, 'ticket_created')
        self.assertEqual(User.objects.get().email, 'cliente@example.com')
        self.assertEqual(User.objects.get().name, 'Cliente')
        self.assertEqual(InboundEmailLog.objects.get().payload, message)
        self.assertEqual(message['from']['emailAddress']['address'], '  CLIENTE@EXAMPLE.COM  ')

    def test_requester_validation_runs_before_any_database_write(self):
        with self.assertRaises(InvalidEmailSender) as rejected:
            EmailIngestionService.get_or_create_requester('a' * 248 + '@example.com', 'Cliente')
        self.assertEqual(rejected.exception.code, 'sender_email_too_long')
        self.assertFalse(User.objects.exists())

    def test_corrected_sender_can_reprocess_the_same_log(self):
        message = self.rejected_message()
        self.assertEqual(
            EmailIngestionService.process_message(message, self.brand),
            EmailIngestionService.REJECTED_SENDER_RESULT,
        )
        previous = self.assert_rejected(message, 'sender_email_too_long')
        corrected = self.rejected_message(address='cliente@example.com')

        self.assertEqual(EmailIngestionService.process_message(corrected, self.brand), 'ticket_created')

        log = InboundEmailLog.objects.get()
        self.assertEqual(log.pk, previous.pk)
        self.assertEqual(log.status, InboundEmailLog.STATUS_PROCESSED)
        self.assertEqual(log.result, 'ticket_created')
        self.assertIsNone(log.error)
        self.assertEqual(log.payload, corrected)
        self.assertIsNotNone(log.ticket_id)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_rejection_is_not_reused_for_a_later_transient_failure(self):
        message = self.rejected_message()
        EmailIngestionService.process_message(message, self.brand)
        corrected = self.rejected_message(address='cliente@example.com')

        with patch.object(EmailIngestionService, '_process_message', side_effect=RuntimeError('Temporary failure')):
            with self.assertRaisesRegex(RuntimeError, 'Temporary failure'):
                EmailIngestionService.process_message(corrected, self.brand)

        log = InboundEmailLog.objects.get()
        self.assertEqual(log.status, InboundEmailLog.STATUS_FAILED)
        self.assertIsNone(log.result)
        self.assertEqual(log.error, 'Temporary failure')
        self.assertEqual(log.payload, corrected)


class SharedMailboxTests(_SyncFixture):
    """Las cuatro direcciones de soporte son alias del mismo buzon fisico."""

    def setUp(self):
        super().setUp()
        self.brand_b = Brand.objects.create(
            name='Comunycarse', support_email='helpdesk@comunycarse.example',
            mailbox_type='m365', language='es',
        )
        self.brand_c = Brand.objects.create(
            name='eComFax', support_email='helpdesk@ecomfax.example',
            mailbox_type='m365', language='es',
        )
        self.same_folder = {
            'helpdesk@cloudwws.example': 'FOLDER-1',
            'helpdesk@comunycarse.example': 'FOLDER-1',
            'helpdesk@ecomfax.example': 'FOLDER-1',
        }

    @override_settings(ENVIRONMENT_NAME='Staging')
    def test_aliases_are_polled_once_and_create_a_single_ticket(self):
        # Antes lo evitaba isRead: la primera marca marcaba leido y las demas ya
        # no veian el mensaje. Sin el, sin agrupar por carpeta fisica saldrian
        # tres tickets del mismo correo.
        graph = FakeGraph(
            self.same_folder,
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
            repeat=True,
        )

        self.run_poll(graph)

        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(MailboxSyncCursor.objects.count(), 1)
        self.assertEqual(len(graph.delta_urls), 1)
        self.assertEqual(Ticket.objects.get().brand, self.brand)

    @override_settings(ENVIRONMENT_NAME='Staging')
    def test_distinct_physical_mailboxes_get_one_cursor_each(self):
        graph = FakeGraph(
            {
                'helpdesk@cloudwws.example': 'FOLDER-1',
                'helpdesk@comunycarse.example': 'FOLDER-2',
                'helpdesk@ecomfax.example': 'FOLDER-1',
            },
            [[page([], delta_link='https://graph.test/delta/1')]],
            repeat=True,
        )

        self.run_poll(graph)

        cursors = MailboxSyncCursor.objects.order_by('mailbox')
        self.assertEqual(
            [c.mailbox for c in cursors],
            ['helpdesk@cloudwws.example', 'helpdesk@comunycarse.example'],
        )


class EnvironmentIsolationTests(_SyncFixture):
    def test_each_environment_advances_its_own_cursor(self):
        """El bug que motiva el cambio: DEV no puede dejar a STA sin correo.

        Los dos entornos comparten aqui la misma base de datos (en real no), asi
        que lo que se comprueba es lo que de verdad estaba roto: que el segundo
        entorno SIGUE VIENDO el mensaje en Graph y arranca de su propio cursor,
        en vez de encontrarse el buzon ya vaciado por el primero.
        """
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
            repeat=True,
        )

        with override_settings(ENVIRONMENT_NAME='Development'):
            self.run_poll(graph)
        with override_settings(ENVIRONMENT_NAME='Staging'):
            self.run_poll(graph)

        self.assertEqual(graph.served_message_ids, ['msg-1', 'msg-1'])
        cursors = MailboxSyncCursor.objects.order_by('environment')
        self.assertEqual([c.environment for c in cursors], ['Development', 'Staging'])
        self.assertEqual({c.folder_key for c in cursors}, {tasks._folder_key('FOLDER-1')})
        self.assertTrue(all(c.delta_link for c in cursors))
        # Staging arranco de su propia ventana inicial, no del deltaLink de Development.
        self.assertIn('receivedDateTime ge', graph.delta_urls[1])

    def test_isread_is_no_longer_used_as_the_cursor(self):
        graph = FakeGraph(
            {'helpdesk@cloudwws.example': 'FOLDER-1'},
            [[page([graph_message('msg-1')], delta_link='https://graph.test/delta/1')]],
        )

        with override_settings(ENVIRONMENT_NAME='Staging'):
            self.run_poll(graph)

        self.assertFalse(any('isRead' in url for url in graph.delta_urls))
