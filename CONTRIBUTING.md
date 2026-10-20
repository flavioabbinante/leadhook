# Contributing to LeadHook

Thanks for helping! LeadHook moves personal data and access tokens between Meta and other systems, so we value **small, well-tested, boring changes** over clever ones. This guide gets you from clone to merged pull request.

- [Ground rules](#ground-rules)
- [Ways to contribute](#ways-to-contribute)
- [Local setup](#local-setup)
- [How the code is organized](#how-the-code-is-organized)
- [Making a change](#making-a-change)
- [Testing](#testing)
- [Conventions](#conventions)
- [Recipes](#recipes)
- [Pull request checklist](#pull-request-checklist)

## Ground rules

- **English only:** code, identifiers, comments, admin UI strings, docs, commit messages, issues and pull requests.
- **No real data, ever.** Never commit or paste real leads, page IDs, tokens or app secrets — not in fixtures, logs, screenshots or issues. Test fixtures are anonymized (see `tests/fixtures.py`).
- **Security issues go private.** Use GitHub's *Report a vulnerability* button, never a public issue. See [SECURITY.md](SECURITY.md).
- **Open an issue first** for anything bigger than a bug fix or a small improvement, so we agree on the approach before you write code.
- By contributing you agree that your work is licensed under [AGPL-3.0](LICENSE).

## Ways to contribute

- 🐛 **Report a bug** — include the LeadHook commit, how you run it (Compose or not), the steps to reproduce, and the relevant log lines *with IDs only*.
- 💡 **Propose a feature** — describe the problem first; the [design spec](docs/superpowers/specs/2026-10-15-leadhook-design.md) lists what is deliberately out of scope for v1.
- 📖 **Improve the docs** — the README's Meta setup guide benefits most from people who just went through it.
- 🧪 **Add tests** for edge cases you met with real Meta payloads (anonymized).

## Local setup

Requirements: Python 3.12, [uv](https://github.com/astral-sh/uv); Docker is optional.

```bash
git clone https://github.com/flavioabbinante/leadhook.git && cd leadhook
uv sync                                   # creates .venv with dev dependencies
uv run pytest                             # Celery runs eagerly on in-memory SQLite: no services needed
uv run ruff check . && uv run ruff format --check .
```

To run the app itself:

```bash
cp .env.example .env
# Set DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY (command in the file), DJANGO_DEBUG=true,
# SECURE_SSL_REDIRECT=false, remove DATABASE_URL (SQLite) and set REDIS_URL=redis://localhost:6379/0
docker run -d -p 6379:6379 redis:7-alpine
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver         # http://localhost:8000/admin/
uv run celery -A leadhook worker -l info  # in a second terminal
uv run celery -A leadhook beat -l info    # in a third, for periodic jobs
```

To develop on PostgreSQL instead of SQLite, start the database and Redis from Compose on
localhost only and point `.env` at them:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis
# .env: DATABASE_URL=postgres://leadhook:leadhook@localhost:5432/leadhook
#       REDIS_URL=redis://localhost:6379/0
```

Or run the whole stack like production: `docker compose up --build`.

To receive real webhooks locally, expose port 8000 with a tunnel (for example `cloudflared tunnel --url http://localhost:8000`), set `PUBLIC_BASE_URL` and `ALLOWED_HOSTS` to the tunnel host, and use a Meta app in development mode with the Lead Ads Testing Tool.

## How the code is organized

![Architecture](docs/images/architecture-light.png)

| Path | What lives there |
|---|---|
| `leadhook/core/` | Encryption (`crypto.py`, `fields.py`), alerts, health/heartbeat, the wrong-key middleware |
| `leadhook/meta/` | Meta app, page and form models; Graph API client (`graph.py`); webhook view; periodic page/form checks; setup wizard (`setup.py`) |
| `leadhook/leads/` | Webhook events and leads; payload parsing; fetch and reconciliation; retention; admin dashboard |
| `leadhook/routing/` | `resolve_destinations()` — form destinations, else page defaults |
| `leadhook/destinations/` | Destinations and deliveries; payload/templating; outbound HTTP with SSRF guard; retries, leases and the sweeper |
| `tests/` | One test module per source module, plus `test_end_to_end.py` |
| `docs/superpowers/` | Design spec and implementation plans — read the spec before changing behavior |

The pipeline is `process_event → fetch_lead → route_lead → deliver`. Business logic lives in `services.py` modules; Celery tasks in `tasks.py` are thin wrappers that turn service outcomes into retries. Keep it that way: services are easy to test without Celery.

## Making a change

1. Branch from `main`: `feat/<topic>`, `fix/<topic>`, `docs/<topic>` or `chore/<topic>`.
2. **Write the failing test first**, then the code that makes it pass.
3. Keep the change focused: one behavior per pull request. Refactors go in their own pull request.
4. Run the full check before pushing:

   ```bash
   uv run ruff format . && uv run ruff check . && uv run pytest
   DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations --check --dry-run
   ```

5. Commit with a [Conventional Commits](https://www.conventionalcommits.org/) style message, scoped by app when it helps:

   ```
   feat(destinations): support PATCH as delivery method
   fix(meta): keep page token when Meta omits it
   docs: clarify Leads Access step
   ```

6. Open the pull request and fill in the template. CI runs ruff, the tests on SQLite **and** PostgreSQL 16, and a Docker build; all three must be green.

## Testing

Tests use `pytest`, `pytest-django` and `responses`. Things that save time:

- **Factories** in `tests/factories.py` (`make_meta_app`, `make_page`, `make_form`, `make_lead`, `make_destination`) create valid objects; override only what your test cares about.
- **Fixtures** in `tests/fixtures.py` hold anonymized Graph API payloads and `sign()` for webhook signatures.
- **No network.** Mock every Graph API and destination call with `responses`. A test that silently reaches the internet is a bug.
- **DNS is faked** by the autouse `fake_dns` fixture: unknown hosts resolve to a public IP; map a host to a private one with `fake_dns["host"] = "10.0.0.1"`.
- **Celery runs eagerly** (`CELERY_TASK_ALWAYS_EAGER`): calling a task runs it inline, and a retry raises `celery.exceptions.Retry`.
- **Emails** land in the `mailoutbox` fixture; the cache (alert throttles, heartbeat) is cleared before each test.
- **PostgreSQL:** CI runs every test on PostgreSQL too. Locally: `docker run -d -p 5432:5432 -e POSTGRES_USER=leadhook -e POSTGRES_PASSWORD=leadhook postgres:16-alpine` and `LEADHOOK_TEST_DATABASE_URL=postgres://leadhook:leadhook@localhost:5432/leadhook uv run pytest`.
- When code guards against something (a race, a lost lease, a leaked secret), make sure the test **fails without the guard** — temporarily remove it and run the test once.

## Conventions

- **Logs, errors and alerts carry IDs only** — never names, emails, phone numbers or answers. Error messages must never contain tokens, app secrets, destination secrets or destination URL paths/queries (`redact_url()` and `GraphClient._redact()` exist for this).
- **Secrets** are `EncryptedTextField`s and admin forms extend `SecretAwareModelForm`, so a blank field keeps the stored value.
- **Retries and leases:** deliveries are claimed with a conditional `UPDATE` and saved only while the lease is held. Read `destinations/services.py` before touching delivery state.
- **Everything periodic is idempotent:** running a beat task twice, or two copies at once, must not create duplicate leads or extra sends.
- **Migrations** get a descriptive name: `makemigrations <app> --name <what_changed>`. Never edit a migration that is already on `main`.
- **Comments explain *why*,** not what. A deliberate shortcut gets a `# shortcut: <limit>, <when to upgrade>` comment.
- **Style** is whatever `ruff format` produces (line length 100). Don't add dependencies for a few lines of code.

## Recipes

**Add a periodic task**

1. Write the logic as a service function and test it directly.
2. Add a thin `@shared_task` in the app's `tasks.py`.
3. Add an entry to `CELERY_BEAT_SCHEDULE` in `leadhook/settings.py`; `tests/test_settings.py::test_beat_schedule_tasks_exist` checks it points to a real task.
4. Mention it in the README's *Monitoring* section.

**Add a setting**

Read it with `env(...)` in `leadhook/settings.py` with a safe default, document it in `.env.example` and in the README's *Configuration* table, and override it in tests with the `settings` fixture.

**Add an alert**

Add a member to `AlertKind` in `leadhook/core/alerts.py` and call `send_alert()` from the service where the problem is detected. Alerts are throttled per kind, so put every affected ID in a single email instead of sending one per object.

**Update the architecture diagram**

The diagram in the README is generated with [Archify](https://github.com/tt-a1i/archify) from `docs/architecture.archify.json`. After changing components or flows, update the JSON (including the `sources` line ranges and `meta.repository.revision`), then:

```bash
node <archify>/bin/archify.mjs finalize architecture docs/architecture.archify.json \
  .archify/leadhook-architecture.html --repo-root . --quality showcase
node <archify>/bin/archify.mjs visual-check .archify/leadhook-architecture.html \
  --summary --require-provenance --out-dir .archify/visual-check
```

Crop the 2048×1320 light and dark captures into `docs/images/architecture-{light,dark}.png`. `.archify/` is git-ignored.

## Pull request checklist

- [ ] Tests cover the change and fail without it
- [ ] `ruff format`, `ruff check`, `pytest` and `makemigrations --check` pass locally
- [ ] No personal data or secrets in code, fixtures, logs or screenshots
- [ ] User-visible changes are documented (README, `.env.example`, admin help text)
- [ ] One focused change, with a Conventional Commits title

Thank you for contributing! 🪝
