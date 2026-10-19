"""Page and form operations shared by tasks, admin and setup."""

from leadhook.core.alerts import AlertKind, send_alert

from .models import Page


def record_page_error(page: Page, message: str) -> None:
    """Store a page problem; alert only when it is new (repeated checks do not re-alert)."""
    is_new = page.error != message
    page.error = message
    page.save(update_fields=["error"])
    if is_new:
        send_alert(AlertKind.PAGE_ERROR, f"Problem with page {page.page_id}", f"{page}: {message}")
