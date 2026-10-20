from urllib.parse import parse_qs, urlparse

import responses
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from responses import matchers

from leadhook.meta.models import MetaApp
from tests.factories import make_meta_app

BASE = "https://graph.facebook.com/v21.0"
LOGIN = "/admin/setup/facebook/"
CALLBACK = "/admin/setup/facebook/callback/"
PUBLIC = urlparse(settings.PUBLIC_BASE_URL)
# Requests as if made on PUBLIC_BASE_URL, where Facebook sends the user back.
HOST = {"HTTP_HOST": PUBLIC.netloc, "secure": PUBLIC.scheme == "https"}
ACCOUNTS = {"data": [{"id": "111", "name": "Shop", "access_token": "page-secret-token"}]}


def _messages(response) -> str:
    return " ".join(str(m) for m in get_messages(response.wsgi_request))


def _start(client) -> str:
    response = client.get(LOGIN, **HOST)
    assert response.status_code == 302
    return parse_qs(urlparse(response["Location"]).query)["state"][0]


def test_login_redirects_to_facebook_dialog_with_config(admin_client):
    make_meta_app(access_token="", login_config_id="cfg-1")
    location = admin_client.get(LOGIN, **HOST)["Location"]
    assert location.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["1000"]
    assert query["config_id"] == ["cfg-1"]
    assert query["redirect_uri"] == [settings.PUBLIC_BASE_URL + CALLBACK]
    assert "scope" not in query


def test_login_without_config_asks_for_scopes(admin_client):
    make_meta_app(access_token="")
    query = parse_qs(urlparse(admin_client.get(LOGIN, **HOST)["Location"]).query)
    assert "leads_retrieval" in query["scope"][0]


@responses.activate
def test_callback_stores_system_user_token_registers_webhook_and_lists_pages(admin_client):
    app = make_meta_app(access_token="")
    state = _start(admin_client)
    responses.get(
        f"{BASE}/oauth/access_token",
        match=[matchers.query_param_matcher({"code": "the-code"}, strict_match=False)],
        json={"access_token": "suat-token", "token_type": "bearer"},
    )
    responses.get(f"{BASE}/debug_token", json={"data": {"is_valid": True, "expires_at": 0}})
    responses.get(f"{BASE}/1000/subscriptions", json={"data": []})
    subscribe = responses.post(f"{BASE}/1000/subscriptions", json={"success": True})
    responses.get(f"{BASE}/me/accounts", json=ACCOUNTS)

    response = admin_client.get(CALLBACK, {"state": state, "code": "the-code"}, **HOST)
    assert response["Location"] == "/admin/setup/?load=pages#pages"
    assert "Webhook registered" in _messages(response)
    app.refresh_from_db()
    assert app.access_token == "suat-token"
    assert app.token_type == MetaApp.TokenType.SYSTEM_USER
    assert app.token_expires_at is None
    sent = parse_qs(urlparse(subscribe.calls[0].request.url).query)
    assert sent["callback_url"] == [app.webhook_url()]
    assert sent["verify_token"] == [app.verify_token]
    assert sent["fields"] == ["leadgen"]

    html = admin_client.get("/admin/setup/?load=pages").content.decode()
    assert 'value="111"' in html  # pages offered without another click
    assert "page-secret-token" not in html


@responses.activate
def test_callback_exchanges_user_token_for_long_lived_one(admin_client):
    app = make_meta_app(access_token="")
    state = _start(admin_client)
    responses.get(
        f"{BASE}/oauth/access_token",
        match=[matchers.query_param_matcher({"code": "the-code"}, strict_match=False)],
        json={"access_token": "short-token"},
    )
    responses.get(f"{BASE}/debug_token", json={"data": {"is_valid": True, "expires_at": 1}})
    responses.get(
        f"{BASE}/oauth/access_token",
        match=[
            matchers.query_param_matcher({"fb_exchange_token": "short-token"}, strict_match=False)
        ],
        json={"access_token": "long-token", "expires_in": 5184000},
    )
    responses.get(f"{BASE}/1000/subscriptions", json={"data": []})
    responses.post(
        f"{BASE}/1000/subscriptions",
        status=400,
        json={"error": {"message": "unreachable", "code": 2200}},
    )

    response = admin_client.get(CALLBACK, {"state": state, "code": "the-code"}, **HOST)
    assert "could not be registered automatically" in _messages(response)
    app.refresh_from_db()
    assert app.access_token == "long-token"
    assert app.token_type == MetaApp.TokenType.USER
    assert app.token_expires_at is not None


def test_callback_rejects_wrong_state(admin_client):
    app = make_meta_app(access_token="")
    _start(admin_client)
    response = admin_client.get(CALLBACK, {"state": "forged", "code": "the-code"}, **HOST)
    assert "did not start here" in _messages(response)
    app.refresh_from_db()
    assert app.access_token == ""


def test_callback_reports_cancelled_login(admin_client):
    make_meta_app(access_token="")
    state = _start(admin_client)
    response = admin_client.get(CALLBACK, {"state": state, "error": "access_denied"}, **HOST)
    assert "was not connected" in _messages(response)


def test_login_needs_change_permission(client):
    make_meta_app()
    client.force_login(User.objects.create_user("viewer", password="x", is_staff=True))
    assert client.get(LOGIN).status_code == 403
    assert client.get(CALLBACK).status_code == 403


@responses.activate
def test_callback_keeps_a_webhook_that_points_elsewhere(admin_client):
    make_meta_app(access_token="")
    state = _start(admin_client)
    responses.get(f"{BASE}/oauth/access_token", json={"access_token": "suat-token"})
    responses.get(f"{BASE}/debug_token", json={"data": {"is_valid": True, "expires_at": 0}})
    responses.get(
        f"{BASE}/1000/subscriptions",
        json={"data": [{"object": "page", "callback_url": "https://other.example/hook"}]},
    )
    subscribe = responses.post(f"{BASE}/1000/subscriptions", json={"success": True})

    response = admin_client.get(CALLBACK, {"state": state, "code": "the-code"}, **HOST)
    assert "already sends leads to https://other.example/hook" in _messages(response)
    assert not subscribe.calls


def test_login_moves_to_public_host_first(admin_client):
    make_meta_app(access_token="")
    response = admin_client.get(LOGIN, HTTP_HOST="testserver")
    assert response["Location"] == settings.PUBLIC_BASE_URL + LOGIN
