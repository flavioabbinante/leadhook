import json
from datetime import timedelta

import responses
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.services import reset_for_resend
from leadhook.destinations.sweep import stuck_received_leads
from leadhook.destinations.tasks import deliver
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.leads.retention import purge_expired_data
from leadhook.leads.services import FetchOutcome, fetch_lead_details
from leadhook.leads.tasks import fetch_lead, reconcile_leads
from tests.factories import make_destination, make_form, make_lead
from tests.fixtures import GRAPH_LEAD, leadgen_payload

BASE = "https://graph.facebook.com/v21.0"


def _make_old(model, pk, days=91):
    model.objects.filter(pk=pk).update(received_at=timezone.now() - timedelta(days=days))


def test_purge_clears_personal_data_keeps_metadata():
    lead = make_lead(status=Lead.Status.DELIVERED)
    delivery = Delivery.objects.create(
        lead=lead,
        destination=make_destination(),
        status=Delivery.Status.SUCCESS,
        request_body='{"full_name": "Jane Doe"}',
        response_body="ok",
    )
    _make_old(Lead, lead.pk)
    counts = purge_expired_data(now=timezone.now())
    assert counts["leads"] == 1
    assert counts["deliveries"] == 1
    lead.refresh_from_db()
    delivery.refresh_from_db()
    assert lead.data == {}
    assert lead.field_data == []
    assert lead.purged_at is not None
    assert (lead.leadgen_id, lead.ad_name, lead.status) == ("333", "Ad 1", Lead.Status.DELIVERED)
    assert (delivery.request_body, delivery.response_body) == ("", "")
    assert delivery.status == Delivery.Status.SUCCESS


def test_purge_keeps_recent_data():
    lead = make_lead()
    assert purge_expired_data(now=timezone.now()) == {"leads": 0, "deliveries": 0, "events": 0}
    lead.refresh_from_db()
    assert lead.data == {"full_name": "Jane Doe"}


def test_purge_clears_old_webhook_payloads():
    old = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    recent = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    _make_old(WebhookEvent, old.pk)
    assert purge_expired_data(now=timezone.now())["events"] == 1
    old.refresh_from_db()
    recent.refresh_from_db()
    assert old.payload == {}
    assert recent.payload == leadgen_payload()


def test_purge_is_idempotent():
    lead = make_lead()
    _make_old(Lead, lead.pk)
    purge_expired_data(now=timezone.now())
    assert purge_expired_data(now=timezone.now()) == {"leads": 0, "deliveries": 0, "events": 0}


def test_resend_refuses_purged_lead():
    lead = make_lead(status=Lead.Status.FAILED, purged_at=timezone.now())
    delivery = Delivery.objects.create(
        lead=lead, destination=make_destination(), status=Delivery.Status.FAILED
    )
    assert not reset_for_resend(delivery)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED


@responses.activate
def test_deliver_fails_pending_delivery_of_purged_lead():
    lead = make_lead(status=Lead.Status.ROUTED, purged_at=timezone.now(), data={})
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert "eliminati" in delivery.last_error
    assert len(responses.calls) == 0


def _purged_received_lead() -> Lead:
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    _make_old(Lead, lead.pk)
    purge_expired_data(now=timezone.now())
    return lead


@responses.activate
def test_fetch_never_refills_a_purged_lead():
    make_form()
    lead = _purged_received_lead()
    fetch_lead(lead.pk)
    assert not responses.calls
    lead.refresh_from_db()
    assert (lead.data, lead.field_data, lead.status) == ({}, [], Lead.Status.RECEIVED)


@responses.activate
def test_fetch_in_flight_during_purge_stores_nothing():
    make_form()
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    _make_old(Lead, lead.pk)
    stale = Lead.objects.get(pk=lead.pk)  # loaded by fetch_lead before the purge ran

    def purge_then_answer(request):
        purge_expired_data(now=timezone.now())
        return 200, {}, json.dumps(GRAPH_LEAD)

    responses.add_callback(responses.GET, f"{BASE}/333", callback=purge_then_answer)
    assert fetch_lead_details(stale) == FetchOutcome.GIVE_UP
    lead.refresh_from_db()
    assert (lead.data, lead.field_data, lead.status) == ({}, [], Lead.Status.RECEIVED)
    assert lead.purged_at is not None


@responses.activate
def test_reconcile_never_refills_a_purged_lead():
    make_form().page.default_destinations.add(make_destination())
    lead = _purged_received_lead()
    responses.get(f"{BASE}/222/leads", json={"data": [GRAPH_LEAD]})
    reconcile_leads()
    lead.refresh_from_db()
    assert (lead.data, lead.field_data, lead.status) == ({}, [], Lead.Status.RECEIVED)
    assert not lead.deliveries.exists()


def test_sweep_skips_purged_received_leads():
    lead = _purged_received_lead()
    Lead.objects.filter(pk=lead.pk).update(received_at=timezone.now() - timedelta(days=1))
    later = timezone.now() + timedelta(hours=2)
    assert not stuck_received_leads(later).filter(pk=lead.pk).exists()
