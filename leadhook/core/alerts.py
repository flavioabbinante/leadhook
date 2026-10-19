"""Throttled email alerts for operators. Messages carry IDs only, never lead personal data."""

import logging
from enum import StrEnum

from django.conf import settings
from django.core.cache import cache

from .tasks import send_alert_email

logger = logging.getLogger(__name__)

FOOTER = (
    "\n\nOpen LeadHook: {url}/admin/\n"
    "Alerts of this kind are paused for 15 minutes after each email; "
    "the admin dashboard shows everything."
)


class AlertKind(StrEnum):
    DELIVERY_FAILED = "delivery_failed"
    UNROUTED = "unrouted"
    PAGE_ERROR = "page_error"
    FETCH_FAILED = "fetch_failed"
    SILENCE = "silence"


def send_alert(kind: AlertKind, subject: str, body: str) -> bool:
    """Queue an alert email unless one of the same kind went out in the throttle window."""
    if not settings.ALERT_EMAILS:
        return False
    key = f"alert:{kind}"
    try:
        if not cache.add(key, 1, timeout=settings.ALERT_THROTTLE_SECONDS):
            return False
        send_alert_email.delay(
            f"[LeadHook] {subject}", body + FOOTER.format(url=settings.PUBLIC_BASE_URL)
        )
    except Exception:
        # An alert must never break the pipeline step that raised it.
        logger.exception("Could not queue %s alert", kind)
        try:
            cache.delete(key)
        except Exception:
            pass
        return False
    return True
