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
