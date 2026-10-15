import pytest
import responses
from celery.exceptions import Retry

from leadhook.destinations.models import Delivery
from leadhook.destinations.tasks import deliver, route_lead
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_lead

HOOK = "https://hooks.example.com/lead"


def _pending_delivery() -> Delivery:
    lead = make_lead(status=Lead.Status.ROUTED)
    return Delivery.objects.create(lead=lead, destination=make_destination())


def test_route_lead_without_destinations_marks_unrouted():
    lead = make_lead()
    route_lead(lead.pk)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


@responses.activate
def test_route_lead_for_unrouted_lead_after_destination_added():
    responses.post(HOOK, json={})
    lead = make_lead(status=Lead.Status.UNROUTED)
    lead.page.default_destinations.add(make_destination())
    route_lead(lead.pk)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.DELIVERED


@responses.activate
def test_deliver_schedules_retry_on_server_error():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    with pytest.raises(Retry):
        deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.next_retry_at is not None
    assert delivery.attempts == 1


@responses.activate
def test_deliver_gives_up_after_last_retry():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    deliver.push_request(retries=deliver.max_retries)
    try:
        deliver.run(delivery.pk)
    finally:
        deliver.pop_request()
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert "Gave up after 1 attempts" in delivery.last_error
    assert delivery.lead.status == Lead.Status.FAILED


def test_deliver_ignores_non_pending_deliveries():
    delivery = _pending_delivery()
    delivery.status = Delivery.Status.SUCCESS
    delivery.save()
    deliver(delivery.pk)  # no HTTP mock registered: would raise if it tried to send
    delivery.refresh_from_db()
    assert delivery.attempts == 0
