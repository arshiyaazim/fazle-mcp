"""
Hermes -> OpenCode handoff (Phase 4, 2026-08-10 — see the approved plan
and project_hermes_unification_20260810.md in memory). Lets Hermes hand a
diagnosed problem to OpenCode (a separate, already-running, sandboxed
coding agent — opencode-serve.service) to implement, once the user has
explicitly authorized that specific step. Hermes never writes code
directly through this tool; it only dispatches a prompt and reads back
what OpenCode did. Review afterward uses audit_tools.py's EXISTING
audit_git_status / audit_recent_commits, not a new tool.

Reuses OpenCode's existing REST API exactly as assistant-backend's
opencode.js already proxies it (create session -> send prompt -> poll for
completion, opencode.js does the polling server-side) — nothing about
OpenCode itself changes here.

Auth: opencode.js's routes are requireAdmin-gated, and the fazle-mcp
service account (hermes-mcp-svc, used by every other tool in this
package) is deliberately non-admin. Rather than widen that account's role
wholesale, this uses a separate, narrowly-scoped shared secret
(HERMES_OPENCODE_SVC_TOKEN) that requireAdminOrHermesSvc
(assistant-platform/backend/src/middleware/auth.js) accepts ONLY on the
opencode.js router — every other admin-gated route is unaffected and
still requires a real admin JWT.

Mode gate: identical fail-closed _read_mode() logic to scheduler_tools.py
(deliberately duplicated, not shared — see that module's own docstring
for why: hermes-runner and this fazle-mcp subprocess are independent
processes). opencode_dispatch requires RUN mode AND confirm=True,
mirroring run_scheduled_task's existing confirm-before-destructive gate
exactly — this does not invent a second confirmation mechanism.
opencode_check is read-only and ungated.

Hard gate (2026-08-19, Owner-directed follow-on to the BUILD/RUN/business-
action capability audit): opencode_dispatch is the highest-blast-radius
tool in the whole surface (opencode-serve.service's WorkingDirectory is
the entire VPS home directory, not one repo) — it now ALSO requires a
real approved_action_id whose task_tools.get_action() row has
status='approved' and action_type='opencode_dispatch', checked before
even the RUN-mode/confirm check runs. This is the one action type in the
new propose->approve->execute flow (modules.hermes_tasks) that gets a
code-enforced gate rather than a purely procedural (system-prompt-only)
one, precisely because this tool's own reach is the largest.
"""

import json
import os

import httpx

import task_tools

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))

ASSISTANT_API_URL = os.environ.get("ASSISTANT_API_URL", "http://127.0.0.1:3001/api")
SVC_TOKEN = os.environ.get("HERMES_OPENCODE_SVC_TOKEN", "")

# opencode.js's own POST /session/:id/prompt already blocks server-side for
# up to 150s waiting on OpenCode's reply (see that file's comment on why
# 90s measured too tight) — give it a little headroom on this side too.
_PROMPT_TIMEOUT_S = 155


def _read_mode():
    """Fail-closed mode read — identical shape to scheduler_tools.py's own
    copy (see that module's docstring for why this is deliberately
    duplicated rather than shared). Any problem (missing file, unreadable,
    garbage, or an expired TTL) lands on READ, never BUILD/RUN by default."""
    try:
        with open(MODE_FILE, "r") as f:
            raw = f.read().strip()
    except OSError:
        return "READ"
    if not raw:
        return "READ"
    try:
        data = json.loads(raw)
        mode = str(data.get("mode", "")).upper()
        expires_at = data.get("expires_at")
        if expires_at:
            import datetime

            try:
                exp = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.datetime.now(datetime.timezone.utc) >= exp:
                    return "READ"
            except (ValueError, AttributeError):
                return "READ"
        return mode if mode in MODES else "READ"
    except (json.JSONDecodeError, AttributeError, TypeError):
        mode = raw.upper()
        return mode if mode in MODES else "READ"


def _request(method, path, json_body=None, timeout=15):
    """Shared request helper for both verbs — always returns a dict, never
    raises, matching this package's existing client contract
    (fazle_core_client.get/post, assistant_bridge_client.get)."""
    if not SVC_TOKEN:
        return {"error": "opencode handoff not configured (HERMES_OPENCODE_SVC_TOKEN unset)"}
    try:
        resp = httpx.request(
            method,
            f"{ASSISTANT_API_URL}/opencode{path}",
            headers={"Authorization": f"Bearer {SVC_TOKEN}"},
            json=json_body,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {"error": "opencode request timed out"}
    except httpx.RequestError as e:
        return {"error": f"opencode unreachable: {e}"}
    if resp.status_code in (401, 403):
        return {"error": "opencode rejected the handoff token (unauthorized)"}
    if not resp.is_success:
        return {"error": f"opencode error (status {resp.status_code})"}
    try:
        return resp.json()
    except ValueError:
        return {"error": "opencode returned a non-JSON response"}


def opencode_dispatch(prompt: str, approved_action_id: int, confirm: bool = False) -> dict:
    """Hand a diagnosed task to OpenCode (a separate, sandboxed coding
    agent, full-VPS-scope) to implement. Creates a new OpenCode session and
    sends `prompt` as its task; OpenCode's own endpoint waits for the
    reply. Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='opencode_dispatch' — call propose_action(action_type=
    "opencode_dispatch", ...) first, then wait for the admin's explicit
    "APPROVE ACTION <id>" before ever calling this; (2) RUN mode; (3)
    confirm=True after the user has explicitly authorized this specific
    step in conversation (identical pattern to run_scheduled_task's
    confirm-before-destructive gate). After this returns, pull the real
    diff/commit via audit_git_status / audit_recent_commits and call
    record_execution_result(approved_action_id, ...) — that's where the
    verified evidence gets captured, not this tool's own return value."""
    if not prompt or not prompt.strip():
        return {"status": "error", "error": "prompt is required"}

    if not approved_action_id:
        return {"status": "denied", "error": "approved_action_id is required — propose_action() then get an explicit APPROVE ACTION <id> first."}
    action = task_tools.get_action(approved_action_id)
    if "error" in action:
        return {"status": "denied", "error": f"could not verify approved_action_id={approved_action_id}: {action['error']}"}
    if action.get("status") != "approved":
        return {
            "status": "denied",
            "error": f"action #{approved_action_id} is not approved (status={action.get('status')}) — "
                     "it must be explicitly approved by the admin before opencode_dispatch can run.",
        }
    if action.get("action_type") != "opencode_dispatch":
        return {
            "status": "denied",
            "error": f"action #{approved_action_id} has action_type={action.get('action_type')!r}, "
                     "not 'opencode_dispatch' — propose it with the correct action_type.",
        }

    mode = _read_mode()
    if mode != "RUN":
        return {
            "status": "denied",
            "mode_at_execution": mode,
            "error": "opencode_dispatch requires RUN mode — switch modes first.",
        }
    if not confirm:
        return {
            "status": "denied",
            "mode_at_execution": mode,
            "error": "opencode_dispatch requires explicit confirmation (confirm=true) after asking the user.",
        }

    session = _request("POST", "/session")
    if "error" in session:
        return {"status": "error", "mode_at_execution": mode, "error": session["error"]}
    session_id = session.get("id")
    if not session_id:
        return {
            "status": "error",
            "mode_at_execution": mode,
            "error": "opencode did not return a session id",
        }

    result = _request(
        "POST", f"/session/{session_id}/prompt", {"text": prompt}, timeout=_PROMPT_TIMEOUT_S
    )
    if "error" in result:
        return {
            "status": "error",
            "mode_at_execution": mode,
            "session_id": session_id,
            "error": result["error"],
        }
    return {
        "status": "success",
        "mode_at_execution": mode,
        "confirmed": confirm,
        "approved_action_id": approved_action_id,
        "session_id": session_id,
        "reply": result.get("reply", ""),
        "model": result.get("model", ""),
    }


def opencode_check(session_id: str) -> dict:
    """Read-only: fetch an OpenCode session's message history. Use this to
    follow up on a session from opencode_dispatch (e.g. it timed out
    server-side) or review a past result. No mode/confirm gate — this
    never changes anything."""
    if not session_id or not session_id.strip():
        return {"status": "error", "error": "session_id is required"}
    result = _request("GET", f"/session/{session_id}/messages")
    if isinstance(result, dict) and "error" in result:
        return {"status": "error", "session_id": session_id, "error": result["error"]}
    return {"status": "success", "session_id": session_id, "messages": result}
