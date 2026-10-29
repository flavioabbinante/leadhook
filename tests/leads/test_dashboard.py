from datetime import timedelta

from django.utils import timezone

from leadhook.destinations.models import Delivery
from leadhook.leads.dashboard import dashboard_stats
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_lead


def test_dashboard_stats_counts_by_form():
    now = timezone.now()
    lead = make_lead(created_time=now)
    older = make_lead(leadgen_id="2", form=lead.form)
    Lead.objects.filter(pk=older.pk).update(created_time=now - timedelta(days=3))
    make_lead(leadgen_id="3", form=lead.form, created_time=now, status=Lead.Status.UNROUTED)
    stats = dashboard_stats(now)
    assert stats["leads_today"] == 2
    assert stats["leads_week"] == 3
    assert stats["unrouted_leads"] == 1
    assert stats["forms"] == [
        {"form_external_id": "222", "form__name": "Test form", "today": 2, "week": 3, "share": 100}
    ]
    assert stats["beat_ok"] is False


def test_dashboard_stats_trends_and_delivery_health():
    now = timezone.now()
    lead = make_lead(created_time=now, status=Lead.Status.DELIVERED)
    older = make_lead(leadgen_id="2", form=lead.form)
    Lead.objects.filter(pk=older.pk).update(created_time=now - timedelta(days=9))
    destination = make_destination()
    Delivery.objects.create(lead=lead, destination=destination, status=Delivery.Status.SUCCESS)
    Delivery.objects.create(lead=older, destination=destination, status=Delivery.Status.FAILED)
    stats = dashboard_stats(now)
    assert (stats["leads_week"], stats["leads_previous_week"]) == (1, 1)
    assert (stats["delivery_total"], stats["delivery_success"], stats["delivery_failed"]) == (
        2,
        50,
        50,
    )
    assert [p.week for p in stats["pages"]] == [2 - 1]
    assert list(stats["recent_leads"])[0] == lead


def test_dashboard_counts_replayed_leads_on_the_day_meta_created_them():
    now = timezone.now()
    make_lead(created_time=now - timedelta(days=20))  # received now, e.g. a Meta resend
    stats = dashboard_stats(now)
    assert (stats["leads_today"], stats["leads_week"]) == (0, 0)
    assert [p.week for p in stats["pages"]] == [0]
