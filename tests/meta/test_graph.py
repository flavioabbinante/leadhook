import pytest
import requests
import responses
from responses import matchers

from leadhook.meta.graph import LEAD_FIELDS, GraphClient, GraphError, client_for_page
from tests.factories import make_page
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
