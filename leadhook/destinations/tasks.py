from celery import shared_task
from django.utils import timezone

from leadhook.leads.models import Lead
from leadhook.leads.services import FETCH_BACKOFF
from leadhook.leads.tasks import fetch_lead, process_event

from .models import Delivery
from .services import (
    DELIVERY_BACKOFF,
    attempt_delivery,
    create_deliveries,
    is_dispatchable,
    mark_delivery_failed,
    next_retry_delay,
    refresh_lead_status,
    schedule_retry,
)
from .sweep import stuck_deliveries, stuck_events, stuck_fetched_leads, stuck_received_leads

SWEEP_BATCH = 500


@shared_task
def route_lead(lead_id: int) -> None:
    lead = Lead.objects.select_related("page", "form").get(pk=lead_id)
    if lead.purged_at is not None:
        return
    if lead.status not in (Lead.Status.FETCHED, Lead.Status.UNROUTED, Lead.Status.ROUTED):
        return
    # ROUTED is accepted so a lost deliver dispatch is recovered on redelivery;
    # create_deliveries is idempotent and deliver ignores non-pending deliveries.
    # Deliveries waiting out a retry countdown or being sent right now are left alone.
    for delivery in create_deliveries(lead):
        if is_dispatchable(delivery):
            deliver.delay(delivery.pk)


# Retry decisions come from Delivery.attempts - Delivery.retry_base, never from
# self.request.retries, which restarts at 0 on every fresh dispatch (route_lead, resend).
# max_retries=None so Celery's own limit never interferes.
@shared_task(bind=True, max_retries=None)
def deliver(self, delivery_id: int) -> None:
    delivery = Delivery.objects.select_related("lead__page", "lead__form", "destination").get(
        pk=delivery_id
    )
    if delivery.status != Delivery.Status.PENDING:
        return
    if delivery.lead.purged_at is not None:
        mark_delivery_failed(
            delivery,
            "I dati del lead sono stati eliminati dalla politica di conservazione "
            "prima della consegna",
        )
        refresh_lead_status(delivery.lead)
        return
    result = attempt_delivery(delivery)
    if result.outcome == "skip":
        return
    if result.outcome == "retry":
        lease = delivery.locked_until  # attempt_delivery keeps the lease on retry
        # 0 after the first attempt of a cycle; a cycle allows len(DELIVERY_BACKOFF) + 1 attempts.
        retry_index = delivery.attempts - delivery.retry_base - 1
        if retry_index >= len(DELIVERY_BACKOFF):
            mark_delivery_failed(
                delivery,
                f"Rinuncia dopo {delivery.attempts} tentativi: {result.error}",
                lease=lease,
            )
        else:
            delay = next_retry_delay(retry_index, result.retry_after)
            if not schedule_retry(delivery, delay, lease=lease):
                return
            raise self.retry(countdown=delay)
    refresh_lead_status(delivery.lead)


@shared_task
def sweep_stuck_work() -> None:
    """Re-dispatch lost work; every task it dispatches is safe to run twice."""
    now = timezone.now()
    for pk in stuck_events(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        process_event.delay(pk)
    for pk in stuck_received_leads(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        # One attempt, no retry chain: the next sweep (an hour later) tries again.
        fetch_lead.delay(pk, attempt=len(FETCH_BACKOFF))
    for pk in stuck_fetched_leads(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        route_lead.delay(pk)
    for pk in stuck_deliveries(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        deliver.delay(pk)
