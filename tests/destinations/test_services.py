from datetime import UTC, datetime, timedelta

import responses

from leadhook.destinations.models import Delivery, Destination
from leadhook.destinations.services import (
    attempt_delivery,
    create_deliveries,
    mark_delivery_failed,
    next_retry_delay,
    refresh_lead_status,
    reset_for_resend,
    schedule_retry,
)
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_lead

URL = "https://hooks.example.com/lead"
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _delivery(**destination_kwargs) -> Delivery:
    lead = make_lead()
    destination = make_destination(**destination_kwargs)
    return Delivery.objects.create(lead=lead, destination=destination)


def test_create_deliveries_marks_unrouted_when_no_destination():
    lead = make_lead()
    assert create_deliveries(lead) == []
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


def test_create_deliveries_is_idempotent():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination())
    assert len(create_deliveries(lead)) == 1
    assert len(create_deliveries(lead)) == 1
    assert Delivery.objects.count() == 1
    lead.refresh_from_db()
    assert lead.status == Lead.Status.ROUTED


@responses.activate
def test_successful_attempt_records_everything():
    responses.post(URL, json={"ok": True})
    delivery = _delivery()
    result = attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert result.outcome == "success"
    assert delivery.status == Delivery.Status.SUCCESS
    assert delivery.attempts == 1
    assert delivery.delivered_at == NOW
    assert delivery.last_status_code == 200
    assert '"id": "333"' in delivery.request_body
    sent = responses.calls[0].request
    assert sent.headers["Idempotency-Key"] == "333"
    assert sent.headers["X-LeadHook-Timestamp"] == str(int(NOW.timestamp()))


@responses.activate
def test_client_error_marks_delivery_failed():
    responses.post(URL, status=404)
    delivery = _delivery()
    attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.last_error == "HTTP 404"


@responses.activate
def test_retryable_error_keeps_delivery_pending():
    responses.post(URL, status=503)
    delivery = _delivery()
    assert attempt_delivery(delivery, now=NOW).outcome == "retry"
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING


def test_template_error_fails_without_sending():
    delivery = _delivery(payload_template='{"a": {{ id }')
    result = attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert result.outcome == "fail"
    assert delivery.status == Delivery.Status.FAILED
    assert "Template error" in delivery.last_error


def test_next_retry_delay():
    assert next_retry_delay(0, None) == 60
    assert next_retry_delay(5, None) == 86400
    assert next_retry_delay(0, 600) == 600
    assert next_retry_delay(3, 10) == 7200
    assert next_retry_delay(0, 10**9) == 86400


def test_schedule_retry_sets_next_retry_at():
    delivery = _delivery()
    schedule_retry(delivery, 300, now=NOW)
    delivery.refresh_from_db()
    assert delivery.next_retry_at == NOW + timedelta(seconds=300)


def test_refresh_lead_status():
    lead = make_lead(status=Lead.Status.ROUTED)
    ok = Delivery.objects.create(
        lead=lead, destination=make_destination(), status=Delivery.Status.SUCCESS
    )
    other = Delivery.objects.create(lead=lead, destination=make_destination(name="b"))
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.ROUTED  # one still pending
    mark_delivery_failed(other, "gave up")
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.PARTIALLY_FAILED
    ok.status = Delivery.Status.FAILED
    ok.save()
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.FAILED


def test_reset_for_resend():
    delivery = _delivery()
    mark_delivery_failed(delivery, "HTTP 404")
    Delivery.objects.filter(pk=delivery.pk).update(
        next_retry_at=NOW, locked_until=NOW + timedelta(seconds=60)
    )
    delivery.refresh_from_db()
    delivery.lead.status = Lead.Status.FAILED
    delivery.lead.save()
    reset_for_resend(delivery)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.next_retry_at is None
    assert delivery.locked_until is None
    assert delivery.lead.status == Lead.Status.ROUTED


def test_destination_kind_is_webhook_by_default():
    assert make_destination().kind == Destination.Kind.WEBHOOK


def test_refresh_lead_status_ignores_stale_in_memory_status():
    lead = make_lead(status=Lead.Status.DELIVERED)
    Delivery.objects.create(
        lead=lead, destination=make_destination(), status=Delivery.Status.SUCCESS
    )
    # another worker moved the lead back to ROUTED; our in-memory copy still says DELIVERED
    Lead.objects.filter(pk=lead.pk).update(status=Lead.Status.ROUTED)
    refresh_lead_status(lead)
    assert Lead.objects.get(pk=lead.pk).status == Lead.Status.DELIVERED


def test_create_deliveries_does_not_downgrade_finished_lead():
    lead = make_lead(status=Lead.Status.DELIVERED)
    lead.page.default_destinations.add(make_destination())
    create_deliveries(lead)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.DELIVERED
