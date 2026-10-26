import logging

from cryptography.fernet import InvalidToken
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse
from django.urls import reverse

logger = logging.getLogger(__name__)

MESSAGE = (
    "LeadHook non riesce a decifrare le chiavi segrete salvate nel database: "
    "LEADHOOK_ENCRYPTION_KEY non è la chiave usata per cifrarle. "
    "Ripristina la chiave originale e riavvia LeadHook."
)


class EncryptionKeyErrorMiddleware:
    """A readable error instead of a traceback when LEADHOOK_ENCRYPTION_KEY was changed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, InvalidToken):
            return None
        logger.error("Stored secrets cannot be decrypted: LEADHOOK_ENCRYPTION_KEY changed")
        return HttpResponse(MESSAGE, status=500, content_type="text/plain")


# Admin pages reachable after the password but before the second factor.
TWO_FACTOR_EXEMPT = {
    "admin:login",
    "admin:logout",
    "admin:jsi18n",
    "two-factor",
    "admin_password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
}


class TwoFactorMiddleware:
    """Sends a logged-in user to the TOTP page until they confirm a code in this session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from .twofactor import is_verified

        if (
            not request.path_info.startswith("/admin/")
            or not request.user.is_authenticated
            or is_verified(request)
            or request.resolver_match.view_name in TWO_FACTOR_EXEMPT
        ):
            return None
        return redirect_to_login(request.get_full_path(), reverse("two-factor"))
