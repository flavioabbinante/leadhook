"""Django settings for LeadHook. Every deployment value comes from the environment."""

import os
from pathlib import Path

import environ
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(os.environ.get("LEADHOOK_ENV_FILE", str(BASE_DIR / ".env")))

DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env("DJANGO_SECRET_KEY")

_FERNET_KEY_HINT = (
    "Generate one with: python -c "
    '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)
LEADHOOK_ENCRYPTION_KEY = env("LEADHOOK_ENCRYPTION_KEY", default="")
if not LEADHOOK_ENCRYPTION_KEY:
    raise ImproperlyConfigured(f"LEADHOOK_ENCRYPTION_KEY is not set. {_FERNET_KEY_HINT}")
try:
    Fernet(LEADHOOK_ENCRYPTION_KEY.encode())
except (ValueError, TypeError):
    raise ImproperlyConfigured(
        "LEADHOOK_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64-encoded bytes). "
        f"{_FERNET_KEY_HINT}"
    ) from None

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
# A retry is scheduled at most 24 h ahead; Redis must not redeliver it before that.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 26 * 3600}

META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v26.0")
ALLOW_PRIVATE_DESTINATIONS = env.bool("ALLOW_PRIVATE_DESTINATIONS", default=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO").upper()},
}
