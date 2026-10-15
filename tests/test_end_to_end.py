import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import responses
from django.urls import reverse

from leadhook.destinations.models import Delivery, Destination
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_form
from tests.fixtures import GRAPH_LEAD, leadgen_payload, sign


@pytest.fixture
def receiver():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(
                {
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "raw": body,
                    "json": json.loads(body),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/hook", received=received)
    server.shutdown()


@pytest.fixture
def setup(settings, receiver):
    settings.ALLOW_PRIVATE_DESTINATIONS = True
    form = make_form()
    destination = make_destination(
        url=receiver.url,
        auth_type=Destination.AuthType.HMAC,
        secret="hmac-key",
        success_condition="response.success == true",
    )
    form.page.default_destinations.add(destination)
    return SimpleNamespace(app=form.page.meta_app, receiver=receiver)


def _post_webhook(client, app, body: bytes):
    return client.post(
        reverse("meta-webhook", args=[app.webhook_uuid]),
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sign(body),
    )


@responses.activate
def test_end_to_end_lead_delivery(client, setup, django_capture_on_commit_callbacks):
    responses.add_passthru("http://127.0.0.1")
    responses.get("https://graph.facebook.com/v21.0/333", json=GRAPH_LEAD)
    body = json.dumps(leadgen_payload()).encode()

    with django_capture_on_commit_callbacks(execute=True):
        assert _post_webhook(client, setup.app, body).status_code == 200

    assert len(setup.receiver.received) == 1
    request = setup.receiver.received[0]
    payload = request["json"]
    assert payload["id"] == "333"
    assert payload["data"]["full_name"] == "Jane Doe"
    assert payload["form"]["name"] == "Test form"
    assert payload["_leadhook"]["attempt"] == 1

    timestamp = request["headers"]["x-leadhook-timestamp"]
    signed = f"{timestamp}.".encode() + request["raw"]
    expected = hmac.new(b"hmac-key", signed, hashlib.sha256).hexdigest()
    assert request["headers"]["x-leadhook-signature"] == f"sha256={expected}"
    assert request["headers"]["idempotency-key"] == "333"

    assert Lead.objects.get().status == Lead.Status.DELIVERED
    assert Delivery.objects.get().status == Delivery.Status.SUCCESS


@responses.activate
def test_end_to_end_duplicate_webhook(client, setup, django_capture_on_commit_callbacks):
    responses.add_passthru("http://127.0.0.1")
    responses.get("https://graph.facebook.com/v21.0/333", json=GRAPH_LEAD)
    body = json.dumps(leadgen_payload()).encode()

    with django_capture_on_commit_callbacks(execute=True):
        _post_webhook(client, setup.app, body)
    with django_capture_on_commit_callbacks(execute=True):
        _post_webhook(client, setup.app, body)

    assert len(setup.receiver.received) == 1
