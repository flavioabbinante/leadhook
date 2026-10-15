import json
from datetime import UTC, datetime

from leadhook.leads.models import Lead

from .models import Delivery, Destination
from .templating import render_payload

PAYLOAD_VERSION = 1


def _meta_time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000") if value else ""


def build_context(lead: Lead, delivery: Delivery, *, attempt: int, sent_at: datetime) -> dict:
    """The standard payload; also the variables available to custom templates."""
    form, page = lead.form, lead.page
    return {
        "id": lead.leadgen_id,
        "created_time": _meta_time(lead.created_time),
        "data": lead.data,
        "form": {
            "id": form.form_id if form else lead.form_external_id,
            "name": form.name if form else "",
            "locale": form.locale if form else "",
            "status": form.status if form else "",
        },
        "page": {
            "id": page.page_id if page else lead.page_external_id,
            "name": page.name if page else "",
        },
        "ad": {"id": lead.ad_id, "name": lead.ad_name},
        "adset": {"id": lead.adset_id, "name": lead.adset_name},
        "campaign": {"id": lead.campaign_id, "name": lead.campaign_name},
        "is_organic": lead.is_organic,
        "field_data": lead.field_data,
        "_leadhook": {
            "version": PAYLOAD_VERSION,
            "delivery_id": delivery.public_id,
            "attempt": attempt,
            "source": lead.source,
            "sent_at": sent_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def render_body(destination: Destination, context: dict) -> str:
    if destination.payload_template.strip():
        return render_payload(destination.payload_template, context)
    return json.dumps(context, ensure_ascii=False)
