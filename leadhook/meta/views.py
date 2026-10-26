import hmac
import json
import logging
import re

from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from leadhook.leads.models import WebhookEvent
from leadhook.leads.tasks import process_event

from .models import MetaApp
from .signature import verify_signature

logger = logging.getLogger(__name__)

MAX_STORED_UNPARSEABLE_BODY = 10_000
# At most one warning per app in this window, however many unsigned requests arrive.
BAD_SIGNATURE_LOG_INTERVAL_S = 60
# json.loads joins valid surrogate pairs, so any surrogate left in a string is unpaired.
LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _clean_strings(value):
    """Make every string and key storable in PostgreSQL JSONB.

    U+0000 is removed and lone UTF-16 surrogates (e.g. "\\ud800" in the JSON) are replaced
    with U+FFFD; PostgreSQL rejects both.
    """
    if isinstance(value, str):
        return LONE_SURROGATE.sub("\ufffd", value.replace("\u0000", ""))
    if isinstance(value, list):
        return [_clean_strings(item) for item in value]
    if isinstance(value, dict):
        return {_clean_strings(key): _clean_strings(item) for key, item in value.items()}
    return value


def _enqueue(event_id: int) -> None:
    try:
        process_event.delay(event_id)
    except Exception:
        # Event stays stored and unprocessed; never fail the response (Meta would retry).
        logger.warning("Could not enqueue processing for webhook event %s", event_id)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request: HttpRequest, webhook_uuid) -> HttpResponse:
    meta_app = get_object_or_404(MetaApp, webhook_uuid=webhook_uuid)

    if request.method == "GET":
        token = request.GET.get("hub.verify_token", "")
        if request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(
            token.encode(), meta_app.verify_token.encode()
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("Token di verifica non valido")

    # Django caps the body at DATA_UPLOAD_MAX_MEMORY_SIZE before we read it.
    raw_body = request.body
    if not verify_signature(
        meta_app.app_secret, raw_body, request.headers.get("X-Hub-Signature-256")
    ):
        # Nothing is parsed or stored before authentication: unsigned floods cost no rows.
        throttle_key = f"meta-webhook-bad-signature:{meta_app.pk}"
        if cache.add(throttle_key, True, BAD_SIGNATURE_LOG_INTERVAL_S):
            logger.warning("Rejected webhook with invalid signature for Meta app %s", meta_app.pk)
        # 200 anyway: Meta retries non-2xx responses, and attackers learn nothing.
        return HttpResponse("OK", content_type="text/plain")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        payload = {
            "unparseable_body": _clean_strings(
                raw_body.decode("utf-8", "replace")[:MAX_STORED_UNPARSEABLE_BODY]
            )
        }
    else:
        payload = _clean_strings(payload)

    event = WebhookEvent.objects.create(meta_app=meta_app, payload=payload, signature_ok=True)
    transaction.on_commit(lambda: _enqueue(event.pk))
    return HttpResponse("OK", content_type="text/plain")
