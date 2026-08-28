"""Transactional help-centre editing, revisioning and asset storage."""

import hashlib
import logging
import mimetypes
import os
import re
import uuid

import boto3
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from app.models import HelpArticle, HelpArticleAsset, HelpArticleRevision
from app.services.help_content import help_plain_text, sanitize_help_html


logger = logging.getLogger(__name__)
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class HelpEditorConflict(Exception):
    pass


class HelpAssetValidationError(Exception):
    pass


def is_public_help_host(request):
    host = request.get_host().split(":", 1)[0].lower()
    from app.models import HelpCenter
    return any(
        host in {str(domain).lower() for domain in (center.domains or [])}
        for center in HelpCenter.objects.filter(active=True).only("domains")
    )


def next_article_slug(center, locale, title, requested="", exclude_article=None):
    root = slugify(requested or title, allow_unicode=False)[:200] or "articulo"
    candidate = root
    suffix = 2
    queryset = HelpArticle.objects.filter(
        section__category__center=center,
        section__category__locale=locale,
        slug=candidate,
    )
    if exclude_article:
        queryset = queryset.exclude(pk=exclude_article.pk)
    while queryset.exists():
        tail = f"-{suffix}"
        candidate = f"{root[:220-len(tail)]}{tail}"
        suffix += 1
        queryset = HelpArticle.objects.filter(
            section__category__center=center,
            section__category__locale=locale,
            slug=candidate,
        )
        if exclude_article:
            queryset = queryset.exclude(pk=exclude_article.pk)
    return candidate


def _revision_number(article):
    return (article.revisions.aggregate(value=Max("number"))["value"] or 0) + 1


def _snapshot(article, values, user, *, note="", status=HelpArticleRevision.STATUS_DRAFT, base_version=None):
    clean_html = sanitize_help_html(values.get("body_html", ""))
    return HelpArticleRevision.objects.create(
        article=article,
        number=_revision_number(article),
        section=values["section"],
        slug=values["slug"],
        title=values["title"].strip(),
        body_html=clean_html,
        body_text=help_plain_text(clean_html),
        promoted=bool(values.get("promoted")),
        position=values.get("position") or 0,
        status=status,
        base_version=article.version if base_version is None else base_version,
        change_note=(note or "")[:500],
        created_by=user,
        published_at=timezone.now() if status == HelpArticleRevision.STATUS_PUBLISHED else None,
    )


@transaction.atomic
def create_article(*, section, title, slug, body_html, promoted, position, change_note, user, translation_key=""):
    final_slug = next_article_slug(section.category.center, section.category.locale, title, slug)
    clean_html = sanitize_help_html(body_html)
    article = HelpArticle.objects.create(
        section=section,
        slug=final_slug,
        title=title.strip(),
        body_html=clean_html,
        body_text=help_plain_text(clean_html),
        promoted=promoted,
        position=position or 0,
        published=False,
        origin=HelpArticle.ORIGIN_AGENT,
        editorial_override=True,
        version=1,
        translation_key=translation_key,
        created_by=user,
        updated_by=user,
    )
    revision = _snapshot(
        article,
        {"section": section, "slug": final_slug, "title": title, "body_html": body_html, "promoted": promoted, "position": position},
        user,
        note=change_note or "Borrador inicial",
        base_version=0,
    )
    logger.info("help_article_created", extra={"article_id": article.id, "revision_id": revision.id, "actor_id": user.id})
    return article, revision


@transaction.atomic
def save_draft(*, article, values, user, expected_version, note=""):
    locked = HelpArticle.objects.select_for_update().select_related("section__category").get(pk=article.pk)
    if locked.version != expected_version:
        raise HelpEditorConflict("El artículo cambió mientras lo estabas editando.")
    values = dict(values)
    values["slug"] = next_article_slug(
        values["section"].category.center,
        values["section"].category.locale,
        values["title"],
        locked.slug if locked.published else values.get("slug"),
        exclude_article=locked,
    )
    revision = _snapshot(locked, values, user, note=note or "Borrador guardado", base_version=locked.version)
    locked.version += 1
    locked.updated_by = user
    locked.editorial_override = True
    locked.save(update_fields=["version", "updated_by", "editorial_override", "updated_at"])
    logger.info("help_article_draft_saved", extra={"article_id": locked.id, "revision_id": revision.id, "actor_id": user.id})
    return locked, revision


@transaction.atomic
def publish_revision(*, article, revision, user, expected_version):
    locked = HelpArticle.objects.select_for_update().get(pk=article.pk)
    revision = HelpArticleRevision.objects.select_related("section__category").get(pk=revision.pk, article=locked)
    if locked.version != expected_version:
        raise HelpEditorConflict("Hay una versión más reciente. Revísala antes de publicar.")
    if revision.section.category.center_id != locked.section.category.center_id:
        raise HelpEditorConflict("No se puede mover el artículo a otro centro de ayuda.")

    HelpArticleRevision.objects.filter(
        article=locked, status=HelpArticleRevision.STATUS_PUBLISHED,
    ).exclude(pk=revision.pk).update(status=HelpArticleRevision.STATUS_SUPERSEDED)
    revision.status = HelpArticleRevision.STATUS_PUBLISHED
    revision.published_at = timezone.now()
    revision.save(update_fields=["status", "published_at"])

    locked.section = revision.section
    locked.slug = revision.slug if not locked.published else locked.slug
    locked.title = revision.title
    locked.body_html = revision.body_html
    locked.body_text = revision.body_text
    locked.promoted = revision.promoted
    locked.position = revision.position
    locked.published = True
    locked.editorial_override = True
    locked.updated_by = user
    locked.published_revision = revision
    locked.version += 1
    locked.save()
    logger.info("help_article_published", extra={"article_id": locked.id, "revision_id": revision.id, "actor_id": user.id})
    return locked


@transaction.atomic
def restore_revision(*, article, revision, user, expected_version):
    locked = HelpArticle.objects.select_for_update().get(pk=article.pk)
    if locked.version != expected_version:
        raise HelpEditorConflict("El artículo cambió antes de poder restaurar la versión.")
    source = HelpArticleRevision.objects.get(pk=revision.pk, article=locked)
    draft = _snapshot(
        locked,
        {
            "section": source.section, "slug": locked.slug, "title": source.title,
            "body_html": source.body_html, "promoted": source.promoted, "position": source.position,
        },
        user,
        note=f"Restaurada desde la revisión {source.number}",
        base_version=locked.version,
    )
    locked.version += 1
    locked.editorial_override = True
    locked.updated_by = user
    locked.save(update_fields=["version", "editorial_override", "updated_by", "updated_at"])
    logger.info("help_article_revision_restored", extra={"article_id": locked.id, "revision_id": draft.id, "source_revision_id": source.id, "actor_id": user.id})
    return locked, draft


@transaction.atomic
def unpublish_article(*, article, user, expected_version):
    locked = HelpArticle.objects.select_for_update().get(pk=article.pk)
    if locked.version != expected_version:
        raise HelpEditorConflict("El artículo cambió antes de poder retirarlo.")
    HelpArticleRevision.objects.filter(
        article=locked, status=HelpArticleRevision.STATUS_PUBLISHED,
    ).update(status=HelpArticleRevision.STATUS_SUPERSEDED)
    locked.published = False
    locked.editorial_override = True
    locked.updated_by = user
    locked.version += 1
    locked.save(update_fields=["published", "editorial_override", "updated_by", "version", "updated_at"])
    _snapshot(
        locked,
        {"section": locked.section, "slug": locked.slug, "title": locked.title, "body_html": locked.body_html, "promoted": locked.promoted, "position": locked.position},
        user,
        note="Artículo retirado de la ayuda pública",
        base_version=locked.version - 1,
    )
    logger.info("help_article_unpublished", extra={"article_id": locked.id, "actor_id": user.id})
    return locked


class HelpAssetService:
    @staticmethod
    def safe_name(name):
        basename = os.path.basename((name or "archivo").replace("\\", "/"))
        stem, ext = os.path.splitext(basename)
        safe_stem = _SAFE_CHARS.sub("-", stem).strip(".-_")[:120] or "archivo"
        safe_ext = _SAFE_CHARS.sub("", ext.lower())[:12]
        return f"{safe_stem}{safe_ext}"

    @staticmethod
    def validate(uploaded):
        name = HelpAssetService.safe_name(uploaded.name)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        allowed = getattr(settings, "HELP_CONTENT_ALLOWED_EXTENSIONS", set())
        max_bytes = getattr(settings, "HELP_CONTENT_ASSET_MAX_BYTES", 20 * 1024 * 1024)
        if not ext or ext not in allowed:
            raise HelpAssetValidationError(f"El tipo de archivo .{ext or '?'} no está permitido.")
        if uploaded.size > max_bytes:
            raise HelpAssetValidationError(f"El archivo supera el límite de {max_bytes // (1024 * 1024)} MB.")
        return name

    @staticmethod
    def save(uploaded, article, user):
        name = HelpAssetService.validate(uploaded)
        digest = hashlib.sha256()
        for chunk in uploaded.chunks():
            digest.update(chunk)
        uploaded.seek(0)
        storage_key = f"help-content/{article.center.slug}/{article.id}/{uuid.uuid4().hex}-{name}"
        bucket = (getattr(settings, "HELP_CONTENT_S3_BUCKET", "") or "").strip()
        backend = "s3" if bucket else "local"
        # No se confía en el Content-Type enviado por el navegador: se deriva
        # de la extensión ya validada para impedir contenido activo disfrazado.
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if backend == "s3":
            client = boto3.client("s3", region_name=getattr(settings, "HELP_CONTENT_S3_REGION", None))
            client.upload_fileobj(uploaded, bucket, storage_key, ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"})
        else:
            storage_key = default_storage.save(storage_key, uploaded)
        return HelpArticleAsset.objects.create(
            article=article,
            original_name=name,
            storage_key=storage_key,
            storage_backend=backend,
            content_type=content_type,
            size=uploaded.size,
            checksum=digest.hexdigest(),
            uploaded_by=user,
        )

    @staticmethod
    def open(asset):
        if asset.storage_backend == "s3":
            client = boto3.client("s3", region_name=getattr(settings, "HELP_CONTENT_S3_REGION", None))
            response = client.get_object(Bucket=getattr(settings, "HELP_CONTENT_S3_BUCKET", ""), Key=asset.storage_key)
            return response["Body"]
        return default_storage.open(asset.storage_key, "rb")
