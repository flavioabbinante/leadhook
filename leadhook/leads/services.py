"""Lead ingestion and fetching. Celery-free so it can be tested and reused directly."""

from datetime import datetime, timedelta
from enum import StrEnum

from django.db import models
from django.utils import timezone

from leadhook.meta.graph import GraphClient, GraphError, client_for_page
from leadhook.meta.models import Form, Page
from leadhook.meta.services import record_page_error

from .models import Lead
from .parsing import parse_meta_time, simplify_field_data

# Seconds to wait before each retry of a transient Graph API failure.
FETCH_BACKOFF = [30, 120, 600, 1800, 3600, 3600]
RECONCILE_OVERLAP = timedelta(hours=1)
RECONCILE_FIRST_WINDOW = timedelta(days=1)


class FetchOutcome(StrEnum):
    DONE = "done"
    RETRY = "retry"
    GIVE_UP = "give_up"


def ingest_lead(
    *, leadgen_id: str, page_external_id: str, form_external_id: str, source: str
) -> Lead | None:
    """Create the lead if it is new. Returns None for leads already known (deduplication)."""
    lead, created = Lead.objects.get_or_create(
        leadgen_id=leadgen_id,
        defaults={
            "page_external_id": page_external_id,
            "form_external_id": form_external_id,
            "source": source,
        },
    )
    return lead if created else None


def _fit(model: type[models.Model], field: str, value) -> str:
    """Coerce a Graph API value to text that fits the field (Meta names can be longer)."""
    text = "" if value is None else str(value)
    return text[: model._meta.get_field(field).max_length]


def _resolve_page(lead: Lead) -> Page | None:
    if lead.page is not None:
        return lead.page
    return Page.objects.select_related("meta_app").filter(page_id=lead.page_external_id).first()


def _get_or_create_form(page: Page, form_external_id: str, client: GraphClient) -> Form | None:
    if not form_external_id:
        return None
    form = Form.objects.filter(form_id=form_external_id).first()
    if form is not None:
        return form
    try:
        raw = client.get_form(form_external_id)
    except GraphError:
        raw = {}
    form, _ = Form.objects.get_or_create(
        form_id=form_external_id,
        defaults={
            "page": page,
            "name": _fit(Form, "name", raw.get("name")),
            "locale": _fit(Form, "locale", raw.get("locale")),
            "status": _fit(Form, "status", raw.get("status")),
            "last_synced_at": timezone.now() if raw else None,
        },
    )
    return form


def fetch_lead_details(lead: Lead) -> FetchOutcome:
    """Read the lead from the Graph API and store its details."""
    page = _resolve_page(lead)
    if page is None:
        lead.error = (
            f"Unknown page {lead.page_external_id}: "
            "connect it in the admin, then reprocess the lead."
        )
        lead.save(update_fields=["error", "updated_at"])
        return FetchOutcome.GIVE_UP

    lead.page = page
    client = client_for_page(page)
    try:
        raw = client.get_lead(lead.leadgen_id)
    except GraphError as exc:
        lead.error = str(exc)
        lead.save(update_fields=["page", "error", "updated_at"])
        if exc.is_token_error:
            record_page_error(page, f"Meta rejected the page token: {exc}")
            return FetchOutcome.GIVE_UP
        return FetchOutcome.RETRY if exc.is_transient else FetchOutcome.GIVE_UP

    store_lead_details(lead, page, raw, client)
    return FetchOutcome.DONE


def store_lead_details(lead: Lead, page: Page, raw: dict, client: GraphClient) -> None:
    """Save a Graph API lead object on `lead` and mark it fetched."""
    lead.page = page
    lead.created_time = parse_meta_time(raw.get("created_time"))
    for name in ("ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name"):
        setattr(lead, name, _fit(Lead, name, raw.get(name)))
    lead.is_organic = bool(raw.get("is_organic", False))
    lead.field_data = raw.get("field_data") or []
    lead.data = simplify_field_data(lead.field_data)
    lead.form_external_id = raw.get("form_id") or lead.form_external_id
    lead.form = _get_or_create_form(page, lead.form_external_id, client)
    lead.status = Lead.Status.FETCHED
    lead.error = ""
    lead.save()
    Page.objects.filter(pk=page.pk).update(last_lead_at=timezone.now(), error="")


def reconcile_form(form: Form, client: GraphClient, *, now: datetime) -> list[Lead]:
    """Store leads Meta has for the form that LeadHook never fetched; return them for routing."""
    since = (form.last_reconciled_at or now - RECONCILE_FIRST_WINDOW) - RECONCILE_OVERLAP
    found: list[Lead] = []
    for raw in client.list_form_leads(form.form_id, since=since):
        leadgen_id = str(raw.get("id") or "")
        if not leadgen_id:
            continue
        lead = ingest_lead(
            leadgen_id=leadgen_id,
            page_external_id=form.page.page_id,
            form_external_id=form.form_id,
            source=Lead.Source.RECONCILE,
        )
        if lead is None:
            # Known lead: finish it only if its own fetch never succeeded.
            lead = Lead.objects.filter(leadgen_id=leadgen_id, status=Lead.Status.RECEIVED).first()
        if lead is None:
            continue
        store_lead_details(lead, form.page, raw, client)
        found.append(lead)
    form.last_reconciled_at = now
    form.save(update_fields=["last_reconciled_at"])
    return found
