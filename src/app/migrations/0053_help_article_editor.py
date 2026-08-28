"""Add the revisioned help-centre editor and private article assets."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_published_revisions(apps, schema_editor):
    HelpArticle = apps.get_model("app", "HelpArticle")
    HelpArticleRevision = apps.get_model("app", "HelpArticleRevision")

    for article in HelpArticle.objects.select_related("section").iterator():
        revision = HelpArticleRevision.objects.create(
            article_id=article.id,
            number=1,
            section_id=article.section_id,
            slug=article.slug,
            title=article.title,
            body_html=article.body_html,
            body_text=article.body_text,
            promoted=article.promoted,
            position=article.position,
            status="published" if article.published else "draft",
            base_version=1,
            change_note="Versión inicial migrada",
            published_at=article.updated_at if article.published else None,
        )
        if article.published:
            HelpArticle.objects.filter(pk=article.pk).update(published_revision_id=revision.pk)


def remove_backfilled_revisions(apps, schema_editor):
    HelpArticle = apps.get_model("app", "HelpArticle")
    HelpArticle.objects.update(published_revision_id=None)
    apps.get_model("app", "HelpArticleRevision").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("app", "0052_refresh_help_center_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="helparticle",
            name="created_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_help_articles", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="helparticle",
            name="editorial_override",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="helparticle",
            name="origin",
            field=models.CharField(choices=[("zendesk", "Zendesk"), ("agent", "Agente"), ("system", "Sistema")], db_index=True, default="zendesk", max_length=20),
        ),
        migrations.AddField(
            model_name="helparticle",
            name="updated_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_help_articles", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="helparticle",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name="HelpArticleRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveIntegerField()),
                ("slug", models.SlugField(max_length=220)),
                ("title", models.CharField(max_length=500)),
                ("body_html", models.TextField(blank=True, default="")),
                ("body_text", models.TextField(blank=True, default="")),
                ("promoted", models.BooleanField(default=False)),
                ("position", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("draft", "Borrador"), ("published", "Publicada"), ("superseded", "Sustituida")], db_index=True, default="draft", max_length=20)),
                ("base_version", models.PositiveIntegerField(default=1)),
                ("change_note", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="app.helparticle")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="help_article_revisions", to=settings.AUTH_USER_MODEL)),
                ("section", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="article_revisions", to="app.helpsection")),
            ],
            options={
                "ordering": ["-number"],
                "indexes": [models.Index(fields=["article", "status"], name="help_revision_status_idx")],
                "constraints": [models.UniqueConstraint(fields=("article", "number"), name="help_article_revision_number_uniq")],
            },
        ),
        migrations.AddField(
            model_name="helparticle",
            name="published_revision",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_for_articles", to="app.helparticlerevision"),
        ),
        migrations.CreateModel(
            name="HelpArticleAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("original_name", models.CharField(max_length=255)),
                ("storage_key", models.CharField(max_length=1000)),
                ("storage_backend", models.CharField(default="local", max_length=20)),
                ("content_type", models.CharField(blank=True, default="", max_length=255)),
                ("size", models.PositiveBigIntegerField(default=0)),
                ("checksum", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assets", to="app.helparticle")),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="help_article_assets", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.RunPython(backfill_published_revisions, remove_backfilled_revisions),
    ]
