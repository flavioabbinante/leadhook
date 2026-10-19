import json
import re
from datetime import UTC, datetime

import pytest
import requests
import responses
from responses import matchers

from leadhook.meta.graph import (
    LEAD_FIELDS,
    GraphClient,
    GraphError,
    app_subscriptions,
    client_for_page,
    debug_token,
)
from tests.factories import make_meta_app, make_page
from tests.fixtures import GRAPH_FORM, GRAPH_LEAD

BASE = "https://graph.facebook.com/v21.0"


@responses.activate
def test_get_lead_requests_all_fields_with_token():
    responses.get(
        f"{BASE}/333",
        json=GRAPH_LEAD,
        match=[matchers.query_param_matcher({"fields": LEAD_FIELDS, "access_token": "tok"})],
    )
    assert GraphClient("v21.0", "tok").get_lead("333") == GRAPH_LEAD


@responses.activate
def test_get_form():
    responses.get(f"{BASE}/222", json=GRAPH_FORM)
    assert GraphClient("v21.0", "tok").get_form("222")["name"] == "Energy offer"


@responses.activate
def test_token_error_is_detected():
    responses.get(
        f"{BASE}/333",
        status=400,
        json={
            "error": {"message": "Error validating access token", "code": 190, "error_subcode": 463}
        },
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert excinfo.value.is_token_error
    assert not excinfo.value.is_transient
    assert excinfo.value.subcode == 463


@responses.activate
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {"error": {"message": "Unknown", "code": 1}}),
        (400, {"error": {"message": "Rate limited", "code": 4}}),
        (400, {"error": {"message": "Page rate limit", "code": 32}}),
        (503, "Service unavailable"),
    ],
)
def test_transient_errors(status, body):
    if isinstance(body, dict):
        responses.get(f"{BASE}/333", status=status, json=body)
    else:
        responses.get(f"{BASE}/333", status=status, body=body)
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert excinfo.value.is_transient


@responses.activate
def test_permission_error_is_permanent():
    responses.get(
        f"{BASE}/333", status=400, json={"error": {"message": "No permission", "code": 200}}
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "tok").get_lead("333")
    assert not excinfo.value.is_transient
    assert not excinfo.value.is_token_error
    assert str(excinfo.value) == "No permission"


@responses.activate
def test_network_error_message_does_not_leak_token():
    responses.get(
        f"{BASE}/333",
        body=requests.ConnectionError(f"Failed to reach {BASE}/333?access_token=very-secret-token"),
    )
    with pytest.raises(GraphError) as excinfo:
        GraphClient("v21.0", "very-secret-token").get_lead("333")
    assert excinfo.value.is_transient
    assert "very-secret-token" not in str(excinfo.value)


def test_client_for_page_uses_page_token_and_app_version():
    page = make_page()
    client = client_for_page(page)
    assert client.access_token == "page-token"
    assert client.version == "v21.0"


@responses.activate
def test_http_429_is_transient_without_error_code():
    responses.get(f"{BASE}/333", status=429, body="Too Many Requests")
    with pytest.raises(GraphError) as info:
        GraphClient("v21.0", "tok").get_lead("333")
    assert info.value.is_transient


def test_client_for_page_falls_back_to_system_user_token():
    page = make_page(page_token="")
    assert client_for_page(page).access_token == "system-user-token"


@responses.activate
def test_ids_are_quoted_as_one_path_segment():
    responses.get(re.compile(r"https://graph\.facebook\.com/.*"), json={})
    GraphClient("v21.0", "tok").get_lead("../me")
    assert responses.calls[0].request.url.split("?")[0] == f"{BASE}/..%2Fme"


@responses.activate
def test_get_all_follows_cursors():
    # Same URL registered twice: responses returns them in registration order.
    responses.get(
        f"{BASE}/111/leadgen_forms",
        json={"data": [{"id": "1"}], "paging": {"cursors": {"after": "c1"}, "next": "https://x"}},
    )
    responses.get(
        f"{BASE}/111/leadgen_forms",
        json={"data": [{"id": "2"}], "paging": {"cursors": {"after": "c2"}}},
    )
    forms = GraphClient("v21.0", "tok").list_forms("111")
    assert [f["id"] for f in forms] == ["1", "2"]
    assert "after=c1" in responses.calls[1].request.url
    assert len(responses.calls) == 2


@responses.activate
def test_list_form_leads_filters_by_time_created():
    since = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    filtering = json.dumps(
        [{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}]
    )
    responses.get(
        f"{BASE}/222/leads",
        json={"data": [GRAPH_LEAD]},
        match=[
            matchers.query_param_matcher(
                {"fields": LEAD_FIELDS, "filtering": filtering, "limit": "100"},
                strict_match=False,
            )
        ],
    )
    assert GraphClient("v21.0", "tok").list_form_leads("222", since=since) == [GRAPH_LEAD]


@responses.activate
def test_subscribe_leadgen_posts_subscribed_fields():
    responses.post(
        f"{BASE}/111/subscribed_apps",
        json={"success": True},
        match=[
            matchers.query_param_matcher({"subscribed_fields": "leadgen", "access_token": "tok"})
        ],
    )
    GraphClient("v21.0", "tok").subscribe_leadgen("111")


@responses.activate
def test_subscribed_app_ids():
    responses.get(f"{BASE}/111/subscribed_apps", json={"data": [{"id": "1000"}, {"id": 7}]})
    assert GraphClient("v21.0", "tok").subscribed_app_ids("111") == {"1000", "7"}


@responses.activate
def test_debug_token_uses_app_token():
    app = make_meta_app()
    responses.get(
        f"{BASE}/debug_token",
        json={"data": {"is_valid": True, "expires_at": 0}},
        match=[
            matchers.query_param_matcher(
                {"input_token": "system-user-token", "access_token": "1000|app-secret"}
            )
        ],
    )
    assert debug_token(app, app.access_token) == {"is_valid": True, "expires_at": 0}


@responses.activate
def test_debug_token_error_redacts_both_tokens():
    app = make_meta_app()
    responses.get(
        f"{BASE}/debug_token",
        body=requests.ConnectionError(
            "failed: /debug_token?input_token=system-user-token&access_token=1000%7Capp-secret"
        ),
    )
    with pytest.raises(GraphError) as excinfo:
        debug_token(app, app.access_token)
    message = str(excinfo.value)
    assert "system-user-token" not in message
    assert "app-secret" not in message


@responses.activate
def test_app_subscriptions():
    app = make_meta_app()
    responses.get(f"{BASE}/1000/subscriptions", json={"data": [{"object": "page"}]})
    assert app_subscriptions(app) == [{"object": "page"}]
