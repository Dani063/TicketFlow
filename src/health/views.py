import logging

from django.http import JsonResponse

from health.runtime import BudgetSource, TerminationState, get_runtime

logger = logging.getLogger(__name__)


def livez(request):
    """
    Kubernetes liveness probe.

    Always returns HTTP 200 while the process is alive.
    A failing liveness probe causes Kubernetes to restart the pod — we never
    want that during a graceful shutdown, so this endpoint does NOT return 503
    when the pod is terminating.
    """
    return JsonResponse({"status": "alive"}, status=200)


def readyz(request):
    """
    Kubernetes readiness probe.

    Returns HTTP 200 when the pod is ready to receive traffic.
    Returns HTTP 503 once SIGTERM has been received so that Kubernetes removes
    the pod from the service endpoints before the forced SIGKILL arrives.

    Response body (JSON):
      status         – "ready" | "not ready"
      state          – TerminationState value
      remaining_ms   – usable milliseconds remaining (omitted when not terminating)
      deadline_utc   – ISO-8601 absolute deadline (omitted when not terminating)
      source         – where the deadline information came from
      is_fallback    – true when using the hardcoded 15-second fallback
    """
    try:
        runtime = get_runtime()
    except RuntimeError:
        # Runtime not initialised (e.g. unit-test environment without AppConfig).
        # Treat as healthy to avoid spurious readiness failures.
        logger.warning("readyz called but runtime is not initialised — returning 200")
        return JsonResponse({"status": "ready", "state": "NotTerminating"}, status=200)

    snapshot = runtime.get_snapshot()

    if snapshot.state == TerminationState.NOT_TERMINATING:
        return JsonResponse(
            {
                "status": "ready",
                "state": snapshot.state.value,
            },
            status=200,
        )

    body: dict = {
        "status": "not ready",
        "state": snapshot.state.value,
        "source": snapshot.source.value,
        "is_fallback": snapshot.is_fallback,
    }

    if snapshot.remaining_seconds != float("inf"):
        body["remaining_ms"] = max(round(snapshot.remaining_seconds * 1000), 0)

    if snapshot.deadline_utc is not None:
        body["deadline_utc"] = snapshot.deadline_utc.isoformat()

    return JsonResponse(body, status=503)
