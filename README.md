<div align="center">

# 🪝 LeadHook

**Every Facebook & Instagram lead, delivered to the right webhook — from all your pages, through one Meta app.**

Self-hosted · open source · built for n8n, Make, Zapier and any CRM with an API.

[![CI](https://github.com/flavioabbinante/leadhook/actions/workflows/ci.yml/badge.svg)](https://github.com/flavioabbinante/leadhook/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://github.com/python/cpython)
[![Django 5.2](https://img.shields.io/badge/Django-5.2_LTS-092E20?logo=django&logoColor=white)](https://github.com/django/django)
[![Celery 5](https://img.shields.io/badge/Celery-5-37814A?logo=celery&logoColor=white)](https://github.com/celery/celery)
[![Redis 7](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://github.com/redis/redis)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://github.com/postgres/postgres)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://github.com/docker/compose)

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Works with n8n](https://img.shields.io/badge/works_with-n8n-EA4B71?logo=n8n&logoColor=white)](https://github.com/n8n-io/n8n)

[Quick start](#-quick-start) ·
[How it works](#-how-it-works) ·
[Meta setup](#-meta-app-setup) ·
[Payload](#-payload) ·
[Configuration](#-configuration) ·
[Contributing](CONTRIBUTING.md)

</div>

---

## ✨ Why LeadHook?

n8n's Facebook Lead Ads trigger holds **one subscription per Meta app**. Run several pages or campaigns and they fight over the same callback URL; route by hard-coded form IDs and every new campaign means editing a workflow.

LeadHook owns that callback. It receives leads from **every page and form**, fetches the full answers from the Graph API, and forwards each one to the destination you picked for its form — or the page default — with retries, signatures and an audit trail.

| | |
|---|---|
| 🔀 **One app, unlimited pages** | Route by form, fall back to a default per page. Leads with no route are kept as *unrouted*, never lost. |
| 🧩 **Drop-in for n8n** | The standard payload matches n8n's Facebook Lead Ads trigger. Need something else? Write it with sandboxed Jinja2 templates. |
| 🔁 **Delivered, exactly once per destination** | 7 attempts over ~1.5 days with backoff and `Retry-After`, `Idempotency-Key` on every request, one-click resend. |
| 🛟 **No lost leads** | Every event is stored before Meta gets a 200. Reconciliation every 15 minutes recovers leads whose webhook never arrived. |
| 🔐 **Secure by default** | Signed webhooks, Fernet-encrypted secrets, HMAC/Bearer/header auth, SSRF guard that also blocks DNS rebinding. |
| 📈 **Easy to run** | `docker compose up`, a guided setup with "Send test lead", dashboard, email alerts and `/health/`. |

## 🧭 How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/architecture-dark.png">
  <img alt="LeadHook architecture: Meta Lead Ads sends a signed leadgen webhook to the Django web app, which stores the event in PostgreSQL and enqueues it on Redis. A Celery worker fetches the lead from the Meta Graph API, routes it and POSTs JSON to your webhooks, sending email alerts on failures. Celery beat schedules reconciliation, sweeping, form sync, page checks, silence checks, retention and the heartbeat." src="docs/images/architecture-light.png">
</picture>

1. **Receive** — Meta POSTs a signed `leadgen` event; LeadHook verifies `X-Hub-Signature-256`, stores it and answers 200 right away.
2. **Fetch** — a worker reads the full lead (answers, ad, campaign) from the Graph API with the page token.
3. **Route** — form destinations win; otherwise the page defaults; otherwise the lead waits as *unrouted*.
4. **Deliver** — one delivery per destination, signed and retried; the lead's status follows its deliveries.
5. **Keep watch** — beat runs reconciliation, a sweeper for lost work, form sync, token checks, silence alerts and retention.

<sub>Diagram generated with [Archify](https://github.com/tt-a1i/archify) from `docs/architecture.archify.json`.</sub>

## 🚀 Quick start

Requirements: Docker with Compose, a public HTTPS URL served by a reverse proxy (Caddy, Traefik, nginx…) that forwards to port 8000. Compose publishes the web container on `127.0.0.1:8000` only, so it is reachable from the host (and the proxy) but not directly from the internet; keep it behind the proxy.

```bash
git clone https://github.com/flavioabbinante/leadhook.git && cd leadhook
cp .env.example .env
# Fill in DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY, PUBLIC_BASE_URL and ALLOWED_HOSTS
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Open `https://<your-domain>/admin/`, then **Set up LeadHook** (`/admin/setup/`, also linked from the dashboard) and follow the steps.

## 📘 Meta app setup

LeadHook uses your own Meta app. One-time preparation in Meta:

1. **Business Verification.** In Business Settings → Security Center, verify your business. Without it the app cannot go Live, and apps in development mode only receive test leads.
2. **App.** In [Meta for Developers](https://developers.facebook.com/apps/) create an app of type **Business**, add the **Facebook Login for Business** and **Webhooks** products, and note the App ID and App secret (App settings → Basic).
3. **Login.** In Facebook Login for Business → Settings, add the **Valid OAuth Redirect URI** shown in LeadHook's setup (`PUBLIC_BASE_URL/admin/setup/facebook/callback/`). Optionally create a **configuration** with a *System-user access token* and the permissions `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`; its ID goes in the Meta app's *Login configuration ID*, and the connection then never expires. Without one, LeadHook asks for those permissions and keeps a 60-day token (page tokens derived from it do not expire; the dashboard alerts before it runs out).
4. **Leads Access.** In Business Settings → Integrations → Leads Access, allow the app for each page; otherwise Meta returns leads without answers.
5. **Live mode.** Switch the app to Live.

Then in LeadHook, open **Set up LeadHook** (`/admin/setup/`):

1. Add the Meta app with the App ID and App secret.
2. Press **Connect with Facebook** and allow LeadHook. It stores the token and registers the webhook on the app, unless the app already sends its webhook to another URL: an app has one callback URL, so LeadHook never replaces it (this needs `PUBLIC_BASE_URL` reachable over HTTPS; the step also shows the Callback URL and Verify token to set it up by hand).
3. Select your pages and **Connect**: LeadHook subscribes each page to lead events and imports its forms.
4. Create a destination and connect it in **Routing** to a page (default for all its forms) or a single form.
5. **Send test lead** and watch it arrive under Leads and Deliveries.

A System User token pasted by hand in the Meta app still works instead of the login.

## 📦 Payload

With an empty template, every destination receives:

```json
{
  "id": "1234567890123456",
  "created_time": "2026-10-15T08:30:00+0000",
  "data": { "full_name": "Jane Doe", "phone_number": "+15551234567" },
  "form": { "id": "…", "name": "…", "locale": "en_US", "status": "ACTIVE" },
  "page": { "id": "…", "name": "…" },
  "ad": { "id": "…", "name": "…" },
  "adset": { "id": "…", "name": "…" },
  "campaign": { "id": "…", "name": "…" },
  "is_organic": false,
  "field_data": [ { "name": "full_name", "values": ["Jane Doe"] } ],
  "_leadhook": { "version": 1, "delivery_id": "dlv_1", "attempt": 1, "source": "webhook", "sent_at": "…" }
}
```

Headers: `Idempotency-Key` (the lead ID), `X-LeadHook-Timestamp`, and with HMAC authentication `X-LeadHook-Signature: sha256=HMAC_SHA256(secret, timestamp + "." + body)`.

### Custom templates

Write any JSON with Jinja2 placeholders. All payload fields above are available as variables.

```jinja
{
  "first_name": {{ data.full_name | first_word | tojson }},
  "last_name": {{ data.full_name | rest_words | tojson }},
  "mobile": {{ data.phone_number | phone_strip_prefix("+1") | tojson }},
  "created": {{ created_time | date("America/New_York", "%Y-%m-%d %H:%M") | tojson }},
  "plan": {{ data.plan | map_value({"basic_plan": "Basic"}) | tojson }}
}
```

Always end each value with `| tojson`. The admin shows a live preview of the rendered payload.

**Success condition** (optional): a Jinja2 expression evaluated against the parsed response, e.g. `response.success == true`. When it is false the delivery is retried.

### Retries

Network errors, timeouts, 5xx, 408 and 429 are retried after 1 min, 5 min, 30 min, 2 h, 6 h and 24 h (`Retry-After` is honored) — 7 attempts over about a day and a half. Other 4xx responses fail immediately. Finished deliveries (successful or failed) can be resent from **Deliveries** in the admin, which starts a fresh cycle of 7 attempts; deliveries still pending are skipped.

## 📊 Monitoring

- **Dashboard** (admin home): leads today and per form, failed deliveries, unrouted leads, page health, background job status.
- **Email alerts** to `ALERT_EMAILS`: permanently failed delivery, unrouted lead, page token or subscription problem, lead that could not be fetched, unusual silence. At most one email per kind every 15 minutes.
- **`GET /health/`** returns 200 with `{"database": "ok", "redis": "ok", "beat": "ok"}`, or 503 naming what is wrong. `beat` turns `stale` when the background jobs have not run for 5 minutes. The endpoint is exempt from the HTTPS redirect; the `Host` header must still be in `ALLOWED_HOSTS`.
- **Background jobs** (Celery beat): reconciliation (15 min), sweeper (5 min), form sync (1 h), silence check (1 h), page and token check (daily), retention purge (daily).
- **Retention:** after `LEAD_RETENTION_DAYS` the lead answers, webhook payloads and request/response bodies are erased; IDs, ad/campaign names and statuses are kept. Purged leads cannot be resent.

## 🔧 Configuration

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | Required. |
| `LEADHOOK_ENCRYPTION_KEY` | — | Required. Fernet key for stored secrets. Never change it once set. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Public URL, used to build the webhook callback URL. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names. |
| `DATABASE_URL` | SQLite | PostgreSQL recommended. |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and cache (alert throttling, heartbeat). |
| `META_GRAPH_VERSION` | see `.env.example` | Graph API version for new Meta apps. |
| `ALLOW_PRIVATE_DESTINATIONS` | `false` | Allow destinations on private networks (e.g. n8n in the same Docker network). |
| `CSRF_TRUSTED_ORIGINS` | `PUBLIC_BASE_URL` | Comma-separated origins allowed to submit admin forms, e.g. `https://leads.example.com`. |
| `SECURE_SSL_REDIRECT` | `true` | Set to `false` only for local testing over HTTP. |
| `SECURE_HSTS_SECONDS` | `31536000` | HSTS max-age when `SECURE_SSL_REDIRECT` is on; use a small value while testing. |
| `DJANGO_DEBUG` | `false` | Debug mode. Never enable in production. |
| `LOG_LEVEL` | `INFO` | Log level for the web, worker and beat processes. |
| `WEB_CONCURRENCY` | `3` | Number of gunicorn workers in the web container. |
| `EMAIL_URL` | `consolemail://` | SMTP server for alerts, e.g. `smtp+tls://user:password@smtp.example.com:587`. |
| `DEFAULT_FROM_EMAIL` | `leadhook@localhost` | Sender of alert emails. |
| `ALERT_EMAILS` | — | Comma-separated recipients; no alerts when empty. |
| `LEAD_RETENTION_DAYS` | `90` | Lead answers, webhook payloads and request/response bodies older than this are erased. |
| `SILENCE_ALERT_HOURS` | `24` | Alert when a page that had leads in the last 30 days has none for this long. |
| `REQUESTS_CA_BUNDLE` | — | CA bundle for destinations behind a private certificate authority. |

## 🧪 Development

```bash
uv sync
uv run pytest            # ~300 tests, no Redis or PostgreSQL needed
uv run ruff check . && uv run ruff format --check .
```

Local setup, project layout, testing conventions and the pull request checklist are in **[CONTRIBUTING.md](CONTRIBUTING.md)**. Found a vulnerability? See **[SECURITY.md](SECURITY.md)**.

## 📄 License

[GNU AGPL-3.0](LICENSE). If you run a modified version as a service, you must publish your changes.
