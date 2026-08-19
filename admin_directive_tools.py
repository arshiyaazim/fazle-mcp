"""
Admin-directive tools for Earth (2026-08-19, Hermes->Earth rename +
autonomy-policy pass — Item 3 of the same-day plan).

Thin pass-through to fazle-core's existing POST /admin/directives /
POST /admin/directives/{id}/revoke / GET /admin/directives
(modules/admin_directives + app/main.py:3256-3286) — no new core route,
no new DB table, no new RBAC gate. core/.claude/CLAUDE.md's non-negotiable
rule on modules.admin_directives being write-capable now has an explicit,
narrow exception for this tool (see that file's own updated bullet): a
directive may be written by Earth's own judgment call, not only a literal
typed WhatsApp REMEMBER/FORGET/AUTOREPLY command or a web request — but
still only when the admin has just given a clear, current instruction to
change a standing rule, gated by the exact same RUN+confirm+superadmin
RBAC as every other Tier C write, never inferred from a vague or
historical statement.

Mode gate: identical fail-closed _read_mode() shape to attendance_tools.py/
send_whatsapp_tools.py (deliberately duplicated, not shared — see those
modules' docstrings for why).
"""

import json
import os

import fazle_core_client as core

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))


def _read_mode():
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


def remember_business_rule(
    subject_type: str, subject_key: str, directive_text: str,
    category: str = "general", confirm: bool = False,
) -> dict:
    """Persist an admin's explicit, current business-rule instruction as an
    active directive that overrides the matching hardcoded default at its
    read site (e.g. subject_type="recruitment_rule",
    subject_key="recruitment_minimum_age", directive_text="23"). Call this
    ONLY when the admin has just stated a clear, current instruction to
    change a standing rule -- never infer this from a vague, historical,
    or hypothetical statement. subject_key must match a read site's known
    key -- ask which key applies rather than guessing a new one. Requires
    RUN mode AND confirm=true. Reversible via revoke_directive."""
    denial = _gate("remember_business_rule", confirm)
    if denial:
        return denial
    result = core.post("/admin/directives", {
        "directive_text": directive_text, "category": category,
        "subject_type": subject_type, "subject_key": subject_key,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def revoke_directive(directive_id: int, confirm: bool = False) -> dict:
    """Revoke (soft-delete) a previously saved admin directive by id,
    reverting its subject_key's read site back to its hardcoded default.
    Requires RUN mode AND confirm=true."""
    denial = _gate("revoke_directive", confirm)
    if denial:
        return denial
    result = core.post(f"/admin/directives/{directive_id}/revoke", {})
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def get_directives(subject_key: str = None, status: str = "active", limit: int = 50) -> dict:
    """Read-only: list saved admin directives, optionally filtered by
    subject_key. Tier A -- no mode gate, no confirmation needed."""
    params = {"status": status, "limit": limit}
    if subject_key:
        params["subject_key"] = subject_key
    return core.get("/admin/directives", params=params)
