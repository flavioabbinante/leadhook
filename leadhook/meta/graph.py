"""Minimal Meta Graph API client."""

import json
from datetime import datetime
from urllib.parse import quote, urlencode

import requests

from .models import MetaApp, Page

GRAPH_BASE_URL = "https://graph.facebook.com"
DIALOG_BASE_URL = "https://www.facebook.com"
# Used when the app has no Facebook Login for Business configuration.
LOGIN_SCOPES = (
    "leads_retrieval,pages_manage_metadata,pages_show_list,pages_read_engagement,"
    "pages_manage_ads,business_management"
)
LEAD_FIELDS = (
    "id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,"
    "campaign_id,campaign_name,form_id,is_organic"
)
FORM_FIELDS = "id,name,locale,status,questions{key,label}"

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


def node(id_: str) -> str:
    """A Graph node ID as a single safe path segment (no traversal to other endpoints)."""
    return quote(str(id_), safe="")


class GraphClient:
    def __init__(
        self,
        version: str,
        access_token: str,
        timeout: float = 15,
        redact: tuple[str, ...] = (),
    ) -> None:
        self.version = version
        self.access_token = access_token
        self.timeout = timeout
        secrets = {s for s in (access_token, *redact) if s}
        # requests URL-encodes query values, so error messages may carry the encoded form.
        self._secrets = sorted(
            secrets | {quote(s, safe="") for s in secrets}, key=len, reverse=True
        )

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_BASE_URL}/{self.version}/{path.lstrip('/')}"
        query = {**(params or {}), "access_token": self.access_token}
        try:
            response = requests.request(method, url, params=query, timeout=self.timeout)
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

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    def post(self, path: str, params: dict | None = None) -> dict:
        return self.request("POST", path, params)

    def get_all(self, path: str, params: dict | None = None, *, max_pages: int = 50) -> list[dict]:
        """Every item of `data`, following cursor pagination.

        shortcut: stops after max_pages pages (5000 items at limit=100); raise it if a page
        ever has more forms or a 15-minute reconciliation window holds more leads.
        """
        params = dict(params or {})
        items: list[dict] = []
        for _ in range(max_pages):
            body = self.get(path, params)
            items.extend(item for item in body.get("data") or [] if isinstance(item, dict))
            paging = body.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                break
            params["after"] = after
        return items

    def get_lead(self, leadgen_id: str) -> dict:
        return self.get(node(leadgen_id), {"fields": LEAD_FIELDS})

    def get_form(self, form_id: str) -> dict:
        return self.get(node(form_id), {"fields": FORM_FIELDS})

    def list_forms(self, page_id: str) -> list[dict]:
        return self.get_all(f"{node(page_id)}/leadgen_forms", {"fields": FORM_FIELDS, "limit": 100})

    def list_form_leads(self, form_id: str, *, since: datetime) -> list[dict]:
        filtering = json.dumps(
            [{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}]
        )
        return self.get_all(
            f"{node(form_id)}/leads", {"fields": LEAD_FIELDS, "filtering": filtering, "limit": 100}
        )

    def list_pages(self) -> list[dict]:
        """Pages reachable with this (System User) token, each with its page access token."""
        return self.get_all("me/accounts", {"fields": "id,name,access_token", "limit": 100})

    def subscribed_app_ids(self, page_id: str) -> set[str]:
        return {str(app.get("id")) for app in self.get_all(f"{node(page_id)}/subscribed_apps")}

    def subscribe_leadgen(self, page_id: str) -> None:
        self.post(f"{node(page_id)}/subscribed_apps", {"subscribed_fields": "leadgen"})

    def create_test_lead(self, form_id: str) -> dict:
        return self.post(f"{node(form_id)}/test_leads")

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def client_for_page(page: Page) -> GraphClient:
    """Use the page's own token, or the Meta app's System User token when it has none."""
    token = page.page_token or page.meta_app.access_token
    return GraphClient(page.meta_app.graph_version, token)


def app_client(meta_app: MetaApp, *, redact: tuple[str, ...] = ()) -> GraphClient:
    """Client authenticated with the app access token (app_id|app_secret)."""
    return GraphClient(
        meta_app.graph_version,
        f"{meta_app.app_id}|{meta_app.app_secret}",
        redact=(meta_app.app_secret, *redact),
    )


def debug_token(meta_app: MetaApp, token: str) -> dict:
    response = app_client(meta_app, redact=(token,)).get("debug_token", {"input_token": token})
    return response.get("data") or {}


def app_subscriptions(meta_app: MetaApp) -> list[dict]:
    return app_client(meta_app).get_all(f"{node(meta_app.app_id)}/subscriptions")


def login_dialog_url(meta_app: MetaApp, state: str) -> str:
    params = {
        "client_id": meta_app.app_id,
        "redirect_uri": meta_app.login_redirect_url(),
        "state": state,
        "response_type": "code",
    }
    if meta_app.login_config_id:
        params["config_id"] = meta_app.login_config_id
    else:
        params["scope"] = LOGIN_SCOPES
    return f"{DIALOG_BASE_URL}/{meta_app.graph_version}/dialog/oauth?{urlencode(params)}"


def exchange_login_code(meta_app: MetaApp, code: str) -> dict:
    return app_client(meta_app, redact=(code,)).get(
        "oauth/access_token",
        {
            "client_id": meta_app.app_id,
            "client_secret": meta_app.app_secret,
            "redirect_uri": meta_app.login_redirect_url(),
            "code": code,
        },
    )


def exchange_long_lived_token(meta_app: MetaApp, token: str) -> dict:
    """Turn a one-hour user token into a ~60-day one (its page tokens then never expire)."""
    return app_client(meta_app, redact=(token,)).get(
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": meta_app.app_id,
            "client_secret": meta_app.app_secret,
            "fb_exchange_token": token,
        },
    )


def subscribe_app_webhook(meta_app: MetaApp) -> None:
    """Point the app's Page webhook at LeadHook; Meta checks the callback URL right away."""
    app_client(meta_app).post(
        f"{node(meta_app.app_id)}/subscriptions",
        {
            "object": "page",
            "callback_url": meta_app.webhook_url(),
            "fields": "leadgen",
            "verify_token": meta_app.verify_token,
            "include_values": "true",
        },
    )
