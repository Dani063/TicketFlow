import logging

from app.models import OperationalMetric

logger = logging.getLogger(__name__)


def record_metric(name, value=1, labels=None):
    try:
        return OperationalMetric.objects.create(
            name=name,
            value=value,
            labels=labels or {},
        )
    except Exception:
        logger.exception("metric_record_failed", extra={"metric": name})
        return None
