from datetime import timedelta
from unittest import mock

import responses
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.tasks import sweep_stuck_work
from leadhook.leads.models import Lead, WebhookEvent
from tests.factories import make_destination, make_form, make_lead
from tests.fixtures import GRAPH_LEAD, leadgen_payload

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"


def _age(model, pk, **fields):
    model.objects.filter(pk=pk).update(**fields)


@responses.activate
def test_sweep_processes_unenqueued_events():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    event = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    _age(WebhookEvent, event.pk, received_at=timezone.now() - timedelta(minutes=10))
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={})
    sweep_stuck_work()
    assert Lead.objects.get(leadgen_id="333").status == Lead.Status.DELIVERED


def test_sweep_ignores_recent_and_invalid_events():
    WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    bad = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=False)
    _age(WebhookEvent, bad.pk, received_at=timezone.now() - timedelta(minutes=10))
    with mock.patch("leadhook.destinations.tasks.process_event.delay") as dispatch:
        sweep_stuck_work()
    dispatch.assert_not_called()


@responses.activate
def test_sweep_redelivers_crashed_delivery():
    responses.post(HOOK, json={})
    lead = make_lead(status=Lead.Status.ROUTED)
    delivery = Delivery.objects.create(lead=lead, destination=make_destination(), attempts=1)
    past = timezone.now() - timedelta(minutes=10)
    _age(Delivery, delivery.pk, locked_until=past, updated_at=past)
    sweep_stuck_work()
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SUCCESS
    assert delivery.attempts == 2


def test_sweep_skips_leased_and_scheduled_deliveries():
    now = timezone.now()
    past = now - timedelta(minutes=10)
    lead = make_lead(status=Lead.Status.ROUTED)
    leased = Delivery.objects.create(lead=lead, destination=make_destination(name="a"))
    _age(Delivery, leased.pk, locked_until=now + timedelta(minutes=1), updated_at=past)
    waiting = Delivery.objects.create(lead=lead, destination=make_destination(name="b"))
    _age(Delivery, waiting.pk, next_retry_at=now + timedelta(hours=2), updated_at=past)
    Delivery.objects.create(lead=lead, destination=make_destination(name="c"))  # just created
    with mock.patch("leadhook.destinations.tasks.deliver.delay") as dispatch:
        sweep_stuck_work()
    dispatch.assert_not_called()


def test_sweep_routes_stuck_fetched_lead():
    lead = make_lead(status=Lead.Status.FETCHED)
    _age(Lead, lead.pk, updated_at=timezone.now() - timedelta(minutes=10))
    sweep_stuck_work()
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


def test_sweep_refetches_received_leads_hourly_for_a_week():
    now = timezone.now()
    due = Lead.objects.create(leadgen_id="1", page_external_id="111")
    _age(Lead, due.pk, updated_at=now - timedelta(hours=2), received_at=now - timedelta(days=1))
    recent = Lead.objects.create(leadgen_id="2", page_external_id="111")
    _age(Lead, recent.pk, updated_at=now - timedelta(minutes=30))
    expired = Lead.objects.create(leadgen_id="3", page_external_id="111")
    _age(Lead, expired.pk, updated_at=now - timedelta(days=8), received_at=now - timedelta(days=8))
    with mock.patch("leadhook.destinations.tasks.fetch_lead.delay") as dispatch:
        sweep_stuck_work()
    assert [call.args[0] for call in dispatch.call_args_list] == [due.pk]
