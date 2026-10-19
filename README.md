# LeadHook

Self-hosted router for **Facebook & Instagram Lead Ads**. LeadHook receives leads from all your pages and forms through a single Meta app and forwards them to any webhook — n8n, Make, Zapier or your own CRM.

Why: n8n's Facebook Lead Ads trigger can only hold one subscription per Meta app, so running several pages or campaigns at once means fighting over the same callback URL. LeadHook owns that callback and routes every lead where it belongs.

## Features

- One Meta app, unlimited pages and forms.
- Route by form, with a default per page; leads with no route are kept as *unrouted*, never lost.
- Standard payload compatible with n8n's Facebook Lead Ads trigger, or your own JSON via sandboxed Jinja2 templates.
- Bearer, custom header or HMAC-signed requests; optional success condition on the response body.
- Automatic retries with backoff for about a day and a half (7 attempts); one-click resend from the admin.
- Duplicate-proof: each lead is delivered once per destination, with an `Idempotency-Key` header.
- Secrets encrypted at rest.
- No lost leads: reconciliation every 15 minutes recovers leads whose webhook never arrived, and a sweeper re-dispatches work lost by a restart.
- Email alerts, an admin dashboard and a `/health/` endpoint for uptime monitors.
- Guided setup in the admin, including a "Send test lead" button.
- Personal data erased after `LEAD_RETENTION_DAYS` (90 by default).

## Quick start

Requirements: Docker with Compose, a public HTTPS URL served by a reverse proxy (Caddy, Traefik, nginx…) that forwards to port 8000. Compose publishes the web container on `127.0.0.1:8000` only, so it is reachable from the host (and the proxy) but not directly from the internet; keep it behind the proxy.

```bash
git clone https://github.com/<you>/leadhook.git && cd leadhook
cp .env.example .env
# Fill in DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY, PUBLIC_BASE_URL and ALLOWED_HOSTS
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Open `https://<your-domain>/admin/`, then **Set up LeadHook** (`/admin/setup/`, also linked from the dashboard) and follow the steps.

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

## Payload

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

## Monitoring

- **Dashboard** (admin home): leads today and per form, failed deliveries, unrouted leads, page health, background job status.
- **Email alerts** to `ALERT_EMAILS`: permanently failed delivery, unrouted lead, page token or subscription problem, lead that could not be fetched, unusual silence. At most one email per kind every 15 minutes.
- **`GET /health/`** returns 200 with `{"database": "ok", "redis": "ok", "beat": "ok"}`, or 503 naming what is wrong. `beat` turns `stale` when the background jobs have not run for 5 minutes. The endpoint is exempt from the HTTPS redirect; the `Host` header must still be in `ALLOWED_HOSTS`.
- **Background jobs** (Celery beat): reconciliation (15 min), sweeper (5 min), form sync (1 h), silence check (1 h), page and token check (daily), retention purge (daily).
- **Retention:** after `LEAD_RETENTION_DAYS` the lead answers, webhook payloads and request/response bodies are erased; IDs, ad/campaign names and statuses are kept. Purged leads cannot be resent.

## Configuration

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

## Development

```bash
uv sync
cp .env.example .env   # set LEADHOOK_ENCRYPTION_KEY, DJANGO_DEBUG=true, remove DATABASE_URL,
                       # and set REDIS_URL=redis://localhost:6379/0 (the compose host name "redis"
                       # does not resolve outside Docker)
uv run python manage.py migrate
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run Celery tasks eagerly on in-memory SQLite and need neither Redis nor PostgreSQL. To run them against PostgreSQL, set `LEADHOOK_TEST_DATABASE_URL` (CI does). To run a real worker locally, start Redis (e.g. `docker run -p 6379:6379 redis:7-alpine`) and run `uv run celery -A leadhook worker` with `REDIS_URL=redis://localhost:6379/0`.

## License

[GNU AGPL-3.0](LICENSE). If you run a modified version as a service, you must publish your changes.
