from celery import shared_task
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert

from .models import Lead, WebhookEvent
from .parsing import extract_leadgen_changes
from .services import FETCH_BACKOFF, FetchOutcome, fetch_lead_details, ingest_lead


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
    if lead.status != Lead.Status.RECEIVED:
        return
    outcome = fetch_lead_details(lead)
    if outcome == FetchOutcome.RETRY:
        if attempt >= len(FETCH_BACKOFF):
            # Stays "received" with its error; the hourly sweep (and reconciliation) retry it.
            send_alert(
                AlertKind.FETCH_FAILED,
                "Lead could not be fetched",
                f"Lead {lead.leadgen_id} could not be read from Meta after {attempt + 1} "
                f"attempts: {lead.error}\nLeadHook keeps retrying it every hour for 7 days.",
            )
            return
        raise self.retry(
            args=(lead_id,), kwargs={"attempt": attempt + 1}, countdown=FETCH_BACKOFF[attempt]
        )
    if outcome == FetchOutcome.DONE:
        from leadhook.destinations.tasks import route_lead

        route_lead.delay(lead.pk)
