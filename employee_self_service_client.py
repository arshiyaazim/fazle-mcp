"""Core-Hermes client for Core's verified employee self-service flow."""
from __future__ import annotations

import httpx

import business_read_client


def _post(path: str, body: dict) -> dict:
    if not business_read_client.BEARER:
        return {"decision": "DENY", "error": "Employee self-service is not configured"}
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/employee-self-service/{path}",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"},
            json=body, timeout=30,
        )
    except httpx.RequestError:
        return {"decision": "DENY", "error": "Employee self-service is unavailable"}
    if response.status_code in (401, 403, 422):
        return {"decision": "DENY", "error": "Employee self-service denied"}
    if not response.is_success:
        return {"decision": "DENY", "error": f"Employee self-service failed (status {response.status_code})"}
    try:
        result = response.json()
    except ValueError:
        return {"decision": "DENY", "error": "Employee self-service returned invalid JSON"}
    if not isinstance(result, dict):
        return {"decision": "DENY", "error": "Employee self-service returned invalid data"}
    return result


def query(*, requester_phone: str, requester_channel: str, topic: str,
          date_from: str | None = None, date_to: str | None = None, limit: int = 20) -> dict:
    return _post("query", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "topic": topic, "date_from": date_from, "date_to": date_to, "limit": limit,
    })


def gate_reply(payload, *, requester_phone: str, requester_channel: str, topic: str,
               context_token: str) -> dict:
    result = _post("gate-reply", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "topic": topic, "context_token": context_token, "payload": payload,
    })
    if result.get("decision") != "ALLOW" or "payload" not in result or "reply_to" not in result:
        return {"decision": "DENY", "error": "Employee self-service disclosure denied"}
    return result
