"""
HTTP client to fazle-core's OWN FastAPI app (127.0.0.1:8200, host-level,
`require_api_key`-gated) — a NEW integration, separate from the existing
`_get()` in server.py, which instead calls assistant-backend's fazleBridge.js
(127.0.0.1:3001). fazle-core exposes real message-flow/scheduler endpoints
directly (see /api/social/*, /scheduler/*) that assistant-backend has no
equivalent for, so this talks to fazle-core directly rather than adding a
third hop through assistant-backend.

Auth: a dedicated API key (FAZLE_CORE_API_KEY), NOT the master
INTERNAL_API_KEY — minted via fazle-core's existing per-admin-key mechanism
(modules.rbac.get_admin_by_api_key). Until a real key is configured, every
call below returns a clear "not configured" structured error rather than
silently failing or falling back to an unsafe default.

RBAC tier: per fazle-core's own "RBAC is data, not code" principle (see
core/.claude/CLAUDE.md), this account's tier is NOT hardcoded here — it's
whatever's currently granted in fazle_admin_roles for this identity
(hermes-mcp-service, scripts/create_hermes_mcp_admin.py). Some routes
require more than a generic "admin-tier" key: send_whatsapp_tools.py's
send_whatsapp_message specifically needs superadmin. If a call unexpectedly
403s, check the live grant (max_role_level() in modules/rbac) rather than
assuming this docstring's description of the tier — it's a snapshot, not a
guarantee. (Last confirmed superadmin 2026-08-10, owner-manual grant.)
"""

import os

import httpx

FAZLE_CORE_API_URL = os.environ.get("FAZLE_CORE_API_URL", "http://127.0.0.1:8200")
FAZLE_CORE_API_KEY = os.environ.get("FAZLE_CORE_API_KEY", "")


def configured():
    return bool(FAZLE_CORE_API_KEY)


def _handle_response(resp):
    """Shared response handling for every verb below -- one error-shape
    contract, one place to change it."""
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
    return _handle_response(resp)


def post(path, json_body=None, timeout=30, params=None):
    """POST against fazle-core's own app. params (added 2026-08-19, Task 4)
    is for routes whose FastAPI signature takes query params instead of
    (or alongside) a JSON body -- e.g. modules/fazle_payroll_engine's
    operator approve/reject and employee edit-request approve/reject
    routes, which are query-param-only with no request body model.
    Optional and additive -- every existing caller passing only
    (path, json_body) is unaffected."""
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.post(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            json=json_body or {},
            params=params or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    return _handle_response(resp)


def put(path, json_body=None, timeout=30, params=None):
    """PUT against fazle-core's own app (added 2026-08-19, Task 4, for
    routes like PUT /admin/attendance/{id} that use PUT rather than POST).
    Same error-shape contract as get()/post() -- always returns a dict,
    never raises."""
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.put(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            json=json_body or {},
            params=params or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    return _handle_response(resp)


def patch(path, json_body=None, timeout=30, params=None):
    """PATCH against fazle-core's own app (added 2026-08-19, Task 4, for
    routes like PATCH /api/fpe/employees/{id} / PATCH /api/escort-roster/{id}
    that use PATCH rather than PUT/POST). Same error-shape contract as
    get()/post()/put()."""
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.patch(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            json=json_body or {},
            params=params or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    return _handle_response(resp)


def delete(path, params=None, timeout=30):
    """DELETE against fazle-core's own app (added 2026-08-19, Task 4, for
    routes like DELETE /admin/attendance/{id} / DELETE /api/escort-roster/{id}).
    Same error-shape contract as get()/post()/put()."""
    if not FAZLE_CORE_API_KEY:
        return {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
    try:
        resp = httpx.delete(
            f"{FAZLE_CORE_API_URL}{path}",
            headers={"X-Internal-Key": FAZLE_CORE_API_KEY},
            params=params or {},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "fazle-core request timed out"}
    except httpx.RequestError as e:
        return {"error": f"fazle-core unreachable: {e}"}
    return _handle_response(resp)
