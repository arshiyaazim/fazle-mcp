"""
Client / Orders / Billing / Receivables tools for Hermes (2026-08-21, P1-B
of the Phase 4 coverage-audit follow-on -- see
HERMES_PHASE4_BUSINESS_DOMAIN_COVERAGE_AUDIT memory).

Thin pass-throughs to fazle-core's EXISTING modules/client_billing/routes.py
(/admin/client-billing-profiles/*, /admin/bills/*) -- profiles, bills,
outstanding/dashboard summaries, and payment-log reads are already
read-only there; generate/mark-paid/profile-create/update/delete are the
only writes, already validated (numeric fields, contact-mobile membership
in the ESCORT_CLIENT_PHONES allowlist, idempotent mark-paid) and audited
server-side. No new business logic here -- profit-estimate figures
(employee_wage_* fields) already exist server-side purely for the
ESTIMATION shown on a generated bill and must never be treated as, or fed
into, a real payroll/P&L figure (see modules/client_billing/profiles.py's
own docstring and CANONICAL_BUSINESS_RULES.md's Payroll section).

Why this module exists: the Phase 4 audit found the client-billing backend
was mature (profiles, bills, payments, dashboard) but Hermes had only one
exposed tool (get_billing_outstanding, server.py) and no write coverage at
all. This module closes that gap.

Mode gate: identical fail-closed _read_mode() logic to every other Task 2/4
tool module (deliberately duplicated, not shared).

Hard gate on every mutation (2026-08-21, Owner-directed -- explicitly NOT a
bare confirm=True): generate_bill/mark_bill_paid/create_client_billing_
profile/update_client_billing_profile/delete_client_billing_profile each
require a real approved_action_id whose task_tools.get_action() row has
status='approved' AND action_type='billing_mutation' -- identical shape to
dispatch_tools.py's hard gate and opencode_tools.py's opencode_dispatch. See
db/migrations/070_hermes_action_approvals_dispatch_billing_types.sql for the
schema widening that added 'billing_mutation' to hermes_action_approvals.
action_type.

Client resolution (Owner instruction, 2026-08-21): a bare client name must
never silently select an arbitrary record. find_client_billing_profile()
resolves by verified phone first (contact_person_mobile, matched by
mechanical last-10-digit comparison to tolerate 01XXXXXXXXX vs
8801XXXXXXXXX form -- NOT a reimplementation of fazle-core's own BD-phone
business normalization, which stays server-side); if only a name is given
and it matches more than one active profile, this returns
ambiguous=True with every candidate rather than guessing one."""

import json
import os

import fazle_core_client as core
import task_tools
from arg_coerce import as_str

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))

ACTION_TYPE = "billing_mutation"


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
    """Shared hard-gate check for every mutation below -- identical pattern
    to dispatch_tools.py/opencode_tools.py."""
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
                     "it must be explicitly approved by the admin before this billing action can run.",
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


def _result_from(core_response: dict, confirm: bool) -> dict:
    if "error" in core_response:
        out = {"ok": False, "mode_at_execution": "RUN", "error": core_response["error"]}
        detail = core_response.get("error_detail")
        if isinstance(detail, dict) and isinstance(detail.get("detail"), str):
            out["error"] = detail["detail"]
        return out
    return {"ok": True, "mode_at_execution": "RUN", "confirmed": confirm, **core_response}


def _last10(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


# ── Client resolution ──────────────────────────────────────────────────────

def find_client_billing_profile(phone: str | int = "", client_name: str = "") -> dict:
    """Resolve a client to its billing profile WITHOUT ever silently
    guessing. Pass phone when you have it -- it is matched against every
    active profile's verified contact_person_mobile (mechanical last-10-
    digit comparison, tolerant of 01XXXXXXXXX vs 8801XXXXXXXXX form). Pass
    client_name only when no phone is available; if it matches more than
    one active profile, returns {"ambiguous": true, "candidates": [...]} --
    you must then ask for a phone number or client_id, never pick one.
    Returns {"found": false} if nothing matches (never a 500/exception)."""
    phone = as_str(phone)
    if not phone and not client_name:
        return {"found": False, "error": "phone or client_name is required"}
    profiles = core.get("/admin/client-billing-profiles", {"active": True})
    if isinstance(profiles, dict) and "error" in profiles:
        return {"found": False, "error": profiles["error"]}
    rows = profiles.get("profiles", []) if isinstance(profiles, dict) else []

    if phone:
        target = _last10(phone)
        matches = [r for r in rows if _last10(r.get("contact_person_mobile", "")) == target and target]
        if len(matches) == 1:
            return {"found": True, "profile": matches[0]}
        if len(matches) > 1:
            return {"found": False, "ambiguous": True, "candidates": matches}
        # Fall through to name matching only if a name was ALSO given --
        # otherwise a phone that matches nothing is a clean "not found".
        if not client_name:
            return {"found": False}

    if client_name:
        name_lower = client_name.strip().lower()
        matches = [r for r in rows if name_lower in (r.get("client_name") or "").lower()]
        if len(matches) == 1:
            return {"found": True, "profile": matches[0]}
        if len(matches) > 1:
            return {
                "found": False, "ambiguous": True, "candidates": matches,
                "note": "More than one active client matches this name -- ask for a phone "
                        "number or client_id before proceeding, never assume the first match.",
            }
        return {"found": False}

    # Unreachable in practice: the upfront guard above already returns when
    # both phone and client_name are empty, and every other combination
    # returns from one of the two blocks above.
    return {"found": False}  # pragma: no cover


# ── Read-only (no gate) ────────────────────────────────────────────────────

def list_client_billing_profiles(active: bool = True) -> dict:
    """Read-only. List client billing profiles (rates, wages-for-estimate,
    contact info). active=False includes inactive/deleted-soft profiles
    too (there are none currently -- delete is a hard delete per Owner
    decision; kept for schema-forward-compat with list_client_billing_
    profiles's own active_only param)."""
    return core.get("/admin/client-billing-profiles", {"active": active})


def get_client_billing_profile(client_id: int) -> dict:
    """Read-only. One client billing profile's full detail (rates by duty
    type, wage-for-estimate fields, contact info)."""
    return core.get(f"/admin/client-billing-profiles/{int(client_id)}")


def get_client_outstanding(contact_id: int = None) -> dict:
    """Read-only. Pending (unpaid) bills, optionally scoped to one client's
    contact_id (get it from find_client_billing_profile()'s profile.
    contact_id -- may be null if the profile predates contact linkage, in
    which case use get_outstanding_summary() and match by client_name
    instead)."""
    params = {"contact_id": contact_id} if contact_id is not None else {}
    return core.get("/admin/bills/outstanding", params)


def get_outstanding_summary() -> dict:
    """Read-only. Per-client aggregated outstanding totals (pending bill
    count, total outstanding amount, oldest pending bill date) -- the "which
    clients owe how much" view. Answers "আজ/এই মাসে কত client receivable
    আছে" at the per-client level; there is no separate time-bucketed (today/
    this-month) receivable tool yet -- see remaining-gaps note in this
    pass's report."""
    return core.get("/admin/bills/outstanding/summary")


def get_billing_dashboard_summary() -> dict:
    """Read-only. Aggregate totals across ALL bills: pending/paid counts,
    outstanding amount, collected amount, total bill count."""
    return core.get("/admin/bills/dashboard-summary")


def get_bill(bill_id: int) -> dict:
    """Read-only. One bill's full detail (service_charge, other_charges,
    total_amount, payment_status, bill_number, client_name)."""
    return core.get(f"/admin/bills/{int(bill_id)}")


def get_bill_payment_log(bill_id: int) -> dict:
    """Read-only. Append-only payment audit trail for one bill."""
    return core.get(f"/admin/bills/{int(bill_id)}/payment-log")


def list_billable_programs() -> dict:
    """Read-only. Completed escort programs with an active client billing
    profile and not yet billed -- the selection pool for generate_bill()."""
    return core.get("/admin/bills/billable-programs")


def list_bills() -> dict:
    """Read-only. All bills (any payment_status), most recent first."""
    return core.get("/admin/bills")


# ── Mutations (approved_action_id + RUN mode + confirm=true) ──────────────

def generate_bill(
    program_id: int, duty_type: str, approved_action_id: int,
    other_charges: float = 0, remarks: str = None, confirm: bool = False,
) -> dict:
    """Generate an invoice-numbered bill from a Completed escort program
    through the real billing engine (modules.client_billing.bills.
    generate_bill) -- one bill per program (server-side unique-violation
    guard), duty_type must be one of general/24h/12h/monthly and the
    matching rate must already be configured on the client's billing
    profile (check get_client_billing_profile() first; if the rate is
    unconfigured this is rejected, never guessed). The program must be
    status='Completed' and linked to a contact_id with an active billing
    profile -- use list_billable_programs() to find eligible programs.

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='billing_mutation'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed generating this exact bill."""
    denial = _gate("generate_bill", confirm, approved_action_id)
    if denial:
        return denial
    body = {"program_id": program_id, "duty_type": duty_type, "other_charges": other_charges}
    if remarks:
        body["remarks"] = remarks
    result = core.post("/admin/bills/generate", body)
    return _result_from(result, confirm)


def mark_bill_paid(
    bill_id: int, approved_action_id: int, payment_method: str = None,
    payment_reference: str = None, remarks: str = None, confirm: bool = False,
) -> dict:
    """Record a bill as paid through the real payment-recording engine
    (modules.client_billing.payments.mark_bill_paid). Idempotent-rejects
    (ok=False) if the bill is already Paid rather than silently
    re-recording -- payment is binary (Pending/Paid only, no partial
    payments); paid_amount is always set to the bill's full total_amount.

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='billing_mutation'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed marking this exact bill paid."""
    denial = _gate("mark_bill_paid", confirm, approved_action_id)
    if denial:
        return denial
    body = {}
    if payment_method:
        body["payment_method"] = payment_method
    if payment_reference:
        body["payment_reference"] = payment_reference
    if remarks:
        body["remarks"] = remarks
    result = core.post(f"/admin/bills/{int(bill_id)}/mark-paid", body)
    return _result_from(result, confirm)


def create_client_billing_profile(
    client_name: str, contact_person_mobile: str, approved_action_id: int,
    confirm: bool = False, **fields,
) -> dict:
    """Create a new client billing profile (modules.client_billing.profiles.
    create_client_billing_profile). contact_person_mobile MUST already be
    in the live ESCORT_CLIENT_PHONES .env allowlist (server-side validated)
    -- add it there first via escort-roster's existing escort-clients
    mechanism if it isn't. fields accepts any subset of office_address/
    contact_person_name/chairman_name/chairman_mobile/managing_member_name/
    managing_member_mobile/general_rate/rate_24h/rate_12h/rate_monthly/
    employee_wage_general/employee_wage_24h/employee_wage_12h/
    employee_wage_monthly/vat_amount/tax_amount/other_charges/notes/
    contact_id.

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='billing_mutation'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed creating this exact profile."""
    denial = _gate("create_client_billing_profile", confirm, approved_action_id)
    if denial:
        return denial
    body = {"client_name": client_name, "contact_person_mobile": contact_person_mobile, **fields}
    result = core.post("/admin/client-billing-profiles", body)
    return _result_from(result, confirm)


def update_client_billing_profile(client_id: int, approved_action_id: int, confirm: bool = False, **fields) -> dict:
    """Update an existing client billing profile (any subset of its
    fields, same set as create_client_billing_profile's **fields, plus
    client_name/contact_person_mobile themselves).

    Requires ALL of: (1) approved_action_id pointing at a real
    hermes_action_approvals row with status='approved' AND
    action_type='billing_mutation'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly instructed this exact change."""
    denial = _gate("update_client_billing_profile", confirm, approved_action_id)
    if denial:
        return denial
    result = core.put(f"/admin/client-billing-profiles/{int(client_id)}", fields)
    return _result_from(result, confirm)


def delete_client_billing_profile(client_id: int, approved_action_id: int, confirm: bool = False) -> dict:
    """Hard delete a client billing profile (modules.client_billing.
    profiles.delete_client_billing_profile) -- server-side superadmin-tier
    gate already applies to this route independently of Hermes's own gate
    below; bills already generated against this client are unaffected
    (wbom_billing_records stores its own snapshot, not a live reference).

    *** Irreversible. *** Requires ALL of: (1) approved_action_id pointing
    at a real hermes_action_approvals row with status='approved' AND
    action_type='billing_mutation'; (2) RUN mode; (3) confirm=true after
    the admin has explicitly and specifically instructed this exact
    deletion."""
    denial = _gate("delete_client_billing_profile", confirm, approved_action_id)
    if denial:
        return denial
    result = core.delete(f"/admin/client-billing-profiles/{int(client_id)}")
    return _result_from(result, confirm)
