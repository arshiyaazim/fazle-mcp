"""Core-Hermes client for verified Owner/Admin business intelligence."""
from __future__ import annotations

import httpx

import business_read_client


def _post(path: str, body: dict) -> dict:
    if not business_read_client.BEARER:
        return {"decision": "DENY", "error": "Admin business intelligence is not configured"}
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/admin-business-intelligence/{path}",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"},
            json=body, timeout=30,
        )
    except httpx.HTTPError:
        return {"decision": "DENY", "error": "Admin business intelligence is unavailable"}
    if response.status_code in (401, 403):
        return {"decision": "DENY", "error": "Verified Admin authorization required"}
    if response.status_code != 200:
        return {"decision": "DENY", "error": f"Admin business intelligence failed (status {response.status_code})"}
    try:
        result = response.json()
    except ValueError:
        return {"decision": "DENY", "error": "Admin business intelligence returned invalid JSON"}
    return result if isinstance(result, dict) else {"decision": "DENY", "error": "Admin business intelligence returned invalid data"}


def query(*, requester_phone: str, requester_channel: str, report_type: str,
          natural_language_request: str, date_from: str | None = None,
          date_to: str | None = None, limit: int = 5000, offset: int = 0) -> dict:
    body = {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "report_type": report_type, "natural_language_request": natural_language_request,
        "limit": limit, "offset": offset,
    }
    if date_from:
        body["date_from"] = date_from
    if date_to:
        body["date_to"] = date_to
    result = _post("query", body)
    if "evidence" not in result or "context_token" not in result:
        return {"decision": "DENY", "error": result.get("error", "Admin business intelligence denied")}
    return result


def gate_reply(payload, *, requester_phone: str, requester_channel: str,
               report_type: str, context_token: str) -> dict:
    result = _post("gate-reply", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "report_type": report_type, "context_token": context_token, "payload": payload,
    })
    if result.get("decision") != "ALLOW" or "payload" not in result or "reply_to" not in result:
        return {"decision": "DENY", "error": result.get("error", "Admin business intelligence disclosure denied")}
    return result
