"""Small helpers that create valid model instances with overridable defaults."""

from leadhook.core.models import get_default_workspace
from leadhook.meta.models import Form, MetaApp, Page


def make_meta_app(**kwargs) -> MetaApp:
    values = {
        "workspace": get_default_workspace(),
        "name": "Test app",
        "app_id": "1000",
        "app_secret": "app-secret",
        "access_token": "system-user-token",
    }
    values.update(kwargs)
    return MetaApp.objects.create(**values)


def make_page(meta_app: MetaApp | None = None, **kwargs) -> Page:
    values = {
        "meta_app": meta_app or make_meta_app(),
        "page_id": "111",
        "name": "Test page",
        "page_token": "page-token",
        "subscribed": True,
    }
    values.update(kwargs)
    return Page.objects.create(**values)


def make_form(page: Page | None = None, **kwargs) -> Form:
    values = {
        "page": page or make_page(),
        "form_id": "222",
        "name": "Test form",
        "locale": "en_US",
        "status": "ACTIVE",
    }
    values.update(kwargs)
    return Form.objects.create(**values)
