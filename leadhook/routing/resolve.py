from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead


def resolve_destinations(lead: Lead) -> list[Destination]:
    """Form destinations win when configured (even if paused); otherwise page defaults."""
    if lead.form is not None and lead.form.destinations.exists():
        candidates = lead.form.destinations.all()
    elif lead.page is not None:
        candidates = lead.page.default_destinations.all()
    else:
        return []
    return list(candidates.filter(active=True).order_by("id"))
