import logging
import signal
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class HealthConfig(AppConfig):
    name = "health"
    verbose_name = "Kubernetes Health"

    def ready(self) -> None:
        """
        Called once per process when Django finishes loading.
        Initialises the TerminationBudgetRuntime and registers a SIGTERM handler
        so that the runtime is notified as soon as Kubernetes sends the shutdown signal.

        Configuration via settings.KUBERNETES_HEALTH (all keys optional):
          CRITICAL_THRESHOLD_SECONDS    – default 10
          CONFIGURABLE_MARGIN_SECONDS   – default 0
          CONFIGURED_GRACE_PERIOD_SECONDS – fallback grace period (None = use K8s or 15 s)
          ENABLE_KUBERNETES             – default True
          POLLING_INTERVAL_SECONDS      – default 2
        """
        from django.conf import settings
        import health.runtime as rt
        from health.runtime import TerminationBudgetRuntime

        opts: dict = getattr(settings, "KUBERNETES_HEALTH", {})

        rt._runtime = TerminationBudgetRuntime(
            critical_threshold_seconds=opts.get("CRITICAL_THRESHOLD_SECONDS", 10.0),
            configurable_margin_seconds=opts.get("CONFIGURABLE_MARGIN_SECONDS", 0.0),
            configured_grace_period_seconds=opts.get("CONFIGURED_GRACE_PERIOD_SECONDS"),
            enable_kubernetes=opts.get("ENABLE_KUBERNETES", True),
            polling_interval_seconds=opts.get("POLLING_INTERVAL_SECONDS", 2.0),
        )

        runtime_ref = rt._runtime

        def _sigterm_handler(signum: int, frame) -> None:
            logger.info("SIGTERM received — initiating termination budget tracking")
            # notify_shutdown_started does I/O (K8s API), so run off the signal stack
            threading.Thread(
                target=runtime_ref.notify_shutdown_started,
                daemon=True,
                name="termination-budget-init",
            ).start()

        registered: list[str] = []
        try:
            signal.signal(signal.SIGTERM, _sigterm_handler)
            registered.append("SIGTERM")

            # SIGQUIT is Linux/macOS only; Gunicorn sends it to workers for graceful shutdown.
            sigquit = getattr(signal, "SIGQUIT", None)
            if sigquit is not None:
                signal.signal(sigquit, _sigterm_handler)
                registered.append("SIGQUIT")

            logger.info(
                "Kubernetes termination budget runtime ready "
                "(%s handler(s) registered)",
                " + ".join(registered),
            )
        except (OSError, ValueError) as exc:
            # signal.signal() only works from the main thread.
            # Under some test runners or management commands this will fail — that's OK.
            logger.debug("Could not register signal handlers: %s", exc)
