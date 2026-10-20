import json
from datetime import UTC, datetime

import pytest

from leadhook.destinations.models import Delivery, FieldMapping
from leadhook.destinations.payload import build_context, render_body
from leadhook.destinations.templating import TemplateError
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


def _map(destination, target, source, **kwargs):
    FieldMapping.objects.create(destination=destination, target=target, source=source, **kwargs)


def test_render_body_uses_field_mappings():
    lead = make_lead(data={"full_name": "Jane Van Doe", "phone": "+39 333 1234567"})
    context, delivery = _context(lead)
    destination = delivery.destination
    _map(destination, "campaign_code", FieldMapping.FIXED, fixed_value="7")
    _map(destination, "channel", FieldMapping.FIXED, fixed_value="Web")
    _map(destination, "fields.nome", "answer:full_name", transform="first_word")
    _map(destination, "fields.cognome", "answer:full_name", transform="rest_words")
    _map(destination, "fields.cell", "answer:phone", transform="strip_plus39")
    _map(destination, "fields.missing", "answer:city")
    _map(destination, "campaign", "lead:campaign.name")
    assert json.loads(render_body(destination, context)) == {
        "campaign_code": 7,
        "channel": "Web",
        "fields": {"nome": "Jane", "cognome": "Van Doe", "cell": "3331234567", "missing": ""},
        "campaign": "Campaign 1",
    }


def test_template_wins_over_field_mappings():
    context, delivery = _context()
    delivery.destination.payload_template = '{"a": 1}'
    _map(delivery.destination, "b", FieldMapping.FIXED, fixed_value="2")
    assert json.loads(render_body(delivery.destination, context)) == {"a": 1}


def test_clashing_field_mappings_raise():
    context, delivery = _context()
    _map(delivery.destination, "fields", FieldMapping.FIXED, fixed_value="x")
    _map(delivery.destination, "fields.email", "answer:email")
    with pytest.raises(TemplateError):
        render_body(delivery.destination, context)


def test_telegram_body_lists_answers_under_question_labels():
    lead = make_lead()
    lead.form.questions = [{"key": "full_name", "label": "Nome e cognome"}]
    lead.form.save()
    context, delivery = _context(lead)
    destination = delivery.destination
    destination.kind, destination.telegram_chat_id = "telegram", "-100"
    assert json.loads(render_body(destination, context)) == {
        "chat_id": "-100",
        "text": "New lead · Test form\n\nNome e cognome: Jane Doe\n\nCampaign: Campaign 1",
    }
