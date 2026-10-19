import responses
from django.urls import reverse

from leadhook.meta.models import Form, Page
from tests.factories import make_destination, make_form, make_meta_app, make_page
from tests.fixtures import GRAPH_FORM

BASE = "https://graph.facebook.com/v21.0"
SETUP = "/admin/setup/"
ACCOUNTS = {"data": [{"id": "111", "name": "Shop", "access_token": "page-secret-token"}]}


def test_setup_requires_staff(client):
    response = client.get(SETUP)
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_setup_links_to_meta_app_form_first(admin_client):
    assert reverse("admin:meta_metaapp_add") in admin_client.get(SETUP).content.decode()


def test_setup_shows_callback_url_and_verify_token(admin_client):
    app = make_meta_app()
    html = admin_client.get(SETUP).content.decode()
    assert app.webhook_url() in html
    assert app.verify_token in html


@responses.activate
def test_verify_webhook_ok(admin_client):
    app = make_meta_app()
    responses.get(
        f"{BASE}/1000/subscriptions",
        json={
            "data": [
                {
                    "object": "page",
                    "callback_url": app.webhook_url(),
                    "fields": [{"name": "leadgen", "version": "v21.0"}],
                }
            ]
        },
    )
    response = admin_client.post(SETUP, {"action": "verify"}, follow=True)
    assert "Webhook verified" in response.content.decode()


@responses.activate
def test_verify_webhook_missing_subscription(admin_client):
    make_meta_app()
    responses.get(f"{BASE}/1000/subscriptions", json={"data": []})
    response = admin_client.post(SETUP, {"action": "verify"}, follow=True)
    assert "no Page subscription" in response.content.decode()


@responses.activate
def test_load_pages_never_puts_page_tokens_in_html(admin_client):
    make_meta_app()
    responses.get(f"{BASE}/me/accounts", json=ACCOUNTS)
    html = admin_client.post(SETUP, {"action": "load_pages"}).content.decode()
    assert "Shop" in html
    assert "page-secret-token" not in html


@responses.activate
def test_connect_pages_subscribes_and_syncs_forms(admin_client):
    make_meta_app()
    responses.get(f"{BASE}/me/accounts", json=ACCOUNTS)
    responses.post(f"{BASE}/111/subscribed_apps", json={"success": True})
    responses.get(f"{BASE}/111/leadgen_forms", json={"data": [GRAPH_FORM]})
    admin_client.post(SETUP, {"action": "connect_pages", "page_ids": ["111"]})
    page = Page.objects.get(page_id="111")
    assert page.subscribed
    assert page.page_token == "page-secret-token"
    assert Form.objects.get(form_id="222").page == page


@responses.activate
def test_send_test_lead(admin_client):
    make_form()
    responses.post(f"{BASE}/222/test_leads", json={"id": "999"})
    response = admin_client.post(SETUP, {"action": "test_lead", "form_id": "222"}, follow=True)
    assert "Test lead requested" in response.content.decode()


@responses.activate
def test_send_test_lead_error_explains_one_per_form(admin_client):
    make_form()
    responses.post(
        f"{BASE}/222/test_leads",
        status=400,
        json={"error": {"message": "A test lead already exists", "code": 100}},
    )
    response = admin_client.post(SETUP, {"action": "test_lead", "form_id": "222"}, follow=True)
    assert "Lead Ads Testing Tool" in response.content.decode()


def test_dashboard_links_setup_until_complete(admin_client):
    index = reverse("admin:index")
    assert "Continue the guided setup" in admin_client.get(index).content.decode()
    page = make_page()
    page.default_destinations.add(make_destination())
    assert "Continue the guided setup" not in admin_client.get(index).content.decode()
