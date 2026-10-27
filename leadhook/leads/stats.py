"""Campaign statistics: leads per day and per campaign, ad set, ad and form."""

import csv
import functools
import logging
from datetime import UTC, date, datetime, timedelta

from django.contrib import admin
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
DROP_RATIO = 0.3  # without Timestral: alert under this share of the same-hours average
FORECAST_MODEL = "mistralai/Timestral-28M"
FORECAST_LOW = 0.05  # with Timestral: alert under this quantile of the forecast


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


def _forecast(history: list[int], steps: int) -> tuple[float, float] | None:
    """(median, low) leads Timestral expects in the next `steps` hours, None if unavailable."""
    try:
        import numpy as np

        forecast = _forecaster().forecast(np.array(history, dtype=float), prediction_length=steps)
    except ImportError:
        return None
    except Exception:
        logger.exception("Timestral forecast failed, falling back to the plain average")
        return None
    # shortcut: summing hourly quantiles gives a lower bound than the window's true quantile
    # (fewer alerts); forecast 6-hour buckets instead if real drops go unnoticed.
    return float(forecast.median.sum()), float(forecast.quantile(FORECAST_LOW).sum())


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
    hour, or without it from the same hours of those weeks. Test leads (manual) don't count."""
    window = int(DROP_WINDOW.total_seconds() // 3600)
    week = 7 * 24
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - DROP_WINDOW - timedelta(weeks=DROP_WEEKS)
    counts = hourly_counts(
        Lead.objects.exclude(source=Lead.Source.MANUAL), start, DROP_WEEKS * week + window
    )
    history, actual = counts[:-window], sum(counts[-window:])

    forecast = _forecast(history, window)
    if forecast is not None:
        expected, low = forecast
        drop = actual < low
    else:
        same_hours = [counts[w * week : w * week + window] for w in range(DROP_WEEKS)]
        expected = sum(map(sum, same_hours)) / DROP_WEEKS
        drop = actual < expected * DROP_RATIO
    if expected >= DROP_MIN_EXPECTED and drop:
        return actual, expected
    return None


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
    }
    return TemplateResponse(request, "admin/leadhook_stats.html", context)
