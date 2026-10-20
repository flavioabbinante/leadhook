import responses

from leadhook.leads.models import Lead
from leadhook.leads.services import FetchOutcome, fetch_lead_details, ingest_lead
from leadhook.meta.models import Form
from tests.factories import make_form, make_page
from tests.fixtures import GRAPH_FORM, GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"


def _received_lead(**kwargs) -> Lead:
    values = {"leadgen_id": "333", "page_external_id": "111", "form_external_id": "222"}
    values.update(kwargs)
    return Lead.objects.create(**values)


def test_ingest_lead_is_idempotent():
    first = ingest_lead(
        leadgen_id="333", page_external_id="111", form_external_id="222", source="webhook"
    )
    second = ingest_lead(
        leadgen_id="333", page_external_id="111", form_external_id="222", source="reconcile"
    )
    assert first is not None
    assert second is None
    assert Lead.objects.get().source == Lead.Source.WEBHOOK


@responses.activate
def test_fetch_lead_details_fills_the_lead():
    form = make_form()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.DONE
    lead.refresh_from_db()
    assert lead.status == Lead.Status.FETCHED
    assert lead.page == form.page
    assert lead.form == form
    assert lead.data == {
        "full_name": "Jane Doe",
        "phone_number": "+15551234567",
        "services": ["electricity", "gas"],
    }
    assert (lead.ad_name, lead.adset_name, lead.campaign_name) == (
        "Ad 1",
        "Ad set 1",
        "Campaign 1",
    )
    assert lead.created_time.isoformat() == "2026-10-15T08:30:00+00:00"
    form.page.refresh_from_db()
    assert form.page.last_lead_at is not None


@responses.activate
def test_unknown_form_is_created_from_graph():
    page = make_page()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.get(f"{BASE}/222", json=GRAPH_FORM)
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.DONE
    form = Form.objects.get(form_id="222")
    assert (form.page, form.name, form.status) == (page, "Energy offer", "ACTIVE")


def test_fetch_lead_for_unknown_page_keeps_lead():
    lead = _received_lead(page_external_id="999")
    assert fetch_lead_details(lead) == FetchOutcome.GIVE_UP
    lead.refresh_from_db()
    assert lead.status == Lead.Status.RECEIVED
    assert "Pagina sconosciuta 999" in lead.error


@responses.activate
def test_token_error_flags_page_and_gives_up():
    page = make_page()
    responses.get(
        f"{BASE}/333", status=400, json={"error": {"message": "Session expired", "code": 190}}
    )
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.GIVE_UP
    page.refresh_from_db()
    lead.refresh_from_db()
    assert "Session expired" in page.error
    assert lead.status == Lead.Status.RECEIVED


@responses.activate
def test_successful_fetch_clears_page_error():
    form = make_form()
    form.page.error = "Meta ha rifiutato il token della pagina"
    form.page.save()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    fetch_lead_details(_received_lead())
    form.page.refresh_from_db()
    assert form.page.error == ""


@responses.activate
def test_transient_error_asks_for_retry():
    make_page()
    responses.get(f"{BASE}/333", status=500, json={"error": {"message": "Oops", "code": 2}})
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.RETRY
    lead.refresh_from_db()
    assert lead.error == "Oops"


@responses.activate
def test_long_meta_names_are_truncated_to_field_length():
    make_page()
    long_name = "n" * 300
    responses.get(
        f"{BASE}/333",
        json={
            **GRAPH_LEAD,
            "ad_name": long_name,
            "adset_name": long_name,
            "campaign_name": long_name,
        },
    )
    responses.get(f"{BASE}/222", json={**GRAPH_FORM, "name": long_name})
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.DONE
    lead.refresh_from_db()
    assert lead.ad_name == "n" * 255
    assert lead.adset_name == "n" * 255
    assert lead.campaign_name == "n" * 255
    assert Form.objects.get(form_id="222").name == "n" * 255
