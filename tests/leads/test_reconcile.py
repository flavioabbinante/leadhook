import json
from datetime import UTC, datetime, timedelta

import responses
from responses import matchers

from leadhook.leads.models import Lead
from leadhook.leads.tasks import reconcile_leads
from leadhook.meta.graph import LEAD_FIELDS
from tests.factories import make_destination, make_form, make_lead
from tests.fixtures import GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"
LEADS_URL = f"{BASE}/222/leads"


@responses.activate
def test_reconcile_creates_missing_leads_and_delivers():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    responses.post(HOOK, json={})
    reconcile_leads()
    lead = Lead.objects.get(leadgen_id="333")
    assert lead.source == Lead.Source.RECONCILE
    assert lead.status == Lead.Status.DELIVERED
    assert lead.data["full_name"] == "Jane Doe"
    form.refresh_from_db()
    assert form.last_reconciled_at is not None


@responses.activate
def test_reconcile_skips_known_leads():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    make_lead(form=form, status=Lead.Status.DELIVERED)
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    reconcile_leads()
    assert Lead.objects.count() == 1
    assert not [c for c in responses.calls if c.request.url.startswith(HOOK)]


@responses.activate
def test_reconcile_completes_known_received_lead():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    Lead.objects.create(
        leadgen_id="333", page_external_id="111", form_external_id="222", error="HTTP 500"
    )
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    responses.post(HOOK, json={})
    reconcile_leads()
    lead = Lead.objects.get(leadgen_id="333")
    assert lead.status == Lead.Status.DELIVERED
    assert lead.source == Lead.Source.WEBHOOK


@responses.activate
def test_reconcile_window_overlaps_last_run():
    last = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    make_form(last_reconciled_at=last)
    filtering = json.dumps(
        [
            {
                "field": "time_created",
                "operator": "GREATER_THAN",
                "value": int((last - timedelta(hours=1)).timestamp()),
            }
        ]
    )
    listing = responses.get(
        LEADS_URL,
        json={"data": []},
        match=[
            matchers.query_param_matcher(
                {"fields": LEAD_FIELDS, "filtering": filtering}, strict_match=False
            )
        ],
    )
    reconcile_leads()
    # reconcile_leads swallows Graph errors, so an unmatched request would not fail by itself.
    assert listing.call_count == 1


@responses.activate
def test_reconcile_failure_keeps_last_reconciled_at():
    last = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    form = make_form(last_reconciled_at=last)
    responses.get(LEADS_URL, status=500)
    reconcile_leads()
    form.refresh_from_db()
    assert form.last_reconciled_at == last


@responses.activate
def test_reconcile_ignores_inactive_forms():
    make_form(status="ARCHIVED")
    reconcile_leads()
    assert len(responses.calls) == 0


@responses.activate
def test_reconcile_token_error_flags_page(mailoutbox):
    form = make_form()
    responses.get(LEADS_URL, status=400, json={"error": {"message": "bad token", "code": 190}})
    reconcile_leads()
    form.page.refresh_from_db()
    assert form.page.error.startswith("Meta rejected the page token")
    assert len(mailoutbox) == 1
