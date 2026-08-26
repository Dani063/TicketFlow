import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.management.commands.import_zendesk_help_centers import (
    _append_attachment_downloads, _clean_body, _is_client_article,
)
from app.models import (
    Attachment, Brand, HelpArticle, HelpCategory, HelpCenter, HelpSection,
    PublicTicketAttempt, SatisfactionRating, Ticket, User,
)
from app.public_forms import PublicTicketForm


@override_settings(
    ALLOWED_HOSTS=["localhost", "support.ecomfax.com", "testserver"],
    PUBLIC_FORM_MIN_SECONDS=0,
    PUBLIC_TICKET_IP_LIMIT=5,
    PUBLIC_TICKET_EMAIL_LIMIT=3,
    OUTBOUND_EMAIL_ENABLED=False,
    AI_CLASSIFICATION_ENABLED=False,
    PUBLIC_ATTACHMENTS_S3_BUCKET="",
)
class PublicHelpCenterTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.brand = Brand.objects.create(
            name="Portal test eComFax", support_email="support@example.com", from_name="eComFax",
        )
        self.center, _ = HelpCenter.objects.update_or_create(slug="ecomfax", defaults={
            "name": "eComFax", "service": "ecomfax", "brand": self.brand,
            "domains": ["support.ecomfax.com"], "default_locale": "es", "supported_locales": ["es", "en"],
            "tagline_es": "Ayuda eComFax", "tagline_en": "eComFax help",
            "primary_color": "#24213f", "accent_color": "#ffb703", "active": True,
        })
        self.category = HelpCategory.objects.create(
            center=self.center, locale="es", source_id=801, slug="primeros-pasos", name="Primeros pasos",
        )
        self.section = HelpSection.objects.create(
            category=self.category, source_id=802, slug="configuracion", name="Configuracion",
        )
        self.article = HelpArticle.objects.create(
            section=self.section, source_id=803, slug="configurar-cuenta", title="Configurar mi cuenta",
            body_html="<p>Configura tu fax desde el panel.</p>", body_text="Configura tu fax desde el panel.", promoted=True,
        )

    def tearDown(self):
        self.media_override.disable()
        self.media.cleanup()

    def _started(self, url):
        response = self.client.get(url, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        return response.context["form"]["started"].value()

    def _valid_payload(self, url, email="cliente@example.com"):
        return {
            "started": self._started(url), "website": "", "email": email,
            "subject": "No puedo enviar un fax", "description": "El envio queda pendiente desde esta mañana.",
            "phone": "+34 600 123 123",
        }

    def test_public_navigation_search_and_legacy_redirect(self):
        home = self.client.get(reverse("help_home", args=["ecomfax", "es"]), HTTP_HOST="localhost")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Primeros pasos")
        self.assertContains(home, "Enviar una solicitud")

        results = self.client.get(reverse("help_search", args=["ecomfax", "es"]), {"q": "configura fax"}, HTTP_HOST="localhost")
        self.assertEqual(results.status_code, 200)
        self.assertContains(results, "Configurar mi cuenta")
        self.assertContains(results, 'content="noindex, follow"')

        legacy = self.client.get(f"/hc/es/articles/{self.article.source_id}-old-title/", HTTP_HOST="support.ecomfax.com")
        self.assertEqual(legacy.status_code, 301)
        self.assertEqual(legacy.url, reverse("help_article", args=["ecomfax", "es", self.article.slug]))

        host_hub = self.client.get("/help/", HTTP_HOST="support.ecomfax.com")
        self.assertRedirects(host_hub, reverse("help_home", args=["ecomfax", "es"]), fetch_redirect_response=False)
        host_root = self.client.get("/", HTTP_HOST="support.ecomfax.com")
        self.assertRedirects(host_root, reverse("help_home", args=["ecomfax", "es"]), fetch_redirect_response=False)
        internal = self.client.get("/tickets/", HTTP_HOST="support.ecomfax.com")
        self.assertRedirects(internal, reverse("help_home", args=["ecomfax", "es"]), fetch_redirect_response=False)

    def test_anonymous_request_creates_normal_ticket_and_private_attachment(self):
        url = reverse("help_request", args=["ecomfax", "es"])
        payload = self._valid_payload(url)
        payload["service"] = "recordia"  # nunca debe fiarse de este valor
        payload["attachments"] = SimpleUploadedFile("captura prueba.txt", b"evidence", content_type="text/plain")
        response = self.client.post(url, payload, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/requests/success/", response.url)
        success = self.client.get(response.url, HTTP_HOST="localhost")
        self.assertEqual(success.status_code, 200)
        self.assertContains(success, "Hemos recibido tu solicitud")
        self.assertNotContains(success, "/tickets/")

        ticket = Ticket.objects.get(subject="No puedo enviar un fax")
        self.assertEqual(ticket.service, "ecomfax")
        self.assertEqual(ticket.channel, "web")
        self.assertEqual(ticket.brand, self.brand)
        self.assertEqual(ticket.requester.phone, "+34 600 123 123")
        self.assertFalse(ticket.requester.has_usable_password())
        attachment = Attachment.objects.get(ticket=ticket)
        self.assertTrue(attachment.is_private)
        self.assertEqual(attachment.original_name, "captura-prueba.txt")
        self.assertEqual(attachment.size, 8)

        anonymous = self.client.get(attachment.file_url, HTTP_HOST="localhost")
        self.assertEqual(anonymous.status_code, 404)
        self.client.force_login(ticket.requester)
        authorized = self.client.get(attachment.file_url, HTTP_HOST="localhost")
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized["Content-Disposition"], 'attachment; filename="captura-prueba.txt"')
        authorized.close()

    def test_invalid_extension_does_not_create_ticket(self):
        url = reverse("help_request", args=["ecomfax", "es"])
        payload = self._valid_payload(url, "bad-file@example.com")
        payload["attachments"] = SimpleUploadedFile("payload.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(url, payload, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no está permitido")
        self.assertFalse(Ticket.objects.filter(requester__email="bad-file@example.com").exists())

    @override_settings(PUBLIC_TICKET_IP_LIMIT=1)
    def test_rate_limit_blocks_second_request(self):
        url = reverse("help_request", args=["ecomfax", "es"])
        first = self.client.post(url, self._valid_payload(url, "one@example.com"), HTTP_HOST="localhost", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(first.status_code, 302)
        second = self.client.post(url, self._valid_payload(url, "two@example.com"), HTTP_HOST="localhost", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(PublicTicketAttempt.objects.filter(center=self.center).count(), 1)

    def test_form_honeypot_and_signed_start_are_required(self):
        form = PublicTicketForm({
            "started": "invalid", "website": "https://spam.example", "email": "x@example.com",
            "subject": "Spam", "description": "Spam body", "phone": "123",
        }, locale="es")
        self.assertFalse(form.is_valid())
        self.assertIn("started", form.errors)
        self.assertIn("website", form.errors)

    def test_import_sanitizer_removes_scripts_and_untrusted_embeds(self):
        cleaned = _clean_body('<p>Bien</p><script>alert(1)</script><iframe src="https://evil.example/embed"></iframe><iframe src="https://www.youtube.com/embed/abc"></iframe>')
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("evil.example", cleaned)
        self.assertIn("youtube.com/embed/abc", cleaned)
        self.assertIn('loading="lazy"', cleaned)


@override_settings(OUTBOUND_EMAIL_ENABLED=False, AI_CLASSIFICATION_ENABLED=False, ALLOWED_HOSTS=["localhost", "testserver"])
class PublicSatisfactionBrandingTests(TestCase):
    def test_survey_uses_product_portal_brand_and_language(self):
        role_user = User.objects.create_user("survey@example.com", "Survey customer", "unused-password")
        brand = Brand.objects.create(name="Survey brand", support_email="support@example.com")
        center = HelpCenter.objects.create(
            slug="survey-ecomfax", name="eComFax", service="survey-service", brand=brand,
            domains=[], default_locale="en", supported_locales=["es", "en"],
            primary_color="#24213f", accent_color="#ffb703",
        )
        ticket = Ticket.objects.create(
            subject="Resolved request", description="Done", status="resolved", requester=role_user,
            created_by=role_user, brand=brand, service="survey-service", channel="web", language="en",
        )
        rating = SatisfactionRating.objects.create(
            ticket=ticket, score="offered", source=SatisfactionRating.SOURCE_NATIVE,
            token="valid-public-rating-token", offered_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7), requester=role_user,
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        response = self.client.get(reverse("satisfaction_survey", args=[rating.token]), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "How would you rate the support you received?")
        self.assertContains(response, "--brand-primary:#24213f")
        self.assertContains(response, 'content="noindex, nofollow"')


class HelpContentSnapshotTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(settings.PROJECT_ROOT) / "resources" / "help_content" / "help_centers.json"
        cls.snapshot = json.loads(path.read_text(encoding="utf-8"))

    def _articles(self, center_slug, locale=None):
        center = next(row for row in self.snapshot["centers"] if row["slug"] == center_slug)
        return [
            (category["locale"], article)
            for category in center["categories"]
            if locale is None or category["locale"] == locale
            for section in category["sections"]
            for article in section["articles"]
        ]

    def test_non_inline_zendesk_attachments_become_downloads(self):
        body = _append_attachment_downloads("", [{
            "id": 123, "display_file_name": "Manual de uso.docx",
            "content_url": "https://support.example/hc/article_attachments/123",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 985219, "inline": False,
        }], "es")
        self.assertIn("Descargas", body)
        self.assertIn("Manual%20de%20uso.docx", body)
        self.assertIn("962 kB", body)

    def test_only_public_and_end_user_segments_are_imported(self):
        allowed = [249029]
        self.assertTrue(_is_client_article({"draft": False, "user_segment_id": None}, allowed))
        self.assertTrue(_is_client_article({"draft": False, "user_segment_id": 249029}, allowed))
        self.assertFalse(_is_client_article({"draft": False, "user_segment_id": 249009}, allowed))
        self.assertFalse(_is_client_article({"draft": True, "user_segment_id": None}, allowed))

    def test_reported_missing_articles_and_manual_are_present(self):
        ecomfax = {article["source_id"]: article for _, article in self._articles("ecomfax", "es")}
        recordia_es = {article["source_id"]: article for _, article in self._articles("recordia", "es")}
        recordia_en = {article["source_id"]: article for _, article in self._articles("recordia", "en")}

        self.assertIn(".docx", ecomfax[10278697827612]["body_html"])
        self.assertIn("/static/help_centers/content/", ecomfax[10278697827612]["body_html"])
        self.assertIn(360021526379, recordia_es)
        self.assertIn("/static/help_centers/content/recordia/360021526379/", recordia_es[360021526379]["body_html"])
        self.assertIn(360021640359, recordia_en)

    def test_snapshot_has_no_empty_articles_or_broken_local_assets(self):
        static_refs = set()
        for center in self.snapshot["centers"]:
            for _, article in self._articles(center["slug"]):
                self.assertTrue(article["body_html"].strip(), f"Artículo vacío: {article['source_id']}")
                self.assertNotRegex(
                    article["body_html"],
                    r'(?:href|src)=["\'](?:https?://[^"\']+)?/hc/(?:[^/]+/)?article_attachments/',
                    f"Adjunto remoto sin localizar: {article['source_id']}",
                )
                static_refs.update(re.findall(r'["\'](/static/help_centers/[^"\']+)', article["body_html"]))

        self.assertTrue(static_refs)
        for url in static_refs:
            target = Path(settings.PROJECT_ROOT) / "resources" / url.lstrip("/")
            self.assertTrue(target.is_file(), f"Recurso local inexistente: {url}")

    def test_all_local_article_links_resolve(self):
        for center in self.snapshot["centers"]:
            for locale in center["supported_locales"]:
                articles = [article for _, article in self._articles(center["slug"], locale)]
                valid_slugs = {article["slug"] for article in articles}
                pattern = re.compile(rf'/help/{re.escape(center["slug"])}/{locale}/articles/([^/"\'?#]+)/')
                for article in articles:
                    for target_slug in pattern.findall(article["body_html"]):
                        self.assertIn(
                            target_slug, valid_slugs,
                            f"Enlace roto desde {article['source_id']} a {target_slug}",
                        )
