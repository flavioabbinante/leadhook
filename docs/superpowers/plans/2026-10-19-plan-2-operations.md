# LeadHook Plan 2 — Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LeadHook safe to run unattended: recover leads whose webhook never arrived, keep pages and forms in sync, alert operators by email, expose `/health/`, purge old personal data, re-dispatch work lost by the broker, show a dashboard, and guide first-time setup in the admin. Close or drop every item of the Plan 1 review backlog.

**Architecture:** Same Django project and apps as Plan 1. New periodic Celery tasks are thin wrappers around service functions, scheduled with `CELERY_BEAT_SCHEDULE` in `settings.py`. Alerts and the beat heartbeat use the Django cache, backed by the existing Redis. The dashboard is an inclusion tag on the admin index. The setup wizard is one admin-wrapped view.

**Tech Stack:** Unchanged from Plan 1. No new dependencies: Django's built-in `RedisCache` uses the `redis` package already installed by `celery[redis]`. `urllib3` (2.x, installed by `requests`) is used directly for the connection guard.

**Spec:** `docs/superpowers/specs/2026-10-15-leadhook-design.md` (§4 rules, §5 periodic tasks, §8 error handling, §9 monitoring, §10 onboarding, §11 configuration).

**Previous plan:** `docs/superpowers/plans/2026-10-15-leadhook-core-pipeline.md`. Its Global Constraints still apply; they are repeated below where Plan 2 relies on them.

## Global Constraints

- English only: code, identifiers, comments, admin UI strings, docs, commit messages.
- Logs, alert emails and stored error messages never contain lead personal data (IDs only) and never contain access tokens, app secrets, destination secrets or destination URL paths/queries.
- Alerts are sent only to `ALERT_EMAILS`, at most one email per alert kind every 15 minutes (`ALERT_THROTTLE_SECONDS = 900`). An alert failure must never break the pipeline step that raised it.
- Periodic tasks are idempotent: running one twice, or two copies concurrently, must not create duplicate leads or duplicate sends beyond what the delivery lease already allows.
- Each task adds its own beat entry to `CELERY_BEAT_SCHEDULE` in `leadhook/settings.py`; `tests/test_settings.py::test_beat_schedule_tasks_exist` checks every entry points to a real task.
- Migrations are created with `DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations <app> --name <name>`. Plan 2 creates exactly three: `meta/0003_form_last_reconciled_at` (Task 4), `meta/0004_page_silence_alerted_at` (Task 5), `leads/0002_lead_purged_at` (Task 7). Execute tasks in order so the numbering holds.
- Every task ends green on: `uv run ruff check . && uv run ruff format --check . && uv run pytest`. Run `uv run ruff format .` first: the snippets below are not always wrapped exactly as ruff wants.
- Commit after every task; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Reconciliation finds a lead the webhook already delivered** → no second delivery. Pinned by `test_reconcile_skips_known_leads` (Task 4).
2. **Sweeper runs while a delivery is being sent or waits for its retry** → it is not dispatched again. Pinned by `test_sweep_skips_leased_and_scheduled_deliveries` (Task 6).
3. **Retention purge** → personal data is cleared, metadata kept, and a purged lead can never be sent again with empty data. Pinned by `test_purge_clears_personal_data_keeps_metadata`, `test_resend_refuses_purged_lead`, `test_deliver_fails_pending_delivery_of_purged_lead` (Task 7).
4. **DNS rebinding:** the host passes the SSRF pre-check but the connection lands on a private address → the request is not sent. Pinned by `test_connection_to_private_peer_is_blocked_after_dns_check` (Task 8).
5. **A slow send outlives its lease and another worker takes over** → the late result does not overwrite the newer one. Pinned by `test_late_result_after_lease_loss_is_discarded` (Task 8).
6. **Secrets in error text:** `debug_token` failures and destination network errors → messages contain neither tokens nor URL path/query. Pinned by `test_debug_token_error_redacts_both_tokens` (Task 3) and `test_network_error_does_not_leak_url_secrets` (Task 8).
7. **Alerts** → throttled per kind, IDs only. Pinned by `test_alerts_are_throttled_per_kind`, `test_failed_delivery_alert_has_ids_only` (Task 1).

## Backlog triage (`docs/superpowers/plans/2026-10-15-plan-2-backlog.md`)

| Backlog item | Decision |
|---|---|
| Malformed `LEADHOOK_ENCRYPTION_KEY` fails only on first use | Already fixed in `cef7292` (validated in `settings.py`) |
| `InvalidToken` (wrong/rotated key) → raw traceback in admin | Task 9 |
| `parsing.py` not robust to nameless field / malformed `created_time` | Task 9 (non-dict `change.value` is already skipped) |
| Encrypted-at-rest tests only assert `!= plaintext` | Task 9 |
| DNS rebinding TOCTOU in SSRF guard | Task 8 |
| `response.text` read fully before truncation | Task 8 |
| `leadgen_id` not URL-quoted | Task 3 |
| Template recompiled per send | Dropped (ruling in backlog) |
| Test gaps: `AuthType.NONE`, other 3xx, DNS `OSError` retry, filter failure | Task 8 |
| `_get_or_create_form` swallows `GraphError` (blank form never re-synced) | Task 5 (`sync_forms` refills it) |
| Fetch exhaustion is silent | Task 1 (alert), Task 6 (hourly re-fetch for 7 days) |
| Test gaps: task-level token error, `Retry-After` via `deliver`, fetch exhaustion | Task 1 (token error, exhaustion alert), Task 8 (`Retry-After`) |
| Filler test `test_destination_kind_is_webhook_by_default` | Dropped (ruling in backlog) |
| `ALLOW_PRIVATE_DESTINATIONS` + bad IDNA → retry instead of fail | Task 8 |
| `Retry-After` with > 4300 digits | Already fixed (`MAX_RETRY_AFTER_DIGITS`) |
| `"scheme://None"` message | Dropped (ruling in backlog) |
| `trust_env=False` ignores `REQUESTS_CA_BUNDLE` | Task 8 |
| Stored-but-unenqueued webhook events need a sweeper | Task 6 |
| Lone surrogate `\ud800` in JSON | Already fixed (`_clean_strings` in `meta/views.py`) |
| Broker-failure test does not assert the log | Dropped (ruling in backlog) |
| `DestinationAdmin` stashes `preview_lead` on the shared admin instance | Task 10 |
| `extra_headers` shown in clear text | Already fixed (help text warns) |
| Secret-masking tests only for `MetaApp` | Task 9 |
| Dockerfile duplicate `chown` layer, `.dockerignore` | Already fixed in `09d0f5c` |
| Worker/beat may start before `migrate` | Task 12 |
| README "up to 24 hours" wording | Already fixed ("about a day and a half") |
| e2e `server_close` missing | Dropped (ruling in backlog) |
| Crashed mid-send delivery stays `PENDING` after lease expiry | Task 6 |
| Lease overrun overwrites a newer result | Task 8 |
| No exhaustion test under lease | Task 8 |
| URL secrets in `last_error` | Task 8 |

Plan 1 "out of this plan" items: `reconcile_leads` (Task 4), `sync_forms`, `check_pages`, `check_silence` (Task 5), `purge_old_data` (Task 7), `heartbeat` + `/health/` (Task 2), email alerts (Task 1), dashboard (Task 10), onboarding wizard with test lead (Task 11), "route unrouted leads?" prompt (Task 10), stale delivery sweeper (Task 6), lead chooser for payload preview (Task 10), README Meta setup guide (Task 12). **Dropped:** README screenshots (cannot be produced by agents; add by hand later).

---

## File Structure (new and changed files)

```
leadhook/
├── settings.py                 + CACHES, email, alerts, retention, CA bundle, beat schedule, middleware
├── urls.py                     + /health/, /admin/setup/, admin index template
├── core/
│   ├── alerts.py               NEW  send_alert(), AlertKind
│   ├── health.py               NEW  heartbeat helpers
│   ├── middleware.py           NEW  readable error for a wrong encryption key
│   ├── tasks.py                NEW  send_alert_email, heartbeat
│   └── views.py                NEW  health
├── meta/
│   ├── graph.py                + POST, pagination, quoting, app token, new endpoints
│   ├── services.py             NEW  record_page_error, fit_to_field, sync_page_forms, check_*, connect_page
│   ├── tasks.py                NEW  sync_forms, check_pages, check_silence
│   ├── setup.py                NEW  setup wizard view
│   ├── admin.py                + unrouted-leads prompt
│   └── templates/admin/leadhook_setup.html   NEW
├── leads/
│   ├── parsing.py              hardened
│   ├── services.py             + store_lead_details, reconcile_form
│   ├── retention.py            NEW  purge_old_data()
│   ├── tasks.py                + reconcile_leads, purge_old_data task, exhaustion alert
│   ├── dashboard.py            NEW  dashboard_stats()
│   ├── templatetags/__init__.py, templatetags/leadhook_dashboard.py   NEW
│   ├── templates/admin/leadhook_index.html, templates/leads/dashboard.html   NEW
│   └── admin.py                + purged guard
└── destinations/
    ├── http.py                 + connection guard, streamed read, URL redaction, CA bundle
    ├── services.py             + alerts, lease-guarded saves, purged guard
    ├── sweep.py                NEW  stuck-work queries
    ├── tasks.py                + sweep_stuck_work, lease-aware retry, purged guard
    └── admin.py                + thread-safe preview with lead chooser
tests/
├── conftest.py                 + cache cleared per test
├── settings.py                 + locmem cache, ALERT_EMAILS
├── core/test_alerts.py, core/test_health.py, core/test_middleware.py      NEW
├── meta/test_services.py, meta/test_tasks.py, meta/test_setup.py          NEW
├── leads/test_reconcile.py, leads/test_retention.py, leads/test_dashboard.py   NEW
└── destinations/test_sweep.py  NEW
```

---

### Task 1: Alerts, cache and email settings

**Files:**
- Modify: `leadhook/settings.py`, `tests/settings.py`, `tests/conftest.py`
- Create: `leadhook/core/alerts.py`, `leadhook/core/tasks.py`, `leadhook/meta/services.py`
- Modify: `leadhook/destinations/services.py` (`create_deliveries`, `mark_delivery_failed`), `leadhook/leads/services.py` (token error), `leadhook/leads/tasks.py` (fetch exhaustion)
- Test: `tests/core/test_alerts.py`, `tests/destinations/test_tasks.py`, `tests/leads/test_tasks.py`

**Interfaces:**
- Produces: `AlertKind` (`DELIVERY_FAILED`, `UNROUTED`, `PAGE_ERROR`, `FETCH_FAILED`, `SILENCE`); `send_alert(kind, subject, body) -> bool` (True if an email was queued); `record_page_error(page, message) -> None` (saves `Page.error`, alerts only when the message changed); settings `CACHES`, `EMAIL_*`, `DEFAULT_FROM_EMAIL`, `ALERT_EMAILS`, `ALERT_THROTTLE_SECONDS`, `LEAD_RETENTION_DAYS`, `SILENCE_ALERT_HOURS`.
- Alert call sites after this task: permanent delivery failure, lead becoming unrouted, page token rejected while fetching, fetch retries exhausted.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_alerts.py`:
```python
from types import SimpleNamespace

from leadhook.core.alerts import AlertKind, send_alert


def test_alert_is_emailed_to_alert_recipients(mailoutbox):
    assert send_alert(AlertKind.UNROUTED, "Lead not routed", "Lead 333 has no destination.")
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to == ["ops@example.com"]
    assert mail.subject == "[LeadHook] Lead not routed"
    assert "Lead 333 has no destination." in mail.body
    assert "https://leadhook.test/admin/" in mail.body


def test_alerts_are_throttled_per_kind(mailoutbox):
    assert send_alert(AlertKind.UNROUTED, "a", "a")
    assert not send_alert(AlertKind.UNROUTED, "b", "b")
    assert send_alert(AlertKind.DELIVERY_FAILED, "c", "c")
    assert [m.subject for m in mailoutbox] == ["[LeadHook] a", "[LeadHook] c"]


def test_no_recipients_means_no_alert(settings, mailoutbox):
    settings.ALERT_EMAILS = []
    assert not send_alert(AlertKind.UNROUTED, "a", "a")
    assert mailoutbox == []


def test_alert_failure_never_raises_and_does_not_throttle(monkeypatch, mailoutbox):
    def broker_down(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(
        "leadhook.core.alerts.send_alert_email", SimpleNamespace(delay=broker_down)
    )
    assert not send_alert(AlertKind.UNROUTED, "a", "a")
    monkeypatch.undo()
    assert send_alert(AlertKind.UNROUTED, "b", "b")
```

Append to `tests/destinations/test_tasks.py` (add `from django.core.cache import cache` to the imports):
```python
@responses.activate
def test_failed_delivery_alert_has_ids_only(mailoutbox):
    responses.post(HOOK, status=400)
    delivery = _pending_delivery()
    deliver(delivery.pk)
    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    assert "333" in body
    assert "HTTP 400" in body
    assert "Jane Doe" not in body


def test_unrouted_lead_alerts_once(mailoutbox):
    lead = make_lead()
    route_lead(lead.pk)
    cache.clear()  # rule out the throttle: the second run must stay quiet on its own
    route_lead(lead.pk)
    assert len(mailoutbox) == 1
    assert "333" in mailoutbox[0].body
```

Append to `tests/leads/test_tasks.py` (add `from leadhook.meta.models import Page` to the imports):
```python
@responses.activate
def test_fetch_lead_token_error_flags_page_and_alerts(mailoutbox):
    make_form()
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    responses.get(
        f"{BASE}/333",
        status=400,
        json={"error": {"message": "Error validating access token", "code": 190}},
    )
    fetch_lead(lead.pk)  # gives up without raising Retry
    lead.refresh_from_db()
    assert lead.status == Lead.Status.RECEIVED
    assert Page.objects.get(page_id="111").error.startswith("Meta rejected the page token")
    assert len(mailoutbox) == 1
    assert "page-token" not in mailoutbox[0].body


@responses.activate
def test_fetch_lead_exhaustion_alerts(mailoutbox):
    make_form()
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    responses.get(f"{BASE}/333", status=500)
    fetch_lead(lead.pk, attempt=len(FETCH_BACKOFF))
    assert len(mailoutbox) == 1
    assert "could not be read from Meta" in mailoutbox[0].body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_alerts.py tests/destinations/test_tasks.py tests/leads/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.core.alerts'`.

- [ ] **Step 3: Implement**

`leadhook/settings.py` — after the `CELERY_BROKER_TRANSPORT_OPTIONS` line add:
```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
        "KEY_PREFIX": "leadhook",
    }
}

# EMAIL_URL examples: smtp+tls://user:password@smtp.example.com:587, consolemail://
EMAIL_CONFIG = env.email_url("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="leadhook@localhost")
ALERT_EMAILS = env.list("ALERT_EMAILS", default=[])
ALERT_THROTTLE_SECONDS = 15 * 60

LEAD_RETENTION_DAYS = env.int("LEAD_RETENTION_DAYS", default=90)
SILENCE_ALERT_HOURS = env.int("SILENCE_ALERT_HOURS", default=24)
```

`tests/settings.py` — append:
```python
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ALERT_EMAILS = ["ops@example.com"]
```

`tests/conftest.py` — add `from django.core.cache import cache` and:
```python
@pytest.fixture(autouse=True)
def _clear_cache():
    """Alert throttles and the heartbeat live in the cache; start every test without them."""
    cache.clear()
```

`leadhook/core/tasks.py`:
```python
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


# smtplib errors are OSError subclasses: retry a few times, then give up (logged by Celery).
@shared_task(autoretry_for=(OSError,), retry_backoff=60, max_retries=3)
def send_alert_email(subject: str, body: str) -> None:
    send_mail(subject, body, None, settings.ALERT_EMAILS)
```

`leadhook/core/alerts.py`:
```python
"""Throttled email alerts for operators. Messages carry IDs only, never lead personal data."""

import logging
from enum import StrEnum

from django.conf import settings
from django.core.cache import cache

from .tasks import send_alert_email

logger = logging.getLogger(__name__)

FOOTER = (
    "\n\nOpen LeadHook: {url}/admin/\n"
    "Alerts of this kind are paused for 15 minutes after each email; "
    "the admin dashboard shows everything."
)


class AlertKind(StrEnum):
    DELIVERY_FAILED = "delivery_failed"
    UNROUTED = "unrouted"
    PAGE_ERROR = "page_error"
    FETCH_FAILED = "fetch_failed"
    SILENCE = "silence"


def send_alert(kind: AlertKind, subject: str, body: str) -> bool:
    """Queue an alert email unless one of the same kind went out in the throttle window."""
    if not settings.ALERT_EMAILS:
        return False
    key = f"alert:{kind}"
    try:
        if not cache.add(key, 1, timeout=settings.ALERT_THROTTLE_SECONDS):
            return False
        send_alert_email.delay(
            f"[LeadHook] {subject}", body + FOOTER.format(url=settings.PUBLIC_BASE_URL)
        )
    except Exception:
        # An alert must never break the pipeline step that raised it.
        logger.exception("Could not queue %s alert", kind)
        try:
            cache.delete(key)
        except Exception:
            pass
        return False
    return True
```

`leadhook/meta/services.py`:
```python
"""Page and form operations shared by tasks, admin and setup."""

from leadhook.core.alerts import AlertKind, send_alert

from .models import Page


def record_page_error(page: Page, message: str) -> None:
    """Store a page problem; alert only when it is new (repeated checks do not re-alert)."""
    is_new = page.error != message
    page.error = message
    page.save(update_fields=["error"])
    if is_new:
        send_alert(AlertKind.PAGE_ERROR, f"Problem with page {page.page_id}", f"{page}: {message}")
```

`leadhook/leads/services.py` — import `from leadhook.meta.services import record_page_error` and replace the token-error branch in `fetch_lead_details`:
```python
        if exc.is_token_error:
            record_page_error(page, f"Meta rejected the page token: {exc}")
            return FetchOutcome.GIVE_UP
```

`leadhook/leads/tasks.py` — import `from leadhook.core.alerts import AlertKind, send_alert` and replace the exhaustion branch in `fetch_lead`:
```python
        if attempt >= len(FETCH_BACKOFF):
            # Stays "received" with its error; the hourly sweep (and reconciliation) retry it.
            send_alert(
                AlertKind.FETCH_FAILED,
                "Lead could not be fetched",
                f"Lead {lead.leadgen_id} could not be read from Meta after {attempt + 1} "
                f"attempts: {lead.error}\nLeadHook keeps retrying it every hour for 7 days.",
            )
            return
```

`leadhook/destinations/services.py` — import `from leadhook.core.alerts import AlertKind, send_alert`.

In `create_deliveries`, replace the `if not destinations:` block:
```python
    if not destinations:
        if lead.status != Lead.Status.UNROUTED:
            send_alert(
                AlertKind.UNROUTED,
                "Lead not routed",
                f"Lead {lead.leadgen_id} (page {lead.page_external_id}, form "
                f"{lead.form_external_id}) has no active destination. Assign one to the form "
                "or a default to the page, then reprocess the lead.",
            )
        lead.status = Lead.Status.UNROUTED
        lead.save(update_fields=["status", "updated_at"])
        return []
```

At the end of `mark_delivery_failed` (after `delivery.save(...)`):
```python
    send_alert(
        AlertKind.DELIVERY_FAILED,
        f"Delivery to {delivery.destination} failed",
        f"Lead {delivery.lead.leadgen_id} could not be delivered to {delivery.destination} "
        f"({delivery.public_id}): {error}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass (the 8 new tests included).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(alerts): throttled email alerts for failures, unrouted leads and page errors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Heartbeat and `/health/`

**Files:**
- Create: `leadhook/core/health.py`, `leadhook/core/views.py`
- Modify: `leadhook/core/tasks.py`, `leadhook/settings.py`, `leadhook/urls.py`
- Test: `tests/core/test_health.py`, `tests/test_settings.py`

**Interfaces:**
- Produces: `record_heartbeat()`, `heartbeat_age() -> float | None` (raises if the cache is down), `heartbeat_is_fresh() -> bool` (never raises), `HEARTBEAT_MAX_AGE = 300`; task `leadhook.core.tasks.heartbeat`; `GET /health/` → JSON `{"database", "redis", "beat"}`, 200 when all `"ok"`, else 503; `CELERY_BEAT_SCHEDULE` dict.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_health.py`:
```python
import time

from django.core.cache import cache

from leadhook.core.health import HEARTBEAT_KEY, heartbeat_is_fresh
from leadhook.core.tasks import heartbeat


def test_health_is_503_without_heartbeat(client):
    response = client.get("/health/")
    assert response.status_code == 503
    assert response.json() == {"database": "ok", "redis": "ok", "beat": "stale"}


def test_health_is_200_after_heartbeat(client):
    heartbeat()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "redis": "ok", "beat": "ok"}


def test_old_heartbeat_is_stale(client):
    cache.set(HEARTBEAT_KEY, time.time() - 301)
    assert client.get("/health/").json()["beat"] == "stale"
    assert not heartbeat_is_fresh()


def test_cache_failure_is_reported(client, monkeypatch):
    def down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("leadhook.core.views.heartbeat_age", down)
    response = client.get("/health/")
    assert response.status_code == 503
    assert response.json()["redis"] == "error"
```

Append to `tests/test_settings.py` (add `import importlib` and `from django.conf import settings` if missing):
```python
def test_beat_schedule_tasks_exist():
    assert settings.CELERY_BEAT_SCHEDULE
    for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
        module, _, attr = entry["task"].rpartition(".")
        task = getattr(importlib.import_module(module), attr)
        assert callable(getattr(task, "delay", None)), name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_health.py tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.core.health'`.

- [ ] **Step 3: Implement**

`leadhook/core/health.py`:
```python
"""Beat heartbeat kept in the cache (Redis); read by /health/ and the dashboard."""

import time

from django.core.cache import cache

HEARTBEAT_KEY = "heartbeat"
HEARTBEAT_MAX_AGE = 300  # seconds; the heartbeat task runs every minute


def record_heartbeat() -> None:
    cache.set(HEARTBEAT_KEY, time.time(), timeout=None)


def heartbeat_age() -> float | None:
    """Seconds since the last heartbeat, None if never recorded. Raises if the cache is down."""
    beat = cache.get(HEARTBEAT_KEY)
    return None if beat is None else time.time() - beat


def heartbeat_is_fresh() -> bool:
    try:
        age = heartbeat_age()
    except Exception:
        return False
    return age is not None and age < HEARTBEAT_MAX_AGE
```

`leadhook/core/tasks.py` — add `from .health import record_heartbeat` and:
```python
@shared_task
def heartbeat() -> None:
    record_heartbeat()
```

`leadhook/core/views.py`:
```python
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .health import HEARTBEAT_MAX_AGE, heartbeat_age


@never_cache
@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Database, Redis and beat (via the heartbeat the worker records every minute)."""
    checks = {"database": "ok", "redis": "ok", "beat": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        checks["database"] = "error"
    try:
        age = heartbeat_age()
    except Exception:
        checks["redis"] = "error"
        age = None
    if age is None or age >= HEARTBEAT_MAX_AGE:
        checks["beat"] = "stale"
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(checks, status=200 if healthy else 503)
```

`leadhook/urls.py` — import `from leadhook.core.views import health` and add `path("health/", health, name="health"),` to `urlpatterns`.

`leadhook/settings.py`:
- After `SESSION_COOKIE_HTTPONLY = True` add (lets a container or load-balancer probe use plain HTTP):
```python
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
```
- After the alert settings from Task 1 add:
```python
# Periodic tasks; every task adds its own entry (intervals in seconds).
CELERY_BEAT_SCHEDULE = {
    "heartbeat": {"task": "leadhook.core.tasks.heartbeat", "schedule": 60.0},
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(health): beat heartbeat and /health/ endpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Graph client — POST, pagination, quoting, app token, new endpoints

**Files:**
- Modify: `leadhook/meta/graph.py`
- Test: `tests/meta/test_graph.py`

**Interfaces:**
- Produces on `GraphClient`: `request(method, path, params)`, `get`, `post`, `get_all(path, params, max_pages=50) -> list[dict]` (follows `paging.cursors.after` while `paging.next` is present), `list_forms(page_id)`, `list_form_leads(form_id, *, since: datetime)`, `list_pages()` (`/me/accounts` with `id,name,access_token`), `subscribed_app_ids(page_id) -> set[str]`, `subscribe_leadgen(page_id)`, `create_test_lead(form_id) -> dict`. Constructor gains `redact: tuple[str, ...] = ()`; every listed secret and its URL-encoded form is replaced with `***` in error messages.
- Produces module functions: `node(id) -> str` (one quoted path segment), `app_client(meta_app, *, redact=())` (app token `app_id|app_secret`), `debug_token(meta_app, token) -> dict` (the `data` object), `app_subscriptions(meta_app) -> list[dict]`.
- Existing `get_lead`, `get_form`, `client_for_page` keep their signatures; ids are now quoted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/meta/test_graph.py` (add `import json`, `import re`, `from datetime import UTC, datetime`, and import `app_subscriptions, debug_token` from `leadhook.meta.graph`; add `make_meta_app` to the factories import):
```python
@responses.activate
def test_ids_are_quoted_as_one_path_segment():
    responses.get(re.compile(r"https://graph\.facebook\.com/.*"), json={})
    GraphClient("v21.0", "tok").get_lead("../me")
    assert responses.calls[0].request.url.split("?")[0] == f"{BASE}/..%2Fme"


@responses.activate
def test_get_all_follows_cursors():
    # Same URL registered twice: responses returns them in registration order.
    responses.get(
        f"{BASE}/111/leadgen_forms",
        json={"data": [{"id": "1"}], "paging": {"cursors": {"after": "c1"}, "next": "https://x"}},
    )
    responses.get(
        f"{BASE}/111/leadgen_forms",
        json={"data": [{"id": "2"}], "paging": {"cursors": {"after": "c2"}}},
    )
    forms = GraphClient("v21.0", "tok").list_forms("111")
    assert [f["id"] for f in forms] == ["1", "2"]
    assert "after=c1" in responses.calls[1].request.url
    assert len(responses.calls) == 2


@responses.activate
def test_list_form_leads_filters_by_time_created():
    since = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    filtering = json.dumps(
        [{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}]
    )
    responses.get(
        f"{BASE}/222/leads",
        json={"data": [GRAPH_LEAD]},
        match=[
            matchers.query_param_matcher(
                {"fields": LEAD_FIELDS, "filtering": filtering, "limit": "100"},
                strict_match=False,
            )
        ],
    )
    assert GraphClient("v21.0", "tok").list_form_leads("222", since=since) == [GRAPH_LEAD]


@responses.activate
def test_subscribe_leadgen_posts_subscribed_fields():
    responses.post(
        f"{BASE}/111/subscribed_apps",
        json={"success": True},
        match=[
            matchers.query_param_matcher({"subscribed_fields": "leadgen", "access_token": "tok"})
        ],
    )
    GraphClient("v21.0", "tok").subscribe_leadgen("111")


@responses.activate
def test_subscribed_app_ids():
    responses.get(f"{BASE}/111/subscribed_apps", json={"data": [{"id": "1000"}, {"id": 7}]})
    assert GraphClient("v21.0", "tok").subscribed_app_ids("111") == {"1000", "7"}


@responses.activate
def test_debug_token_uses_app_token():
    app = make_meta_app()
    responses.get(
        f"{BASE}/debug_token",
        json={"data": {"is_valid": True, "expires_at": 0}},
        match=[
            matchers.query_param_matcher(
                {"input_token": "system-user-token", "access_token": "1000|app-secret"}
            )
        ],
    )
    assert debug_token(app, app.access_token) == {"is_valid": True, "expires_at": 0}


@responses.activate
def test_debug_token_error_redacts_both_tokens():
    app = make_meta_app()
    responses.get(
        f"{BASE}/debug_token",
        body=requests.ConnectionError(
            "failed: /debug_token?input_token=system-user-token&access_token=1000%7Capp-secret"
        ),
    )
    with pytest.raises(GraphError) as excinfo:
        debug_token(app, app.access_token)
    message = str(excinfo.value)
    assert "system-user-token" not in message
    assert "app-secret" not in message


@responses.activate
def test_app_subscriptions():
    app = make_meta_app()
    responses.get(f"{BASE}/1000/subscriptions", json={"data": [{"object": "page"}]})
    assert app_subscriptions(app) == [{"object": "page"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta/test_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'app_subscriptions'`.

- [ ] **Step 3: Implement**

Replace `leadhook/meta/graph.py` from the `GraphClient` class to the end of the file (keep the constants, `TOKEN_ERROR_CODES`, `TRANSIENT_ERROR_CODES` and `GraphError` as they are; add `import json`, `from datetime import datetime`, `from urllib.parse import quote` and `MetaApp` to the models import):
```python
def node(id_: str) -> str:
    """A Graph node ID as a single safe path segment (no traversal to other endpoints)."""
    return quote(str(id_), safe="")


class GraphClient:
    def __init__(
        self,
        version: str,
        access_token: str,
        timeout: float = 15,
        redact: tuple[str, ...] = (),
    ) -> None:
        self.version = version
        self.access_token = access_token
        self.timeout = timeout
        secrets = {s for s in (access_token, *redact) if s}
        # requests URL-encodes query values, so error messages may carry the encoded form.
        self._secrets = sorted(secrets | {quote(s, safe="") for s in secrets}, key=len, reverse=True)

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_BASE_URL}/{self.version}/{path.lstrip('/')}"
        query = {**(params or {}), "access_token": self.access_token}
        try:
            response = requests.request(method, url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            message = self._redact(f"{type(exc).__name__}: {exc}")
            raise GraphError(message, status=None, code=None) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if response.status_code >= 400 or error:
            error = error if isinstance(error, dict) else {}
            raise GraphError(
                self._redact(error.get("message") or f"HTTP {response.status_code}"),
                status=response.status_code,
                code=error.get("code"),
                subcode=error.get("error_subcode"),
            )
        return body if isinstance(body, dict) else {}

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    def post(self, path: str, params: dict | None = None) -> dict:
        return self.request("POST", path, params)

    def get_all(self, path: str, params: dict | None = None, *, max_pages: int = 50) -> list[dict]:
        """Every item of `data`, following cursor pagination.

        shortcut: stops after max_pages pages (5000 items at limit=100); raise it if a page
        ever has more forms or a 15-minute reconciliation window holds more leads.
        """
        params = dict(params or {})
        items: list[dict] = []
        for _ in range(max_pages):
            body = self.get(path, params)
            items.extend(item for item in body.get("data") or [] if isinstance(item, dict))
            paging = body.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                break
            params["after"] = after
        return items

    def get_lead(self, leadgen_id: str) -> dict:
        return self.get(node(leadgen_id), {"fields": LEAD_FIELDS})

    def get_form(self, form_id: str) -> dict:
        return self.get(node(form_id), {"fields": FORM_FIELDS})

    def list_forms(self, page_id: str) -> list[dict]:
        return self.get_all(f"{node(page_id)}/leadgen_forms", {"fields": FORM_FIELDS, "limit": 100})

    def list_form_leads(self, form_id: str, *, since: datetime) -> list[dict]:
        filtering = json.dumps(
            [{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}]
        )
        return self.get_all(
            f"{node(form_id)}/leads", {"fields": LEAD_FIELDS, "filtering": filtering, "limit": 100}
        )

    def list_pages(self) -> list[dict]:
        """Pages reachable with this (System User) token, each with its page access token."""
        return self.get_all("me/accounts", {"fields": "id,name,access_token", "limit": 100})

    def subscribed_app_ids(self, page_id: str) -> set[str]:
        return {str(app.get("id")) for app in self.get_all(f"{node(page_id)}/subscribed_apps")}

    def subscribe_leadgen(self, page_id: str) -> None:
        self.post(f"{node(page_id)}/subscribed_apps", {"subscribed_fields": "leadgen"})

    def create_test_lead(self, form_id: str) -> dict:
        return self.post(f"{node(form_id)}/test_leads")

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def client_for_page(page: Page) -> GraphClient:
    """Use the page's own token, or the Meta app's System User token when it has none."""
    token = page.page_token or page.meta_app.access_token
    return GraphClient(page.meta_app.graph_version, token)


def app_client(meta_app: MetaApp, *, redact: tuple[str, ...] = ()) -> GraphClient:
    """Client authenticated with the app access token (app_id|app_secret)."""
    return GraphClient(
        meta_app.graph_version,
        f"{meta_app.app_id}|{meta_app.app_secret}",
        redact=(meta_app.app_secret, *redact),
    )


def debug_token(meta_app: MetaApp, token: str) -> dict:
    return app_client(meta_app, redact=(token,)).get("debug_token", {"input_token": token}).get(
        "data"
    ) or {}


def app_subscriptions(meta_app: MetaApp) -> list[dict]:
    return app_client(meta_app).get_all(f"{node(meta_app.app_id)}/subscriptions")
```

If an existing test in `tests/meta/test_graph.py` patches `requests.get` directly, change it to patch `requests.request`; tests that use `responses` need no change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/meta -v && uv run pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(meta): Graph client pagination, POST, app token and quoted node ids

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `reconcile_leads`

**Files:**
- Modify: `leadhook/meta/models.py` (`Form.last_reconciled_at`), new migration `meta/0003_form_last_reconciled_at`
- Modify: `leadhook/leads/services.py`, `leadhook/leads/tasks.py`, `leadhook/settings.py`
- Test: `tests/leads/test_reconcile.py`

**Interfaces:**
- Consumes: `GraphClient.list_form_leads` (Task 3), `record_page_error` (Task 1), `route_lead`.
- Produces: `store_lead_details(lead, page, raw, client) -> None` (shared by fetch and reconciliation; sets status `FETCHED`); `reconcile_form(form, client, *, now) -> list[Lead]` (leads to route; advances `form.last_reconciled_at` to `now` only on success); task `leadhook.leads.tasks.reconcile_leads` every 15 min over forms with `status == "ACTIVE"`.
- Window: `time_created > (last_reconciled_at or now − 1 day) − 1 hour`.
- A known lead is only completed when still `RECEIVED` (its own fetch never succeeded); any other known lead is left alone.

- [ ] **Step 1: Write the failing tests**

`tests/leads/test_reconcile.py`:
```python
import json
from datetime import UTC, datetime, timedelta

import responses
from responses import matchers

from leadhook.leads.models import Lead
from leadhook.leads.tasks import reconcile_leads
from leadhook.meta.graph import LEAD_FIELDS
from tests.factories import make_destination, make_form, make_lead
from tests.fixtures import GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"
LEADS_URL = f"{BASE}/222/leads"


@responses.activate
def test_reconcile_creates_missing_leads_and_delivers():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    responses.post(HOOK, json={})
    reconcile_leads()
    lead = Lead.objects.get(leadgen_id="333")
    assert lead.source == Lead.Source.RECONCILE
    assert lead.status == Lead.Status.DELIVERED
    assert lead.data["full_name"] == "Jane Doe"
    form.refresh_from_db()
    assert form.last_reconciled_at is not None


@responses.activate
def test_reconcile_skips_known_leads():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    make_lead(form=form, status=Lead.Status.DELIVERED)
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    reconcile_leads()
    assert Lead.objects.count() == 1
    assert not [c for c in responses.calls if c.request.url.startswith(HOOK)]


@responses.activate
def test_reconcile_completes_known_received_lead():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    Lead.objects.create(
        leadgen_id="333", page_external_id="111", form_external_id="222", error="HTTP 500"
    )
    responses.get(LEADS_URL, json={"data": [GRAPH_LEAD]})
    responses.post(HOOK, json={})
    reconcile_leads()
    lead = Lead.objects.get(leadgen_id="333")
    assert lead.status == Lead.Status.DELIVERED
    assert lead.source == Lead.Source.WEBHOOK


@responses.activate
def test_reconcile_window_overlaps_last_run():
    last = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    make_form(last_reconciled_at=last)
    filtering = json.dumps(
        [
            {
                "field": "time_created",
                "operator": "GREATER_THAN",
                "value": int((last - timedelta(hours=1)).timestamp()),
            }
        ]
    )
    listing = responses.get(
        LEADS_URL,
        json={"data": []},
        match=[
            matchers.query_param_matcher(
                {"fields": LEAD_FIELDS, "filtering": filtering}, strict_match=False
            )
        ],
    )
    reconcile_leads()
    # reconcile_leads swallows Graph errors, so an unmatched request would not fail by itself.
    assert listing.call_count == 1


@responses.activate
def test_reconcile_failure_keeps_last_reconciled_at():
    last = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    form = make_form(last_reconciled_at=last)
    responses.get(LEADS_URL, status=500)
    reconcile_leads()
    form.refresh_from_db()
    assert form.last_reconciled_at == last


@responses.activate
def test_reconcile_ignores_inactive_forms():
    make_form(status="ARCHIVED")
    reconcile_leads()
    assert len(responses.calls) == 0


@responses.activate
def test_reconcile_token_error_flags_page(mailoutbox):
    form = make_form()
    responses.get(LEADS_URL, status=400, json={"error": {"message": "bad token", "code": 190}})
    reconcile_leads()
    form.page.refresh_from_db()
    assert form.page.error.startswith("Meta rejected the page token")
    assert len(mailoutbox) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads/test_reconcile.py -v`
Expected: FAIL — `ImportError: cannot import name 'reconcile_leads'`.

- [ ] **Step 3: Implement**

`leadhook/meta/models.py` — add to `Form` after `last_synced_at`:
```python
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
```
Run: `DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations meta --name form_last_reconciled_at`

`leadhook/leads/services.py` — add `from datetime import datetime, timedelta`, then split the success part of `fetch_lead_details` into `store_lead_details` and add `reconcile_form`:
```python
RECONCILE_OVERLAP = timedelta(hours=1)
RECONCILE_FIRST_WINDOW = timedelta(days=1)


def store_lead_details(lead: Lead, page: Page, raw: dict, client: GraphClient) -> None:
    """Save a Graph API lead object on `lead` and mark it fetched."""
    lead.page = page
    lead.created_time = parse_meta_time(raw.get("created_time"))
    for name in ("ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name"):
        setattr(lead, name, _fit(Lead, name, raw.get(name)))
    lead.is_organic = bool(raw.get("is_organic", False))
    lead.field_data = raw.get("field_data") or []
    lead.data = simplify_field_data(lead.field_data)
    lead.form_external_id = raw.get("form_id") or lead.form_external_id
    lead.form = _get_or_create_form(page, lead.form_external_id, client)
    lead.status = Lead.Status.FETCHED
    lead.error = ""
    lead.save()
    Page.objects.filter(pk=page.pk).update(last_lead_at=timezone.now(), error="")
```
`fetch_lead_details` keeps its page resolution and error handling; after `raw = client.get_lead(...)` succeeds it becomes:
```python
    store_lead_details(lead, page, raw, client)
    return FetchOutcome.DONE
```
Then:
```python
def reconcile_form(form: Form, client: GraphClient, *, now: datetime) -> list[Lead]:
    """Store leads Meta has for the form that LeadHook never fetched; return them for routing."""
    since = (form.last_reconciled_at or now - RECONCILE_FIRST_WINDOW) - RECONCILE_OVERLAP
    found: list[Lead] = []
    for raw in client.list_form_leads(form.form_id, since=since):
        leadgen_id = str(raw.get("id") or "")
        if not leadgen_id:
            continue
        lead = ingest_lead(
            leadgen_id=leadgen_id,
            page_external_id=form.page.page_id,
            form_external_id=form.form_id,
            source=Lead.Source.RECONCILE,
        )
        if lead is None:
            # Known lead: finish it only if its own fetch never succeeded.
            lead = Lead.objects.filter(leadgen_id=leadgen_id, status=Lead.Status.RECEIVED).first()
        if lead is None:
            continue
        store_lead_details(lead, form.page, raw, client)
        found.append(lead)
    form.last_reconciled_at = now
    form.save(update_fields=["last_reconciled_at"])
    return found
```

`leadhook/leads/tasks.py` — add imports (`import logging`, `from leadhook.meta.graph import GraphError, client_for_page`, `from leadhook.meta.models import Form`, `from leadhook.meta.services import record_page_error`, and `reconcile_form` from `.services`), a module `logger = logging.getLogger(__name__)`, and:
```python
@shared_task
def reconcile_leads() -> None:
    """Recover leads whose webhook never arrived (spec §5)."""
    from leadhook.destinations.tasks import route_lead

    now = timezone.now()
    # shortcut: one Graph call per active form every 15 minutes; batch by page if rate limits bite.
    for form in Form.objects.filter(status="ACTIVE").select_related("page__meta_app"):
        try:
            leads = reconcile_form(form, client_for_page(form.page), now=now)
        except GraphError as exc:
            if exc.is_token_error:
                record_page_error(form.page, f"Meta rejected the page token: {exc}")
            else:
                logger.warning("Reconciliation of form %s failed: %s", form.form_id, exc)
            continue
        for lead in leads:
            route_lead.delay(lead.pk)
```

`leadhook/settings.py` — add to `CELERY_BEAT_SCHEDULE`:
```python
    "reconcile-leads": {"task": "leadhook.leads.tasks.reconcile_leads", "schedule": 900.0},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(leads): reconcile leads missed by the webhook every 15 minutes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `sync_forms`, `check_pages`, `check_silence`

**Files:**
- Modify: `leadhook/meta/models.py` (`Page.silence_alerted_at`), new migration `meta/0004_page_silence_alerted_at`
- Modify: `leadhook/meta/services.py`, `leadhook/leads/services.py` (use `fit_to_field`), `leadhook/settings.py`
- Create: `leadhook/meta/tasks.py`
- Test: `tests/meta/test_services.py`, `tests/meta/test_tasks.py`

**Interfaces:**
- Consumes: Task 3 Graph methods, `record_page_error`, `send_alert`.
- Produces in `meta/services.py`: `fit_to_field(model, field, value) -> str` (moved from `leads/services._fit`; `leads/services.py` imports it and drops `_fit`), `sync_page_forms(page, client=None) -> int`, `check_meta_app(meta_app, *, now) -> None`, `check_page(page, *, now) -> None`, `silent_pages(now) -> QuerySet[Page]`.
- Produces tasks in `meta/tasks.py`: `sync_forms` (hourly), `check_pages` (daily), `check_silence` (hourly).
- Rules: `sync_page_forms` creates/updates forms by `form_id` (never deletes); `check_page` resubscribes when the app is missing from `subscribed_apps`, clears `Page.error` and sets `subscribed=True` when healthy, records an error on token or subscription failure, does nothing on transient errors; `check_meta_app` alerts when the app token is invalid or expires within 7 days and stores `token_expires_at`; a page is silent when its last lead is older than `SILENCE_ALERT_HOURS` but within 30 days, and it is alerted once per silence (`silence_alerted_at`), all silent pages in one email.

- [ ] **Step 1: Write the failing tests**

`tests/meta/test_services.py`:
```python
from datetime import UTC, datetime, timedelta

import responses
from django.utils import timezone

from leadhook.meta.models import Form, MetaApp, Page
from leadhook.meta.services import check_meta_app, check_page, sync_page_forms
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
    from leadhook.meta.services import silent_pages

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
```

`tests/meta/test_tasks.py`:
```python
from datetime import timedelta

import responses
from django.utils import timezone

from leadhook.meta.tasks import check_silence, sync_forms
from tests.factories import make_page

BASE = "https://graph.facebook.com/v21.0"


def test_check_silence_alerts_once_per_silence(mailoutbox):
    page = make_page(last_lead_at=timezone.now() - timedelta(hours=30))
    check_silence()
    check_silence()
    assert len(mailoutbox) == 1
    assert "111" in mailoutbox[0].body
    page.refresh_from_db()
    assert page.silence_alerted_at is not None


@responses.activate
def test_sync_forms_token_error_flags_page(mailoutbox):
    page = make_page()
    responses.get(
        f"{BASE}/111/leadgen_forms", status=400, json={"error": {"message": "x", "code": 190}}
    )
    sync_forms()
    page.refresh_from_db()
    assert page.error.startswith("Meta rejected the page token")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta/test_services.py tests/meta/test_tasks.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_meta_app'`.

- [ ] **Step 3: Implement**

`leadhook/meta/models.py` — add to `Page` after `error`:
```python
    silence_alerted_at = models.DateTimeField(null=True, blank=True)
```
Run: `DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations meta --name page_silence_alerted_at`

`leadhook/meta/services.py` — full file:
```python
"""Page and form operations shared by tasks, admin and setup."""

import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert

from .graph import GraphClient, GraphError, client_for_page, debug_token
from .models import Form, MetaApp, Page

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_WARNING = timedelta(days=7)
SILENCE_LOOKBACK = timedelta(days=30)


def fit_to_field(model: type[models.Model], field: str, value) -> str:
    """Coerce a Graph API value to text that fits the field (Meta names can be longer)."""
    text = "" if value is None else str(value)
    return text[: model._meta.get_field(field).max_length]


def record_page_error(page: Page, message: str) -> None:
    """Store a page problem; alert only when it is new (repeated checks do not re-alert)."""
    is_new = page.error != message
    page.error = message
    page.save(update_fields=["error"])
    if is_new:
        send_alert(AlertKind.PAGE_ERROR, f"Problem with page {page.page_id}", f"{page}: {message}")


def sync_page_forms(page: Page, client: GraphClient | None = None) -> int:
    """Create or refresh every lead form of the page; returns how many Meta listed."""
    client = client or client_for_page(page)
    now = timezone.now()
    forms = client.list_forms(page.page_id)
    for raw in forms:
        form_id = str(raw.get("id") or "")
        if not form_id:
            continue
        Form.objects.update_or_create(
            form_id=form_id,
            defaults={
                "page": page,
                "name": fit_to_field(Form, "name", raw.get("name")),
                "locale": fit_to_field(Form, "locale", raw.get("locale")),
                "status": fit_to_field(Form, "status", raw.get("status")),
                "last_synced_at": now,
            },
        )
    return len(forms)


def check_meta_app(meta_app: MetaApp, *, now: datetime) -> None:
    """Alert when the app's access token is invalid or about to expire."""
    if not meta_app.access_token:
        return
    try:
        info = debug_token(meta_app, meta_app.access_token)
    except GraphError as exc:
        logger.warning("Could not validate the token of Meta app %s: %s", meta_app.app_id, exc)
        return
    if not info.get("is_valid"):
        send_alert(
            AlertKind.PAGE_ERROR,
            f"Meta app {meta_app.app_id}: invalid access token",
            f"The access token of {meta_app} is no longer valid. Replace it in the admin.",
        )
        return
    expires = info.get("expires_at") or 0
    if not expires:
        return  # System User tokens never expire
    expires_at = datetime.fromtimestamp(expires, UTC)
    MetaApp.objects.filter(pk=meta_app.pk).update(token_expires_at=expires_at)
    if expires_at - now < TOKEN_EXPIRY_WARNING:
        send_alert(
            AlertKind.PAGE_ERROR,
            f"Meta app {meta_app.app_id}: access token expires soon",
            f"The access token of {meta_app} expires on {expires_at:%Y-%m-%d}. "
            "Replace it in the admin before then (a System User token never expires).",
        )


def check_page(page: Page, *, now: datetime) -> None:
    """Make sure the page's token works and the app is subscribed to its leadgen events."""
    client = client_for_page(page)
    try:
        if page.meta_app.app_id not in client.subscribed_app_ids(page.page_id):
            client.subscribe_leadgen(page.page_id)
    except GraphError as exc:
        if exc.is_transient:
            logger.warning("Check of page %s skipped: %s", page.page_id, exc)
            return
        if exc.is_token_error:
            record_page_error(page, f"Meta rejected the page token: {exc}")
        else:
            record_page_error(page, f"Could not subscribe the page to lead events: {exc}")
        Page.objects.filter(pk=page.pk).update(last_checked_at=now)
        return
    Page.objects.filter(pk=page.pk).update(subscribed=True, error="", last_checked_at=now)


def silent_pages(now: datetime) -> QuerySet[Page]:
    """Pages that usually get leads (one in the last 30 days) but none for SILENCE_ALERT_HOURS."""
    threshold = now - timedelta(hours=settings.SILENCE_ALERT_HOURS)
    return (
        Page.objects.filter(last_lead_at__lt=threshold, last_lead_at__gte=now - SILENCE_LOOKBACK)
        .filter(Q(silence_alerted_at__isnull=True) | Q(silence_alerted_at__lt=F("last_lead_at")))
        .order_by("page_id")
    )
```

`leadhook/leads/services.py` — delete `_fit`, import `from leadhook.meta.services import fit_to_field, record_page_error`, and replace every `_fit(` with `fit_to_field(`.

`leadhook/meta/tasks.py`:
```python
import logging

from celery import shared_task
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert

from .graph import GraphError
from .models import MetaApp, Page
from .services import check_meta_app, check_page, record_page_error, silent_pages, sync_page_forms

logger = logging.getLogger(__name__)


@shared_task
def sync_forms() -> None:
    for page in Page.objects.select_related("meta_app"):
        try:
            sync_page_forms(page)
        except GraphError as exc:
            if exc.is_token_error:
                record_page_error(page, f"Meta rejected the page token: {exc}")
            else:
                logger.warning("Form sync of page %s failed: %s", page.page_id, exc)


@shared_task
def check_pages() -> None:
    now = timezone.now()
    for meta_app in MetaApp.objects.all():
        check_meta_app(meta_app, now=now)
    for page in Page.objects.select_related("meta_app"):
        check_page(page, now=now)


@shared_task
def check_silence() -> None:
    now = timezone.now()
    pages = list(silent_pages(now))
    if not pages:
        return
    # One email for all pages: per-kind throttling would drop separate ones.
    send_alert(
        AlertKind.SILENCE,
        f"No leads from {len(pages)} page(s)",
        "\n".join(f"{page}: last lead {page.last_lead_at:%Y-%m-%d %H:%M} UTC" for page in pages),
    )
    Page.objects.filter(pk__in=[page.pk for page in pages]).update(silence_alerted_at=now)
```

`leadhook/settings.py` — add to `CELERY_BEAT_SCHEDULE`:
```python
    "sync-forms": {"task": "leadhook.meta.tasks.sync_forms", "schedule": 3600.0},
    "check-pages": {"task": "leadhook.meta.tasks.check_pages", "schedule": 86400.0},
    "check-silence": {"task": "leadhook.meta.tasks.check_silence", "schedule": 3600.0},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(meta): periodic form sync, page/token checks and silence alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Sweeper for work lost by the broker

**Files:**
- Create: `leadhook/destinations/sweep.py`
- Modify: `leadhook/destinations/tasks.py`, `leadhook/settings.py`
- Test: `tests/destinations/test_sweep.py`

**Interfaces:**
- Consumes: `process_event`, `fetch_lead`, `FETCH_BACKOFF` (leads), `route_lead`, `deliver`.
- Produces: `stuck_events(now)`, `stuck_received_leads(now)`, `stuck_fetched_leads(now)`, `stuck_deliveries(now)` (querysets); task `leadhook.destinations.tasks.sweep_stuck_work` every 5 min, at most 500 items per kind per run.
- Rules:
  - **Events:** valid signature, unprocessed, received more than 5 min ago.
  - **`RECEIVED` leads:** untouched for 1 h and received within 7 days. Re-fetched once, with no retry chain (`attempt=len(FETCH_BACKOFF)`).
  - **`FETCHED` leads:** untouched for 5 min, then re-routed.
  - **`PENDING` deliveries:** not leased (or lease expired), and either never scheduled and untouched for 5 min, or overdue for their retry by more than 5 min. These are re-delivered.
  - **Never dispatched:** deliveries leased right now, or still waiting for their retry time.

- [ ] **Step 1: Write the failing tests**

`tests/destinations/test_sweep.py`:
```python
from datetime import timedelta
from unittest import mock

import responses
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.tasks import sweep_stuck_work
from leadhook.leads.models import Lead, WebhookEvent
from tests.factories import make_destination, make_form, make_lead
from tests.fixtures import GRAPH_LEAD, leadgen_payload

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"


def _age(model, pk, **fields):
    model.objects.filter(pk=pk).update(**fields)


@responses.activate
def test_sweep_processes_unenqueued_events():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    event = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    _age(WebhookEvent, event.pk, received_at=timezone.now() - timedelta(minutes=10))
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={})
    sweep_stuck_work()
    assert Lead.objects.get(leadgen_id="333").status == Lead.Status.DELIVERED


def test_sweep_ignores_recent_and_invalid_events():
    WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    bad = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=False)
    _age(WebhookEvent, bad.pk, received_at=timezone.now() - timedelta(minutes=10))
    with mock.patch("leadhook.destinations.tasks.process_event.delay") as dispatch:
        sweep_stuck_work()
    dispatch.assert_not_called()


@responses.activate
def test_sweep_redelivers_crashed_delivery():
    responses.post(HOOK, json={})
    lead = make_lead(status=Lead.Status.ROUTED)
    delivery = Delivery.objects.create(lead=lead, destination=make_destination(), attempts=1)
    past = timezone.now() - timedelta(minutes=10)
    _age(Delivery, delivery.pk, locked_until=past, updated_at=past)
    sweep_stuck_work()
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SUCCESS
    assert delivery.attempts == 2


def test_sweep_skips_leased_and_scheduled_deliveries():
    now = timezone.now()
    past = now - timedelta(minutes=10)
    lead = make_lead(status=Lead.Status.ROUTED)
    leased = Delivery.objects.create(lead=lead, destination=make_destination(name="a"))
    _age(Delivery, leased.pk, locked_until=now + timedelta(minutes=1), updated_at=past)
    waiting = Delivery.objects.create(lead=lead, destination=make_destination(name="b"))
    _age(Delivery, waiting.pk, next_retry_at=now + timedelta(hours=2), updated_at=past)
    fresh = Delivery.objects.create(lead=lead, destination=make_destination(name="c"))
    with mock.patch("leadhook.destinations.tasks.deliver.delay") as dispatch:
        sweep_stuck_work()
    dispatch.assert_not_called()
    assert fresh.status == Delivery.Status.PENDING


def test_sweep_routes_stuck_fetched_lead():
    lead = make_lead(status=Lead.Status.FETCHED)
    _age(Lead, lead.pk, updated_at=timezone.now() - timedelta(minutes=10))
    sweep_stuck_work()
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


def test_sweep_refetches_received_leads_hourly_for_a_week():
    now = timezone.now()
    due = Lead.objects.create(leadgen_id="1", page_external_id="111")
    _age(Lead, due.pk, updated_at=now - timedelta(hours=2), received_at=now - timedelta(days=1))
    recent = Lead.objects.create(leadgen_id="2", page_external_id="111")
    _age(Lead, recent.pk, updated_at=now - timedelta(minutes=30))
    expired = Lead.objects.create(leadgen_id="3", page_external_id="111")
    _age(Lead, expired.pk, updated_at=now - timedelta(days=8), received_at=now - timedelta(days=8))
    with mock.patch("leadhook.destinations.tasks.fetch_lead.delay") as dispatch:
        sweep_stuck_work()
    assert [call.args[0] for call in dispatch.call_args_list] == [due.pk]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/destinations/test_sweep.py -v`
Expected: FAIL — `ImportError: cannot import name 'sweep_stuck_work'`.

- [ ] **Step 3: Implement**

`leadhook/destinations/sweep.py`:
```python
"""Pipeline work whose Celery message was lost (broker outage, crashed worker, restart)."""

from datetime import datetime, timedelta

from django.db.models import Q, QuerySet

from leadhook.leads.models import Lead, WebhookEvent

from .models import Delivery

STUCK_AFTER = timedelta(minutes=5)
REFETCH_EVERY = timedelta(hours=1)
REFETCH_FOR = timedelta(days=7)


def stuck_events(now: datetime) -> QuerySet[WebhookEvent]:
    return WebhookEvent.objects.filter(
        signature_ok=True, processed_at__isnull=True, received_at__lt=now - STUCK_AFTER
    )


def stuck_received_leads(now: datetime) -> QuerySet[Lead]:
    """Leads whose fetch gave up or was lost; a failed fetch saves its error, bumping updated_at."""
    return Lead.objects.filter(
        status=Lead.Status.RECEIVED,
        updated_at__lt=now - REFETCH_EVERY,
        received_at__gte=now - REFETCH_FOR,
    )


def stuck_fetched_leads(now: datetime) -> QuerySet[Lead]:
    return Lead.objects.filter(status=Lead.Status.FETCHED, updated_at__lt=now - STUCK_AFTER)


def stuck_deliveries(now: datetime) -> QuerySet[Delivery]:
    """Pending, not leased, and either never scheduled or overdue for their retry."""
    grace = now - STUCK_AFTER
    return (
        Delivery.objects.filter(status=Delivery.Status.PENDING)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .filter(Q(next_retry_at__isnull=True, updated_at__lt=grace) | Q(next_retry_at__lt=grace))
    )
```

`leadhook/destinations/tasks.py` — add imports (`from django.utils import timezone`, `from leadhook.leads.services import FETCH_BACKOFF`, `from leadhook.leads.tasks import fetch_lead, process_event`, `from .sweep import stuck_deliveries, stuck_events, stuck_fetched_leads, stuck_received_leads`) and:
```python
SWEEP_BATCH = 500


@shared_task
def sweep_stuck_work() -> None:
    """Re-dispatch lost work; every task it dispatches is safe to run twice."""
    now = timezone.now()
    for pk in stuck_events(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        process_event.delay(pk)
    for pk in stuck_received_leads(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        # One attempt, no retry chain: the next sweep (an hour later) tries again.
        fetch_lead.delay(pk, attempt=len(FETCH_BACKOFF))
    for pk in stuck_fetched_leads(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        route_lead.delay(pk)
    for pk in stuck_deliveries(now).values_list("pk", flat=True)[:SWEEP_BATCH]:
        deliver.delay(pk)
```
If importing `leadhook.leads.tasks` at module level causes a circular import (it imports `route_lead` lazily, so it should not), move these imports inside `sweep_stuck_work` and patch targets in the tests to `leadhook.leads.tasks.<name>.delay`.

`leadhook/settings.py` — add to `CELERY_BEAT_SCHEDULE`:
```python
    "sweep-stuck-work": {"task": "leadhook.destinations.tasks.sweep_stuck_work", "schedule": 300.0},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(pipeline): sweep events, leads and deliveries lost by the broker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Retention purge

**Files:**
- Modify: `leadhook/leads/models.py` (`Lead.purged_at`), new migration `leads/0002_lead_purged_at`
- Create: `leadhook/leads/retention.py`
- Modify: `leadhook/leads/tasks.py`, `leadhook/leads/admin.py`, `leadhook/destinations/services.py` (`reset_for_resend`), `leadhook/destinations/tasks.py` (`route_lead`, `deliver`), `leadhook/destinations/admin.py` (resend message), `leadhook/settings.py`
- Test: `tests/leads/test_retention.py`

**Interfaces:**
- Produces: `purge_expired_data(*, now) -> dict[str, int]` with keys `leads`, `deliveries`, `events`; task `leadhook.leads.tasks.purge_old_data`, run daily.
- Rule (spec §4): for leads received more than `LEAD_RETENTION_DAYS` ago, clear `Lead.field_data` and `Lead.data` and set `Lead.purged_at`. Clear `request_body` and `response_body` of their deliveries. Clear `WebhookEvent.payload` for events older than the same cutoff. Everything else is kept: IDs, ad, campaign, statuses, errors, timestamps.
- Guards against sending empty data:
  - `reset_for_resend` returns False for purged leads.
  - `route_lead` ignores purged leads.
  - `deliver` marks a pending delivery of a purged lead FAILED without sending.
  - The admin "Reprocess" action skips purged leads.

- [ ] **Step 1: Write the failing tests**

`tests/leads/test_retention.py`:
```python
from datetime import timedelta

import responses
from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.destinations.services import reset_for_resend
from leadhook.destinations.tasks import deliver
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.leads.retention import purge_expired_data
from tests.factories import make_destination, make_lead
from tests.fixtures import leadgen_payload


def _make_old(model, pk, days=91):
    model.objects.filter(pk=pk).update(received_at=timezone.now() - timedelta(days=days))


def test_purge_clears_personal_data_keeps_metadata():
    lead = make_lead(status=Lead.Status.DELIVERED)
    delivery = Delivery.objects.create(
        lead=lead,
        destination=make_destination(),
        status=Delivery.Status.SUCCESS,
        request_body='{"full_name": "Jane Doe"}',
        response_body="ok",
    )
    _make_old(Lead, lead.pk)
    counts = purge_expired_data(now=timezone.now())
    assert counts["leads"] == 1
    assert counts["deliveries"] == 1
    lead.refresh_from_db()
    delivery.refresh_from_db()
    assert lead.data == {}
    assert lead.field_data == []
    assert lead.purged_at is not None
    assert (lead.leadgen_id, lead.ad_name, lead.status) == ("333", "Ad 1", Lead.Status.DELIVERED)
    assert (delivery.request_body, delivery.response_body) == ("", "")
    assert delivery.status == Delivery.Status.SUCCESS


def test_purge_keeps_recent_data():
    lead = make_lead()
    assert purge_expired_data(now=timezone.now()) == {"leads": 0, "deliveries": 0, "events": 0}
    lead.refresh_from_db()
    assert lead.data == {"full_name": "Jane Doe"}


def test_purge_clears_old_webhook_payloads():
    old = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    recent = WebhookEvent.objects.create(payload=leadgen_payload(), signature_ok=True)
    _make_old(WebhookEvent, old.pk)
    assert purge_expired_data(now=timezone.now())["events"] == 1
    old.refresh_from_db()
    recent.refresh_from_db()
    assert old.payload == {}
    assert recent.payload == leadgen_payload()


def test_purge_is_idempotent():
    lead = make_lead()
    _make_old(Lead, lead.pk)
    purge_expired_data(now=timezone.now())
    assert purge_expired_data(now=timezone.now()) == {"leads": 0, "deliveries": 0, "events": 0}


def test_resend_refuses_purged_lead():
    lead = make_lead(status=Lead.Status.FAILED, purged_at=timezone.now())
    delivery = Delivery.objects.create(
        lead=lead, destination=make_destination(), status=Delivery.Status.FAILED
    )
    assert not reset_for_resend(delivery)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED


@responses.activate
def test_deliver_fails_pending_delivery_of_purged_lead():
    lead = make_lead(status=Lead.Status.ROUTED, purged_at=timezone.now(), data={})
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert "purged" in delivery.last_error
    assert len(responses.calls) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads/test_retention.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.leads.retention'`.

- [ ] **Step 3: Implement**

`leadhook/leads/models.py` — add to `Lead` after `error`:
```python
    # Set by the retention purge; field_data/data are empty from then on.
    purged_at = models.DateTimeField(null=True, blank=True)
```
Run: `DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations leads --name lead_purged_at`

`leadhook/leads/retention.py`:
```python
"""Retention (spec §4): clear personal data older than LEAD_RETENTION_DAYS, keep metadata."""

from datetime import datetime, timedelta

from django.conf import settings

from leadhook.destinations.models import Delivery

from .models import Lead, WebhookEvent


def purge_expired_data(*, now: datetime) -> dict[str, int]:
    cutoff = now - timedelta(days=settings.LEAD_RETENTION_DAYS)
    leads = Lead.objects.filter(received_at__lt=cutoff, purged_at__isnull=True)
    # Deliveries first: once the leads are marked purged, `leads` no longer selects them.
    deliveries = Delivery.objects.filter(lead__in=leads).update(request_body="", response_body="")
    purged = leads.update(field_data=[], data={}, purged_at=now)
    events = (
        WebhookEvent.objects.filter(received_at__lt=cutoff).exclude(payload={}).update(payload={})
    )
    return {"leads": purged, "deliveries": deliveries, "events": events}
```

`leadhook/leads/tasks.py` — import `from .retention import purge_expired_data` and add:
```python
@shared_task
def purge_old_data() -> None:
    logger.info("Retention purge: %s", purge_expired_data(now=timezone.now()))
```

`leadhook/destinations/services.py` — at the top of `reset_for_resend`, before the update:
```python
    if delivery.lead.purged_at is not None:
        return False  # the lead's data is gone; resending would send an empty payload
```
Add "or when the lead's data was purged" to its docstring.

`leadhook/destinations/tasks.py`:
- In `route_lead`, after loading the lead: `if lead.purged_at is not None: return`.
- In `deliver`, right after the `status != PENDING` check:
```python
    if delivery.lead.purged_at is not None:
        mark_delivery_failed(
            delivery, "Lead data was purged by the retention policy before delivery"
        )
        refresh_lead_status(delivery.lead)
        return
```

`leadhook/leads/admin.py` — in `reprocess`, skip purged leads: `if lead.purged_at is not None: continue` as the first line of the loop. Add `"purged_at"` handling for free: `readonly_fields` already lists every field.

`leadhook/destinations/admin.py` — change the resend skip message to `f" {skipped} skipped (in progress, scheduled, or lead data purged)."`. If an existing test asserts the old wording, update that assertion.

`leadhook/settings.py` — add to `CELERY_BEAT_SCHEDULE`:
```python
    "purge-old-data": {"task": "leadhook.leads.tasks.purge_old_data", "schedule": 86400.0},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(leads): daily retention purge of personal data

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Outbound delivery hardening

**Files:**
- Modify: `leadhook/destinations/http.py`, `leadhook/destinations/services.py`, `leadhook/destinations/tasks.py`, `leadhook/settings.py`
- Test: `tests/destinations/test_http.py`, `tests/destinations/test_tasks.py`, `tests/destinations/test_services.py`

**Interfaces:**
- Produces in `http.py`:
  - `GuardedAdapter`: a requests adapter that checks the IP actually connected to and raises `BlockedDestination` for a non-public peer. It is mounted unless `ALLOW_PRIVATE_DESTINATIONS`.
  - `redact_url(message, url) -> str`: removes the URL, its path, its query and its userinfo from a message.
  - Response body read as a stream, at most `RESPONSE_READ_LIMIT` bytes (1 MB), parsed for the success condition. `RESPONSE_BODY_LIMIT` (4 KB) of it is stored.
  - `session.verify = settings.DESTINATION_CA_BUNDLE or True`.
  - IDNA validation before the `ALLOW_PRIVATE_DESTINATIONS` early return.
- Produces in `services.py`: `mark_delivery_failed(..., lease=None) -> bool` and `schedule_retry(..., lease=None) -> bool`. With `lease`, they write only while the delivery still holds that lease. `attempt_delivery` saves its result the same way and returns a `"skip"` result when the lease was lost.
- `tasks.deliver` passes the lease it holds on the retry path.
- `describe_unexpected_error` also applies `redact_url`.
- The alert added in Task 1 stays in `mark_delivery_failed`. It is sent only when the failure was actually saved.

- [ ] **Step 1: Write the failing tests**

Append to `tests/destinations/test_http.py` (add `import socket` and import `redact_url` from `leadhook.destinations.http`):
```python
def test_no_auth_sends_no_credentials():
    headers = build_headers(make_destination(), body="{}", timestamp=1, idempotency_key="333")
    assert "Authorization" not in headers
    assert "X-LeadHook-Signature" not in headers


@pytest.mark.parametrize("status", [301, 303, 307, 308])
@responses.activate
def test_other_redirects_fail_without_following(status):
    responses.post(URL, status=status, headers={"Location": "https://elsewhere.example.com/"})
    result = send_request(make_destination(), "{}", HEADERS)
    assert result.outcome == "fail"
    assert len(responses.calls) == 1


def test_dns_failure_is_retried(monkeypatch):
    def fail(host, port):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr("leadhook.destinations.http.resolve_host", fail)
    result = send_request(make_destination(), "{}", HEADERS)
    assert result.outcome == "retry"
    assert "DNS resolution failed" in result.error


def test_bad_idna_host_fails_even_when_private_allowed(settings):
    settings.ALLOW_PRIVATE_DESTINATIONS = True
    with pytest.raises(BlockedDestination, match="Invalid destination host name"):
        check_destination_url("https://" + "a" * 64 + ".example.com/")


def test_connection_to_private_peer_is_blocked_after_dns_check(fake_dns):
    fake_dns["localhost"] = "93.184.216.34"  # the pre-check sees a public address...
    with socket.create_server(("127.0.0.1", 0)) as server:  # ...the connection lands here
        port = server.getsockname()[1]
        destination = make_destination(url=f"http://localhost:{port}/lead")
        result = send_request(destination, "{}", HEADERS)
    assert result.outcome == "fail"
    assert "non-public address (127.0.0.1)" in result.error


@responses.activate
def test_success_condition_reads_json_beyond_stored_limit():
    responses.post(URL, json={"padding": "x" * 10000, "success": True})
    destination = make_destination(success_condition="response.success == true")
    result = send_request(destination, "{}", HEADERS)
    assert result.outcome == "success"
    assert len(result.response_body) == 4096


@responses.activate
def test_network_error_does_not_leak_url_secrets():
    url = "https://hooks.example.com/webhook/0f1e2d3c?token=s3cret"
    error = requests.ConnectionError("Max retries exceeded with url: /webhook/0f1e2d3c?token=s3cret")
    responses.post(url, body=error)
    result = send_request(make_destination(url=url), "{}", HEADERS)
    assert result.outcome == "retry"
    assert "s3cret" not in result.error
    assert "0f1e2d3c" not in result.error


def test_redact_url_removes_userinfo():
    message = "failed for https://user:pw@hooks.example.com/x"
    assert "pw" not in redact_url(message, "https://user:pw@hooks.example.com/x")


@responses.activate
def test_custom_ca_bundle_is_used(settings):
    settings.DESTINATION_CA_BUNDLE = "/etc/ssl/custom-ca.pem"
    responses.post(URL)
    send_request(make_destination(), "{}", HEADERS)
    assert responses.calls[0].request.req_kwargs["verify"] == "/etc/ssl/custom-ca.pem"
```

Append to `tests/destinations/test_tasks.py`:
```python
def _steal_lease(delivery):
    """A responses callback simulating another worker taking over mid-request."""

    def callback(request):
        Delivery.objects.filter(pk=delivery.pk).update(
            locked_until=timezone.now() + timedelta(hours=1)
        )
        return (500, {}, "")

    return callback


@responses.activate
def test_late_result_after_lease_loss_is_discarded():
    delivery = _pending_delivery()
    responses.add_callback(responses.POST, HOOK, callback=_steal_lease(delivery))
    deliver(delivery.pk)  # no Retry: the result was dropped
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.last_status_code is None
    assert delivery.next_retry_at is None


@responses.activate
def test_exhaustion_does_not_overwrite_a_stolen_lease():
    delivery = _pending_delivery()
    Delivery.objects.filter(pk=delivery.pk).update(attempts=len(DELIVERY_BACKOFF))
    responses.add_callback(responses.POST, HOOK, callback=_steal_lease(delivery))
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING


@responses.activate
def test_deliver_honors_retry_after():
    responses.post(HOOK, status=429, headers={"Retry-After": "600"})
    delivery = _pending_delivery()
    before = timezone.now()
    with pytest.raises(Retry):
        deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.next_retry_at >= before + timedelta(seconds=600)


def test_failing_template_filter_fails_delivery_without_retry():
    lead = make_lead(status=Lead.Status.ROUTED)
    destination = make_destination(
        payload_template='{"d": {{ created_time | date("Nowhere/Zone") | tojson }}}'
    )
    delivery = Delivery.objects.create(lead=lead, destination=destination)
    deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.last_error.startswith("Template error")
```

Append to `tests/destinations/test_services.py` (add the imports it needs: `timedelta`, `timezone`, `Delivery`, `Lead`, `mark_delivery_failed`, `schedule_retry`, `make_destination`, `make_lead`):
```python
def _leased_by_someone_else():
    lead = make_lead(status=Lead.Status.ROUTED)
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    ours = timezone.now() + timedelta(minutes=1)
    Delivery.objects.filter(pk=delivery.pk).update(locked_until=ours + timedelta(hours=1))
    return delivery, ours


def test_mark_failed_with_lost_lease_changes_nothing(mailoutbox):
    delivery, ours = _leased_by_someone_else()
    assert not mark_delivery_failed(delivery, "late", lease=ours)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert mailoutbox == []


def test_schedule_retry_with_lost_lease_changes_nothing():
    delivery, ours = _leased_by_someone_else()
    assert not schedule_retry(delivery, 60, lease=ours)
    delivery.refresh_from_db()
    assert delivery.next_retry_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/destinations -v`
Expected: FAIL — `ImportError: cannot import name 'redact_url'`.

- [ ] **Step 3: Implement**

`leadhook/settings.py` — after `ALLOW_PRIVATE_DESTINATIONS`:
```python
# Custom CA bundle for destinations behind a private CA (requests ignores the env var
# because the outbound session disables trust_env).
DESTINATION_CA_BUNDLE = env("REQUESTS_CA_BUNDLE", default="")
```

`leadhook/destinations/http.py`:

Imports: add `import json`, `from requests.adapters import HTTPAdapter`, `from urllib3.connection import HTTPConnection, HTTPSConnection`, `from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool`.

Constants: add `RESPONSE_READ_LIMIT = 1_000_000  # bytes parsed for the success condition`.

In `check_destination_url`, insert right before `if settings.ALLOW_PRIVATE_DESTINATIONS:`:
```python
    try:
        hostname.encode("idna")
    except UnicodeError:
        raise BlockedDestination(f"Invalid destination host name ({shown})") from None
```

Add after `check_destination_url`:
```python
def _check_peer(sock) -> None:
    ip = ipaddress.ip_address(sock.getpeername()[0].split("%", 1)[0])
    if not ip.is_global:
        sock.close()
        raise BlockedDestination(
            f"Connection to a non-public address ({ip}) was blocked. "
            "Set ALLOW_PRIVATE_DESTINATIONS=true to allow it."
        )


class _PeerCheckMixin:
    # urllib3 2.x: _new_conn opens the TCP socket, before TLS and before any byte is sent.
    def _new_conn(self):
        sock = super()._new_conn()
        _check_peer(sock)
        return sock


class _GuardedHTTPConnection(_PeerCheckMixin, HTTPConnection):
    pass


class _GuardedHTTPSConnection(_PeerCheckMixin, HTTPSConnection):
    pass


class _GuardedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class GuardedAdapter(HTTPAdapter):
    """Checks the address actually connected to, closing the DNS-rebinding gap that a
    resolve-then-connect check (check_destination_url) leaves open."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _GuardedHTTPConnectionPool,
            "https": _GuardedHTTPSConnectionPool,
        }


def redact_url(message: str, url: str) -> str:
    """Remove the URL, its path, query and credentials from a message (they may be secrets,
    e.g. an n8n webhook path or a ?token= parameter)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return message.replace(url, "***")
    hidden = [url]
    if parts.query:
        hidden += [f"{parts.path}?{parts.query}", parts.query]
    if len(parts.path) > 1:
        hidden.append(parts.path)
    if "@" in parts.netloc:
        hidden.append(parts.netloc.rsplit("@", 1)[0])
    for value in hidden:
        message = message.replace(value, "***")
    return message


def _read_limited(response: requests.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]


def _decode(content: bytes, encoding: str | None) -> str:
    try:
        return content.decode(encoding or "utf-8", "replace")
    except LookupError:  # unknown charset in Content-Type
        return content.decode("utf-8", "replace")
```

Replace `send_request` with:
```python
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
            session.verify = settings.DESTINATION_CA_BUNDLE or True
            if not settings.ALLOW_PRIVATE_DESTINATIONS:
                session.mount("http://", GuardedAdapter())
                session.mount("https://", GuardedAdapter())
            with session.request(
                destination.method,
                destination.url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=destination.timeout_s,
                allow_redirects=False,
                stream=True,
            ) as response:
                content = _read_limited(response, RESPONSE_READ_LIMIT)
    except BlockedDestination as exc:
        return SendResult("fail", error=str(exc))
    except requests.RequestException as exc:
        return SendResult("retry", error=redact_url(f"{type(exc).__name__}: {exc}", destination.url))

    status = response.status_code
    text = _decode(content, response.encoding)[:RESPONSE_BODY_LIMIT]

    if 200 <= status < 300:
        if destination.success_condition.strip():
            try:
                parsed = json.loads(content)
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
```

`leadhook/destinations/services.py`:

Import `redact_url` from `.http`. In `describe_unexpected_error`, before `return message`: `message = redact_url(message, destination.url)`.

Add:
```python
def _save_if_leased(delivery: Delivery, lease: datetime, fields: list[str]) -> bool:
    """Write `fields` only while `lease` is still the delivery's lease (late results drop)."""
    values = {name: getattr(delivery, name) for name in fields}
    updated = Delivery.objects.filter(pk=delivery.pk, locked_until=lease).update(
        **values, updated_at=timezone.now()
    )
    return bool(updated)
```

Replace the tail of `attempt_delivery`, from `delivery.request_body = body` to the end:
```python
    delivery.request_body = body
    delivery.response_body = result.response_body
    delivery.last_status_code = result.status_code
    if result.outcome == "fail":
        saved = mark_delivery_failed(
            delivery,
            result.error,
            extra_fields=("request_body", "response_body", "last_status_code"),
            lease=lease,
        )
    else:
        delivery.last_error = result.error
        delivery.next_retry_at = None
        if result.outcome == "success":
            delivery.status = Delivery.Status.SUCCESS
            delivery.delivered_at = now
            delivery.locked_until = None
        # On retry the lease is kept until schedule_retry/mark_delivery_failed releases it,
        # so no other worker can slip in between the attempt and its rescheduling.
        saved = _save_if_leased(
            delivery,
            lease,
            [
                "request_body",
                "response_body",
                "last_status_code",
                "last_error",
                "next_retry_at",
                "status",
                "delivered_at",
                "locked_until",
            ],
        )
    if not saved:
        # The send outlived the lease and another attempt took over: its result wins.
        logger.warning("Delivery %s: lease lost before the result was saved", delivery.pk)
        return SendResult("skip", error="Lease lost before the result was saved")
    return result
```

Replace `schedule_retry` and `mark_delivery_failed`:
```python
def schedule_retry(
    delivery: Delivery,
    delay: int,
    *,
    now: datetime | None = None,
    lease: datetime | None = None,
) -> bool:
    delivery.next_retry_at = (now or timezone.now()) + timedelta(seconds=delay)
    delivery.locked_until = None
    fields = ["next_retry_at", "locked_until"]
    if lease is not None:
        return _save_if_leased(delivery, lease, fields)
    delivery.save(update_fields=[*fields, "updated_at"])
    return True


def mark_delivery_failed(
    delivery: Delivery,
    error: str,
    *,
    extra_fields: tuple[str, ...] = (),
    lease: datetime | None = None,
) -> bool:
    """The single place that marks a delivery FAILED (and releases its lease).

    With `lease`, nothing is written unless this worker still holds that lease.
    """
    delivery.status = Delivery.Status.FAILED
    delivery.last_error = error
    delivery.next_retry_at = None
    delivery.locked_until = None
    fields = ["status", "last_error", "next_retry_at", "locked_until", *extra_fields]
    if lease is not None:
        if not _save_if_leased(delivery, lease, fields):
            return False
    else:
        delivery.save(update_fields=[*fields, "updated_at"])
    send_alert(
        AlertKind.DELIVERY_FAILED,
        f"Delivery to {delivery.destination} failed",
        f"Lead {delivery.lead.leadgen_id} could not be delivered to {delivery.destination} "
        f"({delivery.public_id}): {error}",
    )
    return True
```

`leadhook/destinations/tasks.py` — replace the retry branch of `deliver`:
```python
    if result.outcome == "retry":
        lease = delivery.locked_until  # attempt_delivery keeps the lease on retry
        # 0 after the first attempt of a cycle; a cycle allows len(DELIVERY_BACKOFF) + 1 attempts.
        retry_index = delivery.attempts - delivery.retry_base - 1
        if retry_index >= len(DELIVERY_BACKOFF):
            mark_delivery_failed(
                delivery, f"Gave up after {delivery.attempts} attempts: {result.error}", lease=lease
            )
        else:
            delay = next_retry_delay(retry_index, result.retry_after)
            if not schedule_retry(delivery, delay, lease=lease):
                return
            raise self.retry(countdown=delay)
    refresh_lead_status(delivery.lead)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass, including `tests/test_end_to_end.py` (it sets `ALLOW_PRIVATE_DESTINATIONS = True`, so the guard is not mounted there).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix(destinations): pin connections against DNS rebinding, stream responses, guard late results

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Parsing, encryption-key and test hardening

**Files:**
- Modify: `leadhook/leads/parsing.py`
- Create: `leadhook/core/middleware.py`
- Modify: `leadhook/settings.py` (`MIDDLEWARE`)
- Test: `tests/leads/test_parsing.py`, `tests/core/test_middleware.py`, `tests/core/test_crypto.py`, `tests/test_admin.py`

**Interfaces:**
- `simplify_field_data(field_data)` skips items that are not objects or have no name. `values` given as a string counts as one value. Any other non-list `values` counts as no value. A non-list input gives `{}`.
- `parse_meta_time(value)` returns `None` for a non-string or malformed value.
- `EncryptionKeyErrorMiddleware` turns `cryptography.fernet.InvalidToken` into a plain-text 500 page that explains the key mismatch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/leads/test_parsing.py`:
```python
def test_simplify_field_data_skips_malformed_items():
    field_data = [
        {"values": ["no name"]},
        "junk",
        {"name": "", "values": ["empty name"]},
        {"name": "city", "values": "Rome"},
        {"name": "note", "values": None},
    ]
    assert simplify_field_data(field_data) == {"city": "Rome", "note": ""}


def test_simplify_field_data_non_list_input():
    assert simplify_field_data({"full_name": "x"}) == {}


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01T00:00:00+0000", 1760517000, ""])
def test_parse_meta_time_malformed_is_none(value):
    assert parse_meta_time(value) is None
```
(add `import pytest` and `parse_meta_time` to the imports if missing).

`tests/core/test_middleware.py`:
```python
from cryptography.fernet import Fernet
from django.db import connection
from django.urls import reverse

from tests.factories import make_meta_app


def test_wrong_encryption_key_shows_readable_error(admin_client):
    app = make_meta_app()
    foreign = Fernet(Fernet.generate_key()).encrypt(b"app-secret").decode()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE meta_metaapp SET app_secret = %s WHERE id = %s", [foreign, app.pk])
    response = admin_client.get(reverse("admin:meta_metaapp_change", args=[app.pk]))
    assert response.status_code == 500
    assert b"LEADHOOK_ENCRYPTION_KEY" in response.content
```

In `tests/core/test_crypto.py` replace `test_encrypted_field_encrypts_on_save_and_decrypts_on_load` (add `from django.db import connection`, `from leadhook.core.crypto import decrypt`, `from leadhook.meta.models import MetaApp`, `from tests.factories import make_meta_app` as needed):
```python
def test_encrypted_field_encrypts_on_save_and_decrypts_on_load():
    app = make_meta_app(app_secret="top-secret")
    with connection.cursor() as cursor:
        cursor.execute("SELECT app_secret FROM meta_metaapp WHERE id = %s", [app.pk])
        raw = cursor.fetchone()[0]
    assert "top-secret" not in raw
    assert decrypt(raw) == "top-secret"  # really encrypted with our key, not just altered
    assert MetaApp.objects.get(pk=app.pk).app_secret == "top-secret"
```

Append to `tests/test_admin.py` (add `make_page` to the factories import):
```python
def test_page_and_destination_change_pages_never_show_secrets(admin_client):
    page = make_page(page_token="page-token-secret-1234")
    destination = make_destination(
        auth_type=Destination.AuthType.BEARER, secret="destination-secret-5678"
    )
    for url, secret, tail in [
        (reverse("admin:meta_page_change", args=[page.pk]), "page-token-secret-1234", "1234"),
        (
            reverse("admin:destinations_destination_change", args=[destination.pk]),
            "destination-secret-5678",
            "5678",
        ),
    ]:
        html = admin_client.get(url).content.decode()
        assert secret not in html
        assert f"Stored: ••••{tail}" in html


def test_saving_destination_without_secret_keeps_it(admin_client):
    destination = make_destination(auth_type=Destination.AuthType.BEARER, secret="keep-me")
    response = admin_client.post(
        reverse("admin:destinations_destination_change", args=[destination.pk]),
        {
            "name": "Renamed",
            "kind": "webhook",
            "url": destination.url,
            "method": "POST",
            "auth_type": "bearer",
            "auth_header_name": "",
            "secret": "",
            "extra_headers": "{}",
            "payload_template": "",
            "success_condition": "",
            "timeout_s": "15",
            "active": "on",
        },
    )
    assert response.status_code == 302
    destination.refresh_from_db()
    assert destination.name == "Renamed"
    assert destination.secret == "keep-me"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads/test_parsing.py tests/core tests/test_admin.py -v`
Expected: FAIL — parsing tests raise `KeyError`/`TypeError`; the middleware test gets an uncaught `InvalidToken`.

- [ ] **Step 3: Implement**

`leadhook/leads/parsing.py` — replace the two functions:
```python
def simplify_field_data(field_data) -> dict:
    """Map Meta field_data to {name: value}: one value -> string, several -> list, none -> "".

    Items that are not objects or have no name are skipped.
    """
    result: dict = {}
    if not isinstance(field_data, list):
        return result
    for field in field_data:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        values = field.get("values")
        if isinstance(values, str):
            values = [values]
        elif not isinstance(values, list):
            values = []
        name = str(field["name"])
        if len(values) == 0:
            result[name] = ""
        elif len(values) == 1:
            result[name] = values[0]
        else:
            result[name] = list(values)
    return result


def parse_meta_time(value) -> datetime | None:
    """Meta's timestamp as an aware datetime, or None when missing or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, META_TIME_FORMAT)
    except ValueError:
        return None
```

`leadhook/core/middleware.py`:
```python
import logging

from cryptography.fernet import InvalidToken
from django.http import HttpResponse

logger = logging.getLogger(__name__)

MESSAGE = (
    "LeadHook cannot decrypt the secrets stored in its database: LEADHOOK_ENCRYPTION_KEY is "
    "not the key they were encrypted with. Restore the original key and restart LeadHook."
)


class EncryptionKeyErrorMiddleware:
    """A readable error instead of a traceback when LEADHOOK_ENCRYPTION_KEY was changed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, InvalidToken):
            return None
        logger.error("Stored secrets cannot be decrypted: LEADHOOK_ENCRYPTION_KEY changed")
        return HttpResponse(MESSAGE, status=500, content_type="text/plain")
```

`leadhook/settings.py` — append `"leadhook.core.middleware.EncryptionKeyErrorMiddleware",` as the last item of `MIDDLEWARE`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix: robust field_data parsing, readable wrong-key error, stronger secret tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Admin dashboard, preview lead chooser, unrouted-leads prompt

**Files:**
- Create: `leadhook/leads/dashboard.py`, `leadhook/leads/templatetags/__init__.py` (empty), `leadhook/leads/templatetags/leadhook_dashboard.py`, `leadhook/leads/templates/admin/leadhook_index.html`, `leadhook/leads/templates/leads/dashboard.html`
- Modify: `leadhook/urls.py`, `leadhook/destinations/admin.py`, `leadhook/meta/admin.py`
- Test: `tests/leads/test_dashboard.py`, `tests/test_admin.py`

**Interfaces:**
- Produces: `dashboard_stats(now) -> dict` with keys `leads_today`, `leads_week`, `forms` (list of `{form_external_id, form__name, today, week}` sorted by `week` desc), `failed_deliveries`, `unrouted_leads`, `waiting_for_meta` (RECEIVED for more than 1 h), `pages` (all pages), `beat_ok`.
- Produces: `{% leadhook_dashboard %}` inclusion tag; `admin.site.index_template = "admin/leadhook_index.html"`.
- `DestinationAdmin` stores the requested preview lead on the object it loads for the request, never on the shared admin instance. The preview lists the 10 most recent fetched, non-purged leads as `?preview_lead=` links.
- `FormAdmin`/`PageAdmin`: after a save that leaves active destinations, a warning message with a link to the matching unrouted leads (spec §4: "the admin offers to route them"). `last_reconciled_at` and `silence_alerted_at` become read-only.

- [ ] **Step 1: Write the failing tests**

`tests/leads/test_dashboard.py`:
```python
from datetime import timedelta

from django.utils import timezone

from leadhook.leads.dashboard import dashboard_stats
from leadhook.leads.models import Lead
from tests.factories import make_lead


def test_dashboard_stats_counts_by_form():
    now = timezone.now()
    lead = make_lead()
    older = make_lead(leadgen_id="2", form=lead.form)
    Lead.objects.filter(pk=older.pk).update(received_at=now - timedelta(days=3))
    make_lead(leadgen_id="3", form=lead.form, status=Lead.Status.UNROUTED)
    stats = dashboard_stats(now)
    assert stats["leads_today"] == 2
    assert stats["leads_week"] == 3
    assert stats["unrouted_leads"] == 1
    assert stats["forms"] == [
        {"form_external_id": "222", "form__name": "Test form", "today": 2, "week": 3}
    ]
    assert stats["beat_ok"] is False
```

Append to `tests/test_admin.py` (add `from django.contrib import admin as django_admin` and `from leadhook.meta.models import Page`):
```python
def test_dashboard_shows_counts_and_page_health(admin_client):
    lead = make_lead(status=Lead.Status.UNROUTED)
    Page.objects.filter(pk=lead.page.pk).update(error="Meta rejected the page token: x")
    failed = make_lead(leadgen_id="2", form=lead.form, status=Lead.Status.FAILED)
    Delivery.objects.create(
        lead=failed, destination=make_destination(), status=Delivery.Status.FAILED
    )
    html = admin_client.get(reverse("admin:index")).content.decode()
    assert "Leads today" in html
    assert "Meta rejected the page token: x" in html
    assert 'status__exact=unrouted">1</a>' in html
    assert 'status__exact=failed">1</a>' in html
    assert "Not running" in html  # no heartbeat recorded in tests


def test_payload_preview_lead_chooser_is_per_request(admin_client):
    destination = make_destination()
    first = make_lead(leadgen_id="1")
    make_lead(leadgen_id="2", form=first.form)
    url = reverse("admin:destinations_destination_change", args=[destination.pk])
    html = admin_client.get(url + "?preview_lead=1").content.decode()
    assert "Using lead 1" in html
    assert 'href="?preview_lead=2"' in html
    assert not hasattr(django_admin.site._registry[Destination], "_preview_lead_id")


def test_saving_form_with_destinations_offers_unrouted_leads(admin_client):
    lead = make_lead(status=Lead.Status.UNROUTED)
    form = lead.form
    destination = make_destination()
    response = admin_client.post(
        reverse("admin:meta_form_change", args=[form.pk]),
        {
            "page": form.page.pk,
            "form_id": form.form_id,
            "name": form.name,
            "locale": form.locale,
            "status": form.status,
            "destinations": [destination.pk],
        },
        follow=True,
    )
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "1 unrouted lead(s)" in text
    assert f"form__id__exact={form.pk}" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads/test_dashboard.py tests/test_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.leads.dashboard'`.

- [ ] **Step 3: Implement**

`leadhook/leads/dashboard.py`:
```python
"""Numbers for the admin home page (spec §9)."""

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from leadhook.core.health import heartbeat_is_fresh
from leadhook.destinations.models import Delivery
from leadhook.meta.models import Page

from .models import Lead


def dashboard_stats(now: datetime) -> dict:
    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    recent = Lead.objects.filter(received_at__gte=week_start)
    forms = (
        recent.values("form_external_id", "form__name")
        .annotate(today=Count("pk", filter=Q(received_at__gte=day_start)), week=Count("pk"))
        .order_by("-week", "form_external_id")
    )
    return {
        "leads_today": Lead.objects.filter(received_at__gte=day_start).count(),
        "leads_week": recent.count(),
        "forms": list(forms),
        "failed_deliveries": Delivery.objects.filter(status=Delivery.Status.FAILED).count(),
        "unrouted_leads": Lead.objects.filter(status=Lead.Status.UNROUTED).count(),
        "waiting_for_meta": Lead.objects.filter(
            status=Lead.Status.RECEIVED, received_at__lt=now - timedelta(hours=1)
        ).count(),
        "pages": Page.objects.order_by("name", "page_id"),
        "beat_ok": heartbeat_is_fresh(),
    }
```

`leadhook/leads/templatetags/leadhook_dashboard.py`:
```python
from django import template
from django.utils import timezone

from leadhook.leads.dashboard import dashboard_stats

register = template.Library()


@register.inclusion_tag("leads/dashboard.html")
def leadhook_dashboard() -> dict:
    return dashboard_stats(timezone.now())
```

`leadhook/leads/templates/admin/leadhook_index.html`:
```django
{% extends "admin/index.html" %}
{% load leadhook_dashboard %}
{% block content %}{% leadhook_dashboard %}{{ block.super }}{% endblock %}
```

`leadhook/leads/templates/leads/dashboard.html`:
```django
<div class="module" id="leadhook-dashboard">
  <table>
    <caption>Overview</caption>
    <tr><th scope="row">Leads today</th><td>{{ leads_today }}</td></tr>
    <tr><th scope="row">Leads in the last 7 days</th><td>{{ leads_week }}</td></tr>
    <tr><th scope="row">Failed deliveries</th>
      <td><a href="{% url 'admin:destinations_delivery_changelist' %}?status__exact=failed">{{ failed_deliveries }}</a></td></tr>
    <tr><th scope="row">Unrouted leads</th>
      <td><a href="{% url 'admin:leads_lead_changelist' %}?status__exact=unrouted">{{ unrouted_leads }}</a></td></tr>
    <tr><th scope="row">Leads waiting for Meta for over 1 hour</th>
      <td><a href="{% url 'admin:leads_lead_changelist' %}?status__exact=received">{{ waiting_for_meta }}</a></td></tr>
    <tr><th scope="row">Background jobs</th>
      <td>{% if beat_ok %}Running{% else %}<strong>Not running</strong>: check the worker and beat containers.{% endif %}</td></tr>
  </table>
</div>
<div class="module">
  <table>
    <caption>Pages</caption>
    <thead><tr><th scope="col">Page</th><th scope="col">Subscribed</th><th scope="col">Last lead</th><th scope="col">Last check</th><th scope="col">Problem</th></tr></thead>
    {% for page in pages %}
      <tr>
        <td><a href="{% url 'admin:meta_page_change' page.pk %}">{{ page }}</a></td>
        <td>{{ page.subscribed|yesno:"Yes,No" }}</td>
        <td>{{ page.last_lead_at|default:"—" }}</td>
        <td>{{ page.last_checked_at|default:"—" }}</td>
        <td>{{ page.error|default:"—" }}</td>
      </tr>
    {% empty %}
      <tr><td colspan="5">No pages connected yet.</td></tr>
    {% endfor %}
  </table>
</div>
<div class="module">
  <table>
    <caption>Leads by form (last 7 days)</caption>
    <thead><tr><th scope="col">Form</th><th scope="col">Today</th><th scope="col">7 days</th></tr></thead>
    {% for row in forms %}
      <tr><td>{{ row.form__name|default:row.form_external_id }}</td><td>{{ row.today }}</td><td>{{ row.week }}</td></tr>
    {% empty %}
      <tr><td colspan="3">No leads in the last 7 days.</td></tr>
    {% endfor %}
  </table>
</div>
```

`leadhook/urls.py` — after `admin.site.index_title = ...` add:
```python
admin.site.index_template = "admin/leadhook_index.html"
```

`leadhook/destinations/admin.py` — delete the `get_form` override and add (import `format_html_join` next to `format_html`):
```python
    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        if obj is not None:
            # Per request: the admin instance is shared between threads.
            obj._preview_lead_id = request.GET.get("preview_lead")
        return obj
```
In `payload_preview` read the id from the object, restrict to fetched, non-purged leads, and add the chooser:
```python
        leads = Lead.objects.exclude(status=Lead.Status.RECEIVED).filter(purged_at__isnull=True)
        lead_id = getattr(obj, "_preview_lead_id", None)
        lead = leads.filter(leadgen_id=lead_id).first() if lead_id else leads.first()
        if lead is None:
            return "No fetched leads yet."
        ...  # build_context / render_body / TemplateError handling unchanged
        others = format_html_join(
            " · ",
            '<a href="?preview_lead={}">{}</a>',
            ((other.leadgen_id, other.leadgen_id) for other in leads[:10]),
        )
        return format_html(
            "<p>Using lead {}. Preview another: {}</p><pre>{}</pre>", lead.leadgen_id, others, body
        )
```
If `test_destination_payload_preview` asserts the old sentence, update it to `"Using lead"`.

`leadhook/meta/admin.py` — add imports (`from django.contrib import messages`, `from django.urls import reverse`, `from django.utils.html import format_html`, `from leadhook.leads.models import Lead`) and:
```python
def _offer_unrouted_leads(modeladmin, request, leads, lead_filter: str) -> None:
    """Spec §4: once destinations exist, point to the leads that waited for one."""
    count = leads.filter(status=Lead.Status.UNROUTED).count()
    if not count:
        return
    url = reverse("admin:leads_lead_changelist") + f"?status__exact=unrouted&{lead_filter}"
    modeladmin.message_user(
        request,
        format_html(
            '{} unrouted lead(s) can now be delivered: <a href="{}">select them and run '
            "“Reprocess selected leads”</a>.",
            count,
            url,
        ),
        messages.WARNING,
    )
```
In `PageAdmin` add `"silence_alerted_at"` to `readonly_fields` and:
```python
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        page = form.instance
        if page.default_destinations.filter(active=True).exists():
            _offer_unrouted_leads(self, request, page.leads.all(), f"page__id__exact={page.pk}")
```
In `FormAdmin` set `readonly_fields = ("last_synced_at", "last_reconciled_at")` and:
```python
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        lead_form = form.instance
        if lead_form.destinations.filter(active=True).exists():
            _offer_unrouted_leads(
                self, request, lead_form.leads.all(), f"form__id__exact={lead_form.pk}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(admin): dashboard, per-request payload preview chooser, unrouted-leads prompt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Onboarding wizard

**Files:**
- Create: `leadhook/meta/setup.py`, `leadhook/meta/templates/admin/leadhook_setup.html`
- Modify: `leadhook/meta/services.py` (`connect_page`), `leadhook/urls.py`, `leadhook/leads/dashboard.py`, `leadhook/leads/templates/leads/dashboard.html`
- Test: `tests/meta/test_setup.py`

**Interfaces:**
- Produces: `GET/POST /admin/setup/` (URL name `setup`, staff only via `admin.site.admin_view`). POST `action` is one of:
  - `verify`: checks `/{app_id}/subscriptions` for a `page` subscription with this callback URL and the `leadgen` field.
  - `load_pages`: renders the pages from `/me/accounts` as checkboxes. Page tokens never reach the HTML.
  - `connect_pages`: lists the pages again server-side, then stores, subscribes and form-syncs each selected page.
  - `test_lead`: `POST /{form_id}/test_leads`.
- Produces: `setup_status() -> dict` (`meta_app`, `has_app`, `has_pages`, `has_destination`, `has_lead`), `setup_complete() -> bool` (app with token, a subscribed page, an active destination assigned to a page or form); `connect_page(meta_app, raw) -> tuple[Page, int]`.
- Dashboard: shows "Continue the guided setup" until `setup_complete()`.
- Step 1 of spec §10 (admin user) is satisfied by being signed in; step 2 links to the existing Meta app admin form.

- [ ] **Step 1: Write the failing tests**

`tests/meta/test_setup.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta/test_setup.py -v`
Expected: FAIL — `/admin/setup/` returns 404.

- [ ] **Step 3: Implement**

`leadhook/meta/services.py` — add:
```python
def connect_page(meta_app: MetaApp, raw: dict) -> tuple[Page, int]:
    """Store a page listed by /me/accounts, subscribe it to leadgen and sync its forms."""
    defaults = {"meta_app": meta_app, "name": fit_to_field(Page, "name", raw.get("name"))}
    if raw.get("access_token"):
        defaults["page_token"] = raw["access_token"]
    page, _ = Page.objects.update_or_create(page_id=str(raw["id"]), defaults=defaults)
    client = client_for_page(page)
    client.subscribe_leadgen(page.page_id)
    Page.objects.filter(pk=page.pk).update(subscribed=True, error="")
    page.subscribed, page.error = True, ""
    return page, sync_page_forms(page, client)
```

`leadhook/meta/setup.py`:
```python
"""Guided first-run setup in the admin (spec §10)."""

from django.contrib import messages
from django.contrib.admin import site
from django.db.models import Q
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead

from .graph import GraphClient, GraphError, app_subscriptions, client_for_page
from .models import Form, MetaApp, Page
from .services import connect_page

TEMPLATE = "admin/leadhook_setup.html"


def setup_status() -> dict:
    meta_app = MetaApp.objects.order_by("pk").first()
    return {
        "meta_app": meta_app,
        "has_app": bool(meta_app and meta_app.access_token),
        "has_pages": Page.objects.filter(subscribed=True).exists(),
        "has_destination": Destination.objects.filter(active=True)
        .filter(Q(default_for_pages__isnull=False) | Q(forms__isnull=False))
        .exists(),
        "has_lead": Lead.objects.exists(),
    }


def setup_complete() -> bool:
    status = setup_status()
    return status["has_app"] and status["has_pages"] and status["has_destination"]


def setup_view(request):
    status = setup_status()
    meta_app = status["meta_app"]
    context = {
        **site.each_context(request),
        **status,
        "title": "Set up LeadHook",
        "available_pages": None,
        "forms": Form.objects.filter(page__subscribed=True)
        .select_related("page")
        .order_by("page__name", "name"),
    }
    if request.method == "POST" and meta_app is not None:
        action = request.POST.get("action")
        if action == "verify":
            _verify_webhook(request, meta_app)
        elif action == "load_pages":
            pages = _load_pages(request, meta_app)
            if pages is not None:
                return TemplateResponse(request, TEMPLATE, {**context, "available_pages": pages})
        elif action == "connect_pages":
            _connect_pages(request, meta_app, request.POST.getlist("page_ids"))
        elif action == "test_lead":
            _send_test_lead(request, request.POST.get("form_id", ""))
        return redirect("setup")
    return TemplateResponse(request, TEMPLATE, context)


def _verify_webhook(request, meta_app: MetaApp) -> None:
    try:
        subscriptions = app_subscriptions(meta_app)
    except GraphError as exc:
        messages.error(request, f"Could not read the app's webhook subscriptions: {exc}")
        return
    for subscription in subscriptions:
        fields = {f.get("name") for f in subscription.get("fields") or [] if isinstance(f, dict)}
        if (
            subscription.get("object") == "page"
            and subscription.get("callback_url") == meta_app.webhook_url()
            and "leadgen" in fields
        ):
            messages.success(request, "Webhook verified: Meta sends page leadgen events here.")
            return
    messages.error(
        request,
        "Meta has no Page subscription with the leadgen field for this callback URL yet. "
        "Complete the webhook step, then verify again.",
    )


def _user_pages(meta_app: MetaApp) -> list[dict]:
    return GraphClient(meta_app.graph_version, meta_app.access_token).list_pages()


def _load_pages(request, meta_app: MetaApp) -> list[dict] | None:
    try:
        pages = _user_pages(meta_app)
    except GraphError as exc:
        messages.error(request, f"Could not list pages: {exc}")
        return None
    connected = set(Page.objects.values_list("page_id", flat=True))
    # Only id and name: page tokens stay server side and are fetched again on connect.
    return [
        {"id": str(p["id"]), "name": p.get("name", ""), "connected": str(p["id"]) in connected}
        for p in pages
        if p.get("id")
    ]


def _connect_pages(request, meta_app: MetaApp, page_ids: list[str]) -> None:
    if not page_ids:
        messages.warning(request, "Select at least one page.")
        return
    try:
        available = {str(p.get("id")): p for p in _user_pages(meta_app)}
    except GraphError as exc:
        messages.error(request, f"Could not list pages: {exc}")
        return
    for page_id in page_ids:
        raw = available.get(page_id)
        if raw is None:
            messages.error(request, f"Page {page_id} is not reachable with the app's token.")
            continue
        try:
            page, form_count = connect_page(meta_app, raw)
        except GraphError as exc:
            messages.error(request, f"Page {raw.get('name') or page_id}: {exc}")
            continue
        messages.success(request, f"Connected {page} with {form_count} form(s).")


def _send_test_lead(request, form_id: str) -> None:
    form = Form.objects.select_related("page__meta_app").filter(form_id=form_id).first()
    if form is None:
        messages.error(request, "Choose a form.")
        return
    try:
        client_for_page(form.page).create_test_lead(form.form_id)
    except GraphError as exc:
        messages.error(
            request,
            f"Meta refused the test lead: {exc}. Meta keeps one test lead per form: delete "
            "the previous one in the Lead Ads Testing Tool and try again.",
        )
        return
    messages.success(
        request,
        format_html(
            'Test lead requested. It appears in <a href="{}">Leads</a> as soon as Meta sends '
            "the webhook, usually within a minute.",
            reverse("admin:leads_lead_changelist"),
        ),
    )
```

`leadhook/meta/templates/admin/leadhook_setup.html`:
```django
{% extends "admin/base_site.html" %}
{% block breadcrumbs %}<div class="breadcrumbs"><a href="{% url 'admin:index' %}">Home</a> › Set up</div>{% endblock %}
{% block content %}
<div id="content-main">
<ol>
  <li>
    <h2>✔ Admin user</h2>
    <p>You are signed in as {{ request.user.get_username }}.</p>
  </li>
  <li>
    <h2>{% if has_app %}✔{% else %}○{% endif %} Meta app</h2>
    <p>Enter the App ID, App secret and a System User access token. The README's
      “Meta app setup” section explains how to create them.</p>
    {% if meta_app %}
      <p><a class="button" href="{% url 'admin:meta_metaapp_change' meta_app.pk %}">Edit {{ meta_app.name }}</a></p>
    {% else %}
      <p><a class="button" href="{% url 'admin:meta_metaapp_add' %}">Add the Meta app</a></p>
    {% endif %}
  </li>
  {% if meta_app %}
  <li>
    <h2>Webhook</h2>
    <p>In the Meta app dashboard open <strong>Webhooks</strong>, choose <strong>Page</strong>,
      subscribe with these values, then subscribe to the <code>leadgen</code> field.</p>
    <p>Callback URL: <code>{{ meta_app.webhook_url }}</code><br>
      Verify token: <code>{{ meta_app.verify_token }}</code></p>
    <form method="post">{% csrf_token %}<button type="submit" name="action" value="verify">Verify</button></form>
  </li>
  <li>
    <h2>{% if has_pages %}✔{% else %}○{% endif %} Pages</h2>
    {% if available_pages is not None %}
      <form method="post">{% csrf_token %}
        {% for page in available_pages %}
          <p><label><input type="checkbox" name="page_ids" value="{{ page.id }}"{% if page.connected %} checked{% endif %}>
            {{ page.name }} ({{ page.id }})</label></p>
        {% empty %}
          <p>No pages are reachable with this token. Assign pages to the System User in Business Settings.</p>
        {% endfor %}
        <button type="submit" name="action" value="connect_pages">Connect selected pages</button>
      </form>
    {% else %}
      <p>Connecting a page subscribes it to lead events and loads its forms.</p>
      <form method="post">{% csrf_token %}<button type="submit" name="action" value="load_pages">Load pages</button></form>
    {% endif %}
  </li>
  <li>
    <h2>{% if has_destination %}✔{% else %}○{% endif %} Destination</h2>
    <p>Create a destination (for example your n8n webhook), then pick it as a page default or on a form.</p>
    <p><a class="button" href="{% url 'admin:destinations_destination_add' %}">Add a destination</a>
      <a class="button" href="{% url 'admin:meta_page_changelist' %}">Set page defaults</a></p>
  </li>
  <li>
    <h2>{% if has_lead %}✔{% else %}○{% endif %} Test lead</h2>
    {% if forms %}
      <form method="post">{% csrf_token %}
        <label for="form_id">Form</label>
        <select name="form_id" id="form_id">
          {% for form in forms %}<option value="{{ form.form_id }}">{{ form.page.name }} — {{ form }}</option>{% endfor %}
        </select>
        <button type="submit" name="action" value="test_lead">Send test lead</button>
      </form>
    {% else %}
      <p>Connect a page first.</p>
    {% endif %}
  </li>
  {% endif %}
</ol>
</div>
{% endblock %}
```

`leadhook/urls.py` — import `from leadhook.meta.setup import setup_view` and add **before** the `admin/` entry:
```python
    path("admin/setup/", admin.site.admin_view(setup_view), name="setup"),
```

`leadhook/leads/dashboard.py` — add `"setup_complete": setup_complete(),` to the returned dict, importing `setup_complete` inside `dashboard_stats` (`from leadhook.meta.setup import setup_complete`) to avoid an import cycle.

`leadhook/leads/templates/leads/dashboard.html` — first line:
```django
{% if not setup_complete %}<p class="errornote">Setup is not complete. <a href="{% url 'setup' %}">Continue the guided setup</a>.</p>{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(admin): guided setup wizard with page connection and test lead

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Packaging and documentation

**Files:**
- Modify: `docker/entrypoint.sh`, `.env.example`, `README.md`, `docs/superpowers/plans/2026-10-15-plan-2-backlog.md`

**Interfaces:**
- Worker and beat wait until `migrate --check` passes, so they never run against an unmigrated database.
- Docs cover the new settings, alerts, `/health/`, retention, reconciliation, the setup wizard and the Meta app guide.

- [ ] **Step 1: Implement**

`docker/entrypoint.sh`: add the function after `set -e`, then call it before the `exec` of `worker` and `beat`:
```sh
wait_for_migrations() {
  # The web container applies migrations; worker and beat must not run before that.
  until python manage.py migrate --check >/dev/null 2>&1; do
    echo "Waiting for database migrations..."
    sleep 3
  done
}
```
```sh
  worker)
    wait_for_migrations
    exec celery -A leadhook worker --loglevel "${LOG_LEVEL:-info}"
    ;;
  beat)
    wait_for_migrations
    exec celery -A leadhook beat --loglevel "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
```

`.env.example` — append to the optional section:
```sh
# Alert emails (failed deliveries, unrouted leads, page/token problems, silence).
# EMAIL_URL examples: smtp+tls://user:password@smtp.example.com:587 — default prints to the log.
EMAIL_URL=consolemail://
DEFAULT_FROM_EMAIL=leadhook@example.com
ALERT_EMAILS=ops@example.com
# Days after which lead answers, payloads and request/response bodies are erased.
LEAD_RETENTION_DAYS=90
# Alert when a page that usually gets leads has had none for this many hours.
SILENCE_ALERT_HOURS=24
# CA bundle for destinations served with a private certificate authority.
# REQUESTS_CA_BUNDLE=/etc/ssl/certs/internal-ca.pem
```

`README.md`:
1. **Features** — append:
   - `- No lost leads: reconciliation every 15 minutes recovers leads whose webhook never arrived, and a sweeper re-dispatches work lost by a restart.`
   - `- Email alerts, an admin dashboard and a /health/ endpoint for uptime monitors.`
   - `- Guided setup in the admin, including a "Send test lead" button.`
   - `- Personal data erased after LEAD_RETENTION_DAYS (90 by default).`
2. **Quick start** — after `Open https://<your-domain>/admin/.` add: `Then open **Set up LeadHook** (`/admin/setup/`, also linked from the dashboard) and follow the steps.`
3. Replace the **Meta app setup** section with:
```markdown
## Meta app setup

LeadHook uses your own Meta app. One-time preparation in Meta:

1. **Business Verification.** In Business Settings → Security Center, verify your business. Without it the app cannot go Live, and apps in development mode only receive test leads.
2. **App.** In [Meta for Developers](https://developers.facebook.com/apps/) create an app of type **Business**, add the **Webhooks** product, and note the App ID and App secret (App settings → Basic).
3. **Permissions.** The app needs `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement` and `pages_manage_ads`. For your own pages Standard Access is enough; no App Review is needed while the System User belongs to the same business.
4. **System User.** In Business Settings → Users → System users, create an admin System User, assign it your pages (full control) and the app, then generate a token for the app with the permissions above. System User tokens never expire.
5. **Leads Access.** In Business Settings → Integrations → Leads Access, allow the app (or the System User) for each page; otherwise Meta returns leads without answers.
6. **Live mode.** Switch the app to Live.

Then in LeadHook, open **Set up LeadHook** (`/admin/setup/`):

1. Add the Meta app with the App ID, App secret and System User token.
2. Copy the **Callback URL** and **Verify token** into the Meta app: Webhooks → **Page** → Subscribe to this object, then subscribe to the `leadgen` field. Press **Verify**.
3. **Load pages**, select yours and **Connect**: LeadHook subscribes each page to lead events and imports its forms.
4. Create a destination and set it as a page default (or on single forms).
5. **Send test lead** and watch it arrive under Leads and Deliveries.
```
4. Add a section before **Configuration**:
```markdown
## Monitoring

- **Dashboard** (admin home): leads today and per form, failed deliveries, unrouted leads, page health, background job status.
- **Email alerts** to `ALERT_EMAILS`: permanently failed delivery, unrouted lead, page token or subscription problem, lead that could not be fetched, unusual silence. At most one email per kind every 15 minutes.
- **`GET /health/`** returns 200 with `{"database": "ok", "redis": "ok", "beat": "ok"}`, or 503 naming what is wrong. `beat` turns `stale` when the background jobs have not run for 5 minutes. The endpoint is exempt from the HTTPS redirect; the `Host` header must still be in `ALLOWED_HOSTS`.
- **Background jobs** (Celery beat): reconciliation (15 min), sweeper (5 min), form sync (1 h), silence check (1 h), page and token check (daily), retention purge (daily).
```
5. **Configuration** table — add rows:
```markdown
| `EMAIL_URL` | `consolemail://` | SMTP server for alerts, e.g. `smtp+tls://user:password@smtp.example.com:587`. |
| `DEFAULT_FROM_EMAIL` | `leadhook@localhost` | Sender of alert emails. |
| `ALERT_EMAILS` | — | Comma-separated recipients; no alerts when empty. |
| `LEAD_RETENTION_DAYS` | `90` | Lead answers, webhook payloads and request/response bodies older than this are erased. |
| `SILENCE_ALERT_HOURS` | `24` | Alert when a page that had leads in the last 30 days has none for this long. |
| `REQUESTS_CA_BUNDLE` | — | CA bundle for destinations behind a private certificate authority. |
```
Also change the `REDIS_URL` row description to `Celery broker and cache (alert throttling, heartbeat).`

`docs/superpowers/plans/2026-10-15-plan-2-backlog.md` — add below the title:
```markdown
> **Status:** triaged in `2026-10-19-plan-2-operations.md` (section "Backlog triage"); every item is either implemented there, already fixed, or dropped with its ruling.
```

- [ ] **Step 2: Verify**

Run: `sh -n docker/entrypoint.sh && docker build -t leadhook:plan2 . && uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: syntax OK, image builds, all tests pass. If Docker is unavailable locally, rely on the CI `docker` job and say so in the task report.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: monitoring, alerts, retention and guided Meta setup; worker waits for migrations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pytest` green on SQLite.
- [ ] `LEADHOOK_TEST_DATABASE_URL=postgres://… uv run pytest` green on PostgreSQL (or the CI `test-postgres` job).
- [ ] `DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations --check --dry-run` reports no changes.
- [ ] Every row of "Backlog triage" marked as a task is implemented, and every Review Focus test exists by name.
- [ ] Open a PR from `feat/operations` to `main`.
