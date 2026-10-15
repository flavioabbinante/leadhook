from leadhook.leads.models import Lead
from leadhook.routing.resolve import resolve_destinations
from tests.factories import make_destination, make_lead


def test_form_destinations_take_precedence_over_page_defaults():
    lead = make_lead()
    page_default = make_destination(name="default")
    form_specific = make_destination(name="specific")
    lead.page.default_destinations.add(page_default)
    lead.form.destinations.add(form_specific)
    assert resolve_destinations(lead) == [form_specific]


def test_page_defaults_are_used_when_form_has_none():
    lead = make_lead()
    first = make_destination(name="a")
    second = make_destination(name="b")
    lead.page.default_destinations.add(second, first)
    assert resolve_destinations(lead) == [first, second]


def test_no_destinations_means_unrouted():
    assert resolve_destinations(make_lead()) == []


def test_inactive_destinations_are_skipped():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination(active=False))
    assert resolve_destinations(lead) == []


def test_paused_form_destination_does_not_fall_back_to_page_defaults():
    lead = make_lead()
    lead.page.default_destinations.add(make_destination(name="default"))
    lead.form.destinations.add(make_destination(name="paused", active=False))
    assert resolve_destinations(lead) == []


def test_lead_without_form_uses_page_defaults():
    lead = make_lead()
    default = make_destination()
    lead.page.default_destinations.add(default)
    lead.form = None
    assert resolve_destinations(lead) == [default]


def test_lead_without_page_and_form():
    lead = Lead(leadgen_id="1")
    assert resolve_destinations(lead) == []
