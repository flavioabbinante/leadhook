"""Routing a lead to deliveries and performing delivery attempts."""

import logging
from datetime import datetime, timedelta

from django.db.models import F, Q
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert
from leadhook.leads.models import Lead
from leadhook.routing.resolve import resolve_destinations

from .http import SendResult, build_headers, send_request
from .models import Delivery
from .payload import build_context, render_body
from .templating import TemplateError

logger = logging.getLogger(__name__)
# Seconds before each retry; 6 retries = 7 attempts over ~1.5 days.
DELIVERY_BACKOFF = [60, 300, 1800, 7200, 21600, 86400]
MAX_RETRY_DELAY = 86400
LEASE_MARGIN = 60  # seconds added to the destination timeout
RETRY_TOLERANCE = 5  # seconds a retry may arrive before its next_retry_at


def create_deliveries(lead: Lead) -> list[Delivery]:
    """Create one Delivery per resolved destination; return those still pending."""
    destinations = resolve_destinations(lead)
    if not destinations:
        if lead.status != Lead.Status.UNROUTED:
            send_alert(
                AlertKind.UNROUTED,
                "Lead not routed",
                f"Lead {lead.leadgen_id} (page {lead.page_external_id}, form "
                f"{lead.form_external_id}) has no active destination. Assign one to the form "
                "or a default to the page, then reprocess the lead.",
            )
        lead.status = Lead.Status.UNROUTED
        lead.save(update_fields=["status", "updated_at"])
        return []
    deliveries = [
        Delivery.objects.get_or_create(lead=lead, destination=destination)[0]
        for destination in destinations
    ]
    if lead.status in (Lead.Status.FETCHED, Lead.Status.UNROUTED, Lead.Status.ROUTED):
        lead.status = Lead.Status.ROUTED
        lead.save(update_fields=["status", "updated_at"])
    pending = [d for d in deliveries if d.status == Delivery.Status.PENDING]
    if not pending:
        # Every delivery is already final: settle the lead instead of leaving it ROUTED.
        refresh_lead_status(lead)
    return pending


def describe_unexpected_error(exc: Exception, destination) -> str:
    """A storable description of an unexpected error that never contains the secret."""
    if isinstance(exc, UnicodeError):
        # The message would quote the offending character of a header (possibly the secret).
        return (
            f"{type(exc).__name__}: a header name or value contains characters that cannot "
            "be sent (only Latin-1 is allowed, without line breaks)"
        )
    message = f"{type(exc).__name__}: {exc}"
    if destination.secret:
        message = message.replace(destination.secret, "***")
    return message


def attempt_delivery(delivery: Delivery, *, now: datetime | None = None) -> SendResult:
    now = now or timezone.now()
    # Claim the attempt with a lease in one conditional UPDATE: only a PENDING delivery that
    # is due and not leased by another worker can be claimed. A crash after the POST but
    # before the result is saved lets the lease expire and then allows a resend (inherent to
    # acks_late); receivers dedupe on the Idempotency-Key header.
    lease = now + timedelta(seconds=delivery.destination.timeout_s + LEASE_MARGIN)
    claimed = (
        Delivery.objects.filter(pk=delivery.pk, status=Delivery.Status.PENDING)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .filter(
            Q(next_retry_at__isnull=True)
            | Q(next_retry_at__lte=now + timedelta(seconds=RETRY_TOLERANCE))
        )
        .update(attempts=F("attempts") + 1, locked_until=lease, updated_at=now)
    )
    if not claimed:
        return SendResult("skip", error="Attempt claimed by another worker")
    delivery.attempts, delivery.retry_base = Delivery.objects.values_list(
        "attempts", "retry_base"
    ).get(pk=delivery.pk)
    delivery.locked_until = lease
    destination = delivery.destination
    body = ""
    # Once claimed, every error must end in a saved outcome; an escaping exception would leave
    # the delivery PENDING with its lease held and no error recorded.
    try:
        context = build_context(delivery.lead, delivery, attempt=delivery.attempts, sent_at=now)
        body = render_body(destination, context)
        headers = build_headers(
            destination,
            body=body,
            timestamp=int(now.timestamp()),
            idempotency_key=delivery.lead.leadgen_id,
        )
        result = send_request(destination, body, headers)
    except TemplateError as exc:
        result = SendResult("fail", error=str(exc))
    except Exception as exc:
        error = describe_unexpected_error(exc, destination)
        logger.error("Delivery %s failed with an unexpected error: %s", delivery.pk, error)
        result = SendResult("fail", error=f"Unexpected error: {error}")

    delivery.request_body = body
    delivery.response_body = result.response_body
    delivery.last_status_code = result.status_code
    if result.outcome == "fail":
        mark_delivery_failed(
            delivery,
            result.error,
            extra_fields=("request_body", "response_body", "last_status_code"),
        )
        return result
    delivery.last_error = result.error
    delivery.next_retry_at = None
    if result.outcome == "success":
        delivery.status = Delivery.Status.SUCCESS
        delivery.delivered_at = now
        delivery.locked_until = None
    # On retry the lease is kept until schedule_retry/mark_delivery_failed releases it,
    # so no other worker can slip in between the attempt and its rescheduling.
    delivery.save()
    return result


def next_retry_delay(retries_done: int, retry_after: int | None) -> int:
    base = DELIVERY_BACKOFF[min(retries_done, len(DELIVERY_BACKOFF) - 1)]
    if retry_after is None:
        return base
    return min(max(base, retry_after), MAX_RETRY_DELAY)


def schedule_retry(delivery: Delivery, delay: int, *, now: datetime | None = None) -> None:
    delivery.next_retry_at = (now or timezone.now()) + timedelta(seconds=delay)
    delivery.locked_until = None
    delivery.save(update_fields=["next_retry_at", "locked_until", "updated_at"])


def mark_delivery_failed(
    delivery: Delivery, error: str, *, extra_fields: tuple[str, ...] = ()
) -> None:
    """The single place that marks a delivery FAILED (and releases its lease)."""
    delivery.status = Delivery.Status.FAILED
    delivery.last_error = error
    delivery.next_retry_at = None
    delivery.locked_until = None
    delivery.save(
        update_fields=[
            "status",
            "last_error",
            "next_retry_at",
            "locked_until",
            "updated_at",
            *extra_fields,
        ]
    )
    send_alert(
        AlertKind.DELIVERY_FAILED,
        f"Delivery to {delivery.destination} failed",
        f"Lead {delivery.lead.leadgen_id} could not be delivered to {delivery.destination} "
        f"({delivery.public_id}): {error}",
    )


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


def reset_for_resend(delivery: Delivery) -> bool:
    """Put a finished (SUCCESS or FAILED) delivery back to PENDING with a fresh retry budget.

    Returns False and changes nothing for a PENDING delivery: it is being sent right now or
    is scheduled for a retry, and resetting it would break its lease.
    """
    now = timezone.now()
    reset = (
        Delivery.objects.filter(pk=delivery.pk)
        .exclude(status=Delivery.Status.PENDING)
        .update(
            status=Delivery.Status.PENDING,
            retry_base=F("attempts"),
            next_retry_at=None,
            locked_until=None,
            updated_at=now,
        )
    )
    if not reset:
        return False
    delivery.refresh_from_db(
        fields=["status", "attempts", "retry_base", "next_retry_at", "locked_until", "updated_at"]
    )
    lead = delivery.lead
    if lead.status != Lead.Status.ROUTED:
        lead.status = Lead.Status.ROUTED
        lead.save(update_fields=["status", "updated_at"])
    return True


def is_dispatchable(delivery: Delivery, *, now: datetime | None = None) -> bool:
    """True for a pending delivery that is due and not leased by a running attempt."""
    now = now or timezone.now()
    return (
        delivery.status == Delivery.Status.PENDING
        and (delivery.next_retry_at is None or delivery.next_retry_at <= now)
        and (delivery.locked_until is None or delivery.locked_until < now)
    )
