from unittest import mock

import pytest
import responses
from django.contrib.auth.models import Permission, User
from django.urls import reverse

from leadhook.meta.models import Form, MetaApp, Page
from leadhook.meta.setup import CONNECT_PAGES, TEST_LEAD, VIEW_APP
from tests.factories import make_destination, make_form, make_meta_app, make_page
from tests.fixtures import GRAPH_FORM

BASE = "https://graph.facebook.com/v21.0"
SETUP = "/admin/setup/"
ACCOUNTS = {"data": [{"id": "111", "name": "Shop", "access_token": "page-secret-token"}]}


def test_setup_requires_staff(client):
    response = client.get(SETUP)
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_setup_asks_for_app_credentials_first(admin_client):
    html = admin_client.get(SETUP).content.decode()
    assert 'name="app_id"' in html
    assert "Salva e collega con Facebook" in html


def test_saving_app_credentials_starts_facebook_login(admin_client):
    response = admin_client.post(
        SETUP, {"action": "save_app", "app_id": "4242", "app_secret": "the-secret"}
    )
    assert response["Location"] == reverse("facebook-login")
    app = MetaApp.objects.get()
    assert (app.app_id, app.app_secret) == ("4242", "the-secret")
    # Saving again with a blank secret keeps the stored one.
    admin_client.post(SETUP, {"action": "save_app", "app_id": "4242", "app_secret": ""})
    app.refresh_from_db()
    assert app.app_secret == "the-secret"


def test_app_credentials_are_required(admin_client):
    response = admin_client.post(SETUP, {"action": "save_app", "app_id": "", "app_secret": ""})
    assert response.status_code == 200
    assert not MetaApp.objects.exists()


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
    assert "Webhook verificato" in response.content.decode()


@responses.activate
def test_verify_webhook_missing_subscription(admin_client):
    make_meta_app()
    responses.get(f"{BASE}/1000/subscriptions", json={"data": []})
    response = admin_client.post(SETUP, {"action": "verify"}, follow=True)
    assert "iscrizione Page con il campo leadgen" in response.content.decode()


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
    assert "Lead di prova richiesto" in response.content.decode()


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
    assert "Continua la configurazione guidata" in admin_client.get(index).content.decode()
    page = make_page()
    page.default_destinations.add(make_destination())
    assert "Continua la configurazione guidata" not in admin_client.get(index).content.decode()


def _staff(client, permissions=()):
    user = User.objects.create_user("staff", password="pw", is_staff=True)
    for name in permissions:
        app_label, codename = name.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    client.force_login(user)


@pytest.mark.parametrize(
    "data",
    [
        {"action": "verify"},
        {"action": "load_pages"},
        {"action": "connect_pages", "page_ids": ["111"]},
        {"action": "test_lead", "form_id": "222"},
    ],
)
@responses.activate
def test_staff_without_permissions_cannot_run_setup_steps(client, data):
    make_form()
    _staff(client)
    with mock.patch("leadhook.meta.setup.connect_page") as connect:
        assert client.post(SETUP, data).status_code == 403
    connect.assert_not_called()
    assert len(responses.calls) == 0  # refused before any Graph call


@responses.activate
def test_staff_without_permissions_sees_no_app_values_pages_or_forms(client):
    app = make_meta_app()
    make_form(page=make_page(meta_app=app), name="Secret form")
    _staff(client)
    html = client.get(SETUP, {"load": "pages"}).content.decode()
    assert len(responses.calls) == 0
    assert app.verify_token not in html
    assert "Secret form" not in html
    assert 'value="connect_pages"' not in html


@responses.activate
def test_staff_with_permissions_runs_setup_steps(client):
    make_form()
    _staff(client, VIEW_APP + CONNECT_PAGES + TEST_LEAD)
    responses.get(f"{BASE}/me/accounts", json=ACCOUNTS)
    responses.post(f"{BASE}/222/test_leads", json={"id": "999"})
    with mock.patch("leadhook.meta.setup.connect_page", return_value=("Shop", 1)) as connect:
        client.post(SETUP, {"action": "connect_pages", "page_ids": ["111"]})
    connect.assert_called_once()
    assert "Shop" in client.get(SETUP, {"load": "pages"}).content.decode()
    response = client.post(SETUP, {"action": "test_lead", "form_id": "222"}, follow=True)
    assert "Lead di prova richiesto" in response.content.decode()
