"""Beat heartbeat kept in the cache (Redis); read by /health/ and the dashboard."""

import time

from django.core.cache import cache

HEARTBEAT_KEY = "heartbeat"
HEARTBEAT_MAX_AGE = 300  # seconds; the heartbeat task runs every minute


def record_heartbeat() -> None:
    cache.set(HEARTBEAT_KEY, time.time(), timeout=None)


def heartbeat_age() -> float | None:
    """Seconds since the last heartbeat, None if never recorded. Raises if the cache is down."""
    beat = cache.get(HEARTBEAT_KEY)
    return None if beat is None else time.time() - beat


def heartbeat_is_fresh() -> bool:
    try:
        age = heartbeat_age()
    except Exception:
        return False
    return age is not None and age < HEARTBEAT_MAX_AGE
