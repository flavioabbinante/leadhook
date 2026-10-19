from cryptography.fernet import Fernet
from django.db import connection
from django.urls import reverse

from tests.factories import make_meta_app


def test_wrong_encryption_key_shows_readable_error(admin_client):
    app = make_meta_app()
    foreign = Fernet(Fernet.generate_key()).encrypt(b"app-secret").decode()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE meta_metaapp SET app_secret = %s WHERE id = %s", [foreign, app.pk])
    response = admin_client.get(reverse("admin:meta_metaapp_change", args=[app.pk]))
    assert response.status_code == 500
    assert b"LEADHOOK_ENCRYPTION_KEY" in response.content
