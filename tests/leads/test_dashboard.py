from datetime import timedelta

from django.utils import timezone

from leadhook.leads.dashboard import dashboard_stats
from leadhook.leads.models import Lead
from tests.factories import make_lead


def test_dashboard_stats_counts_by_form():
    now = timezone.now()
    lead = make_lead()
    older = make_lead(leadgen_id="2", form=lead.form)
    Lead.objects.filter(pk=older.pk).update(received_at=now - timedelta(days=3))
    make_lead(leadgen_id="3", form=lead.form, status=Lead.Status.UNROUTED)
    stats = dashboard_stats(now)
    assert stats["leads_today"] == 2
    assert stats["leads_week"] == 3
    assert stats["unrouted_leads"] == 1
    assert stats["forms"] == [
        {"form_external_id": "222", "form__name": "Test form", "today": 2, "week": 3}
    ]
    assert stats["beat_ok"] is False
