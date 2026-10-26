import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from leadhook.leads.models import WebhookEvent
from tests.factories import make_meta_app
from tests.fixtures import leadgen_payload, sign


def _url(app) -> str:
    return reverse("meta-webhook", args=[app.webhook_uuid])


def test_webhook_url_uses_public_base_url():
    app = make_meta_app()
    assert app.webhook_url() == f"https://leadhook.test/webhooks/meta/{app.webhook_uuid}/"


def test_verification_challenge(client):
    app = make_meta_app()
    response = client.get(
        _url(app),
        {"hub.mode": "subscribe", "hub.verify_token": app.verify_token, "hub.challenge": "42"},
    )
    assert response.status_code == 200
    assert response.content == b"42"
    assert response["Content-Type"].startswith("text/plain")


def test_verification_with_wrong_token_is_rejected(client):
    app = make_meta_app()
    response = client.get(
        _url(app), {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"}
    )
    assert response.status_code == 403


def test_unknown_webhook_uuid_returns_404(client):
    response = client.post(
        "/webhooks/meta/00000000-0000-0000-0000-000000000000/",
        data=b"{}",
        content_type="application/json",
    )
    assert response.status_code == 404


def test_signed_event_is_stored_and_processed(django_capture_on_commit_callbacks):
    app = make_meta_app()
    body = json.dumps(leadgen_payload()).encode()
    csrf_client = Client(enforce_csrf_checks=True)
    with mock.patch("leadhook.meta.views.process_event.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = csrf_client.post(
                _url(app),
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=sign(body),
            )
    assert response.status_code == 200
    event = WebhookEvent.objects.get()
    assert event.signature_ok
    assert event.meta_app == app
    assert event.payload == leadgen_payload()
    delay.assert_called_once_with(event.pk)


@pytest.mark.parametrize("signature", [None, sign(b"{}", secret="attacker"), "sha256=\u00e9"])
def test_bad_or_missing_signature_is_not_stored(client, signature, caplog):
    app = make_meta_app()
    body = json.dumps({"padding": "x" * 10_000}).encode()
    headers = {} if signature is None else {"HTTP_X_HUB_SIGNATURE_256": signature}
    with mock.patch("leadhook.meta.views.json.loads") as loads:
        for _ in range(3):
            response = client.post(_url(app), data=body, content_type="application/json", **headers)
            assert response.status_code == 200
    loads.assert_not_called()  # never parsed before authentication
    assert not WebhookEvent.objects.exists()
    # Throttled audit: one warning for the burst, no payload in it.
    warnings = [r for r in caplog.records if "invalid signature" in r.getMessage()]
    assert len(warnings) == 1
    assert "padding" not in warnings[0].getMessage()


def test_non_json_body_is_stored_without_crashing(client, django_capture_on_commit_callbacks):
    app = make_meta_app()
    body = b"not json"
    with mock.patch("leadhook.meta.views.process_event.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                _url(app),
                data=body,
                content_type="text/plain",
                HTTP_X_HUB_SIGNATURE_256=sign(body),
            )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().payload == {"unparseable_body": "not json"}


def test_other_methods_are_not_allowed(client):
    app = make_meta_app()
    assert client.put(_url(app)).status_code == 405


def test_non_ascii_verify_token_is_forbidden(client):
    app = make_meta_app()
    response = client.get(
        _url(app), {"hub.mode": "subscribe", "hub.verify_token": "\u00e9", "hub.challenge": "42"}
    )
    assert response.status_code == 403


def test_nul_characters_are_stripped_from_payload(client):
    app = make_meta_app()
    body = json.dumps({"a": ["x\u0000y"], "b\u0000": {"c": "d\u0000"}}).encode()
    response = client.post(
        _url(app), data=body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body)
    )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().payload == {"a": ["xy"], "b": {"c": "d"}}


def test_nul_in_unparseable_body_is_stripped(client):
    app = make_meta_app()
    body = b"a\x00b"
    response = client.post(
        _url(app), data=body, content_type="text/plain", HTTP_X_HUB_SIGNATURE_256=sign(body)
    )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().payload == {"unparseable_body": "ab"}


def test_broker_failure_still_returns_200(client, django_capture_on_commit_callbacks):
    app = make_meta_app()
    body = json.dumps(leadgen_payload()).encode()
    with mock.patch("leadhook.meta.views.process_event.delay", side_effect=OSError("down")):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                _url(app),
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=sign(body),
            )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().processed_at is None


def test_lone_surrogates_are_replaced_in_payload(client):
    app = make_meta_app()
    body = b'{"a": ["x\\ud800y"], "k\\udfff": "\\ud83d\\ude00"}'
    response = client.post(
        _url(app), data=body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body)
    )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().payload == {"a": ["x�y"], "k�": "\U0001f600"}
