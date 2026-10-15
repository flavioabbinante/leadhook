import json
from datetime import UTC, datetime

from leadhook.destinations.models import Delivery
from leadhook.destinations.payload import build_context, render_body
from tests.factories import make_destination, make_lead

SENT_AT = datetime(2026, 10, 15, 8, 30, 4, tzinfo=UTC)


def _context(lead=None, attempt=1):
    lead = lead or make_lead()
    delivery = Delivery.objects.create(lead=lead, destination=make_destination())
    return build_context(lead, delivery, attempt=attempt, sent_at=SENT_AT), delivery


def test_standard_payload_matches_n8n_simplified_shape():
    context, delivery = _context()
    assert context["id"] == "333"
    assert context["created_time"] == "2026-10-15T08:30:00+0000"
    assert context["data"] == {"full_name": "Jane Doe"}
    assert context["form"] == {
        "id": "222",
        "name": "Test form",
        "locale": "en_US",
        "status": "ACTIVE",
    }
    assert context["page"] == {"id": "111", "name": "Test page"}
    assert context["ad"] == {"id": "444", "name": "Ad 1"}
    assert context["adset"] == {"id": "555", "name": "Ad set 1"}
    assert context["campaign"] == {"id": "666", "name": "Campaign 1"}
    assert context["is_organic"] is False
    assert context["field_data"] == [{"name": "full_name", "values": ["Jane Doe"]}]
    assert context["_leadhook"] == {
        "version": 1,
        "delivery_id": delivery.public_id,
        "attempt": 1,
        "source": "webhook",
        "sent_at": "2026-10-15T08:30:04Z",
    }


def test_context_without_form_or_page_uses_external_ids():
    lead = make_lead()
    lead.form = None
    lead.page = None
    context, _ = _context(lead)
    assert context["form"] == {"id": "222", "name": "", "locale": "", "status": ""}
    assert context["page"] == {"id": "111", "name": ""}


def test_render_body_uses_standard_payload_when_template_is_empty():
    context, delivery = _context()
    body = render_body(delivery.destination, context)
    assert json.loads(body) == context


def test_render_body_uses_custom_template():
    context, delivery = _context()
    delivery.destination.payload_template = '{"name": {{ data.full_name | tojson }}}'
    assert json.loads(render_body(delivery.destination, context)) == {"name": "Jane Doe"}
