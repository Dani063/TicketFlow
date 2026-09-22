"""Recupera los articulos cuyo contenido era solo un video o unas imagenes."""

import html
import re

from django.db import migrations
from django.utils.html import strip_tags

from app.migration_data.help_content_snapshot_0052 import load_snapshot

EMBED_RE = re.compile(r"<(?:iframe|img|video)\b", re.I)
# El saneador retira la clase al importar, asi que el aviso se reconoce por el texto.
PLACEHOLDERS = {
    "El artículo original no contiene información adicional.",
    "The original article does not contain any additional information.",
}


def _is_placeholder(body):
    return "help-source-empty" in body or html.unescape(strip_tags(body)).strip() in PLACEHOLDERS


def restore_media_only_articles(apps, schema_editor):
    HelpArticle = apps.get_model("app", "HelpArticle")
    recovered = {}
    for center_row in load_snapshot().get("centers", []):
        for cat_row in center_row["categories"]:
            for sec_row in cat_row["sections"]:
                for art_row in sec_row["articles"]:
                    body = art_row.get("body_html", "")
                    if EMBED_RE.search(body):
                        key = (center_row["slug"], cat_row["locale"], art_row.get("source_id"))
                        recovered[key] = body

    candidates = HelpArticle.objects.filter(
        origin="zendesk", editorial_override=False, source_id__isnull=False,
    ).select_related("section__category__center")
    for article in candidates:
        if not _is_placeholder(article.body_html):
            continue
        category = article.section.category
        body = recovered.get((category.center.slug, category.locale, article.source_id))
        if not body:
            continue
        article.body_html = body
        article.body_text = html.unescape(strip_tags(body))
        article.save(update_fields=["body_html", "body_text"])


class Migration(migrations.Migration):
    dependencies = [("app", "0062_reporting_fact_tables")]
    operations = [migrations.RunPython(restore_media_only_articles, migrations.RunPython.noop)]
