from datetime import UTC, datetime, timedelta
from unittest import mock

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


def _assert_failed_and_released(delivery: Delivery, result) -> None:
    delivery.refresh_from_db()
    assert result.outcome == "fail"
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.locked_until is None
    assert delivery.attempts == 1


def test_non_latin1_extra_header_fails_delivery_instead_of_crashing():
    # Real socket path (no HTTP mock): http.client rejects the header before connecting.
    delivery = _delivery(url="http://hooks.example.com/lead", extra_headers={"X-Name": "caf€"})
    result = attempt_delivery(delivery, now=NOW)
    _assert_failed_and_released(delivery, result)
    assert "UnicodeEncodeError" in delivery.last_error
    assert "Latin-1" in delivery.last_error


def test_non_latin1_secret_is_never_echoed_in_error():
    delivery = _delivery(
        url="http://hooks.example.com/lead",
        auth_type=Destination.AuthType.BEARER,
        secret="t€kenSECRET",
    )
    result = attempt_delivery(delivery, now=NOW)
    _assert_failed_and_released(delivery, result)
    assert "SECRET" not in delivery.last_error
    assert "€" not in delivery.last_error


def test_zero_timeout_fails_delivery_instead_of_crashing():
    delivery = _delivery(timeout_s=0)
    result = attempt_delivery(delivery, now=NOW)
    _assert_failed_and_released(delivery, result)
    assert "ValueError" in delivery.last_error


def test_unexpected_error_redacts_secret():
    delivery = _delivery(auth_type=Destination.AuthType.BEARER, secret="hunter2-secret")
    with mock.patch(
        "leadhook.destinations.services.send_request",
        side_effect=RuntimeError("boom with hunter2-secret inside"),
    ):
        result = attempt_delivery(delivery, now=NOW)
    _assert_failed_and_released(delivery, result)
    assert "RuntimeError" in delivery.last_error
    assert "hunter2-secret" not in delivery.last_error
    assert "***" in delivery.last_error


def test_unexpected_render_error_fails_delivery():
    delivery = _delivery()
    with mock.patch("leadhook.destinations.services.render_body", side_effect=KeyError("missing")):
        result = attempt_delivery(delivery, now=NOW)
    _assert_failed_and_released(delivery, result)
    assert "KeyError" in delivery.last_error


def test_template_error_goes_through_mark_delivery_failed():
    delivery = _delivery(payload_template='{"a": {{ id }')
    with mock.patch(
        "leadhook.destinations.services.mark_delivery_failed", wraps=mark_delivery_failed
    ) as spy:
        attempt_delivery(delivery, now=NOW)
    spy.assert_called_once()
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.locked_until is None


def test_create_deliveries_finalizes_lead_when_nothing_is_pending():
    lead = make_lead(status=Lead.Status.ROUTED)
    destination = make_destination()
    lead.page.default_destinations.add(destination)
    Delivery.objects.create(lead=lead, destination=destination, status=Delivery.Status.SUCCESS)
    assert create_deliveries(lead) == []
    lead.refresh_from_db()
    assert lead.status == Lead.Status.DELIVERED


def test_reset_for_resend_refuses_pending_delivery():
    delivery = _delivery()
    lease = NOW + timedelta(seconds=60)
    Delivery.objects.filter(pk=delivery.pk).update(
        attempts=2, locked_until=lease, next_retry_at=NOW
    )
    delivery.refresh_from_db()
    assert reset_for_resend(delivery) is False
    delivery.refresh_from_db()
    assert delivery.locked_until == lease
    assert delivery.next_retry_at == NOW
    assert delivery.retry_base == 0


def test_reset_for_resend_restarts_retry_budget():
    delivery = _delivery()
    Delivery.objects.filter(pk=delivery.pk).update(attempts=7, status=Delivery.Status.FAILED)
    delivery.refresh_from_db()
    assert reset_for_resend(delivery) is True
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.retry_base == 7


def _leased_by_someone_else():
    lead = make_lead(status=Lead.Status.ROUTED)
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    ours = NOW + timedelta(minutes=1)
    Delivery.objects.filter(pk=delivery.pk).update(locked_until=ours + timedelta(hours=1))
    return delivery, ours


def test_mark_failed_with_lost_lease_changes_nothing(mailoutbox):
    delivery, ours = _leased_by_someone_else()
    assert not mark_delivery_failed(delivery, "late", lease=ours)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert mailoutbox == []


def test_schedule_retry_with_lost_lease_changes_nothing():
    delivery, ours = _leased_by_someone_else()
    assert not schedule_retry(delivery, 60, lease=ours)
    delivery.refresh_from_db()
    assert delivery.next_retry_at is None
