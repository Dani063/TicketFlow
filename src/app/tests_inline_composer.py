"""#21525: imágenes dentro del texto al responder, como en Outlook o Word."""

import base64
import json
import tempfile
import uuid
from email import message_from_bytes, policy as email_policy
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from app.models import Attachment, Comment, OutboundEmailLog
from app.services.email_outbound import OutboundEmailService
from app.tests import _BaseFixture

# PNG de 1x1 válido: la subida comprueba la firma del fichero.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@override_settings(
    ATTACHMENT_STORAGE_BACKEND="local", PUBLIC_ATTACHMENTS_S3_BUCKET="",
    OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True,
)
class InlineComposerImageTests(_BaseFixture):
    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(self.media_override.disable)
        self.ticket = self.make_ticket(brand=self.brand_a)
        self.client.force_login(self.agent)

    def upload(self, name="image.png", content=PNG, content_type="image/png"):
        response = self.client.post(
            reverse("upload_attachment", args=[self.ticket.id]),
            {"file": SimpleUploadedFile(name, content, content_type=content_type), "upload_key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        return response.json()["id"]

    def publish(self, html_body, attachment_ids, content="", is_public=True):
        response = self.client.post(
            reverse("add_comment", args=[self.ticket.id]),
            data=json.dumps({
                "content": content, "html_body": html_body, "is_public": is_public,
                "attachment_ids": attachment_ids, "request_id": str(uuid.uuid4()),
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()

    def test_image_in_the_text_is_inline_and_not_in_the_gallery(self):
        image = self.upload()
        document = self.upload("informe.txt", b"informe", "text/plain")

        data = self.publish(
            f'<p>Mira la captura:</p><p><img src="/attachments/{image}/preview/"></p><p>Y sigo escribiendo.</p>',
            [image, document], content="Mira la captura:\nY sigo escribiendo.",
        )

        inline = Attachment.objects.get(id=image)
        self.assertTrue(inline.is_inline)
        self.assertEqual(inline.content_id, f"tf-att-{image}")
        self.assertFalse(Attachment.objects.get(id=document).is_inline)
        self.assertIn(f"/attachments/{image}/preview/", data["conversation_html"])
        page = self.client.get(reverse("create_ticket") + f"?id={self.ticket.id}")
        cards = [
            card["id"]
            for item in page.context["feed"] if item["item_type"] == "comment"
            for card in item["obj"].attachment_cards
        ]
        self.assertNotIn(image, cards)
        self.assertIn(document, cards)

    def test_a_message_with_only_an_image_is_published(self):
        image = self.upload()

        self.publish(f'<p><img src="/attachments/{image}/preview/"></p>', [image])

        self.assertTrue(Comment.objects.filter(ticket=self.ticket, html_body__contains=f"/attachments/{image}/").exists())

    def test_customer_email_carries_the_image_inside_the_body(self):
        image = self.upload()
        document = self.upload("informe.txt", b"informe", "text/plain")
        self.publish(
            f'<p>Antes</p><p><img src="/attachments/{image}/preview/"></p><p>Después</p>',
            [image, document], content="Antes\nDespués",
        )
        log = OutboundEmailLog.objects.get(ticket=self.ticket, template_key="agent_reply")

        with patch("app.services.msgraph.get_graph_token", return_value="tok"), patch("requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            OutboundEmailService.deliver(log)

        sent = message_from_bytes(base64.b64decode(post.call_args.kwargs["data"]), policy=email_policy.default)
        html = sent.get_body(preferencelist=("html",))
        self.assertIn(f"cid:tf-att-{image}", html.get_content())
        self.assertNotIn("/attachments/", html.get_content())
        related = [part for part in sent.walk() if part.get("Content-ID")]
        self.assertEqual([part["Content-ID"] for part in related], [f"<tf-att-{image}>"])
        self.assertEqual(related[0].get_payload(decode=True), PNG)
        attached = [part.get_filename() for part in sent.iter_attachments()]
        self.assertEqual(attached, ["informe.txt"])

    def test_image_removed_from_the_text_goes_as_a_normal_attachment(self):
        image = self.upload()

        self.publish("<p>Sin la imagen</p>", [image], content="Sin la imagen")

        self.assertFalse(Attachment.objects.get(id=image).is_inline)
