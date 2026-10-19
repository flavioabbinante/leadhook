from django import template
from django.utils import timezone

from leadhook.leads.dashboard import dashboard_stats

register = template.Library()


@register.inclusion_tag("leads/dashboard.html")
def leadhook_dashboard() -> dict:
    return dashboard_stats(timezone.now())
