from datetime import UTC, datetime

import pytest

from leadhook.leads.parsing import (
    LeadgenChange,
    extract_leadgen_changes,
    parse_meta_time,
    simplify_field_data,
)
from tests.fixtures import leadgen_payload


def test_extract_leadgen_changes():
    assert extract_leadgen_changes(leadgen_payload()) == [
        LeadgenChange(leadgen_id="333", page_id="111", form_id="222")
    ]


def test_extract_ignores_other_fields_and_coerces_ids_to_strings():
    payload = leadgen_payload()
    payload["entry"][0]["changes"].append({"field": "feed", "value": {"post_id": "1"}})
    payload["entry"][0]["changes"][0]["value"]["leadgen_id"] = 333
    assert extract_leadgen_changes(payload) == [
        LeadgenChange(leadgen_id="333", page_id="111", form_id="222")
    ]


def test_extract_falls_back_to_entry_id_for_page():
    payload = leadgen_payload()
    del payload["entry"][0]["changes"][0]["value"]["page_id"]
    assert extract_leadgen_changes(payload)[0].page_id == "111"


def test_extract_tolerates_malformed_payloads():
    assert extract_leadgen_changes({}) == []
    assert extract_leadgen_changes({"entry": "nope"}) == []
    assert extract_leadgen_changes({"entry": [{"changes": [{"field": "leadgen"}]}]}) == []


def test_simplify_field_data_handles_empty_and_multi_values():
    field_data = [
        {"name": "full_name", "values": ["Jane Doe"]},
        {"name": "services", "values": ["electricity", "gas"]},
        {"name": "notes", "values": []},
        {"name": "city"},
    ]
    assert simplify_field_data(field_data) == {
        "full_name": "Jane Doe",
        "services": ["electricity", "gas"],
        "notes": "",
        "city": "",
    }


def test_simplify_field_data_accepts_none():
    assert simplify_field_data(None) == {}


def test_parse_meta_time():
    assert parse_meta_time("2026-10-15T08:30:00+0000") == datetime(2026, 10, 15, 8, 30, tzinfo=UTC)
    assert parse_meta_time(None) is None
    assert parse_meta_time("") is None


def test_extract_skips_changes_whose_value_is_not_an_object():
    payload = {
        "entry": [
            {
                "id": "111",
                "changes": [
                    {"field": "leadgen", "value": "nope"},
                    {"field": "leadgen", "value": ["x"]},
                    {"field": "leadgen", "value": {"leadgen_id": "9", "form_id": "2"}},
                ],
            }
        ]
    }
    assert [c.leadgen_id for c in extract_leadgen_changes(payload)] == ["9"]


def test_simplify_field_data_skips_malformed_items():
    field_data = [
        {"values": ["no name"]},
        "junk",
        {"name": "", "values": ["empty name"]},
        {"name": "city", "values": "Rome"},
        {"name": "note", "values": None},
    ]
    assert simplify_field_data(field_data) == {"city": "Rome", "note": ""}


def test_simplify_field_data_non_list_input():
    assert simplify_field_data({"full_name": "x"}) == {}


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01T00:00:00+0000", 1760517000, ""])
def test_parse_meta_time_malformed_is_none(value):
    assert parse_meta_time(value) is None
