"""Two-factor login: every staff user confirms a TOTP code (RFC 6238) after the password."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.contrib import admin
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from .auth import clear_failures, is_locked, record_failure
from .models import TwoFactorDevice

SESSION_KEY = "lh_2fa_user"
STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def code_at(secret: str, counter: int) -> str:
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def matching_counter(secret: str, code: str, now: float | None = None) -> int | None:
    """Time step of the code, allowing one step of clock drift either way."""
    current = int((time.time() if now is None else now) // STEP)
    for counter in (current - 1, current, current + 1):
        if hmac.compare_digest(code_at(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str) -> str:
    query = urlencode({"secret": secret, "issuer": "LeadHook"})
    return f"otpauth://totp/LeadHook:{quote(account)}?{query}"


def is_verified(request: HttpRequest) -> bool:
    return request.session.get(SESSION_KEY) == request.user.pk


@never_cache
def two_factor(request: HttpRequest) -> HttpResponse:
    device, _ = TwoFactorDevice.objects.get_or_create(
        user=request.user, defaults={"secret": new_secret()}
    )
    next_url = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        next_url = reverse("admin:index")
    error = None
    if request.method == "POST":
        key = f"2fa:{request.user.pk}"
        if is_locked(key):
            error = "Troppi tentativi sbagliati. Riprova tra 15 minuti."
        else:
            with transaction.atomic():
                # Lock the row so the same code cannot be used by two parallel requests.
                device = TwoFactorDevice.objects.select_for_update().get(pk=device.pk)
                counter = matching_counter(device.secret, request.POST.get("code", "").strip())
                accepted = counter is not None and counter > device.last_counter
                if accepted:
                    device.last_counter = counter
                    device.confirmed_at = device.confirmed_at or timezone.now()
                    device.save(update_fields=["last_counter", "confirmed_at"])
            if accepted:
                clear_failures(key)
                request.session.cycle_key()
                request.session[SESSION_KEY] = request.user.pk
                return redirect(next_url)
            record_failure(key)
            error = "Codice non valido."
    context = {
        **admin.site.each_context(request),
        "title": "Verifica in due passaggi",
        "device": device,
        "error": error,
    }
    if device.confirmed_at is None:
        # Spaced in groups of four for typing it into the app by hand.
        context["secret"] = " ".join(device.secret[i : i + 4] for i in range(0, 32, 4))
        context["uri"] = provisioning_uri(device.secret, request.user.get_username())
    return render(request, "admin/leadhook_two_factor.html", context)
