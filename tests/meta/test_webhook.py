import json
from unittest import mock

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


def test_bad_signature_is_stored_but_not_processed(client, django_capture_on_commit_callbacks):
    app = make_meta_app()
    body = json.dumps(leadgen_payload()).encode()
    with mock.patch("leadhook.meta.views.process_event.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                _url(app),
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=sign(body, secret="attacker"),
            )
    assert response.status_code == 200
    event = WebhookEvent.objects.get()
    assert not event.signature_ok
    assert event.error == "Invalid signature"
    delay.assert_not_called()


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
