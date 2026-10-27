import logging
from datetime import date, timedelta

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert
from leadhook.meta.graph import GraphError, client_for_page
from leadhook.meta.models import Form
from leadhook.meta.services import record_page_error

from .models import Lead, WebhookEvent
from .parsing import extract_leadgen_changes
from .retention import purge_expired_data
from .services import (
    FETCH_BACKOFF,
    FetchOutcome,
    fetch_lead_details,
    ingest_lead,
    reconcile_form,
)
from .stats import DROP_WINDOW, forecast_period, lead_drop, period_cache_key

logger = logging.getLogger(__name__)


@shared_task
def process_event(event_id: int) -> None:
    event = WebhookEvent.objects.get(pk=event_id)
    if event.processed_at is not None or not event.signature_ok:
        return
    for change in extract_leadgen_changes(event.payload):
        lead = ingest_lead(
            leadgen_id=change.leadgen_id,
            page_external_id=change.page_id,
            form_external_id=change.form_id,
            source=Lead.Source.WEBHOOK,
        )
        if lead is None:
            # Redelivered event: the earlier fetch dispatch may have been lost.
            lead = Lead.objects.filter(
                leadgen_id=change.leadgen_id, status=Lead.Status.RECEIVED
            ).first()
        if lead is not None:
            fetch_lead.delay(lead.pk)
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at"])


# `attempt` is the retry index, carried in the task arguments: self.request.retries restarts
# at 0 on every fresh dispatch. max_retries=None so Celery's own limit never interferes.
@shared_task(bind=True, max_retries=None)
def fetch_lead(self, lead_id: int, attempt: int = 0) -> None:
    lead = Lead.objects.select_related("page__meta_app", "form").get(pk=lead_id)
    if lead.status != Lead.Status.RECEIVED or lead.purged_at is not None:
        return
    outcome = fetch_lead_details(lead)
    if outcome == FetchOutcome.RETRY:
        if attempt >= len(FETCH_BACKOFF):
            # Stays "received" with its error; the hourly sweep (and reconciliation) retry it.
            send_alert(
                AlertKind.FETCH_FAILED,
                "Impossibile leggere il lead da Meta",
                f"Non è stato possibile leggere il lead {lead.leadgen_id} da Meta dopo "
                f"{attempt + 1} tentativi: {lead.error}\n"
                "LeadHook continua a riprovare ogni ora per 7 giorni.",
            )
            return
        raise self.retry(
            args=(lead_id,), kwargs={"attempt": attempt + 1}, countdown=FETCH_BACKOFF[attempt]
        )
    if outcome == FetchOutcome.DONE:
        from leadhook.destinations.tasks import route_lead

        route_lead.delay(lead.pk)


@shared_task
def reconcile_leads() -> None:
    """Recover leads whose webhook never arrived (spec §5)."""
    from leadhook.destinations.tasks import route_lead

    now = timezone.now()
    # shortcut: one Graph call per active form every 15 minutes; batch by page if rate limits bite.
    for form in Form.objects.filter(status="ACTIVE").select_related("page__meta_app"):
        try:
            leads = reconcile_form(form, client_for_page(form.page), now=now)
        except GraphError as exc:
            if exc.is_token_error:
                record_page_error(form.page, f"Meta ha rifiutato il token della pagina: {exc}")
            else:
                logger.warning("Reconciliation of form %s failed: %s", form.form_id, exc)
            continue
        for lead in leads:
            route_lead.delay(lead.pk)


@shared_task
def purge_old_data() -> None:
    logger.info("Retention purge: %s", purge_expired_data(now=timezone.now()))


@shared_task
def check_lead_drop() -> None:
    drop = lead_drop(timezone.now())
    # One email a day per drop: send_alert's 15-minute pause would repeat it every hour.
    if drop is None or not cache.add("lead_drop_alerted", 1, timeout=86400):
        return
    received, expected = drop
    hours = int(DROP_WINDOW.total_seconds() // 3600)
    send_alert(
        AlertKind.LEAD_DROP,
        "Lead in calo",
        f"Nelle ultime {hours} ore sono arrivati {received} lead; in base alle settimane "
        f"precedenti ne erano attesi circa {expected:.0f}.\n"
        "Controlla campagne, form e collegamento con Meta.",
    )


@shared_task
def forecast_lead_period(first_day: str, days: int) -> None:
    """Timestral's forecast of a period for the stats page; {} when Timestral is unavailable."""
    day = date.fromisoformat(first_day)
    key = period_cache_key(day, days)
    try:
        result = forecast_period(day, days, timezone.now()) or {}
        # A period that includes today grows every hour: recompute it soon.
        ongoing = day + timedelta(days=days) > timezone.localdate()
        cache.set(key, result, timeout=600 if ongoing else 6 * 3600)
    finally:
        cache.delete(f"{key}:pending")
