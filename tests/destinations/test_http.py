import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests
import responses

from leadhook.destinations.http import (
    BlockedDestination,
    build_headers,
    check_destination_url,
    parse_retry_after,
    send_request,
)
from leadhook.destinations.models import Destination
from tests.factories import make_destination

URL = "https://hooks.example.com/lead"
HEADERS = {"Content-Type": "application/json"}


def test_bearer_header():
    destination = make_destination(auth_type=Destination.AuthType.BEARER, secret="tok")
    headers = build_headers(destination, body="{}", timestamp=1, idempotency_key="333")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Idempotency-Key"] == "333"
    assert headers["X-LeadHook-Timestamp"] == "1"
    assert headers["Content-Type"] == "application/json"
    assert "X-LeadHook-Signature" not in headers


def test_custom_header_auth():
    destination = make_destination(
        auth_type=Destination.AuthType.HEADER, auth_header_name="X-N8N-Key", secret="k"
    )
    headers = build_headers(destination, body="{}", timestamp=1, idempotency_key="1")
    assert headers["X-N8N-Key"] == "k"


def test_hmac_signature_can_be_verified_by_receiver():
    destination = make_destination(auth_type=Destination.AuthType.HMAC, secret="hmac-key")
    headers = build_headers(destination, body='{"a":1}', timestamp=1760517000, idempotency_key="1")
    expected = hmac.new(b"hmac-key", b'1760517000.{"a":1}', hashlib.sha256).hexdigest()
    assert headers["X-LeadHook-Signature"] == f"sha256={expected}"


def test_extra_headers_cannot_override_authentication():
    destination = make_destination(
        auth_type=Destination.AuthType.BEARER,
        secret="tok",
        extra_headers={"Authorization": "Bearer other", "X-Source": "leadhook"},
    )
    headers = build_headers(destination, body="{}", timestamp=1, idempotency_key="1")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Source"] == "leadhook"


@responses.activate
def test_2xx_is_success():
    responses.post(URL, json={"ok": True})
    result = send_request(make_destination(), "{}", HEADERS)
    assert (result.outcome, result.status_code) == ("success", 200)
    assert result.response_body == '{"ok": true}'


@responses.activate
@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_retryable_statuses(status):
    responses.post(URL, status=status)
    result = send_request(make_destination(), "{}", HEADERS)
    assert result.outcome == "retry"
    assert result.status_code == status


@responses.activate
@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_client_errors_fail_without_retry(status):
    responses.post(URL, status=status)
    assert send_request(make_destination(), "{}", HEADERS).outcome == "fail"


@responses.activate
def test_redirects_are_not_followed():
    responses.post(URL, status=302, headers={"Location": "http://169.254.169.254/"})
    result = send_request(make_destination(), "{}", HEADERS)
    assert result.outcome == "fail"
    assert len(responses.calls) == 1


@responses.activate
def test_retry_after_is_reported():
    responses.post(URL, status=429, headers={"Retry-After": "120"})
    assert send_request(make_destination(), "{}", HEADERS).retry_after == 120


@responses.activate
def test_network_error_is_retried():
    responses.post(URL, body=requests.ConnectionError("connection refused"))
    result = send_request(make_destination(), "{}", HEADERS)
    assert result.outcome == "retry"
    assert "connection refused" in result.error


@responses.activate
def test_response_body_is_truncated():
    responses.post(URL, body="x" * 10000)
    assert len(send_request(make_destination(), "{}", HEADERS).response_body) == 4096


@responses.activate
def test_success_condition_met():
    responses.post(URL, json={"success": True})
    destination = make_destination(success_condition="response.success == true")
    assert send_request(destination, "{}", HEADERS).outcome == "success"


@responses.activate
def test_success_condition_not_met_is_retried():
    responses.post(URL, json={"success": False, "message": "duplicate"})
    destination = make_destination(success_condition="response.success == true")
    result = send_request(destination, "{}", HEADERS)
    assert result.outcome == "retry"
    assert result.error == "Success condition not met"


@responses.activate
def test_success_condition_with_html_body_is_retried_not_crashing():
    responses.post(URL, body="<html>OK</html>", content_type="text/html")
    destination = make_destination(success_condition="response.success == true")
    assert send_request(destination, "{}", HEADERS).outcome == "retry"


@responses.activate
def test_broken_success_condition_fails():
    responses.post(URL, json={})
    destination = make_destination(success_condition="response.success ==")
    assert send_request(destination, "{}", HEADERS).outcome == "fail"


def test_private_addresses_are_blocked(fake_dns):
    fake_dns["internal.test"] = "10.0.0.5"
    with pytest.raises(BlockedDestination):
        check_destination_url("https://internal.test/hook")
    urls = [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "http://192.168.1.10/",
    ]
    for url in urls:
        with pytest.raises(BlockedDestination):
            check_destination_url(url)


def test_public_addresses_are_allowed():
    check_destination_url(URL)


def test_private_addresses_allowed_when_configured(settings, fake_dns):
    settings.ALLOW_PRIVATE_DESTINATIONS = True
    fake_dns["n8n"] = "172.18.0.4"
    check_destination_url("http://n8n:5678/webhook/x")


def test_non_http_schemes_are_always_blocked(settings):
    settings.ALLOW_PRIVATE_DESTINATIONS = True
    with pytest.raises(BlockedDestination):
        check_destination_url("file:///etc/passwd")


def test_blocked_destination_fails_without_sending(fake_dns):
    fake_dns["internal.test"] = "10.0.0.5"
    destination = make_destination(url="https://internal.test/hook")
    result = send_request(destination, "{}", HEADERS)
    assert result.outcome == "fail"
    assert "non-public" in result.error


def test_parse_retry_after():
    assert parse_retry_after("120") == 120
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    future = datetime.now(UTC) + timedelta(minutes=10)
    assert 590 <= parse_retry_after(format_datetime(future, usegmt=True)) <= 600


@responses.activate
def test_proxy_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    seen = []
    original = requests.Session.__init__

    def spy(self, *args, **kwargs):
        original(self, *args, **kwargs)
        seen.append(self)

    monkeypatch.setattr(requests.Session, "__init__", spy)
    responses.post(URL, json={"ok": True})
    assert send_request(make_destination(), "{}", HEADERS).outcome == "success"
    assert seen
    assert all(session.trust_env is False for session in seen)


def test_parse_retry_after_rejects_non_ascii_digits():
    assert parse_retry_after("²") is None
    assert parse_retry_after("١٢") is None


def test_parse_retry_after_naive_date_is_utc_and_far_future_is_capped():
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 -0000") == 0
    assert parse_retry_after("Fri, 01 Jan 2100 00:00:00 GMT") == 86400
    assert parse_retry_after("99999999") == 86400


def test_invalid_port_fails_without_raising():
    result = send_request(make_destination(url="https://example.com:99999/"), "{}", HEADERS)
    assert result.outcome == "fail"
    assert "Invalid destination URL" in result.error


def test_bad_idna_host_fails_without_raising(monkeypatch):
    def resolve(host, port):
        raise UnicodeError("label too long")

    monkeypatch.setattr("leadhook.destinations.http.resolve_host", resolve)
    result = send_request(make_destination(url="https://bad.example/"), "{}", HEADERS)
    assert result.outcome == "fail"
    assert "Invalid destination host" in result.error


def test_blocked_message_does_not_leak_credentials_or_path(fake_dns):
    fake_dns["internal.test"] = "10.0.0.5"
    url = "https://user:s3cret@internal.test/hook?token=abc"
    result = send_request(make_destination(url=url), "{}", HEADERS)
    assert result.outcome == "fail"
    assert "non-public" in result.error
    assert "s3cret" not in result.error
    assert "token=abc" not in result.error
    with pytest.raises(BlockedDestination) as info:
        check_destination_url("ftp://user:s3cret@example.com/x")
    assert "s3cret" not in str(info.value)
