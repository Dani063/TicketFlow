import re

from django.http import HttpResponseRedirect
from django.core.cache import cache


class PublicHelpHostMiddleware:
    """Mantiene los dominios de soporte aislados de la aplicacion interna.

    Los agentes trabajan en el dominio de TicketFlow. En support.ecomfax.com y
    support.recordia.net solo se exponen ayuda, formulario y encuesta publica.
    """

    PUBLIC_PREFIXES = (
        "/help/", "/hc/", "/satisfaction/", "/static/", "/robots.txt",
        "/sitemap.xml", "/livez", "/readyz", "/healthz",
    )
    HELP_CENTER_RE = re.compile(r"^/help/([^/]+)/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            from app.models import HelpCenter
            host = request.get_host().split(":", 1)[0].lower()
            cache_key = f"public-help-host:{host}"
            center_slug = cache.get(cache_key)
            if center_slug is None:
                center_slug = next((item.slug for item in HelpCenter.objects.filter(active=True) if host in [d.lower() for d in (item.domains or [])]), "-")
                cache.set(cache_key, center_slug, 60)
            center = HelpCenter.objects.filter(active=True, slug=center_slug).first() if center_slug != "-" else None
        except Exception:
            center = None
        if center:
            home = f"/help/{center.slug}/{center.default_locale}/"
            if request.path == "/":
                return HttpResponseRedirect(home)
            match = self.HELP_CENTER_RE.match(request.path)
            if match and match.group(1) != center.slug:
                return HttpResponseRedirect(home)
            if not request.path.startswith(self.PUBLIC_PREFIXES):
                return HttpResponseRedirect(home)
        return self.get_response(request)
