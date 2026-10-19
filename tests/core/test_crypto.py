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
