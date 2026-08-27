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


_VALID_SOURCE_BRIDGES = {"bridge1", "bridge2", "bridge3", "meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}


def send_whatsapp_message(
    recipient: str,
    body: str,
    source_bridge: str = "",
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
    admin command.

    source_bridge is REQUIRED — pass whichever bridge ("bridge1"/
    "bridge2"/"bridge3"/"meta") the conversation you're replying to
    actually happened on. 2026-08-15: this used to default to "bridge2"
    silently, doubled by an identical silent default server-side in
    fazle-core's /admin/send-whatsapp -- a caller (or Hermes itself) that
    omitted it, or a conversation that actually happened on bridge1/
    bridge3, would silently send via bridge2 instead. Both defaults are
    now removed; check which bridge the admin's own message came in on
    (or ask, if genuinely ambiguous) before calling this.

    2026-08-21 (urgent bridge3 recruitment-reply recovery, Task 5): a
    successful return here means QUEUED, not delivered — fazle-core's
    /admin/send-whatsapp returns {"ok": true} the instant the message is
    accepted into fazle_outbound_queue; the actual bridge send happens
    asynchronously afterward and can still fail (confirmed live: a queued
    message whose bridge transport was down came back "ok": true here,
    then failed every retry and reached status='dlq' — reporting that as
    "Sent. Done." to the admin was a real, live overclaim bug). This
    function's return now carries "status": "queued" explicitly, never
    "sent" — call check_outbound_status(queue_id) a few seconds later
    before telling the admin the message was actually sent/delivered."""
    if not recipient or not recipient.strip():
        return {"ok": False, "error": "recipient is required"}
    if not body or not body.strip():
        return {"ok": False, "error": "body is required"}
    if not source_bridge or not source_bridge.strip():
        return {"ok": False, "error": "source_bridge is required -- specify the bridge this reply belongs to (e.g. \"bridge1\", \"bridge2\", \"bridge3\", \"meta\")"}
    if source_bridge not in _VALID_SOURCE_BRIDGES:
        return {"ok": False, "error": f"unknown source_bridge {source_bridge!r}; expected one of {sorted(_VALID_SOURCE_BRIDGES)}"}

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
    return {
        "ok": True,
        "mode_at_execution": mode,
        "confirmed": confirm,
        "status": "queued",
        "note": (
            "Queued only — not yet confirmed delivered. Call "
            "check_outbound_status(queue_id) before reporting this as sent."
        ),
        **result,
    }


def check_outbound_status(queue_id: int = 0, recipient: str = "") -> dict:
    """Real delivery-status check for one or more fazle_outbound_queue rows
    (2026-08-21, urgent bridge3 recruitment-reply recovery, Task 5) — call
    this after send_whatsapp_message/approve_draft before telling the admin
    a message was actually sent. Their "ok": true only means QUEUED.

    Pass queue_id (the id send_whatsapp_message/approve_draft returned), or
    recipient to see that phone's most recent queue rows if the id wasn't
    captured. Each item's status is one of: pending, sending (still in
    flight — not yet resolved either way), sent (bridge/outbound layer
    confirmed delivery — the only status that means "delivered"), failed
    or dlq (did NOT go out — report this as failed, never as sent)."""
    if not queue_id and not recipient:
        return {"ok": False, "error": "queue_id or recipient is required"}
    params = {}
    if queue_id:
        params["queue_id"] = int(queue_id)
    if recipient:
        params["recipient"] = recipient
    result = core.get("/api/outbound/status", params)
    if "error" in result:
        return {"ok": False, "error": result["error"]}
    return {"ok": True, **result}


def approve_draft(draft_id: int, admin_instruction: str = "", confirm: bool = False) -> dict:
    """Approve and send ONE existing pending draft reply (fazle_draft_
    replies), by ID -- ONLY when the admin has explicitly instructed this
    in the current conversation turn. Requires RUN mode AND confirm=True,
    identical gate to send_whatsapp_message (this is the same risk tier: a
    real outbound WhatsApp message).

    2026-08-15: added alongside send_whatsapp_message rather than reusing
    it, because approving an EXISTING draft is not the same action as
    sending fresh content -- this reuses fazle-core's own existing
    POST /api/drafts/{id}/approve endpoint (the exact same path the admin
    dashboard's "Approve" button and the WhatsApp `APPROVE <id>` command
    both already go through), so the draft's own row is correctly marked
    approved/sent, intent-specific approval side effects (e.g. attendance
    drafts routing through modules.attendance.finalize_attendance_draft())
    still run, and no second, parallel send path is invented. Read the
    draft's content first with audit_get_drafts(phone=...) -- never
    approve a draft whose text you have not actually read this turn.

    admin_instruction is NOT sent to fazle-core (POST /api/drafts/{id}/
    approve takes no body -- it records only draft_id/recipient/reply_text
    plus a reviewer identity resolved from the API key itself, unlike
    /admin/send-whatsapp). Kept as a parameter here only so Hermes's own
    conversation-side reasoning stays consistent with send_whatsapp_
    message's calling convention -- it is simply never sent to fazle-core."""
    if not draft_id:
        return {"ok": False, "error": "draft_id is required"}

    mode = _read_mode()
    if mode != "RUN":
        return {
            "ok": False,
            "mode_at_execution": mode,
            "error": "approve_draft requires RUN mode — switch modes first.",
        }
    if not confirm:
        return {
            "ok": False,
            "mode_at_execution": mode,
            "error": "approve_draft requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific draft to be sent.",
        }

    result = core.post(f"/api/drafts/{int(draft_id)}/approve")
    if "error" in result:
        return {"ok": False, "mode_at_execution": mode, "error": result["error"]}
    return {
        "ok": True,
        "mode_at_execution": mode,
        "confirmed": confirm,
        "status": "queued",
        "note": (
            "Queued only — not yet confirmed delivered. Call "
            "check_outbound_status(recipient=...) before reporting this as sent."
        ),
        **result,
    }
