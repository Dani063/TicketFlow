_HEALTH_PATHS = frozenset(["/livez", "/readyz"])


class HealthProbeHostMiddleware:
    """
    Patches the Host header to 'localhost' for health probe paths so that
    ALB and kubelet requests (which send the pod IP as Host) pass Django's
    ALLOWED_HOSTS check without exposing a wildcard entry.
    Must be first in MIDDLEWARE, before SecurityMiddleware calls request.get_host().
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in _HEALTH_PATHS:
            request.META["HTTP_HOST"] = "localhost"
        return self.get_response(request)
