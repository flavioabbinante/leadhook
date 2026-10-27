from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from leadhook.leads.models import Lead
from leadhook.leads.stats import FORECAST_CACHE_KEY, daily_counts, period_leads
from tests.factories import make_lead

URL = reverse("stats")


def _lead(leadgen_id, days_ago, campaign="Autunno", **kwargs):
    return make_lead(
        leadgen_id=leadgen_id,
        created_time=timezone.now() - timedelta(days=days_ago),
        campaign_name=campaign,
        **kwargs,
    )


def test_daily_counts_are_zero_filled_and_scaled_to_the_peak():
    form = _lead("1", 0).form
    _lead("2", 0, form=form)
    _lead("3", 2, form=form)
    _lead("4", 40, form=form)  # outside a 7-day period
    today = timezone.localdate()
    series = daily_counts(period_leads(7, today), 7, today)
    assert [p["count"] for p in series] == [0, 0, 0, 0, 1, 0, 2]
    assert series[-1]["day"] == today
    assert [p["height"] for p in series[-3:]] == ["50.0", "0.0", "100.0"]


def test_stats_page_filters_by_campaign_and_exports_csv(admin_client):
    form = _lead("1", 1).form
    _lead("2", 1, campaign="Gas", form=form, status=Lead.Status.DELIVERED)
    _lead("3", 1, campaign="", form=form)

    content = admin_client.get(URL, {"days": 7}).content.decode()
    assert '<span class="lh-stat-hero">3</span>' in content
    assert "Senza nome (organico o non disponibile)" in content

    content = admin_client.get(URL, {"days": 7, "campaign": "Gas"}).content.decode()
    assert '<span class="lh-stat-hero">1</span>' in content

    content = admin_client.get(URL, {"days": 7, "campaign": ""}).content.decode()
    assert '<span class="lh-stat-hero">1</span>' in content  # leads without a campaign

    csv = admin_client.get(URL, {"days": 7, "format": "csv"}).content.decode()
    assert csv.splitlines()[0] == "giorno,campagna,gruppo_di_inserzioni,inserzione,form,lead"
    assert "Jane" not in csv  # aggregated counts only


def test_stats_needs_lead_permission(client):
    client.force_login(User.objects.create_user("staff", password="pw", is_staff=True))
    assert client.get(URL).status_code == 403


def test_stats_page_draws_the_last_timestral_forecast(admin_client):
    assert "Nessuna previsione" in admin_client.get(URL).content.decode()

    end = timezone.now().replace(minute=0, second=0, microsecond=0)
    counts = [4] * 42 + [5, 1, 0, 0, 1, 0]
    cache.set(
        FORECAST_CACHE_KEY,
        {"end": end, "counts": counts, "median": [6.0] * 6, "low": [2.0] * 6, "high": [10.0] * 6},
    )
    content = admin_client.get(URL).content.decode()
    assert "<strong>7 lead</strong>, previsti 36 (di solito tra 12 e 60)" in content
    assert content.count('class="lh-forecast-band"') == 6
    assert content.count("is-below") == 5 + 1  # five hours under the minimum, plus the legend
    assert "Timestral: 6,0 (2,0–10,0)" in content
