"""Anonymized Meta payloads shared by tests."""

import hashlib
import hmac

GRAPH_LEAD = {
    "id": "333",
    "created_time": "2026-10-15T08:30:00+0000",
    "field_data": [
        {"name": "full_name", "values": ["Jane Doe"]},
        {"name": "phone_number", "values": ["+15551234567"]},
        {"name": "services", "values": ["electricity", "gas"]},
    ],
    "ad_id": "444",
    "ad_name": "Ad 1",
    "adset_id": "555",
    "adset_name": "Ad set 1",
    "campaign_id": "666",
    "campaign_name": "Campaign 1",
    "form_id": "222",
    "is_organic": False,
}

GRAPH_FORM = {
    "id": "222",
    "name": "Energy offer",
    "locale": "en_US",
    "status": "ACTIVE",
    "questions": [{"key": "full_name", "label": "Full name", "type": "FULL_NAME"}],
}


def leadgen_payload(leadgen_id: str = "333", page_id: str = "111", form_id: str = "222") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1760517000,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": leadgen_id,
                            "page_id": page_id,
                            "form_id": form_id,
                            "created_time": 1760517000,
                        },
                    }
                ],
            }
        ],
    }


def sign(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
