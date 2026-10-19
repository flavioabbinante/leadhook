from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .health import HEARTBEAT_MAX_AGE, heartbeat_age


@never_cache
@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Database, Redis and beat (via the heartbeat the worker records every minute)."""
    checks = {"database": "ok", "redis": "ok", "beat": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        checks["database"] = "error"
    try:
        age = heartbeat_age()
    except Exception:
        checks["redis"] = "error"
        age = None
    if age is None or age >= HEARTBEAT_MAX_AGE:
        checks["beat"] = "stale"
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(checks, status=200 if healthy else 503)
