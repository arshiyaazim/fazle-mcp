"""
Payment draft (fazle_payment_drafts) action tools for Hermes (2026-08-19,
Task 2 of HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Closes the headline capability-exposure gap that audit found: the full
canonical approve/edit/reject chain (modules.draft_approval.approve_draft
→ create_canonical_transaction() → ledger upsert → audit) has existed and
been production-tested since Sprint-3B, but was only reachable through the
admin's own literal WhatsApp text commands (APPROVED/DREDIT/DREJECT) —
never through any route or tool Hermes could call. This module is a thin
pass-through over fazle-core's new /api/payment-drafts/* routes
(modules/draft_approval/routes.py) — no new business logic here or there.

Mode gate: identical fail-closed _read_mode() logic to
send_whatsapp_tools.py/scheduler_tools.py/opencode_tools.py (deliberately
duplicated, not shared). Every mutating tool here requires RUN mode AND
confirm=True — this is real money moving, the same risk tier as
send_whatsapp_message, not a lower one.

get_payment_drafts is read-only, no gate — reuses fazle-core's existing
GET /admin/payment-drafts (no new route needed for listing).
"""

import json
import os

import fazle_core_client as core
from arg_coerce import as_str

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


def get_payment_drafts(phone: str | int = "", status: str = "pending", limit: int = 20) -> dict:
    """Read-only, no mode gate. Lists fazle_payment_drafts (a different
    table from fazle_draft_replies -- use audit_get_drafts for reply
    drafts). status filters client-side against the draft's own status
    field (default "pending" -- pass "" for all statuses). phone filters
    client-side by digit-match against payout_mobile/employee_mobile.
    ALWAYS call this and read a draft's full row before approving/editing/
    rejecting it -- never act on a draft id you have not read this turn."""
    limit = min(max(int(limit or 20), 1), 100)
    result = core.get("/admin/payment-drafts", {"limit": limit})
    if "error" in result:
        return result
    drafts = result.get("payment_drafts", [])
    if status:
        drafts = [d for d in drafts if d.get("status") == status]
    phone = as_str(phone)
    if phone:
        digits = "".join(ch for ch in phone if ch.isdigit())
        drafts = [
            d for d in drafts
            if digits and digits in ((d.get("payout_mobile") or d.get("employee_mobile") or ""))
        ]
    return {"payment_drafts": drafts, "count": len(drafts)}


def get_payment_draft_audit(draft_id: int) -> dict:
    """Read-only, no mode gate. All audit events recorded for one payment
    draft (created/approved/edited/rejected, with before/after state)."""
    return core.get(f"/api/payment-drafts/{int(draft_id)}/audit")


def get_payment_draft_employee_balance(employee_id: int, period: str = "") -> dict:
    """Read-only, no mode gate. One employee's ledger balance for a given
    accounting period (defaults to the current period). Sourced entirely
    from fpe_employee_ledger -- no manual calculation."""
    params = {"period": period} if period else None
    return core.get(f"/api/payment-drafts/employee/{int(employee_id)}/balance", params)


def approve_payment_draft(draft_id: int, amount: float, method: str, confirm: bool = False) -> dict:
    """Approve ONE pending payment draft -- creates the canonical
    transaction + ledger entry, exactly the same as the WhatsApp
    `APPROVED <id> <amount> <method>` admin command. Requires RUN mode AND
    confirm=True, same risk tier as send_whatsapp_message -- this moves
    real money. ALWAYS call get_payment_drafts first and read the draft's
    full row before approving it. After approving, use get_employee_ledger/
    get_employee_transaction_history to independently verify the
    transaction and ledger entry actually appeared."""
    mode = _read_mode()
    if mode != "RUN":
        return {"ok": False, "mode_at_execution": mode, "error": "approve_payment_draft requires RUN mode — switch modes first."}
    if not confirm:
        return {
            "ok": False, "mode_at_execution": mode,
            "error": "approve_payment_draft requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific draft to be approved.",
        }
    result = core.post(f"/api/payment-drafts/{int(draft_id)}/approve", {"amount": amount, "method": method})
    if "error" in result:
        return {"ok": False, "mode_at_execution": mode, "error": result["error"]}
    return {"ok": True, "mode_at_execution": mode, "confirmed": confirm, **result}


def edit_payment_draft(
    draft_id: int,
    new_amount: float = None,
    new_method: str = None,
    new_payout_mobile: str = None,
    reason: str = None,
    confirm: bool = False,
) -> dict:
    """Edit ONE pending payment draft (version increment, before/after
    state saved). Does NOT create a transaction -- a subsequent
    approve_payment_draft call is still required. Same as the WhatsApp
    `DREDIT` admin command. Requires RUN mode AND confirm=True."""
    mode = _read_mode()
    if mode != "RUN":
        return {"ok": False, "mode_at_execution": mode, "error": "edit_payment_draft requires RUN mode — switch modes first."}
    if not confirm:
        return {
            "ok": False, "mode_at_execution": mode,
            "error": "edit_payment_draft requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific edit.",
        }
    result = core.post(f"/api/payment-drafts/{int(draft_id)}/edit", {
        "new_amount": new_amount, "new_method": new_method,
        "new_payout_mobile": new_payout_mobile, "reason": reason,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": mode, "error": result["error"]}
    return {"ok": True, "mode_at_execution": mode, "confirmed": confirm, **result}


def reject_payment_draft(draft_id: int, reason: str = None, confirm: bool = False) -> dict:
    """Reject ONE pending payment draft. No transaction, no ledger. Same
    as the WhatsApp `DREJECT` admin command. Requires RUN mode AND
    confirm=True."""
    mode = _read_mode()
    if mode != "RUN":
        return {"ok": False, "mode_at_execution": mode, "error": "reject_payment_draft requires RUN mode — switch modes first."}
    if not confirm:
        return {
            "ok": False, "mode_at_execution": mode,
            "error": "reject_payment_draft requires explicit confirmation (confirm=true) "
            "after the admin has directly instructed this specific draft to be rejected.",
        }
    result = core.post(f"/api/payment-drafts/{int(draft_id)}/reject", {"reason": reason})
    if "error" in result:
        return {"ok": False, "mode_at_execution": mode, "error": result["error"]}
    return {"ok": True, "mode_at_execution": mode, "confirmed": confirm, **result}
