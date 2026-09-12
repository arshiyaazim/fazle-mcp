"""Core-Hermes client for relevant approved public/recruitment/service KB."""
from __future__ import annotations

import httpx

import business_read_client


def _post(path: str, body: dict) -> dict:
    if not business_read_client.BEARER:
        return {"decision": "DENY", "error": "Public knowledge access is not configured"}
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/public-knowledge/{path}",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"},
            json=body, timeout=30,
        )
    except httpx.HTTPError:
        return {"decision": "DENY", "error": "Public knowledge access is unavailable"}
    if response.status_code in (401, 403):
        return {"decision": "DENY", "error": "Requested information is not approved for this requester"}
    if response.status_code != 200:
        return {"decision": "DENY", "error": f"Public knowledge access failed (status {response.status_code})"}
    try:
        result = response.json()
    except ValueError:
        return {"decision": "DENY", "error": "Public knowledge access returned invalid JSON"}
    return result if isinstance(result, dict) else {"decision": "DENY", "error": "Public knowledge access returned invalid data"}


def query(*, requester_phone: str, requester_channel: str, topic: str, question: str) -> dict:
    result = _post("query", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "topic": topic, "question": question,
    })
    if "approved_context" not in result or "context_token" not in result:
        return {"decision": "DENY", "error": result.get("error", "Public knowledge request denied")}
    return result


def gate_reply(payload, *, requester_phone: str, requester_channel: str,
               topic: str, context_token: str) -> dict:
    result = _post("gate-reply", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "topic": topic, "context_token": context_token, "payload": payload,
    })
    if result.get("decision") != "ALLOW" or "payload" not in result or "reply_to" not in result:
        return {"decision": "DENY", "error": result.get("error", "Public knowledge disclosure denied")}
    return result
