"""Outbound webhook requests: headers, SSRF protection and outcome classification."""

import hashlib
import hmac
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests
from django.conf import settings

import leadhook

from .models import Destination
from .templating import TemplateError, evaluate_condition

RESPONSE_BODY_LIMIT = 4096
RETRYABLE_STATUS_CODES = {408, 429}
MAX_RETRY_AFTER = 86400
# Longer digit strings are far above MAX_RETRY_AFTER anyway (and int() rejects > 4300 digits).
MAX_RETRY_AFTER_DIGITS = 10


@dataclass
class SendResult:
    outcome: str  # "success" | "retry" | "fail"
    status_code: int | None = None
    error: str = ""
    response_body: str = ""
    retry_after: int | None = None


class BlockedDestination(Exception):
    pass


def resolve_host(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def check_destination_url(url: str) -> None:
    """Raise BlockedDestination for non-HTTP URLs or (unless allowed) non-public addresses.

    DNS failures raise OSError, which callers treat as a retryable network error.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise BlockedDestination("Invalid destination URL (malformed host or port)") from None
    # Never echo credentials, path or query from the URL in messages.
    shown = f"{parts.scheme}://{hostname}"
    if parts.scheme not in ("http", "https") or not hostname:
        raise BlockedDestination(f"Unsupported destination URL ({shown})")
    if settings.ALLOW_PRIVATE_DESTINATIONS:
        return
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = resolve_host(hostname, port)
    except UnicodeError:
        raise BlockedDestination(f"Invalid destination host name ({shown})") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise BlockedDestination(
                f"Destination {shown} resolves to a non-public address ({ip}). "
                "Set ALLOW_PRIVATE_DESTINATIONS=true to allow it."
            )


def build_headers(
    destination: Destination, *, body: str, timestamp: int, idempotency_key: str
) -> dict[str, str]:
    headers = {str(k): str(v) for k, v in (destination.extra_headers or {}).items()}
    headers.update(
        {
            "Content-Type": "application/json",
            "User-Agent": f"LeadHook/{leadhook.__version__}",
            "Idempotency-Key": idempotency_key,
            "X-LeadHook-Timestamp": str(timestamp),
        }
    )
    auth = destination.auth_type
    if auth == Destination.AuthType.BEARER:
        headers["Authorization"] = f"Bearer {destination.secret}"
    elif auth == Destination.AuthType.HEADER:
        headers[destination.auth_header_name] = destination.secret
    elif auth == Destination.AuthType.HMAC:
        digest = hmac.new(
            destination.secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
        ).hexdigest()
        headers["X-LeadHook-Signature"] = f"sha256={digest}"
    return headers


def parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d+", value, re.ASCII):
        if len(value.lstrip("0")) > MAX_RETRY_AFTER_DIGITS:
            return MAX_RETRY_AFTER
        return min(int(value), MAX_RETRY_AFTER)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return min(MAX_RETRY_AFTER, max(0, int((moment - datetime.now(UTC)).total_seconds())))


def send_request(destination: Destination, body: str, headers: dict) -> SendResult:
    try:
        check_destination_url(destination.url)
    except BlockedDestination as exc:
        return SendResult("fail", error=str(exc))
    except OSError as exc:
        return SendResult("retry", error=f"DNS resolution failed: {exc}")

    try:
        with requests.Session() as session:
            # Ignore HTTP(S)_PROXY and .netrc so the request goes where the SSRF guard checked.
            session.trust_env = False
            response = session.request(
                destination.method,
                destination.url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=destination.timeout_s,
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        return SendResult("retry", error=f"{type(exc).__name__}: {exc}")

    status = response.status_code
    text = response.text[:RESPONSE_BODY_LIMIT]

    if 200 <= status < 300:
        if destination.success_condition.strip():
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            try:
                ok = evaluate_condition(
                    destination.success_condition, response=parsed, status_code=status
                )
            except TemplateError as exc:
                return SendResult("fail", status, str(exc), text)
            if not ok:
                return SendResult("retry", status, "Success condition not met", text)
        return SendResult("success", status, "", text)

    retry_after = parse_retry_after(response.headers.get("Retry-After"))
    if status >= 500 or status in RETRYABLE_STATUS_CODES:
        return SendResult("retry", status, f"HTTP {status}", text, retry_after)
    return SendResult("fail", status, f"HTTP {status}", text)
