import logging

from django.conf import settings
from django.db import connection
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


def _inbound_status():
    """Salud de la recogida de correo por buzón, para vigilarla desde fuera.

    ``ok`` es falso si un cursor activo lleva más de INBOUND_CURSOR_STALE_MINUTES
    sin una vuelta buena, si hay mensajes en cuarentena o si un pendiente supera
    INBOUND_SWEEP_PENDING_ALERT_MINUTES. No cambia el código HTTP: internalz no
    debe sacar el pod de servicio por un problema del buzón.
    """
    from django.db.models import Count, Min
    from django.utils import timezone

    try:
        from app.models import InboundEmailLog, MailboxSyncCursor

        now = timezone.now()
        stale_minutes = getattr(settings, "INBOUND_CURSOR_STALE_MINUTES", 5)
        pending_alert = getattr(settings, "INBOUND_SWEEP_PENDING_ALERT_MINUTES", 10)
        open_statuses = (InboundEmailLog.STATUS_PENDING, InboundEmailLog.STATUS_PROCESSING,
                         InboundEmailLog.STATUS_FAILED)
        mailboxes = []
        healthy = True
        for cursor in MailboxSyncCursor.objects.filter(environment=getattr(settings, "ENVIRONMENT_NAME", "local")):
            counts = dict(
                InboundEmailLog.objects.filter(folder_key=cursor.folder_key)
                .filter(status__in=open_statuses + (InboundEmailLog.STATUS_QUARANTINED,))
                .values_list("status").annotate(n=Count("id"))
            )
            oldest = (
                InboundEmailLog.objects.filter(folder_key=cursor.folder_key, status__in=open_statuses)
                .aggregate(oldest=Min("graph_received_at"))["oldest"]
            )
            age = int((now - cursor.last_sweep_ok_at).total_seconds()) if cursor.last_sweep_ok_at else None
            oldest_minutes = int((now - oldest).total_seconds() // 60) if oldest else None
            active = cursor.ingest_mode == MailboxSyncCursor.MODE_SWEEP
            ok = (
                active
                and age is not None and age < stale_minutes * 60
                and not counts.get(InboundEmailLog.STATUS_QUARANTINED)
                and (oldest_minutes is None or oldest_minutes < pending_alert)
            )
            healthy = healthy and ok
            mailboxes.append({
                "mailbox": cursor.mailbox,
                "mode": cursor.ingest_mode,
                "ok": ok,
                "last_sweep_ok_age_seconds": age,
                "swept_until": cursor.swept_until.isoformat() if cursor.swept_until else None,
                "pending": counts.get(InboundEmailLog.STATUS_PENDING, 0),
                "processing": counts.get(InboundEmailLog.STATUS_PROCESSING, 0),
                "failed": counts.get(InboundEmailLog.STATUS_FAILED, 0),
                "quarantined": counts.get(InboundEmailLog.STATUS_QUARANTINED, 0),
                "oldest_pending_minutes": oldest_minutes,
            })
        return {"ok": healthy and bool(mailboxes), "mailboxes": mailboxes}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def internalz(request):
    checks = {}
    status = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
        status = 503

    broker_url = getattr(settings, "CELERY_BROKER_URL", "")
    checks["celery"] = {
        "ok": bool(broker_url),
        "broker": broker_url.split("://", 1)[0] if broker_url else "",
        "default_queue": getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", ""),
    }
    if not broker_url:
        status = 503

    try:
        from app.models import InboundEmailLog, OperationalMetric

        last_email = InboundEmailLog.objects.order_by("-received_at").first()
        last_metric = OperationalMetric.objects.order_by("-recorded_at").first()
        checks["operations"] = {
            "ok": True,
            "last_inbound_email_at": last_email.received_at.isoformat() if last_email else None,
            "last_inbound_email_status": last_email.status if last_email else None,
            "last_metric": last_metric.name if last_metric else None,
            "last_metric_at": last_metric.recorded_at.isoformat() if last_metric else None,
        }
    except Exception as exc:
        checks["operations"] = {"ok": False, "error": str(exc)}

    checks["inbound"] = _inbound_status()

    return JsonResponse({"status": "ok" if status == 200 else "degraded", "checks": checks}, status=status)
