from datetime import UTC, date, datetime, timedelta
from unittest import mock

import numpy as np
import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from leadhook.leads.models import Lead
from leadhook.leads.stats import (
    FORECAST_CACHE_KEY,
    daily_counts,
    forecast_cache_key,
    forecast_chart,
    forecast_next,
    forecast_period,
    period_leads,
)
from leadhook.leads.tasks import forecast_leads
from tests.factories import fake_forecaster, make_lead

URL = reverse("stats")
# The tests count days back from "now": pinned so their periods keep (or skip) the 25 October
# switch from summer time whatever day the suite runs.
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)


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


def _received(leadgen_id, at):
    form = Lead.objects.first().form if Lead.objects.exists() else None
    lead = make_lead(form, leadgen_id=leadgen_id, status=Lead.Status.DELIVERED)
    Lead.objects.filter(pk=lead.pk).update(received_at=at)


def test_forecast_period_checks_every_six_hour_block_like_the_hourly_check():
    # 24-25 October 2026 in Italy: summer time ends on the 25th, a 25-hour day.
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    first_day = date(2026, 10, 24)
    start = timezone.make_aware(datetime.combine(first_day, datetime.min.time()))
    for i in range(20):  # the 06-12 block got 20 leads: over 30% of the 60 expected
        _received(f"block-{i}", start + timedelta(hours=7))
    _received("2", start - timedelta(days=7))  # in the history only
    _received("3", start - timedelta(days=25))  # the first lead: over 14 days of history
    with mock.patch("leadhook.leads.stats._forecaster") as forecaster:
        forecaster.return_value = fake_forecaster(median=60, low=30)
        result = forecast_period(first_day, 2, now)
        histories = forecaster.return_value.forecast.call_args.args[0]
    hours = 49
    blocks = 8  # 00, 06, 12, 18 on the local clock: one is 7 hours long on a 25-hour day
    # One batch: a forecast per block, then one per hourly check, 4 weeks of history each.
    assert histories.shape == (blocks + hours, 4 * 7 * 24)
    assert forecaster.return_value.forecast.call_args.kwargs["prediction_length"] == 7
    assert np.nansum(histories[0]) == 2 and np.nansum(histories[2]) == 22
    assert len(result["counts"]) == len(result["median"]) == hours
    assert sum(result["counts"]) == 20
    # The 01:00 check alerts on an empty window; the next ones only after a day of quiet.
    utc_start = start.astimezone(UTC)
    assert result["alerts"] == [utc_start + timedelta(hours=h) for h in (1, 25, 49)]


def test_forecast_period_stops_at_the_current_hour():
    now = timezone.now()
    _received("old", now - timedelta(days=20))
    with mock.patch("leadhook.leads.stats._forecaster", return_value=fake_forecaster(6, 2)):
        result = forecast_period(timezone.localdate(), 1, now)
        assert forecast_period(timezone.localdate() + timedelta(days=1), 1, now) is None
    assert len(result["counts"]) == len(result["median"]) == timezone.localtime(now).hour


def _run_queued(delay, forecaster):
    """Run the forecast the page queued, as the worker would."""
    with mock.patch("leadhook.leads.stats._forecaster", return_value=forecaster):
        forecast_leads(*delay.call_args.args)


@mock.patch("django.utils.timezone.now", return_value=NOW)
def test_stats_page_reviews_a_chosen_period_in_the_background(_now, admin_client):
    _received("old", timezone.now() - timedelta(days=26))
    day = timezone.localdate() - timedelta(days=5)
    next_day = timezone.make_aware(datetime.combine(day + timedelta(days=2), datetime.min.time()))
    first = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    hours = int((next_day.astimezone(UTC) - first.astimezone(UTC)) / timedelta(hours=1))
    params = {"forecast_view": "past", "forecast_day": day.isoformat(), "forecast_days": 2}
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        content = admin_client.get(URL, params).content.decode()
        admin_client.get(URL, params)  # still computing: not queued twice
    key = forecast_cache_key("past", 2, None, day, timezone.now())
    delay.assert_called_once_with(key, "past", 2, None, day.isoformat())
    assert "Timestral sta calcolando 2 giorni" in content and 'data-pending="1"' in content

    _run_queued(delay, fake_forecaster(60, 30))
    assert cache.get(f"{key}:pending") is None
    content = admin_client.get(URL, params).content.decode()
    alerts = len([h for h in (1, 25, 49) if h <= hours])  # at most one a day, as for real
    assert f"l'avviso sarebbe partito {alerts} volte" in content
    assert content.count('class="lh-forecast-band"') == hours

    cache.set(key, {})  # no Timestral
    assert "Timestral non è disponibile" in admin_client.get(URL, params).content.decode()


def test_past_view_defaults_to_the_last_days_up_to_today(admin_client):
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    week_start = (timezone.localdate() - timedelta(days=6)).isoformat()
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        for day in (tomorrow, "not-a-date", ""):
            admin_client.get(URL, {"forecast_view": "past", "forecast_day": day})
            cache.clear()
    assert {call.args[2:] for call in delay.call_args_list} == {(7, None, week_start)}


def test_last_check_view_ignores_the_day(admin_client):
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        content = admin_client.get(URL, {"forecast_day": "2026-01-01"}).content.decode()
    assert "Nessuna previsione nelle ultime 3 ore" in content
    delay.assert_not_called()


def test_forecast_next_continues_the_last_day_of_leads():
    now = timezone.now()
    _received("old", now - timedelta(days=20))
    _received("1", now - timedelta(hours=2))
    with mock.patch("leadhook.leads.stats._forecaster") as forecaster:
        forecaster.return_value = fake_forecaster(median=70, low=35)
        result = forecast_next(7, now, campaign="Campaign 1")
        call = forecaster.return_value.forecast.call_args
    assert call.args[0].shape == (1, 4 * 7 * 24) and call.kwargs["prediction_length"] == 7 * 24
    assert result["counts"][:24].count(1) == 1 and result["counts"][24:] == [None] * 7 * 24
    assert len(result["median"]) == 7 * 24 and "alerts" not in result


def test_stats_page_shows_the_next_days(admin_client):
    _received("old", timezone.now() - timedelta(days=20))
    params = {"forecast_view": "next", "forecast_days": 7}
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        content = admin_client.get(URL, params).content.decode()
    assert "Timestral sta calcolando i prossimi 7 giorni" in content
    _run_queued(delay, fake_forecaster(median=700, low=350))
    content = admin_client.get(URL, params).content.decode()
    assert "<strong>previsti 700 lead</strong>, circa 100 al giorno" in content
    assert content.count("lh-chart-col is-future") == 7 * 24
    assert content.count('class="lh-forecast-band"') == 7 * 24


def test_timestral_card_reviews_one_campaign(admin_client):
    now = timezone.now()
    yesterday = timezone.localdate() - timedelta(days=1)
    _received("autumn", now - timedelta(days=20))
    Lead.objects.update(campaign_name="Autunno")
    _received("spring", now - timedelta(days=20))
    with mock.patch("leadhook.leads.stats._forecaster") as forecaster:
        forecaster.return_value = fake_forecaster(60, 30)
        forecast_period(yesterday, 1, now, campaign="Autunno")
        histories = forecaster.return_value.forecast.call_args.args[0]
    # Only Autunno's lead, in the history of 4 blocks and 24 checks.
    assert np.nansum(histories) == 4 + 24

    # The hourly check covers all campaigns: "last" turns into the campaign's last days.
    params = {"campaign": "Autunno", "forecast_view": "last", "forecast_days": 2}
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        content = admin_client.get(URL, params).content.decode()
    assert delay.call_args.args[1:] == ("past", 2, "Autunno", yesterday.isoformat())
    assert "Timestral sta calcolando la campagna Autunno, 2 giorni" in content
    _run_queued(delay, fake_forecaster(60, 30))
    content = admin_client.get(URL, params).content.decode()
    assert "Campagna: Autunno ·" in content and "cali da avviso" in content


def test_timestral_waits_for_two_weeks_of_history(admin_client):
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    _received("1", now - timedelta(days=3))
    with mock.patch("leadhook.leads.stats._forecaster") as forecaster:
        assert forecast_next(7, now) == {"short_history": 3}
        past = forecast_period(date(2026, 10, 19), 1, now)
    forecaster.assert_not_called()
    assert past == {"short_history": 1}  # up to the period's earliest check, the 18th at 19:00

    Lead.objects.update(received_at=timezone.now() - timedelta(days=3))  # now for the page

    params = {"forecast_view": "next", "forecast_days": 7}
    with mock.patch("leadhook.leads.tasks.forecast_leads.delay") as delay:
        admin_client.get(URL, params)
    _run_queued(delay, fake_forecaster(70, 35))
    content = admin_client.get(URL, params).content.decode()
    assert "almeno 14 giorni di lead" in content and "copre 3 giorni" in content


def test_forecast_chart_cuts_an_exploded_band():
    end = timezone.now().replace(minute=0, second=0, microsecond=0)
    snapshot = {"end": end, "counts": [4, 6, None, None], "median": [5.0, 5.0]}
    snapshot |= {"low": [1.0, 1.0], "high": [9.0, 14000.0], "mean": [5.5, 5.5]}
    chart = forecast_chart(snapshot)
    assert chart["peak"] == 18  # 3x the highest seen (6), not 14000
    assert chart["expected"] == 11  # the mean, not the median
    future = chart["points"][-1]
    assert float(future["band_bottom"]) + float(future["band_height"]) == pytest.approx(100)
