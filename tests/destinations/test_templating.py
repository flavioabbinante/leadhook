import json

import pytest

from leadhook.destinations.templating import TemplateError, evaluate_condition, render_payload

CONTEXT = {
    "id": "333",
    "created_time": "2026-10-15T08:30:00+0000",
    "data": {
        "full_name": "Jane  Mary Doe",
        "phone_number": "+39 333 123 4567",
        "services": ["electricity", "gas"],
        "plan": "electricity_and_gas",
    },
    "ad": {"id": "444", "name": "Ad 1"},
    "_leadhook": {"attempt": 2},
}


def render(template: str):
    return json.loads(render_payload(template, CONTEXT))


def test_crm_style_template():
    template = """{
      "campaign_id": 43,
      "fields": {
        "first_name": {{ data.full_name | first_word | tojson }},
        "last_name": {{ data.full_name | rest_words | tojson }},
        "mobile": {{ data.phone_number | phone_strip_prefix("+39") | tojson }}
      },
      "extra": {"utm_content": {{ ad.name | tojson }}, "lead_id": {{ id | tojson }}}
    }"""
    assert render(template) == {
        "campaign_id": 43,
        "fields": {"first_name": "Jane", "last_name": "Mary Doe", "mobile": "3331234567"},
        "extra": {"utm_content": "Ad 1", "lead_id": "333"},
    }


def test_phone_strip_prefix_handles_00_form_and_missing_prefix():
    template = '{"a": {{ "%s" | phone_strip_prefix("+39") | tojson }}}'
    assert render(template % "0039 333 1234567") == {"a": "3331234567"}
    assert render(template % "333 1234567") == {"a": "3331234567"}


def test_date_filter_converts_timezone():
    template = '{"d": {{ created_time | date("Europe/Rome", "%d/%m/%Y %H:%M") | tojson }}}'
    assert render(template) == {"d": "15/10/2026 10:30"}


def test_map_value_filter_maps_scalars_and_lists():
    template = """{
      "plan": {{ data.plan | map_value({"electricity_and_gas": "Electricity and gas"}) | tojson }},
      "services": {{ data.services | map_value({"gas": "Gas"}) | tojson }}
    }"""
    assert render(template) == {
        "plan": "Electricity and gas",
        "services": ["electricity", "Gas"],
    }


def test_missing_values_render_as_empty_strings():
    template = '{"a": {{ data.missing | tojson }}, "b": {{ data.missing.deeper | tojson }}}'
    assert render(template) == {"a": "", "b": ""}


def test_default_filter_works_for_missing_values():
    assert render('{"a": {{ data.missing | default("n/a") | tojson }}}') == {"a": "n/a"}


def test_tojson_keeps_unicode_and_escapes_quotes():
    assert json.loads(render_payload('{"a": {{ name | tojson }}}', {"name": 'Città "X"'})) == {
        "a": 'Città "X"'
    }


def test_invalid_json_output_is_rejected():
    with pytest.raises(TemplateError, match="non è un JSON valido"):
        render_payload('{"a": {{ id }}', CONTEXT)


def test_template_syntax_error_is_reported():
    with pytest.raises(TemplateError):
        render_payload('{"a": {{ id }', CONTEXT)


def test_sandbox_blocks_python_internals():
    output = render_payload('{"x": {{ "".__class__.__mro__ | tojson }}}', CONTEXT)
    assert json.loads(output) == {"x": ""}


def test_condition_true_and_false():
    expression = "response.success == true"
    assert evaluate_condition(expression, response={"success": True}, status_code=200)
    assert not evaluate_condition(expression, response={"success": False}, status_code=200)


def test_condition_with_non_json_response_is_false():
    assert not evaluate_condition("response.success == true", response=None, status_code=200)


def test_condition_can_use_status_code():
    assert evaluate_condition("status_code == 201", response=None, status_code=201)


def test_invalid_condition_raises_template_error():
    with pytest.raises(TemplateError):
        evaluate_condition("response.success ==", response={}, status_code=200)
