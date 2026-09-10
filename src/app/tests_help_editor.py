import json
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from app.management.commands.import_zendesk_help_centers import Command, SOURCES
from app.models import (
    HelpArticle,
    HelpArticleRevision,
    HelpCategory,
    HelpCenter,
    HelpSection,
    Role,
    User,
)


@override_settings(
    ALLOWED_HOSTS=["testserver", "help-editor.example"],
    HELP_EDITOR_ENABLED=True,
    HELP_CONTENT_S3_BUCKET="",
    HELP_CONTENT_ALLOWED_EXTENSIONS={"png", "pdf", "txt"},
)
class HelpArticleEditorTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.agent_role, _ = Role.objects.get_or_create(role_name="Agent")
        self.end_role, _ = Role.objects.get_or_create(role_name="End user")
        self.agent = User.objects.create_user("agent-editor@example.com", "Agente editor", "test-pass")
        self.agent.role = self.agent_role
        self.agent.save(update_fields=["role"])
        self.customer = User.objects.create_user("reader-editor@example.com", "Lector", "test-pass")
        self.customer.role = self.end_role
        self.customer.save(update_fields=["role"])
        self.center = HelpCenter.objects.create(
            slug="editor-test",
            name="Editor Test",
            service="editor-test",
            domains=["help-editor.example"],
            default_locale="es",
            supported_locales=["es", "en"],
            primary_color="#172033",
            accent_color="#ffb703",
        )
        self.category = HelpCategory.objects.create(
            center=self.center,
            locale="es",
            slug="guias",
            name="Guías",
        )
        self.section = HelpSection.objects.create(
            category=self.category,
            slug="primeros-pasos",
            name="Primeros pasos",
        )
        self.article = HelpArticle.objects.create(
            section=self.section,
            slug="articulo-publicado",
            title="Artículo publicado",
            body_html="<p>Contenido público original.</p>",
            body_text="Contenido público original.",
            published=True,
        )
        revision = HelpArticleRevision.objects.create(
            article=self.article,
            number=1,
            section=self.section,
            slug=self.article.slug,
            title=self.article.title,
            body_html=self.article.body_html,
            body_text=self.article.body_text,
            status=HelpArticleRevision.STATUS_PUBLISHED,
            base_version=1,
        )
        self.article.published_revision = revision
        self.article.save(update_fields=["published_revision"])

    def tearDown(self):
        self.media_override.disable()
        self.media.cleanup()

    def _payload(self, **changes):
        payload = {
            "section": self.section.id,
            "title": "Título editado",
            "slug": self.article.slug,
            "body_html": "<h2>Respuesta</h2><p>Contenido nuevo.</p>",
            "promoted": "on",
            "position": 4,
            "change_note": "Mejora de la explicación",
            "expected_version": self.article.version,
            "intent": "save",
        }
        payload.update(changes)
        return payload

    def test_editor_requires_agent_and_is_not_exposed_on_public_host(self):
        url = reverse("help_editor_article_edit", args=[self.article.id])
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login/", anonymous.url)

        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.agent)
        internal = self.client.get(url)
        self.assertEqual(internal.status_code, 200)
        self.assertContains(internal, "Editar sin interrumpir lo publicado")

        public_host = self.client.get(url, HTTP_HOST="help-editor.example")
        self.assertRedirects(
            public_host,
            reverse("help_home", args=[self.center.slug, "es"]),
            fetch_redirect_response=False,
        )

    def test_non_agents_cannot_mutate_articles_or_assets(self):
        edit_url = reverse("help_editor_article_edit", args=[self.article.id])
        publish_url = reverse(
            "help_editor_article_publish",
            args=[self.article.id, self.article.published_revision_id],
        )
        upload_url = reverse("help_editor_asset_upload", args=[self.article.id])
        before_revisions = self.article.revisions.count()

        anonymous_edit = self.client.post(edit_url, self._payload(title="Intento anónimo"))
        self.assertEqual(anonymous_edit.status_code, 302)
        self.assertIn("/login/", anonymous_edit.url)

        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(edit_url, self._payload(title="Intento cliente")).status_code, 403)
        self.assertEqual(
            self.client.post(publish_url, {"expected_version": self.article.version}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(upload_url, {
                "asset": SimpleUploadedFile("no-autorizado.txt", b"blocked", content_type="text/plain"),
            }).status_code,
            403,
        )
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Artículo publicado")
        self.assertEqual(self.article.revisions.count(), before_revisions)
        self.assertFalse(self.article.assets.exists())

    @override_settings(HELP_EDITOR_ENABLED=False)
    def test_feature_flag_hides_controls_and_editor(self):
        self.client.force_login(self.agent)
        public_url = reverse("help_article", args=[self.center.slug, "es", self.article.slug])
        page = self.client.get(public_url)
        self.assertNotContains(page, "Modo agente")
        self.assertEqual(
            self.client.get(reverse("help_editor_article_edit", args=[self.article.id])).status_code,
            404,
        )

    def test_agent_controls_only_appear_on_internal_portal(self):
        self.client.force_login(self.agent)
        public_url = reverse("help_article", args=[self.center.slug, "es", self.article.slug])
        internal = self.client.get(public_url)
        self.assertContains(internal, "Modo agente")
        self.assertContains(internal, "Editar artículo")

        custom_host = self.client.get(public_url, HTTP_HOST="help-editor.example")
        self.assertNotContains(custom_host, "Modo agente")
        self.assertNotContains(custom_host, "Editar artículo")

    def test_draft_does_not_change_public_article_until_publish(self):
        self.client.force_login(self.agent)
        edit_url = reverse("help_editor_article_edit", args=[self.article.id])
        saved = self.client.post(edit_url, self._payload())
        self.assertEqual(saved.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Artículo publicado")
        self.assertEqual(self.article.body_text, "Contenido público original.")
        draft = self.article.revisions.get(status=HelpArticleRevision.STATUS_DRAFT)
        self.assertEqual(draft.title, "Título editado")

        public = self.client.get(reverse("help_article", args=[self.center.slug, "es", self.article.slug]))
        self.assertContains(public, "Contenido público original")
        self.assertNotContains(public, "Contenido nuevo")

        published = self.client.post(
            reverse("help_editor_article_publish", args=[self.article.id, draft.id]),
            {"expected_version": self.article.version},
        )
        self.assertEqual(published.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "Título editado")
        self.assertIn("Contenido nuevo", self.article.body_text)
        self.assertEqual(self.article.published_revision_id, draft.id)

    def test_new_draft_is_hidden_and_slug_is_unique_across_locale(self):
        second_category = HelpCategory.objects.create(center=self.center, locale="es", slug="otras", name="Otras")
        second_section = HelpSection.objects.create(category=second_category, slug="otra-seccion", name="Otra sección")
        self.client.force_login(self.agent)
        response = self.client.post(reverse("help_editor_article_new", args=[self.center.slug, "es"]), {
            "section": second_section.id,
            "title": "Artículo publicado",
            "slug": "articulo-publicado",
            "body_html": "<p>Este contenido aún es privado.</p>",
            "position": 0,
            "change_note": "Borrador inicial",
            "expected_version": 0,
            "intent": "save",
        })
        self.assertEqual(response.status_code, 302)
        created = HelpArticle.objects.exclude(pk=self.article.pk).get(section=second_section)
        self.assertEqual(created.slug, "articulo-publicado-2")
        self.assertFalse(created.published)
        public = self.client.get(reverse("help_article", args=[self.center.slug, "es", created.slug]))
        self.assertEqual(public.status_code, 404)
        search = self.client.get(reverse("help_search", args=[self.center.slug, "es"]), {"q": "privado"})
        self.assertNotContains(search, created.title)

    def test_translation_flow_links_both_locales(self):
        category_en = HelpCategory.objects.create(center=self.center, locale="en", slug="guides", name="Guides")
        section_en = HelpSection.objects.create(category=category_en, slug="getting-started", name="Getting started")
        self.client.force_login(self.agent)
        url = reverse("help_editor_article_new", args=[self.center.slug, "en"])
        start = self.client.get(url, {"translate_from": self.article.id})
        self.assertEqual(start.status_code, 200)
        self.assertContains(start, "Base para traducción")
        self.assertContains(start, self.article.title)

        created = self.client.post(url, {
            "translate_from": self.article.id,
            "section": section_en.id,
            "title": "Published article",
            "slug": "published-article",
            "body_html": "<p>Translated content.</p>",
            "position": 0,
            "expected_version": 0,
            "intent": "save",
        })
        self.assertEqual(created.status_code, 302)
        translated = HelpArticle.objects.get(section=section_en)
        self.article.refresh_from_db()
        self.assertTrue(self.article.translation_key)
        self.assertEqual(translated.translation_key, self.article.translation_key)
        self.assertEqual(translated.locale, "en")

    def test_sanitizer_and_optimistic_lock(self):
        self.client.force_login(self.agent)
        stale_version = self.article.version
        edit_url = reverse("help_editor_article_edit", args=[self.article.id])
        first = self.client.post(edit_url, self._payload(
            expected_version=stale_version,
            body_html='<p onclick="alert(1)">Seguro</p><script>alert(2)</script>',
        ))
        self.assertEqual(first.status_code, 302)
        draft = self.article.revisions.get(status=HelpArticleRevision.STATUS_DRAFT)
        self.assertNotIn("onclick", draft.body_html)
        self.assertNotIn("<script", draft.body_html)

        stale = self.client.post(edit_url, self._payload(expected_version=stale_version, title="Cambio simultáneo"))
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, "cambió mientras", status_code=409)
        self.assertFalse(self.article.revisions.filter(title="Cambio simultáneo").exists())

    def test_history_restore_and_unpublish_preserve_content(self):
        self.client.force_login(self.agent)
        self.client.post(reverse("help_editor_article_edit", args=[self.article.id]), self._payload())
        self.article.refresh_from_db()
        original = self.article.revisions.get(number=1)
        restored = self.client.post(
            reverse("help_editor_article_restore", args=[self.article.id, original.id]),
            {"expected_version": self.article.version},
        )
        self.assertEqual(restored.status_code, 302)
        self.article.refresh_from_db()
        self.assertTrue(self.article.revisions.filter(change_note__contains="Restaurada").exists())

        withdrawn = self.client.post(
            reverse("help_editor_article_unpublish", args=[self.article.id]),
            {"expected_version": self.article.version},
        )
        self.assertEqual(withdrawn.status_code, 302)
        self.article.refresh_from_db()
        self.assertFalse(self.article.published)
        self.assertTrue(self.article.revisions.exists())

    def test_private_asset_upload_and_stable_download(self):
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("help_editor_asset_upload", args=[self.article.id]),
            {"asset": SimpleUploadedFile("captura prueba.png", b"fake-png", content_type="image/png")},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        self.assertIn("/help/assets/", payload["url"])
        download = self.client.get(payload["url"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")
        download.close()

        self.article.published = False
        self.article.save(update_fields=["published"])
        self.client.logout()
        self.assertEqual(self.client.get(payload["url"]).status_code, 404)

    def test_importer_does_not_overwrite_or_prune_editorial_content(self):
        self.article.source_id = 99101
        self.article.editorial_override = True
        self.article.origin = HelpArticle.ORIGIN_ZENDESK
        self.article.save(update_fields=["source_id", "editorial_override", "origin"])
        manual = HelpArticle.objects.create(
            section=self.section,
            slug="manual-agent",
            title="Manual del agente",
            body_html="<p>No borrar</p>",
            body_text="No borrar",
            origin=HelpArticle.ORIGIN_AGENT,
            editorial_override=True,
            published=True,
        )
        data = {"centers": [{
            "slug": self.center.slug,
            "name": self.center.name,
            "service": self.center.service,
            "domains": self.center.domains,
            "default_locale": "es",
            "supported_locales": ["es", "en"],
            "tagline_es": "",
            "tagline_en": "",
            "primary": "#172033",
            "accent": "#ffb703",
            "categories": [{
                "locale": "es",
                "source_id": 7001,
                "translation_key": "category-7001",
                "slug": self.category.slug,
                "name": self.category.name,
                "sections": [{
                    "source_id": 7002,
                    "translation_key": "section-7002",
                    "slug": self.section.slug,
                    "name": self.section.name,
                    "articles": [{
                        "source_id": 99101,
                        "translation_key": "article-99101",
                        "slug": self.article.slug,
                        "title": "Título del origen",
                        "body_html": "<p>Contenido del origen</p>",
                    }],
                }],
            }],
        }]}
        with patch.dict(SOURCES, {
            self.center.slug: {
                "brand_setting": "PUBLIC_ECOMFAX_BRAND_NAME",
            },
        }):
            Command()._import(data, prune=True)
        self.article.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(self.article.title, "Artículo publicado")
        self.assertTrue(self.article.published)
        self.assertTrue(manual.published)
