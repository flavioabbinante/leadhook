from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.views import PasswordResetView
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .auth import is_locked, record_failure
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


class ThrottledPasswordResetView(PasswordResetView):
    """Reset links point at PUBLIC_BASE_URL, and one address gets a few emails at most."""

    @property
    def extra_email_context(self):
        url = urlsplit(settings.PUBLIC_BASE_URL)
        return {"protocol": url.scheme, "domain": url.netloc, "site_name": "LeadHook"}

    def form_valid(self, form):
        key = f"reset:{form.cleaned_data['email'].lower()}"
        if is_locked(key):
            # Same "email sent" page, so the throttle reveals nothing about the address.
            return redirect(self.get_success_url())
        record_failure(key)
        return super().form_valid(form)
