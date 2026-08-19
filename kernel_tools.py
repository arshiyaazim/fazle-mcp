"""
hermes_kernel decision/event log lookup for Hermes (2026-08-19) — thin,
read-only pass-throughs to fazle-core's own hermes_decision_audit_log/
hermes_event_log tables via fazle_core_client (same client identity_tools.py
uses).

Closes the second capability-exposure gap found in the same-day follow-up
to HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md:
modules/hermes_kernel has been writing one hermes_decision_audit_log row
per real process_message() turn since HERMES_DECISION_AUDIT_ENABLED was
flipped true in fazle-core's .env (2026-08-17) — chosen_action (the actual
reply-permission decision: reply/draft/clarification/escalation/no_action)
and explanation (a free-text workflow/domain tag) included — but no route
or tool ever read it. No new classification logic here.

No mode gate — same class as identity_tools.py's existing toolkit
(read-only, no side effects).
"""

import fazle_core_client as core


def lookup_decisions(phone: str = "", trace_id: str = "",
                      chosen_action: str = "", limit: int = 20) -> dict:
    """Look up past routing decisions from fazle-core's
    hermes_decision_audit_log — the same shadow record written for every
    real message turn. chosen_action tells you whether a message was
    auto-replied, held as a draft for admin approval, escalated, or
    ignored (the actual policy/reply-permission decision); explanation is
    a free-text tag for which workflow handled it (e.g.
    "escort-slip-review", "employee-payroll" — the closest thing to a
    domain/section label this system has); intent_method tells you
    whether intent came from the LLM-first classifier or its deterministic
    fallback for that specific message. Filter by phone and/or trace_id
    and/or chosen_action; all optional. Each row also includes
    source_message_id (added 2026-08-19, migration 067) — the bridge's
    own id for the inbound message that produced this decision, null for
    rows written before that column existed. This closes the message→
    decision hop only — it does NOT include which draft/payment/escort
    record (if any) resulted from the decision; that linkage still
    doesn't exist (see HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_
    AUDIT_2026-08-19.md Section C — decision→entity linkage is a
    separate, larger, still-open gap)."""
    return core.get("/api/hermes/decisions/lookup", {
        "phone": phone, "trace_id": trace_id,
        "chosen_action": chosen_action, "limit": limit,
    })


def lookup_kernel_events(trace_id: str, limit: int = 50) -> dict:
    """Look up the standard kernel event trail (EVT-001 message.received,
    etc.) for one decision's trace_id, from fazle-core's hermes_event_log.

    trace_id is the ONLY lookup key this tool accepts, and it is
    REQUIRED — there is no phone/name/free-text search parameter of any
    kind (do not invent one, e.g. "query"). You cannot search kernel
    events by who they're about; you must already have a real trace_id
    first. Get one from lookup_decisions(phone=...) — that tool DOES
    accept a phone filter."""
    return core.get("/api/hermes/kernel/events", {
        "trace_id": trace_id, "limit": limit,
    })
