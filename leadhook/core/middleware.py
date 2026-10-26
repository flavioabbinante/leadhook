import logging

from cryptography.fernet import InvalidToken
from django.http import HttpResponse

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
