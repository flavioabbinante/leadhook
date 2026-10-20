"""“Connect with Facebook”: OAuth login that stores the token and registers the webhook."""

import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .graph import (
    GraphError,
    app_subscriptions,
    debug_token,
    exchange_login_code,
    exchange_long_lived_token,
    login_dialog_url,
    subscribe_app_webhook,
)
from .models import MetaApp

STATE_KEY = "facebook_login_state"


def _meta_app(request) -> MetaApp:
    if not request.user.has_perm("meta.change_metaapp"):
        raise PermissionDenied
    meta_app = MetaApp.objects.order_by("pk").first()
    if meta_app is None:
        raise PermissionDenied
    return meta_app


def facebook_login(request):
    meta_app = _meta_app(request)
    public = urlsplit(settings.PUBLIC_BASE_URL)
    if (request.scheme, request.get_host()) != (public.scheme, public.netloc):
        # Facebook returns to PUBLIC_BASE_URL; start there so the session (and its state) match.
        return redirect(settings.PUBLIC_BASE_URL + reverse("facebook-login"))
    state = secrets.token_urlsafe(24)
    request.session[STATE_KEY] = state
    return redirect(login_dialog_url(meta_app, state))


def facebook_callback(request):
    meta_app = _meta_app(request)
    expected = request.session.pop(STATE_KEY, None)
    state = request.GET.get("state", "")
    # The state ties the callback to a login this user started (CSRF protection for OAuth).
    if not expected or not secrets.compare_digest(expected, state):
        messages.error(request, "The Facebook login expired or did not start here. Try again.")
        return redirect("setup")
    code = request.GET.get("code")
    if not code:
        reason = request.GET.get("error_description") or "you cancelled the login"
        messages.warning(request, f"Facebook was not connected: {reason}.")
        return redirect("setup")
    try:
        connect_with_code(meta_app, code)
    except GraphError as exc:
        messages.error(request, f"Facebook refused the connection: {exc}")
        return redirect("setup")
    messages.success(request, "Facebook connected. Choose the pages to receive leads from.")
    try:
        other = _other_callback_url(meta_app)
        if other:
            # One callback URL per app: replacing it would cut off whoever uses it now.
            messages.warning(
                request,
                f"The Meta app already sends leads to {other}. LeadHook left it untouched: "
                "use a Meta app of your own, or replace the webhook by hand if nobody needs it.",
            )
        else:
            subscribe_app_webhook(meta_app)
            messages.success(request, "Webhook registered on the Meta app.")
    except GraphError as exc:
        # Usually PUBLIC_BASE_URL is not reachable from the internet (e.g. on localhost).
        messages.warning(
            request,
            f"The webhook could not be registered automatically ({exc}). "
            "Make sure PUBLIC_BASE_URL is reachable over HTTPS, or set it up by hand below.",
        )
    return redirect(reverse("setup") + "?load=pages#pages")


def connect_with_code(meta_app: MetaApp, code: str) -> None:
    """Exchange the login code for the longest-lived token available and store it."""
    token = exchange_login_code(meta_app, code)["access_token"]
    expires = debug_token(meta_app, token).get("expires_at") or 0
    if not expires:
        # A Facebook Login for Business system-user token: it never expires.
        meta_app.token_type, meta_app.token_expires_at = MetaApp.TokenType.SYSTEM_USER, None
    else:
        long_lived = exchange_long_lived_token(meta_app, token)
        token = long_lived["access_token"]
        expires_in = long_lived.get("expires_in")
        meta_app.token_type = MetaApp.TokenType.USER
        meta_app.token_expires_at = (
            timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
        )
    meta_app.access_token = token
    meta_app.save(update_fields=["access_token", "token_type", "token_expires_at"])


def _other_callback_url(meta_app: MetaApp) -> str:
    """The app's Page webhook URL when it points somewhere other than this LeadHook."""
    for subscription in app_subscriptions(meta_app):
        url = subscription.get("callback_url") or ""
        if subscription.get("object") == "page" and url and url != meta_app.webhook_url():
            return url
    return ""
