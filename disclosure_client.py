"""Core-Hermes client for Core's canonical human-disclosure gate."""
from __future__ import annotations

import httpx

import business_read_client


def gate(payload, *, requester_phone: str, information_type: str, subject_employee_id: int | None = None) -> dict:
    if not business_read_client.BEARER:
        return {"decision": "DENY", "error": "Human disclosure gate is not configured"}
    body = {
        "requester_phone": requester_phone,
        "information_type": information_type,
        "subject_employee_id": subject_employee_id,
        "payload": payload,
    }
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/disclosure/gate",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"},
            json=body, timeout=30,
        )
    except httpx.RequestError:
        return {"decision": "DENY", "error": "Human disclosure gate is unavailable"}
    if response.status_code in (401, 403):
        return {"decision": "DENY", "error": "Human disclosure denied"}
    if not response.is_success:
        return {"decision": "DENY", "error": f"Human disclosure gate failed (status {response.status_code})"}
    try:
        result = response.json()
    except ValueError:
        return {"decision": "DENY", "error": "Human disclosure gate returned invalid JSON"}
    if not isinstance(result, dict) or result.get("decision") != "ALLOW" or "payload" not in result:
        return {"decision": "DENY", "error": "Human disclosure denied"}
    return result
