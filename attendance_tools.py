"""
Attendance write tools for Hermes (2026-08-19, Task 4 of
HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Thin pass-throughs to fazle-core's existing modules/attendance/routes.py
(POST /admin/attendance, POST /admin/attendance/draft,
PUT /admin/attendance/{id}, DELETE /admin/attendance/{id}) — all already
exist, already RBAC-gated server-side (_require_admin_or_above /
_require_office_assistant_or_above), already production-tested. No new
business logic. get_attendance (a read tool) already existed before this
task -- this module adds only the write side.

Mode gate: identical fail-closed _read_mode() logic to
send_whatsapp_tools.py/payment_draft_tools.py (deliberately duplicated,
not shared). Every mutation here requires RUN mode AND confirm=True.
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
    """Shared RUN+confirm gate. Returns None if allowed, else the denial dict."""
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


def create_attendance(
    employee_id: int, attendance_date: str, duty_status: str,
    remarks: str = None, confirm: bool = False,
) -> dict:
    """Create a final (not draft) attendance record. attendance_date is
    YYYY-MM-DD. Requires RUN mode AND confirm=true. Prefer
    create_attendance_draft unless the admin has explicitly asked for a
    direct, final record."""
    denial = _gate("create_attendance", confirm)
    if denial:
        return denial
    result = core.post("/admin/attendance", {
        "employee_id": employee_id, "attendance_date": attendance_date,
        "duty_status": duty_status, "remarks": remarks,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def create_attendance_draft(
    employee_id: int, attendance_date: str, duty_status: str,
    remarks: str = None, confirm: bool = False,
) -> dict:
    """Create a PENDING attendance draft (office-assistant-tier action) --
    never a final record. Requires RUN mode AND confirm=true."""
    denial = _gate("create_attendance_draft", confirm)
    if denial:
        return denial
    result = core.post("/admin/attendance/draft", {
        "employee_id": employee_id, "attendance_date": attendance_date,
        "duty_status": duty_status, "remarks": remarks,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def update_attendance(
    attendance_id: int, duty_status: str = None, remarks: str = None, confirm: bool = False,
) -> dict:
    """Update an existing attendance record's duty_status and/or remarks.
    Requires RUN mode AND confirm=true."""
    denial = _gate("update_attendance", confirm)
    if denial:
        return denial
    result = core.put(f"/admin/attendance/{int(attendance_id)}", {
        "duty_status": duty_status, "remarks": remarks,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def delete_attendance(attendance_id: int, confirm: bool = False) -> dict:
    """Delete an attendance record. Requires RUN mode AND confirm=true."""
    denial = _gate("delete_attendance", confirm)
    if denial:
        return denial
    result = core.delete(f"/admin/attendance/{int(attendance_id)}")
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}
