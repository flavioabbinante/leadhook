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


def fake_forecaster(median: float, low: float, high: float | None = None) -> mock.Mock:
    """A Timestral stand-in: every series gets `median`/`low`/`high` spread over the horizon."""

    def forecast(context, prediction_length):
        shape = (len(context), 1, prediction_length)

        def spread(total):
            return np.full(shape, total / prediction_length)

        quantiles = {0.05: spread(low), 0.95: spread(median * 2 if high is None else high)}
        return mock.Mock(median=spread(median), quantile=quantiles.__getitem__)

    return mock.Mock(forecast=mock.Mock(side_effect=forecast))
