"""
Escort dispatch/assignment tools for Hermes (2026-08-21, P1-A of the Phase 4
coverage-audit follow-on -- see HERMES_PHASE4_BUSINESS_DOMAIN_COVERAGE_AUDIT
memory).

Thin pass-throughs to fazle-core's EXISTING modules/dispatch/routes.py
(/api/dispatch/*) -- availability, check-conflict, suggestions, overlaps are
already read-only there; assign/unassign/replace-escort are the only writes,
already transactional, advisory-locked, and audited server-side
(modules.dispatch.assign_program's own docstring). No new business logic
here, and NOT a replacement for escort_roster_tools.py's raw roster CRUD --
see that module's docstring for the explicit "raw maintenance vs business
assignment" split this pass adds.

Why this module exists: the Phase 4 audit found Hermes could already read
the roster (list_roster/get_escort_report) and mutate raw roster rows
directly (create/patch/delete_roster_entry), but had NO path to the real
dispatch engine -- meaning an admin instruction like "assign Karim to MV X"
could only be honored by hand-writing a roster row, bypassing every
conflict/eligibility/concurrency check modules.dispatch.assign_program
enforces (status-eligibility matrix, advisory locks, optimistic concurrency
via expected_current_employee_id, active-escort-only checks). This module
closes that gap by exposing the real engine instead.

Mode gate: identical fail-closed _read_mode() logic to every other Task 2/4
tool module (deliberately duplicated, not shared).

Hard gate on every mutation (2026-08-21, Owner-directed -- explicitly NOT a
bare confirm=True): dispatch_assign_program/dispatch_unassign_program/
dispatch_replace_escort each require a real approved_action_id whose
task_tools.get_action() row has status='approved' AND
action_type='dispatch_assignment' -- call propose_action(action_type=
"dispatch_assignment", ...) or authorize_action(action_type=
"dispatch_assignment", ...) first, then wait for the admin's explicit
approval before ever calling one of these. Identical shape to
opencode_tools.py's opencode_dispatch hard gate -- see db/migrations/070_
hermes_action_approvals_dispatch_billing_types.sql for the schema widening
that added 'dispatch_assignment' to hermes_action_approvals.action_type.

Read-only tools below (dispatch_check_availability, dispatch_check_conflict,
dispatch_get_suggestions, dispatch_list_overlaps) have NO mode/approval gate
-- they never mutate anything, matching modules/dispatch/routes.py's own
RBAC tiering (viewer role or above for all four)."""

import json
import os

import fazle_core_client as core
import task_tools

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))

ACTION_TYPE = "dispatch_assignment"


def _read_mode():
    """Fail-closed mode read -- identical shape to every other tool
    module's own copy (deliberately duplicated, not shared)."""
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


def _require_approved_action(approved_action_id):
    """Shared hard-gate check for every mutation below -- identical pattern
    to opencode_tools.py's opencode_dispatch. Returns None if the action is
    a genuinely approved, correctly-typed action; otherwise a denial dict to
    return directly."""
    if not approved_action_id:
        return {
            "ok": False,
            "error": "approved_action_id is required -- propose_action(action_type="
                     f"{ACTION_TYPE!r}, ...) or authorize_action(...) then get an "
                     "explicit APPROVE ACTION <id> (or equivalent fused authorization) first.",
        }
    action = task_tools.get_action(approved_action_id)
    if "error" in action:
        return {"ok": False, "error": f"could not verify approved_action_id={approved_action_id}: {action['error']}"}
    if action.get("status") != "approved":
        return {
            "ok": False,
            "error": f"action #{approved_action_id} is not approved (status={action.get('status')}) -- "
                     "it must be explicitly approved by the admin before this dispatch action can run.",
        }
    if action.get("action_type") != ACTION_TYPE:
        return {
            "ok": False,
            "error": f"action #{approved_action_id} has action_type={action.get('action_type')!r}, "
                     f"not {ACTION_TYPE!r} -- propose/authorize it with the correct action_type.",
        }
    return None


def _gate(action: str, confirm: bool, approved_action_id):
    denial = _require_approved_action(approved_action_id)
    if denial:
        return denial
    mode = _read_mode()
    if mode != "RUN":
        return {"ok": False, "mode_at_execution": mode, "error": f"{action} requires RUN mode -- switch modes first."}
    if not confirm:
        return {
            "ok": False, "mode_at_execution": mode,
            "error": f"{action} requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific change.",
        }
    return None


def _result_from(core_response: dict, confirm: bool) -> dict:
    """Normalize a fazle_core_client response into this module's {ok, ...}
    shape, surfacing error_detail's structured fields (code/conflicts) when
    fazle-core rejected the request with a business-rule error (e.g. a real
    scheduling conflict) rather than a transport failure."""
    if "error" in core_response:
        out = {"ok": False, "mode_at_execution": "RUN", "error": core_response["error"]}
        detail = core_response.get("error_detail")
        if isinstance(detail, dict):
            inner = detail.get("detail")
            if isinstance(inner, dict):
                if "code" in inner:
                    out["code"] = inner["code"]
                if "conflicts" in inner:
                    out["conflicts"] = inner["conflicts"]
                if "detail" in inner:
                    out["error"] = inner["detail"]
            elif isinstance(inner, str):
                out["error"] = inner
        return out
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **core_response}


# ── Read-only (no gate) ───────────────────────────────────────────────────

def dispatch_check_availability(
    employee_id: int = None, available_only: bool = None, page: int = 1, page_size: int = 50,
) -> dict:
    """Read-only. List active escort workers (designation='Escort',
    status='Active' only) with current availability, any conflict, last
    assignment date, and active workload -- never returns every employee."""
    params = {"page": page, "page_size": page_size}
    if employee_id is not None:
        params["employee_id"] = employee_id
    if available_only is not None:
        params["available_only"] = available_only
    return core.get("/api/dispatch/availability", params)


def dispatch_check_conflict(
    escort_employee_id: int, start_date: str, start_shift: str, end_date: str, end_shift: str,
    exclude_program_id: int = None,
) -> dict:
    """Read-only. Check whether one escort employee has an overlapping
    assignment against a proposed window. start_date/end_date are
    YYYY-MM-DD; start_shift/end_shift are single letters D/N (day/night).
    Use this (or dispatch_get_suggestions) BEFORE dispatch_assign_program --
    never assign blind. Returns available=True/False/None (None =
    availability_unknown, e.g. a data-quality issue on a blocking program --
    never treat None as available)."""
    body = {
        "escort_employee_id": escort_employee_id, "start_date": start_date, "start_shift": start_shift,
        "end_date": end_date, "end_shift": end_shift,
    }
    if exclude_program_id is not None:
        body["exclude_program_id"] = exclude_program_id
    return core.post("/api/dispatch/check-conflict", body)


def dispatch_get_suggestions(
    start_date: str, start_shift: str, end_date: str, end_shift: str,
    destination: str = None, limit: int = 10,
) -> dict:
    """Read-only. Deterministic, explainable ranked suggestions for who to
    assign to a proposed window -- no LLM guessing. Ranking: (1) available
    (no overlap) (2) longest idle since last assignment (3) lowest current
    workload (4) employee_id as a stable tie-breaker. Use this for "কে
    available?" / "suggest an escort for MV X" -- never invent a name."""
    params = {
        "start_date": start_date, "start_shift": start_shift, "end_date": end_date,
        "end_shift": end_shift, "limit": limit,
    }
    if destination:
        params["destination"] = destination
    return core.get("/api/dispatch/suggestions", params)


def dispatch_list_overlaps(
    employee_id: int = None, status: str = None, page: int = 1, page_size: int = 20,
) -> dict:
    """Read-only. Surface existing overlapping-assignment pairs already in
    the data (a review facility, never mutates). Use to check "is there
    already a scheduling problem" before proposing a fix."""
    params = {"page": page, "page_size": page_size}
    if employee_id is not None:
        params["employee_id"] = employee_id
    if status:
        params["status"] = status
    return core.get("/api/dispatch/overlaps", params)


# ── Mutations (approved_action_id + RUN mode + confirm=true) ──────────────

def dispatch_assign_program(
    program_id: int, escort_employee_id: int, approved_action_id: int,
    expected_current_employee_id: int = None, confirm: bool = False,
) -> dict:
    """Controlled manual assignment/reassignment through the REAL dispatch
    engine (modules.dispatch.assign_program) -- transactional, conflict-
    checked inside the same DB transaction, advisory-lock concurrency-safe.
    Only "draft"/"confirmed" programs are assignable; the employee must be
    Active and designation='Escort'. expected_current_employee_id is
    optimistic-concurrency: pass the program's CURRENT escort_employee_id
    (None if unassigned) as read via dispatch_check_availability/
    get_escort_report -- if it has changed since you last read it, this call
    is rejected as stale rather than silently overwriting a concurrent
    change. On a genuine scheduling conflict, returns ok=False with
    code='dispatch_conflict' and the actual conflicting programs in
    `conflicts` -- this tool does NOT support overriding a conflict
    (assign-override is a separate, more privileged fazle-core endpoint not
    wired to Hermes in this pass).

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='dispatch_assignment'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly authorized THIS assignment in conversation.
    This is never satisfied by raw roster CRUD (create/patch_roster_entry)
    -- those tools deliberately do not run conflict/eligibility checks; use
    THIS tool for any real business assignment."""
    denial = _gate("dispatch_assign_program", confirm, approved_action_id)
    if denial:
        return denial
    body = {"escort_employee_id": escort_employee_id}
    if expected_current_employee_id is not None:
        body["expected_current_employee_id"] = expected_current_employee_id
    result = core.post(f"/api/dispatch/programs/{int(program_id)}/assign", body)
    return _result_from(result, confirm)


def dispatch_unassign_program(
    program_id: int, expected_current_employee_id: int, reason: str, approved_action_id: int,
    confirm: bool = False,
) -> dict:
    """Safe unassignment through the real dispatch engine
    (modules.dispatch.unassign_program) -- draft/confirmed programs only,
    blocked server-side by any accepted attendance or existing payment
    draft (never hard-deletes the program). expected_current_employee_id is
    mandatory optimistic-concurrency (must match the program's real current
    assignment). reason is mandatory and goes into the audit trail.

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='dispatch_assignment'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed this specific release/reassignment."""
    denial = _gate("dispatch_unassign_program", confirm, approved_action_id)
    if denial:
        return denial
    body = {"expected_current_employee_id": expected_current_employee_id, "reason": reason}
    result = core.post(f"/api/dispatch/programs/{int(program_id)}/unassign", body)
    return _result_from(result, confirm)


def dispatch_replace_escort(
    program_id: int, mother_vessel: str, lighter_vessel: str, escort_name: str, escort_mobile: str,
    start_date: str, start_shift: str, approved_action_id: int,
    escort_employee_id: int = None, master_mobile: str = None, destination: str = None,
    confirm: bool = False,
) -> dict:
    """Manual trigger for the same escort-replacement-segment workflow the
    automatic admin-message pipeline uses (modules.dispatch.
    create_replacement_segment) -- for correcting/backfilling historical
    assignment data, same validation/transaction/audit trail as the
    automatic path, not a second implementation. program_id is the earlier
    program being replaced/continued.

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='dispatch_assignment'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed this specific replacement."""
    denial = _gate("dispatch_replace_escort", confirm, approved_action_id)
    if denial:
        return denial
    body = {
        "mother_vessel": mother_vessel, "lighter_vessel": lighter_vessel,
        "escort_name": escort_name, "escort_mobile": escort_mobile,
        "start_date": start_date, "start_shift": start_shift,
    }
    if escort_employee_id is not None:
        body["escort_employee_id"] = escort_employee_id
    if master_mobile:
        body["master_mobile"] = master_mobile
    if destination:
        body["destination"] = destination
    result = core.post(f"/api/dispatch/programs/{int(program_id)}/replace-escort", body)
    return _result_from(result, confirm)
