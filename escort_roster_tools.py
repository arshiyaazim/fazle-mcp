"""
Escort roster write tools for Hermes (2026-08-19, Task 4 of
HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Thin pass-throughs to fazle-core's existing modules/escort_roster/routes.py
(POST /api/escort-roster, PATCH/DELETE /api/escort-roster/{id},
POST /api/escort-roster/{id}/recalculate, POST /api/escort-roster/
change-requests/{id}/approve|reject) -- all already exist, already
_require_key-gated server-side (plus in-body _resolve_actor role
branching), already production-tested. No new business logic.

*** IMPORTANT -- direct-mutation warning (Owner decision, 2026-08-19) ***
PATCH/{program_id} and DELETE/{program_id} branch server-side on the
caller's RBAC tier: an operator-tier human gets routed into a PENDING
CHANGE REQUEST needing separate admin approval, but the FAZLE_CORE_API_KEY
this module uses is superadmin-tier (hermes-mcp-service, last confirmed
2026-08-10) -- so patch_roster_entry/delete_roster_entry MUTATE THE ROSTER
DIRECTLY AND IMMEDIATELY, skipping the change-request review step an
operator-tier human would go through. The Owner explicitly chose this
(RUN mode + confirm=True + explicit admin instruction is treated as
sufficient review, the same protection tier already trusted for
send_whatsapp_message/approve_payment_draft) over forcing every Hermes
roster edit through the change-request path. Only call these with
confirm=True after the admin has explicitly and specifically instructed
this exact change.

Mode gate: identical fail-closed _read_mode() logic to the other Task
2/4 tool modules (deliberately duplicated, not shared).

*** Raw roster maintenance vs. business assignment (2026-08-21, Owner-
directed, P1-A of the Phase 4 coverage-audit follow-on) ***
create_roster_entry/patch_roster_entry/delete_roster_entry/
recalculate_roster_entry write directly to a roster row with NO
conflict/eligibility/concurrency check -- they exist for raw data
correction/backfill (fixing a typo, recalculating conveyance, deleting a
duplicate), not for deciding who is on duty. For any real business
assignment ("Karim-কে MV X-এ escort duty দাও", "assign someone to this
program", reassigning/releasing an active duty) use dispatch_tools.py's
dispatch_assign_program/dispatch_unassign_program/dispatch_replace_escort
instead -- those go through modules.dispatch, the engine that actually
enforces status-eligibility, Active+Escort-only, overlap/conflict
checking, and optimistic concurrency. Using patch_roster_entry to
hand-write escort_employee_id onto a program bypasses every one of those
checks; do not use it as a workaround when dispatch_assign_program
rejects an assignment (a rejection is the engine telling you the
assignment is invalid, not an obstacle to route around).
"""

import json
import os

import fazle_core_client as core

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))


def _read_mode():
    """Fail-closed mode read — identical shape to send_whatsapp_tools.py's
    own copy (see that module's docstring for why this is deliberately
    duplicated rather than shared)."""
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


def list_roster(
    page: int = 1, page_size: int = 50, search: str = None, status: str = None,
    start_from: str = None, start_to: str = None,
) -> dict:
    """Read-only, no mode gate. Paginated, searchable, filterable escort
    roster list."""
    params = {"page": page, "page_size": page_size}
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    if start_from:
        params["start_from"] = start_from
    if start_to:
        params["start_to"] = start_to
    return core.get("/api/escort-roster", params)


def create_roster_entry(
    mother_vessel: str, lighter_vessel: str = None, master_mobile: str = None,
    escort_name: str = None, escort_mobile: str = None, destination: str = None,
    start_date: str = None, start_shift: str = None, end_date: str = None,
    end_shift: str = None, release_point: str = None, conveyance: float = None,
    notes: str = None, roster_status: str = "draft", confirm: bool = False,
) -> dict:
    """Create a new escort roster entry. mother_vessel is required.
    start_shift/end_shift are single letters D/N (day/night). Requires
    RUN mode AND confirm=true."""
    denial = _gate("create_roster_entry", confirm)
    if denial:
        return denial
    result = core.post("/api/escort-roster", {
        "mother_vessel": mother_vessel, "lighter_vessel": lighter_vessel,
        "master_mobile": master_mobile, "escort_name": escort_name,
        "escort_mobile": escort_mobile, "destination": destination,
        "start_date": start_date, "start_shift": start_shift,
        "end_date": end_date, "end_shift": end_shift,
        "release_point": release_point, "conveyance": conveyance,
        "notes": notes, "roster_status": roster_status,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def patch_roster_entry(program_id: int, confirm: bool = False, **fields) -> dict:
    """Update an existing escort roster entry (any subset of
    notes/release_point/roster_status/conveyance/end_date/end_shift/
    start_date/start_shift/mother_vessel/lighter_vessel/master_mobile/
    escort_name/escort_mobile via **fields).

    *** Mutates the roster entry DIRECTLY AND IMMEDIATELY -- this account
    is superadmin-tier, so this call skips the pending-change-request
    review step an operator-tier human would go through for the same
    edit. Only call with confirm=true after the admin has explicitly and
    specifically instructed this exact change. *** Requires RUN mode AND
    confirm=true."""
    denial = _gate("patch_roster_entry", confirm)
    if denial:
        return denial
    result = core.patch(f"/api/escort-roster/{int(program_id)}", fields)
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def delete_roster_entry(program_id: int, confirm: bool = False) -> dict:
    """Delete an escort roster entry.

    *** Mutates the roster DIRECTLY AND IMMEDIATELY -- same superadmin-
    tier direct-mutation warning as patch_roster_entry. Only call with
    confirm=true after the admin has explicitly and specifically
    instructed this exact deletion. *** Requires RUN mode AND confirm=true."""
    denial = _gate("delete_roster_entry", confirm)
    if denial:
        return denial
    result = core.delete(f"/api/escort-roster/{int(program_id)}")
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def recalculate_roster_entry(program_id: int, confirm: bool = False) -> dict:
    """Recalculate one roster entry's derived figures (e.g. conveyance
    totals). No actor-branching -- always applies directly. Requires RUN
    mode AND confirm=true."""
    denial = _gate("recalculate_roster_entry", confirm)
    if denial:
        return denial
    result = core.post(f"/api/escort-roster/{int(program_id)}/recalculate")
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def approve_roster_change_request(request_id: int, confirm: bool = False) -> dict:
    """Approve a pending roster change request (one an operator-tier
    actor submitted instead of mutating directly). Requires RUN mode AND
    confirm=true."""
    denial = _gate("approve_roster_change_request", confirm)
    if denial:
        return denial
    result = core.post(f"/api/escort-roster/change-requests/{int(request_id)}/approve")
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def reject_roster_change_request(request_id: int, note: str = None, confirm: bool = False) -> dict:
    """Reject a pending roster change request. note is an optional
    rejection reason. Requires RUN mode AND confirm=true."""
    denial = _gate("reject_roster_change_request", confirm)
    if denial:
        return denial
    params = {"note": note} if note else None
    result = core.post(
        f"/api/escort-roster/change-requests/{int(request_id)}/reject", params=params
    )
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}
