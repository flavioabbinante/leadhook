"""Campaign statistics: leads per day and per campaign, ad set, ad and form."""

import csv
import functools
import logging
import math
from datetime import UTC, date, datetime, time, timedelta

from django.contrib import admin
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Count, QuerySet
from django.db.models.functions import Coalesce, TruncDate, TruncHour
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone

from .models import Lead

logger = logging.getLogger(__name__)

PERIODS = (7, 30, 90)
BREAKDOWNS = (
    ("campaign_name", "Campagna"),
    ("adset_name", "Gruppo di inserzioni"),
    ("ad_name", "Inserzione"),
    ("form__name", "Form"),
)
NO_NAME = "Senza nome (organico o non disponibile)"
ALL_CAMPAIGNS = "__all__"  # "" is a real filter value: leads without a campaign name

DROP_WINDOW = timedelta(hours=6)
DROP_WEEKS = 4
DROP_MIN_EXPECTED = 5  # below this the usual volume is too small to tell a drop from noise
DROP_RATIO = 0.3  # alert only under this share of the expected volume
FORECAST_MODEL = "mistralai/Timestral-28M"
FORECAST_LOW = 0.05  # with Timestral, also under this quantile: an unusual drop, not just a dip
FORECAST_HIGH = 0.95  # top of the band drawn on the stats page
FORECAST_CACHE_KEY = "lead_forecast"  # the last check's forecast, for the stats page chart
FORECAST_CHART_HOURS = 48
FORECAST_PERIODS = (1, 2, 3, 7, 14)  # days that can be reviewed at once: 14 take ~30 s on CPU


def period_leads(days: int, today: date) -> QuerySet:
    """Leads of the last `days` local days, annotated with the local day they were sent."""
    start = today - timedelta(days=days - 1)
    sent = Coalesce("created_time", "received_at")
    day = TruncDate(sent, tzinfo=timezone.get_current_timezone())
    return Lead.objects.annotate(day=day).filter(day__gte=start, day__lte=today)


@functools.cache
def _forecaster():
    from timestral import TimestralForecaster  # optional extra "forecast": torch is heavy

    return TimestralForecaster.from_pretrained(FORECAST_MODEL)


def _forecast(histories: list[list[int]], steps: int) -> list[tuple[list, list, list]] | None:
    """For each history, the hourly (median, low, high) leads Timestral expects in the next
    `steps` hours; None if unavailable. One batch: much faster than a call per history."""
    try:
        import numpy as np

        forecast = _forecaster().forecast(np.array(histories, dtype=float), prediction_length=steps)
    except ImportError:
        return None
    except Exception:
        logger.exception("Timestral forecast failed, falling back to the plain average")
        return None
    quantiles = forecast.median, forecast.quantile(FORECAST_LOW), forecast.quantile(FORECAST_HIGH)
    return [
        tuple([float(v) for v in values[i].flatten()] for values in quantiles)
        for i in range(len(histories))
    ]


def _is_drop(actual: int, expected: float, low: float) -> bool:
    # A very regular history gives a tight forecast: the ratio keeps small dips quiet.
    return expected >= DROP_MIN_EXPECTED and actual < min(low, expected * DROP_RATIO)


def hourly_counts(leads: QuerySet, start: datetime, hours: int) -> list[int]:
    """Leads per hour from `start` (on the hour), zero-filled."""
    by_hour = dict(
        leads.filter(received_at__gte=start, received_at__lt=start + timedelta(hours=hours))
        .annotate(hour=TruncHour("received_at", tzinfo=UTC))
        .values_list("hour")
        .annotate(n=Count("pk"))
        .order_by()
    )
    return [by_hour.get(start + timedelta(hours=h), 0) for h in range(hours)]


def lead_drop(now: datetime) -> tuple[int, float] | None:
    """(received, expected) when the last DROP_WINDOW full hours got far fewer leads than
    expected, else None. Expected comes from Timestral fed the previous DROP_WEEKS weeks hour by
    hour (and the count must also be under its low quantile), or without it from the same hours
    of those weeks. Test leads (manual) don't count."""
    window = int(DROP_WINDOW.total_seconds() // 3600)
    week = 7 * 24
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - DROP_WINDOW - timedelta(weeks=DROP_WEEKS)
    counts = hourly_counts(
        Lead.objects.exclude(source=Lead.Source.MANUAL), start, DROP_WEEKS * week + window
    )
    history, actual = counts[:-window], sum(counts[-window:])

    forecast = _forecast([history], window)
    if forecast is not None:
        median, low_hourly, high_hourly = forecast[0]
        # shortcut: summing hourly quantiles gives a lower bound than the window's true quantile
        # (fewer alerts); forecast 6-hour buckets instead if real drops go unnoticed.
        expected, low = sum(median), sum(low_hourly)
        snapshot = {"end": end, "counts": counts[-FORECAST_CHART_HOURS:], "median": median}
        snapshot |= {"low": low_hourly, "high": high_hourly}
        cache.set(FORECAST_CACHE_KEY, snapshot, timeout=3 * 3600)  # gone if checks stop
    else:
        same_hours = [counts[w * week : w * week + window] for w in range(DROP_WEEKS)]
        expected = sum(map(sum, same_hours)) / DROP_WEEKS
        low = expected
    return (actual, expected) if _is_drop(actual, expected, low) else None


def period_cache_key(first_day: date, days: int) -> str:
    return f"lead_forecast:{first_day.isoformat()}:{days}"


def forecast_period(first_day: date, days: int, now: datetime) -> dict | None:
    """What Timestral expected in `days` local days from `first_day`, up to `now`: each
    DROP_WINDOW block forecast from the DROP_WEEKS weeks before it, as the hourly check does.
    None without Timestral or for a day still to come."""
    window = int(DROP_WINDOW.total_seconds() // 3600)
    context = DROP_WEEKS * 7 * 24
    tz = timezone.get_current_timezone()
    start = datetime.combine(first_day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(first_day + timedelta(days=days), time.min, tzinfo=tz)
    end = min(end.astimezone(UTC), now.astimezone(UTC).replace(minute=0, second=0, microsecond=0))
    hours = int((end - start) / timedelta(hours=1))
    if hours <= 0:
        return None
    counts = hourly_counts(
        Lead.objects.exclude(source=Lead.Source.MANUAL),
        start - timedelta(hours=context),
        context + hours,
    )
    # shortcut: blocks follow UTC hours, so after a summer-time switch they start an hour off the
    # local clock (11-17 instead of 12-18); align them per local day if that ever confuses anyone.
    blocks = range(0, hours, window)
    forecasts = _forecast([counts[b : b + context] for b in blocks], window)
    if forecasts is None:
        return None
    snapshot = {"end": end, "counts": counts[context:], "median": [], "low": [], "high": []}
    snapshot["alerts"] = []
    for b, block in zip(blocks, forecasts, strict=True):
        n = min(window, hours - b)  # today's last block stops at the current hour
        median, low, high = (values[:n] for values in block)
        snapshot["median"] += median
        snapshot["low"] += low
        snapshot["high"] += high
        if _is_drop(sum(counts[context + b : context + b + n]), sum(median), sum(low)):
            snapshot["alerts"].append(start + timedelta(hours=b))
    return snapshot


def forecast_chart(snapshot: dict | None) -> dict | None:
    """Hourly bars of a forecast snapshot (the last lead-drop check, or a reviewed period); the
    forecast hours carry Timestral's band."""
    if snapshot is None:
        return None
    counts, median, low, high = (snapshot[k] for k in ("counts", "median", "low", "high"))
    window = len(median)
    first = snapshot["end"] - timedelta(hours=len(counts))
    peak = math.ceil(max(*counts, *high)) or 1

    def height(value: float) -> str:
        return f"{value * 100 / peak:.1f}"

    dense = len(counts) > 72  # several days: one label a day, bars without gaps
    points = []
    for i, count in enumerate(counts):
        hour = first + timedelta(hours=i)
        local = timezone.localtime(hour)
        point = {"hour": hour, "count": count, "height": height(count), "median": None}
        if local.hour % (24 if dense else 6) == 0:
            point["label"] = local.strftime("%d/%m" if dense else "%H:%M")
        if (f := i - len(counts) + window) >= 0:
            point |= {
                "median": round(median[f], 1),
                "low": round(low[f], 1),
                "high": round(high[f], 1),
                "band_bottom": height(low[f]),
                "band_height": height(high[f] - low[f]),
                "median_at": height(median[f]),
                "below": count < low[f],
            }
        points.append(point)
    return {
        "points": points,
        "dense": dense,
        "alerts": snapshot.get("alerts"),  # only for a reviewed period
        "end": snapshot["end"],
        "window": window,
        "actual": sum(counts[-window:]),
        "expected": round(sum(median)),
        "low": round(sum(low)),
        "high": round(sum(high)),
        "peak": peak,
        "ticks": sorted({peak, peak // 2} - {0}, reverse=True),
    }


def daily_counts(leads: QuerySet, days: int, today: date) -> list[dict]:
    """One entry per day of the period, zero-filled, with the bar height in percent."""
    counts = dict(leads.values_list("day").annotate(n=Count("pk")).order_by())
    peak = max(counts.values(), default=0)
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        count = counts.get(day, 0)
        height = f"{count * 100 / peak:.1f}" if peak else "0"
        series.append({"day": day, "count": count, "height": height})
    return series


def breakdown(leads: QuerySet, field: str, total: int) -> list[dict]:
    rows = leads.values(field).annotate(n=Count("pk")).order_by("-n", field)[:10]
    return [
        {
            "name": row[field] or NO_NAME,
            "value": row[field] or "",
            "count": row["n"],
            "share": round(row["n"] * 100 / total) if total else 0,
        }
        for row in rows
    ]


def export_csv(leads: QuerySet, days: int, today: date) -> HttpResponse:
    # Aggregated counts only: never names, emails or phone numbers.
    start = today - timedelta(days=days - 1)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="lead-{start}-{today}.csv"'
    writer = csv.writer(response)
    writer.writerow(["giorno", "campagna", "gruppo_di_inserzioni", "inserzione", "form", "lead"])
    fields = ("day", "campaign_name", "adset_name", "ad_name", "form__name")
    for row in leads.values(*fields).annotate(n=Count("pk")).order_by(*fields):
        writer.writerow([*(row[field] or "" for field in fields), row["n"]])
    return response


def stats_view(request):
    if not request.user.has_perm("leads.view_lead"):
        raise PermissionDenied
    days = int(request.GET["days"]) if request.GET.get("days") in map(str, PERIODS) else 30
    today = timezone.localdate()
    in_period = period_leads(days, today)
    campaign = request.GET.get("campaign")
    if campaign == ALL_CAMPAIGNS:
        campaign = None
    leads = in_period if campaign is None else in_period.filter(campaign_name=campaign)
    if request.GET.get("format") == "csv":
        return export_csv(leads, days, today)

    forecast_days = request.GET.get("forecast_days")
    forecast_days = int(forecast_days) if forecast_days in map(str, FORECAST_PERIODS) else 1
    try:
        forecast_day = date.fromisoformat(request.GET.get("forecast_day", ""))
    except ValueError:
        forecast_day = None
    if forecast_day is not None and forecast_day > today:
        forecast_day = None
    forecast_pending = forecast_unavailable = False
    if forecast_day is None:
        forecast = forecast_chart(cache.get(FORECAST_CACHE_KEY))
    else:
        from .tasks import forecast_lead_period  # tasks imports this module

        key = period_cache_key(forecast_day, forecast_days)
        result = cache.get(key)
        # Up to ~30 s of CPU: the worker computes it, the page reloads until it's there.
        if result is None and cache.add(f"{key}:pending", 1, timeout=600):
            forecast_lead_period.delay(forecast_day.isoformat(), forecast_days)
        forecast_pending = result is None
        forecast_unavailable = result == {}
        forecast = forecast_chart(result or None)

    series = daily_counts(leads, days, today)
    total = sum(point["count"] for point in series)
    best = max(series, key=lambda point: point["count"])
    peak = best["count"]
    context = {
        **admin.site.each_context(request),
        "title": "Statistiche",
        "days": days,
        "periods": PERIODS,
        "campaign": campaign,
        "all_campaigns": ALL_CAMPAIGNS,
        "campaigns": breakdown(in_period, "campaign_name", in_period.count()),
        "series": series,
        "label_every": {7: 1, 30: 5, 90: 15}[days],
        # Gridlines at the peak and half of it; the baseline is drawn by the chart itself.
        "ticks": sorted({peak, peak // 2} - {0}, reverse=True),
        "peak": peak,
        "total": total,
        "average": round(total / days, 1),
        "best": best if peak else None,
        "delivered": leads.filter(status=Lead.Status.DELIVERED).count(),
        "breakdowns": [
            (label, field, breakdown(leads, field, total)) for field, label in BREAKDOWNS
        ],
        "forecast": forecast,
        "forecast_day": forecast_day,
        "forecast_days": forecast_days,
        "forecast_periods": FORECAST_PERIODS,
        "forecast_pending": forecast_pending,
        "forecast_unavailable": forecast_unavailable,
        "today": today,
    }
    return TemplateResponse(request, "admin/leadhook_stats.html", context)
