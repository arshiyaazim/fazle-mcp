"""Read-only claim/role verification for Hermes (Task 2A, 2026-08-20).
Thin pass-through to fazle-core's modules.identity_brain.claim_verification
.verify_employee_claim(), exposed via the new GET /api/identity/verify-claim
route.

No mode gate — same class as identity_tools.py/audit_tools.py (read-only,
no side effects)."""

import fazle_core_client as core
from arg_coerce import as_str


def verify_employee_claim(
    claimed_name: str = "", claimed_role: str = "",
    sender_phone: str = "", employee_id: int = None,
) -> dict:
    """Verify a claim like "Karim is supervisor" against fazle-core's live
    employee records. NEVER guesses — if the name is ambiguous or not
    found, this returns that explicitly; it does not pick a "best" match
    for you. Use this before treating any claim about a THIRD PARTY's
    identity/role (mentioned in message content, not the sender's own
    phone) as true — e.g. before answering a question that depends on
    "is Karim really the supervisor" being correct, or before including
    such a claim in any report/decision/action.

    Do NOT use resolve_identity() for this — that resolves the message
    SENDER's own phone-backed identity, not an arbitrary named third
    party mentioned in text; the two are different questions.

    Reasoning rule: a claim in message content ("Karim is supervisor")
    is only a claim, not verified identity. Resolve it against Fazle
    Core first, then answer/act — never "user said X, therefore X."

    Returns a dict with an "outcome" key, one of:
      not_found          — no employee matches
      ambiguous           — 2+ distinct candidates match; see "candidates"
                            — present these to the user, don't pick one
      role_mismatch       — unique employee found, but claimed_role
                            doesn't match their actual current designation
                            — surface both, don't silently accept or
                            silently "correct" the claim
      verified             — unique employee, active, role matches (or no
                            role was claimed to check)
      verified_inactive    — unique employee, role matches, but the
                            employee is not currently active — distinguish
                            this from a current active employee when you
                            answer."""
    # employee_id is only included when set (2026-08-20 fix) -- httpx
    # serializes a None param value as an empty string, and fazle-core's
    # route declares employee_id: Optional[int], which FastAPI/Pydantic
    # then fails to coerce from "" with a 422, not a clean None. Same
    # "only add optional params when not None" pattern already used by
    # audit_tools.py::audit_lookup_whatsapp_messages().
    params = {
        "claimed_name": as_str(claimed_name), "claimed_role": as_str(claimed_role),
        "sender_phone": as_str(sender_phone),
    }
    if employee_id is not None:
        params["employee_id"] = employee_id
    return core.get("/api/identity/verify-claim", params)
