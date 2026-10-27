import io
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from PIL import Image

from app.models import Attachment, Comment, Ticket, User
from app.sanitizers import resolve_inline_cids, sanitize_email_html
from app.services.conversation import prepare_comments
from app.services.email_ingestion import EmailIngestionService
from app.tests_inbound_sweep import _SweepFixture, mail, T0


@override_settings(PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS={'png', 'txt'}, PUBLIC_ATTACHMENT_MAX_FILES=5,
                   PUBLIC_ATTACHMENT_MAX_BYTES=1024, PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=4096)
class GraphInlineTests(SimpleTestCase):
    def test_inline_files_do_not_use_regular_quota_and_all_pages_are_read(self):
        from app.services.msgraph_mail import fetch_attachments
        images = [{'id': f'img-{i}', 'name': f'img-{i}.png', 'size': 1, 'isInline': True,
                   'contentId': f'<cid-{i}>', 'contentType': 'image/png'} for i in range(6)]
        files = [{'id': f'file-{i}', 'name': f'file-{i}.txt', 'size': 1, 'isInline': False} for i in range(6)]
        next_page = 'https://graph.microsoft.com/v1.0/attachments-page-two'

        def request(method, url, headers, **kwargs):
            if url.endswith('/attachments'):
                return SimpleNamespace(json=lambda: {'value': images[:5] + files, '@odata.nextLink': next_page})
            if url == next_page:
                return SimpleNamespace(json=lambda: {'value': images[5:]})
            return SimpleNamespace(content=b'x')

        with patch('app.services.msgraph_mail._request', side_effect=request) as fetch:
            result = fetch_attachments('help@example.test', 'message-id', {})
        self.assertEqual(sum(a['is_inline'] for a in result), 6)
        self.assertEqual(sum(not a['is_inline'] for a in result), 5)
        self.assertIn('microsoft.graph.fileAttachment/contentId',
                      fetch.call_args_list[0].kwargs['params']['$select'].split(','))
        self.assertEqual(result[0]['content_id'], 'cid-0')

    def test_cid_rewriting_accepts_encoded_ids_and_keeps_sanitization(self):
        source = '<img src=cid:photo%40example.test onerror="bad()"><img src="cid:unknown"><a href="javascript:bad()">Link</a>'
        output = resolve_inline_cids(source, {'photo@example.test': '/attachments/42/preview/'})
        self.assertIn('src="/attachments/42/preview/"', output)
        self.assertNotIn('cid:', output)
        self.assertNotIn('onerror', output)
        self.assertNotIn('javascript:', output)
        self.assertNotIn('cid:', sanitize_email_html('<img src="cid:photo@example.test">'))


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False,
                   AI_CLASSIFICATION_ENABLED=False, ATTACHMENT_STORAGE_BACKEND='local',
                   PUBLIC_ATTACHMENTS_S3_BUCKET='', PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS={'png', 'txt'},
                   INBOUND_MARK_READ_ENABLED=False)
class EmailInlineTests(_SweepFixture):
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory(prefix='ticketflow-inline-', dir='C:/repo/.codex-tmp')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        image = io.BytesIO()
        Image.new('RGB', (2, 2), color='blue').save(image, format='PNG')
        self.png = image.getvalue()

    def image(self, content_id='photo@example.test'):
        return {'name': 'foto con espacios.png', 'content_type': 'image/png', 'content': self.png,
                'is_inline': True, 'content_id': content_id}

    def test_inline_only_mail_is_downloaded_even_when_graph_says_no_attachments(self):
        message = mail(1, T0 - timedelta(minutes=5), hasAttachments=False,
                       body={'contentType': 'html', 'content': '<p>Foto</p><img src="cid:photo@example.test">'})
        self.graph.deliver(message)
        with patch('app.services.msgraph_mail.fetch_attachments', return_value=[self.image()]) as fetch:
            self.assertIn('ok', self.sweep())
        fetch.assert_called_once()
        comment = Comment.objects.get(email_message_id=message['id'])
        attachment = Attachment.objects.get(comment=comment)
        self.assertTrue(attachment.is_inline)
        self.assertEqual(attachment.content_id, 'photo@example.test')
        self.assertIn(f'/attachments/{attachment.pk}/preview/', comment.html_body)
        self.assertNotIn('cid:', comment.html_body)

    def test_retries_keep_cid_urls_stable_and_inline_images_out_of_the_gallery(self):
        message = mail(2, T0, body={'contentType': 'html', 'content': '<p>Foto</p><img src="cid:photo@example.test">'})
        attachments = [self.image(), {'name': 'informe.txt', 'content': b'Informe', 'content_type': 'text/plain'}]
        result, ticket = EmailIngestionService._process_message(message, self.brand, attachments=attachments)
        self.assertEqual(result, 'ticket_created')
        comment = Comment.objects.get(ticket=ticket)
        original_html = comment.html_body
        result, _ = EmailIngestionService._process_message(message, self.brand, attachments=attachments)
        self.assertEqual(result, 'skip_dup')
        self.assertEqual(Attachment.objects.filter(comment=comment).count(), 2)
        comment.refresh_from_db()
        self.assertEqual(comment.html_body, original_html)
        self.client.force_login(ticket.requester)
        response = self.client.get('/tickets/create/', {'id': ticket.pk})
        feed_comment = next(x['obj'] for x in response.context['feed'] if x['item_type'] == 'comment')
        self.assertEqual([a['filename'] for a in feed_comment.attachment_cards], ['informe.txt'])
        from app.views import _comment_payload
        prepare_comments([comment])
        self.assertEqual([a['filename'] for a in _comment_payload(comment, ticket)['attachments']], ['informe.txt'])

    def test_inline_preview_is_protected_by_the_existing_ticket_permissions(self):
        message = mail(3, T0, body={'contentType': 'html', 'content': '<img src="cid:photo@example.test">'})
        _, ticket = EmailIngestionService._process_message(message, self.brand, attachments=[self.image()])
        attachment = Attachment.objects.get(ticket=ticket)
        self.client.force_login(ticket.requester)
        response = self.client.get(f'/attachments/{attachment.pk}/preview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        outsider = User.objects.create_user('inline-outsider@example.test', 'Ajeno')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f'/attachments/{attachment.pk}/preview/').status_code, 404)

    def test_different_content_ids_with_identical_names_and_sizes_are_not_collapsed(self):
        message = mail(4, T0, body={'contentType': 'html', 'content': '<img src="cid:first"><img src="cid:second">'})
        _, ticket = EmailIngestionService._process_message(message, self.brand,
                    attachments=[self.image('first'), self.image('second')])
        attachments = list(Attachment.objects.filter(ticket=ticket).order_by('pk'))
        self.assertEqual(len(attachments), 2)
        body = Comment.objects.get(ticket=ticket).html_body
        for attachment in attachments:
            self.assertIn(f'/attachments/{attachment.pk}/preview/', body)
