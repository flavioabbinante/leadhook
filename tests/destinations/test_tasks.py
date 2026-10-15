from datetime import timedelta

import pytest
import responses
from celery.exceptions import Retry
from django.utils import timezone

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


@responses.activate
def test_route_lead_redispatches_pending_deliveries_of_routed_lead():
    responses.post(HOOK, json={})
    delivery = _pending_delivery()
    delivery.lead.page.default_destinations.add(delivery.destination)
    route_lead(delivery.lead.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SUCCESS
    assert len(responses.calls) == 1


@responses.activate
def test_deliver_skips_delivery_leased_by_another_worker():
    responses.post(HOOK, json={})
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=1, locked_until=timezone.now() + timedelta(seconds=60)
    )
    deliver(delivery.pk)
    assert len(responses.calls) == 0
    delivery.refresh_from_db()
    assert delivery.attempts == 1


@responses.activate
def test_expired_lease_can_be_reclaimed():
    responses.post(HOOK, json={})
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=1, locked_until=timezone.now() - timedelta(seconds=1)
    )
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SUCCESS
    assert delivery.attempts == 2
    assert delivery.locked_until is None


@responses.activate
def test_deliver_and_route_lead_leave_deliveries_waiting_for_retry_alone():
    responses.post(HOOK, json={})
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=1, next_retry_at=timezone.now() + timedelta(seconds=300)
    )
    delivery.lead.page.default_destinations.add(delivery.destination)
    deliver(delivery.pk)
    route_lead(delivery.lead.pk)
    assert len(responses.calls) == 0
    delivery.refresh_from_db()
    assert delivery.attempts == 1


@responses.activate
def test_retry_releases_lease_and_sets_next_retry_at():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    with pytest.raises(Retry):
        deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.locked_until is None
    assert delivery.next_retry_at is not None
