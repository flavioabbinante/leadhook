from datetime import timedelta

import responses
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.services import reset_for_resend
from leadhook.destinations.tasks import deliver
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.leads.retention import purge_expired_data
from tests.factories import make_destination, make_lead
from tests.fixtures import leadgen_payload


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
    assert "purged" in delivery.last_error
    assert len(responses.calls) == 0
