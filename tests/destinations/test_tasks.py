from datetime import timedelta

import pytest
import responses
from celery.exceptions import Retry
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.services import DELIVERY_BACKOFF, reset_for_resend
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
    Delivery.objects.filter(pk=delivery.pk).update(attempts=len(DELIVERY_BACKOFF))
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.attempts == len(DELIVERY_BACKOFF) + 1
    assert "Gave up after 7 attempts" in delivery.last_error
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


def _retry_delay(delivery: Delivery) -> float:
    before = timezone.now()
    with pytest.raises(Retry):
        deliver(delivery.pk)
    delivery.refresh_from_db()
    return (delivery.next_retry_at - before).total_seconds()


@responses.activate
def test_redispatch_continues_backoff_from_attempts_already_made():
    # A fresh dispatch (route_lead re-dispatch, sweeper) has request.retries == 0,
    # but three attempts were already made: the next delay is DELIVERY_BACKOFF[3].
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(attempts=3)
    delay = _retry_delay(delivery)
    assert DELIVERY_BACKOFF[3] - 5 <= delay <= DELIVERY_BACKOFF[3] + 5
    assert delivery.attempts == 4


@responses.activate
def test_attempts_never_exceed_budget_on_fresh_dispatch():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(attempts=len(DELIVERY_BACKOFF))
    deliver.delay(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.attempts == len(DELIVERY_BACKOFF) + 1


@responses.activate
def test_resend_restarts_retry_budget():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=len(DELIVERY_BACKOFF) + 1, status=Delivery.Status.FAILED
    )
    delivery.refresh_from_db()
    assert reset_for_resend(delivery)
    delay = _retry_delay(delivery)
    assert DELIVERY_BACKOFF[0] - 5 <= delay <= DELIVERY_BACKOFF[0] + 5
    assert delivery.status == Delivery.Status.PENDING
    # ...and gives up after another full cycle of 7 attempts.
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=delivery.retry_base + len(DELIVERY_BACKOFF), next_retry_at=None
    )
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.attempts == 2 * (len(DELIVERY_BACKOFF) + 1)
