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
