"""
Hermes Capability Expansion — Level 2 (2026-08-10, see the approved plan
and project_hermes_unification_20260810.md in memory).

send_whatsapp_message lets Hermes send ONE specific WhatsApp message to
ONE specific recipient, but ONLY when the admin has explicitly instructed
it in that same conversation turn — never triggered by anything else, and
explicitly NOT part of the automated customer-facing reply pipeline
(message_router/identity_brain/app.llm are untouched by this path).

Mode gate: identical fail-closed _read_mode() logic to scheduler_tools.py/
opencode_tools.py (deliberately duplicated, not shared — see those
modules' own docstrings for why). Requires RUN mode AND confirm=True,
mirroring opencode_dispatch's existing pattern exactly.

Auth: reuses fazle_core_client.py's existing FAZLE_CORE_API_KEY (the same
credential every other fazle-core-facing tool in this package already
uses) — no new token needed, unlike the OpenCode handoff (which needed a
separate token because it goes through assistant-backend's admin-JWT
gate). The account behind FAZLE_CORE_API_KEY must be superadmin-tier for
this specific call to succeed (fazle-core's own RBAC, COMMAND_ROLE[
"send_whatsapp_message"] = "superadmin") — an Owner-authorized, manually
performed role change, not something this module does or assumes.

Audit: fazle-core's /admin/send-whatsapp endpoint itself records every
call (fazle_admin_audit) pairing admin_instruction with the exact
recipient+body sent — this module just needs to pass admin_instruction
through, not implement any audit logic of its own.
"""

import json
import os

import fazle_core_client as core

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))


def _read_mode():
    """Fail-closed mode read — identical shape to scheduler_tools.py's and
    opencode_tools.py's own copies (see those modules' docstrings for why
    this is deliberately duplicated rather than shared)."""
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


def send_whatsapp_message(
    recipient: str,
    body: str,
    source_bridge: str = "bridge2",
    admin_instruction: str = "",
    confirm: bool = False,
) -> dict:
    """Send ONE WhatsApp message to ONE recipient — ONLY when the admin
    has explicitly instructed this in the current conversation turn.
    Requires RUN mode AND confirm=True (only set after the user has
    explicitly asked for this specific send — same pattern as
    opencode_dispatch/run_scheduled_task). Pass admin_instruction as the
    admin's own original instruction text — it's paired with the exact
    recipient+body actually sent in fazle-core's audit log, so a mistake
    is traceable after the fact. This is NOT for automated customer
    replies — use draft_whatsapp_reply for anything that isn't a direct
    admin command."""
    if not recipient or not recipient.strip():
        return {"ok": False, "error": "recipient is required"}
    if not body or not body.strip():
        return {"ok": False, "error": "body is required"}

    mode = _read_mode()
    if mode != "RUN":
        return {
            "ok": False,
            "mode_at_execution": mode,
            "error": "send_whatsapp_message requires RUN mode — switch modes first.",
        }
    if not confirm:
        return {
            "ok": False,
            "mode_at_execution": mode,
            "error": "send_whatsapp_message requires explicit confirmation "
            "(confirm=true) after the admin has directly instructed this send.",
        }

    result = core.post(
        "/admin/send-whatsapp",
        {
            "recipient": recipient,
            "body": body,
            "source_bridge": source_bridge,
            "admin_instruction": admin_instruction,
        },
    )
    if "error" in result:
        return {"ok": False, "mode_at_execution": mode, "error": result["error"]}
    return {"ok": True, "mode_at_execution": mode, "confirmed": confirm, **result}
