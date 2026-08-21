"""
Escort-program lifecycle tools for Hermes (2026-08-21, P1-C of the Phase 4
coverage-audit follow-on -- see HERMES_ESCORT_PROGRAM_ROSTER_LIFECYCLE_AUDIT
memory).

Forensic finding this closes: the only production path that creates a real
wbom_escort_programs row was modules.escort.save_escort_programs(), fed by
the free-text WhatsApp order parser -- there was no structured way for
Hermes to create one on an Owner-direct instruction ("MV X / lighter Y-এর
জন্য নতুন escort program তৈরি করো"). escort_roster_tools.py's
create_roster_entry() looked like the obvious candidate but is NOT the
same thing: it only ever writes to escort_roster_entries and was never
wired to create a wbom_escort_programs row -- confirmed live (13 genuine
production orphan roster rows created that way, predating this pass, and
one disposable smoke-test row during the previous P1-A pass that
modules.dispatch correctly rejected with program_not_found).

create_escort_program is a thin pass-through to fazle-core's new
POST /api/escort/programs (modules/escort/routes.py) -- itself a thin
structured-input wrapper over the EXISTING save_escort_programs(), which
already does dedup checking, the real INSERT, and an immediate roster
sync. No program-creation logic is duplicated here or in fazle-core.

Read tools (list/get escort programs) deliberately reuse escort_roster_
tools.py's existing list_roster -- every real program has always had a
1:1 synced roster entry (confirmed live: 0 of 352 production programs
lack one) so a second, parallel "list programs" read view is unnecessary
and would itself be exactly the kind of duplicate-source-of-truth risk
this pass is meant to close, not create.

Mode gate: identical fail-closed _read_mode() logic to every other Task
2/4 tool module (deliberately duplicated, not shared).

Hard gate on the one mutation (2026-08-21, Owner-directed -- explicitly
NOT a bare confirm=True): create_escort_program requires a real
approved_action_id whose task_tools.get_action() row has status='approved'
AND action_type='escort_program_creation' -- a DEDICATED action_type
(migration 071), deliberately NOT reusing dispatch_tools.py's
'dispatch_assignment' -- deciding whether a duty/program should exist at
all is a different decision from deciding who is assigned to it. Identical
shape to dispatch_tools.py's/opencode_tools.py's own hard gates.
"""

import json
import os

import fazle_core_client as core
import task_tools

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))

ACTION_TYPE = "escort_program_creation"


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
    """Shared hard-gate check -- identical pattern to dispatch_tools.py/
    client_billing_tools.py/opencode_tools.py."""
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
                     "it must be explicitly approved by the admin before this can run.",
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


def create_escort_program(
    mother_vessel: str, lighter_vessel: str, master_mobile: str, approved_action_id: int,
    destination: str = None, capacity: str = None, cargo_type: str = None, importer: str = None,
    date_hint: str = None, shift: str = "D", confirm: bool = False,
) -> dict:
    """Create a new authoritative escort program (wbom_escort_programs,
    status='draft') through fazle-core's real order-creation service
    (save_escort_programs()) -- the SAME path a real WhatsApp client order
    uses, dedup-checked, immediately roster-synced. mother_vessel,
    lighter_vessel, and master_mobile (the client/master's real contact
    number -- required, never invent or leave blank) are mandatory; a
    duplicate (same mother+lighter vessel, same date, same client) is
    detected server-side and returns the EXISTING program_id rather than
    creating a second row.

    This is NOT the same as create_roster_entry (escort_roster_tools.py) --
    that tool only ever writes a roster-display row with no corresponding
    program and cannot later be dispatch_assign_program'd. Use THIS tool
    whenever the Owner means "create a new escort duty/program", and
    dispatch_tools.py's tools for everything that happens next (checking
    conflicts, getting suggestions, assigning an employee).

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='escort_program_creation'; (2) RUN mode; (3) confirm=true
    after the admin has explicitly instructed creating this exact program."""
    denial = _gate("create_escort_program", confirm, approved_action_id)
    if denial:
        return denial
    body = {
        "mother_vessel": mother_vessel, "lighter_vessel": lighter_vessel, "master_mobile": master_mobile,
        "shift": shift,
    }
    if destination:
        body["destination"] = destination
    if capacity:
        body["capacity"] = capacity
    if cargo_type:
        body["cargo_type"] = cargo_type
    if importer:
        body["importer"] = importer
    if date_hint:
        body["date_hint"] = date_hint
    result = core.post("/api/escort/programs", body)
    if "error" in result:
        out = {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
        detail = result.get("error_detail")
        if isinstance(detail, dict) and isinstance(detail.get("detail"), str):
            out["error"] = detail["detail"]
        return out
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}
