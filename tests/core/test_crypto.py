from django.db import connection

from leadhook.core.crypto import decrypt, encrypt, mask_secret
from leadhook.core.fields import EncryptedTextField
from leadhook.meta.models import MetaApp
from tests.factories import make_meta_app


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
    app = make_meta_app(app_secret="top-secret")
    with connection.cursor() as cursor:
        cursor.execute("SELECT app_secret FROM meta_metaapp WHERE id = %s", [app.pk])
        raw = cursor.fetchone()[0]
    assert "top-secret" not in raw
    assert decrypt(raw) == "top-secret"  # really encrypted with our key, not just altered
    assert MetaApp.objects.get(pk=app.pk).app_secret == "top-secret"


def test_encrypted_field_keeps_empty_values_empty():
    field = EncryptedTextField()
    assert field.get_prep_value("") == ""
    assert field.from_db_value("", None, None) == ""
    assert field.from_db_value(None, None, None) is None
