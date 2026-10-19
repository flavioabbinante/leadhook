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
