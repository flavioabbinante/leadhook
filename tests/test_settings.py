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
