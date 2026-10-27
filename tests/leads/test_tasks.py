from datetime import timedelta
from unittest import mock

import numpy as np
import pytest
import responses
from celery.exceptions import Retry
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.leads.services import FETCH_BACKOFF, FetchOutcome
from leadhook.leads.stats import lead_drop
from leadhook.leads.tasks import check_lead_drop, fetch_lead, process_event
from leadhook.meta.models import Form, Page
from tests.factories import make_destination, make_form, make_lead, make_page
from tests.fixtures import GRAPH_LEAD, leadgen_payload

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"


def _event(payload=None, signature_ok=True) -> WebhookEvent:
    return WebhookEvent.objects.create(
        payload=payload or leadgen_payload(), signature_ok=signature_ok
    )


@responses.activate
def test_full_pipeline_from_event_to_delivery():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={"ok": True})

    event = _event()
    process_event(event.pk)

    lead = Lead.objects.get()
    assert lead.status == Lead.Status.DELIVERED
    assert Delivery.objects.get().status == Delivery.Status.SUCCESS
    event.refresh_from_db()
    assert event.processed_at is not None


@responses.activate
def test_duplicate_events_deliver_once():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={"ok": True})

    process_event(_event().pk)
    process_event(_event().pk)

    assert Lead.objects.count() == 1
    assert len([c for c in responses.calls if c.request.url == HOOK]) == 1


def test_events_with_bad_signature_are_ignored():
    process_event(_event(signature_ok=False).pk)
    assert Lead.objects.count() == 0


@responses.activate
def test_partial_failure_across_destinations():
    form = make_form()
    form.page.default_destinations.add(
        make_destination(name="ok", url="https://ok.example.com/"),
        make_destination(name="bad", url="https://bad.example.com/"),
    )
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post("https://ok.example.com/", json={})
    responses.post("https://bad.example.com/", status=400)

    process_event(_event().pk)

    assert Lead.objects.get().status == Lead.Status.PARTIALLY_FAILED


@responses.activate
def test_fetch_lead_retries_on_transient_graph_error():
    make_page()
    responses.get(f"{BASE}/333", status=500, json={"error": {"message": "Oops", "code": 2}})
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    with pytest.raises(Retry):
        fetch_lead(lead.pk)


def test_fetch_lead_skips_leads_already_fetched():
    lead = Lead.objects.create(leadgen_id="333", status=Lead.Status.FETCHED)
    with mock.patch("leadhook.leads.tasks.fetch_lead_details") as fetch:
        fetch_lead(lead.pk)
    fetch.assert_not_called()


def test_process_event_redispatches_fetch_for_existing_received_lead():
    Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    with mock.patch("leadhook.leads.tasks.fetch_lead.delay") as delay:
        process_event(_event().pk)
    delay.assert_called_once()


def test_fetch_lead_retry_index_comes_from_attempt_argument():
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111")
    with (
        mock.patch("leadhook.leads.tasks.fetch_lead_details", return_value=FetchOutcome.RETRY),
        mock.patch.object(fetch_lead, "retry", side_effect=Retry()) as retry,
        pytest.raises(Retry),
    ):
        fetch_lead(lead.pk, attempt=3)
    retry.assert_called_once()
    assert retry.call_args.kwargs["countdown"] == FETCH_BACKOFF[3]
    assert retry.call_args.kwargs["kwargs"] == {"attempt": 4}


def test_fetch_lead_gives_up_after_last_attempt():
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111")
    with (
        mock.patch("leadhook.leads.tasks.fetch_lead_details", return_value=FetchOutcome.RETRY),
        mock.patch.object(fetch_lead, "retry") as retry,
    ):
        fetch_lead(lead.pk, attempt=len(FETCH_BACKOFF))
    retry.assert_not_called()
    lead.refresh_from_db()
    assert lead.status == Lead.Status.RECEIVED


@responses.activate
def test_fetch_lead_token_error_flags_page_and_alerts(mailoutbox):
    make_form()
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    responses.get(
        f"{BASE}/333",
        status=400,
        json={"error": {"message": "Error validating access token", "code": 190}},
    )
    fetch_lead(lead.pk)  # gives up without raising Retry
    lead.refresh_from_db()
    assert lead.status == Lead.Status.RECEIVED
    assert Page.objects.get(page_id="111").error.startswith(
        "Meta ha rifiutato il token della pagina"
    )
    assert len(mailoutbox) == 1
    assert "page-token" not in mailoutbox[0].body


@responses.activate
def test_fetch_lead_exhaustion_alerts(mailoutbox):
    make_form()
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    responses.get(f"{BASE}/333", status=500)
    fetch_lead(lead.pk, attempt=len(FETCH_BACKOFF))
    assert len(mailoutbox) == 1
    assert "leggere il lead 333 da Meta" in mailoutbox[0].body


def _leads_at(when, count, start=0):
    form = Form.objects.first() or make_form()
    for i in range(count):
        lead = make_lead(form, leadgen_id=f"drop-{start + i}")
        Lead.objects.filter(pk=lead.pk).update(received_at=when)


@pytest.fixture
def no_forecast():
    with mock.patch("leadhook.leads.stats._forecaster", side_effect=ImportError):
        yield


def _fake_forecaster(median, low):
    forecast = mock.Mock(median=np.full(6, median / 6))
    forecast.quantile.return_value = np.full(6, low / 6)
    return mock.Mock(forecast=mock.Mock(return_value=forecast))


def test_check_lead_drop_alerts_once_when_volume_collapses(mailoutbox, no_forecast):
    now = timezone.now()
    for week in range(1, 5):
        _leads_at(now - timedelta(weeks=week, hours=1), 10, start=week * 100)
    _leads_at(now - timedelta(hours=1), 2)
    check_lead_drop()
    check_lead_drop()
    assert len(mailoutbox) == 1
    assert "sono arrivati 2 lead" in mailoutbox[0].body


def test_lead_drop_ignores_normal_and_low_volume(no_forecast):
    now = timezone.now()
    for week in range(1, 5):
        _leads_at(now - timedelta(weeks=week, hours=1), 10, start=week * 100)
    _leads_at(now - timedelta(hours=1), 8)
    assert lead_drop(now) is None
    Lead.objects.all().delete()
    _leads_at(now - timedelta(weeks=1, hours=1), 4)  # average 1 per window: too few to judge
    assert lead_drop(now) is None


def test_lead_drop_uses_timestral_forecast():
    now = timezone.now()
    _leads_at(now - timedelta(weeks=1, hours=1), 3, start=100)
    _leads_at(now - timedelta(hours=1), 6)
    with mock.patch("leadhook.leads.stats._forecaster") as forecaster:
        forecaster.return_value = _fake_forecaster(median=20, low=8)
        assert lead_drop(now) == (6, 20)
        history = forecaster.return_value.forecast.call_args.args[0]
        assert len(history) == 4 * 7 * 24 and history.sum() == 3
        forecaster.return_value = _fake_forecaster(median=20, low=5)
        assert lead_drop(now) is None  # 6 leads are within the forecast range
