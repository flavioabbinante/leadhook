import json
from datetime import UTC, datetime

from leadhook.leads.models import Lead
from leadhook.meta.models import Form

from .models import Delivery, Destination, FieldMapping
from .telegram import TEXT_LIMIT, render_markdown
from .templating import TemplateError, _first_word, _phone_strip_prefix, _rest_words, render_payload

PAYLOAD_VERSION = 1


def _meta_time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000") if value else ""


def build_context(lead: Lead, delivery: Delivery, *, attempt: int, sent_at: datetime) -> dict:
    """The standard payload; also the variables available to custom templates."""
    form, page = lead.form, lead.page
    return {
        "id": lead.leadgen_id,
        "created_time": _meta_time(lead.created_time),
        "data": lead.data,
        "form": {
            "id": form.form_id if form else lead.form_external_id,
            "name": form.name if form else "",
            "locale": form.locale if form else "",
            "status": form.status if form else "",
        },
        "page": {
            "id": page.page_id if page else lead.page_external_id,
            "name": page.name if page else "",
        },
        "ad": {"id": lead.ad_id, "name": lead.ad_name},
        "adset": {"id": lead.adset_id, "name": lead.adset_name},
        "campaign": {"id": lead.campaign_id, "name": lead.campaign_name},
        "is_organic": lead.is_organic,
        "field_data": lead.field_data,
        "_leadhook": {
            "version": PAYLOAD_VERSION,
            "delivery_id": delivery.public_id,
            "attempt": attempt,
            "source": lead.source,
            "sent_at": sent_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def render_body(destination: Destination, context: dict) -> str:
    if destination.is_telegram:
        message = {"chat_id": destination.telegram_chat_id}
        if destination.message_template.strip():
            message["text"] = render_markdown(destination.message_template, context)
            message["parse_mode"] = "HTML"
        else:
            message["text"] = telegram_text(context)
        return json.dumps(message, ensure_ascii=False)
    if destination.payload_template.strip():
        return render_payload(destination.payload_template, context)
    mappings = list(destination.field_mappings.all())
    if mappings:
        return json.dumps(build_mapped_body(mappings, context), ensure_ascii=False)
    return json.dumps(context, ensure_ascii=False)


def telegram_text(context: dict) -> str:
    """Plain-text message: the form, each answer under its question label, the campaign."""
    form = Form.objects.filter(form_id=context["form"]["id"]).only("questions").first()
    labels = {q["key"]: q["label"] for q in form.questions} if form else {}
    lines = [f"Nuovo lead · {context['form']['name'] or 'Form Meta'}", ""]
    for key, value in context["data"].items():
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        lines.append(f"{labels.get(key, key)}: {shown}")
    if context["campaign"]["name"]:
        lines += ["", f"Campagna: {context['campaign']['name']}"]
    return "\n".join(lines)[:TEXT_LIMIT]


TRANSFORMS = {
    FieldMapping.Transform.FIRST_WORD: _first_word,
    FieldMapping.Transform.REST_WORDS: _rest_words,
    FieldMapping.Transform.STRIP_PLUS39: lambda value: _phone_strip_prefix(value, "+39"),
}


def build_mapped_body(mappings: list[FieldMapping], context: dict) -> dict:
    """The payload described by field mappings; dotted names become nested objects."""
    body: dict = {}
    for mapping in mappings:
        *parents, leaf = mapping.target.split(".")
        node = body
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TemplateError(
                    f"Il campo {mapping.target} è in conflitto con il campo {part}."
                )
        if leaf in node:
            raise TemplateError(f"Il campo {mapping.target} è impostato due volte.")
        node[leaf] = _mapped_value(mapping, context)
    return body


def _mapped_value(mapping: FieldMapping, context: dict):
    if mapping.source == FieldMapping.FIXED:
        try:
            value = json.loads(mapping.fixed_value)
        except ValueError:
            return mapping.fixed_value
        return value if isinstance(value, int | float | bool) else mapping.fixed_value
    kind, _, key = mapping.source.partition(":")
    value = ""
    if kind == "answer":
        value = context["data"].get(key, "")
    elif kind == "lead":
        value = context
        for part in key.split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
    transform = TRANSFORMS.get(mapping.transform)
    return transform(value) if transform else value
