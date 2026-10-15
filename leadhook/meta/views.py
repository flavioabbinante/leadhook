import hmac
import json

from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from leadhook.leads.models import WebhookEvent
from leadhook.leads.tasks import process_event

from .models import MetaApp
from .signature import verify_signature

MAX_STORED_UNPARSEABLE_BODY = 10_000


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request: HttpRequest, webhook_uuid) -> HttpResponse:
    meta_app = get_object_or_404(MetaApp, webhook_uuid=webhook_uuid)

    if request.method == "GET":
        token = request.GET.get("hub.verify_token", "")
        if request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(
            token, meta_app.verify_token
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
            "unparseable_body": raw_body.decode("utf-8", "replace")[:MAX_STORED_UNPARSEABLE_BODY]
        }

    event = WebhookEvent.objects.create(
        meta_app=meta_app,
        payload=payload,
        signature_ok=signature_ok,
        error="" if signature_ok else "Invalid signature",
    )
    if signature_ok:
        transaction.on_commit(lambda: process_event.delay(event.pk))
    # Always 200: Meta retries non-2xx responses, and attackers learn nothing.
    return HttpResponse("OK", content_type="text/plain")
