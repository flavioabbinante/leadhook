import logging

from celery import shared_task
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert

from .graph import GraphError
from .models import MetaApp, Page
from .services import check_meta_app, check_page, record_page_error, silent_pages, sync_page_forms

logger = logging.getLogger(__name__)


@shared_task
def sync_forms() -> None:
    for page in Page.objects.select_related("meta_app"):
        try:
            sync_page_forms(page)
        except GraphError as exc:
            if exc.is_token_error:
                record_page_error(page, f"Meta rejected the page token: {exc}")
            else:
                logger.warning("Form sync of page %s failed: %s", page.page_id, exc)


@shared_task
def check_pages() -> None:
    now = timezone.now()
    for meta_app in MetaApp.objects.all():
        check_meta_app(meta_app, now=now)
    for page in Page.objects.select_related("meta_app"):
        check_page(page, now=now)


@shared_task
def check_silence() -> None:
    now = timezone.now()
    pages = list(silent_pages(now))
    if not pages:
        return
    # One email for all pages: per-kind throttling would drop separate ones.
    send_alert(
        AlertKind.SILENCE,
        f"No leads from {len(pages)} page(s)",
        "\n".join(f"{page}: last lead {page.last_lead_at:%Y-%m-%d %H:%M} UTC" for page in pages),
    )
    Page.objects.filter(pk__in=[page.pk for page in pages]).update(silence_alerted_at=now)
