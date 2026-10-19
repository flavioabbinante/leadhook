from celery import shared_task

from leadhook.leads.models import Lead

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


@shared_task
def route_lead(lead_id: int) -> None:
    lead = Lead.objects.select_related("page", "form").get(pk=lead_id)
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
    result = attempt_delivery(delivery)
    if result.outcome == "skip":
        return
    if result.outcome == "retry":
        # 0 after the first attempt of a cycle; a cycle allows len(DELIVERY_BACKOFF) + 1 attempts.
        retry_index = delivery.attempts - delivery.retry_base - 1
        if retry_index >= len(DELIVERY_BACKOFF):
            mark_delivery_failed(
                delivery, f"Gave up after {delivery.attempts} attempts: {result.error}"
            )
        else:
            delay = next_retry_delay(retry_index, result.retry_after)
            schedule_retry(delivery, delay)
            raise self.retry(countdown=delay)
    refresh_lead_status(delivery.lead)
