"""Authenticated article-management views for the public help centres."""

import mimetypes
from functools import wraps
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST

from app.help_editor_forms import HelpArticleEditorForm
from app.models import HelpArticle, HelpArticleAsset, HelpArticleRevision, HelpCenter
from app.permissions import can_manage_help_content
from app.services.help_editor import (
    HelpAssetService,
    HelpAssetValidationError,
    HelpEditorConflict,
    create_article,
    is_public_help_host,
    publish_revision,
    restore_revision,
    save_draft,
    unpublish_article,
)
from app.services.help_content import sanitize_help_html


def help_editor_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "HELP_EDITOR_ENABLED", True) or is_public_help_host(request):
            raise Http404
        if not request.user.is_authenticated:
            target = f"{settings.LOGIN_URL}?{urlencode({'next': request.get_full_path()})}"
            return redirect(target)
        if not can_manage_help_content(request.user):
            return HttpResponseForbidden("No tienes permisos para gestionar el centro de ayuda.")
        return view(request, *args, **kwargs)
    return wrapped


def _article_or_404(article_id):
    return get_object_or_404(
        HelpArticle.objects.select_related("section__category__center", "published_revision"),
        pk=article_id,
    )


def _form_values(cleaned):
    return {
        "section": cleaned["section"],
        "slug": cleaned.get("slug") or "",
        "title": cleaned["title"],
        "body_html": cleaned["body_html"],
        "promoted": cleaned.get("promoted", False),
        "position": cleaned.get("position") or 0,
    }


def _editor_context(request, *, center, locale, article=None, revision=None, form=None, **extra):
    from app.public_views import _context
    context = _context(request, center, locale)
    context.update({
        "center": center,
        "locale": locale,
        "article": article,
        "revision": revision,
        "form": form,
        "assets": article.assets.all() if article else [],
        "live_url": reverse("help_article", args=[center.slug, locale, article.slug]) if article and article.published else "",
        "editor_body_html": sanitize_help_html(form["body_html"].value()) if form else "",
    })
    context.update(extra)
    return context


@help_editor_required
def article_new(request, center_slug, locale):
    center = get_object_or_404(HelpCenter, slug=center_slug, active=True)
    if not center.locale_supported(locale):
        raise Http404
    source = None
    source_id = request.GET.get("translate_from") or request.POST.get("translate_from")
    if source_id:
        source = HelpArticle.objects.filter(pk=source_id, section__category__center=center).select_related("section__category").first()

    initial = {
        "expected_version": 0,
        "position": 0,
    }
    section_id = request.GET.get("section")
    if section_id:
        initial["section"] = section_id
    if source:
        initial.update({"title": source.title, "body_html": source.body_html, "promoted": source.promoted})

    form = HelpArticleEditorForm(
        request.POST or None,
        center=center,
        locale=locale,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        translation_key = ""
        if source:
            translation_key = source.translation_key or f"manual-article-{source.id}"
            if not source.translation_key:
                source.translation_key = translation_key
                source.save(update_fields=["translation_key"])
        article, revision = create_article(
            **_form_values(form.cleaned_data),
            change_note=form.cleaned_data.get("change_note", ""),
            user=request.user,
            translation_key=translation_key,
        )
        if request.POST.get("intent") == "publish":
            article = publish_revision(article=article, revision=revision, user=request.user, expected_version=article.version)
            messages.success(request, "Artículo publicado correctamente.")
            return redirect("help_article", center_slug=center.slug, locale=locale, article_slug=article.slug)
        messages.success(request, "Borrador creado. Ya puedes previsualizarlo o adjuntar recursos.")
        return redirect("help_editor_article_edit", article_id=article.id)

    return render(request, "help_editor/article_form.html", _editor_context(
        request,
        center=center,
        locale=locale,
        form=form,
        source_article=source,
        is_new=True,
        translate_from=source_id or "",
    ))


@help_editor_required
def article_edit(request, article_id):
    article = _article_or_404(article_id)
    revision_id = request.GET.get("revision")
    revision = None
    if revision_id:
        revision = get_object_or_404(HelpArticleRevision, pk=revision_id, article=article)
    if not revision:
        revision = article.revisions.filter(status=HelpArticleRevision.STATUS_DRAFT).first()
    if not revision:
        revision = article.published_revision or article.revisions.first()

    initial = {
        "section": revision.section if revision else article.section,
        "title": revision.title if revision else article.title,
        "slug": revision.slug if revision else article.slug,
        "body_html": revision.body_html if revision else article.body_html,
        "promoted": revision.promoted if revision else article.promoted,
        "position": revision.position if revision else article.position,
        "expected_version": article.version,
    }
    form = HelpArticleEditorForm(
        request.POST or None,
        center=article.center,
        locale=article.locale,
        article=article,
        initial=initial,
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            article, revision = save_draft(
                article=article,
                values=_form_values(form.cleaned_data),
                user=request.user,
                expected_version=form.cleaned_data["expected_version"],
                note=form.cleaned_data.get("change_note", ""),
            )
            if request.POST.get("intent") == "publish":
                article = publish_revision(
                    article=article,
                    revision=revision,
                    user=request.user,
                    expected_version=article.version,
                )
                messages.success(request, "Cambios publicados correctamente.")
                return redirect("help_article", center_slug=article.center.slug, locale=article.locale, article_slug=article.slug)
        except HelpEditorConflict as exc:
            form.add_error(None, str(exc))
            status = 409
        else:
            messages.success(request, "Borrador guardado. La versión pública no ha cambiado.")
            return redirect(f"{reverse('help_editor_article_edit', args=[article.id])}?revision={revision.id}")

    return render(request, "help_editor/article_form.html", _editor_context(
        request,
        center=article.center,
        locale=article.locale,
        article=article,
        revision=revision,
        form=form,
        is_new=False,
    ), status=status)


@require_GET
@help_editor_required
def article_preview(request, article_id, revision_id):
    article = _article_or_404(article_id)
    revision = get_object_or_404(
        HelpArticleRevision.objects.select_related("section__category"),
        pk=revision_id,
        article=article,
    )
    preview_article = SimpleNamespace(
        id=article.id,
        section=revision.section,
        slug=revision.slug,
        title=revision.title,
        body_html=revision.body_html,
        source_updated_at=revision.created_at,
        published=article.published,
    )
    from app.public_views import _context
    context = _context(
        request,
        article.center,
        article.locale,
        article=preview_article,
        related=[],
        translation=None,
        editor_preview=True,
        preview_revision=revision,
        robots="noindex, nofollow",
    )
    return render(request, "help_center/article.html", context)


@require_GET
@help_editor_required
def article_history(request, article_id):
    article = _article_or_404(article_id)
    revisions = article.revisions.select_related("created_by", "section__category")
    return render(request, "help_editor/history.html", _editor_context(
        request,
        center=article.center,
        locale=article.locale,
        article=article,
        revisions=revisions,
    ))


@require_POST
@help_editor_required
def article_publish(request, article_id, revision_id):
    article = _article_or_404(article_id)
    revision = get_object_or_404(HelpArticleRevision, pk=revision_id, article=article)
    try:
        article = publish_revision(
            article=article,
            revision=revision,
            user=request.user,
            expected_version=int(request.POST.get("expected_version", -1)),
        )
    except HelpEditorConflict as exc:
        messages.error(request, str(exc))
        return redirect("help_editor_article_history", article_id=article.id)
    messages.success(request, f"Revisión {revision.number} publicada.")
    return redirect("help_article", center_slug=article.center.slug, locale=article.locale, article_slug=article.slug)


@require_POST
@help_editor_required
def article_restore(request, article_id, revision_id):
    article = _article_or_404(article_id)
    revision = get_object_or_404(HelpArticleRevision, pk=revision_id, article=article)
    try:
        article, draft = restore_revision(
            article=article,
            revision=revision,
            user=request.user,
            expected_version=int(request.POST.get("expected_version", -1)),
        )
    except HelpEditorConflict as exc:
        messages.error(request, str(exc))
        return redirect("help_editor_article_history", article_id=article.id)
    messages.success(request, f"La revisión {revision.number} se ha recuperado como un nuevo borrador.")
    return redirect(f"{reverse('help_editor_article_edit', args=[article.id])}?revision={draft.id}")


@require_POST
@help_editor_required
def article_unpublish(request, article_id):
    article = _article_or_404(article_id)
    try:
        unpublish_article(
            article=article,
            user=request.user,
            expected_version=int(request.POST.get("expected_version", -1)),
        )
    except HelpEditorConflict as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Artículo retirado de la ayuda pública. Su historial se conserva.")
    return redirect("help_editor_article_edit", article_id=article.id)


@require_POST
@help_editor_required
def article_asset_upload(request, article_id):
    article = _article_or_404(article_id)
    uploaded = request.FILES.get("asset")
    if not uploaded:
        return JsonResponse({"ok": False, "error": "Selecciona un archivo."}, status=400)
    try:
        asset = HelpAssetService.save(uploaded, article, request.user)
    except HelpAssetValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({
        "ok": True,
        "id": str(asset.id),
        "name": asset.original_name,
        "content_type": asset.content_type,
        "url": reverse("help_article_asset", args=[asset.id, asset.original_name]),
    })


@require_GET
def article_asset(request, asset_id, filename):
    asset = get_object_or_404(HelpArticleAsset.objects.select_related("article__section__category__center"), pk=asset_id)
    if not asset.article.published:
        if is_public_help_host(request) or not can_manage_help_content(request.user):
            raise Http404
    content_type = asset.content_type or mimetypes.guess_type(asset.original_name)[0] or "application/octet-stream"
    inline = content_type.startswith("image/") or content_type == "application/pdf"
    response = FileResponse(
        HelpAssetService.open(asset),
        as_attachment=not inline,
        filename=asset.original_name,
        content_type=content_type,
    )
    response["Cache-Control"] = "public, max-age=86400" if asset.article.published else "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
