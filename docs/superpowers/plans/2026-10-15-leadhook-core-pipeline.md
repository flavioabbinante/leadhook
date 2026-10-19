# LeadHook Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core of LeadHook: receive Meta `leadgen` webhooks for many pages, fetch each lead from the Graph API, route it by form/page, and deliver it to webhook destinations with retries — configurable through the Django admin and runnable with `docker compose up`.

**Architecture:** One Django project (`leadhook`) with four apps (`core`, `meta`, `leads`, `destinations`) plus a plain `routing` module. The web process stores raw webhook events and enqueues Celery tasks; a Celery worker runs the pipeline `process_event → fetch_lead → route_lead → deliver`. Business logic lives in small service modules; Celery tasks are thin wrappers that map service outcomes to retries.

**Tech Stack:** Python 3.12, Django 5.2 LTS, Celery 5 + Redis, PostgreSQL (SQLite for dev/tests), `requests`, Jinja2 sandbox, `cryptography` (Fernet), `django-environ`, WhiteNoise, gunicorn; tests with `pytest`, `pytest-django`, `responses`; lint with `ruff`; dependency management with `uv`.

**Spec:** `docs/superpowers/specs/2026-10-15-leadhook-design.md`

**Out of this plan (Plan 2 — operations):** `reconcile_leads`, `sync_forms`, `check_pages`, `check_silence`, `purge_old_data`, `heartbeat` + `/health/`, email alerts, dashboard, onboarding wizard (incl. test lead), "route unrouted leads?" prompt, sweeper for stale pending deliveries, lead chooser for payload preview, full README Meta setup guide with screenshots.

## Global Constraints

- English only: code, identifiers, comments, admin UI strings, docs, README, commit messages.
- License AGPL-3.0 (`license = "AGPL-3.0-only"` in `pyproject.toml`, full text in `LICENSE`).
- Python `>=3.12`; Django `>=5.2,<6.0`; Celery `>=5.4,<6.0`.
- Secrets (`app_secret`, `access_token`, `page_token`, destination `secret`) are stored Fernet-encrypted with `LEADHOOK_ENCRYPTION_KEY`; startup fails if the key is missing; admin shows only the last 4 characters.
- Logs never contain lead personal data — IDs only. Error messages never contain access tokens.
- Delivery retry backoff (seconds): `[60, 300, 1800, 7200, 21600, 86400]` → 7 attempts total. Retry on network error, timeout, 5xx, 408, 429, or 2xx with a false success condition; immediate failure on other 4xx/3xx, template error, invalid rendered JSON.
- Standard payload top level matches n8n's Facebook Lead Ads trigger "Simplify" output (`id`, `created_time`, `data`, `form`, `page`, `ad`, `adset`) plus additive `campaign`, `is_organic`, `field_data`, `_leadhook`.
- Outbound requests never follow redirects; non-public IPs are blocked unless `ALLOW_PRIVATE_DESTINATIONS=true`.
- Redis broker `visibility_timeout` must exceed the longest retry delay (24 h) — set to 26 h.
- Commit after every task; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Meta sends the same lead twice** (webhook retry, or two events) → exactly one `Lead` and one delivery per destination. Pinned by `test_duplicate_events_deliver_once` (Task 10) and `test_end_to_end_duplicate_webhook` (Task 13).
2. **Network error while calling the Graph API** → the error stored on the lead must not contain the access token (requests puts the full URL, including `access_token`, in exception messages). Pinned by `test_network_error_message_does_not_leak_token` (Task 6).
3. **Form answers with zero or several values** (empty optional field, multi-select checkbox) → `data` holds `""` and a list respectively, never crashes. Pinned by `test_simplify_field_data_handles_empty_and_multi_values` (Task 4).
4. **Lead for a page that is not connected in LeadHook** → lead is kept with a readable error and status `received`, nothing crashes, it can be reprocessed later. Pinned by `test_fetch_lead_for_unknown_page_keeps_lead` (Task 10).
5. **Editing a Meta app or destination in the admin without retyping secrets** → stored secrets are kept, not wiped. Pinned by `test_saving_meta_app_without_secrets_keeps_them` (Task 12).

---

## File Structure

```
leadhook/                         repo root
├── pyproject.toml, uv.lock, .python-version, .gitignore, .dockerignore, .env.example
├── manage.py
├── Dockerfile, docker-compose.yml, docker/entrypoint.sh
├── README.md, LICENSE, CONTRIBUTING.md, SECURITY.md
├── .github/workflows/ci.yml
├── leadhook/
│   ├── __init__.py               version + Celery app import
│   ├── celery.py                 Celery app
│   ├── settings.py               env-driven settings
│   ├── urls.py, wsgi.py
│   ├── core/                     crypto.py, fields.py, models.py (Workspace), admin_forms.py
│   ├── meta/                     models.py (MetaApp, Page, Form), graph.py, signature.py, views.py, admin.py
│   ├── leads/                    models.py (WebhookEvent, Lead), parsing.py, services.py, tasks.py, admin.py
│   ├── routing/                  __init__.py, resolve.py (no models, not an installed app)
│   └── destinations/             models.py (Destination, Delivery), payload.py, templating.py,
│                                 http.py, services.py, tasks.py, admin.py
└── tests/
    ├── __init__.py, settings.py, conftest.py, factories.py, fixtures.py
    ├── test_settings.py
    ├── core/, meta/, leads/, routing/, destinations/   (one test module per source module)
    └── test_end_to_end.py
```

Every test sub-directory gets an empty `__init__.py`.

---

### Task 1: Project skeleton and settings

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `manage.py`
- Create: `leadhook/__init__.py`, `leadhook/celery.py`, `leadhook/settings.py`, `leadhook/urls.py`, `leadhook/wsgi.py`
- Create: `tests/__init__.py`, `tests/settings.py`, `tests/conftest.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `leadhook.celery_app`; settings `PUBLIC_BASE_URL` (no trailing slash), `LEADHOOK_ENCRYPTION_KEY`, `META_GRAPH_VERSION`, `ALLOW_PRIVATE_DESTINATIONS`; test settings module `tests.settings` (eager Celery, Graph version `v21.0`, `PUBLIC_BASE_URL="https://leadhook.test"`).

- [ ] **Step 1: Create `pyproject.toml`, `.python-version`, `.gitignore`**

`pyproject.toml`:
```toml
[project]
name = "leadhook"
version = "0.1.0"
description = "Self-hosted router from Facebook Lead Ads to webhooks"
readme = "README.md"
requires-python = ">=3.12"
license = "AGPL-3.0-only"
dependencies = [
    "django>=5.2,<6.0",
    "celery[redis]>=5.4,<6.0",
    "django-environ>=0.11",
    "cryptography>=43",
    "requests>=2.32",
    "jinja2>=3.1",
    "psycopg[binary]>=3.2",
    "gunicorn>=23",
    "whitenoise>=6.7",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-django>=4.9",
    "responses>=0.25",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "tests.settings"
pythonpath = ["."]
testpaths = ["tests"]
python_files = ["test_*.py"]

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = ["**/migrations/*.py"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.env
db.sqlite3
staticfiles/
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: creates `.venv` with Python 3.12 and writes `uv.lock`.

- [ ] **Step 3: Create `.env.example`**

```
# --- Required ---
DJANGO_SECRET_KEY=change-me-to-a-long-random-string
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Never change it after secrets are stored: they would become unreadable.
LEADHOOK_ENCRYPTION_KEY=
PUBLIC_BASE_URL=https://leads.example.com
ALLOWED_HOSTS=leads.example.com

# --- Optional ---
DJANGO_DEBUG=false
# Set to false only for local testing over plain HTTP.
SECURE_SSL_REDIRECT=true
DATABASE_URL=postgres://leadhook:leadhook@postgres:5432/leadhook
REDIS_URL=redis://redis:6379/0
# Check https://developers.facebook.com/docs/graph-api/changelog for the latest version.
META_GRAPH_VERSION=v24.0
# Allow destinations on private networks (e.g. n8n in the same Docker network).
ALLOW_PRIVATE_DESTINATIONS=false
LOG_LEVEL=info
```

- [ ] **Step 4: Write the failing tests**

`tests/__init__.py`: empty file.

`tests/settings.py`:
```python
import os

os.environ["DJANGO_SECRET_KEY"] = "test-secret-key"
os.environ["LEADHOOK_ENCRYPTION_KEY"] = "bGVhZGhvb2stdGVzdC1rZXktMDEyMzQ1Njc4OWFiY2Q="
os.environ["LEADHOOK_ENV_FILE"] = os.devnull
os.environ["DATABASE_URL"] = "sqlite://:memory:"

from leadhook.settings import *  # noqa: E402,F403

PUBLIC_BASE_URL = "https://leadhook.test"
ALLOWED_HOSTS = ["testserver", "leadhook.test"]
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
META_GRAPH_VERSION = "v21.0"
ALLOW_PRIVATE_DESTINATIONS = False
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
```

`tests/conftest.py`:
```python
import pytest


@pytest.fixture(autouse=True)
def _enable_db_for_all_tests(db):
    """Every test may touch the database; keeps individual tests free of markers."""
```

`tests/test_settings.py`:
```python
import os
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command

ROOT = Path(__file__).resolve().parent.parent


def test_django_system_checks_pass():
    call_command("check")


def test_settings_require_encryption_key():
    env = {k: v for k, v in os.environ.items() if k != "LEADHOOK_ENCRYPTION_KEY"}
    env.update(
        DJANGO_SETTINGS_MODULE="leadhook.settings",
        DJANGO_SECRET_KEY="x",
        LEADHOOK_ENV_FILE=os.devnull,
        PYTHONPATH=str(ROOT),
    )
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "LEADHOOK_ENCRYPTION_KEY" in result.stderr
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook'`.

- [ ] **Step 6: Write the Django project**

`leadhook/__init__.py`:
```python
from .celery import app as celery_app

__version__ = "0.1.0"
__all__ = ["celery_app", "__version__"]
```

`leadhook/celery.py`:
```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "leadhook.settings")

app = Celery("leadhook")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

`leadhook/settings.py`:
```python
"""Django settings for LeadHook. Every deployment value comes from the environment."""

import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(os.environ.get("LEADHOOK_ENV_FILE", str(BASE_DIR / ".env")))

DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env("DJANGO_SECRET_KEY")

LEADHOOK_ENCRYPTION_KEY = env("LEADHOOK_ENCRYPTION_KEY", default="")
if not LEADHOOK_ENCRYPTION_KEY:
    raise ImproperlyConfigured(
        "LEADHOOK_ENCRYPTION_KEY is not set. Generate one with: python -c "
        '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000").rstrip("/")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[PUBLIC_BASE_URL])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "leadhook.core",
    "leadhook.meta",
    "leadhook.leads",
    "leadhook.destinations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "leadhook.urls"
WSGI_APPLICATION = "leadhook.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = (
        env.int("SECURE_HSTS_SECONDS", default=31536000) if SECURE_SSL_REDIRECT else 0
    )
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT

CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_IGNORE_RESULT = True
# Retries are scheduled up to 24 h ahead; Redis must not redeliver them before that.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 26 * 3600}

META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v24.0")
ALLOW_PRIVATE_DESTINATIONS = env.bool("ALLOW_PRIVATE_DESTINATIONS", default=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO").upper()},
}
```

Before committing, check https://developers.facebook.com/docs/graph-api/changelog and replace `v24.0` (here and in `.env.example`) with the latest available Graph API version.

`leadhook/urls.py`:
```python
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "LeadHook"
admin.site.site_title = "LeadHook"
admin.site.index_title = "Administration"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
]
```

`leadhook/wsgi.py`:
```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "leadhook.settings")
application = get_wsgi_application()
```

`manage.py`:
```python
#!/usr/bin/env python
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "leadhook.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

The four apps listed in `INSTALLED_APPS` must exist for `check` to pass. Create each with an empty `__init__.py`, an empty `migrations/__init__.py` and this `apps.py` (shown for `core`; use `meta`/`MetaConfig`, `leads`/`LeadsConfig`, `destinations`/`DestinationsConfig` and verbose names "Meta", "Leads", "Destinations" for the others):

`leadhook/core/apps.py`:
```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "leadhook.core"
    label = "core"
    verbose_name = "Core"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_settings.py -v`
Expected: 2 passed.

- [ ] **Step 8: Create a local `.env` for development**

Run:
```bash
cp .env.example .env
KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
sed -i.bak "s|^LEADHOOK_ENCRYPTION_KEY=.*|LEADHOOK_ENCRYPTION_KEY=$KEY|; s|^DJANGO_DEBUG=.*|DJANGO_DEBUG=true|; s|^DATABASE_URL=.*|# DATABASE_URL=|; s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=http://localhost:8000|; s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=localhost,127.0.0.1|" .env && rm .env.bak
uv run python manage.py check
```
Expected: `System check identified no issues (0 silenced).` (`.env` is git-ignored.)

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore: project skeleton with env-driven settings and Celery app

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Secret encryption and Workspace

**Files:**
- Create: `leadhook/core/crypto.py`, `leadhook/core/fields.py`, `leadhook/core/models.py`
- Create: `leadhook/core/migrations/0001_initial.py` (generated), `leadhook/core/migrations/0002_default_workspace.py`
- Test: `tests/core/__init__.py`, `tests/core/test_crypto.py`, `tests/core/test_models.py`

**Interfaces:**
- Produces: `encrypt(value: str) -> str`, `decrypt(token: str) -> str`, `mask_secret(value: str | None) -> str` in `leadhook.core.crypto`; `EncryptedTextField` in `leadhook.core.fields`; `Workspace` model and `get_default_workspace() -> Workspace` in `leadhook.core.models`.

- [ ] **Step 1: Write the failing tests**

`tests/core/__init__.py`: empty.

`tests/core/test_crypto.py`:
```python
from leadhook.core.crypto import decrypt, encrypt, mask_secret
from leadhook.core.fields import EncryptedTextField


def test_encrypt_round_trip():
    token = encrypt("app-secret")
    assert token != "app-secret"
    assert decrypt(token) == "app-secret"


def test_encryption_is_not_deterministic():
    assert encrypt("same") != encrypt("same")


def test_mask_secret_shows_last_four_characters_only():
    assert mask_secret("system-user-token") == "••••oken"


def test_mask_secret_hides_short_values_completely():
    assert mask_secret("abc") == "•••"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_encrypted_field_encrypts_on_save_and_decrypts_on_load():
    field = EncryptedTextField()
    stored = field.get_prep_value("page-token")
    assert stored != "page-token"
    assert field.from_db_value(stored, None, None) == "page-token"


def test_encrypted_field_keeps_empty_values_empty():
    field = EncryptedTextField()
    assert field.get_prep_value("") == ""
    assert field.from_db_value("", None, None) == ""
    assert field.from_db_value(None, None, None) is None
```

`tests/core/test_models.py`:
```python
from leadhook.core.models import Workspace, get_default_workspace


def test_default_workspace_is_created_by_migration():
    assert Workspace.objects.count() == 1
    assert Workspace.objects.get().name == "Default"


def test_get_default_workspace_returns_the_existing_one():
    assert get_default_workspace() == Workspace.objects.get()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.core.crypto'`.

- [ ] **Step 3: Implement encryption, field and model**

`leadhook/core/crypto.py`:
```python
"""Symmetric encryption for secrets stored in the database."""

from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings

MASK_CHAR = "•"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(settings.LEADHOOK_ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def mask_secret(value: str | None) -> str:
    """Return a display-safe version of a secret: only the last 4 characters are visible."""
    if not value:
        return ""
    if len(value) <= 4:
        return MASK_CHAR * len(value)
    return MASK_CHAR * 4 + value[-4:]
```

`leadhook/core/fields.py`:
```python
from django.db import models

from .crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """TextField stored Fernet-encrypted. Not usable in lookups."""

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return encrypt(value)
```

`leadhook/core/models.py`:
```python
from django.db import models


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


def get_default_workspace() -> Workspace:
    workspace = Workspace.objects.order_by("id").first()
    if workspace is None:
        workspace = Workspace.objects.create(name="Default")
    return workspace
```

- [ ] **Step 4: Generate the schema migration and write the data migration**

Run: `uv run python manage.py makemigrations core`
Expected: creates `leadhook/core/migrations/0001_initial.py`.

`leadhook/core/migrations/0002_default_workspace.py`:
```python
from django.db import migrations


def create_default_workspace(apps, schema_editor):
    Workspace = apps.get_model("core", "Workspace")
    if not Workspace.objects.exists():
        Workspace.objects.create(name="Default")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(create_default_workspace, migrations.RunPython.noop)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/core -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(core): encrypted secret field and default workspace

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Meta models (MetaApp, Page, Form)

**Files:**
- Create: `leadhook/meta/models.py`, `leadhook/meta/migrations/0001_initial.py` (generated)
- Create: `tests/factories.py`
- Test: `tests/meta/__init__.py`, `tests/meta/test_models.py`

**Interfaces:**
- Consumes: `EncryptedTextField`, `Workspace`, `get_default_workspace()` (Task 2).
- Produces: `MetaApp` (`workspace`, `name`, `app_id`, `app_secret`, `verify_token`, `webhook_uuid`, `access_token`, `token_type`, `token_expires_at`, `graph_version`), `MetaApp.TokenType`; `Page` (`meta_app`, `page_id`, `name`, `page_token`, `subscribed`, `last_checked_at`, `last_lead_at`, `error`); `Form` (`page`, `form_id`, `name`, `locale`, `status`, `last_synced_at`). Test factories `make_meta_app(**kw)`, `make_page(meta_app=None, **kw)`, `make_form(page=None, **kw)` with defaults `app_secret="app-secret"`, `access_token="system-user-token"`, `page_id="111"`, `page_token="page-token"`, `form_id="222"`. M2M destination fields are added in Task 5.

- [ ] **Step 1: Write the failing tests**

`tests/meta/__init__.py`: empty.

`tests/factories.py`:
```python
"""Small helpers that create valid model instances with overridable defaults."""

from leadhook.core.models import get_default_workspace
from leadhook.meta.models import Form, MetaApp, Page


def make_meta_app(**kwargs) -> MetaApp:
    values = {
        "workspace": get_default_workspace(),
        "name": "Test app",
        "app_id": "1000",
        "app_secret": "app-secret",
        "access_token": "system-user-token",
    }
    values.update(kwargs)
    return MetaApp.objects.create(**values)


def make_page(meta_app: MetaApp | None = None, **kwargs) -> Page:
    values = {
        "meta_app": meta_app or make_meta_app(),
        "page_id": "111",
        "name": "Test page",
        "page_token": "page-token",
        "subscribed": True,
    }
    values.update(kwargs)
    return Page.objects.create(**values)


def make_form(page: Page | None = None, **kwargs) -> Form:
    values = {
        "page": page or make_page(),
        "form_id": "222",
        "name": "Test form",
        "locale": "en_US",
        "status": "ACTIVE",
    }
    values.update(kwargs)
    return Form.objects.create(**values)
```

`tests/meta/test_models.py`:
```python
from django.db import connection

from leadhook.meta.models import MetaApp
from tests.factories import make_form, make_meta_app, make_page


def _raw_column(table: str, column: str, pk: int) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table} WHERE id = %s", [pk])
        return cursor.fetchone()[0]


def test_meta_app_secrets_are_encrypted_at_rest():
    app = make_meta_app()
    assert _raw_column("meta_metaapp", "app_secret", app.pk) != "app-secret"
    assert _raw_column("meta_metaapp", "access_token", app.pk) != "system-user-token"
    reloaded = MetaApp.objects.get(pk=app.pk)
    assert reloaded.app_secret == "app-secret"
    assert reloaded.access_token == "system-user-token"


def test_meta_app_defaults():
    app = make_meta_app()
    assert len(app.verify_token) == 32
    assert app.webhook_uuid is not None
    assert app.graph_version == "v21.0"
    assert app.token_type == MetaApp.TokenType.SYSTEM_USER


def test_each_meta_app_gets_its_own_webhook_uuid_and_verify_token():
    first = make_meta_app()
    second = make_meta_app(app_id="2000")
    assert first.webhook_uuid != second.webhook_uuid
    assert first.verify_token != second.verify_token


def test_page_token_is_encrypted_at_rest():
    page = make_page()
    assert _raw_column("meta_page", "page_token", page.pk) != "page-token"


def test_form_belongs_to_page():
    form = make_form()
    assert form.page.forms.get() == form
    assert str(form) == "Test form (222)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta -v`
Expected: FAIL — `ImportError: cannot import name 'Form' from 'leadhook.meta.models'`.

- [ ] **Step 3: Implement the models**

`leadhook/meta/models.py`:
```python
import secrets
import uuid

from django.conf import settings
from django.db import models

from leadhook.core.fields import EncryptedTextField
from leadhook.core.models import Workspace


def generate_verify_token() -> str:
    return secrets.token_hex(16)


def default_graph_version() -> str:
    return settings.META_GRAPH_VERSION


class MetaApp(models.Model):
    class TokenType(models.TextChoices):
        SYSTEM_USER = "system_user", "System user (never expires)"
        USER = "user", "User"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="meta_apps")
    name = models.CharField(max_length=200)
    app_id = models.CharField("App ID", max_length=64)
    app_secret = EncryptedTextField()
    verify_token = models.CharField(max_length=64, default=generate_verify_token)
    webhook_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    access_token = EncryptedTextField(blank=True)
    token_type = models.CharField(
        max_length=20, choices=TokenType.choices, default=TokenType.SYSTEM_USER
    )
    token_expires_at = models.DateTimeField(null=True, blank=True)
    graph_version = models.CharField(max_length=16, default=default_graph_version)

    class Meta:
        verbose_name = "Meta app"

    def __str__(self) -> str:
        return f"{self.name} ({self.app_id})"


class Page(models.Model):
    meta_app = models.ForeignKey(MetaApp, on_delete=models.CASCADE, related_name="pages")
    page_id = models.CharField("Page ID", max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    page_token = EncryptedTextField(blank=True)
    subscribed = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_lead_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.name or 'Page'} ({self.page_id})"


class Form(models.Model):
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="forms")
    form_id = models.CharField("Form ID", max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    locale = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=32, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name or 'Form'} ({self.form_id})"
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations meta`
Expected: creates `leadhook/meta/migrations/0001_initial.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/meta -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(meta): MetaApp, Page and Form models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Lead models and webhook/lead parsing

**Files:**
- Create: `leadhook/leads/models.py`, `leadhook/leads/parsing.py`, `leadhook/leads/migrations/0001_initial.py` (generated)
- Create: `tests/fixtures.py`
- Modify: `tests/factories.py` (add `make_lead`)
- Test: `tests/leads/__init__.py`, `tests/leads/test_parsing.py`, `tests/leads/test_models.py`

**Interfaces:**
- Consumes: `MetaApp`, `Page`, `Form` (Task 3).
- Produces:
  - `WebhookEvent` (`meta_app`, `received_at`, `payload: dict`, `signature_ok`, `processed_at`, `error`).
  - `Lead` (`leadgen_id` unique, `page`, `form`, `page_external_id`, `form_external_id`, `created_time`, `ad_id`, `ad_name`, `adset_id`, `adset_name`, `campaign_id`, `campaign_name`, `is_organic`, `field_data: list`, `data: dict`, `source`, `status`, `error`, `received_at`, `updated_at`), `Lead.Status` (`RECEIVED`, `FETCHED`, `ROUTED`, `UNROUTED`, `DELIVERED`, `PARTIALLY_FAILED`, `FAILED`), `Lead.Source` (`WEBHOOK`, `RECONCILE`, `MANUAL`).
  - `leadhook.leads.parsing`: `LeadgenChange(leadgen_id: str, page_id: str, form_id: str)` dataclass, `extract_leadgen_changes(payload: dict) -> list[LeadgenChange]`, `simplify_field_data(field_data: list[dict]) -> dict`, `parse_meta_time(value: str | None) -> datetime | None`.
  - `tests/fixtures.py`: `GRAPH_LEAD: dict`, `GRAPH_FORM: dict`, `leadgen_payload(leadgen_id="333", page_id="111", form_id="222") -> dict`, `sign(body: bytes, secret="app-secret") -> str`.
  - `tests/factories.make_lead(form=None, **kw) -> Lead` (defaults: `leadgen_id="333"`, status `FETCHED`, `data={"full_name": "Jane Doe"}`).

- [ ] **Step 1: Write the fixtures and failing tests**

`tests/leads/__init__.py`: empty.

`tests/fixtures.py`:
```python
"""Anonymized Meta payloads shared by tests."""

import hashlib
import hmac

GRAPH_LEAD = {
    "id": "333",
    "created_time": "2026-10-15T08:30:00+0000",
    "field_data": [
        {"name": "full_name", "values": ["Jane Doe"]},
        {"name": "phone_number", "values": ["+15551234567"]},
        {"name": "services", "values": ["electricity", "gas"]},
    ],
    "ad_id": "444",
    "ad_name": "Ad 1",
    "adset_id": "555",
    "adset_name": "Ad set 1",
    "campaign_id": "666",
    "campaign_name": "Campaign 1",
    "form_id": "222",
    "is_organic": False,
}

GRAPH_FORM = {"id": "222", "name": "Energy offer", "locale": "en_US", "status": "ACTIVE"}


def leadgen_payload(leadgen_id: str = "333", page_id: str = "111", form_id: str = "222") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1760517000,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": leadgen_id,
                            "page_id": page_id,
                            "form_id": form_id,
                            "created_time": 1760517000,
                        },
                    }
                ],
            }
        ],
    }


def sign(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
```

Append to `tests/factories.py`:
```python
from datetime import UTC, datetime

from leadhook.leads.models import Lead


def make_lead(form: Form | None = None, **kwargs) -> Lead:
    form = form or make_form()
    values = {
        "leadgen_id": "333",
        "page": form.page,
        "form": form,
        "page_external_id": form.page.page_id,
        "form_external_id": form.form_id,
        "created_time": datetime(2026, 10, 15, 8, 30, tzinfo=UTC),
        "ad_id": "444",
        "ad_name": "Ad 1",
        "adset_id": "555",
        "adset_name": "Ad set 1",
        "campaign_id": "666",
        "campaign_name": "Campaign 1",
        "field_data": [{"name": "full_name", "values": ["Jane Doe"]}],
        "data": {"full_name": "Jane Doe"},
        "status": Lead.Status.FETCHED,
    }
    values.update(kwargs)
    return Lead.objects.create(**values)
```
Move the new imports to the top of the file with the existing ones.

`tests/leads/test_parsing.py`:
```python
from datetime import UTC, datetime

from leadhook.leads.parsing import (
    LeadgenChange,
    extract_leadgen_changes,
    parse_meta_time,
    simplify_field_data,
)
from tests.fixtures import leadgen_payload


def test_extract_leadgen_changes():
    assert extract_leadgen_changes(leadgen_payload()) == [
        LeadgenChange(leadgen_id="333", page_id="111", form_id="222")
    ]


def test_extract_ignores_other_fields_and_coerces_ids_to_strings():
    payload = leadgen_payload()
    payload["entry"][0]["changes"].append({"field": "feed", "value": {"post_id": "1"}})
    payload["entry"][0]["changes"][0]["value"]["leadgen_id"] = 333
    assert extract_leadgen_changes(payload) == [
        LeadgenChange(leadgen_id="333", page_id="111", form_id="222")
    ]


def test_extract_falls_back_to_entry_id_for_page():
    payload = leadgen_payload()
    del payload["entry"][0]["changes"][0]["value"]["page_id"]
    assert extract_leadgen_changes(payload)[0].page_id == "111"


def test_extract_tolerates_malformed_payloads():
    assert extract_leadgen_changes({}) == []
    assert extract_leadgen_changes({"entry": "nope"}) == []
    assert extract_leadgen_changes({"entry": [{"changes": [{"field": "leadgen"}]}]}) == []


def test_simplify_field_data_handles_empty_and_multi_values():
    field_data = [
        {"name": "full_name", "values": ["Jane Doe"]},
        {"name": "services", "values": ["electricity", "gas"]},
        {"name": "notes", "values": []},
        {"name": "city"},
    ]
    assert simplify_field_data(field_data) == {
        "full_name": "Jane Doe",
        "services": ["electricity", "gas"],
        "notes": "",
        "city": "",
    }


def test_simplify_field_data_accepts_none():
    assert simplify_field_data(None) == {}


def test_parse_meta_time():
    assert parse_meta_time("2026-10-15T08:30:00+0000") == datetime(2026, 10, 15, 8, 30, tzinfo=UTC)
    assert parse_meta_time(None) is None
    assert parse_meta_time("") is None
```

`tests/leads/test_models.py`:
```python
import pytest
from django.db import IntegrityError

from leadhook.leads.models import Lead
from tests.factories import make_lead


def test_leadgen_id_is_unique():
    lead = make_lead()
    with pytest.raises(IntegrityError):
        make_lead(form=lead.form)


def test_new_lead_defaults():
    lead = Lead.objects.create(leadgen_id="999", page_external_id="111", form_external_id="222")
    assert lead.status == Lead.Status.RECEIVED
    assert lead.source == Lead.Source.WEBHOOK
    assert lead.data == {}
    assert lead.field_data == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads -v`
Expected: FAIL — `ImportError` for `leadhook.leads.models` / `leadhook.leads.parsing`.

- [ ] **Step 3: Implement models and parsing**

`leadhook/leads/models.py`:
```python
from django.db import models

from leadhook.meta.models import Form, MetaApp, Page


class WebhookEvent(models.Model):
    meta_app = models.ForeignKey(
        MetaApp, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    payload = models.JSONField(default=dict)
    signature_ok = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Event {self.pk} at {self.received_at:%Y-%m-%d %H:%M:%S}"


class Lead(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        FETCHED = "fetched", "Fetched"
        ROUTED = "routed", "Routed"
        UNROUTED = "unrouted", "Unrouted"
        DELIVERED = "delivered", "Delivered"
        PARTIALLY_FAILED = "partially_failed", "Partially failed"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"
        RECONCILE = "reconcile", "Reconciliation"
        MANUAL = "manual", "Manual"

    leadgen_id = models.CharField("Lead ID", max_length=64, unique=True)
    page = models.ForeignKey(
        Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    form = models.ForeignKey(
        Form, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    page_external_id = models.CharField("Meta page ID", max_length=64, blank=True)
    form_external_id = models.CharField("Meta form ID", max_length=64, blank=True)
    created_time = models.DateTimeField(null=True, blank=True)
    ad_id = models.CharField(max_length=64, blank=True)
    ad_name = models.CharField(max_length=255, blank=True)
    adset_id = models.CharField(max_length=64, blank=True)
    adset_name = models.CharField(max_length=255, blank=True)
    campaign_id = models.CharField(max_length=64, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    is_organic = models.BooleanField(default=False)
    field_data = models.JSONField(default=list, blank=True)
    data = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.WEBHOOK)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Lead {self.leadgen_id}"
```

`leadhook/leads/parsing.py`:
```python
"""Pure functions that turn Meta payloads into plain Python values."""

from dataclasses import dataclass
from datetime import datetime

META_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


@dataclass(frozen=True)
class LeadgenChange:
    leadgen_id: str
    page_id: str
    form_id: str


def extract_leadgen_changes(payload: dict) -> list[LeadgenChange]:
    """Return every leadgen change in a Meta webhook payload, skipping anything malformed."""
    changes: list[LeadgenChange] = []
    entries = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return changes
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                continue
            changes.append(
                LeadgenChange(
                    leadgen_id=str(leadgen_id),
                    page_id=str(value.get("page_id") or entry.get("id") or ""),
                    form_id=str(value.get("form_id") or ""),
                )
            )
    return changes


def simplify_field_data(field_data: list[dict] | None) -> dict:
    """Map Meta field_data to {name: value}: one value -> string, several -> list, none -> ""."""
    result: dict = {}
    for field in field_data or []:
        values = field.get("values") or []
        if len(values) == 0:
            result[field["name"]] = ""
        elif len(values) == 1:
            result[field["name"]] = values[0]
        else:
            result[field["name"]] = list(values)
    return result


def parse_meta_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, META_TIME_FORMAT)
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations leads`
Expected: creates `leadhook/leads/migrations/0001_initial.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/leads -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(leads): WebhookEvent and Lead models with Meta payload parsing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Destination and Delivery models

**Files:**
- Create: `leadhook/destinations/models.py`, migrations (generated)
- Modify: `leadhook/meta/models.py` (add M2M fields to `Page` and `Form`), new meta migration (generated)
- Modify: `tests/factories.py` (add `make_destination`)
- Test: `tests/destinations/__init__.py`, `tests/destinations/test_models.py`

**Interfaces:**
- Consumes: `Workspace`, `get_default_workspace` (Task 2); `Lead` (Task 4).
- Produces:
  - `Destination` (`workspace`, `name`, `kind`, `url`, `method`, `auth_type`, `auth_header_name`, `secret`, `extra_headers: dict`, `payload_template`, `success_condition`, `timeout_s`, `active`, `created_at`), `Destination.Kind.WEBHOOK`, `Destination.Method` (`POST`, `PUT`), `Destination.AuthType` (`NONE`, `HEADER`, `BEARER`, `HMAC`), `Destination.clean()` validation.
  - `Delivery` (`lead`, `destination`, `status`, `attempts`, `next_retry_at`, `last_status_code`, `last_error`, `request_body`, `response_body`, `delivered_at`, `created_at`, `updated_at`), `Delivery.Status` (`PENDING`, `SUCCESS`, `FAILED`), `Delivery.public_id -> str` (`"dlv_<pk>"`, or `"dlv_preview"` when unsaved). Unique on (`lead`, `destination`).
  - `Page.default_destinations` (M2M, `related_name="default_for_pages"`), `Form.destinations` (M2M, `related_name="forms"`).
  - `tests/factories.make_destination(**kw) -> Destination` (defaults `name="n8n"`, `url="https://hooks.example.com/lead"`, `auth_type=NONE`).

- [ ] **Step 1: Write the failing tests**

`tests/destinations/__init__.py`: empty.

Append to `tests/factories.py` (imports at top):
```python
from leadhook.destinations.models import Destination


def make_destination(**kwargs) -> Destination:
    values = {
        "workspace": get_default_workspace(),
        "name": "n8n",
        "url": "https://hooks.example.com/lead",
        "auth_type": Destination.AuthType.NONE,
    }
    values.update(kwargs)
    return Destination.objects.create(**values)
```

`tests/destinations/test_models.py`:
```python
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection

from leadhook.destinations.models import Delivery, Destination
from tests.factories import make_destination, make_lead


def test_one_delivery_per_lead_and_destination():
    lead = make_lead()
    destination = make_destination()
    Delivery.objects.create(lead=lead, destination=destination)
    with pytest.raises(IntegrityError):
        Delivery.objects.create(lead=lead, destination=destination)


def test_delivery_public_id():
    delivery = Delivery.objects.create(lead=make_lead(), destination=make_destination())
    assert delivery.public_id == f"dlv_{delivery.pk}"
    assert Delivery().public_id == "dlv_preview"


def test_destination_secret_is_encrypted_at_rest():
    destination = make_destination(auth_type=Destination.AuthType.BEARER, secret="s3cret-value")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret FROM destinations_destination WHERE id = %s", [destination.pk]
        )
        assert cursor.fetchone()[0] != "s3cret-value"


def test_page_and_form_destination_links():
    lead = make_lead()
    destination = make_destination()
    lead.page.default_destinations.add(destination)
    lead.form.destinations.add(destination)
    assert list(destination.default_for_pages.all()) == [lead.page]
    assert list(destination.forms.all()) == [lead.form]


@pytest.mark.parametrize(
    ("auth_type", "header_name", "secret", "error_field"),
    [
        (Destination.AuthType.HEADER, "", "x", "auth_header_name"),
        (Destination.AuthType.HEADER, "X-Api-Key", "", "secret"),
        (Destination.AuthType.BEARER, "", "", "secret"),
        (Destination.AuthType.HMAC, "", "", "secret"),
    ],
)
def test_destination_clean_requires_auth_details(auth_type, header_name, secret, error_field):
    destination = Destination(
        name="x",
        url="https://a.example.com",
        auth_type=auth_type,
        auth_header_name=header_name,
        secret=secret,
    )
    with pytest.raises(ValidationError) as excinfo:
        destination.clean()
    assert error_field in excinfo.value.message_dict


def test_destination_clean_rejects_non_object_extra_headers():
    destination = Destination(name="x", url="https://a.example.com", extra_headers=["nope"])
    with pytest.raises(ValidationError) as excinfo:
        destination.clean()
    assert "extra_headers" in excinfo.value.message_dict
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/destinations -v`
Expected: FAIL — `ImportError: cannot import name 'Destination'`.

- [ ] **Step 3: Implement the models**

`leadhook/destinations/models.py`:
```python
from django.core.exceptions import ValidationError
from django.db import models

from leadhook.core.fields import EncryptedTextField
from leadhook.core.models import Workspace
from leadhook.leads.models import Lead


class Destination(models.Model):
    class Kind(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"

    class Method(models.TextChoices):
        POST = "POST", "POST"
        PUT = "PUT", "PUT"

    class AuthType(models.TextChoices):
        NONE = "none", "None"
        HEADER = "header", "Custom header"
        BEARER = "bearer", "Bearer token"
        HMAC = "hmac", "HMAC signature"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="destinations")
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.WEBHOOK)
    url = models.URLField("URL", max_length=2000)
    method = models.CharField(max_length=8, choices=Method.choices, default=Method.POST)
    auth_type = models.CharField(max_length=16, choices=AuthType.choices, default=AuthType.NONE)
    auth_header_name = models.CharField(
        max_length=100, blank=True, help_text="Header name for 'Custom header' authentication."
    )
    secret = EncryptedTextField(
        blank=True, help_text="Header value, bearer token or HMAC key, depending on authentication."
    )
    extra_headers = models.JSONField(
        default=dict,
        blank=True,
        help_text='Additional headers as a JSON object, e.g. {"X-Source": "leadhook"}.',
    )
    payload_template = models.TextField(
        blank=True,
        help_text="Jinja2 template producing JSON. Leave empty for the standard payload.",
    )
    success_condition = models.TextField(
        blank=True,
        help_text="Optional Jinja2 expression, e.g. response.success == true. Empty means any 2xx.",
    )
    timeout_s = models.PositiveIntegerField("Timeout (seconds)", default=15)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.auth_type == self.AuthType.HEADER and not self.auth_header_name:
            errors["auth_header_name"] = "Required for custom header authentication."
        if self.auth_type != self.AuthType.NONE and not self.secret:
            errors["secret"] = "Required for the selected authentication."
        if not isinstance(self.extra_headers, dict):
            errors["extra_headers"] = "Must be a JSON object."
        if errors:
            raise ValidationError(errors)


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="deliveries")
    destination = models.ForeignKey(
        Destination, on_delete=models.CASCADE, related_name="deliveries"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    request_body = models.TextField(blank=True)
    response_body = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "deliveries"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["lead", "destination"], name="unique_delivery_per_destination"
            )
        ]

    def __str__(self) -> str:
        return f"{self.lead} → {self.destination}"

    @property
    def public_id(self) -> str:
        return f"dlv_{self.pk}" if self.pk else "dlv_preview"
```

Add to `Page` in `leadhook/meta/models.py`:
```python
    default_destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="default_for_pages",
        help_text="Used for forms of this page that have no destinations of their own.",
    )
```

Add to `Form` in `leadhook/meta/models.py`:
```python
    destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="forms",
        help_text="When set, these replace the page's default destinations for this form.",
    )
```

- [ ] **Step 4: Generate migrations**

Run: `uv run python manage.py makemigrations destinations meta`
Expected: creates `leadhook/destinations/migrations/0001_initial.py` and `leadhook/meta/migrations/0002_*.py`.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass (8 new in `tests/destinations/test_models.py`).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(destinations): Destination and Delivery models, page/form destination links

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Graph API client

**Files:**
- Create: `leadhook/meta/graph.py`
- Test: `tests/meta/test_graph.py`

**Interfaces:**
- Consumes: `Page`, `MetaApp` (Task 3); `GRAPH_LEAD`, `GRAPH_FORM` (Task 4 fixtures).
- Produces: `GraphError(message, *, status: int | None, code: int | None, subcode: int | None = None)` with properties `is_token_error: bool`, `is_transient: bool`; `GraphClient(version: str, access_token: str, timeout: float = 15)` with `get(path: str, params: dict | None = None) -> dict`, `get_lead(leadgen_id: str) -> dict`, `get_form(form_id: str) -> dict`; `client_for_page(page: Page) -> GraphClient`; constants `GRAPH_BASE_URL`, `LEAD_FIELDS`, `FORM_FIELDS`.

- [ ] **Step 1: Write the failing tests**

`tests/meta/test_graph.py`:
```python
import pytest
import requests
import responses
from responses import matchers

from leadhook.meta.graph import LEAD_FIELDS, GraphClient, GraphError, client_for_page
from tests.factories import make_page
from tests.fixtures import GRAPH_FORM, GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"


@responses.activate
def test_get_lead_requests_all_fields_with_token():
    responses.get(
        f"{BASE}/333",
        json=GRAPH_LEAD,
        match=[matchers.query_param_matcher({"fields": LEAD_FIELDS, "access_token": "tok"})],
    )
    assert GraphClient("v21.0", "tok").get_lead("333") == GRAPH_LEAD


@responses.activate
def test_get_form():
    responses.get(f"{BASE}/222", json=GRAPH_FORM)
    assert GraphClient("v21.0", "tok").get_form("222")["name"] == "Energy offer"


@responses.activate
def test_token_error_is_detected():
    responses.get(
        f"{BASE}/333",
        status=400,
        json={
            "error": {"message": "Error validating access token", "code": 190, "error_subcode": 463}
        },
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert excinfo.value.is_token_error
    assert not excinfo.value.is_transient
    assert excinfo.value.subcode == 463


@responses.activate
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {"error": {"message": "Unknown", "code": 1}}),
        (400, {"error": {"message": "Rate limited", "code": 4}}),
        (400, {"error": {"message": "Page rate limit", "code": 32}}),
        (503, "Service unavailable"),
    ],
)
def test_transient_errors(status, body):
    if isinstance(body, dict):
        responses.get(f"{BASE}/333", status=status, json=body)
    else:
        responses.get(f"{BASE}/333", status=status, body=body)
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert excinfo.value.is_transient


@responses.activate
def test_permission_error_is_permanent():
    responses.get(
        f"{BASE}/333", status=400, json={"error": {"message": "No permission", "code": 200}}
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert not excinfo.value.is_transient
    assert not excinfo.value.is_token_error
    assert str(excinfo.value) == "No permission"


@responses.activate
def test_network_error_message_does_not_leak_token():
    responses.get(
        f"{BASE}/333",
        body=requests.ConnectionError(f"Failed to reach {BASE}/333?access_token=very-secret-token"),
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "very-secret-token").get_lead("333")
    assert excinfo.value.is_transient
    assert "very-secret-token" not in str(excinfo.value)


def test_client_for_page_uses_page_token_and_app_version():
    page = make_page()
    client = client_for_page(page)
    assert client.access_token == "page-token"
    assert client.version == "v21.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.meta.graph'`.

- [ ] **Step 3: Implement the client**

`leadhook/meta/graph.py`:
```python
"""Minimal Meta Graph API client."""

import requests

from .models import Page

GRAPH_BASE_URL = "https://graph.facebook.com"
LEAD_FIELDS = (
    "id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,"
    "campaign_id,campaign_name,form_id,is_organic"
)
FORM_FIELDS = "id,name,locale,status"

# https://developers.facebook.com/docs/graph-api/guides/error-handling
TOKEN_ERROR_CODES = {190}
TRANSIENT_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613}


class GraphError(Exception):
    def __init__(
        self, message: str, *, status: int | None, code: int | None, subcode: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode

    @property
    def is_token_error(self) -> bool:
        return self.code in TOKEN_ERROR_CODES

    @property
    def is_transient(self) -> bool:
        if self.is_token_error:
            return False
        return self.status is None or self.status >= 500 or self.code in TRANSIENT_ERROR_CODES


class GraphClient:
    def __init__(self, version: str, access_token: str, timeout: float = 15) -> None:
        self.version = version
        self.access_token = access_token
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_BASE_URL}/{self.version}/{path.lstrip('/')}"
        query = {**(params or {}), "access_token": self.access_token}
        try:
            response = requests.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GraphError(
                self._redact(f"{type(exc).__name__}: {exc}"), status=None, code=None
            ) from None
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

    def get_lead(self, leadgen_id: str) -> dict:
        return self.get(leadgen_id, {"fields": LEAD_FIELDS})

    def get_form(self, form_id: str) -> dict:
        return self.get(form_id, {"fields": FORM_FIELDS})

    def _redact(self, text: str) -> str:
        if self.access_token:
            text = text.replace(self.access_token, "***")
        return text


def client_for_page(page: Page) -> GraphClient:
    return GraphClient(page.meta_app.graph_version, page.page_token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/meta/test_graph.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(meta): Graph API client with error classification and token redaction

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Routing rules

**Files:**
- Create: `leadhook/routing/__init__.py` (empty), `leadhook/routing/resolve.py`
- Test: `tests/routing/__init__.py`, `tests/routing/test_resolve.py`

**Interfaces:**
- Consumes: `Lead` (Task 4), `Destination`, `Page.default_destinations`, `Form.destinations` (Task 5).
- Produces: `resolve_destinations(lead: Lead) -> list[Destination]` — form destinations if the form has any configured (active or not), otherwise the page defaults; only active ones returned, ordered by id; `[]` when there is no form and no page.

- [ ] **Step 1: Write the failing tests**

`tests/routing/__init__.py`: empty.

`tests/routing/test_resolve.py`:
```python
from leadhook.leads.models import Lead
from leadhook.routing.resolve import resolve_destinations
from tests.factories import make_destination, make_lead


def test_form_destinations_take_precedence_over_page_defaults():
    lead = make_lead()
    page_default = make_destination(name="default")
    form_specific = make_destination(name="specific")
    lead.page.default_destinations.add(page_default)
    lead.form.destinations.add(form_specific)
    assert resolve_destinations(lead) == [form_specific]


def test_page_defaults_are_used_when_form_has_none():
    lead = make_lead()
    first = make_destination(name="a")
    second = make_destination(name="b")
    lead.page.default_destinations.add(second, first)
    assert resolve_destinations(lead) == [first, second]


def test_no_destinations_means_unrouted():
    assert resolve_destinations(make_lead()) == []


def test_inactive_destinations_are_skipped():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination(active=False))
    assert resolve_destinations(lead) == []


def test_paused_form_destination_does_not_fall_back_to_page_defaults():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination(name="default"))
    lead.form.destinations.add(make_destination(name="paused", active=False))
    assert resolve_destinations(lead) == []


def test_lead_without_form_uses_page_defaults():
    lead = make_lead()
    default = make_destination()
    lead.page.default_destinations.add(default)
    lead.form = None
    assert resolve_destinations(lead) == [default]


def test_lead_without_page_and_form():
    lead = Lead(leadgen_id="1")
    assert resolve_destinations(lead) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/routing -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.routing'`.

- [ ] **Step 3: Implement**

`leadhook/routing/resolve.py`:
```python
from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead


def resolve_destinations(lead: Lead) -> list[Destination]:
    """Form destinations win when configured (even if paused); otherwise page defaults."""
    if lead.form is not None and lead.form.destinations.exists():
        candidates = lead.form.destinations.all()
    elif lead.page is not None:
        candidates = lead.page.default_destinations.all()
    else:
        return []
    return list(candidates.filter(active=True).order_by("id"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/routing -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(routing): resolve destinations from form or page defaults

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Payload context, templates and success condition

**Files:**
- Create: `leadhook/destinations/payload.py`, `leadhook/destinations/templating.py`
- Test: `tests/destinations/test_payload.py`, `tests/destinations/test_templating.py`

**Interfaces:**
- Consumes: `Lead` (Task 4), `Destination`, `Delivery.public_id` (Task 5).
- Produces:
  - `leadhook.destinations.templating`: `TemplateError(Exception)`, `render_payload(template: str, context: dict) -> str` (validated JSON), `evaluate_condition(expression: str, *, response, status_code: int) -> bool`. Filters: `tojson`, `first_word`, `rest_words`, `phone_strip_prefix(prefix)`, `date(tz="UTC", fmt="%Y-%m-%d %H:%M:%S")`, `map_value(mapping)`, plus Jinja's built-in `default`.
  - `leadhook.destinations.payload`: `build_context(lead: Lead, delivery: Delivery, *, attempt: int, sent_at: datetime) -> dict`, `render_body(destination: Destination, context: dict) -> str`.

The spec names the value-mapping filter `map(dict)`; Jinja already has a built-in `map` filter, so this plan names it `map_value` and Step 5 updates the spec.

- [ ] **Step 1: Write the failing tests**

`tests/destinations/test_payload.py`:
```python
import json
from datetime import UTC, datetime

from leadhook.destinations.models import Delivery
from leadhook.destinations.payload import build_context, render_body
from tests.factories import make_destination, make_lead

SENT_AT = datetime(2026, 10, 15, 8, 30, 4, tzinfo=UTC)


def _context(lead=None, attempt=1):
    lead = lead or make_lead()
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    return build_context(lead, delivery, attempt=attempt, sent_at=SENT_AT), delivery


def test_standard_payload_matches_n8n_simplified_shape():
    context, delivery = _context()
    assert context["id"] == "333"
    assert context["created_time"] == "2026-10-15T08:30:00+0000"
    assert context["data"] == {"full_name": "Jane Doe"}
    assert context["form"] == {
        "id": "222",
        "name": "Test form",
        "locale": "en_US",
        "status": "ACTIVE",
    }
    assert context["page"] == {"id": "111", "name": "Test page"}
    assert context["ad"] == {"id": "444", "name": "Ad 1"}
    assert context["adset"] == {"id": "555", "name": "Ad set 1"}
    assert context["campaign"] == {"id": "666", "name": "Campaign 1"}
    assert context["is_organic"] is False
    assert context["field_data"] == [{"name": "full_name", "values": ["Jane Doe"]}]
    assert context["_leadhook"] == {
        "version": 1,
        "delivery_id": delivery.public_id,
        "attempt": 1,
        "source": "webhook",
        "sent_at": "2026-10-15T08:30:04Z",
    }


def test_context_without_form_or_page_uses_external_ids():
    lead = make_lead()
    lead.form = None
    lead.page = None
    context, _ = _context(lead)
    assert context["form"] == {"id": "222", "name": "", "locale": "", "status": ""}
    assert context["page"] == {"id": "111", "name": ""}


def test_render_body_uses_standard_payload_when_template_is_empty():
    context, delivery = _context()
    body = render_body(delivery.destination, context)
    assert json.loads(body) == context


def test_render_body_uses_custom_template():
    context, delivery = _context()
    delivery.destination.payload_template = '{"name": {{ data.full_name | tojson }}}'
    assert json.loads(render_body(delivery.destination, context)) == {"name": "Jane Doe"}
```

`tests/destinations/test_templating.py`:
```python
import json

import pytest

from leadhook.destinations.templating import TemplateError, evaluate_condition, render_payload

CONTEXT = {
    "id": "333",
    "created_time": "2026-10-15T08:30:00+0000",
    "data": {
        "full_name": "Jane  Mary Doe",
        "phone_number": "+39 333 123 4567",
        "services": ["electricity", "gas"],
        "plan": "electricity_and_gas",
    },
    "ad": {"id": "444", "name": "Ad 1"},
    "_leadhook": {"attempt": 2},
}


def render(template: str):
    return json.loads(render_payload(template, CONTEXT))


def test_crm_style_template():
    template = """{
      "campaign_id": 43,
      "fields": {
        "first_name": {{ data.full_name | first_word | tojson }},
        "last_name": {{ data.full_name | rest_words | tojson }},
        "mobile": {{ data.phone_number | phone_strip_prefix("+39") | tojson }}
      },
      "extra": {"utm_content": {{ ad.name | tojson }}, "lead_id": {{ id | tojson }}}
    }"""
    assert render(template) == {
        "campaign_id": 43,
        "fields": {"first_name": "Jane", "last_name": "Mary Doe", "mobile": "3331234567"},
        "extra": {"utm_content": "Ad 1", "lead_id": "333"},
    }


def test_phone_strip_prefix_handles_00_form_and_missing_prefix():
    assert render('{"a": {{ "0039 333 1234567" | phone_strip_prefix("+39") | tojson }}}') == {
        "a": "3331234567"
    }
    assert render('{"a": {{ "333 1234567" | phone_strip_prefix("+39") | tojson }}}') == {
        "a": "3331234567"
    }


def test_date_filter_converts_timezone():
    template = '{"d": {{ created_time | date("Europe/Rome", "%d/%m/%Y %H:%M") | tojson }}}'
    assert render(template) == {"d": "15/10/2026 10:30"}


def test_map_value_filter_maps_scalars_and_lists():
    template = """{
      "plan": {{ data.plan | map_value({"electricity_and_gas": "Electricity and gas"}) | tojson }},
      "services": {{ data.services | map_value({"gas": "Gas"}) | tojson }}
    }"""
    assert render(template) == {
        "plan": "Electricity and gas",
        "services": ["electricity", "Gas"],
    }


def test_missing_values_render_as_empty_strings():
    assert render(
        '{"a": {{ data.missing | tojson }}, "b": {{ data.missing.deeper | tojson }}}'
    ) == {
        "a": "",
        "b": "",
    }


def test_default_filter_works_for_missing_values():
    assert render('{"a": {{ data.missing | default("n/a") | tojson }}}') == {"a": "n/a"}


def test_tojson_keeps_unicode_and_escapes_quotes():
    assert json.loads(render_payload('{"a": {{ name | tojson }}}', {"name": 'Città "X"'})) == {
        "a": 'Città "X"'
    }


def test_invalid_json_output_is_rejected():
    with pytest.raises(TemplateError, match="not valid JSON"):
        render_payload('{"a": {{ id }}', CONTEXT)


def test_template_syntax_error_is_reported():
    with pytest.raises(TemplateError):
        render_payload('{"a": {{ id }', CONTEXT)


def test_sandbox_blocks_python_internals():
    output = render_payload('{"x": {{ "".__class__.__mro__ | tojson }}}', CONTEXT)
    assert json.loads(output) == {"x": ""}


def test_condition_true_and_false():
    assert evaluate_condition(
        "response.success == true", response={"success": True}, status_code=200
    )
    assert not evaluate_condition(
        "response.success == true", response={"success": False}, status_code=200
    )


def test_condition_with_non_json_response_is_false():
    assert not evaluate_condition("response.success == true", response=None, status_code=200)


def test_condition_can_use_status_code():
    assert evaluate_condition("status_code == 201", response=None, status_code=201)


def test_invalid_condition_raises_template_error():
    with pytest.raises(TemplateError):
        evaluate_condition("response.success ==", response={}, status_code=200)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/destinations/test_payload.py tests/destinations/test_templating.py -v`
Expected: FAIL — `ModuleNotFoundError` for `payload` / `templating`.

- [ ] **Step 3: Implement templating**

`leadhook/destinations/templating.py`:
```python
"""Sandboxed Jinja2 rendering for custom payloads and success conditions."""

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from jinja2 import ChainableUndefined, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

META_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class TemplateError(Exception):
    pass


def _plain(value):
    return "" if isinstance(value, Undefined) or value is None else value


def _tojson(value) -> str:
    return json.dumps(_plain(value), ensure_ascii=False)


def _words(value) -> list[str]:
    return str(_plain(value)).split()


def _first_word(value) -> str:
    words = _words(value)
    return words[0] if words else ""


def _rest_words(value) -> str:
    return " ".join(_words(value)[1:])


def _phone_strip_prefix(value, prefix: str) -> str:
    number = re.sub(r"\s+", "", str(_plain(value)))
    prefixes = [prefix]
    if prefix.startswith("+"):
        prefixes.append("00" + prefix[1:])
    for candidate in prefixes:
        if number.startswith(candidate):
            return number[len(candidate) :]
    return number


def _date(value, tz: str = "UTC", fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    value = _plain(value)
    if value == "":
        return ""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value)
        try:
            moment = datetime.strptime(text, META_TIME_FORMAT)
        except ValueError:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz)).strftime(fmt)


def _map_value(value, mapping: dict):
    value = _plain(value)
    if isinstance(value, list):
        return [mapping.get(item, item) for item in value]
    try:
        return mapping.get(value, value)
    except TypeError:
        return value


_env = ImmutableSandboxedEnvironment(autoescape=False, undefined=ChainableUndefined)
_env.filters.update(
    tojson=_tojson,
    first_word=_first_word,
    rest_words=_rest_words,
    phone_strip_prefix=_phone_strip_prefix,
    date=_date,
    map_value=_map_value,
)


def render_payload(template: str, context: dict) -> str:
    """Render a JSON payload template; raise TemplateError if rendering fails or output is not JSON."""
    try:
        rendered = _env.from_string(template).render(**context)
    except Exception as exc:
        raise TemplateError(f"Template error: {exc}") from exc
    try:
        json.loads(rendered)
    except ValueError as exc:
        raise TemplateError(f"Rendered payload is not valid JSON: {exc}") from exc
    return rendered


def evaluate_condition(expression: str, *, response, status_code: int) -> bool:
    try:
        compiled = _env.compile_expression(expression, undefined_to_none=True)
        return bool(compiled(response=response, status_code=status_code))
    except Exception as exc:
        raise TemplateError(f"Success condition error: {exc}") from exc
```

- [ ] **Step 4: Implement payload building**

`leadhook/destinations/payload.py`:
```python
import json
from datetime import UTC, datetime

from leadhook.leads.models import Lead

from .models import Delivery, Destination
from .templating import render_payload

PAYLOAD_VERSION = 1


def _meta_time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000") if value else ""


def build_context(lead: Lead, delivery: Delivery, *, attempt: int, sent_at: datetime) -> dict:
    """The standard payload; also the variables available to custom templates."""
    form, page = lead.form, lead.page
    return {
        "id": lead.leadgen_id,
        "created_time": _meta_time(lead.created_time),
        "data": lead.data,
        "form": {
            "id": form.form_id if form else lead.form_external_id,
            "name": form.name if form else "",
            "locale": form.locale if form else "",
            "status": form.status if form else "",
        },
        "page": {
            "id": page.page_id if page else lead.page_external_id,
            "name": page.name if page else "",
        },
        "ad": {"id": lead.ad_id, "name": lead.ad_name},
        "adset": {"id": lead.adset_id, "name": lead.adset_name},
        "campaign": {"id": lead.campaign_id, "name": lead.campaign_name},
        "is_organic": lead.is_organic,
        "field_data": lead.field_data,
        "_leadhook": {
            "version": PAYLOAD_VERSION,
            "delivery_id": delivery.public_id,
            "attempt": attempt,
            "source": lead.source,
            "sent_at": sent_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def render_body(destination: Destination, context: dict) -> str:
    if destination.payload_template.strip():
        return render_payload(destination.payload_template, context)
    return json.dumps(context, ensure_ascii=False)
```

- [ ] **Step 5: Run tests, then align the spec**

Run: `uv run pytest tests/destinations -v`
Expected: all pass.

In `docs/superpowers/specs/2026-10-15-leadhook-design.md`, section 6 "Custom template", replace `` `map(dict)` (unmapped value → unchanged) `` with `` `map_value(dict)` (unmapped value → unchanged; lists are mapped item by item — named `map_value` because Jinja already has a built-in `map`) ``.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(destinations): standard payload, sandboxed templates and success conditions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Outbound HTTP: auth headers, SSRF guard, outcome classification

**Files:**
- Create: `leadhook/destinations/http.py`
- Modify: `tests/conftest.py` (add `fake_dns` fixture)
- Test: `tests/destinations/test_http.py`

**Interfaces:**
- Consumes: `Destination` (Task 5), `evaluate_condition`, `TemplateError` (Task 8).
- Produces: `SendResult(outcome: str, status_code: int | None = None, error: str = "", response_body: str = "", retry_after: int | None = None)` with `outcome` in `{"success", "retry", "fail"}`; `BlockedDestination(Exception)`; `resolve_host(host: str, port: int) -> list[str]`; `check_destination_url(url: str) -> None`; `build_headers(destination, *, body: str, timestamp: int, idempotency_key: str) -> dict[str, str]`; `send_request(destination, body: str, headers: dict) -> SendResult`; `parse_retry_after(value: str | None) -> int | None`; constant `RESPONSE_BODY_LIMIT = 4096`.
- Test fixture `fake_dns` (autouse): returns a dict `host -> ip`; unknown hosts resolve to `93.184.216.34`, IP literals resolve to themselves.

- [ ] **Step 1: Add the DNS fixture and write the failing tests**

Append to `tests/conftest.py`:
```python
import ipaddress


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """Deterministic DNS for the SSRF guard: unknown hosts are public, IP literals resolve to themselves."""
    table: dict[str, str] = {}

    def resolve(host: str, port: int) -> list[str]:
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return [table.get(host, "93.184.216.34")]

    monkeypatch.setattr("leadhook.destinations.http.resolve_host", resolve)
    return table
```
(Move `import ipaddress` to the top of the file.)

`tests/destinations/test_http.py`:
```python
import hashlib
import hmac
from email.utils import format_datetime
from datetime import UTC, datetime, timedelta

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
    assert (
        build_headers(destination, body="{}", timestamp=1, idempotency_key="1")["X-N8N-Key"] == "k"
    )


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
    for url in [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "http://192.168.1.10/",
    ]:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/destinations/test_http.py -v`
Expected: FAIL — errors because `leadhook.destinations.http` does not exist (the autouse fixture cannot patch it).

- [ ] **Step 3: Implement**

`leadhook/destinations/http.py`:
```python
"""Outbound webhook requests: headers, SSRF protection and outcome classification."""

import hashlib
import hmac
import ipaddress
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
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedDestination(f"Unsupported destination URL: {url}")
    if settings.ALLOW_PRIVATE_DESTINATIONS:
        return
    port = parts.port or (443 if parts.scheme == "https" else 80)
    for address in resolve_host(parts.hostname, port):
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise BlockedDestination(
                f"Destination resolves to a non-public address ({ip}). "
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
    if value.strip().isdigit():
        return int(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0, int((moment - datetime.now(UTC)).total_seconds()))


def send_request(destination: Destination, body: str, headers: dict) -> SendResult:
    try:
        check_destination_url(destination.url)
    except BlockedDestination as exc:
        return SendResult("fail", error=str(exc))
    except OSError as exc:
        return SendResult("retry", error=f"DNS resolution failed: {exc}")

    try:
        response = requests.request(
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/destinations/test_http.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite (the new autouse fixture applies everywhere)**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(destinations): outbound requests with auth, HMAC signing and SSRF guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Pipeline services and Celery tasks

**Files:**
- Create: `leadhook/leads/services.py`, `leadhook/leads/tasks.py`, `leadhook/destinations/services.py`, `leadhook/destinations/tasks.py`
- Test: `tests/leads/test_services.py`, `tests/leads/test_tasks.py`, `tests/destinations/test_services.py`, `tests/destinations/test_tasks.py`

**Interfaces:**
- Consumes: everything from Tasks 3–9.
- Produces:
  - `leadhook.leads.services`: `FETCH_BACKOFF = [30, 120, 600, 1800, 3600, 3600]`, `FetchOutcome` (`DONE`, `RETRY`, `GIVE_UP`), `ingest_lead(*, leadgen_id: str, page_external_id: str, form_external_id: str, source: str) -> Lead | None` (returns the lead only when newly created), `fetch_lead_details(lead: Lead) -> FetchOutcome`.
  - `leadhook.leads.tasks`: `process_event(event_id: int)`, `fetch_lead(lead_id: int)` (bound, `max_retries=len(FETCH_BACKOFF)`).
  - `leadhook.destinations.services`: `DELIVERY_BACKOFF = [60, 300, 1800, 7200, 21600, 86400]`, `create_deliveries(lead) -> list[Delivery]` (pending ones), `attempt_delivery(delivery, *, now=None) -> SendResult`, `next_retry_delay(retries_done: int, retry_after: int | None) -> int`, `schedule_retry(delivery, delay: int, *, now=None)`, `mark_delivery_failed(delivery, error: str)`, `refresh_lead_status(lead)`, `reset_for_resend(delivery)`.
  - `leadhook.destinations.tasks`: `route_lead(lead_id: int)`, `deliver(delivery_id: int)` (bound, `max_retries=len(DELIVERY_BACKOFF)`).

- [ ] **Step 1: Write the failing tests**

`tests/leads/test_services.py`:
```python
import responses

from leadhook.leads.models import Lead
from leadhook.leads.services import FetchOutcome, fetch_lead_details, ingest_lead
from leadhook.meta.models import Form
from tests.factories import make_form, make_page
from tests.fixtures import GRAPH_FORM, GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"


def _received_lead(**kwargs) -> Lead:
    values = {"leadgen_id": "333", "page_external_id": "111", "form_external_id": "222"}
    values.update(kwargs)
    return Lead.objects.create(**values)


def test_ingest_lead_is_idempotent():
    first = ingest_lead(
        leadgen_id="333", page_external_id="111", form_external_id="222", source="webhook"
    )
    second = ingest_lead(
        leadgen_id="333", page_external_id="111", form_external_id="222", source="reconcile"
    )
    assert first is not None
    assert second is None
    assert Lead.objects.get().source == Lead.Source.WEBHOOK


@responses.activate
def test_fetch_lead_details_fills_the_lead():
    form = make_form()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.DONE
    lead.refresh_from_db()
    assert lead.status == Lead.Status.FETCHED
    assert lead.page == form.page
    assert lead.form == form
    assert lead.data == {
        "full_name": "Jane Doe",
        "phone_number": "+15551234567",
        "services": ["electricity", "gas"],
    }
    assert (lead.ad_name, lead.adset_name, lead.campaign_name) == ("Ad 1", "Ad set 1", "Campaign 1")
    assert lead.created_time.isoformat() == "2026-10-15T08:30:00+00:00"
    form.page.refresh_from_db()
    assert form.page.last_lead_at is not None


@responses.activate
def test_unknown_form_is_created_from_graph():
    page = make_page()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.get(f"{BASE}/222", json=GRAPH_FORM)
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.DONE
    form = Form.objects.get(form_id="222")
    assert (form.page, form.name, form.status) == (page, "Energy offer", "ACTIVE")


def test_fetch_lead_for_unknown_page_keeps_lead():
    lead = _received_lead(page_external_id="999")
    assert fetch_lead_details(lead) == FetchOutcome.GIVE_UP
    lead.refresh_from_db()
    assert lead.status == Lead.Status.RECEIVED
    assert "Unknown page 999" in lead.error


@responses.activate
def test_token_error_flags_page_and_gives_up():
    page = make_page()
    responses.get(
        f"{BASE}/333", status=400, json={"error": {"message": "Session expired", "code": 190}}
    )
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.GIVE_UP
    page.refresh_from_db()
    lead.refresh_from_db()
    assert "Session expired" in page.error
    assert lead.status == Lead.Status.RECEIVED


@responses.activate
def test_successful_fetch_clears_page_error():
    form = make_form()
    form.page.error = "Meta rejected the page token"
    form.page.save()
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    fetch_lead_details(_received_lead())
    form.page.refresh_from_db()
    assert form.page.error == ""


@responses.activate
def test_transient_error_asks_for_retry():
    make_page()
    responses.get(f"{BASE}/333", status=500, json={"error": {"message": "Oops", "code": 2}})
    lead = _received_lead()
    assert fetch_lead_details(lead) == FetchOutcome.RETRY
    lead.refresh_from_db()
    assert lead.error == "Oops"
```

`tests/destinations/test_services.py`:
```python
from datetime import UTC, datetime, timedelta

import responses

from leadhook.destinations.models import Delivery, Destination
from leadhook.destinations.services import (
    attempt_delivery,
    create_deliveries,
    mark_delivery_failed,
    next_retry_delay,
    refresh_lead_status,
    reset_for_resend,
    schedule_retry,
)
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_lead

URL = "https://hooks.example.com/lead"
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _delivery(**destination_kwargs) -> Delivery:
    lead = make_lead()
    destination = make_destination(**destination_kwargs)
    return Delivery.objects.create(lead=lead, destination=destination)


def test_create_deliveries_marks_unrouted_when_no_destination():
    lead = make_lead()
    assert create_deliveries(lead) == []
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


def test_create_deliveries_is_idempotent():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination())
    assert len(create_deliveries(lead)) == 1
    assert len(create_deliveries(lead)) == 1
    assert Delivery.objects.count() == 1
    lead.refresh_from_db()
    assert lead.status == Lead.Status.ROUTED


@responses.activate
def test_successful_attempt_records_everything():
    responses.post(URL, json={"ok": True})
    delivery = _delivery()
    result = attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert result.outcome == "success"
    assert delivery.status == Delivery.Status.SUCCESS
    assert delivery.attempts == 1
    assert delivery.delivered_at == NOW
    assert delivery.last_status_code == 200
    assert '"id": "333"' in delivery.request_body
    sent = responses.calls[0].request
    assert sent.headers["Idempotency-Key"] == "333"
    assert sent.headers["X-LeadHook-Timestamp"] == str(int(NOW.timestamp()))


@responses.activate
def test_client_error_marks_delivery_failed():
    responses.post(URL, status=404)
    delivery = _delivery()
    attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.last_error == "HTTP 404"


@responses.activate
def test_retryable_error_keeps_delivery_pending():
    responses.post(URL, status=503)
    delivery = _delivery()
    assert attempt_delivery(delivery, now=NOW).outcome == "retry"
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING


def test_template_error_fails_without_sending():
    delivery = _delivery(payload_template='{"a": {{ id }')
    result = attempt_delivery(delivery, now=NOW)
    delivery.refresh_from_db()
    assert result.outcome == "fail"
    assert delivery.status == Delivery.Status.FAILED
    assert "Template error" in delivery.last_error


def test_next_retry_delay():
    assert next_retry_delay(0, None) == 60
    assert next_retry_delay(5, None) == 86400
    assert next_retry_delay(0, 600) == 600
    assert next_retry_delay(3, 10) == 7200
    assert next_retry_delay(0, 10**9) == 86400


def test_schedule_retry_sets_next_retry_at():
    delivery = _delivery()
    schedule_retry(delivery, 300, now=NOW)
    delivery.refresh_from_db()
    assert delivery.next_retry_at == NOW + timedelta(seconds=300)


def test_refresh_lead_status():
    lead = make_lead(status=Lead.Status.ROUTED)
    ok = Delivery.objects.create(
        lead=lead, destination=make_destination(), status=Delivery.Status.SUCCESS
    )
    other = Delivery.objects.create(lead=lead, destination=make_destination(name="b"))
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.ROUTED  # one still pending
    mark_delivery_failed(other, "gave up")
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.PARTIALLY_FAILED
    ok.status = Delivery.Status.FAILED
    ok.save()
    refresh_lead_status(lead)
    assert lead.status == Lead.Status.FAILED


def test_reset_for_resend():
    delivery = _delivery()
    mark_delivery_failed(delivery, "HTTP 404")
    delivery.lead.status = Lead.Status.FAILED
    delivery.lead.save()
    reset_for_resend(delivery)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.next_retry_at is None
    assert delivery.lead.status == Lead.Status.ROUTED


def test_destination_kind_is_webhook_by_default():
    assert make_destination().kind == Destination.Kind.WEBHOOK
```

`tests/leads/test_tasks.py`:
```python
from unittest import mock

import pytest
import responses
from celery.exceptions import Retry

from leadhook.destinations.models import Delivery
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.leads.tasks import fetch_lead, process_event
from tests.factories import make_destination, make_form, make_page
from tests.fixtures import GRAPH_LEAD, leadgen_payload

BASE = "https://graph.facebook.com/v21.0"
HOOK = "https://hooks.example.com/lead"


def _event(payload=None, signature_ok=True) -> WebhookEvent:
    return WebhookEvent.objects.create(
        payload=payload or leadgen_payload(), signature_ok=signature_ok
    )


@responses.activate
def test_full_pipeline_from_event_to_delivery():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={"ok": True})

    event = _event()
    process_event(event.pk)

    lead = Lead.objects.get()
    assert lead.status == Lead.Status.DELIVERED
    assert Delivery.objects.get().status == Delivery.Status.SUCCESS
    event.refresh_from_db()
    assert event.processed_at is not None


@responses.activate
def test_duplicate_events_deliver_once():
    form = make_form()
    form.page.default_destinations.add(make_destination())
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post(HOOK, json={"ok": True})

    process_event(_event().pk)
    process_event(_event().pk)

    assert Lead.objects.count() == 1
    assert len([c for c in responses.calls if c.request.url == HOOK]) == 1


def test_events_with_bad_signature_are_ignored():
    process_event(_event(signature_ok=False).pk)
    assert Lead.objects.count() == 0


@responses.activate
def test_partial_failure_across_destinations():
    form = make_form()
    form.page.default_destinations.add(
        make_destination(name="ok", url="https://ok.example.com/"),
        make_destination(name="bad", url="https://bad.example.com/"),
    )
    responses.get(f"{BASE}/333", json=GRAPH_LEAD)
    responses.post("https://ok.example.com/", json={})
    responses.post("https://bad.example.com/", status=400)

    process_event(_event().pk)

    assert Lead.objects.get().status == Lead.Status.PARTIALLY_FAILED


@responses.activate
def test_fetch_lead_retries_on_transient_graph_error():
    make_page()
    responses.get(f"{BASE}/333", status=500, json={"error": {"message": "Oops", "code": 2}})
    lead = Lead.objects.create(leadgen_id="333", page_external_id="111", form_external_id="222")
    with pytest.raises(Retry):
        fetch_lead(lead.pk)


def test_fetch_lead_skips_leads_already_fetched():
    lead = Lead.objects.create(leadgen_id="333", status=Lead.Status.FETCHED)
    with mock.patch("leadhook.leads.tasks.fetch_lead_details") as fetch:
        fetch_lead(lead.pk)
    fetch.assert_not_called()
```

`tests/destinations/test_tasks.py`:
```python
import pytest
import responses
from celery.exceptions import Retry

from leadhook.destinations.models import Delivery
from leadhook.destinations.tasks import deliver, route_lead
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_lead

HOOK = "https://hooks.example.com/lead"


def _pending_delivery() -> Delivery:
    lead = make_lead(status=Lead.Status.ROUTED)
    return Delivery.objects.create(lead=lead, destination=make_destination())


def test_route_lead_without_destinations_marks_unrouted():
    lead = make_lead()
    route_lead(lead.pk)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.UNROUTED


@responses.activate
def test_route_lead_for_unrouted_lead_after_destination_added():
    responses.post(HOOK, json={})
    lead = make_lead(status=Lead.Status.UNROUTED)
    lead.page.default_destinations.add(make_destination())
    route_lead(lead.pk)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.DELIVERED


@responses.activate
def test_deliver_schedules_retry_on_server_error():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    with pytest.raises(Retry):
        deliver(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.next_retry_at is not None
    assert delivery.attempts == 1


@responses.activate
def test_deliver_gives_up_after_last_retry():
    responses.post(HOOK, status=503)
    delivery = _pending_delivery()
    deliver.push_request(retries=deliver.max_retries)
    try:
        deliver.run(delivery.pk)
    finally:
        deliver.pop_request()
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert "Gave up after 1 attempts" in delivery.last_error
    assert delivery.lead.status == Lead.Status.FAILED


def test_deliver_ignores_non_pending_deliveries():
    delivery = _pending_delivery()
    delivery.status = Delivery.Status.SUCCESS
    delivery.save()
    deliver(delivery.pk)  # no HTTP mock registered: would raise if it tried to send
    delivery.refresh_from_db()
    assert delivery.attempts == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/leads tests/destinations -v`
Expected: FAIL — `ModuleNotFoundError` for the new service/task modules.

- [ ] **Step 3: Implement lead services and tasks**

`leadhook/leads/services.py`:
```python
"""Lead ingestion and fetching. Celery-free so it can be tested and reused directly."""

from enum import StrEnum

from django.utils import timezone

from leadhook.meta.graph import GraphClient, GraphError, client_for_page
from leadhook.meta.models import Form, Page

from .models import Lead
from .parsing import parse_meta_time, simplify_field_data

# Seconds to wait before each retry of a transient Graph API failure.
FETCH_BACKOFF = [30, 120, 600, 1800, 3600, 3600]


class FetchOutcome(StrEnum):
    DONE = "done"
    RETRY = "retry"
    GIVE_UP = "give_up"


def ingest_lead(
    *, leadgen_id: str, page_external_id: str, form_external_id: str, source: str
) -> Lead | None:
    """Create the lead if it is new. Returns None for leads already known (deduplication)."""
    lead, created = Lead.objects.get_or_create(
        leadgen_id=leadgen_id,
        defaults={
            "page_external_id": page_external_id,
            "form_external_id": form_external_id,
            "source": source,
        },
    )
    return lead if created else None


def _resolve_page(lead: Lead) -> Page | None:
    if lead.page is not None:
        return lead.page
    return Page.objects.select_related("meta_app").filter(page_id=lead.page_external_id).first()


def _get_or_create_form(page: Page, form_external_id: str, client: GraphClient) -> Form | None:
    if not form_external_id:
        return None
    form = Form.objects.filter(form_id=form_external_id).first()
    if form is not None:
        return form
    try:
        raw = client.get_form(form_external_id)
    except GraphError:
        raw = {}
    form, _ = Form.objects.get_or_create(
        form_id=form_external_id,
        defaults={
            "page": page,
            "name": raw.get("name", ""),
            "locale": raw.get("locale", ""),
            "status": raw.get("status", ""),
            "last_synced_at": timezone.now() if raw else None,
        },
    )
    return form


def fetch_lead_details(lead: Lead) -> FetchOutcome:
    """Read the lead from the Graph API and store its details."""
    page = _resolve_page(lead)
    if page is None:
        lead.error = f"Unknown page {lead.page_external_id}: connect it in the admin, then reprocess the lead."
        lead.save(update_fields=["error", "updated_at"])
        return FetchOutcome.GIVE_UP

    lead.page = page
    client = client_for_page(page)
    try:
        raw = client.get_lead(lead.leadgen_id)
    except GraphError as exc:
        lead.error = str(exc)
        lead.save(update_fields=["page", "error", "updated_at"])
        if exc.is_token_error:
            page.error = f"Meta rejected the page token: {exc}"
            page.save(update_fields=["error"])
            return FetchOutcome.GIVE_UP
        return FetchOutcome.RETRY if exc.is_transient else FetchOutcome.GIVE_UP

    lead.created_time = parse_meta_time(raw.get("created_time"))
    for name in ("ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name"):
        setattr(lead, name, raw.get(name) or "")
    lead.is_organic = bool(raw.get("is_organic", False))
    lead.field_data = raw.get("field_data") or []
    lead.data = simplify_field_data(lead.field_data)
    lead.form_external_id = raw.get("form_id") or lead.form_external_id
    lead.form = _get_or_create_form(page, lead.form_external_id, client)
    lead.status = Lead.Status.FETCHED
    lead.error = ""
    lead.save()
    Page.objects.filter(pk=page.pk).update(last_lead_at=timezone.now(), error="")
    return FetchOutcome.DONE
```

`leadhook/leads/tasks.py`:
```python
from celery import shared_task
from django.utils import timezone

from .models import Lead, WebhookEvent
from .parsing import extract_leadgen_changes
from .services import FETCH_BACKOFF, FetchOutcome, fetch_lead_details, ingest_lead


@shared_task
def process_event(event_id: int) -> None:
    event = WebhookEvent.objects.get(pk=event_id)
    if event.processed_at is not None or not event.signature_ok:
        return
    for change in extract_leadgen_changes(event.payload):
        lead = ingest_lead(
            leadgen_id=change.leadgen_id,
            page_external_id=change.page_id,
            form_external_id=change.form_id,
            source=Lead.Source.WEBHOOK,
        )
        if lead is not None:
            fetch_lead.delay(lead.pk)
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at"])


@shared_task(bind=True, max_retries=len(FETCH_BACKOFF))
def fetch_lead(self, lead_id: int) -> None:
    lead = Lead.objects.select_related("page__meta_app", "form").get(pk=lead_id)
    if lead.status != Lead.Status.RECEIVED:
        return
    outcome = fetch_lead_details(lead)
    if outcome == FetchOutcome.RETRY:
        if self.request.retries >= self.max_retries:
            return  # stays "received" with its error; reprocess or reconciliation picks it up
        raise self.retry(countdown=FETCH_BACKOFF[self.request.retries])
    if outcome == FetchOutcome.DONE:
        from leadhook.destinations.tasks import route_lead

        route_lead.delay(lead.pk)
```

- [ ] **Step 4: Implement delivery services and tasks**

`leadhook/destinations/services.py`:
```python
"""Routing a lead to deliveries and performing delivery attempts."""

from datetime import datetime, timedelta

from django.utils import timezone

from leadhook.leads.models import Lead
from leadhook.routing.resolve import resolve_destinations

from .http import SendResult, build_headers, send_request
from .models import Delivery
from .payload import build_context, render_body
from .templating import TemplateError

# Seconds before each retry; 6 retries = 7 attempts over ~1.5 days.
DELIVERY_BACKOFF = [60, 300, 1800, 7200, 21600, 86400]
MAX_RETRY_DELAY = 86400


def create_deliveries(lead: Lead) -> list[Delivery]:
    """Create one Delivery per resolved destination; return those still pending."""
    destinations = resolve_destinations(lead)
    if not destinations:
        lead.status = Lead.Status.UNROUTED
        lead.save(update_fields=["status", "updated_at"])
        return []
    deliveries = [
        Delivery.objects.get_or_create(lead=lead, destination=destination)[0]
        for destination in destinations
    ]
    lead.status = Lead.Status.ROUTED
    lead.save(update_fields=["status", "updated_at"])
    return [d for d in deliveries if d.status == Delivery.Status.PENDING]


def attempt_delivery(delivery: Delivery, *, now: datetime | None = None) -> SendResult:
    now = now or timezone.now()
    delivery.attempts += 1
    destination = delivery.destination
    context = build_context(delivery.lead, delivery, attempt=delivery.attempts, sent_at=now)
    try:
        body = render_body(destination, context)
    except TemplateError as exc:
        body = ""
        result = SendResult("fail", error=str(exc))
    else:
        headers = build_headers(
            destination,
            body=body,
            timestamp=int(now.timestamp()),
            idempotency_key=delivery.lead.leadgen_id,
        )
        result = send_request(destination, body, headers)

    delivery.request_body = body
    delivery.response_body = result.response_body
    delivery.last_status_code = result.status_code
    delivery.last_error = result.error
    delivery.next_retry_at = None
    if result.outcome == "success":
        delivery.status = Delivery.Status.SUCCESS
        delivery.delivered_at = now
    elif result.outcome == "fail":
        delivery.status = Delivery.Status.FAILED
    delivery.save()
    return result


def next_retry_delay(retries_done: int, retry_after: int | None) -> int:
    base = DELIVERY_BACKOFF[min(retries_done, len(DELIVERY_BACKOFF) - 1)]
    if retry_after is None:
        return base
    return min(max(base, retry_after), MAX_RETRY_DELAY)


def schedule_retry(delivery: Delivery, delay: int, *, now: datetime | None = None) -> None:
    delivery.next_retry_at = (now or timezone.now()) + timedelta(seconds=delay)
    delivery.save(update_fields=["next_retry_at", "updated_at"])


def mark_delivery_failed(delivery: Delivery, error: str) -> None:
    delivery.status = Delivery.Status.FAILED
    delivery.last_error = error
    delivery.next_retry_at = None
    delivery.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])


def refresh_lead_status(lead: Lead) -> None:
    statuses = set(lead.deliveries.values_list("status", flat=True))
    if not statuses or Delivery.Status.PENDING in statuses:
        return
    if statuses == {Delivery.Status.SUCCESS}:
        new_status = Lead.Status.DELIVERED
    elif statuses == {Delivery.Status.FAILED}:
        new_status = Lead.Status.FAILED
    else:
        new_status = Lead.Status.PARTIALLY_FAILED
    if lead.status != new_status:
        lead.status = new_status
        lead.save(update_fields=["status", "updated_at"])


def reset_for_resend(delivery: Delivery) -> None:
    delivery.status = Delivery.Status.PENDING
    delivery.next_retry_at = None
    delivery.save(update_fields=["status", "next_retry_at", "updated_at"])
    lead = delivery.lead
    if lead.status != Lead.Status.ROUTED:
        lead.status = Lead.Status.ROUTED
        lead.save(update_fields=["status", "updated_at"])
```

`leadhook/destinations/tasks.py`:
```python
from celery import shared_task

from leadhook.leads.models import Lead

from .models import Delivery
from .services import (
    DELIVERY_BACKOFF,
    attempt_delivery,
    create_deliveries,
    mark_delivery_failed,
    next_retry_delay,
    refresh_lead_status,
    schedule_retry,
)


@shared_task
def route_lead(lead_id: int) -> None:
    lead = Lead.objects.select_related("page", "form").get(pk=lead_id)
    if lead.status not in (Lead.Status.FETCHED, Lead.Status.UNROUTED):
        return
    for delivery in create_deliveries(lead):
        deliver.delay(delivery.pk)


@shared_task(bind=True, max_retries=len(DELIVERY_BACKOFF))
def deliver(self, delivery_id: int) -> None:
    delivery = Delivery.objects.select_related("lead__page", "lead__form", "destination").get(
        pk=delivery_id
    )
    if delivery.status != Delivery.Status.PENDING:
        return
    result = attempt_delivery(delivery)
    if result.outcome == "retry":
        if self.request.retries >= self.max_retries:
            mark_delivery_failed(
                delivery, f"Gave up after {delivery.attempts} attempts: {result.error}"
            )
        else:
            delay = next_retry_delay(self.request.retries, result.retry_after)
            schedule_retry(delivery, delay)
            raise self.retry(countdown=delay)
    refresh_lead_status(delivery.lead)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: lead pipeline tasks with deduplication, routing and delivery retries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Meta webhook endpoint

**Files:**
- Create: `leadhook/meta/signature.py`, `leadhook/meta/views.py`
- Modify: `leadhook/urls.py` (add route), `leadhook/meta/models.py` (add `MetaApp.webhook_url()`)
- Test: `tests/meta/test_signature.py`, `tests/meta/test_webhook.py`

**Interfaces:**
- Consumes: `MetaApp` (Task 3), `WebhookEvent` (Task 4), `process_event` (Task 10), `sign`, `leadgen_payload` (Task 4 fixtures).
- Produces: URL name `meta-webhook` at `webhooks/meta/<uuid:webhook_uuid>/`; `verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool`; `MetaApp.webhook_url() -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/meta/test_signature.py`:
```python
from leadhook.meta.signature import verify_signature
from tests.fixtures import sign

BODY = b'{"object":"page"}'


def test_valid_signature():
    assert verify_signature("app-secret", BODY, sign(BODY))


def test_wrong_secret():
    assert not verify_signature("other", BODY, sign(BODY))


def test_tampered_body():
    assert not verify_signature("app-secret", BODY + b" ", sign(BODY))


def test_missing_or_malformed_header():
    assert not verify_signature("app-secret", BODY, None)
    assert not verify_signature("app-secret", BODY, "sha1=abc")
    assert not verify_signature("app-secret", BODY, "")
```

`tests/meta/test_webhook.py`:
```python
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
                _url(app), data=body, content_type="text/plain", HTTP_X_HUB_SIGNATURE_256=sign(body)
            )
    assert response.status_code == 200
    assert WebhookEvent.objects.get().payload == {"unparseable_body": "not json"}


def test_other_methods_are_not_allowed(client):
    app = make_meta_app()
    assert client.put(_url(app)).status_code == 405
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/meta/test_signature.py tests/meta/test_webhook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadhook.meta.signature'` / `NoReverseMatch`.

- [ ] **Step 3: Implement**

`leadhook/meta/signature.py`:
```python
import hashlib
import hmac

PREFIX = "sha256="


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header against the raw request body."""
    if not header or not header.startswith(PREFIX):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len(PREFIX) :])
```

`leadhook/meta/views.py`:
```python
import hmac
import json

from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from leadhook.leads.models import WebhookEvent
from leadhook.leads.tasks import process_event

from .models import MetaApp
from .signature import verify_signature

MAX_STORED_UNPARSEABLE_BODY = 10_000


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request: HttpRequest, webhook_uuid) -> HttpResponse:
    meta_app = get_object_or_404(MetaApp, webhook_uuid=webhook_uuid)

    if request.method == "GET":
        token = request.GET.get("hub.verify_token", "")
        if request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(
            token, meta_app.verify_token
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("Invalid verify token")

    raw_body = request.body
    signature_ok = verify_signature(
        meta_app.app_secret, raw_body, request.headers.get("X-Hub-Signature-256")
    )
    try:
        payload = json.loads(raw_body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        payload = {
            "unparseable_body": raw_body.decode("utf-8", "replace")[:MAX_STORED_UNPARSEABLE_BODY]
        }

    event = WebhookEvent.objects.create(
        meta_app=meta_app,
        payload=payload,
        signature_ok=signature_ok,
        error="" if signature_ok else "Invalid signature",
    )
    if signature_ok:
        transaction.on_commit(lambda: process_event.delay(event.pk))
    # Always 200: Meta retries non-2xx responses, and attackers learn nothing.
    return HttpResponse("OK", content_type="text/plain")
```

In `leadhook/urls.py` add the import `from leadhook.meta.views import meta_webhook` and the route:
```python
(path("webhooks/meta/<uuid:webhook_uuid>/", meta_webhook, name="meta-webhook"),)
```

Add to `MetaApp` in `leadhook/meta/models.py` (imports `from django.urls import reverse` at the top):
```python
    def webhook_url(self) -> str:
        return settings.PUBLIC_BASE_URL + reverse("meta-webhook", args=[self.webhook_uuid])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/meta -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(meta): signed webhook endpoint with verification challenge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Admin

**Files:**
- Create: `leadhook/core/admin_forms.py`, `leadhook/meta/admin.py`, `leadhook/leads/admin.py`, `leadhook/destinations/admin.py`
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: all models; `mask_secret` (Task 2); `build_context`, `render_body`, `TemplateError` (Task 8); `fetch_lead` (Task 10); `route_lead`, `deliver`, `reset_for_resend` (Task 10).
- Produces: `SecretAwareModelForm` (subclass with `secret_fields: tuple[str, ...]`); admin actions `LeadAdmin.reprocess`, `DeliveryAdmin.resend`; `DestinationAdmin.payload_preview` (renders against the newest fetched lead, or `?preview_lead=<leadgen_id>`).

- [ ] **Step 1: Write the failing tests**

`tests/test_admin.py`:
```python
from unittest import mock

from django.urls import reverse

from leadhook.destinations.models import Delivery, Destination
from leadhook.leads.models import Lead
from leadhook.meta.models import MetaApp
from tests.factories import make_destination, make_lead, make_meta_app


def test_admin_pages_render(admin_client):
    lead = make_lead()
    destination = make_destination()
    Delivery.objects.create(lead=lead, destination=destination)
    for name in [
        "meta_metaapp",
        "meta_page",
        "meta_form",
        "leads_lead",
        "leads_webhookevent",
        "destinations_destination",
        "destinations_delivery",
    ]:
        assert admin_client.get(reverse(f"admin:{name}_changelist")).status_code == 200
        assert admin_client.get(reverse(f"admin:{name}_add")).status_code in (200, 403)


def test_meta_app_change_page_never_shows_secrets(admin_client):
    app = make_meta_app()
    content = admin_client.get(reverse("admin:meta_metaapp_change", args=[app.pk])).content.decode()
    assert "app-secret" not in content
    assert "system-user-token" not in content
    assert "••••cret" in content
    assert app.webhook_url() in content
    assert app.verify_token in content


def test_saving_meta_app_without_secrets_keeps_them(admin_client):
    app = make_meta_app()
    response = admin_client.post(
        reverse("admin:meta_metaapp_change", args=[app.pk]),
        {
            "name": "Renamed",
            "app_id": "1000",
            "app_secret": "",
            "access_token": "",
            "token_type": MetaApp.TokenType.SYSTEM_USER,
            "token_expires_at_0": "",
            "token_expires_at_1": "",
            "graph_version": "v21.0",
        },
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert app.name == "Renamed"
    assert app.app_secret == "app-secret"
    assert app.access_token == "system-user-token"


def test_new_meta_app_gets_default_workspace(admin_client):
    response = admin_client.post(
        reverse("admin:meta_metaapp_add"),
        {
            "name": "Mine",
            "app_id": "42",
            "app_secret": "s",
            "access_token": "t",
            "token_type": MetaApp.TokenType.SYSTEM_USER,
            "token_expires_at_0": "",
            "token_expires_at_1": "",
            "graph_version": "v21.0",
        },
    )
    assert response.status_code == 302
    assert MetaApp.objects.get(app_id="42").workspace.name == "Default"


def test_destination_payload_preview(admin_client):
    make_lead()
    destination = make_destination(payload_template='{"who": {{ data.full_name | tojson }}}')
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert "Jane Doe" in content


def test_destination_payload_preview_shows_template_errors(admin_client):
    make_lead()
    destination = make_destination(payload_template='{"who": {{ data.full_name }')
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert "Template error" in content


def test_resend_action(admin_client):
    delivery = Delivery.objects.create(
        lead=make_lead(status=Lead.Status.FAILED),
        destination=make_destination(),
        status=Delivery.Status.FAILED,
    )
    with mock.patch("leadhook.destinations.admin.deliver.delay") as delay:
        response = admin_client.post(
            reverse("admin:destinations_delivery_changelist"),
            {"action": "resend", "_selected_action": [str(delivery.pk)]},
        )
    assert response.status_code == 302
    delay.assert_called_once_with(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING


def test_reprocess_action_dispatches_by_status(admin_client):
    received = Lead.objects.create(leadgen_id="1")
    unrouted = make_lead(leadgen_id="2", status=Lead.Status.UNROUTED)
    delivered = make_lead(leadgen_id="3", form=unrouted.form, status=Lead.Status.DELIVERED)
    with (
        mock.patch("leadhook.leads.admin.fetch_lead.delay") as fetch,
        mock.patch("leadhook.leads.admin.route_lead.delay") as route,
    ):
        admin_client.post(
            reverse("admin:leads_lead_changelist"),
            {
                "action": "reprocess",
                "_selected_action": [str(received.pk), str(unrouted.pk), str(delivered.pk)],
            },
        )
    fetch.assert_called_once_with(received.pk)
    route.assert_called_once_with(unrouted.pk)


def test_destination_form_validates_auth(admin_client):
    response = admin_client.post(
        reverse("admin:destinations_destination_add"),
        {
            "name": "x",
            "kind": "webhook",
            "url": "https://a.example.com",
            "method": "POST",
            "auth_type": Destination.AuthType.BEARER,
            "auth_header_name": "",
            "secret": "",
            "extra_headers": "{}",
            "payload_template": "",
            "success_condition": "",
            "timeout_s": "15",
            "active": "on",
        },
    )
    assert response.status_code == 200
    assert "Required for the selected authentication." in response.content.decode()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_admin.py -v`
Expected: FAIL — `NoReverseMatch` (models not registered in admin).

- [ ] **Step 3: Implement the shared secret-aware form**

`leadhook/core/admin_forms.py`:
```python
from django import forms

from .crypto import mask_secret


class SecretAwareModelForm(forms.ModelForm):
    """Renders secret fields as empty password inputs; a blank submission keeps the stored value."""

    secret_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.secret_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            field.widget = forms.PasswordInput(render_value=False)
            stored = getattr(self.instance, name, "") if self.instance.pk else ""
            if stored:
                field.required = False
                field.help_text = f"Stored: {mask_secret(stored)}. Leave blank to keep it."

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            for name in self.secret_fields:
                if name in self.fields and not cleaned.get(name):
                    cleaned[name] = getattr(self.instance, name)
        return cleaned
```

- [ ] **Step 4: Implement the admin modules**

`leadhook/meta/admin.py`:
```python
from django.contrib import admin

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace

from .models import Form, MetaApp, Page


class MetaAppForm(SecretAwareModelForm):
    secret_fields = ("app_secret", "access_token")

    class Meta:
        model = MetaApp
        exclude = ("workspace",)


@admin.register(MetaApp)
class MetaAppAdmin(admin.ModelAdmin):
    form = MetaAppForm
    list_display = ("name", "app_id", "token_type", "graph_version")
    readonly_fields = ("callback_url", "verify_token")
    fieldsets = (
        (None, {"fields": ("name", "app_id", "app_secret", "graph_version")}),
        ("Access token", {"fields": ("access_token", "token_type", "token_expires_at")}),
        (
            "Webhook setup",
            {
                "fields": ("callback_url", "verify_token"),
                "description": "Paste these values in your Meta app: Webhooks → Page → Callback URL.",
            },
        ),
    )

    @admin.display(description="Callback URL")
    def callback_url(self, obj: MetaApp) -> str:
        return obj.webhook_url() if obj.pk else "Available after saving."

    def save_model(self, request, obj, form, change):
        if not change:
            obj.workspace = get_default_workspace()
        super().save_model(request, obj, form, change)


class PageForm(SecretAwareModelForm):
    secret_fields = ("page_token",)

    class Meta:
        model = Page
        fields = "__all__"


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    form = PageForm
    list_display = ("name", "page_id", "subscribed", "last_lead_at", "error")
    list_filter = ("subscribed", "meta_app")
    search_fields = ("name", "page_id")
    filter_horizontal = ("default_destinations",)
    readonly_fields = ("last_checked_at", "last_lead_at", "error")


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ("name", "form_id", "page", "status")
    list_filter = ("page", "status")
    search_fields = ("name", "form_id")
    filter_horizontal = ("destinations",)
    readonly_fields = ("last_synced_at",)
```

`leadhook/destinations/admin.py`:
```python
import json

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace
from leadhook.leads.models import Lead

from .models import Delivery, Destination
from .payload import build_context, render_body
from .services import reset_for_resend
from .tasks import deliver
from .templating import TemplateError


class DestinationForm(SecretAwareModelForm):
    secret_fields = ("secret",)

    class Meta:
        model = Destination
        exclude = ("workspace",)


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    form = DestinationForm
    list_display = ("name", "url", "auth_type", "active")
    list_filter = ("active", "auth_type")
    search_fields = ("name", "url")
    readonly_fields = ("payload_preview",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.workspace = get_default_workspace()
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        self._preview_lead_id = request.GET.get("preview_lead")
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="Payload preview")
    def payload_preview(self, obj: Destination):
        if not obj.pk:
            return "Save the destination to see a preview."
        leads = Lead.objects.exclude(status=Lead.Status.RECEIVED)
        lead_id = getattr(self, "_preview_lead_id", None)
        lead = leads.filter(leadgen_id=lead_id).first() if lead_id else leads.first()
        if lead is None:
            return "No fetched leads yet."
        context = build_context(
            lead, Delivery(lead=lead, destination=obj), attempt=1, sent_at=timezone.now()
        )
        try:
            body = json.dumps(json.loads(render_body(obj, context)), indent=2, ensure_ascii=False)
        except TemplateError as exc:
            return str(exc)
        return format_html(
            "<p>Using lead {} (add <code>?preview_lead=&lt;lead id&gt;</code> to the URL to pick another).</p>"
            "<pre>{}</pre>",
            lead.leadgen_id,
            body,
        )


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "destination",
        "status",
        "attempts",
        "last_status_code",
        "next_retry_at",
        "updated_at",
    )
    list_filter = ("status", "destination")
    search_fields = ("lead__leadgen_id",)
    actions = ["resend"]
    readonly_fields = [f.name for f in Delivery._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Resend selected deliveries")
    def resend(self, request, queryset):
        count = 0
        for delivery in queryset.select_related("lead"):
            reset_for_resend(delivery)
            deliver.delay(delivery.pk)
            count += 1
        self.message_user(request, f"{count} deliveries queued for resending.")


class DeliveryInline(admin.TabularInline):
    model = Delivery
    extra = 0
    can_delete = False
    fields = ("destination", "status", "attempts", "last_status_code", "last_error", "delivered_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False
```

`leadhook/leads/admin.py`:
```python
from django.contrib import admin

from leadhook.destinations.admin import DeliveryInline
from leadhook.destinations.tasks import route_lead

from .models import Lead, WebhookEvent
from .tasks import fetch_lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("leadgen_id", "page", "form", "status", "source", "received_at")
    list_filter = ("status", "source", "page", "form")
    search_fields = ("leadgen_id",)
    inlines = [DeliveryInline]
    actions = ["reprocess"]
    readonly_fields = [f.name for f in Lead._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Reprocess selected leads")
    def reprocess(self, request, queryset):
        count = 0
        for lead in queryset:
            if lead.status == Lead.Status.RECEIVED:
                fetch_lead.delay(lead.pk)
            elif lead.status in (Lead.Status.FETCHED, Lead.Status.UNROUTED):
                route_lead.delay(lead.pk)
            else:
                continue
            count += 1
        self.message_user(
            request,
            f"{count} lead(s) queued. Delivered or failed leads are resent from Deliveries.",
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "meta_app", "received_at", "signature_ok", "processed_at")
    list_filter = ("signature_ok",)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_admin.py -v`
Expected: all pass.

- [ ] **Step 6: Manual smoke check**

Run:
```bash
uv run python manage.py migrate
DJANGO_SUPERUSER_PASSWORD=admin uv run python manage.py createsuperuser --noinput --username admin --email admin@example.com
uv run python manage.py runserver
```
Open http://localhost:8000/admin/, log in as `admin`/`admin`, create a Meta app and check that the callback URL and verify token appear, and that the secret fields stay empty after saving. Stop the server.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: admin for Meta apps, pages, forms, destinations, leads and deliveries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: End-to-end test

**Files:**
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: the whole pipeline (webhook URL from Task 11, eager Celery from Task 1 settings).

- [ ] **Step 1: Write the test**

`tests/test_end_to_end.py`:
```python
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
    expected = hmac.new(
        b"hmac-key", f"{timestamp}.".encode() + request["raw"], hashlib.sha256
    ).hexdigest()
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
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_end_to_end.py -v`
Expected: 2 passed. If it fails, the failure is a real integration bug in Tasks 8–11: fix it in the owning module, not in the test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: end-to-end webhook to destination delivery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Docker, CI, license and docs

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker/entrypoint.sh`, `docker-compose.yml`
- Create: `.github/workflows/ci.yml`
- Create: `LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`

**Interfaces:**
- Consumes: the whole application.
- Produces: image roles `web`, `worker`, `beat` via `docker/entrypoint.sh`.

- [ ] **Step 1: Docker files**

`.dockerignore`:
```
.git
.venv
.env
db.sqlite3
staticfiles
.pytest_cache
.ruff_cache
**/__pycache__
```

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN DJANGO_SECRET_KEY=build LEADHOOK_ENCRYPTION_KEY=build python manage.py collectstatic --noinput \
    && chmod +x docker/entrypoint.sh \
    && useradd --system --uid 1000 leadhook \
    && chown -R leadhook /app

USER leadhook
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
```

`docker/entrypoint.sh`:
```sh
#!/bin/sh
set -e

case "$1" in
  web)
    python manage.py migrate --noinput
    exec gunicorn leadhook.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}"
    ;;
  worker)
    exec celery -A leadhook worker --loglevel "${LOG_LEVEL:-info}"
    ;;
  beat)
    exec celery -A leadhook beat --loglevel "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
```

`docker-compose.yml`:
```yaml
x-app: &app
  build: .
  image: leadhook:local
  env_file: .env
  environment:
    DATABASE_URL: postgres://leadhook:leadhook@postgres:5432/leadhook
    REDIS_URL: redis://redis:6379/0
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_started
  restart: unless-stopped

services:
  web:
    <<: *app
    command: web
    ports:
      - "8000:8000"
  worker:
    <<: *app
    command: worker
  beat:
    <<: *app
    command: beat
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: leadhook
      POSTGRES_PASSWORD: leadhook
      POSTGRES_DB: leadhook
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U leadhook"]
      interval: 5s
      retries: 10
    restart: unless-stopped
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
```

- [ ] **Step 2: Verify the stack starts**

Run:
```bash
cp .env .env.dev.backup
cp .env.example .env
KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
sed -i.bak "s|^LEADHOOK_ENCRYPTION_KEY=.*|LEADHOOK_ENCRYPTION_KEY=$KEY|; s|^SECURE_SSL_REDIRECT=.*|SECURE_SSL_REDIRECT=false|; s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=http://localhost:8000|; s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=localhost,127.0.0.1|" .env && rm .env.bak
docker compose up -d --build
sleep 20
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/admin/login/
docker compose logs worker | tail -5
docker compose down
mv .env.dev.backup .env
```
Expected: `200`, and the worker log shows `celery@... ready.`

- [ ] **Step 3: CI workflow**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest

  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t leadhook:ci .
```

- [ ] **Step 4: License and docs**

Run: `curl -fsSL https://www.gnu.org/licenses/agpl-3.0.txt -o LICENSE && head -3 LICENSE`
Expected: `GNU AFFERO GENERAL PUBLIC LICENSE` / `Version 3, 19 November 2007`.

`README.md`:
````markdown
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
````

`CONTRIBUTING.md`:
```markdown
# Contributing

Thanks for helping improve LeadHook!

- Open an issue before large changes so we can agree on the approach.
- Everything in this repository is written in English: code, comments, UI text, docs and commit messages.
- Write a test for every behavior change; run `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` before opening a pull request.
- Keep pull requests focused on one change.
- Never commit real lead data, tokens or app secrets — test fixtures must be anonymized.
```

`SECURITY.md`:
```markdown
# Security policy

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button (Security → Advisories) rather than opening a public issue. We aim to acknowledge reports within 3 working days.

LeadHook handles personal data from lead forms and Meta access tokens. When reporting, include the affected version, the configuration needed to reproduce, and the potential impact.
```

- [ ] **Step 5: Format, lint and test**

Run:
```bash
uv run ruff format .
uv run ruff check --fix .
uv run ruff check . && uv run ruff format --check . && uv run pytest
```
Expected: no lint errors, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: Docker setup, CI, AGPL-3.0 license and documentation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
