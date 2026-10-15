# LeadHook — Design

- **Date:** 2026-10-15
- **Status:** approved in discussion, pending spec review
- **Name:** "LeadHook" is a working title

## 1. Goal

An **open source, self-hosted** connector for Facebook/Instagram Lead Ads: it receives leads from **many pages and forms at once** through a single Meta app and forwards them to configurable webhook destinations (n8n, Make, any CRM with an API, etc.).

Motivation: replace a paid service (LeadSync) that was adopted because n8n's Facebook Lead Ads trigger supports only one subscription at a time (Meta accepts a single callback URL per app), and because an n8n router with hard-coded form IDs makes adding new campaigns tedious.

### Success criteria

1. Adding a new campaign requires no code or workflow changes: at most, assigning a destination to a form picked from a list.
2. No lost leads: every event is persisted before Meta gets a response; periodic reconciliation recovers leads whose webhook never arrived.
3. n8n workflows that currently receive leads from LeadSync or from n8n's Facebook trigger keep working unchanged (compatible payload).
4. Installation with `docker compose up` and guided setup in the admin.

### Out of scope (v1)

- Multi-tenancy / third-party pages via Facebook Login (requires Meta Advanced Access). The data model is ready for it (`Workspace`), but v1 uses a single workspace.
- Destinations other than webhooks (email, Google Sheets, Telegram): planned via the `Destination` abstraction.
- Conditional routing on field values (belongs downstream, e.g. in n8n).
- Visual field-mapping UI (future, built on top of templates).
- Hosted cloud offering.
- Translations: the product ships in English only.

## 2. Key decisions

| Topic | Decision |
|---|---|
| Language | English only: code, identifiers, comments, UI, docs, README, commit messages |
| Distribution model | Self-hosted, "bring your own Meta app": each installation uses its own Meta app and its own pages |
| Stack | Python, Django (admin as the primary UI) |
| Background work | Celery + Redis (worker + beat) |
| Database | PostgreSQL in production, SQLite for development only |
| v1 destinations | HTTP webhook only, with standard payload or custom template |
| Routing | Form → N destinations; fallback to the page's default destinations; otherwise `unrouted` |
| License | AGPL-3.0 |

## 3. Architecture

```
             Meta (leadgen webhook)
                      │ POST /webhooks/meta/<uuid>/
                      ▼
┌──────────────── web (Django + gunicorn) ─────────────────┐
│ • verifies X-Hub-Signature-256 + hub.challenge           │
│ • stores raw event → responds 200 immediately            │
│ • admin: Meta app, pages, forms, destinations, leads     │
└────────────────────────────┬─────────────────────────────┘
                             │ Celery task
                ┌────────────▼──────────────┐
  Redis ◄──────►│     worker (Celery)        │
 (broker)       │ 1. fetch_lead: Graph API   │
                │ 2. route: rules → dests    │
                │ 3. deliver: POST webhook   │──► n8n / CRM / …
                │    retry with backoff      │
                └────────────▲──────────────┘
                             │ periodic
                ┌────────────┴──────────────┐
                │     beat (Celery)          │
                │ • lead reconciliation      │
                │ • form sync                │
                │ • token/subscription check │
                │ • data retention purge     │
                └───────────────────────────┘
                     PostgreSQL
```

- **Single Docker image** started in 3 roles: `web`, `worker`, `beat`. `docker-compose.yml` also includes `redis` and `postgres`.
- **Pipeline of separate tasks:** `process_event` → `fetch_lead` → `route_lead` → `deliver` (one task per lead/destination pair). A failure towards one destination retries only that delivery.
- **`Destination` abstraction:** base class with `render(lead) -> Request` and `send(request) -> Result`; v1 implements only `WebhookDestination`.

### Modules (Django apps)

| App | Responsibility |
|---|---|
| `core` | `Workspace`, secret encryption, settings, health check |
| `meta` | `MetaApp`, `Page`, `Form`; Graph API client; webhook endpoint; form sync; subscription and token checks |
| `leads` | `WebhookEvent`, `Lead`; event parsing, lead fetch, reconciliation, retention |
| `routing` | Form → destinations logic |
| `destinations` | `Destination`, `Delivery`; template rendering, sending, retries, success condition, SSRF protection |
| `alerts` | Email alerts with throttling |
| `dashboard` | Admin home, onboarding wizard |

## 4. Data model

🔒 = encrypted field (Fernet, key from `LEADHOOK_ENCRYPTION_KEY`).

### Configuration

**`Workspace`**: `name`. Exactly one in v1, created by a data migration.

**`MetaApp`**: `workspace`, `app_id`, `app_secret`🔒, `verify_token`, `webhook_uuid`, `access_token`🔒, `token_type` (`system_user` | `user`), `token_expires_at` (null for system user tokens), `graph_version` (e.g. `v21.0`).

**`Page`**: `meta_app`, `page_id` (unique), `name`, `page_token`🔒, `subscribed` (bool), `last_checked_at`, `last_lead_at`, `error` (text, empty when healthy), `default_destinations` (M2M → `Destination`).

**`Form`**: `page`, `form_id` (unique), `name`, `status` (`ACTIVE` | `ARCHIVED` | …), `destinations` (M2M → `Destination`), `last_synced_at`.

**`Destination`**: `workspace`, `name`, `kind` (only `webhook` in v1), `url`, `method` (`POST` | `PUT`), `auth_type` (`none` | `header` | `bearer` | `hmac`), `auth_header_name`, `secret`🔒, `extra_headers` (JSON), `payload_template` (text; empty = standard payload), `success_condition` (text; empty = any 2xx), `timeout_s` (default 15), `active`.

### Operational data

**`WebhookEvent`**: `received_at`, `payload` (JSON), `signature_ok`, `processed_at`, `error`.

**`Lead`**: `leadgen_id` (unique), `page`, `form`, `created_time`, `ad_id`, `ad_name`, `adset_id`, `adset_name`, `campaign_id`, `campaign_name`, `is_organic`, `field_data` (raw Meta JSON), `data` (simplified JSON), `source` (`webhook` | `reconcile` | `manual`), `status`, `error`.

Lead states: `received` → `fetched` → `routed` | `unrouted` → `delivered` | `partially_failed` | `failed`.

**`Delivery`**: `lead`, `destination`, `status` (`pending` | `success` | `failed`), `attempts`, `next_retry_at`, `last_status_code`, `last_error`, `request_body`, `response_body` (truncated to 4 KB), `delivered_at`. Unique on (`lead`, `destination`).

### Rules

- **Routing:** if `form.destinations` is non-empty → use those; otherwise `page.default_destinations`; if still empty → lead is `unrouted`. Only `active` destinations are used. When destinations are assigned to a form that has `unrouted` leads, the admin offers to route them.
- **Unknown form:** if a lead arrives for a `form_id` not yet stored, the form is created on the fly by reading it from the Graph API.
- **Deduplication:** `Lead.leadgen_id` is unique; a lead that already exists (from webhook or reconciliation) is neither recreated nor re-sent.
- **Aggregate lead status:** all `Delivery` rows `success` → `delivered`; all `failed` → `failed`; mixed → `partially_failed`.
- **Retention:** `LEAD_RETENTION_DAYS` (default 90). A daily task clears `field_data`, `data`, `request_body`, `response_body` and `WebhookEvent.payload` older than that, keeping metadata.

## 5. Flow

1. **Receive.** `POST /webhooks/meta/<webhook_uuid>/`: verify `X-Hub-Signature-256` against the raw body with `app_secret`; store `WebhookEvent`; enqueue `process_event`; respond 200. Invalid signature → stored with `signature_ok=false`, not processed, respond 200.
2. **`process_event`.** Extract every `entry[].changes[]` with `field == "leadgen"` → (`leadgen_id`, `page_id`, `form_id`). Create `Lead` (`received`) if it does not exist; enqueue `fetch_lead`.
3. **`fetch_lead`.** `GET /{leadgen_id}?fields=id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,is_organic` using the page token. Build `data`. Status `fetched`; enqueue `route_lead`.
4. **`route_lead`.** Apply routing rules; create one `Delivery` per destination; enqueue `deliver` for each.
5. **`deliver`.** Render the payload, send, evaluate the outcome, update `Delivery` and the aggregate lead status.

### Periodic tasks (beat)

| Task | Interval | What it does |
|---|---|---|
| `reconcile_leads` | 15 min | For each `ACTIVE` form on connected pages: `GET /{form_id}/leads` filtered by `time_created > last_check − 1h`; create missing leads with `source=reconcile` |
| `sync_forms` | 1 h | Refresh each page's form list |
| `check_pages` | 1 day | Validate token (`debug_token`) and `subscribed_apps` subscription; resubscribe if missing; flag errors |
| `check_silence` | 1 h | Alert if a page that usually receives leads has had none for `SILENCE_ALERT_HOURS` (default 24) |
| `purge_old_data` | 1 day | Retention |
| `heartbeat` | 1 min | Update the timestamp checked by `/health/` |

## 6. Webhook payload

### Standard payload (empty template)

The top level matches the "Simplify" output of n8n's Facebook Lead Ads trigger; extra fields are additive only.

```json
{
  "id": "1234567890123456",
  "created_time": "2026-10-15T08:30:00+0000",
  "data": { "full_name": "Jane Doe", "phone_number": "+15551234567" },
  "form":     { "id": "…", "name": "…", "locale": "en_US", "status": "ACTIVE" },
  "page":     { "id": "…", "name": "…" },
  "ad":       { "id": "…", "name": "…" },
  "adset":    { "id": "…", "name": "…" },
  "campaign": { "id": "…", "name": "…" },
  "is_organic": false,
  "field_data": [ { "name": "full_name", "values": ["Jane Doe"] } ],
  "_leadhook": { "version": 1, "delivery_id": "…", "attempt": 1, "source": "webhook", "sent_at": "…" }
}
```

In `data`: single value → string; multiple values → array.

### Custom template

- Engine: **Jinja2 `SandboxedEnvironment`**, autoescape off, undefined variables render as empty.
- Context: the same fields as the standard payload (`id`, `data`, `form`, `page`, `ad`, `adset`, `campaign`, `created_time`, `is_organic`, `field_data`, `_leadhook`).
- The rendered output must be valid JSON; otherwise the delivery fails without retry.
- Built-in filters: `tojson`, `first_word`, `rest_words`, `phone_strip_prefix(prefix)` (removes whitespace and the given country prefix, e.g. `"+39"`), `date(tz, format)`, `map(dict)` (unmapped value → unchanged), `default`.
- Admin: preview of the rendered payload against a real lead of choice.

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `Idempotency-Key` | `leadgen_id` |
| `X-LeadHook-Timestamp` | Unix timestamp |
| `X-LeadHook-Signature` | `sha256=HMAC_SHA256(secret, timestamp + "." + body)`, only with `auth_type=hmac` |
| `Authorization: Bearer <secret>` | with `auth_type=bearer` |
| `<auth_header_name>: <secret>` | with `auth_type=header` |
| `extra_headers` | added as configured |

### Outcome and retries

- **Success:** 2xx **and**, if set, `success_condition` evaluates true. The condition is a sandboxed Jinja expression evaluated with `response` (parsed JSON body, `None` if not JSON) and `status_code`, e.g. `response.success == true`.
- **Retry:** network error, timeout, 5xx, 408, 429, or 2xx with a false success condition. Backoff: 1 min, 5 min, 30 min, 2 h, 6 h, 24 h (7 attempts total); `Retry-After` honored when present.
- **Immediate failure:** other 4xx, template error, invalid rendered JSON.
- **Manual resend:** admin action on single or bulk deliveries (creates a new attempt with the next `attempt` number).

## 7. Security

- **Meta webhook:** `GET` answers `hub.challenge` only if `hub.verify_token` matches; `POST` verifies the HMAC over the raw body with constant-time comparison; per-installation UUID in the URL.
- **Admin:** Django auth; in production `SECURE_SSL_REDIRECT`, HSTS, `Secure`/`HttpOnly` cookies.
- **Secrets:** Fernet-encrypted; startup fails if `LEADHOOK_ENCRYPTION_KEY` is missing; admin shows only the last 4 characters; never logged.
- **Logs:** no lead personal data, IDs only.
- **SSRF:** DNS resolution and blocking of private, loopback, link-local and cloud metadata addresses, unless `ALLOW_PRIVATE_DESTINATIONS=true`; redirects are not followed.

## 8. Error handling

| Problem | Behavior |
|---|---|
| Lead fetch failed (network, 5xx, Meta rate limit) | Celery retry with backoff; reconciliation is the safety net |
| Invalid token (Meta error 190) | Stop retries for that page, set `Page.error`, alert; leads stay `received` and resume after the token is updated ("Reprocess" action) |
| Page not subscribed | `check_pages` resubscribes; alert if that fails |
| Template error / invalid JSON | `Delivery` `failed` without retry, alert |
| Destination unreachable | Retry up to 24 h, then `failed` + alert |
| Unrouted lead | Status `unrouted`, alert |

## 9. Monitoring and alerts

- **Dashboard** (admin home): leads today / last 7 days per form, failed deliveries, unrouted leads, per-page health (token, subscription, last lead).
- **Email alerts** (Django SMTP, recipients from `ALERT_EMAILS`): permanently failed delivery, token/subscription error, unrouted lead, unusual silence. Throttled: at most one email per type every 15 minutes.
- **`GET /health/`**: checks database, Redis and beat heartbeat (< 5 minutes old); returns 200 or 503.

## 10. Onboarding

Admin wizard, shown until setup is complete:

1. Create the admin user (`createsuperuser` or wizard).
2. Enter `app_id`, `app_secret` and token (System User recommended), with a link to the guide.
3. Show the callback URL and verify token to paste into the Meta app; a "Verify" button queries `/{app_id}/subscriptions`.
4. List pages reachable with the token; selecting them subscribes via `subscribed_apps` (`leadgen`) and syncs forms.
5. Create the first destination and set it as a page default.
6. "Send test lead": creates a test lead on a chosen form (`POST /{form_id}/test_leads`) and shows it moving through the pipeline.

The README includes the full Meta app setup guide: Business Verification, permissions (`leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`), System User, Leads Access in Business Manager, Live mode.

## 11. Configuration (environment variables)

`DJANGO_SECRET_KEY`, `LEADHOOK_ENCRYPTION_KEY`, `DATABASE_URL`, `REDIS_URL`, `ALLOWED_HOSTS`, `PUBLIC_BASE_URL`, `EMAIL_URL`, `ALERT_EMAILS`, `LEAD_RETENTION_DAYS` (90), `SILENCE_ALERT_HOURS` (24), `ALLOW_PRIVATE_DESTINATIONS` (false), `META_GRAPH_VERSION` (default for new apps).

## 12. Testing

- `pytest` + `pytest-django`; Celery `task_always_eager` in tests.
- Graph API mocked with `responses` and anonymized JSON fixtures (lead, form, pages, error 190, rate limit).
- Required cases: webhook signature (valid, invalid, tampered body) and challenge; webhook + reconciliation deduplication; routing (form → default → unrouted, inactive destinations); aggregate status; backoff and retry/no-retry classification; `Retry-After`; templates, filters and preview; success condition; SSRF blocking; secret encryption; retention.
- End-to-end test: Meta event → mocked fetch → delivery to a local test HTTP server.
- GitHub Actions CI: `ruff`, tests, Docker image build.

## 13. Repository

- License **AGPL-3.0**.
- Everything in English: code, identifiers, comments, admin UI, docs, README, issue/PR templates, commit messages.
- Layout: `leadhook/` (Django project), apps in `leadhook/<app>/`, `tests/`, `docker/`, `docs/`.
- Files: `README.md`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `docker-compose.yml`, `.env.example`.
