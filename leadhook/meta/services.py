"""Page and form operations shared by tasks, admin and setup."""

import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from leadhook.core.alerts import AlertKind, send_alert

from .graph import GraphClient, GraphError, client_for_page, debug_token
from .models import Form, MetaApp, Page

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_WARNING = timedelta(days=7)
SILENCE_LOOKBACK = timedelta(days=30)


def fit_to_field(model: type[models.Model], field: str, value) -> str:
    """Coerce a Graph API value to text that fits the field (Meta names can be longer)."""
    text = "" if value is None else str(value)
    return text[: model._meta.get_field(field).max_length]


def record_page_error(page: Page, message: str) -> None:
    """Store a page problem; alert only when it is new (repeated checks do not re-alert)."""
    is_new = page.error != message
    page.error = message
    page.save(update_fields=["error"])
    if is_new:
        send_alert(AlertKind.PAGE_ERROR, f"Problem with page {page.page_id}", f"{page}: {message}")


def form_questions(raw: dict) -> list[dict]:
    """Key and label of each question of a Graph form object."""
    questions = raw.get("questions")
    if not isinstance(questions, list):
        return []
    return [
        {"key": str(q["key"]), "label": str(q.get("label") or q["key"])}
        for q in questions
        if isinstance(q, dict) and q.get("key")
    ]


def sync_page_forms(page: Page, client: GraphClient | None = None) -> int:
    """Create or refresh every lead form of the page; returns how many Meta listed."""
    client = client or client_for_page(page)
    now = timezone.now()
    forms = client.list_forms(page.page_id)
    for raw in forms:
        form_id = str(raw.get("id") or "")
        if not form_id:
            continue
        Form.objects.update_or_create(
            form_id=form_id,
            defaults={
                "page": page,
                "name": fit_to_field(Form, "name", raw.get("name")),
                "locale": fit_to_field(Form, "locale", raw.get("locale")),
                "status": fit_to_field(Form, "status", raw.get("status")),
                "questions": form_questions(raw),
                "last_synced_at": now,
            },
        )
    return len(forms)


def check_meta_app(meta_app: MetaApp, *, now: datetime) -> None:
    """Alert when the app's access token is invalid or about to expire."""
    if not meta_app.access_token:
        return
    try:
        info = debug_token(meta_app, meta_app.access_token)
    except GraphError as exc:
        logger.warning("Could not validate the token of Meta app %s: %s", meta_app.app_id, exc)
        return
    if not info.get("is_valid"):
        send_alert(
            AlertKind.PAGE_ERROR,
            f"Meta app {meta_app.app_id}: invalid access token",
            f"The access token of {meta_app} is no longer valid. Replace it in the admin.",
        )
        return
    expires = info.get("expires_at") or 0
    if not expires:
        return  # System User tokens never expire
    expires_at = datetime.fromtimestamp(expires, UTC)
    MetaApp.objects.filter(pk=meta_app.pk).update(token_expires_at=expires_at)
    if expires_at - now < TOKEN_EXPIRY_WARNING:
        send_alert(
            AlertKind.PAGE_ERROR,
            f"Meta app {meta_app.app_id}: access token expires soon",
            f"The access token of {meta_app} expires on {expires_at:%Y-%m-%d}. "
            "Replace it in the admin before then (a System User token never expires).",
        )


def check_page(page: Page, *, now: datetime) -> None:
    """Make sure the page's token works and the app is subscribed to its leadgen events."""
    client = client_for_page(page)
    try:
        if page.meta_app.app_id not in client.subscribed_app_ids(page.page_id):
            client.subscribe_leadgen(page.page_id)
    except GraphError as exc:
        if exc.is_transient:
            logger.warning("Check of page %s skipped: %s", page.page_id, exc)
            return
        if exc.is_token_error:
            record_page_error(page, f"Meta rejected the page token: {exc}")
        else:
            record_page_error(page, f"Could not subscribe the page to lead events: {exc}")
        Page.objects.filter(pk=page.pk).update(last_checked_at=now)
        return
    Page.objects.filter(pk=page.pk).update(subscribed=True, error="", last_checked_at=now)


def silent_pages(now: datetime) -> QuerySet[Page]:
    """Pages that usually get leads (one in the last 30 days) but none for SILENCE_ALERT_HOURS."""
    threshold = now - timedelta(hours=settings.SILENCE_ALERT_HOURS)
    return (
        Page.objects.filter(last_lead_at__lt=threshold, last_lead_at__gte=now - SILENCE_LOOKBACK)
        .filter(Q(silence_alerted_at__isnull=True) | Q(silence_alerted_at__lt=F("last_lead_at")))
        .order_by("page_id")
    )


def connect_page(meta_app: MetaApp, raw: dict) -> tuple[Page, int]:
    """Store a page listed by /me/accounts, subscribe it to leadgen and sync its forms."""
    defaults = {"meta_app": meta_app, "name": fit_to_field(Page, "name", raw.get("name"))}
    if raw.get("access_token"):
        defaults["page_token"] = raw["access_token"]
    page, _ = Page.objects.update_or_create(page_id=str(raw["id"]), defaults=defaults)
    client = client_for_page(page)
    client.subscribe_leadgen(page.page_id)
    Page.objects.filter(pk=page.pk).update(subscribed=True, error="")
    page.subscribed, page.error = True, ""
    return page, sync_page_forms(page, client)
