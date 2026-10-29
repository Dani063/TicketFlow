"""Límites de adjuntos por mensaje: 10 archivos y 50 MB por ticket, sin pendientes ocultos."""

import json
import tempfile
import uuid

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from app.models import Attachment
from app.services.attachments import AttachmentService
from app.tests import _BaseFixture


@override_settings(
    ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="",
    PUBLIC_ATTACHMENT_MAX_FILES=10, PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=50 * 1024 * 1024,
    OUTBOUND_EMAIL_ATTACHMENT_MAX_BYTES=25 * 1024 * 1024, OUTBOUND_EMAIL_ENABLED=False,
)
class AttachmentLimitTests(_BaseFixture):
    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(self.media_override.disable)
        self.ticket = self.make_ticket(brand=self.brand_a)
        self.client.force_login(self.agent)

    def post_upload(self, ticket=None, name="nota.txt", pending_ids=None):
        data = {"file": SimpleUploadedFile(name, b"contenido", content_type="text/plain"),
                "upload_key": str(uuid.uuid4())}
        if pending_ids is not None:
            data["pending_ids"] = json.dumps(pending_ids)
        return self.client.post(reverse("upload_attachment", args=[(ticket or self.ticket).id]), data)

    def upload(self, ticket=None, pending_ids=None, name="nota.txt"):
        response = self.post_upload(ticket, name, pending_ids)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()["id"]

    def orphan(self, ticket=None, size=1):
        """Un adjunto subido que el editor ya no tiene (pestaña cerrada o recargada)."""
        return Attachment.objects.create(
            file_url="/attachment/huérfano", file_type="text/plain", storage_key=f"pending/{uuid.uuid4()}.txt",
            storage_backend="local", original_name="huérfano.txt", size=size,
            ticket=ticket or self.ticket, uploaded_by=self.agent,
        )

    def test_ten_files_fit_in_a_message_and_the_eleventh_is_explained(self):
        ids = []
        for index in range(10):
            ids.append(self.upload(pending_ids=ids, name=f"{index}.txt"))

        response = self.post_upload(name="once.txt", pending_ids=ids)

        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "too_many_files")
        self.assertEqual(
            error["message"],
            "No se ha añadido «once.txt»: este mensaje ya tiene 10 archivos, el máximo por mensaje. "
            "Quita alguno para añadir otro.",
        )

    def test_uploads_left_by_a_closed_tab_do_not_count(self):
        for _ in range(10):
            self.orphan()

        ids = [self.upload(pending_ids=[])]
        for _ in range(9):
            ids.append(self.upload(pending_ids=ids))

        self.assertEqual(len(ids), 10)

    def test_the_limit_is_per_ticket(self):
        other = self.make_ticket(brand=self.brand_a)
        first = []
        for _ in range(3):
            first.append(self.upload(pending_ids=first))

        second = []
        for _ in range(10):
            second.append(self.upload(ticket=other, pending_ids=second))
        self.assertEqual(self.post_upload(ticket=other, pending_ids=second).status_code, 400)
        # Los del primer ticket no cuentan aunque se declaren al subir en el otro.
        self.assertEqual(self.post_upload(ticket=other, pending_ids=first + second[:7]).status_code, 200)

    def test_declared_size_counts_and_hidden_size_does_not(self):
        with override_settings(PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES=20):
            hidden = self.orphan(size=15)
            self.upload(pending_ids=[])
            declared = self.orphan(size=15)

            response = self.post_upload(name="grande.txt", pending_ids=[declared.id])

        self.assertEqual(hidden.size, 15)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "attachments_too_large")
        self.assertTrue(response.json()["error"]["message"].startswith(
            "No se ha añadido «grande.txt»: con él los adjuntos de este mensaje sumarían"))

    def test_without_pending_ids_old_clients_keep_counting_everything(self):
        for _ in range(10):
            self.orphan()

        response = self.post_upload()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "too_many_files")

    def test_invalid_pending_ids_are_rejected(self):
        response = self.post_upload(pending_ids="no-es-lista")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_attachment_ids")

    def publish(self, ids, is_public):
        return self.client.post(
            reverse("add_comment", args=[self.ticket.id]),
            data=json.dumps({"content": "Adjuntos", "is_public": is_public, "attachment_ids": ids,
                             "request_id": str(uuid.uuid4())}),
            content_type="application/json",
        )

    def test_a_public_reply_over_the_email_limit_is_refused_with_a_reason(self):
        big = [self.orphan(size=13 * 1024 * 1024).id, self.orphan(size=13 * 1024 * 1024).id]

        response = self.publish(big, is_public=True)

        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "email_attachments_too_large")
        self.assertEqual(
            error["message"],
            "Una respuesta pública se envía por correo y el correo admite como máximo 25 MB de adjuntos; "
            "este mensaje lleva 26 MB. Quita adjuntos o publícalo como nota interna.",
        )
        self.assertFalse(Attachment.objects.filter(id__in=big, comment__isnull=False).exists())

        self.assertEqual(self.publish(big, is_public=False).status_code, 200)

    def test_publishing_more_than_ten_files_is_explained(self):
        ids = [self.orphan().id for _ in range(11)]

        response = self.publish(ids, is_public=False)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["message"],
            "Demasiados archivos por mensaje: lleva 11 y el máximo es 10. Quita alguno antes de enviar.",
        )

    def test_publishing_an_attachment_already_cleaned_up_says_so(self):
        gone = self.orphan()
        kept = self.orphan()
        gone_id = gone.id
        gone.delete()

        response = self.publish([gone_id, kept.id], is_public=False)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["message"],
            "Un adjunto ya no está en el servidor: lo subido y no publicado se borra a las 24 horas. "
            "Quítalo del mensaje y vuelve a añadirlo.",
        )

    def test_size_labels(self):
        self.assertEqual(AttachmentService.size_label(500), "1 KB")
        self.assertEqual(AttachmentService.size_label(552 * 1024), "552 KB")
        self.assertEqual(AttachmentService.size_label(25 * 1024 * 1024), "25 MB")
        self.assertEqual(AttachmentService.size_label(int(27.3 * 1024 * 1024)), "27,3 MB")


class DefaultAttachmentLimitTests(SimpleTestCase):
    def test_defaults_are_ten_files_and_fifty_megabytes_with_email_at_twenty_five(self):
        self.assertEqual(settings.PUBLIC_ATTACHMENT_MAX_FILES, 10)
        self.assertEqual(settings.PUBLIC_ATTACHMENT_MAX_TOTAL_BYTES, 50 * 1024 * 1024)
        self.assertEqual(settings.OUTBOUND_EMAIL_ATTACHMENT_MAX_BYTES, 25 * 1024 * 1024)
