from django.urls import reverse

from leadhook.destinations.models import Delivery
from leadhook.leads.models import Lead
from leadhook.leads.pipeline import lead_pipeline
from tests.factories import make_destination, make_lead


def test_pipeline_of_a_delivered_lead_branches_per_destination():
    lead = make_lead(status=Lead.Status.PARTIALLY_FAILED)
    Delivery.objects.create(
        lead=lead, destination=make_destination(name="CRM"), status="success", last_status_code=200
    )
    Delivery.objects.create(
        lead=lead,
        destination=make_destination(name="Chat", kind="telegram", url=""),
        status="failed",
        last_status_code=400,
        attempts=3,
    )
    steps, branches = lead_pipeline(lead)
    assert [s.state for s in steps] == ["done", "done", "done", "done"]
    assert steps[3].detail == "Destinazioni: 2"
    assert [(b.title, b.icon, b.state) for b in branches] == [
        ("CRM", "target", "done"),
        ("Chat", "send", "error"),
    ]
    assert branches[1].detail == "Fallita · HTTP 400 · 3 tentativi"


def test_pipeline_shows_where_a_lead_stopped():
    steps, branches = lead_pipeline(make_lead(status=Lead.Status.RECEIVED, error="Token expired"))
    assert [s.state for s in steps] == ["done", "done", "error", "pending"]
    assert branches == []
    lead = Lead.objects.get()
    lead.status = Lead.Status.UNROUTED
    steps, _ = lead_pipeline(lead)
    assert steps[3].state == "error"


def test_lead_page_renders_pipeline(admin_client):
    lead = make_lead()
    content = admin_client.get(reverse("admin:leads_lead_change", args=[lead.pk])).content.decode()
    assert 'class="lh-flow"' in content
