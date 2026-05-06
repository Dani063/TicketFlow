"""
Kubernetes Termination Budget Runtime.

Tracks how much usable time remains between SIGTERM and the forced SIGKILL,
mirroring the .NET Recordia.Lib.KubernetesTerminationBudget library.

States:
  NOT_TERMINATING  – pod has not received SIGTERM yet
  TERMINATING      – shutdown started, time > critical threshold
  CRITICAL         – time <= critical threshold but > 0
  EXPIRED          – usable time exhausted; budget_expired_event is set
  UNKNOWN          – shutdown started but no deadline could be determined

Sources (priority order when computing deadline):
  DELETION_TIMESTAMP      – K8s API returned metadata.deletionTimestamp
  CONFIGURED_GRACE_PERIOD – caller configured a fallback grace period
  HARDCODED_FALLBACK      – neither K8s nor config available → 15 s
"""

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

HARDCODED_FALLBACK_SECONDS: float = 15.0
INTERNAL_MARGIN_SECONDS: float = 1.0

_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


# ── Enums and data classes ────────────────────────────────────────────────────

class TerminationState(str, Enum):
    NOT_TERMINATING = "NotTerminating"
    TERMINATING = "Terminating"
    CRITICAL = "Critical"
    EXPIRED = "Expired"
    UNKNOWN = "Unknown"


class BudgetSource(str, Enum):
    NONE = "None"
    DELETION_TIMESTAMP = "DeletionTimestamp"
    CONFIGURED_GRACE_PERIOD = "ConfiguredGracePeriod"
    HARDCODED_FALLBACK = "HardcodedFallback"


@dataclass(frozen=True)
class TerminationSnapshot:
    state: TerminationState
    remaining_seconds: float          # usable seconds (margins applied); inf when NOT_TERMINATING
    observed_at_utc: datetime
    deadline_utc: Optional[datetime]  # raw deadline (no margins); None before shutdown
    grace_period_seconds: Optional[float]
    source: BudgetSource
    is_fallback: bool


# ── Internal observation data ─────────────────────────────────────────────────

class _Obs:
    """Mutable container for a single observation result (avoids dataclass overhead)."""
    __slots__ = ("deadline_utc", "grace_period_seconds", "source", "is_fallback")

    def __init__(
        self,
        deadline_utc: Optional[datetime],
        grace_period_seconds: Optional[float],
        source: BudgetSource,
        is_fallback: bool,
    ) -> None:
        self.deadline_utc = deadline_utc
        self.grace_period_seconds = grace_period_seconds
        self.source = source
        self.is_fallback = is_fallback


# ── Runtime ───────────────────────────────────────────────────────────────────

class TerminationBudgetRuntime:
    """
    Core runtime that manages the termination budget lifecycle.

    Call notify_shutdown_started() when SIGTERM is received.
    Inspect get_snapshot() or wait on budget_expired_event to react.
    """

    def __init__(
        self,
        critical_threshold_seconds: float = 10.0,
        configurable_margin_seconds: float = 0.0,
        configured_grace_period_seconds: Optional[float] = None,
        enable_kubernetes: bool = True,
        polling_interval_seconds: float = 2.0,
    ) -> None:
        self._critical_threshold = critical_threshold_seconds
        self._configurable_margin = configurable_margin_seconds
        self._configured_grace_period = configured_grace_period_seconds
        self._enable_kubernetes = enable_kubernetes
        self._polling_interval = polling_interval_seconds

        # State — all writes protected by _lock
        self._lock = threading.Lock()
        self._shutdown_detected = False
        self._last_k8s_obs: Optional[_Obs] = None   # latest successful K8s observation
        self._fallback_deadline: Optional[datetime] = None
        self._token_canceled = False

        # Signals budget exhaustion to waiting threads
        self.budget_expired_event = threading.Event()

        # Background threads / timers
        self._stop_polling = threading.Event()
        self._polling_thread: Optional[threading.Thread] = None
        self._expiry_timer: Optional[threading.Timer] = None
        self._expiry_timer_lock = threading.Lock()
        self._disposed = False

        # K8s coordinates (read-only after __init__)
        self._pod_name = os.environ.get("POD_NAME")
        self._pod_namespace = os.environ.get("POD_NAMESPACE")
        self._k8s_host = os.environ.get("KUBERNETES_SERVICE_HOST")
        self._k8s_port = os.environ.get("KUBERNETES_SERVICE_PORT")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_snapshot(self) -> TerminationSnapshot:
        """Thread-safe snapshot of current termination budget state."""
        with self._lock:
            return self._build_snapshot()

    def notify_shutdown_started(self) -> None:
        """
        Call once when SIGTERM is received.
        Queries K8s (best-effort), starts polling, and schedules the expiry timer.
        """
        with self._lock:
            if self._shutdown_detected:
                return
            self._shutdown_detected = True

        logger.info("Graceful shutdown initiated — querying Kubernetes API")

        self._refresh_k8s_observation()

        snapshot = self.get_snapshot()
        if snapshot.state == TerminationState.EXPIRED:
            self._cancel_budget()
        else:
            self._schedule_expiry_timer(snapshot.remaining_seconds)

        if not self._token_canceled:
            self._launch_polling_thread()

        self._log_state()

    def stop(self) -> None:
        """Stop polling and expiry timer. Call on clean shutdown."""
        self._disposed = True
        self._stop_polling.set()
        with self._expiry_timer_lock:
            if self._expiry_timer:
                self._expiry_timer.cancel()

    # ── Polling ───────────────────────────────────────────────────────────────

    def _launch_polling_thread(self) -> None:
        self._polling_thread = threading.Thread(
            target=self._polling_loop,
            daemon=True,
            name="termination-budget-poll",
        )
        self._polling_thread.start()

    def _polling_loop(self) -> None:
        while not self._stop_polling.wait(timeout=self._polling_interval):
            if self._token_canceled or self._disposed:
                return
            self._poll_cycle()

    def _poll_cycle(self) -> None:
        try:
            prev = self.get_snapshot()
            self._refresh_k8s_observation()
            curr = self.get_snapshot()

            if curr.state != prev.state or curr.source != prev.source:
                self._log_state()

            if curr.state == TerminationState.EXPIRED:
                self._cancel_budget()
            else:
                self._schedule_expiry_timer(curr.remaining_seconds)
        except Exception:
            logger.exception("Error during termination budget polling cycle")

    # ── Expiry timer (one-shot, precise) ─────────────────────────────────────

    def _schedule_expiry_timer(self, due_in_seconds: float) -> None:
        """
        Programs (or re-programs) a one-shot timer that fires exactly when
        the usable budget runs out, independent of the polling interval.
        """
        if self._disposed or self._token_canceled or due_in_seconds <= 0:
            return
        with self._expiry_timer_lock:
            if self._disposed or self._token_canceled:
                return
            if self._expiry_timer:
                self._expiry_timer.cancel()
            self._expiry_timer = threading.Timer(due_in_seconds, self._on_expiry_timer_fired)
            self._expiry_timer.daemon = True
            self._expiry_timer.start()

    def _on_expiry_timer_fired(self) -> None:
        if not self._disposed and not self._token_canceled:
            self._cancel_budget()

    def _cancel_budget(self) -> None:
        """Mark budget as expired. Idempotent and thread-safe."""
        with self._lock:
            if self._token_canceled:
                return
            self._token_canceled = True

        self.budget_expired_event.set()
        self._stop_polling.set()

        snapshot = self.get_snapshot()
        logger.critical(
            "Termination budget expired — pod will be killed soon. "
            "remaining_ms=%.0f state=%s source=%s",
            max(snapshot.remaining_seconds, 0.0) * 1000,
            snapshot.state.value,
            snapshot.source.value,
        )

    # ── Snapshot computation ──────────────────────────────────────────────────

    def _build_snapshot(self) -> TerminationSnapshot:
        """Build a snapshot. Caller MUST hold self._lock."""
        now = datetime.now(timezone.utc)

        if not self._shutdown_detected:
            return TerminationSnapshot(
                state=TerminationState.NOT_TERMINATING,
                remaining_seconds=float("inf"),
                observed_at_utc=now,
                deadline_utc=None,
                grace_period_seconds=None,
                source=BudgetSource.NONE,
                is_fallback=False,
            )

        obs = self._last_k8s_obs or self._build_fallback_obs()

        if obs.deadline_utc is None:
            return TerminationSnapshot(
                state=TerminationState.UNKNOWN,
                remaining_seconds=0.0,
                observed_at_utc=now,
                deadline_utc=None,
                grace_period_seconds=obs.grace_period_seconds,
                source=obs.source,
                is_fallback=obs.is_fallback,
            )

        remaining_raw = (obs.deadline_utc - now).total_seconds()
        remaining_usable = remaining_raw - self._configurable_margin - INTERNAL_MARGIN_SECONDS

        if self._token_canceled or remaining_usable <= 0:
            state = TerminationState.EXPIRED
        elif remaining_usable <= self._critical_threshold:
            state = TerminationState.CRITICAL
        else:
            state = TerminationState.TERMINATING

        return TerminationSnapshot(
            state=state,
            remaining_seconds=remaining_usable,
            observed_at_utc=now,
            deadline_utc=obs.deadline_utc,
            grace_period_seconds=obs.grace_period_seconds,
            source=obs.source,
            is_fallback=obs.is_fallback,
        )

    def _build_fallback_obs(self) -> _Obs:
        """
        Build (and cache) a fallback observation anchored to the first call,
        so the deadline doesn't drift forward on every poll.
        Caller MUST hold self._lock.
        """
        if self._fallback_deadline is None:
            grace = self._configured_grace_period or HARDCODED_FALLBACK_SECONDS
            self._fallback_deadline = datetime.now(timezone.utc) + timedelta(seconds=grace)

        deadline = self._fallback_deadline
        if self._configured_grace_period is not None:
            return _Obs(
                deadline_utc=deadline,
                grace_period_seconds=self._configured_grace_period,
                source=BudgetSource.CONFIGURED_GRACE_PERIOD,
                is_fallback=False,
            )
        return _Obs(
            deadline_utc=deadline,
            grace_period_seconds=HARDCODED_FALLBACK_SECONDS,
            source=BudgetSource.HARDCODED_FALLBACK,
            is_fallback=True,
        )

    # ── Kubernetes observation ────────────────────────────────────────────────

    def _refresh_k8s_observation(self) -> None:
        """Query K8s API and update _last_k8s_obs. Never raises."""
        if not self._enable_kubernetes or not self._pod_name or not self._pod_namespace:
            if self._enable_kubernetes and not self._pod_name:
                logger.warning(
                    "POD_NAME / POD_NAMESPACE env vars not set — "
                    "Kubernetes observation disabled, using fallback."
                )
            return

        try:
            obs = self._query_k8s_api()
            if obs is not None:
                with self._lock:
                    self._last_k8s_obs = obs
        except Exception:
            logger.warning(
                "Failed to query Kubernetes API for pod %s/%s — using %s.",
                self._pod_name,
                self._pod_namespace,
                "cached observation" if self._last_k8s_obs else "fallback",
            )

    def _query_k8s_api(self) -> Optional[_Obs]:
        """
        HTTP GET /api/v1/namespaces/{ns}/pods/{name} using the in-cluster
        service account token and CA certificate.
        Returns None if deletionTimestamp is not yet present.
        """
        if not self._k8s_host or not self._k8s_port:
            logger.warning(
                "KUBERNETES_SERVICE_HOST / KUBERNETES_SERVICE_PORT not set — "
                "cannot reach K8s API."
            )
            return None

        try:
            with open(_SA_TOKEN_PATH) as fh:
                token = fh.read().strip()
        except OSError:
            logger.warning("Service account token not found at %s.", _SA_TOKEN_PATH)
            return None

        host = self._k8s_host
        base = f"https://[{host}]:{self._k8s_port}" if ":" in host else f"https://{host}:{self._k8s_port}"
        url = f"{base}/api/v1/namespaces/{self._pod_namespace}/pods/{self._pod_name}"

        ctx = ssl.create_default_context()
        if os.path.exists(_SA_CA_PATH):
            ctx.load_verify_locations(_SA_CA_PATH)

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                pod = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            logger.warning(
                "Kubernetes API returned HTTP %d for pod %s/%s.",
                exc.code, self._pod_name, self._pod_namespace,
            )
            return None

        return self._parse_pod(pod)

    def _parse_pod(self, pod: dict) -> Optional[_Obs]:
        """Parse a Pod JSON object and return an _Obs, or None if not yet deleting."""
        metadata = pod.get("metadata", {})
        deletion_ts_str: Optional[str] = metadata.get("deletionTimestamp")

        if not deletion_ts_str:
            logger.debug("Pod %s has no deletionTimestamp yet.", self._pod_name)
            return None

        try:
            deletion_ts = datetime.fromisoformat(deletion_ts_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse deletionTimestamp '%s'.", deletion_ts_str)
            return None

        grace = self._resolve_grace_period(pod, metadata)
        deadline = deletion_ts + timedelta(seconds=grace)

        return _Obs(
            deadline_utc=deadline,
            grace_period_seconds=grace,
            source=BudgetSource.DELETION_TIMESTAMP,
            is_fallback=False,
        )

    def _resolve_grace_period(self, pod: dict, metadata: dict) -> float:
        """
        Priority:
        1. metadata.deletionGracePeriodSeconds  (real value from the delete request)
        2. spec.terminationGracePeriodSeconds   (pod spec)
        3. configured_grace_period_seconds      (caller setting)
        4. HARDCODED_FALLBACK_SECONDS           (15 s)
        """
        del_grace = metadata.get("deletionGracePeriodSeconds")
        if del_grace is not None:
            return float(del_grace)

        spec_grace = pod.get("spec", {}).get("terminationGracePeriodSeconds")
        if spec_grace is not None:
            return float(spec_grace)

        if self._configured_grace_period is not None:
            return self._configured_grace_period

        return HARDCODED_FALLBACK_SECONDS

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log_state(self) -> None:
        s = self.get_snapshot()
        logger.info(
            "Termination budget state changed: state=%s remaining_ms=%.0f "
            "deadline=%s source=%s is_fallback=%s",
            s.state.value,
            max(s.remaining_seconds, 0.0) * 1000,
            s.deadline_utc.isoformat() if s.deadline_utc else "None",
            s.source.value,
            s.is_fallback,
        )


# ── Module-level singleton ────────────────────────────────────────────────────

_runtime: Optional[TerminationBudgetRuntime] = None


def get_runtime() -> TerminationBudgetRuntime:
    """Return the initialized singleton runtime. Raises if not yet set up."""
    if _runtime is None:
        raise RuntimeError(
            "TerminationBudgetRuntime has not been initialized. "
            "Ensure 'health' is in INSTALLED_APPS and AppConfig.ready() has run."
        )
    return _runtime
