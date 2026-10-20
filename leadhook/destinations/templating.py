"""Sandboxed Jinja2 rendering for custom payloads and success conditions."""

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from jinja2 import ChainableUndefined, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

META_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class TemplateError(Exception):
    pass


def _plain(value):
    return "" if isinstance(value, Undefined) or value is None else value


def _tojson(value) -> str:
    return json.dumps(_plain(value), ensure_ascii=False)


def _words(value) -> list[str]:
    return str(_plain(value)).split()


def _first_word(value) -> str:
    words = _words(value)
    return words[0] if words else ""


def _rest_words(value) -> str:
    return " ".join(_words(value)[1:])


def _phone_strip_prefix(value, prefix: str) -> str:
    number = re.sub(r"\s+", "", str(_plain(value)))
    prefixes = [prefix]
    if prefix.startswith("+"):
        prefixes.append("00" + prefix[1:])
    for candidate in prefixes:
        if number.startswith(candidate):
            return number[len(candidate) :]
    return number


def _date(value, tz: str = "UTC", fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    value = _plain(value)
    if value == "":
        return ""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value)
        try:
            moment = datetime.strptime(text, META_TIME_FORMAT)
        except ValueError:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz)).strftime(fmt)


def _map_value(value, mapping: dict):
    value = _plain(value)
    if isinstance(value, list):
        return [mapping.get(item, item) for item in value]
    try:
        return mapping.get(value, value)
    except TypeError:
        return value


_env = ImmutableSandboxedEnvironment(autoescape=False, undefined=ChainableUndefined)
_env.filters.update(
    tojson=_tojson,
    first_word=_first_word,
    rest_words=_rest_words,
    phone_strip_prefix=_phone_strip_prefix,
    date=_date,
    map_value=_map_value,
)


def render_payload(template: str, context: dict) -> str:
    """Render a JSON payload template; raise TemplateError on failure or non-JSON output."""
    try:
        rendered = _env.from_string(template).render(**context)
    except Exception as exc:
        raise TemplateError(f"Errore nel template: {exc}") from exc
    try:
        json.loads(rendered)
    except ValueError as exc:
        raise TemplateError(f"Il payload generato non è un JSON valido: {exc}") from exc
    return rendered


def evaluate_condition(expression: str, *, response, status_code: int) -> bool:
    try:
        compiled = _env.compile_expression(expression, undefined_to_none=True)
        return bool(compiled(response=response, status_code=status_code))
    except Exception as exc:
        raise TemplateError(f"Errore nella condizione di successo: {exc}") from exc
