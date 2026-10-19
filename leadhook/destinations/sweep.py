"""Pipeline work whose Celery message was lost (broker outage, crashed worker, restart)."""

from datetime import datetime, timedelta

from django.db.models import Q, QuerySet

from leadhook.leads.models import Lead, WebhookEvent

from .models import Delivery

STUCK_AFTER = timedelta(minutes=5)
REFETCH_EVERY = timedelta(hours=1)
REFETCH_FOR = timedelta(days=7)


def stuck_events(now: datetime) -> QuerySet[WebhookEvent]:
    return WebhookEvent.objects.filter(
        signature_ok=True, processed_at__isnull=True, received_at__lt=now - STUCK_AFTER
    )


def stuck_received_leads(now: datetime) -> QuerySet[Lead]:
    """Leads whose fetch gave up or was lost; a failed fetch saves its error, bumping updated_at."""
    return Lead.objects.filter(
        status=Lead.Status.RECEIVED,
        updated_at__lt=now - REFETCH_EVERY,
        received_at__gte=now - REFETCH_FOR,
    )


def stuck_fetched_leads(now: datetime) -> QuerySet[Lead]:
    return Lead.objects.filter(status=Lead.Status.FETCHED, updated_at__lt=now - STUCK_AFTER)


def stuck_deliveries(now: datetime) -> QuerySet[Delivery]:
    """Pending, not leased, and either never scheduled or overdue for their retry."""
    grace = now - STUCK_AFTER
    return (
        Delivery.objects.filter(status=Delivery.Status.PENDING)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .filter(Q(next_retry_at__isnull=True, updated_at__lt=grace) | Q(next_retry_at__lt=grace))
    )
