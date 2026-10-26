"""Campaign statistics: leads per day and per campaign, ad set, ad and form."""

import csv
from datetime import date, datetime, timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, QuerySet
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone

from .models import Lead

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
DROP_RATIO = 0.3


def period_leads(days: int, today: date) -> QuerySet:
    """Leads of the last `days` local days, annotated with the local day they were sent."""
    start = today - timedelta(days=days - 1)
    sent = Coalesce("created_time", "received_at")
    day = TruncDate(sent, tzinfo=timezone.get_current_timezone())
    return Lead.objects.annotate(day=day).filter(day__gte=start, day__lte=today)


def lead_drop(now: datetime) -> tuple[int, float] | None:
    """(received, expected) when the last DROP_WINDOW got far fewer leads than the same hours
    of the previous DROP_WEEKS weeks, else None. Test leads (manual) don't count."""
    # shortcut: plain same-hours average; swap in a forecasting model if it raises false alarms.
    leads = Lead.objects.exclude(source=Lead.Source.MANUAL)

    def received(end: datetime) -> int:
        return leads.filter(received_at__gte=end - DROP_WINDOW, received_at__lt=end).count()

    expected = sum(received(now - timedelta(weeks=w)) for w in range(1, DROP_WEEKS + 1))
    expected /= DROP_WEEKS
    actual = received(now)
    if expected >= DROP_MIN_EXPECTED and actual < expected * DROP_RATIO:
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
