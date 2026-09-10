"""Importa los centros de ayuda historicos y conserva un snapshot versionable."""

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify

from app.models import Brand, HelpArticle, HelpArticleRevision, HelpCategory, HelpCenter, HelpSection
from app.services.help_content import sanitize_help_html


SOURCES = {
    "ecomfax": {
        "name": "eComFax", "base": "https://support.ecomfax.com", "service": "ecomfax",
        "domains": ["support.ecomfax.com"], "locales": {"es": "es", "en": "en-001"},
        "end_user_segment_ids": [249029],
        "brand_setting": "PUBLIC_ECOMFAX_BRAND_NAME", "primary": "#24213f", "accent": "#ffb703",
        "tagline_es": "Guías claras para configurar, enviar y recibir faxes con eComFax.",
        "tagline_en": "Clear guides to configure, send and receive faxes with eComFax.",
    },
    "recordia": {
        "name": "Recordia", "base": "https://support.recordia.net", "service": "recordia",
        "domains": ["support.recordia.net"], "locales": {"es": "es", "en": "en-gb"},
        "end_user_segment_ids": [249029],
        "brand_setting": "PUBLIC_RECORDIA_BRAND_NAME", "primary": "#1d2340", "accent": "#4aa8e8",
        "tagline_es": "Documentación práctica para sacar el máximo partido a Recordia.",
        "tagline_en": "Practical documentation to get the most from Recordia.",
    },
}

MANUAL_ARTICLES = {
    "ecomfax": [
        {"locale": "es", "category_id": 360001374799, "category": "Cumplimiento y seguridad", "section_id": 360001914480, "section": "Documentación legal", "id": 360007226840, "title": "Acuerdo de licencia de usuario final (EULA) de eComFax", "body": '<p>Consulta las condiciones de licencia aplicables al servicio eComFax.</p><ul><li><a href="/static/help_centers/legal/ecomfax/eula-es.pdf">EULA eComFax (español)</a></li><li><a href="/static/help_centers/legal/ecomfax/eula-en.pdf">eComFax EULA (English)</a></li></ul>'},
        {"locale": "en", "category_id": 360001374799, "category": "Compliance and security", "section_id": 360001914480, "section": "Legal documentation", "id": 360007226840, "title": "eComFax end-user licence agreement (EULA)", "body": '<p>Read the licence terms that apply to the eComFax service.</p><ul><li><a href="/static/help_centers/legal/ecomfax/eula-en.pdf">eComFax EULA (English)</a></li><li><a href="/static/help_centers/legal/ecomfax/eula-es.pdf">EULA eComFax (español)</a></li></ul>'},
        {"locale": "es", "category_id": 360001374799, "category": "Cumplimiento y seguridad", "section_id": 360001914480, "section": "Documentación legal", "id": 360007238919, "title": "Certificado de conformidad con el Esquema Nacional de Seguridad", "body": '<p>Certificado de conformidad de Cloud Worldwide con el Esquema Nacional de Seguridad.</p><p><a href="/static/help_centers/legal/ecomfax/certificado-ens.pdf">Descargar certificado ENS (PDF)</a></p>'},
    ],
    "recordia": [
        {
            "locale": "es", "category_id": 360000014549, "category": "FAQ's",
            "section_id": 360000033769, "section": "Preguntas Frecuentes", "id": 360021526379,
            "title": "¿Cómo acceder y hacer Log In en Recordia?",
            "body": (
                '<p>Se puede acceder a la <strong>página web</strong> de <strong>Recordia</strong> mediante el '
                'siguiente enlace: <a href="https://www.recordia.net/es/grabacion-de-interacciones-en-la-nube/" '
                'target="_self">https://www.recordia.net/</a>.</p>'
                '<p>Una vez dentro, en la esquina superior derecha se encuentra el icono '
                '<strong>Entrar/Log In</strong>. Al hacer clic, se abrirá la página de acceso de Recordia:</p>'
                '<p><a href="https://user.recordia.net/recorder2/Account/Login?ReturnUrl=%2frecorder2" '
                'target="_self">Acceder a Recordia</a></p>'
                '<p><img src="/static/help_centers/content/recordia/360021526379/'
                '360025980080-login-recordia.png" alt="Pantalla de acceso a Recordia"></p>'
            ),
            "source_url": "https://support.recordia.net/hc/es/articles/360021526379--C%C3%B3mo-acceder-y-hacer-Log-In-en-Recordia",
            "source_updated_at": "2021-05-10T13:40:42Z",
        },
        {
            "locale": "en", "category_id": 360000014549, "category": "FAQ's",
            "section_id": 360000033769, "section": "FAQ", "id": 360021640359,
            "title": "How to access and log in to Recordia",
            "body": (
                '<p>You can access the <strong>Recordia website</strong> from '
                '<a href="https://www.recordia.net/es/grabacion-de-interacciones-en-la-nube/" '
                'target="_self">https://www.recordia.net/</a>.</p>'
                '<p>Use the <strong>Log In</strong> option in the top-right corner to open the Recordia sign-in page:</p>'
                '<p><a href="https://user.recordia.net/recorder2/Account/Login?ReturnUrl=%2frecorder2" '
                'target="_self">Access Recordia</a></p>'
                '<p><img src="/static/help_centers/content/recordia/360021526379/'
                '360025980080-login-recordia.png" alt="Recordia sign-in screen"></p>'
            ),
            "source_url": "https://support.recordia.net/hc/en-gb/articles/360021640359",
            "source_updated_at": "2021-05-10T13:40:42Z",
        },
        {"locale": "es", "category_id": 360003233480, "category": "Cumplimiento y seguridad", "section_id": 360005940179, "section": "Protección de datos", "id": 4402085100434, "title": "Declaración responsable de cumplimiento de la LOPDGDD", "body": '<p>Declaración responsable sobre el cumplimiento de la normativa de protección de datos aplicable a Recordia.</p><p><a href="/static/help_centers/legal/recordia/declaracion-lopdgdd.pdf">Descargar declaración firmada (PDF)</a></p>'},
        {"locale": "en", "category_id": 360003233480, "category": "Compliance and security", "section_id": 360005903440, "section": "Data protection", "id": 4402092750482, "title": "Recordia data protection compliance statement", "body": '<p>Signed compliance statement covering the data protection rules applicable to Recordia.</p><p><a href="/static/help_centers/legal/recordia/declaracion-lopdgdd.pdf">Download signed statement (PDF)</a></p>'},
    ],
}

COMMUNITY_ARTICLES = {
    "ecomfax": [
        {"id": 10417161903260, "es": ("Dificultades en el envío y recepción de faxes", "Si experimentas dificultades al enviar o recibir faxes, describe el caso mediante el formulario de soporte. Incluye fecha, número de origen y destino, mensaje de error y, si es posible, una captura."), "en": ("Problems sending or receiving faxes", "If you have trouble sending or receiving faxes, describe the case in the support form. Include the date, source and destination numbers, error message and, if possible, a screenshot.")},
        {"id": 10417113591196, "es": ("Proponer una nueva funcionalidad", "Puedes hacernos llegar ideas y sugerencias para eComFax mediante el formulario de soporte. Selecciona un asunto descriptivo y explica qué necesidad resolvería la propuesta."), "en": ("Suggest a new feature", "You can send ideas and suggestions for eComFax through the support form. Use a descriptive subject and explain the need your proposal would address.")},
    ],
    "recordia": [
        {"id": 11425116783900, "es": ("Proponer una nueva funcionalidad", "Puedes hacernos llegar ideas y sugerencias para Recordia mediante el formulario de soporte. Explica la necesidad, el flujo actual y el resultado que esperas."), "en": ("Suggest a new feature", "You can send ideas and suggestions for Recordia through the support form. Explain the need, the current workflow and the result you expect.")},
    ],
}

def _slug(title, source_id):
    return (slugify(title, allow_unicode=False)[:185] or f"article-{source_id}")


def _clean_body(body):
    # Se conserva como alias por compatibilidad con pruebas y scripts existentes.
    return sanitize_help_html(body)


def _human_size(value):
    size = max(int(value or 0), 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} kB"
    return f"{size} B"


def _attachment_download_url(attachment):
    """Build a stable URL with a useful filename for local asset naming."""
    url = attachment.get("content_url") or attachment.get("relative_path") or ""
    filename = attachment.get("display_file_name") or attachment.get("file_name") or "download"
    return f"{url.rstrip('/')}/{quote(filename)}" if url else ""


def _append_attachment_downloads(body, attachments, locale):
    downloads = []
    for attachment in attachments or []:
        if attachment.get("inline"):
            continue
        url = _attachment_download_url(attachment)
        source_id = str(attachment.get("id") or "")
        if not url or (source_id and source_id in body):
            continue
        filename = attachment.get("display_file_name") or attachment.get("file_name") or "Download"
        downloads.append(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(filename)}</a>'
            f'<span>{html.escape(_human_size(attachment.get("size")))}</span></li>'
        )
    if not downloads:
        return body
    heading = "Descargas" if locale == "es" else "Downloads"
    return body + f'<div class="help-downloads"><h2>{heading}</h2><ul>{"".join(downloads)}</ul></div>'


def _ensure_article_body(body, locale):
    if strip_tags(body or "").strip():
        return body
    message = (
        "El artículo original no contiene información adicional."
        if locale == "es"
        else "The original article does not contain any additional information."
    )
    return f'<p class="help-source-empty">{message}</p>'


def _is_client_article(article, end_user_segment_ids):
    segment_id = article.get("user_segment_id")
    return not article.get("draft") and (
        segment_id is None or segment_id in end_user_segment_ids
    )


class Command(BaseCommand):
    help = "Importa los articulos publicos de Zendesk y/o crea un snapshot local."

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", help="Ruta donde guardar el JSON obtenido")
        parser.add_argument("--from-snapshot", dest="from_snapshot", help="Importar desde un JSON sin acceder a Zendesk")
        parser.add_argument("--download-assets", action="store_true", help="Descarga imagenes y adjuntos al arbol static")
        parser.add_argument("--prune", action="store_true", help="Despublica contenido que ya no aparece en el origen")
        parser.add_argument(
            "--force-editorial-overrides", action="store_true",
            help="Sobrescribe artículos que un agente haya editado manualmente",
        )

    def handle(self, *args, **options):
        if options["from_snapshot"]:
            data = json.loads(Path(options["from_snapshot"]).read_text(encoding="utf-8"))
        else:
            data = self._fetch_all(download_assets=options["download_assets"])
            if options["prune"] and not self._authenticated_source:
                raise CommandError(
                    "No se permite --prune con el inventario anonimo: podria despublicar articulos "
                    "visibles solo para usuarios autenticados de Zendesk."
                )
        if options["snapshot"]:
            target = Path(options["snapshot"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Snapshot escrito en {target}"))
        counts = self._import(
            data,
            prune=options["prune"],
            force_editorial_overrides=options["force_editorial_overrides"],
        )
        self.stdout.write(self.style.SUCCESS(
            f"Importados {counts['centers']} centros, {counts['categories']} categorias, "
            f"{counts['sections']} secciones y {counts['articles']} articulos."
        ))

    def _get_pages(self, session, url, key):
        rows = []
        while url:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get(key, []))
            url = payload.get("next_page")
        return rows

    def _fetch_all(self, download_assets=False):
        session = requests.Session()
        session.headers["User-Agent"] = "TicketFlow-help-migration/1.0"
        email = getattr(settings, "ZENDESK_EMAIL", "")
        token = getattr(settings, "ZENDESK_API_TOKEN", "")
        self._authenticated_source = bool(email and token)
        if self._authenticated_source:
            session.auth = (f"{email}/token", token)
        else:
            self.stderr.write(
                "Inventario anonimo: los articulos limitados a usuarios autenticados solo se "
                "conservaran si ya forman parte de un snapshot."
            )
        result = {"version": 1, "centers": []}
        for slug, source in SOURCES.items():
            center_data = {
                key: value for key, value in source.items()
                if key not in {"base", "locales", "brand_setting", "end_user_segment_ids"}
            }
            center_data.update({"slug": slug, "default_locale": "es", "supported_locales": ["es", "en"], "categories": []})
            for locale, zendesk_locale in source["locales"].items():
                base_api = f"{source['base']}/api/v2/help_center/{zendesk_locale}"
                categories = self._get_pages(session, f"{base_api}/categories.json?per_page=100", "categories")
                sections = self._get_pages(session, f"{base_api}/sections.json?per_page=100", "sections")
                articles = self._get_pages(session, f"{base_api}/articles.json?per_page=100", "articles")
                sections_by_category = {}
                for section in sections:
                    sections_by_category.setdefault(section["category_id"], []).append(section)
                articles_by_section = {}
                for article in articles:
                    articles_by_section.setdefault(article["section_id"], []).append(article)
                for category in categories:
                    cat = {
                        "locale": locale, "source_id": category["id"], "translation_key": f"category-{category['id']}",
                        "slug": _slug(category["name"], category["id"]), "name": category["name"],
                        "description": category.get("description") or "", "position": category.get("position") or 0, "sections": [],
                    }
                    for section in sections_by_category.get(category["id"], []):
                        sec = {
                            "source_id": section["id"], "translation_key": f"section-{section['id']}",
                            "slug": _slug(section["name"], section["id"]), "name": section["name"],
                            "description": section.get("description") or "", "position": section.get("position") or 0, "articles": [],
                        }
                        for article in articles_by_section.get(section["id"], []):
                            if not _is_client_article(article, source["end_user_segment_ids"]):
                                continue
                            attachments = self._get_article_attachments(session, base_api, article["id"])
                            body = _ensure_article_body(_append_attachment_downloads(
                                _clean_body(article.get("body") or ""), attachments, locale,
                            ), locale)
                            sec["articles"].append({
                                "source_id": article["id"], "translation_key": f"article-{article['id']}",
                                "slug": _slug(article["title"], article["id"]), "title": article["title"], "body_html": body,
                                "promoted": bool(article.get("promoted")), "position": article.get("position") or 0,
                                "source_url": article.get("html_url") or "", "source_updated_at": article.get("updated_at"),
                                "attachments": [self._serialize_attachment(item) for item in attachments],
                            })
                        cat["sections"].append(sec)
                    cat["sections"] = [section for section in cat["sections"] if section["articles"]]
                    if cat["sections"]:
                        center_data["categories"].append(cat)
            self._append_manual(center_data, slug)
            self._append_community(center_data, slug)
            if download_assets:
                self._localize_assets(session, center_data, source["base"])
                self._prune_unreferenced_assets(center_data)
            self._rewrite_article_links(center_data)
            result["centers"].append(center_data)
        return result

    def _get_article_attachments(self, session, base_api, article_id):
        try:
            return self._get_pages(
                session,
                f"{base_api}/articles/{article_id}/attachments.json?per_page=100",
                "article_attachments",
            )
        except requests.RequestException as exc:
            self.stderr.write(f"No se pudieron inventariar los adjuntos del articulo {article_id}: {exc}")
            return []

    @staticmethod
    def _serialize_attachment(item):
        return {
            "source_id": item.get("id"),
            "display_file_name": item.get("display_file_name") or item.get("file_name") or "",
            "content_url": item.get("content_url") or item.get("relative_path") or "",
            "content_type": item.get("content_type") or "",
            "size": item.get("size") or 0,
            "inline": bool(item.get("inline")),
        }

    def _find_or_add_category(self, center, locale, source_id, name):
        category = next((c for c in center["categories"] if c["locale"] == locale and c["source_id"] == source_id), None)
        if not category:
            category = {"locale": locale, "source_id": source_id, "translation_key": f"category-{source_id}", "slug": _slug(name, source_id), "name": name, "description": "", "position": 900, "sections": []}
            center["categories"].append(category)
        return category

    def _append_manual(self, center, slug):
        for row in MANUAL_ARTICLES[slug]:
            category = self._find_or_add_category(center, row["locale"], row["category_id"], row["category"])
            section = next((s for s in category["sections"] if s["source_id"] == row["section_id"]), None)
            if not section:
                section = {"source_id": row["section_id"], "translation_key": f"section-{row['section_id']}", "slug": _slug(row["section"], row["section_id"]), "name": row["section"], "description": "", "position": 0, "articles": []}
                category["sections"].append(section)
            existing = next((a for a in section["articles"] if a["source_id"] == row["id"]), None)
            if existing:
                if not existing.get("body_html"):
                    existing["body_html"] = _clean_body(row["body"])
                existing["source_url"] = existing.get("source_url") or row.get("source_url", "")
                existing["source_updated_at"] = existing.get("source_updated_at") or row.get("source_updated_at")
                continue
            section["articles"].append({
                "source_id": row["id"], "translation_key": f"article-{row['id']}",
                "slug": _slug(row["title"], row["id"]), "title": row["title"],
                "body_html": _clean_body(row["body"]), "promoted": False, "position": 0,
                "source_url": row.get("source_url", ""),
                "source_updated_at": row.get("source_updated_at"), "attachments": [],
            })

    def _append_community(self, center, slug):
        for locale in ("es", "en"):
            cat_name = "Ideas y comunidad" if locale == "es" else "Ideas and community"
            section_name = "Participa y comparte" if locale == "es" else "Take part and share"
            category = self._find_or_add_category(center, locale, 900000000001, cat_name)
            section = {"source_id": 900000000002, "translation_key": "section-community", "slug": _slug(section_name, 900000000002), "name": section_name, "description": "", "position": 950, "articles": []}
            category["sections"].append(section)
            for row in COMMUNITY_ARTICLES[slug]:
                title, description = row[locale]
                section["articles"].append({"source_id": row["id"], "translation_key": f"community-{row['id']}", "slug": _slug(title, row["id"]), "title": title, "body_html": f"<p>{html.escape(description)}</p>", "promoted": False, "position": 0, "source_url": "", "source_updated_at": None})

    def _localize_assets(self, session, center, source_base):
        static_root = Path(settings.PROJECT_ROOT) / "resources" / "static" / "help_centers" / "content" / center["slug"]
        attr_re = re.compile(
            r'(?P<prefix>\b(?:src|href)=["\'])(?P<url>https?://[^"\']+|/hc/(?:[a-z]{2}(?:-[a-z]{2,3})?/)?article_attachments/[^"\']+)(?P<suffix>["\'])',
            re.I,
        )
        for category in center["categories"]:
            for section in category["sections"]:
                for article in section["articles"]:
                    def replace(match):
                        url = match.group("url")
                        parsed = urlparse(url)
                        is_attachment = "article_attachments" in parsed.path
                        is_image = parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
                        if not (is_attachment or is_image):
                            return match.group(0)
                        folder = static_root / str(article["source_id"])
                        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
                        cached = next((item for item in folder.glob(f"{digest}-*") if item.is_file() and item.stat().st_size), None)
                        if cached:
                            static_url = f"/static/help_centers/content/{center['slug']}/{article['source_id']}/{cached.name}"
                            return f"{match.group('prefix')}{static_url}{match.group('suffix')}"
                        try:
                            response = session.get(urljoin(source_base, url), timeout=60)
                            response.raise_for_status()
                        except requests.RequestException as exc:
                            self.stderr.write(f"No se pudo descargar {url}: {exc}")
                            return match.group(0)
                        filename = Path(unquote(urlparse(response.url).path)).name or Path(unquote(parsed.path)).name
                        if not Path(filename).suffix:
                            disposition = response.headers.get("Content-Disposition", "")
                            encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
                            plain = re.search(r'filename="?([^";]+)', disposition, re.I)
                            if encoded:
                                filename = unquote(encoded.group(1))
                            elif plain:
                                filename = plain.group(1)
                        suffix = Path(filename).suffix
                        if not suffix:
                            content_type = response.headers.get("Content-Type", "")
                            suffix = {
                                "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                                "application/msword": ".doc", "application/zip": ".zip",
                            }.get(content_type.split(";", 1)[0], ".bin")
                        stem = slugify(Path(filename).stem)[:70] or "asset"
                        filename = f"{digest}-{stem}{suffix.lower()}"
                        folder.mkdir(parents=True, exist_ok=True)
                        target = folder / filename
                        target.write_bytes(response.content)
                        static_url = f"/static/help_centers/content/{center['slug']}/{article['source_id']}/{filename}"
                        return f"{match.group('prefix')}{static_url}{match.group('suffix')}"
                    article["body_html"] = attr_re.sub(replace, article["body_html"])
                    # Algunos recursos historicos ya fueron eliminados de Zendesk.
                    # Los que no se pudieron localizar siguen apuntando al origen;
                    # retiramos esos enlaces para no publicar descargas o imagenes rotas.
                    article["body_html"] = re.sub(
                        r'<img\b[^>]*src=["\'](?:https?://[^"\']+)?/hc/(?:[^/]+/)?article_attachments/[^>]*>',
                        '', article["body_html"], flags=re.I,
                    )
                    article["body_html"] = re.sub(
                        r'<li>\s*<a\b[^>]*href=["\'](?:https?://[^"\']+)?/hc/(?:[^/]+/)?article_attachments/[^>]*>.*?</li>',
                        '', article["body_html"], flags=re.I | re.S,
                    )
                    article["body_html"] = re.sub(
                        r'<a\b[^>]*href=["\'](?:https?://[^"\']+)?/hc/(?:[^/]+/)?article_attachments/[^>]*>(.*?)</a>',
                        r'\1', article["body_html"], flags=re.I | re.S,
                    )
                    article["body_html"] = re.sub(
                        r'<div class="help-downloads">\s*<h2>[^<]+</h2>\s*<ul>\s*</ul>\s*</div>',
                        '', article["body_html"], flags=re.I | re.S,
                    )

    def _rewrite_article_links(self, center):
        mapping = {}
        for category in center["categories"]:
            for section in category["sections"]:
                for article in section["articles"]:
                    mapping[(category["locale"], str(article["source_id"]))] = article["slug"]
        pattern = re.compile(r'https?://[^/]+/hc/[^/]+/articles/(\d+)(?:-[^"\'#? ]*)?', re.I)
        for category in center["categories"]:
            for section in category["sections"]:
                for article in section["articles"]:
                    locale = category["locale"]
                    article["body_html"] = pattern.sub(lambda m: f"/help/{center['slug']}/{locale}/articles/{mapping.get((locale, m.group(1)), m.group(1))}/", article["body_html"])

    def _prune_unreferenced_assets(self, center):
        content_root = (
            Path(settings.PROJECT_ROOT) / "resources" / "static" / "help_centers" / "content"
        ).resolve()
        static_root = (content_root / center["slug"]).resolve()
        if static_root.parent != content_root or static_root.name != center["slug"]:
            raise CommandError(f"Ruta de recursos inesperada: {static_root}")
        if not static_root.exists():
            return

        prefix = f"/static/help_centers/content/{center['slug']}/"
        referenced = set()
        for category in center["categories"]:
            for section in category["sections"]:
                for article in section["articles"]:
                    for match in re.findall(rf'["\']{re.escape(prefix)}([^"\']+)', article["body_html"]):
                        target = (static_root / unquote(match)).resolve()
                        if target.is_relative_to(static_root):
                            referenced.add(target)

        removed = 0
        for path in static_root.rglob("*"):
            if path.is_file() and path.resolve() not in referenced:
                path.unlink()
                removed += 1
        for path in sorted((item for item in static_root.rglob("*") if item.is_dir()), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        if removed:
            self.stdout.write(f"Retirados {removed} recursos sin referencia de {center['slug']}.")

    @transaction.atomic
    def _import(self, data, prune=False, force_editorial_overrides=False):
        counts = {"centers": 0, "categories": 0, "sections": 0, "articles": 0}
        seen = {"categories": set(), "sections": set(), "articles": set()}
        for row in data.get("centers", []):
            source = SOURCES[row["slug"]]
            brand_name = getattr(settings, source["brand_setting"], "")
            brand = Brand.objects.filter(name__iexact=brand_name).first()
            center, _ = HelpCenter.objects.update_or_create(slug=row["slug"], defaults={
                "name": row["name"], "service": row["service"], "brand": brand, "domains": row["domains"],
                "default_locale": row["default_locale"], "supported_locales": row["supported_locales"],
                "tagline_es": row["tagline_es"], "tagline_en": row["tagline_en"],
                "primary_color": row["primary"], "accent_color": row["accent"], "active": True,
            })
            counts["centers"] += 1
            for cat_row in row["categories"]:
                category, _ = HelpCategory.objects.update_or_create(center=center, locale=cat_row["locale"], slug=cat_row["slug"], defaults={
                    "source_id": cat_row.get("source_id"), "translation_key": cat_row.get("translation_key", ""), "name": cat_row["name"],
                    "description": cat_row.get("description", ""), "position": cat_row.get("position", 0), "published": True,
                })
                seen["categories"].add(category.id); counts["categories"] += 1
                for sec_row in cat_row["sections"]:
                    section, _ = HelpSection.objects.update_or_create(category=category, slug=sec_row["slug"], defaults={
                        "source_id": sec_row.get("source_id"), "translation_key": sec_row.get("translation_key", ""), "name": sec_row["name"],
                        "description": sec_row.get("description", ""), "position": sec_row.get("position", 0), "published": True,
                    })
                    seen["sections"].add(section.id); counts["sections"] += 1
                    for art_row in sec_row["articles"]:
                        clean_html = _clean_body(art_row.get("body_html", ""))
                        defaults = {
                            "source_id": art_row.get("source_id"), "translation_key": art_row.get("translation_key", ""), "title": art_row["title"],
                            "body_html": clean_html, "body_text": html.unescape(strip_tags(clean_html)), "promoted": art_row.get("promoted", False),
                            "position": art_row.get("position", 0), "source_url": art_row.get("source_url", ""),
                            "source_updated_at": parse_datetime(art_row["source_updated_at"]) if art_row.get("source_updated_at") else None, "published": True,
                        }
                        source_id = art_row.get("source_id")
                        article = None
                        if source_id:
                            article = HelpArticle.objects.filter(
                                source_id=source_id,
                                section__category__center=center,
                                section__category__locale=cat_row["locale"],
                            ).first()
                        article = article or HelpArticle.objects.filter(section=section, slug=art_row["slug"]).first()
                        if article and (article.editorial_override or article.origin == HelpArticle.ORIGIN_AGENT) and not force_editorial_overrides:
                            seen["articles"].add(article.id); counts["articles"] += 1
                            continue

                        created = article is None
                        if created:
                            article = HelpArticle(section=section, slug=art_row["slug"], origin=HelpArticle.ORIGIN_ZENDESK)
                        before = (article.section_id, article.slug) + tuple(getattr(article, key, None) for key in defaults)
                        article.section = section
                        article.slug = art_row["slug"]
                        for key, value in defaults.items():
                            setattr(article, key, value)
                        article.origin = HelpArticle.ORIGIN_ZENDESK
                        article.editorial_override = False
                        after = (article.section_id, article.slug) + tuple(getattr(article, key, None) for key in defaults)
                        changed = created or before != after
                        if changed:
                            if not created:
                                article.version += 1
                            article.save()
                            HelpArticleRevision.objects.filter(
                                article=article, status=HelpArticleRevision.STATUS_PUBLISHED,
                            ).update(status=HelpArticleRevision.STATUS_SUPERSEDED)
                            revision = HelpArticleRevision.objects.create(
                                article=article,
                                number=(article.revisions.order_by("-number").values_list("number", flat=True).first() or 0) + 1,
                                section=article.section,
                                slug=article.slug,
                                title=article.title,
                                body_html=article.body_html,
                                body_text=article.body_text,
                                promoted=article.promoted,
                                position=article.position,
                                status=HelpArticleRevision.STATUS_PUBLISHED,
                                base_version=article.version,
                                change_note="Sincronización con Zendesk",
                                published_at=article.updated_at,
                            )
                            article.published_revision = revision
                            article.save(update_fields=["published_revision"])
                        seen["articles"].add(article.id); counts["articles"] += 1
        if prune:
            HelpArticle.objects.filter(
                origin=HelpArticle.ORIGIN_ZENDESK,
                editorial_override=False,
            ).exclude(id__in=seen["articles"]).update(published=False)
            HelpSection.objects.exclude(id__in=seen["sections"]).update(published=False)
            HelpCategory.objects.exclude(id__in=seen["categories"]).update(published=False)
        return counts
