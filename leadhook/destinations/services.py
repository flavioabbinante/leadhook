"""Routing a lead to deliveries and performing delivery attempts."""

from datetime import datetime, timedelta

from django.db.models import F
from django.utils import timezone

from leadhook.leads.models import Lead
from leadhook.routing.resolve import resolve_destinations

from .http import SendResult, build_headers, send_request
from .models import Delivery
from .payload import build_context, render_body
from .templating import TemplateError

# Seconds before each retry; 6 retries = 7 attempts over ~1.5 days.
DELIVERY_BACKOFF = [60, 300, 1800, 7200, 21600, 86400]
MAX_RETRY_DELAY = 86400


def create_deliveries(lead: Lead) -> list[Delivery]:
    """Create one Delivery per resolved destination; return those still pending."""
    destinations = resolve_destinations(lead)
    if not destinations:
        lead.status = Lead.Status.UNROUTED
        lead.save(update_fields=["status", "updated_at"])
        return []
    deliveries = [
        Delivery.objects.get_or_create(lead=lead, destination=destination)[0]
        for destination in destinations
    ]
    lead.status = Lead.Status.ROUTED
    lead.save(update_fields=["status", "updated_at"])
    return [d for d in deliveries if d.status == Delivery.Status.PENDING]


def attempt_delivery(delivery: Delivery, *, now: datetime | None = None) -> SendResult:
    now = now or timezone.now()
    # Atomically claim this attempt: if another worker already took it, do not send.
    # A crash after the POST but before the result is saved can still cause a resend
    # (inherent to acks_late); receivers dedupe on the Idempotency-Key header.
    claimed = Delivery.objects.filter(
        pk=delivery.pk, status=Delivery.Status.PENDING, attempts=delivery.attempts
    ).update(attempts=F("attempts") + 1, updated_at=now)
    if not claimed:
        return SendResult("skip", error="Attempt claimed by another worker")
    delivery.attempts += 1
    destination = delivery.destination
    context = build_context(delivery.lead, delivery, attempt=delivery.attempts, sent_at=now)
    try:
        body = render_body(destination, context)
    except TemplateError as exc:
        body = ""
        result = SendResult("fail", error=str(exc))
    else:
        headers = build_headers(
            destination,
            body=body,
            timestamp=int(now.timestamp()),
            idempotency_key=delivery.lead.leadgen_id,
        )
        result = send_request(destination, body, headers)

    delivery.request_body = body
    delivery.response_body = result.response_body
    delivery.last_status_code = result.status_code
    delivery.last_error = result.error
    delivery.next_retry_at = None
    if result.outcome == "success":
        delivery.status = Delivery.Status.SUCCESS
        delivery.delivered_at = now
    elif result.outcome == "fail":
        delivery.status = Delivery.Status.FAILED
    delivery.save()
    return result


def next_retry_delay(retries_done: int, retry_after: int | None) -> int:
    base = DELIVERY_BACKOFF[min(retries_done, len(DELIVERY_BACKOFF) - 1)]
    if retry_after is None:
        return base
    return min(max(base, retry_after), MAX_RETRY_DELAY)


def schedule_retry(delivery: Delivery, delay: int, *, now: datetime | None = None) -> None:
    delivery.next_retry_at = (now or timezone.now()) + timedelta(seconds=delay)
    delivery.save(update_fields=["next_retry_at", "updated_at"])


def mark_delivery_failed(delivery: Delivery, error: str) -> None:
    delivery.status = Delivery.Status.FAILED
    delivery.last_error = error
    delivery.next_retry_at = None
    delivery.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])


def refresh_lead_status(lead: Lead) -> None:
    lead.refresh_from_db(fields=["status"])
    statuses = set(lead.deliveries.values_list("status", flat=True))
    if not statuses or Delivery.Status.PENDING in statuses:
        return
    if statuses == {Delivery.Status.SUCCESS}:
        new_status = Lead.Status.DELIVERED
    elif statuses == {Delivery.Status.FAILED}:
        new_status = Lead.Status.FAILED
    else:
        new_status = Lead.Status.PARTIALLY_FAILED
    if lead.status != new_status:
        lead.status = new_status
        lead.save(update_fields=["status", "updated_at"])


def reset_for_resend(delivery: Delivery) -> None:
    delivery.status = Delivery.Status.PENDING
    delivery.next_retry_at = None
    delivery.save(update_fields=["status", "next_retry_at", "updated_at"])
    lead = delivery.lead
    if lead.status != Lead.Status.ROUTED:
        lead.status = Lead.Status.ROUTED
        lead.save(update_fields=["status", "updated_at"])
