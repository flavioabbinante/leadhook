import hmac
import json
import logging

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


def _strip_nul(value):
    """PostgreSQL JSONB rejects U+0000; remove it from all strings and keys."""
    if isinstance(value, str):
        return value.replace("\u0000", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {_strip_nul(key): _strip_nul(item) for key, item in value.items()}
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
        return HttpResponseForbidden("Invalid verify token")

    raw_body = request.body
    signature_ok = verify_signature(
        meta_app.app_secret, raw_body, request.headers.get("X-Hub-Signature-256")
    )
    try:
        payload = json.loads(raw_body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        payload = {
            "unparseable_body": _strip_nul(
                raw_body.decode("utf-8", "replace")[:MAX_STORED_UNPARSEABLE_BODY]
            )
        }
    else:
        payload = _strip_nul(payload)

    event = WebhookEvent.objects.create(
        meta_app=meta_app,
        payload=payload,
        signature_ok=signature_ok,
        error="" if signature_ok else "Invalid signature",
    )
    if signature_ok:
        transaction.on_commit(lambda: _enqueue(event.pk))
    # Always 200: Meta retries non-2xx responses, and attackers learn nothing.
    return HttpResponse("OK", content_type="text/plain")
