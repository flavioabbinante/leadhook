"""Pure functions that turn Meta payloads into plain Python values."""

from dataclasses import dataclass
from datetime import datetime

META_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


@dataclass(frozen=True)
class LeadgenChange:
    leadgen_id: str
    page_id: str
    form_id: str


def extract_leadgen_changes(payload: dict) -> list[LeadgenChange]:
    """Return every leadgen change in a Meta webhook payload, skipping anything malformed."""
    changes: list[LeadgenChange] = []
    entries = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return changes
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "leadgen":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                continue
            changes.append(
                LeadgenChange(
                    leadgen_id=str(leadgen_id),
                    page_id=str(value.get("page_id") or entry.get("id") or ""),
                    form_id=str(value.get("form_id") or ""),
                )
            )
    return changes


def simplify_field_data(field_data: list[dict] | None) -> dict:
    """Map Meta field_data to {name: value}: one value -> string, several -> list, none -> ""."""
    result: dict = {}
    for field in field_data or []:
        values = field.get("values") or []
        if len(values) == 0:
            result[field["name"]] = ""
        elif len(values) == 1:
            result[field["name"]] = values[0]
        else:
            result[field["name"]] = list(values)
    return result


def parse_meta_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, META_TIME_FORMAT)
