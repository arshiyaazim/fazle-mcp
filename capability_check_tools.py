"""Capability check for the Admin <-> Hermes control conversation.

2026-09-30 (Owner decision). The Owner's instruction is explicit: Hermes must
verify that a requested action is actually executable with its current
tools/path *first*, and only then ask the Admin for permission. Asking for
permission and then reporting "I have no tool that does that" is the failure
mode this module exists to prevent.

So this is a read-only description of what can be done right now, and what
each candidate action would still need. It changes nothing and authorizes
nothing. It exists so the model can answer "can I do this?" without guessing
and without burning the Admin's attention on a permission request for
something that was never going to work.
"""

from __future__ import annotations

import json
import os
from typing import Any

_MODE_FILE = os.environ.get(
    "HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt")
)

# Actions Hermes can be asked to perform from the control conversation, and
# what each one needs beyond "the Admin asked". Values are deliberately
# descriptive rather than executable: this tool never acts.
_ACTION_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "draft_whatsapp_reply": {
        "summary": "Propose a reply as a pending draft for human review.",
        "requires_mode": None,
        "requires_admin_authorization": False,
        "sends_immediately": False,
        "note": (
            "Always available. This only creates a draft; nothing is sent. "
            "This is the right first step for anything outbound."
        ),
    },
    "approve_draft": {
        "summary": "Approve one pending Hermes-proposed draft and queue it for sending.",
        "requires_mode": None,
        "requires_admin_authorization": True,
        "sends_immediately": False,
        "note": (
            "Needs the Admin to confirm this exact draft (recipient and text) "
            "in the control conversation. One confirmation authorizes one "
            "approval and is then spent. confirm=true is only an execution "
            "acknowledgement, not the authority."
        ),
    },
    "send_whatsapp_message": {
        "summary": "Send one message directly, skipping the draft step.",
        "requires_mode": "RUN",
        "requires_admin_authorization": True,
        "sends_immediately": True,
        "note": (
            "Sends immediately with no draft for review, so prefer "
            "draft_whatsapp_reply + approve_draft unless the Admin explicitly "
            "asked for a direct send. Needs RUN mode AND the Admin's "
            "confirmation of this specific recipient and text."
        ),
    },
    "forward_message": {
        "summary": (
            "Pass one participant's message on to another, e.g. sending Karim's "
            "message to Rahim."
        ),
        "requires_mode": None,
        "requires_admin_authorization": True,
        "sends_immediately": False,
        "note": (
            "Do this as a draft: read the source message, create a draft "
            "addressed to the requested recipient containing that content, "
            "state plainly to the Admin who it came from and who will receive "
            "it, and let the Admin confirm before it is sent. Do not send the "
            "forwarded text as if it came from the Admin unless that is "
            "actually what was asked."
        ),
    },
}


def _read_mode() -> str:
    try:
        with open(_MODE_FILE, "r") as fh:
            raw = fh.read().strip()
    except OSError:
        return "READ"
    if not raw:
        return "READ"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # A legacy bare-word mode file is still accepted, matching every
        # other reader in this codebase.
        return raw if raw.isupper() else "READ"
    mode = str(data.get("mode") or "").strip().upper()
    return mode if mode else "READ"


def check_action_capability(action: str = "", conversation: str = "") -> dict:
    """Describe whether `action` is executable right now, and what it still needs.

    Read-only. Returns a plain dict the model can read directly; it never
    performs the action and never grants anything.
    """
    mode = _read_mode()

    available = {
        name: {
            "executable_now": (
                True
                if not spec["requires_mode"]
                else mode == spec["requires_mode"]
            ),
            "still_needs": _still_needs(name, spec, mode),
            **spec,
        }
        for name, spec in _ACTION_REQUIREMENTS.items()
    }

    out: dict[str, Any] = {
        "current_mode": mode,
        "conversation": conversation or None,
        "actions": available,
        "how_to_use": [
            "Check here BEFORE asking the Admin for permission, so you never "
            "request something that cannot be done with the current tools.",
            "Anything with requires_admin_authorization=true is not something "
            "you can authorise yourself. Ask the Admin to confirm that specific "
            "action, then act on their reply.",
            "Proposing is always allowed; only doing is gated.",
        ],
    }

    if action:
        key = action.strip().lower()
        spec = _ACTION_REQUIREMENTS.get(key)
        if spec is None:
            out["requested_action"] = action
            out["known_action"] = False
            out["verdict"] = (
                f"'{action}' is not an action this deployment recognises. Do not "
                "ask the Admin to authorize it -- tell them plainly that no tool "
                "here does that, and offer the closest supported alternative."
            )
        else:
            entry = available[key]
            out["requested_action"] = key
            out["known_action"] = True
            out["executable_now"] = entry["executable_now"]
            out["still_needs"] = entry["still_needs"]
            out["verdict"] = (
                f"'{key}' is available now."
                if entry["executable_now"]
                else f"'{key}' needs mode {spec['requires_mode']} and the mode is "
                     f"currently {mode}. Elevate for a short window with "
                     f"set_mode_state only after the Admin asks for this action."
            )
    return out


def _still_needs(name: str, spec: dict[str, Any], mode: str) -> list[str]:
    needs: list[str] = []
    if spec["requires_mode"] and mode != spec["requires_mode"]:
        needs.append(
            f"mode {spec['requires_mode']} (currently {mode}) -- ask the Admin, "
            "then set_mode_state with an explicit short ttl_seconds"
        )
    if spec["requires_admin_authorization"]:
        needs.append(
            "the Admin's confirmation of this specific action in the control "
            "conversation"
        )
    if not needs:
        needs.append("nothing beyond the Admin's request")
    return needs


__all__ = ["check_action_capability"]
