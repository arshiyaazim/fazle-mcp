"""
HTTP client to assistant-backend's fazleBridge.js (127.0.0.1:3001/api/fazle/*)
— extracted from server.py's original inline `_get()`/`_get_token()` (Phase 2,
2026-08-04) so domain_reports.py can reuse the exact same authenticated
client instead of a second, duplicated login/session dance. No behavior
change from the original inline version; server.py's own tool wrappers were
repointed at this module rather than left with a second copy.

Auth: logs in as the dedicated, minimal-privilege service account
(hermes-mcp-svc, requireApproved only — not admin) via the same
POST /api/auth/login every real user uses.
"""

import os
import time

import httpx

BASE_URL = os.environ.get("ASSISTANT_API_URL", "http://127.0.0.1:3001/api")
SVC_USERNAME = os.environ.get("FAZLE_MCP_USERNAME", "")
SVC_PASSWORD = os.environ.get("FAZLE_MCP_PASSWORD", "")

_token = None
_token_expires_at = 0


def _get_token():
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at:
        return _token
    resp = httpx.post(f"{BASE_URL}/auth/login", json={"username": SVC_USERNAME, "password": SVC_PASSWORD}, timeout=10)
    resp.raise_for_status()
    _token = resp.json()["token"]
    _token_expires_at = time.time() + 15 * 60
    return _token


def get(path, params=None):
    """GET against assistant-backend's /api/fazle/* proxy. Always returns a
    dict/list from JSON, or a structured {"error": ...} dict — never raises
    for a reachability/auth failure, matching fazle_core_client.get()'s
    contract so callers can treat both the same way."""
    token = _get_token()
    try:
        resp = httpx.get(f"{BASE_URL}/fazle{path}", headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=15)
    except httpx.RequestError as e:
        return {"error": f"fazle bridge unreachable: {e}"}
    if resp.status_code == 401:
        global _token
        _token = None
        token = _get_token()
        resp = httpx.get(f"{BASE_URL}/fazle{path}", headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=15)
    if not resp.is_success:
        return {"error": f"fazle bridge error (status {resp.status_code})"}
    return resp.json()
