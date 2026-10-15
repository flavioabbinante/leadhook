# LeadHook

Self-hosted router for **Facebook & Instagram Lead Ads**. LeadHook receives leads from all your pages and forms through a single Meta app and forwards them to any webhook — n8n, Make, Zapier or your own CRM.

Why: n8n's Facebook Lead Ads trigger can only hold one subscription per Meta app, so running several pages or campaigns at once means fighting over the same callback URL. LeadHook owns that callback and routes every lead where it belongs.

## Features

- One Meta app, unlimited pages and forms.
- Route by form, with a default per page; leads with no route are kept as *unrouted*, never lost.
- Standard payload compatible with n8n's Facebook Lead Ads trigger, or your own JSON via sandboxed Jinja2 templates.
- Bearer, custom header or HMAC-signed requests; optional success condition on the response body.
- Automatic retries with backoff for up to 24 hours; one-click resend from the admin.
- Duplicate-proof: each lead is delivered once per destination, with an `Idempotency-Key` header.
- Secrets encrypted at rest.

## Quick start

Requirements: Docker with Compose, a public HTTPS URL pointing to port 8000 (reverse proxy such as Caddy, Traefik or nginx).

```bash
git clone https://github.com/<you>/leadhook.git && cd leadhook
cp .env.example .env
# Fill in DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY, PUBLIC_BASE_URL and ALLOWED_HOSTS
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Open `https://<your-domain>/admin/`.

## Meta app setup

1. In [Meta for Developers](https://developers.facebook.com/apps/) create an app of type **Business** and add the **Webhooks** product.
2. In LeadHook, add a **Meta app** with the App ID and App secret. Copy the **Callback URL** and **Verify token** it shows.
3. In Meta, Webhooks → **Page** → subscribe with that callback URL and verify token, then subscribe to the `leadgen` field.
4. In Business Settings create a **System User**, assign it your pages, and generate a token with `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement` and `pages_manage_ads`. Paste it into the Meta app in LeadHook.
5. In Business Settings → Integrations → **Leads Access**, grant access to your app.
6. Complete Business Verification and switch the app to **Live** — apps in development mode only receive test webhooks.
7. For each page, add a **Page** in LeadHook with its page access token, and subscribe the page to your app:
   `POST https://graph.facebook.com/<version>/<page-id>/subscribed_apps?subscribed_fields=leadgen&access_token=<page-token>`
8. Create a **Destination** and set it as the page's default destination (or on specific forms).

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

Network errors, timeouts, 5xx, 408 and 429 are retried after 1 min, 5 min, 30 min, 2 h, 6 h and 24 h (`Retry-After` is honored). Other 4xx responses fail immediately. Failed deliveries can be resent from **Deliveries** in the admin.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | Required. |
| `LEADHOOK_ENCRYPTION_KEY` | — | Required. Fernet key for stored secrets. Never change it once set. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Public URL, used to build the webhook callback URL. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names. |
| `DATABASE_URL` | SQLite | PostgreSQL recommended. |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker. |
| `META_GRAPH_VERSION` | see `.env.example` | Graph API version for new Meta apps. |
| `ALLOW_PRIVATE_DESTINATIONS` | `false` | Allow destinations on private networks (e.g. n8n in the same Docker network). |
| `SECURE_SSL_REDIRECT` | `true` | Set to `false` only for local testing over HTTP. |

## Development

```bash
uv sync
cp .env.example .env   # set LEADHOOK_ENCRYPTION_KEY, DJANGO_DEBUG=true, remove DATABASE_URL
uv run python manage.py migrate
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

[GNU AGPL-3.0](LICENSE). If you run a modified version as a service, you must publish your changes.
