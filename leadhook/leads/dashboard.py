"""Numbers for the admin home page (spec §9)."""

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from leadhook.core.health import heartbeat_is_fresh
from leadhook.destinations.models import Delivery
from leadhook.meta.models import Page

from .models import Lead


def dashboard_stats(now: datetime) -> dict:
    from leadhook.meta.setup import setup_complete  # meta.setup imports leads models

    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    recent = Lead.objects.filter(received_at__gte=week_start)
    forms = (
        recent.values("form_external_id", "form__name")
        .annotate(today=Count("pk", filter=Q(received_at__gte=day_start)), week=Count("pk"))
        .order_by("-week", "form_external_id")
    )
    return {
        "leads_today": Lead.objects.filter(received_at__gte=day_start).count(),
        "leads_week": recent.count(),
        "forms": list(forms),
        "failed_deliveries": Delivery.objects.filter(status=Delivery.Status.FAILED).count(),
        "unrouted_leads": Lead.objects.filter(status=Lead.Status.UNROUTED).count(),
        "waiting_for_meta": Lead.objects.filter(
            status=Lead.Status.RECEIVED, received_at__lt=now - timedelta(hours=1)
        ).count(),
        "pages": Page.objects.order_by("name", "page_id"),
        "beat_ok": heartbeat_is_fresh(),
        "setup_complete": setup_complete(),
    }
