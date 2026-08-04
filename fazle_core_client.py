"""
HTTP client to fazle-core's OWN FastAPI app (127.0.0.1:8200, host-level,
`require_api_key`-gated) — a NEW integration, separate from the existing
`_get()` in server.py, which instead calls assistant-backend's fazleBridge.js
(127.0.0.1:3001). fazle-core exposes real message-flow/scheduler endpoints
directly (see /api/social/*, /scheduler/*) that assistant-backend has no
equivalent for, so this talks to fazle-core directly rather than adding a
third hop through assistant-backend.

Auth: a dedicated admin-tier API key (FAZLE_CORE_API_KEY), NOT the master
INTERNAL_API_KEY — see the setup notes in the final report for how to mint
one via fazle-core's existing per-admin-key mechanism
(modules.rbac.get_admin_by_api_key). Until a real key is configured, every
call below returns a clear "not configured" structured error rather than
silently failing or falling back to an unsafe default.
"""

import os

import httpx

FAZLE_CORE_API_URL = os.environ.get("FAZLE_CORE_API_URL", "http://127.0.0.1:8200")
FAZLE_CORE_API_KEY = os.environ.get("FAZLE_CORE_API_KEY", "")


def configured():
    return bool(FAZLE_CORE_API_KEY)


def get(path, params=None, timeout=15):
    """GET against fazle-core's own app. Always returns a dict — never
    raises — so a tool built on this can't crash a Hermes turn."""
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.get(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            params=params or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    if resp.status_code in (401, 403):
        return {"error": "fazle-core rejected the API key (unauthorized)"}
    if not resp.is_success:
        return {"error": f"fazle-core error (status {resp.status_code})"}
    try:
        return resp.json()
    except ValueError:
        return {"error": "fazle-core returned a non-JSON response"}


def post(path, json_body=None, timeout=30):
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.post(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            json=json_body or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    if resp.status_code in (401, 403):
        return {"error": "fazle-core rejected the API key (unauthorized)"}
    if resp.status_code == 404:
        return {"error": "fazle-core: unknown job or route"}
    if not resp.is_success:
        return {"error": f"fazle-core error (status {resp.status_code})"}
    try:
        return resp.json()
    except ValueError:
        return {"error": "fazle-core returned a non-JSON response"}
