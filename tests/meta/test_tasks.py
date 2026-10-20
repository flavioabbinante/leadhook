from datetime import timedelta

import responses
from django.utils import timezone

from leadhook.meta.tasks import check_silence, sync_forms
from tests.factories import make_page

BASE = "https://graph.facebook.com/v21.0"


def test_check_silence_alerts_once_per_silence(mailoutbox):
    page = make_page(last_lead_at=timezone.now() - timedelta(hours=30))
    check_silence()
    check_silence()
    assert len(mailoutbox) == 1
    assert "111" in mailoutbox[0].body
    page.refresh_from_db()
    assert page.silence_alerted_at is not None


@responses.activate
def test_sync_forms_token_error_flags_page(mailoutbox):
    page = make_page()
    responses.get(
        f"{BASE}/111/leadgen_forms", status=400, json={"error": {"message": "x", "code": 190}}
    )
    sync_forms()
    page.refresh_from_db()
    assert page.error.startswith("Meta ha rifiutato il token della pagina")
