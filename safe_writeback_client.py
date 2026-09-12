"""Core-Hermes client for approval-gated Core writeback."""
from __future__ import annotations
import httpx
import business_read_client


def _post(path, body):
    if not business_read_client.BEARER:
        return {"ok": False, "error": "Safe Core writeback is not configured"}
    try:
        response = httpx.post(
            f"{business_read_client.CORE_URL}/api/assistant/ops/safe-writeback/{path}",
            headers={"Authorization": f"Bearer {business_read_client.BEARER}"}, json=body, timeout=30,
        )
    except httpx.HTTPError:
        return {"ok": False, "error": "Safe Core writeback is unavailable"}
    if response.status_code != 200:
        return {"ok": False, "error": "Safe Core writeback denied or failed", "status_code": response.status_code}
    try:
        result = response.json()
    except ValueError:
        return {"ok": False, "error": "Safe Core writeback returned invalid JSON"}
    return result if isinstance(result, dict) else {"ok": False, "error": "Safe Core writeback returned invalid data"}


def propose(**body):
    return _post("propose", body)


def confirm(*, requester_phone, requester_channel, action_id, confirmation):
    return _post("confirm", {
        "requester_phone": requester_phone, "requester_channel": requester_channel,
        "action_id": action_id, "confirmation": confirmation,
    })
