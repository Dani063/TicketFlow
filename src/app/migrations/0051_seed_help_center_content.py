import html

from django.conf import settings
from django.db import migrations
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags

from app.migration_data.help_content_snapshot_0052 import load_snapshot


def seed_help_centers(apps, schema_editor):
    HelpCenter = apps.get_model("app", "HelpCenter")
    HelpCategory = apps.get_model("app", "HelpCategory")
    HelpSection = apps.get_model("app", "HelpSection")
    HelpArticle = apps.get_model("app", "HelpArticle")
    Brand = apps.get_model("app", "Brand")
    data = load_snapshot()
    brand_defaults = {
        "ecomfax": getattr(settings, "PUBLIC_ECOMFAX_BRAND_NAME", "eComFax"),
        "recordia": getattr(settings, "PUBLIC_RECORDIA_BRAND_NAME", "Comunycarse Helpdesk"),
    }
    for row in data.get("centers", []):
        brand = Brand.objects.filter(name__iexact=brand_defaults.get(row["slug"], "")).first()
        center, _ = HelpCenter.objects.update_or_create(slug=row["slug"], defaults={
            "name": row["name"], "service": row["service"], "brand": brand,
            "domains": row["domains"], "default_locale": row["default_locale"],
            "supported_locales": row["supported_locales"], "tagline_es": row["tagline_es"],
            "tagline_en": row["tagline_en"], "primary_color": row["primary"],
            "accent_color": row["accent"], "active": True,
        })
        for cat_row in row["categories"]:
            category, _ = HelpCategory.objects.update_or_create(
                center=center, locale=cat_row["locale"], slug=cat_row["slug"],
                defaults={"source_id": cat_row.get("source_id"), "translation_key": cat_row.get("translation_key", ""),
                          "name": cat_row["name"], "description": cat_row.get("description", ""),
                          "position": cat_row.get("position", 0), "published": True},
            )
            for sec_row in cat_row["sections"]:
                section, _ = HelpSection.objects.update_or_create(
                    category=category, slug=sec_row["slug"],
                    defaults={"source_id": sec_row.get("source_id"), "translation_key": sec_row.get("translation_key", ""),
                              "name": sec_row["name"], "description": sec_row.get("description", ""),
                              "position": sec_row.get("position", 0), "published": True},
                )
                for art_row in sec_row["articles"]:
                    body = art_row.get("body_html", "")
                    HelpArticle.objects.update_or_create(
                        section=section, slug=art_row["slug"],
                        defaults={"source_id": art_row.get("source_id"), "translation_key": art_row.get("translation_key", ""),
                                  "title": art_row["title"], "body_html": body,
                                  "body_text": html.unescape(strip_tags(body)), "promoted": art_row.get("promoted", False),
                                  "position": art_row.get("position", 0), "source_url": art_row.get("source_url", ""),
                                  "source_updated_at": parse_datetime(art_row["source_updated_at"]) if art_row.get("source_updated_at") else None,
                                  "published": True},
                    )


def unseed_help_centers(apps, schema_editor):
    HelpCenter = apps.get_model("app", "HelpCenter")
    HelpCenter.objects.filter(slug__in=["ecomfax", "recordia"]).delete()


class Migration(migrations.Migration):
    dependencies = [("app", "0050_public_help_centers")]
    operations = [migrations.RunPython(seed_help_centers, unseed_help_centers)]
