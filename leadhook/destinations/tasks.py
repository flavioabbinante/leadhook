from celery import shared_task

from leadhook.leads.models import Lead

from .models import Delivery
from .services import (
    DELIVERY_BACKOFF,
    attempt_delivery,
    create_deliveries,
    mark_delivery_failed,
    next_retry_delay,
    refresh_lead_status,
    schedule_retry,
)


@shared_task
def route_lead(lead_id: int) -> None:
    lead = Lead.objects.select_related("page", "form").get(pk=lead_id)
    if lead.status not in (Lead.Status.FETCHED, Lead.Status.UNROUTED):
        return
    for delivery in create_deliveries(lead):
        deliver.delay(delivery.pk)


@shared_task(bind=True, max_retries=len(DELIVERY_BACKOFF))
def deliver(self, delivery_id: int) -> None:
    delivery = Delivery.objects.select_related("lead__page", "lead__form", "destination").get(
        pk=delivery_id
    )
    if delivery.status != Delivery.Status.PENDING:
        return
    result = attempt_delivery(delivery)
    if result.outcome == "retry":
        if self.request.retries >= self.max_retries:
            mark_delivery_failed(
                delivery, f"Gave up after {delivery.attempts} attempts: {result.error}"
            )
        else:
            delay = next_retry_delay(self.request.retries, result.retry_after)
            schedule_retry(delivery, delay)
            raise self.retry(countdown=delay)
    refresh_lead_status(delivery.lead)
