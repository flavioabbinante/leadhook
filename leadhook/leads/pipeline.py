"""The steps a lead went through, for the n8n-style view on the lead page."""

from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone

from leadhook.destinations.models import Delivery

from .models import Lead

DELIVERY_STATES = {
    Delivery.Status.SUCCESS: "done",
    Delivery.Status.FAILED: "error",
    Delivery.Status.PENDING: "pending",
}


@dataclass
class Step:
    title: str
    icon: str
    state: str  # "done" | "pending" | "error"
    detail: str = ""
    time: str = ""
    url: str = ""


def _when(value) -> str:
    return timezone.localtime(value).strftime("%d/%m %H:%M:%S") if value else ""


def lead_pipeline(lead: Lead) -> tuple[list[Step], list[Step]]:
    """The linear steps up to routing, then one branch per delivery."""
    status = lead.status
    form = lead.form.name if lead.form else lead.form_external_id
    # Meta's created_time is when the person sent the form, before the webhook reached us.
    submitted = Step("Modulo inviato", "list", "done", form, _when(lead.created_time))
    received = Step(
        lead.get_source_display(), "zap", "done", "Lead ricevuto", _when(lead.received_at)
    )

    if status == Lead.Status.RECEIVED:
        fetched = Step("Dati del lead", "box", "error" if lead.error else "pending", lead.error)
    else:
        answers = "Dati cancellati per scadenza" if lead.purged_at else f"{len(lead.data)} risposte"
        fetched = Step("Dati del lead", "box", "done", f"Letto da Meta · {answers}")

    deliveries = list(lead.deliveries.select_related("destination").order_by("id"))
    if status in (Lead.Status.RECEIVED, Lead.Status.FETCHED):
        routing = Step("Instradamento", "shuffle", "pending", "In attesa")
    elif status == Lead.Status.UNROUTED:
        routing = Step(
            "Instradamento", "shuffle", "error", "Nessuna destinazione attiva per questo modulo"
        )
    else:
        routing = Step("Instradamento", "shuffle", "done", f"Destinazioni: {len(deliveries)}")

    branches = []
    for delivery in deliveries:
        parts = [delivery.get_status_display()]
        if delivery.last_status_code:
            parts.append(f"HTTP {delivery.last_status_code}")
        if delivery.attempts > 1:
            parts.append(f"{delivery.attempts} tentativi")
        if delivery.status == Delivery.Status.PENDING and delivery.next_retry_at:
            parts.append(f"nuovo tentativo {_when(delivery.next_retry_at)}")
        destination = delivery.destination
        branches.append(
            Step(
                destination.name,
                "send" if destination.is_telegram else "target",
                DELIVERY_STATES.get(delivery.status, "pending"),
                " · ".join(parts),
                _when(delivery.delivered_at or delivery.updated_at),
                reverse("admin:destinations_delivery_change", args=[delivery.pk]),
            )
        )
    return [submitted, received, fetched, routing], branches
