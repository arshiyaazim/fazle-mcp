"""
Hermes Capability Expansion — Level 1 (2026-08-10, see the approved plan
and project_hermes_unification_20260810.md in memory).

draft_whatsapp_reply lets Hermes propose a WhatsApp reply without ever
sending anything itself. It's a thin wrapper around fazle-core's new
POST /api/drafts route (modules/drafts/routes.py), which itself wraps the
existing, race-safe, deduped, audited shared.draft_reply.create_draft_reply()
— the same insert path every internal fazle-core module already uses. No
new draft-storage logic exists here or in fazle-core; only external access
to what already existed.

Every draft this creates lands in status='pending' and can ONLY leave that
state via the existing approve/edit/delete endpoints (modules/drafts/routes.py)
— exactly like every other draft already in the system, whether created by
attendance, payroll, admin_commands, or now Hermes. This tool has no path
to cause an outbound WhatsApp send by itself, so it needs no mode-gate and
no confirm flag (unlike opencode_dispatch/run_scheduled_task, which are
genuinely write-capable in a way this isn't).
"""

import fazle_core_client as core


def draft_whatsapp_reply(
    recipient: str,
    bridge: str,
    draft_text: str,
    role: str = "unknown",
    intent: str = "hermes_suggested",
    context: str = "",
) -> dict:
    """Propose a WhatsApp reply as a pending draft — never sends it.
    recipient: the phone number the reply would go to. bridge: which
    inbound channel this relates to (bridge1/bridge2/bridge3/meta). Draft
    is reviewed/approved by a human via the existing drafts dashboard
    (/api/drafts) — you cannot send this yourself, and shouldn't imply to
    the user that you have. If a draft was recently created for the same
    recipient+bridge (last 5 minutes), this is suppressed as a duplicate
    (ok=False) rather than creating a second one — check get_recent_messages
    or ask the user before retrying."""
    if not recipient or not recipient.strip():
        return {"ok": False, "error": "recipient is required"}
    if not draft_text or not draft_text.strip():
        return {"ok": False, "error": "draft_text is required"}
    return core.post(
        "/api/drafts",
        {
            "recipient": recipient,
            "bridge": bridge,
            "draft_text": draft_text,
            "role": role,
            "intent": intent,
            "context": context,
        },
    )
