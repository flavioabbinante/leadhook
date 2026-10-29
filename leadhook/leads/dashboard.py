"""Numbers for the admin home page (spec §9)."""

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from leadhook.core.health import heartbeat_is_fresh
from leadhook.destinations.models import Delivery, Destination
from leadhook.meta.models import Page

from .models import Lead

RECENT_LEADS = 7


def _percent(part: int, total: int) -> int:
    return round(part * 100 / total) if total else 0


def dashboard_stats(now: datetime) -> dict:
    from leadhook.meta.setup import setup_complete  # meta.setup imports leads models

    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    # Counted by when the lead was sent, like the stats page: Meta replays old leads.
    leads = Lead.objects.annotate(sent=Coalesce("created_time", "received_at"))
    recent = leads.filter(sent__gte=week_start)
    forms = list(
        recent.values("form_external_id", "form__name")
        .annotate(today=Count("pk", filter=Q(sent__gte=day_start)), week=Count("pk"))
        .order_by("-week", "form_external_id")
    )
    busiest_form = max((row["week"] for row in forms), default=0)
    for row in forms:
        row["share"] = _percent(row["week"], busiest_form)

    pages = list(
        Page.objects.annotate(
            week=Count(
                "leads",
                filter=Q(leads__created_time__gte=week_start)
                | Q(leads__created_time__isnull=True, leads__received_at__gte=week_start),
            )
        ).order_by("name", "page_id")
    )
    busiest_page = max((page.week for page in pages), default=0)
    for page in pages:
        page.share = _percent(page.week, busiest_page)

    deliveries = Delivery.objects.filter(created_at__gte=week_start)
    delivery_counts = {
        status: deliveries.filter(status=status).count() for status in Delivery.Status.values
    }
    delivery_total = sum(delivery_counts.values())

    return {
        "leads_today": leads.filter(sent__gte=day_start).count(),
        "leads_yesterday": leads.filter(
            sent__gte=day_start - timedelta(days=1), sent__lt=day_start
        ).count(),
        "leads_week": recent.count(),
        "leads_previous_week": leads.filter(
            sent__gte=week_start - timedelta(days=7), sent__lt=week_start
        ).count(),
        "forms": forms,
        "failed_deliveries": Delivery.objects.filter(status=Delivery.Status.FAILED).count(),
        "unrouted_leads": Lead.objects.filter(status=Lead.Status.UNROUTED).count(),
        "waiting_for_meta": Lead.objects.filter(
            status=Lead.Status.RECEIVED, received_at__lt=now - timedelta(hours=1)
        ).count(),
        "pages": pages,
        "delivery_total": delivery_total,
        "delivery_success": _percent(delivery_counts[Delivery.Status.SUCCESS], delivery_total),
        "delivery_pending": _percent(delivery_counts[Delivery.Status.PENDING], delivery_total),
        "delivery_failed": _percent(delivery_counts[Delivery.Status.FAILED], delivery_total),
        "active_destinations": Destination.objects.filter(active=True).count(),
        "recent_leads": leads.select_related("form", "page").order_by("-sent")[:RECENT_LEADS],
        "beat_ok": heartbeat_is_fresh(),
        "setup_complete": setup_complete(),
    }
