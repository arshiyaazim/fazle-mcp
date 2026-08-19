"""
Employee ledger/transaction-history lookup for Hermes (2026-08-19, Task 3
of HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Thin, read-only pass-throughs to fazle-core's own existing
GET /api/fpe/ledger/{emp_id} and GET /api/fpe/employees/{emp_id}/transactions
routes (modules/fazle_payroll_engine/routes.py) — both already exist,
already viewer-tier, already used by the admin dashboard. No new
fazle-core route, no new business logic, no direct DB connection from
this process.

This closes the "Hermes can trigger a payment but cannot verify its
financial effect" gap named in that audit's Section D/M — once a payment
draft is approved (Task 2), Hermes can re-fetch the employee's ledger and
transaction history and independently confirm the transaction/ledger
entry actually appeared, instead of only trusting the approve call's own
inline response.

No mode gate — same class as identity_tools.py's/kernel_tools.py's
existing toolkit (read-only, no side effects).
"""

import fazle_core_client as core


def get_employee_ledger(emp_id: int, periods: int = 12) -> dict:
    """Per-accounting-period ledger for one employee: opening_balance,
    total_earned, total_paid, total_advance, closing_balance, txn_count,
    last_updated — most recent period first. periods is capped at 36
    server-side. Use this after approve_payment_draft (payment_draft_tools.py)
    to independently verify a payment's financial effect actually landed,
    rather than trusting the approval call's own inline response alone."""
    return core.get(f"/api/fpe/ledger/{int(emp_id)}", {"periods": periods})


def get_employee_transaction_history(
    emp_id: int, page: int = 1, page_size: int = 20,
    date_from: str = "", date_to: str = "",
) -> dict:
    """Paginated transaction history for one employee (aggregated across
    any soft-merged duplicate employee records), with an employee summary
    and total/first/last transaction stats. date_from/date_to are
    YYYY-MM-DD, optional. Excludes reversed transactions. Use this to find
    the specific transaction a payment-draft approval produced, or to
    answer "কোন payment কোন message থেকে এসেছে" style questions at the
    transaction level (still no message-level linkage — see
    HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md
    Section C for that separate, still-open gap)."""
    params = {"page": page, "page_size": page_size}
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to
    return core.get(f"/api/fpe/employees/{int(emp_id)}/transactions", params)
