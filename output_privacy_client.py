"""Core-Hermes client for the canonical final-output privacy gate."""
from __future__ import annotations
import httpx
import business_read_client


def gate(**body):
    if not business_read_client.BEARER:
        return {"decision": "DENY", "error": "Output privacy gate is not configured"}
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/output-privacy/gate",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"}, json=body, timeout=30,
        )
    except httpx.HTTPError:
        return {"decision": "DENY", "error": "Output privacy gate is unavailable"}
    if response.status_code != 200:
        return {"decision": "DENY", "error": "Output privacy gate denied disclosure"}
    try:
        result = response.json()
    except ValueError:
        return {"decision": "DENY", "error": "Output privacy gate returned invalid JSON"}
    if not isinstance(result, dict) or result.get("decision") != "ALLOW" or "payload" not in result or "reply_to" not in result:
        return {"decision": "DENY", "error": "Output privacy gate denied disclosure"}
    return result
