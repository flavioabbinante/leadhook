import importlib
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management import call_command

ROOT = Path(__file__).resolve().parent.parent


def test_django_system_checks_pass():
    call_command("check")


def _setup_django(**extra_env) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "LEADHOOK_ENCRYPTION_KEY"}
    env.update(
        DJANGO_SETTINGS_MODULE="leadhook.settings",
        DJANGO_SECRET_KEY="x",
        LEADHOOK_ENV_FILE=os.devnull,
        PYTHONPATH=str(ROOT),
        **extra_env,
    )
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_settings_require_encryption_key():
    result = _setup_django()
    assert result.returncode != 0
    assert "LEADHOOK_ENCRYPTION_KEY" in result.stderr


def test_settings_reject_invalid_encryption_key():
    result = _setup_django(LEADHOOK_ENCRYPTION_KEY="build")
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "LEADHOOK_ENCRYPTION_KEY is not a valid Fernet key" in result.stderr


def test_settings_accept_valid_encryption_key():
    result = _setup_django(LEADHOOK_ENCRYPTION_KEY="A" * 43 + "=")
    assert result.returncode == 0, result.stderr


def test_beat_schedule_tasks_exist():
    assert settings.CELERY_BEAT_SCHEDULE
    for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
        module, _, attr = entry["task"].rpartition(".")
        task = getattr(importlib.import_module(module), attr)
        assert callable(getattr(task, "delay", None)), name
