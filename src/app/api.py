import json
from functools import wraps

from django.http import JsonResponse


class APIValidationError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def json_ok(data=None, status=200, **extra):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return JsonResponse(body, status=status)


def json_error(code, message, status=400, **extra):
    body = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    # Backward compatibility for the existing frontend, which often reads
    # data.error as a string.
    body["detail"] = message
    body.update(extra)
    return JsonResponse(body, status=status)


def parse_json_body(request):
    try:
        return json.loads(request.body or "{}")
    except (TypeError, ValueError):
        raise APIValidationError("invalid_json", "JSON invalido", status=400)


def api_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return json_error("not_authenticated", "Authentication required", status=401)
        if getattr(request.user, "is_active", True) is False:
            return json_error("inactive_user", "User account is inactive", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


def api_permission_required(predicate, code="forbidden", message="Forbidden"):
    def _decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return json_error("not_authenticated", "Authentication required", status=401)
            if not predicate(request.user):
                return json_error(code, message, status=403)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return _decorator
