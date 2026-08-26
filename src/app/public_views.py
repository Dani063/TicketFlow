import mimetypes

from django.conf import settings
from django.core import signing
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from app.api import APIValidationError
from app.models import Attachment, HelpArticle, HelpCategory, HelpCenter, HelpSection
from app.permissions import can_view_ticket
from app.public_forms import COPY as FORM_COPY, PublicTicketForm
from app.services.attachments import AttachmentService
from app.services.help_centers import (
    PublicTicketIntakeService,
    center_for_request,
    normalize_help_locale,
    public_base_url,
)


UI = {
    "es": {
        "help_center": "Centro de ayuda", "search": "Buscar en la ayuda",
        "search_placeholder": "¿En qué podemos ayudarte?", "submit_request": "Enviar una solicitud",
        "categories": "Explora por categoría", "promoted": "Artículos destacados", "all_articles": "Todos los artículos",
        "results": "Resultados de búsqueda", "no_results": "No hemos encontrado resultados para tu búsqueda.",
        "try_request": "Si no encuentras la respuesta, envíanos una solicitud.", "home": "Inicio",
        "related": "Artículos relacionados", "request_title": "Contacta con soporte",
        "request_intro": "Describe tu duda o incidencia. Recibirás la confirmación por correo y podrás responder a ese mensaje para aportar más información.",
        "attachments_help": "Hasta 5 archivos de 15 MB. PDF, imágenes, Office, CSV, TXT, ZIP o LOG.",
        "send": "Enviar solicitud", "sending": "Enviando...", "success_title": "Hemos recibido tu solicitud",
        "success_body": "Nuestro equipo la revisará lo antes posible. Te hemos enviado un correo de confirmación; puedes responder a ese mensaje para añadir información.",
        "reference": "Tu referencia", "back_help": "Volver al centro de ayuda", "language": "Idioma",
        "choose_center": "Elige el producto sobre el que necesitas ayuda", "updated": "Actualizado",
    },
    "en": {
        "help_center": "Help centre", "search": "Search help", "search_placeholder": "How can we help?",
        "submit_request": "Submit a request", "categories": "Browse by category", "promoted": "Featured articles",
        "all_articles": "All articles", "results": "Search results", "no_results": "We couldn't find any results for your search.",
        "try_request": "If you cannot find the answer, send us a request.", "home": "Home",
        "related": "Related articles", "request_title": "Contact support",
        "request_intro": "Describe your question or issue. You will receive confirmation by email and can reply to that message with more information.",
        "attachments_help": "Up to 5 files of 15 MB. PDF, images, Office, CSV, TXT, ZIP or LOG.",
        "send": "Submit request", "sending": "Sending...", "success_title": "We've received your request",
        "success_body": "Our team will review it as soon as possible. We sent you a confirmation email; reply to it if you need to add information.",
        "reference": "Your reference", "back_help": "Back to help centre", "language": "Language",
        "choose_center": "Choose the product you need help with", "updated": "Updated",
    },
}

PUBLIC_ERROR_EN = {
    "too_many_files": "You can attach up to five files.",
    "file_type_not_allowed": "One of the selected file types is not allowed.",
    "file_too_large": "Each attachment must be no larger than 15 MB.",
    "rate_limited": "You have submitted too many requests. Wait a few minutes before trying again.",
    "portal_brand_missing": "This support portal is temporarily unavailable. Please try again later.",
}


def _center_or_404(request, center_slug):
    if not getattr(settings, "PUBLIC_HELP_ENABLED", True):
        raise Http404
    center = center_for_request(request, center_slug)
    if not center:
        raise Http404
    return center


def _client_ip(request):
    header = getattr(settings, "PUBLIC_CLIENT_IP_HEADER", "REMOTE_ADDR")
    raw = (request.META.get(header, "") or "").strip()
    if header == "HTTP_X_FORWARDED_FOR" and "," in raw:
        raw = raw.split(",")[-1].strip()
    return raw or request.META.get("REMOTE_ADDR", "")


def _context(request, center, locale, **extra):
    locale = normalize_help_locale(locale, center)
    path = request.path
    canonical = request.build_absolute_uri(path)
    base_url = public_base_url(center)
    if base_url:
        canonical = f"{base_url}{path}"
    context = {
        "center": center,
        "locale": locale,
        "ui": UI[locale],
        "canonical_url": canonical,
        "other_locale": "en" if locale == "es" else "es",
        "locale_switch_url": reverse("help_home", args=[center.slug, "en" if locale == "es" else "es"]),
        "brand_style": f"--brand-primary:{center.primary_color};--brand-accent:{center.accent_color}",
    }
    context.update(extra)
    return context


def help_hub(request):
    center = center_for_request(request)
    if center:
        return redirect("help_home", center_slug=center.slug, locale=center.default_locale)
    centers = HelpCenter.objects.filter(active=True).order_by("name")
    return render(request, "help_center/hub.html", {"centers": centers})


def help_home(request, center_slug, locale):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    categories = (
        HelpCategory.objects.filter(center=center, locale=locale, published=True)
        .prefetch_related("sections__articles")
        .annotate(article_count=Count("sections__articles", filter=Q(sections__published=True, sections__articles__published=True)))
    )
    promoted = HelpArticle.objects.filter(
        section__category__center=center, section__category__locale=locale,
        section__category__published=True, section__published=True, published=True, promoted=True,
    ).select_related("section")[:8]
    return render(request, "help_center/home.html", _context(
        request, center, locale, categories=categories, promoted=promoted,
    ))


def help_category(request, center_slug, locale, category_slug):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    category = get_object_or_404(
        HelpCategory.objects.prefetch_related("sections__articles"),
        center=center, locale=locale, slug=category_slug, published=True,
    )
    return render(request, "help_center/category.html", _context(request, center, locale, category=category))


def help_section(request, center_slug, locale, section_slug):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    section = get_object_or_404(
        HelpSection.objects.select_related("category").prefetch_related("articles"),
        category__center=center, category__locale=locale, category__published=True,
        slug=section_slug, published=True,
    )
    return render(request, "help_center/section.html", _context(request, center, locale, section=section))


def help_article(request, center_slug, locale, article_slug):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    article = get_object_or_404(
        HelpArticle.objects.select_related("section__category"),
        section__category__center=center, section__category__locale=locale,
        section__category__published=True, section__published=True, slug=article_slug, published=True,
    )
    related = article.section.articles.filter(published=True).exclude(id=article.id)[:5]
    translation = None
    if article.translation_key:
        translation = HelpArticle.objects.filter(
            translation_key=article.translation_key,
            section__category__center=center,
            section__category__locale=("en" if locale == "es" else "es"),
            published=True,
        ).first()
    context = _context(request, center, locale, article=article, related=related, translation=translation)
    if translation:
        context["locale_switch_url"] = reverse("help_article", args=[center.slug, translation.locale, translation.slug])
    return render(request, "help_center/article.html", context)


def help_search(request, center_slug, locale):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    query = (request.GET.get("q") or "").strip()[:200]
    results = HelpArticle.objects.none()
    if query:
        words = [word for word in query.split() if len(word) > 1][:8]
        clause = Q()
        for word in words:
            clause &= (Q(title__icontains=word) | Q(body_text__icontains=word))
        results = HelpArticle.objects.filter(
            clause, section__category__center=center, section__category__locale=locale,
            section__category__published=True, section__published=True, published=True,
        ).select_related("section")[:50]
    return render(request, "help_center/search.html", _context(
        request, center, locale, query=query, results=results,
        canonical_url=request.build_absolute_uri(request.path), robots="noindex, follow",
    ))


@ensure_csrf_cookie
def help_request(request, center_slug, locale):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    if not getattr(settings, "PUBLIC_TICKET_INTAKE_ENABLED", True):
        raise Http404

    form = PublicTicketForm(request.POST or None, locale=locale, initial={"started": PublicTicketForm.new_started_token()})
    form_error = None
    if request.method == "POST" and form.is_valid():
        try:
            data = dict(form.cleaned_data, locale=locale)
            ticket = PublicTicketIntakeService.create(
                center, data, request.FILES.getlist("attachments"), _client_ip(request),
            )
        except APIValidationError as exc:
            form_error = PUBLIC_ERROR_EN.get(exc.code, "We could not submit your request. Please try again.") if locale == "en" else exc.message
        else:
            proof = signing.dumps({"ticket": ticket.id, "center": center.slug}, salt="public-ticket-success")
            return redirect("help_request_success", center_slug=center.slug, locale=locale, proof=proof)
    return render(request, "help_center/request.html", _context(
        request, center, locale, form=form, form_copy=FORM_COPY[locale], form_error=form_error,
        robots="noindex, nofollow",
    ), status=429 if form_error and ("demasiadas" in form_error or "too many" in form_error) else 200)


def help_request_success(request, center_slug, locale, proof):
    center = _center_or_404(request, center_slug)
    locale = normalize_help_locale(locale, center)
    try:
        payload = signing.loads(proof, salt="public-ticket-success", max_age=86400)
    except (signing.BadSignature, signing.SignatureExpired):
        raise Http404
    if payload.get("center") != center.slug:
        raise Http404
    return render(request, "help_center/success.html", _context(
        request, center, locale, ticket_id=payload.get("ticket"), robots="noindex, nofollow",
    ))


def legacy_help_article(request, locale, source_id, legacy_slug=""):
    center = center_for_request(request)
    qs = HelpArticle.objects.filter(source_id=source_id, published=True).select_related("section__category__center")
    if center:
        qs = qs.filter(section__category__center=center)
    article = qs.filter(section__category__locale=normalize_help_locale(locale, center)).first() or qs.first()
    if not article:
        raise Http404
    return redirect("help_article", center_slug=article.center.slug, locale=article.locale, article_slug=article.slug, permanent=True)


@require_GET
def help_sitemap(request):
    center = center_for_request(request)
    centers = [center] if center else list(HelpCenter.objects.filter(active=True))
    urls = []
    for item in centers:
        for locale in item.supported_locales or [item.default_locale]:
            urls.append(request.build_absolute_uri(reverse("help_home", args=[item.slug, locale])))
        for article in HelpArticle.objects.filter(section__category__center=item, published=True).select_related("section__category"):
            urls.append(request.build_absolute_uri(reverse("help_article", args=[item.slug, article.locale, article.slug])))
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(
        f"<url><loc>{url}</loc></url>" for url in urls
    ) + "</urlset>"
    return HttpResponse(body, content_type="application/xml")


@require_GET
def help_robots(request):
    return HttpResponse(f"User-agent: *\nAllow: /help/\nDisallow: /satisfaction/\nSitemap: {request.build_absolute_uri('/sitemap.xml')}\n", content_type="text/plain")


@require_GET
def attachment_download(request, attachment_id):
    attachment = get_object_or_404(Attachment.objects.select_related("ticket"), id=attachment_id)
    if not attachment.ticket_id or not can_view_ticket(request.user, attachment.ticket):
        raise Http404
    if attachment.storage_backend == "s3":
        return redirect(AttachmentService.presigned_url(attachment))
    if not attachment.storage_key:
        # Adjuntos heredados conservan la URL original.
        return redirect(attachment.file_url)
    content_type = attachment.file_type or mimetypes.guess_type(attachment.original_name)[0] or "application/octet-stream"
    return FileResponse(
        AttachmentService.open_local(attachment), as_attachment=True,
        filename=attachment.original_name or "attachment", content_type=content_type,
    )
