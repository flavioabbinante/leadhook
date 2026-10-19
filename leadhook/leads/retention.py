"""Retention (spec §4): clear personal data older than LEAD_RETENTION_DAYS, keep metadata."""

from datetime import datetime, timedelta

from django.conf import settings

from leadhook.destinations.models import Delivery

from .models import Lead, WebhookEvent


def purge_expired_data(*, now: datetime) -> dict[str, int]:
    cutoff = now - timedelta(days=settings.LEAD_RETENTION_DAYS)
    leads = Lead.objects.filter(received_at__lt=cutoff, purged_at__isnull=True)
    # Deliveries first: once the leads are marked purged, `leads` no longer selects them.
    deliveries = Delivery.objects.filter(lead__in=leads).update(request_body="", response_body="")
    purged = leads.update(field_data=[], data={}, purged_at=now)
    events = (
        WebhookEvent.objects.filter(received_at__lt=cutoff).exclude(payload={}).update(payload={})
    )
    return {"leads": purged, "deliveries": deliveries, "events": events}
