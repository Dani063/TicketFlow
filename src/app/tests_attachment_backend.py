"""Attachment contract tests: isolated storage, SQLite and no external delivery."""
import io
import json
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from app.api import APIValidationError
from app.models import Attachment, AttachmentDraft, AttachmentRequest, Brand, Comment, OutboundEmailLog, ProductLine, ResponseTemplate, Role, Ticket, User
from app.services.attachments import AttachmentService
from app.services.comments import CommentService
from app.services.tickets import TicketService


TEST_SETTINGS = dict(
    ALLOWED_HOSTS=['testserver'], ATTACHMENT_STORAGE_BACKEND='local', PUBLIC_ATTACHMENTS_S3_BUCKET='',
    PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS={'txt', 'log', 'csv', 'pdf', 'png', 'jpg', 'zip'},
    PUBLIC_ATTACHMENT_MAX_FILES=5, PUBLIC_ATTACHMENT_MAX_BYTES=15 * 1024 * 1024,
    PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=25 * 1024 * 1024, AI_CLASSIFICATION_ENABLED=False,
    OUTBOUND_EMAIL_ENABLED=False, CELERY_BROKER_URL='memory://', CELERY_TASK_ALWAYS_EAGER=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)


class AttachmentFixtures:
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = media.name
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        for target in ('boto3.client', 'requests.sessions.Session.request'):
            blocker = patch(target, side_effect=AssertionError('External network forbidden in attachment tests'))
            blocker.start()
            self.addCleanup(blocker.stop)
        role, _ = Role.objects.get_or_create(role_name='agent')
        self.agent = User.objects.create_user('backend-agent@example.com', 'Agent')
        self.agent.role = role
        self.agent.save(update_fields=['role'])
        self.other_agent = User.objects.create_user('backend-other@example.com', 'Other agent')
        self.other_agent.role = role
        self.other_agent.save(update_fields=['role'])
        self.customer = User.objects.create_user('backend-customer@example.com', 'Customer')
        self.outsider = User.objects.create_user('backend-outsider@example.com', 'Outsider')
        self.product = ProductLine.objects.create(code='backend-attachment-tests', name='Attachment tests')
        self.ticket = Ticket.objects.create(subject='Attachments', requester=self.customer, created_by=self.agent,
                                            status='open', product_line=self.product)
        self.client.force_login(self.agent)
        self.draft_key = str(uuid.uuid4())

    @staticmethod
    def file(name='informe técnico.txt', content=b'content', mime='text/plain'):
        return SimpleUploadedFile(name, content, content_type=mime)

    def upload(self, name='informe técnico.txt', content=b'content', mime='text/plain', draft=False, key=None, actor=None):
        return AttachmentService.upload(self.file(name, content, mime), actor or self.agent,
                                        ticket=None if draft else self.ticket,
                                        draft_key=self.draft_key if draft else None,
                                        upload_key=key or uuid.uuid4())

    def post_json(self, name, data, args=()):
        return self.client.post(reverse(name, args=args), data=json.dumps(data), content_type='application/json')

    def payload(self, ids=(), **extra):
        return {'subject': 'Nuevo ticket', 'content': '', 'html_body': '', 'attachment_ids': list(ids),
                'draft_key': self.draft_key, 'request_id': str(uuid.uuid4()), 'product_line': self.product,
                'requester_id': self.customer.id, **extra}


@override_settings(**TEST_SETTINGS)
class AttachmentBackendTests(AttachmentFixtures, TestCase):
    def test_upload_metadata_keeps_unicode_spaces_and_separate_safe_key(self):
        response = self.client.post(reverse('upload_draft_attachment'), {
            'file': self.file(), 'draft_key': self.draft_key, 'upload_key': str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 200)
        metadata = response.json()
        self.assertEqual(set(metadata), {'id', 'filename', 'size', 'file_type', 'category', 'preview_kind',
                                         'preview_url', 'thumbnail_url', 'download_url', 'file_url'})
        self.assertEqual(metadata['filename'], 'informe técnico.txt')
        attachment = Attachment.objects.get(pk=metadata['id'])
        self.assertEqual(attachment.filename, metadata['filename'])
        self.assertNotIn(' ', attachment.storage_key)
        self.assertNotIn('é', attachment.storage_key)
        self.assertIsNone(attachment.ticket_id)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_filename_controls_paths_bidi_and_header_injection(self):
        long_name = AttachmentService.original_filename('á' * 300 + '.pdf')
        self.assertEqual(len(long_name), 255)
        self.assertTrue(long_name.endswith('.pdf'))
        name = AttachmentService.original_filename('C:\\fakepath\\../  nómina\r\n\x00\u202e "2026".txt')
        self.assertEqual(name, 'nómina "2026".txt')
        att = self.upload(name=name)
        response = self.client.get(reverse('attachment_download', args=[att.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=utf-8''", response['Content-Disposition'])
        self.assertNotIn('\r', response['Content-Disposition'])
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        response.close()

    def test_pending_and_internal_authorization_for_preview_and_download(self):
        att = self.upload()
        for route in ('attachment_download', 'attachment_preview'):
            url = reverse(route, args=[att.id])
            self.assertEqual(Client().get(url).status_code, 404)
            for user in (self.customer, self.other_agent, self.outsider):
                client = Client()
                client.force_login(user)
                self.assertEqual(client.get(url).status_code, 404)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
        comment, _ = CommentService.add_comment(self.ticket, self.agent, '', is_public=False, attachment_ids=[att.id])
        att.refresh_from_db()
        for user, status in ((self.customer, 404), (self.other_agent, 200), (self.outsider, 404)):
            client = Client()
            client.force_login(user)
            for route in ('attachment_download', 'attachment_preview'):
                response = client.get(reverse(route, args=[att.id]))
                self.assertEqual(response.status_code, status)
                response.close()
        comment.is_public = True
        comment.save(update_fields=['is_public'])
        client = Client()
        client.force_login(self.customer)
        self.assertEqual(client.get(reverse('attachment_preview', args=[att.id])).status_code, 200)

    def test_draft_is_private_even_from_other_agent(self):
        att = self.upload(draft=True)
        self.client.force_login(self.other_agent)
        self.assertEqual(self.client.get(reverse('attachment_preview', args=[att.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse('delete_pending_attachment', args=[att.id])).status_code, 403)
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(reverse('attachment_preview', args=[att.id])).status_code, 200)

    def test_cc_can_read_published_public_attachment(self):
        self.ticket.ccs.add(self.outsider)
        att = self.upload()
        CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('attachment_preview', args=[att.id])).status_code, 200)

    def test_legacy_metadata_has_unknown_size_and_no_external_io(self):
        att = Attachment.objects.create(ticket=self.ticket, uploaded_by=self.agent,
                                         file_url='https://private.example/folder/informe%20t%C3%A9cnico.pdf?token=secret')
        with patch.object(AttachmentService, 'open_blob', side_effect=AssertionError('No blob reads')):
            metadata = AttachmentService.metadata(att)
        self.assertEqual(metadata['filename'], 'informe técnico.pdf')
        self.assertIsNone(metadata['size'])
        self.assertIsNone(metadata['preview_kind'])
        self.assertNotIn('secret', json.dumps(metadata))
        self.assertEqual(self.client.get(metadata['download_url']).status_code, 410)

    def test_text_preview_bounded_raw_read_and_truncated(self):
        limit = AttachmentService.PREVIEW_MAX_BYTES
        att = self.upload(content=b'<script>alert(1)</script>' + b'a' * limit)
        with patch.object(AttachmentService, 'open_blob') as opened:
            stored = opened.return_value.__enter__.return_value
            stored.read.return_value = b'x' * (limit + 1)
            response = self.client.get(reverse('attachment_preview', args=[att.id]))
            stored.read.assert_called_once_with(limit + 1)
        data = response.json()
        self.assertEqual(data['kind'], 'text')
        self.assertEqual(len(data['text']), limit)
        self.assertTrue(data['truncated'])
        self.assertIn('application/json', response['Content-Type'])
        self.assertIn('sandbox', response['Content-Security-Policy'])

    def test_csv_rows_columns_cells_and_formula_strings_are_bounded(self):
        att = self.upload(name='informe.csv', content=b'a,b\n' * 205, mime='text/csv')
        data = self.client.get(reverse('attachment_preview', args=[att.id])).json()
        self.assertEqual(data['kind'], 'csv')
        self.assertEqual(len(data['rows']), 200)
        self.assertTrue(data['truncated'])
        csv = '=1+1,' + ','.join(['x' * 2100] + ['y'] * 55)
        att = self.upload(name='columnas.csv', content=csv.encode(), mime='text/csv')
        data = self.client.get(reverse('attachment_preview', args=[att.id])).json()
        self.assertEqual(len(data['rows'][0]), 50)
        self.assertEqual(data['rows'][0][0], '=1+1')
        self.assertEqual(len(data['rows'][0][1]), 2000)
        self.assertTrue(data['truncated'])

    def test_small_text_csv_and_utf16_are_not_truncated(self):
        for name, content, expected in [('a.txt', 'á'.encode('utf-16'), 'á'), ('a.csv', b'"uno,dos",tres', None)]:
            att = self.upload(name, content)
            data = self.client.get(reverse('attachment_preview', args=[att.id])).json()
            self.assertFalse(data['truncated'])
            if expected:
                self.assertEqual(data['text'], expected)
            else:
                self.assertEqual(data['rows'], [['uno,dos', 'tres']])

    def test_image_preview_and_thumbnail_decode_reencode_pixels(self):
        output = io.BytesIO()
        Image.new('RGB', (900, 600), 'red').save(output, format='PNG')
        att = self.upload('captura.png', output.getvalue(), 'text/html')
        metadata = AttachmentService.metadata(att)
        self.assertEqual(metadata['preview_kind'], 'image')
        response = self.client.get(metadata['thumbnail_url'])
        self.assertEqual(response['Content-Type'], 'image/png')
        with Image.open(io.BytesIO(response.content)) as img:
            self.assertEqual(img.size, (480, 320))

    def test_html_disguised_as_image_or_pdf_never_served_inline(self):
        for name in ('falsa.png', 'falso.pdf'):
            att = self.upload(name, b'<html><script>alert(1)</script></html>', 'text/html')
            response = self.client.get(reverse('attachment_preview', args=[att.id]))
            self.assertEqual(response.status_code, 415)
            download = self.client.get(reverse('attachment_download', args=[att.id]))
            self.assertEqual(download['Content-Type'], 'application/octet-stream')
            self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
            download.close()

    def test_svg_html_legacy_never_offer_inline_preview(self):
        for name, mime in [('x.svg', 'image/svg+xml'), ('x.html', 'text/html')]:
            att = Attachment.objects.create(original_name=name, file_type=mime, storage_key='legacy/' + name,
                                             file_url='/legacy', ticket=self.ticket, uploaded_by=self.agent)
            self.assertIsNone(AttachmentService.metadata(att)['preview_kind'])
            self.assertEqual(self.client.get(reverse('attachment_preview', args=[att.id])).status_code, 415)

    def test_pdf_preview_has_binary_type_and_security_headers(self):
        att = self.upload('manual.pdf', b'%PDF-1.4\n%%EOF', 'application/pdf')
        response = self.client.get(reverse('attachment_preview', args=[att.id]))
        self.assertEqual(response.content, b'%PDF-1.4\n%%EOF')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_s3_preview_proxies_binary_after_auth_and_download_forces_utf8_attachment(self):
        att = Attachment.objects.create(ticket=self.ticket, uploaded_by=self.agent, file_url='/private',
                                         storage_backend='s3', storage_key='private/manual.pdf',
                                         original_name='manual técnico.pdf', file_type='text/html', size=15)
        with patch.object(AttachmentService, '_s3_client') as factory:
            factory.return_value.get_object.return_value = {'Body': io.BytesIO(b'%PDF-1.4\n%%EOF')}
            response = self.client.get(reverse('attachment_preview', args=[att.id]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            factory.return_value.generate_presigned_url.assert_not_called()
            factory.return_value.generate_presigned_url.return_value = 'https://storage.example/signed'
            self.assertEqual(self.client.get(reverse('attachment_download', args=[att.id])).status_code, 302)
            params = factory.return_value.generate_presigned_url.call_args.kwargs['Params']
            self.assertEqual(params['ResponseContentType'], 'application/octet-stream')
            self.assertIn("filename*=utf-8''manual%20t%C3%A9cnico.pdf", params['ResponseContentDisposition'])
            factory.reset_mock()
            self.client.force_login(self.other_agent)
            self.assertEqual(self.client.get(reverse('attachment_preview', args=[att.id])).status_code, 404)
            factory.assert_not_called()

    def test_draft_upload_requires_valid_keys_and_one_file(self):
        url = reverse('upload_draft_attachment')
        for keys in ({}, {'draft_key': 'bad', 'upload_key': str(uuid.uuid4())}, {'draft_key': self.draft_key, 'upload_key': ''}):
            self.assertEqual(self.client.post(url, {'file': self.file(), **keys}).status_code, 400)
        self.assertEqual(self.client.post(url, {'file': [self.file(), self.file()], 'draft_key': self.draft_key,
                                                'upload_key': str(uuid.uuid4())}).status_code, 400)
        self.assertEqual(Client().post(url, {}).status_code, 401)

    @override_settings(PUBLIC_ATTACHMENT_MAX_FILES=1)
    def test_upload_retry_at_limit_does_not_write_second_blob(self):
        key = uuid.uuid4()
        first = self.upload(key=key)
        with patch.object(AttachmentService, 'save', side_effect=AssertionError('Must replay')):
            again = self.upload(key=key)
        self.assertEqual(first.id, again.id)
        with self.assertRaisesMessage(APIValidationError, 'Demasiados archivos'):
            self.upload()
        self.assertEqual(Attachment.objects.count(), 1)

    def test_upload_key_cannot_be_reused_for_other_bytes_name_or_scope(self):
        key = uuid.uuid4()
        self.upload(key=key)
        for options in ({'content': b'other'}, {'name': 'other.txt'}, {'draft': True}):
            with self.assertRaises(APIValidationError) as error:
                self.upload(key=key, **options)
            self.assertEqual(error.exception.code, 'idempotency_conflict')

    @override_settings(PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=10)
    def test_cumulative_limits_and_scope_isolation(self):
        self.upload(content=b'123456')
        with self.assertRaises(APIValidationError) as error:
            self.upload(content=b'12345')
        self.assertEqual(error.exception.code, 'attachments_too_large')
        self.upload(content=b'123456', draft=True)
        self.upload(content=b'123456', actor=self.other_agent)

    def test_comment_only_files_and_uniform_metadata_idempotent(self):
        att = self.upload()
        data = {'content': '', 'attachment_ids': [att.id], 'request_id': str(uuid.uuid4())}
        response = self.post_json('add_comment', data, [self.ticket.id])
        again = self.post_json('add_comment', data, [self.ticket.id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), again.json())
        self.assertEqual(response.json()['attachments'], [AttachmentService.metadata(att)])
        self.assertEqual(Comment.objects.filter(ticket=self.ticket).count(), 1)
        data['content'] = 'changed'
        self.assertEqual(self.post_json('add_comment', data, [self.ticket.id]).status_code, 409)

    def test_invalid_missing_foreign_duplicate_or_published_ids_reject_entire_comment(self):
        own = self.upload()
        foreign = self.upload(actor=self.other_agent)
        for ids in ([own.id, foreign.id], [own.id, 999999], [own.id, own.id], '1', [True], [1.0], {}, [0]):
            with self.subTest(ids=ids):
                response = self.post_json('add_comment', {'content': 'text', 'attachment_ids': ids}, [self.ticket.id])
                self.assertEqual(response.status_code, 400)
                self.assertFalse(Comment.objects.exists())
                own.refresh_from_db()
                self.assertIsNone(own.comment_id)
        CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[own.id])
        response = self.post_json('add_comment', {'attachment_ids': [own.id]}, [self.ticket.id])
        self.assertEqual(response.status_code, 400)

    def test_empty_comment_and_non_object_json_are_rejected(self):
        for data in ({}, [], {'request_id': 'bad'}, {'is_public': 'false'}):
            self.assertEqual(self.post_json('add_comment', data, [self.ticket.id]).status_code, 400)

    def test_publication_rechecks_count_and_byte_limits_before_claim(self):
        one, two = self.upload(content=b'1234'), self.upload(content=b'1234')
        for settings in ({'PUBLIC_ATTACHMENT_MAX_FILES': 1}, {'PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES': 7},
                         {'PUBLIC_ATTACHMENT_MAX_BYTES': 3}):
            with override_settings(**settings), self.assertRaises(APIValidationError):
                CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[one.id, two.id])
        self.assertFalse(Comment.objects.exists())

    def test_first_message_attachments_exist_in_outbound_email_payload(self):
        brand = Brand.objects.create(name='Attachments outbound', support_email='support@example.com')
        ResponseTemplate.objects.create(key='agent_reply', brand=brand, language='es',
                                         subject='Reply', body_html='{{ comment_content }}', body_text='Reply')
        att = self.upload(draft=True)
        payload = self.payload([att.id], brand=brand, content='', html_body='<p>Hola</p>')
        with override_settings(OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True):
            ticket = TicketService.create_ticket(self.agent, payload)
            again = TicketService.create_ticket(self.agent, payload)
        self.assertEqual(ticket.id, again.id)
        log = OutboundEmailLog.objects.get(ticket=ticket, template_key='agent_reply')
        self.assertEqual(log.payload['attachment_ids'], [att.id])
        self.assertEqual(ticket.comment_set.count(), 1)
        att.refresh_from_db()
        self.assertEqual(att.comment_id, ticket.comment_set.get().id)

    def test_ajax_create_files_only_html_body_and_replay(self):
        att = self.upload(draft=True)
        data = {'subject': 'AJAX', 'producto': self.product.id, 'attachment_ids': json.dumps([att.id]),
                'draft_key': self.draft_key, 'request_id': str(uuid.uuid4()), 'html_body': ''}
        url = reverse('create_ticket')
        first = self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        again = self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(set(first.json()), {'ticket_id', 'url'})
        self.assertEqual(first.json(), again.json())
        ticket = Ticket.objects.get(pk=first.json()['ticket_id'])
        self.assertEqual(ticket.comment_set.get().attachment_set.get().id, att.id)
        data['subject'] = 'Changed'
        self.assertEqual(self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 409)

    def test_ajax_create_uuid_draft_without_files_and_empty_uuid_rejected(self):
        data = {'subject': 'Sin archivos', 'producto': self.product.id, 'attachment_ids': '[]',
                'draft_key': self.draft_key, 'request_id': str(uuid.uuid4()), 'html_body': '<p>Hola</p>'}
        response = self.client.post(reverse('create_ticket'), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ticket.objects.get(pk=response.json()['ticket_id']).comment_set.get().html_body, '<p>Hola</p>')
        self.assertEqual(self.client.post(reverse('create_ticket'), {**data, 'draft_key': ''}).status_code, 400)

    def test_first_message_accepts_sanitized_html_without_plain_text(self):
        ticket = TicketService.create_ticket(self.agent, self.payload(html_body='<p>Hola</p><script>evil()</script>'))
        comment = ticket.comment_set.get()
        self.assertIn('<p>Hola</p>', comment.html_body)
        self.assertNotIn('<script>', comment.html_body)

    def test_draft_scope_and_published_draft_cannot_create_second_ticket(self):
        att = self.upload(draft=True)
        payload = self.payload([att.id])
        for change in ({'draft_key': str(uuid.uuid4())}, {'attachment_ids': [999999]}, {'request_id': None}):
            with self.assertRaises(APIValidationError):
                TicketService.create_ticket(self.agent, {**payload, **change})
        TicketService.create_ticket(self.agent, payload)
        with self.assertRaises(APIValidationError) as error:
            TicketService.create_ticket(self.agent, {**payload, 'request_id': str(uuid.uuid4())})
        self.assertEqual(error.exception.code, 'draft_already_published')
        with self.assertRaises(APIValidationError):
            self.upload(draft=True)

    def test_foreign_draft_cannot_be_published_even_by_agent(self):
        att = self.upload(draft=True, actor=self.other_agent)
        with self.assertRaises(APIValidationError):
            TicketService.create_ticket(self.agent, self.payload([att.id]))
        self.assertEqual(Ticket.objects.count(), 1)

    def test_cancel_before_upload_tombstone_is_persistent_and_scoped(self):
        key = str(uuid.uuid4())
        data = {'upload_key': key, 'draft_key': self.draft_key, 'ticket_id': None}
        self.assertEqual(self.post_json('cancel_attachment_upload', data).status_code, 200)
        self.assertEqual(self.post_json('cancel_attachment_upload', data).status_code, 200)
        with self.assertRaises(APIValidationError) as error:
            self.upload(key=key, draft=True)
        self.assertEqual(error.exception.code, 'upload_cancelled')
        self.upload(key=key, draft=True, actor=self.other_agent)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(self.post_json('cancel_attachment_upload', {**data, 'draft_key': str(uuid.uuid4())}).status_code, 409)

    def test_cancel_after_upload_removes_blob_and_retry_cannot_resurrect(self):
        key = str(uuid.uuid4())
        att = self.upload(key=key)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_json('cancel_attachment_upload', {'upload_key': key, 'ticket_id': self.ticket.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attachment.objects.filter(pk=att.id).exists())
        self.assertFalse(default_storage.exists(att.storage_key))
        with self.assertRaises(APIValidationError) as error:
            self.upload(key=key)
        self.assertEqual(error.exception.code, 'upload_cancelled')

    def test_cancel_published_upload_is_conflict_and_wrong_scope_is_conflict(self):
        key = str(uuid.uuid4())
        att = self.upload(key=key)
        self.assertEqual(self.post_json('cancel_attachment_upload', {'upload_key': key, 'draft_key': self.draft_key}).status_code, 409)
        CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id])
        response = self.post_json('cancel_attachment_upload', {'upload_key': key, 'ticket_id': self.ticket.id})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(AttachmentRequest.objects.filter(operation='cancel').exists())

    def test_delete_known_id_retains_tombstone_and_rolls_back_safely(self):
        key = uuid.uuid4()
        att = self.upload(key=key)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                AttachmentService.delete_pending(att.id, self.agent)
                raise RuntimeError('Rollback delete')
        self.assertTrue(Attachment.objects.filter(pk=att.id).exists())
        self.assertTrue(default_storage.exists(att.storage_key))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(reverse('delete_pending_attachment', args=[att.id])).status_code, 200)
        with self.assertRaises(APIValidationError) as error:
            self.upload(key=key)
        self.assertEqual(error.exception.status, 410)

    def test_upload_db_failure_rolls_back_record_ledger_and_blob(self):
        with patch.object(AttachmentService, 'remember', side_effect=RuntimeError('Ledger failed')):
            with self.assertRaises(RuntimeError):
                self.upload(draft=True)
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(AttachmentDraft.objects.exists())
        self.assertFalse(any(p.is_file() for p in Path(self.media).rglob('*')))

    def test_publication_rollback_preserves_draft_and_retry_can_succeed(self):
        att = self.upload(draft=True)
        payload = self.payload([att.id])
        with patch('app.services.tickets.AutomationService.run_for_ticket', side_effect=RuntimeError('Rollback')):
            with self.assertRaises(RuntimeError):
                TicketService.create_ticket(self.agent, payload)
        att.refresh_from_db()
        self.assertIsNone(att.ticket_id)
        self.assertIsNone(att.comment_id)
        self.assertIsNone(att.draft.published_at)
        self.assertTrue(default_storage.exists(att.storage_key))
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(AttachmentRequest.objects.filter(operation='ticket').exists())
        self.assertFalse(OutboundEmailLog.objects.exists())
        ticket = TicketService.create_ticket(self.agent, payload)
        self.assertEqual(ticket.comment_set.get().attachment_set.get().id, att.id)

    def test_comment_failure_rolls_back_claim_and_idempotency_key(self):
        att = self.upload()
        key = uuid.uuid4()
        with patch('app.services.comments.NotificationService.notify_ticket_users', side_effect=RuntimeError('Rollback')):
            with self.assertRaises(RuntimeError):
                CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id], request_id=key)
        att.refresh_from_db()
        self.assertIsNone(att.comment_id)
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(AttachmentRequest.objects.filter(operation='comment').exists())
        CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id], request_id=key)

    def test_ssr_attachment_cards_and_limits_use_one_attachment_query(self):
        from app.views import create_ticket
        for n in range(3):
            att = self.upload(name=f'{n}.txt')
            CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id])
        request = RequestFactory().get(reverse('create_ticket'), {'id': self.ticket.id, 'fragment': '1'})
        request.user = self.agent
        with patch('app.views.render', return_value=HttpResponse()) as render, CaptureQueriesContext(connections['default']) as queries:
            create_ticket(request)
        context = render.call_args.args[2]
        self.assertEqual(context['attachment_limits'], AttachmentService.limits())
        comments = [item['obj'] for item in context['feed'] if item['item_type'] == 'comment']
        self.assertEqual(len(comments), 3)
        with self.assertNumQueries(0):
            for comment in comments:
                self.assertEqual(comment.attachment_cards, [AttachmentService.metadata(a) for a in comment.attachment_set.all()])
        self.assertEqual(sum('FROM "app_attachment"' in q['sql'] for q in queries), 1)


@override_settings(**TEST_SETTINGS)
class AttachmentConcurrencyTests(AttachmentFixtures, TransactionTestCase):
    def race(self, *actions):
        barrier = threading.Barrier(len(actions))

        def run(action):
            try:
                barrier.wait(timeout=10)
                return action()
            except APIValidationError as exc:
                return exc.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            return list(executor.map(run, actions))

    @override_settings(PUBLIC_ATTACHMENT_MAX_FILES=1)
    def test_concurrent_uploads_enforce_limit(self):
        results = self.race(lambda: self.upload().id, lambda: self.upload().id)
        self.assertEqual(sum(isinstance(r, int) for r in results), 1)
        self.assertIn('too_many_files', results)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_concurrent_same_upload_key_returns_one_attachment(self):
        key = uuid.uuid4()
        results = self.race(lambda: self.upload(key=key).id, lambda: self.upload(key=key).id)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Attachment.objects.count(), 1)

    @override_settings(PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=10)
    def test_concurrent_uploads_enforce_total_bytes(self):
        results = self.race(lambda: self.upload(content=b'123456').id, lambda: self.upload(content=b'123456').id)
        self.assertEqual(sum(isinstance(r, int) for r in results), 1)
        self.assertIn('attachments_too_large', results)

    def test_concurrent_ticket_publication_returns_same_ticket(self):
        att = self.upload(draft=True)
        payload = self.payload([att.id])
        results = self.race(lambda: TicketService.create_ticket(self.agent, payload).id,
                            lambda: TicketService.create_ticket(self.agent, payload).id)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Ticket.objects.count(), 2)
        self.assertEqual(Comment.objects.count(), 1)

    def test_concurrent_publication_request_creates_one_comment(self):
        att = self.upload()
        key = uuid.uuid4()
        def publish():
            return CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id], request_id=key)[0].id
        results = self.race(publish, publish)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Comment.objects.count(), 1)

    def test_upload_cancel_race_always_finishes_cancelled(self):
        key = uuid.uuid4()
        self.race(lambda: self.upload(key=key).id,
                  lambda: AttachmentService.cancel_upload(self.agent, key, ticket_id=self.ticket.id))
        self.assertFalse(Attachment.objects.exists())
        self.assertTrue(AttachmentRequest.objects.filter(operation='cancel', key=key).exists())
        with self.assertRaises(APIValidationError):
            self.upload(key=key)

    def test_cancel_publication_race_has_no_partially_published_attachment(self):
        key = uuid.uuid4()
        att = self.upload(key=key)
        results = self.race(
            lambda: CommentService.add_comment(self.ticket, self.agent, '', attachment_ids=[att.id], request_id=uuid.uuid4())[0].id,
            lambda: AttachmentService.cancel_upload(self.agent, key, ticket_id=self.ticket.id),
        )
        if Comment.objects.exists():
            self.assertIn('attachment_already_sent', results)
            att.refresh_from_db()
            self.assertEqual(att.comment_id, Comment.objects.get().id)
            self.assertTrue(default_storage.exists(att.storage_key))
        else:
            self.assertIn('invalid_attachments', results)
            self.assertFalse(Attachment.objects.exists())
            self.assertFalse(default_storage.exists(att.storage_key))
