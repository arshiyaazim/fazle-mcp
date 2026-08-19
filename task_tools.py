"""
Persistent Task/Execution State + diff-level Action Approval tools for
Earth (2026-08-19, Owner-directed follow-on to the P0-P2 reliability-
hardening pass and the BUILD/RUN/business-action capability audit).

Thin pass-through over fazle-core's new /api/tasks/* and /api/actions/*
routes (modules/hermes_tasks/routes.py) — no new business logic here.

Tiering (HERMES_AUTONOMY_POLICY_2026-08-19.md's existing A/B/C/D scheme,
appended not renumbered):
  Tier A (read, no gate): get_tasks, get_task, get_pending_actions.
  Tier B (autonomous draft/preparation, no real mutation): create_task,
    claim_task, update_task_status, propose_action, record_execution_result
    — Earth's own planning bookkeeping. Never money, never a send, never
    itself an approved action being executed.
  Tier C (explicit-instruction write, RUN+confirm+superadmin): approve_action,
    reject_action — the one command that actually authorizes a proposed
    file mutation/git commit/deploy/migration/restart/production write/
    opencode_dispatch call to proceed. Same gate shape as
    send_whatsapp_message/payment_draft_tools.py's mutating tools. The real
    protection is procedural: Earth must only call this after the admin has
    said "APPROVE ACTION <id>" / "REJECT ACTION <id> <reason>" naming the
    exact id in the actual conversation — same "explicit instruction naming
    the exact entity" discipline already documented for every existing
    Tier C/D tool, not a new invention.
"""

import json
import os

import fazle_core_client as core
from arg_coerce import as_str

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))


def _read_mode():
    """Fail-closed mode read — identical shape to payment_draft_tools.py's
    own copy (deliberately duplicated, not shared)."""
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


def _gate(action: str, confirm: bool):
    mode = _read_mode()
    if mode != "RUN":
        return {"ok": False, "mode_at_execution": mode, "error": f"{action} requires RUN mode — switch modes first."}
    if not confirm:
        return {
            "ok": False, "mode_at_execution": mode,
            "error": f"{action} requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific change.",
        }
    return None


# ── Tasks ────────────────────────────────────────────────────────────────────

def get_tasks(status: str = None, owner: str = None, parent_task_id: int = None, limit: int = 50) -> dict:
    """Read-only: list persistent tasks, optionally filtered by status/
    owner/parent_task_id. At the start of a new conversation, check
    get_tasks(owner="earth", status="IN_PROGRESS") (and WAITING_APPROVAL/
    VERIFYING) for unfinished work before assuming a fresh start -- tasks
    survive a conversation ending."""
    params = {"limit": limit}
    if status:
        params["status"] = status
    if owner:
        params["owner"] = owner
    if parent_task_id is not None:
        params["parent_task_id"] = parent_task_id
    return core.get("/api/tasks", params)


def get_task(task_id: int) -> dict:
    """Read-only: one task's full row plus its complete event history."""
    return core.get(f"/api/tasks/{task_id}")


def create_task(
    title: str, description: str = None, parent_task_id: int = None,
    priority: str = "normal", execution_mode: str = None, created_from: str = None,
    trace_id: str = None, depends_on: list = None,
) -> dict:
    """Create a persistent task (BACKLOG by default). Use parent_task_id
    to build a goal -> task -> subtask hierarchy. depends_on is a plain
    list of task ids Earth should check are all DONE before claiming this
    one -- not enforced server-side, a planning aid only."""
    body = {"title": title, "priority": priority}
    if description:
        body["description"] = description
    if parent_task_id is not None:
        body["parent_task_id"] = parent_task_id
    if execution_mode:
        body["execution_mode"] = execution_mode
    if created_from:
        body["created_from"] = created_from
    if trace_id:
        body["trace_id"] = as_str(trace_id)
    if depends_on:
        body["depends_on"] = depends_on
    return core.post("/api/tasks", body)


def claim_task(task_id: int, owner: str = "earth") -> dict:
    """Claim a READY/BACKLOG task, moving it to IN_PROGRESS. No RUN/confirm
    gate -- this is Earth's own planning bookkeeping, not a business
    mutation."""
    return core.post(f"/api/tasks/{task_id}/claim", {"owner": owner})


def update_task_status(
    task_id: int, status: str, next_action: str = None,
    failure_reason: str = None, verification: dict = None,
) -> dict:
    """Move a task to a new status. Only specific transitions are legal
    (BACKLOG->READY->IN_PROGRESS->WAITING_APPROVAL->VERIFYING->DONE, plus
    ->FAILED/BLOCKED from most non-terminal states, FAILED->READY/BACKLOG
    for a retry) -- an illegal transition is rejected with an explanation
    of what's actually allowed from the current state. next_action is the
    session-recovery hint a later Earth session reads. verification should
    carry real evidence (e.g. a re-fetched read-back), not a bare claim."""
    body = {"status": status}
    if next_action:
        body["next_action"] = next_action
    if failure_reason:
        body["failure_reason"] = failure_reason
    if verification:
        body["verification"] = verification
    return core.patch(f"/api/tasks/{task_id}/status", body)


# ── Action approvals ─────────────────────────────────────────────────────────

def get_pending_actions(task_id: int = None) -> dict:
    """Read-only: list pending action-approval requests, optionally scoped
    to one task."""
    params = {"task_id": task_id} if task_id is not None else None
    return core.get("/api/actions", params)


def get_action(action_id: int) -> dict:
    """Read-only: one action-approval request's full row (status,
    action_type, diff, risk, reviewed_by, execution_result, ...)."""
    return core.get(f"/api/actions/{action_id}")


def propose_action(
    action_type: str, summary: str, task_id: int = None, files_changed: list = None,
    diff: str = None, command: str = None, risk: str = "medium",
    expected_effect: str = None, rollback_plan: str = None,
) -> dict:
    """Propose a file mutation / git commit / deploy / migration / restart /
    production write / opencode_dispatch call BEFORE doing it -- required
    for every one of those action types, even for Earth's own direct
    BUILD/RUN-mode file edits (nothing code-level stops you from skipping
    this, but it is a named, explicit requirement, not optional). diff/
    files_changed should describe the real intended change as precisely as
    you can -- this is what the admin reviews before saying
    'APPROVE ACTION <id>'. No RUN/confirm gate on proposing itself; the
    gate is on approve_action, a separate step only the admin's explicit
    instruction triggers."""
    body = {"action_type": action_type, "summary": summary, "risk": risk}
    if task_id is not None:
        body["task_id"] = task_id
    if files_changed:
        body["files_changed"] = files_changed
    if diff:
        body["diff"] = diff
    if command:
        body["command"] = command
    if expected_effect:
        body["expected_effect"] = expected_effect
    if rollback_plan:
        body["rollback_plan"] = rollback_plan
    return core.post("/api/actions", body)


def approve_action(action_id: int, confirm: bool = False) -> dict:
    """Tier C. Approve a pending action-approval request, authorizing it
    to actually be executed (this call does NOT execute anything itself --
    only flips the request to 'approved'). Call this ONLY after the admin
    has said 'APPROVE ACTION <id>' (or an unambiguous equivalent) naming
    this exact id -- never on a general 'yes'/'go ahead'. Requires RUN mode
    AND confirm=true."""
    denial = _gate("approve_action", confirm)
    if denial:
        return denial
    result = core.post(f"/api/actions/{action_id}/approve", {})
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def reject_action(action_id: int, reason: str = None, confirm: bool = False) -> dict:
    """Tier C. Reject a pending action-approval request (moves its parent
    task, if any, to BLOCKED). Requires RUN mode AND confirm=true."""
    denial = _gate("reject_action", confirm)
    if denial:
        return denial
    body = {"reason": reason} if reason else {}
    result = core.post(f"/api/actions/{action_id}/reject", body)
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def record_execution_result(action_id: int, result: dict, verification: dict = None) -> dict:
    """Record the real outcome of an approved+executed action (only valid
    from status='approved'). Pull the actual diff/commit hash via
    audit_git_status/audit_recent_commits first -- this is where the
    *verified* evidence gets captured, not the pre-approval proposal text.
    Advances the parent task (if any) to VERIFYING. No RUN/confirm gate --
    this is bookkeeping after the real, already-approved action already
    happened."""
    return core.post(f"/api/actions/{action_id}/execution-result", {"result": result, "verification": verification})
