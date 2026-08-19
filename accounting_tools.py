"""
Accounting / FPE operator-review write tools for Hermes (2026-08-19, Task
4 of HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Thin pass-throughs to fazle-core's existing
modules/fazle_payroll_engine/routes.py (GET /api/fpe/operator/pending,
POST /api/fpe/operator/{id}/approve|reject, POST /api/fpe/transactions/
manual, POST /api/fpe/transactions/{id}/reverse) -- all already exist,
already _require_admin_role-gated server-side, already production-tested.
No new business logic.

operator approve/reject take employee_id/reviewer/reason as REQUIRED,
caller-supplied query params -- matches those routes' own existing
contract exactly (same reasoning as employee_tools.py's edit-request
approve/reject).

Mode gate: identical fail-closed _read_mode() logic to the other Task
2/4 tool modules (deliberately duplicated, not shared). Every mutation
here requires RUN mode AND confirm=True -- this touches real cash
transactions, same risk tier as approve_payment_draft.
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


def get_operator_pending(limit: int = 50, offset: int = 0) -> dict:
    """Read-only, no mode gate. Pending FPE operator-review submissions
    (fpe_unmatched_messages awaiting admin approval) -- use this to find a
    pending_id to pass to approve_operator_pending/reject_operator_pending."""
    return core.get("/api/fpe/operator/pending", {"limit": limit, "offset": offset})


def approve_operator_pending(
    pending_id: int, employee_id: int, reviewer: str,
    payout_method: str = None, txn_category: str = "salary",
    amount_override: float = None, note: str = None, confirm: bool = False,
) -> dict:
    """Approve a pending FPE operator submission -- promotes it to a final
    transaction and updates the employee ledger. employee_id and reviewer
    are required (this route's own contract). Requires RUN mode AND
    confirm=true."""
    denial = _gate("approve_operator_pending", confirm)
    if denial:
        return denial
    params = {"employee_id": employee_id, "reviewer": reviewer, "txn_category": txn_category}
    if payout_method is not None:
        params["payout_method"] = payout_method
    if amount_override is not None:
        params["amount_override"] = amount_override
    if note is not None:
        params["note"] = note
    result = core.post(f"/api/fpe/operator/{int(pending_id)}/approve", params=params)
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def reject_operator_pending(pending_id: int, reviewer: str, reason: str, confirm: bool = False) -> dict:
    """Reject a pending FPE operator submission. No transaction, no
    ledger touched. reviewer and reason are both required. Requires RUN
    mode AND confirm=true."""
    denial = _gate("reject_operator_pending", confirm)
    if denial:
        return denial
    result = core.post(
        f"/api/fpe/operator/{int(pending_id)}/reject",
        params={"reviewer": reviewer, "reason": reason},
    )
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def create_manual_transaction(
    employee_id: int, amount: float, payout_method: str, txn_date: str,
    payout_phone: str = None, txn_category: str = "salary", reason: str = "",
    confirm: bool = False,
) -> dict:
    """Create a manual cash transaction for an employee (bypasses the
    normal message-parsing pipeline -- use only when the admin explicitly
    asks for a direct, manual entry). txn_date is YYYY-MM-DD. Requires
    RUN mode AND confirm=true."""
    denial = _gate("create_manual_transaction", confirm)
    if denial:
        return denial
    result = core.post("/api/fpe/transactions/manual", {
        "employee_id": employee_id, "amount": amount, "payout_method": payout_method,
        "payout_phone": payout_phone, "txn_date": txn_date,
        "txn_category": txn_category, "reason": reason,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}


def reverse_transaction(txn_id: int, reason: str, created_by: str = "admin", confirm: bool = False) -> dict:
    """Reverse an existing cash transaction. Requires RUN mode AND
    confirm=true. Use get_employee_transaction_history first to confirm
    which transaction is actually being reversed."""
    denial = _gate("reverse_transaction", confirm)
    if denial:
        return denial
    result = core.post(f"/api/fpe/transactions/{int(txn_id)}/reverse", {
        "txn_id": txn_id, "reason": reason, "created_by": created_by,
    })
    if "error" in result:
        return {"ok": False, "mode_at_execution": "RUN", "error": result["error"]}
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **result}
