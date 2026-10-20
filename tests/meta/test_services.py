from datetime import UTC, datetime, timedelta

import responses
from django.utils import timezone

from leadhook.meta.models import Form, MetaApp, Page
from leadhook.meta.services import check_meta_app, check_page, silent_pages, sync_page_forms
from tests.factories import make_form, make_meta_app, make_page
from tests.fixtures import GRAPH_FORM

BASE = "https://graph.facebook.com/v21.0"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@responses.activate
def test_sync_page_forms_creates_and_refreshes_forms():
    page = make_page()
    make_form(page=page, form_id="222", name="", status="")  # saved blank by a failed Graph read
    responses.get(
        f"{BASE}/111/leadgen_forms",
        json={"data": [GRAPH_FORM, {"id": "999", "name": "New", "status": "ACTIVE"}]},
    )
    assert sync_page_forms(page) == 2
    refreshed = Form.objects.get(form_id="222")
    assert (refreshed.name, refreshed.status) == ("Energy offer", "ACTIVE")
    assert refreshed.last_synced_at is not None
    assert refreshed.questions == [{"key": "full_name", "label": "Full name"}]
    assert Form.objects.get(form_id="999").page == page


@responses.activate
def test_check_page_resubscribes_when_app_missing():
    page = make_page(subscribed=False, error="old problem")
    responses.get(f"{BASE}/111/subscribed_apps", json={"data": []})
    subscribe = responses.post(f"{BASE}/111/subscribed_apps", json={"success": True})
    check_page(page, now=NOW)
    page.refresh_from_db()
    assert subscribe.call_count == 1
    assert page.subscribed
    assert page.error == ""
    assert page.last_checked_at == NOW


@responses.activate
def test_check_page_leaves_subscribed_page_alone():
    page = make_page()
    responses.get(f"{BASE}/111/subscribed_apps", json={"data": [{"id": "1000"}]})
    check_page(page, now=NOW)
    assert len(responses.calls) == 1


@responses.activate
def test_check_page_subscription_failure_is_recorded_and_alerted(mailoutbox):
    page = make_page()
    responses.get(f"{BASE}/111/subscribed_apps", json={"data": []})
    responses.post(
        f"{BASE}/111/subscribed_apps",
        status=403,
        json={"error": {"message": "Missing pages_manage_metadata", "code": 200}},
    )
    check_page(page, now=NOW)
    page.refresh_from_db()
    assert page.error.startswith("Could not subscribe the page to lead events")
    assert len(mailoutbox) == 1


@responses.activate
def test_check_page_ignores_transient_errors():
    page = make_page(error="")
    responses.get(f"{BASE}/111/subscribed_apps", status=503)
    check_page(page, now=NOW)
    page.refresh_from_db()
    assert page.error == ""
    assert page.last_checked_at is None


@responses.activate
def test_check_meta_app_alerts_on_invalid_token(mailoutbox):
    app = make_meta_app()
    responses.get(f"{BASE}/debug_token", json={"data": {"is_valid": False}})
    check_meta_app(app, now=NOW)
    assert "no longer valid" in mailoutbox[0].body


@responses.activate
def test_check_meta_app_stores_expiry_and_warns(mailoutbox):
    app = make_meta_app()
    expires = NOW + timedelta(days=3)
    responses.get(
        f"{BASE}/debug_token",
        json={"data": {"is_valid": True, "expires_at": int(expires.timestamp())}},
    )
    check_meta_app(app, now=NOW)
    assert MetaApp.objects.get(pk=app.pk).token_expires_at == expires
    assert "expires on 2026-10-22" in mailoutbox[0].body


@responses.activate
def test_check_meta_app_quiet_for_never_expiring_token(mailoutbox):
    app = make_meta_app()
    responses.get(f"{BASE}/debug_token", json={"data": {"is_valid": True, "expires_at": 0}})
    check_meta_app(app, now=NOW)
    assert mailoutbox == []


def test_silent_pages_window():
    now = timezone.now()
    quiet = make_page(page_id="1", last_lead_at=now - timedelta(hours=30))
    make_page(page_id="2", last_lead_at=now - timedelta(hours=2))  # recent lead
    make_page(page_id="3", last_lead_at=now - timedelta(days=40))  # dormant for long
    make_page(page_id="4")  # never received a lead
    make_page(
        page_id="5",
        last_lead_at=now - timedelta(hours=30),
        silence_alerted_at=now - timedelta(hours=5),  # already alerted for this silence
    )
    assert list(silent_pages(now)) == [quiet]
    assert Page.objects.count() == 5
