"""
Identity/intent resolution for Hermes (2026-08-18) — thin, read-only
pass-throughs to fazle-core's own identity_brain/intent modules via
fazle_core_client (same client audit_tools.py uses, since these need live
async DB calls, not assistant-backend's passive fazleBridge.js views).

Closes the capability-exposure gap found in
HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md: no
existing tool reached identity_brain.detect_identity() or modules.intent
.classify() even though both are the same live logic every real WhatsApp
message goes through in message_router. No new classification logic here.

No mode gate — same class as audit_tools.py's existing toolkit (read-only,
no side effects).
"""

import fazle_core_client as core


def resolve_identity(phone: str, text: str = "") -> dict:
    """Resolve a phone number's role/identity using fazle-core's live
    identity_brain — the same resolver every real WhatsApp message goes
    through (message_router, bridge_poller). Use this instead of manually
    cross-referencing get_contacts/get_employees, which are unfiltered
    listings, are not phone-variant aware, and can miss employees who only
    exist via the fpe_employees payroll-linked fallback. Returns role,
    identity_confidence (0-100), identity_source, and employee context
    (employee_id/name/designation) when matched. text is optional — passing
    the actual message improves resolution for text-hint-only signals
    (candidate detection, escort-content matching)."""
    return core.get("/api/identity/resolve", {"phone": phone, "text": text})


def classify_intent(text: str) -> dict:
    """Classify a message's intent using fazle-core's live deterministic
    keyword/regex/fuzzy classifier — the same one message_router uses for
    real messages. No LLM call, no DB access. One of: recruitment,
    salary_query, payment_due, escort_duty, complaint, client_order, leave,
    join, attendance, slip_submission, greeting, office_location, unknown."""
    return core.get("/api/intent/classify", {"text": text})
