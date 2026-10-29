"""Small helpers that create valid model instances with overridable defaults."""

from datetime import UTC, datetime
from unittest import mock

import numpy as np

from leadhook.core.models import get_default_workspace
from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead
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


def make_lead(form: Form | None = None, **kwargs) -> Lead:
    form = form or make_form()
    values = {
        "leadgen_id": "333",
        "page": form.page,
        "form": form,
        "page_external_id": form.page.page_id,
        "form_external_id": form.form_id,
        "created_time": datetime(2026, 10, 15, 8, 30, tzinfo=UTC),
        "ad_id": "444",
        "ad_name": "Ad 1",
        "adset_id": "555",
        "adset_name": "Ad set 1",
        "campaign_id": "666",
        "campaign_name": "Campaign 1",
        "field_data": [{"name": "full_name", "values": ["Jane Doe"]}],
        "data": {"full_name": "Jane Doe"},
        "status": Lead.Status.FETCHED,
    }
    values.update(kwargs)
    return Lead.objects.create(**values)


def make_destination(**kwargs) -> Destination:
    values = {
        "workspace": get_default_workspace(),
        "name": "n8n",
        "url": "https://hooks.example.com/lead",
        "auth_type": Destination.AuthType.NONE,
    }
    values.update(kwargs)
    return Destination.objects.create(**values)


def fake_forecaster(median: float, low: float) -> mock.Mock:
    """A Timestral stand-in: every series gets `median` and its 5% quantile `low` spread over
    the horizon, with quantiles symmetric around the median (so the mean is the median)."""
    levels = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)

    def forecast(context, prediction_length):
        per_hour = np.array([median + (q - 0.5) * (median - low) / 0.45 for q in levels])
        shape = (len(context), 1, prediction_length, len(levels))
        return mock.Mock(
            quantiles=np.broadcast_to(per_hour / prediction_length, shape), quantile_levels=levels
        )

    return mock.Mock(forecast=mock.Mock(side_effect=forecast))
