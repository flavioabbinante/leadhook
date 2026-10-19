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
