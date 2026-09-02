import io
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.api import APIValidationError
from app.models import (
    Attachment, Brand, Comment, InboundEmailLog, OutboundEmailLog, ProductLine,
    ResponseTemplate, Role, Ticket, User,
)
from app.services.attachments import AttachmentService
from app.services.comments import CommentService
from app.services.email_ingestion import EmailIngestionService
from app.services.email_outbound import OutboundEmailService


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS={"txt", "pdf", "png"},
    PUBLIC_ATTACHMENT_MAX_FILES=5,
    PUBLIC_ATTACHMENT_MAX_BYTES=15 * 1024 * 1024,
    AI_CLASSIFICATION_ENABLED=False,
    OUTBOUND_EMAIL_ENABLED=False,
)
class AttachmentStorageTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()

        role = Role.objects.create(role_name="agent")
        self.agent = User.objects.create_user(
            email="attachment-agent@example.com",
            name="Attachment Agent",
            password="secret",
        )
        self.agent.role = role
        self.agent.save(update_fields=["role"])
        product, _ = ProductLine.objects.get_or_create(
            code="attachment-tests",
            defaults={"name": "Attachment tests", "active": True},
        )
        self.ticket = Ticket.objects.create(
            subject="Attachment test",
            description="desc",
            status="open",
            requester=self.agent,
            assignee=self.agent,
            created_by=self.agent,
            product_line=product,
        )

    def tearDown(self):
        self.media_override.disable()
        self.media.cleanup()

    @staticmethod
    def _file(name="evidence.txt", content=b"evidence"):
        return SimpleUploadedFile(name, content, content_type="text/plain")

    @override_settings(
        ATTACHMENT_STORAGE_BACKEND="s3",
        PUBLIC_ATTACHMENTS_S3_BUCKET="ticketflow-private-test",
        PUBLIC_ATTACHMENTS_S3_REGION="eu-west-1",
        ATTACHMENT_STORAGE_PREFIX="dev",
    )
    @patch("app.services.attachments.AttachmentService._s3_client")
    def test_s3_upload_uses_private_key_and_persists_metadata(self, client_factory):
        client = client_factory.return_value
        attachment = AttachmentService.save(self._file(), self.ticket, self.agent)

        self.assertEqual(attachment.storage_backend, "s3")
        self.assertTrue(attachment.storage_key.startswith(
            f"dev/attachments/tickets/{self.ticket.id}/"
        ))
        self.assertEqual(attachment.original_name, "evidence.txt")
        client.upload_fileobj.assert_called_once()
        args = client.upload_fileobj.call_args.args
        self.assertEqual(args[1], "ticketflow-private-test")
        self.assertEqual(args[2], attachment.storage_key)
        self.assertEqual(
            client.upload_fileobj.call_args.kwargs["ExtraArgs"]["ServerSideEncryption"],
            "AES256",
        )

    @override_settings(
        ATTACHMENT_STORAGE_BACKEND="s3",
        PUBLIC_ATTACHMENTS_S3_BUCKET="ticketflow-private-test",
    )
    @patch("app.services.attachments.AttachmentService._s3_client")
    def test_s3_access_denied_becomes_structured_service_error(self, client_factory):
        client_factory.return_value.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}},
            "PutObject",
        )
        with self.assertRaises(APIValidationError) as raised:
            AttachmentService.save(self._file(), self.ticket, self.agent)

        self.assertEqual(raised.exception.code, "attachment_storage_unavailable")
        self.assertEqual(raised.exception.status, 503)
        self.assertFalse(Attachment.objects.filter(ticket=self.ticket).exists())

    @override_settings(
        ATTACHMENT_STORAGE_BACKEND="s3",
        PUBLIC_ATTACHMENTS_S3_BUCKET="ticketflow-private-test",
    )
    @patch("app.services.attachments.Attachment.objects.create")
    @patch("app.services.attachments.AttachmentService._s3_client")
    def test_s3_object_is_removed_if_database_insert_fails(self, client_factory, create):
        create.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            AttachmentService.save(self._file(), self.ticket, self.agent)
        client_factory.return_value.delete_object.assert_called_once()

    @override_settings(
        ATTACHMENT_STORAGE_BACKEND="s3",
        PUBLIC_ATTACHMENTS_S3_BUCKET="ticketflow-private-test",
    )
    @patch("app.services.attachments.AttachmentService._s3_client")
    def test_s3_content_can_be_read_for_email_delivery(self, client_factory):
        attachment = Attachment.objects.create(
            file_url="/attachment",
            file_type="text/plain",
            storage_key="attachments/tickets/1/a.txt",
            storage_backend="s3",
            original_name="a.txt",
            size=7,
            ticket=self.ticket,
            uploaded_by=self.agent,
        )
        client_factory.return_value.get_object.return_value = {"Body": io.BytesIO(b"content")}
        self.assertEqual(AttachmentService.read(attachment), b"content")

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_pending_attachment_can_be_removed_by_its_uploader(self):
        attachment = AttachmentService.save(self._file(), self.ticket, self.agent)
        stored_path = attachment.storage_key
        self.assertTrue(Attachment.objects.filter(id=attachment.id).exists())

        self.client.force_login(self.agent)
        response = self.client.post(reverse("delete_pending_attachment", args=[attachment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attachment.objects.filter(id=attachment.id).exists())
        from django.core.files.storage import default_storage
        self.assertFalse(default_storage.exists(stored_path))

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_comment_cannot_claim_another_agents_pending_attachment(self):
        role = Role.objects.get(role_name="agent")
        other_agent = User.objects.create_user(
            email="other-attachment-agent@example.com",
            name="Other Attachment Agent",
            password="secret",
        )
        other_agent.role = role
        other_agent.save(update_fields=["role"])
        attachment = AttachmentService.save(self._file(), self.ticket, other_agent)

        comment, _ = CommentService.add_comment(
            ticket=self.ticket,
            actor=self.agent,
            content="Respuesta sin apropiarse del fichero ajeno",
            attachment_ids=[attachment.id],
        )

        attachment.refresh_from_db()
        self.assertIsNone(attachment.comment_id)
        self.assertFalse(comment.attachment_set.exists())

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_cleanup_removes_only_old_unattached_files(self):
        old = AttachmentService.save(self._file("old.txt"), self.ticket, self.agent)
        recent = AttachmentService.save(self._file("recent.txt"), self.ticket, self.agent)
        Attachment.objects.filter(id=old.id).update(
            uploaded_at=timezone.now() - timedelta(hours=25),
        )

        result = AttachmentService.cleanup_orphans(
            cutoff=timezone.now() - timedelta(hours=24),
        )

        self.assertEqual(result, {"deleted": 1, "failed": 0})
        self.assertFalse(Attachment.objects.filter(id=old.id).exists())
        self.assertTrue(Attachment.objects.filter(id=recent.id).exists())

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_upload_endpoint_enforces_pending_file_limit(self):
        for index in range(5):
            Attachment.objects.create(
                file_url=f"/attachment/{index}",
                file_type="text/plain",
                storage_key=f"pending/{index}.txt",
                storage_backend="local",
                original_name=f"{index}.txt",
                size=1,
                ticket=self.ticket,
                uploaded_by=self.agent,
            )
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("upload_attachment", args=[self.ticket.id]),
            {"file": self._file("sixth.txt")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "too_many_files")

    @override_settings(ATTACHMENT_STORAGE_BACKEND="s3", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_forced_s3_mode_refuses_silent_local_fallback(self):
        with self.assertRaises(APIValidationError) as raised:
            AttachmentService.save(self._file(), self.ticket, self.agent)
        self.assertEqual(raised.exception.code, "attachment_storage_not_configured")
        self.assertEqual(raised.exception.status, 503)

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_inbound_email_stores_attachment_and_retry_is_idempotent(self):
        brand = Brand.objects.create(
            name="Inbound attachment brand",
            support_email="attachments-inbound@example.com",
        )
        message = {
            "id": "graph-message-with-file",
            "conversationId": "graph-conversation-with-file",
            "subject": "Evidence",
            "from": {"emailAddress": {
                "address": "inbound-customer@example.com", "name": "Inbound Customer",
            }},
            "body": {"contentType": "text", "content": "Attached evidence"},
        }
        attachments = [{
            "name": "evidence.txt",
            "content_type": "text/plain",
            "content": b"email attachment",
        }]

        result = EmailIngestionService.process_message(message, brand, attachments=attachments)
        self.assertEqual(result, "ticket_created")
        comment = Comment.objects.get(email_message_id=message["id"])
        attachment = Attachment.objects.get(comment=comment)
        self.assertEqual(AttachmentService.read(attachment), b"email attachment")

        InboundEmailLog.objects.filter(brand=brand, message_id=message["id"]).update(
            status=InboundEmailLog.STATUS_FAILED,
        )
        retry = EmailIngestionService.process_message(message, brand, attachments=attachments)
        self.assertEqual(retry, "skip_dup")
        self.assertEqual(Attachment.objects.filter(comment=comment).count(), 1)

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_agent_reply_payload_snapshots_comment_attachments(self):
        brand = Brand.objects.create(
            name="Outbound attachment brand",
            support_email="attachments-outbound@example.com",
            mailbox_type="ses",
        )
        self.ticket.brand = brand
        self.ticket.save(update_fields=["brand"])
        ResponseTemplate.objects.create(
            key="agent_reply",
            brand=brand,
            language="es",
            subject="[Ticket #{{ticket_id}}] Respuesta",
            body_html="<p>{{comment_html}}</p>",
            body_text="{{comment_text}}",
        )
        comment = Comment.objects.create(
            ticket=self.ticket,
            user=self.agent,
            content="Respuesta con fichero",
        )
        attachment = AttachmentService.save(
            self._file("answer.txt", b"answer"), self.ticket, self.agent, comment=comment,
        )

        with override_settings(
            OUTBOUND_EMAIL_ENABLED=True,
            OUTBOUND_AGENT_REPLY_ENABLED=True,
        ):
            log = OutboundEmailService.queue_agent_reply(self.ticket, comment)

        self.assertEqual(log.payload["attachment_ids"], [attachment.id])

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    @patch("boto3.client")
    def test_ses_delivery_contains_attachment(self, client_factory):
        brand = Brand.objects.create(
            name="SES attachment brand",
            from_name="TicketFlow",
            support_email="ses-attachments@example.com",
            mailbox_type="ses",
        )
        self.ticket.brand = brand
        self.ticket.save(update_fields=["brand"])
        comment = Comment.objects.create(ticket=self.ticket, user=self.agent, content="File")
        attachment = AttachmentService.save(
            self._file("ses.txt", b"ses-content"), self.ticket, self.agent, comment=comment,
        )
        log = OutboundEmailLog.objects.create(
            brand=brand,
            ticket=self.ticket,
            provider="ses",
            to_email="recipient@example.com",
            subject="Attachment",
            payload={
                "body_html": "<p>Attachment</p>",
                "body_text": "Attachment",
                "attachment_ids": [attachment.id],
            },
        )
        client_factory.return_value.send_email.return_value = {"MessageId": "ses-file"}

        self.assertEqual(OutboundEmailService.deliver(log), "ses-file")
        sent = client_factory.return_value.send_email.call_args.kwargs
        item = sent["Content"]["Simple"]["Attachments"][0]
        self.assertEqual(item["FileName"], "ses.txt")
        self.assertEqual(item["RawContent"], b"ses-content")

    @override_settings(ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="")
    def test_graph_delivery_contains_small_attachment(self):
        brand = Brand.objects.create(
            name="Graph attachment brand",
            support_email="graph-attachments@example.com",
            mailbox_type="m365",
        )
        self.ticket.brand = brand
        self.ticket.save(update_fields=["brand"])
        comment = Comment.objects.create(ticket=self.ticket, user=self.agent, content="File")
        attachment = AttachmentService.save(
            self._file("graph.txt", b"graph-content"), self.ticket, self.agent, comment=comment,
        )
        log = OutboundEmailLog.objects.create(
            brand=brand,
            ticket=self.ticket,
            provider="m365",
            to_email="recipient@example.com",
            subject="Attachment",
            payload={"body_html": "<p>Attachment</p>", "attachment_ids": [attachment.id]},
        )

        with patch("app.services.msgraph.get_graph_token", return_value="token"), \
             patch("requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            self.assertIsNone(OutboundEmailService.deliver(log))

        sent_message = post.call_args.kwargs["json"]["message"]
        self.assertEqual(sent_message["attachments"][0]["name"], "graph.txt")
        self.assertEqual(sent_message["attachments"][0]["contentBytes"], "Z3JhcGgtY29udGVudA==")

    @override_settings(
        ATTACHMENT_STORAGE_BACKEND="local",
        PUBLIC_ATTACHMENTS_S3_BUCKET="",
        GRAPH_SIMPLE_ATTACHMENT_MAX_BYTES=5,
    )
    def test_graph_delivery_uses_upload_session_for_large_attachment(self):
        brand = Brand.objects.create(
            name="Graph large attachment brand",
            support_email="graph-large@example.com",
            mailbox_type="m365",
        )
        self.ticket.brand = brand
        self.ticket.save(update_fields=["brand"])
        comment = Comment.objects.create(ticket=self.ticket, user=self.agent, content="File")
        attachment = AttachmentService.save(
            self._file("large.txt", b"123456"), self.ticket, self.agent, comment=comment,
        )
        log = OutboundEmailLog.objects.create(
            brand=brand,
            ticket=self.ticket,
            provider="m365",
            to_email="recipient@example.com",
            subject="Large attachment",
            payload={"body_html": "<p>Attachment</p>", "attachment_ids": [attachment.id]},
        )
        draft = MagicMock()
        draft.json.return_value = {"id": "draft-id"}
        session = MagicMock()
        session.json.return_value = {"uploadUrl": "https://upload.example/session"}
        sent = MagicMock()

        with patch("app.services.msgraph.get_graph_token", return_value="token"), \
             patch("requests.post", side_effect=[draft, session, sent]) as post, \
             patch("requests.put") as put:
            put.return_value.raise_for_status.return_value = None
            self.assertIsNone(OutboundEmailService.deliver(log))

        self.assertIn("/messages", post.call_args_list[0].args[0])
        self.assertIn("createUploadSession", post.call_args_list[1].args[0])
        self.assertTrue(post.call_args_list[2].args[0].endswith("/draft-id/send"))
        self.assertEqual(put.call_args.args[0], "https://upload.example/session")
        self.assertEqual(put.call_args.kwargs["headers"]["Content-Range"], "bytes 0-5/6")

    def test_graph_large_attachment_chunks_are_multiples_of_320_kib(self):
        chunk_size = 10 * 320 * 1024
        item = {
            "name": "large.bin",
            "content_type": "application/octet-stream",
            "content": b"x" * (chunk_size + 1),
        }
        session = MagicMock()
        session.json.return_value = {"uploadUrl": "https://upload.example/session"}

        with patch("requests.post", return_value=session), \
             patch("requests.put") as put:
            OutboundEmailService._upload_graph_large_attachment(
                "https://graph.example/user",
                "draft-id",
                item,
                {"Authorization": "Bearer token"},
            )

        self.assertEqual(put.call_count, 2)
        first_headers = put.call_args_list[0].kwargs["headers"]
        second_headers = put.call_args_list[1].kwargs["headers"]
        self.assertEqual(int(first_headers["Content-Length"]) % (320 * 1024), 0)
        self.assertEqual(first_headers["Content-Range"], f"bytes 0-{chunk_size - 1}/{chunk_size + 1}")
        self.assertEqual(second_headers["Content-Range"], f"bytes {chunk_size}-{chunk_size}/{chunk_size + 1}")

    @override_settings(
        PUBLIC_ATTACHMENT_ALLOWED_EXTENSIONS={"txt"},
        PUBLIC_ATTACHMENT_MAX_FILES=5,
        PUBLIC_ATTACHMENT_MAX_BYTES=1024,
        PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=2048,
    )
    @patch("app.tasks.requests.get")
    def test_graph_fetch_ignores_inline_files(self, get):
        from app.tasks import _fetch_m365_attachments

        listing = MagicMock()
        listing.json.return_value = {"value": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "inline-id", "name": "logo.png", "size": 5,
                "contentType": "image/png", "isInline": True,
            },
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "file/id", "name": "report.txt", "size": 6,
                "contentType": "text/plain", "isInline": False,
            },
        ]}
        content = MagicMock(content=b"report")
        get.side_effect = [listing, content]

        result = _fetch_m365_attachments(
            "helpdesk@example.com", "message/id", {"Authorization": "Bearer token"},
        )

        self.assertEqual(result[0]["content"], b"report")
        self.assertEqual(get.call_count, 2)
        self.assertIn("file%2Fid/$value", get.call_args.args[0])
