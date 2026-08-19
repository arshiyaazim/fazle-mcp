"""
Employee/HR write tools for Hermes (2026-08-19, Task 4 of
HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Thin pass-throughs to fazle-core's existing
modules/fazle_payroll_engine/routes.py (POST /api/fpe/employees,
PATCH /api/fpe/employees/{emp_id}, POST /api/fpe/employees/edit-requests/
{req_id}/approve|reject) -- all already exist, already
_require_admin_role-gated server-side, already production-tested. No new
business logic. update_employee's own route explicitly forbids mutating
fpe_cash_transactions or hard-deleting employees -- that's fazle-core's
own contract, unchanged here.

approve_employee_edit_request/reject_employee_edit_request take reviewer
(and reason, for reject) as REQUIRED, caller-supplied query params -- this
matches those routes' own existing contract exactly (unlike Task 2's
payment-draft routes, which resolve the actor server-side from the API
key -- do not force that pattern here, it isn't this route's contract).

Mode gate: identical fail-closed _read_mode() logic to
send_whatsapp_tools.py/payment_draft_tools.py/attendance_tools.py
(deliberately duplicated, not shared). Every mutation here requires RUN
mode AND confirm=True.
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


def create_employee(
    full_name: str, employee_mobile: str = None, role: str = None,
    status: str = "active", confirm: bool = False,
) -> dict:
    """Create a new employee record. Requires RUN mode AND confirm=true."""
    denial = _gate("create_employee", confirm)
    if denial:
        return denial
    result = core.post("/api/fpe/employees", {
        "full_name": full_name, "employee_mobile": employee_mobile,
        "role": role, "status": status,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def update_employee(
    emp_id: int, full_name: str = None, primary_phone: str = None,
    employee_id_phone: str = None, aliases: list = None, confirm: bool = False,
) -> dict:
    """Update an existing employee's name/phone/aliases. Cannot touch
    fpe_cash_transactions or hard-delete the employee -- that's this
    route's own existing contract. Requires RUN mode AND confirm=true."""
    denial = _gate("update_employee", confirm)
    if denial:
        return denial
    body = {}
    if full_name is not None:
        body["full_name"] = full_name
    if primary_phone is not None:
        body["primary_phone"] = primary_phone
    if employee_id_phone is not None:
        body["employee_id_phone"] = employee_id_phone
    if aliases is not None:
        body["aliases"] = aliases
    result = core.patch(f"/api/fpe/employees/{int(emp_id)}", body)
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def approve_employee_edit_request(req_id: int, reviewer: str, confirm: bool = False) -> dict:
    """Approve a pending employee edit request. reviewer is required (this
    route's own contract -- caller-supplied, not server-resolved).
    Requires RUN mode AND confirm=true."""
    denial = _gate("approve_employee_edit_request", confirm)
    if denial:
        return denial
    result = core.post(
        f"/api/fpe/employees/edit-requests/{int(req_id)}/approve",
        params={"reviewer": reviewer},
    )
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def reject_employee_edit_request(req_id: int, reviewer: str, reason: str, confirm: bool = False) -> dict:
    """Reject a pending employee edit request. reviewer and reason are
    both required (this route's own contract). Requires RUN mode AND
    confirm=true."""
    denial = _gate("reject_employee_edit_request", confirm)
    if denial:
        return denial
    result = core.post(
        f"/api/fpe/employees/edit-requests/{int(req_id)}/reject",
        params={"reviewer": reviewer, "reason": reason},
    )
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}
