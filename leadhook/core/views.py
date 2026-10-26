from django.contrib.auth.views import PasswordResetView
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .auth import take_attempt
from .health import HEARTBEAT_MAX_AGE, heartbeat_age
from .tasks import send_password_reset


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
    """Mails are sent by a task, so the response takes the same time whether the address
    has an account or not; one address gets a few emails at most."""

    def form_valid(self, form):
        email = form.cleaned_data["email"]
        if take_attempt(f"reset:{email.lower()}"):
            send_password_reset.delay(email)
        # Same "email sent" page in every case, so nothing is revealed about the address.
        return redirect(self.get_success_url())
