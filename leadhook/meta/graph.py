"""Minimal Meta Graph API client."""

import requests

from .models import Page

GRAPH_BASE_URL = "https://graph.facebook.com"
LEAD_FIELDS = (
    "id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,"
    "campaign_id,campaign_name,form_id,is_organic"
)
FORM_FIELDS = "id,name,locale,status"

# https://developers.facebook.com/docs/graph-api/guides/error-handling
TOKEN_ERROR_CODES = {190}
TRANSIENT_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613}


class GraphError(Exception):
    def __init__(
        self, message: str, *, status: int | None, code: int | None, subcode: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode

    @property
    def is_token_error(self) -> bool:
        return self.code in TOKEN_ERROR_CODES

    @property
    def is_transient(self) -> bool:
        if self.is_token_error:
            return False
        return (
            self.status is None
            or self.status >= 500
            or self.status == 429
            or self.code in TRANSIENT_ERROR_CODES
        )


class GraphClient:
    def __init__(self, version: str, access_token: str, timeout: float = 15) -> None:
        self.version = version
        self.access_token = access_token
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_BASE_URL}/{self.version}/{path.lstrip('/')}"
        query = {**(params or {}), "access_token": self.access_token}
        try:
            response = requests.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            message = self._redact(f"{type(exc).__name__}: {exc}")
            raise GraphError(message, status=None, code=None) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if response.status_code >= 400 or error:
            error = error if isinstance(error, dict) else {}
            raise GraphError(
                self._redact(error.get("message") or f"HTTP {response.status_code}"),
                status=response.status_code,
                code=error.get("code"),
                subcode=error.get("error_subcode"),
            )
        return body if isinstance(body, dict) else {}

    def get_lead(self, leadgen_id: str) -> dict:
        return self.get(leadgen_id, {"fields": LEAD_FIELDS})

    def get_form(self, form_id: str) -> dict:
        return self.get(form_id, {"fields": FORM_FIELDS})

    def _redact(self, text: str) -> str:
        if self.access_token:
            text = text.replace(self.access_token, "***")
        return text


def client_for_page(page: Page) -> GraphClient:
    """Use the page's own token, or the Meta app's System User token when it has none."""
    token = page.page_token or page.meta_app.access_token
    return GraphClient(page.meta_app.graph_version, token)
