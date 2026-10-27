"""Custom template context processors."""

from django.conf import settings

from app.permissions import is_agent, is_readonly


def fragment_base(request):
    """
    Inject `_base` into every template so pages can extend either `base.html`
    (full page) or `base_fragment.html` (just the content block) depending on
    whether the request was made with ?fragment=1.

    Usage in templates:
        {% extends _base %}
    instead of:
        {% extends 'base.html' %}

    When tabs.js fetches a sidebar page via fragment=1 to swap into
    #app-content, the response contains only the block content — no
    navbar/sidebar/scripts — so the existing shell stays alive.
    """
    is_fragment = request.GET.get('fragment') == '1'
    return {
        'base_template': 'base_fragment.html' if is_fragment else 'base.html',
        'app_version': settings.TICKETFLOW_VERSION,
    }


def user_permissions(request):
    """Permisos para las plantillas, en vez de comparar nombres de rol.

    Comparar el nombre dejaba ver los botones de agente a cualquier rol que no
    fuera de cliente, por ejemplo el de solo lectura (#21378).
    """
    user = getattr(request, 'user', None)
    return {
        'tf_can_edit': is_agent(user),
        'tf_readonly': is_readonly(user),
    }
