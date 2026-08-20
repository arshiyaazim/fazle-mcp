"""
MCP (stdio) server wrapping assistant-backend's fazleBridge.js endpoints —
gives Hermes proper, audited access to fazle-core's 10 read-only ai_read_*
views, instead of it having to (or being tempted to) self-service DB
credentials via its own terminal tool.

Auth: logs in as a dedicated, minimal-privilege service account
(hermes-mcp-svc, requireApproved only — not admin) via the same
POST /api/auth/login every real user uses. No new auth mechanism, no
duplicated DB credentials — reuses the already-audited HTTP surface.
"""

# mcp SDK v2.0.0 renamed FastMCP -> MCPServer (mcp.server.mcpserver), not the
# older 1.x mcp.server.fastmcp path most examples online still show.
from mcp.server.mcpserver import MCPServer

# Task 1/2 (metrics + structured reporting), Task 4 (audit toolkit), Task 5
# (scheduler execution) — implementation lives in separate modules to keep
# this file from becoming unwieldy; every tool is still registered here in
# one place, matching the existing single-registration-point style below.
import accounting_tools
import admin_directive_tools
import assistant_bridge_client
import attendance_tools
import audit_tools
import claim_verification_tools
import domain_reports
import draft_tools
import employee_tools
import escort_roster_tools
import identity_tools
import kernel_tools
import ledger_tools
import metrics_tools
import mode_tools
import monitoring_tools
import opencode_tools
import operational_tools
import payment_draft_tools
import scheduler_tools
import send_whatsapp_tools
import task_tools

# Thin alias — this module's original inline _get() was extracted to
# assistant_bridge_client.py (Phase 2, 2026-08-04) so domain_reports.py can
# reuse the same authenticated client. Kept as _get here to avoid touching
# every call site below for a pure rename.
_get = assistant_bridge_client.get


mcp = MCPServer("fazle-core")


# ── Capability Expansion Level 1 — self-inventory (2026-08-10) ──────────
# Addresses a real UX gap found during investigation: this server's tools
# sit behind MCP tool-search deferral once past a context-size threshold
# (30+ tools), so there was no reliable way for Hermes to enumerate its
# own fazle-core capabilities other than repeated, uncertain tool_search
# calls. Reads the live tool registry directly (mcp._tool_manager._tools)
# rather than a hand-maintained list, so it can never drift out of sync
# with what's actually registered below.

@mcp.tool()
def list_my_capabilities() -> dict:
    """List every tool you (Hermes) currently have available on this
    fazle-core MCP server — name, one-line purpose, and required
    parameters for each. Use this instead of guessing whether a
    capability exists or repeatedly tool_search-ing for it; this reflects
    exactly what's registered right now."""
    try:
        tools = mcp._tool_manager._tools
    except AttributeError:
        return {"error": "capability introspection unavailable in this MCP SDK version"}
    result = []
    for name, t in sorted(tools.items()):
        schema = t.parameters or {}
        result.append({
            "name": name,
            "description": (t.description or "").strip(),
            "required_params": schema.get("required", []),
        })
    return {"tool_count": len(result), "tools": result}


@mcp.tool()
def get_contacts(limit: int = 30) -> list:
    """List contacts known to fazle-core: name, WhatsApp number, relation,
    company. No search-by-name/phone parameter exists — this returns an
    unfiltered batch of up to `limit` contacts (raising `limit` gets more
    of them, but there is NO offset/cursor/page parameter, so you cannot
    page past whatever `limit` you pass). To find a specific person's
    number, prefer resolve_identity(phone=...) if you already have a real
    number, or scan this list's returned names yourself with a larger
    `limit` — do not assume a "next page" exists."""
    return _get("/contacts", {"limit": limit})


@mcp.tool()
def get_employees(status: str = "active") -> list:
    """List fazle-core employees with designation and status. status:
    'active' or 'inactive'. No name/phone search parameter exists — this
    returns every employee matching `status`, unfiltered; there is no way
    to search for one specific employee by name or number through this
    tool. For a specific known phone number, use resolve_identity();
    to search/find someone by name or a partial phone, use
    search_employees()/suggest_employees() instead — those ARE real
    search tools, this one is not."""
    return _get("/employees", {"status": status})


@mcp.tool()
def search_employees(query: str | int, limit: int = 20) -> dict:
    """Search employees by phone, employee_id_phone, name, or alias --
    the actual search tool (get_employees/get_contacts cannot search at
    all). Matches exact phone/employee_id_phone, exact/alias name, then
    fuzzy-name (threshold 90) as a fallback. Each result includes
    match_type so you know how confident the match is. Use this whenever
    the admin refers to someone by name and you don't already have their
    exact number from this conversation."""
    return employee_tools.search_employees(query, limit)


@mcp.tool()
def suggest_employees(query: str | int, limit: int = 8) -> dict:
    """Type-ahead/autocomplete employee search for partial/ambiguous
    input -- misspelled names, or a partial phone fragment like "...1596"
    with no other context. pg_trgm fuzzy matching, 6-tier priority
    scoring fallback. Prefer search_employees() for a fuller query with
    match_type detail; use this one for genuinely partial input."""
    return employee_tools.suggest_employees(query, limit)


@mcp.tool()
def get_recent_messages(limit: int = 10) -> list:
    """Fetch recent WhatsApp messages seen by fazle-core."""
    return _get("/messages", {"limit": limit})


@mcp.tool()
def get_knowledge_base(category: str = "") -> list:
    """Search fazle-core's internal knowledge base articles by category."""
    return _get("/kb", {"category": category} if category else {})


@mcp.tool()
def get_attendance(limit: int = 30) -> list:
    """Look up employee attendance records, most recent first."""
    return _get("/attendance", {"limit": limit})


@mcp.tool()
def get_billing_outstanding(limit: int = 30) -> list:
    """List outstanding/unpaid billing records."""
    return _get("/billing-outstanding", {"limit": limit})


@mcp.tool()
def get_cash_transactions(limit: int = 30, date: str = "", status: str = "") -> list:
    """List real cash transactions (salary, advances, bonuses, deductions,
    corrections) from fpe_cash_transactions — the sole canonical cash
    ledger (Owner Directive 2026-06-29). Never mixes in
    wbom_cash_transactions, which is legacy/archive only. date: optional
    YYYY-MM-DD. status: optional transaction_status filter.

    Re-enabled 2026-08-10: was disabled (returned a static "not yet
    available" error, never queried anything) while its backing view,
    ai_read_cash_transactions, didn't exist. The Owner has since applied
    core/db/migrations/063_ai_read_cash_transactions.sql directly — live-
    verified (view exists, fazle_ai_reader has SELECT, this exact _get()
    call already returns real rows through the full path). Restored to
    its original, pre-disable implementation."""
    params = {"limit": limit}
    if date:
        params["date"] = date
    if status:
        params["status"] = status
    return _get("/cash-transactions", params)


@mcp.tool()
def get_escort_programs(limit: int = 30) -> list:
    """List escort programs (vessel escort assignments), most recent first."""
    return _get("/escort-programs", {"limit": limit})


@mcp.tool()
def get_module_bridge_status() -> list:
    """Check fazle-core's internal service heartbeats (last seen, queue depth)."""
    return _get("/module-bridge-status")


@mcp.tool()
def get_mode_state() -> dict:
    """Current Hermes operating mode (READ/BUILD/RUN) and, if a Break-Glass
    TTL is active, seconds remaining before it auto-reverts to READ. Zero
    arguments, read-only, no secrets — call this instead of guessing when
    asked "what mode are you in" or "how much TTL is left"."""
    return mode_tools.get_mode_state()


@mcp.tool()
def get_payroll_runs(limit: int = 20) -> list:
    """List payroll run records per employee per period, most recent first."""
    return _get("/payroll-runs", {"limit": limit})


@mcp.tool()
def get_recruitment_leads(limit: int = 20) -> list:
    """List recruitment funnel leads."""
    return _get("/recruitment-leads", {"limit": limit})


# ── Task 1/2 — message-flow metrics + structured reporting ──────────────
# Calls fazle-core's OWN app directly (127.0.0.1:8200, a NEW integration —
# see fazle_core_client.py), not fazleBridge.js like the tools above. No
# fazle-core code was changed; these are new callers of its existing,
# already-key-gated /api/social/* routes.

@mcp.tool()
def get_social_status() -> dict:
    """Message-flow health snapshot: queue depth, sent/failed/flagged counts,
    rate-limit state, from fazle-core's social_auto_reply state tracker."""
    return metrics_tools.get_social_status()


@mcp.tool()
def get_social_queue(limit: int = 50) -> dict:
    """Pending/in-flight outbound social messages, most recent first. Phone
    numbers in target_id are masked per PII policy."""
    return metrics_tools.get_social_queue(limit)


@mcp.tool()
def get_social_flagged(limit: int = 50) -> dict:
    """Flagged/held social messages needing review, most recent first. Phone
    numbers in target_id are masked per PII policy."""
    return metrics_tools.get_social_flagged(limit)


@mcp.tool()
def get_social_report() -> dict:
    """One deterministic structured report on social/message-flow health —
    status, metrics, findings, risk, recommendations — built directly
    rather than left to free-form narration of the three tools above."""
    return metrics_tools.get_social_report()


# ── Task 4 — read-only audit toolkit ─────────────────────────────────────
# Rooted to AUDIT_ROOTS (assistant-platform, fazle-core), argv-list
# subprocess calls only (no shell=True anywhere), path-traversal-safe.
# Always-available (same toolset as everything above) — these are
# inherently read-only/bounded, matching Chat's existing read-only
# philosophy, so no Hermes mode gate is needed here.

@mcp.tool()
def audit_search_code(query: str | int, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search source code for a literal string. root: 'assistant-platform'
    (Node/React admin UI) or 'fazle-core' (Python backend, business logic,
    reply templates) -- pick the repo that actually contains what you're
    looking for; a business-logic/template query almost always belongs
    under 'fazle-core', not the default."""
    return audit_tools.audit_search_code(query, root, max_results)


@mcp.tool()
def audit_search_docs(query: str | int, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search markdown documentation for a literal string."""
    return audit_tools.audit_search_docs(query, root, max_results)


@mcp.tool()
def audit_search_kb(query: str | int, max_results: int = 20) -> dict:
    """Search fazle-core's knowledge_base/ for a literal string."""
    return audit_tools.audit_search_kb(query, max_results)


@mcp.tool()
def audit_search_logs(query: str | int = "", log: str = "backend", max_lines: int = 100) -> dict:
    """Search a known log ('backend', 'fazle-core', or 'hermes-runner') for
    a literal string (or tail it if query is empty). Secrets and phone
    numbers are redacted from results. query must be the exact literal
    text you expect to find, not a paraphrase or description of it."""
    return audit_tools.audit_search_logs(query, log, max_lines)


@mcp.tool()
def audit_read_file(path: str, root: str = "assistant-platform", start_line: int | None = None, end_line: int | None = None, max_lines: int = 500) -> dict:
    """Read a file (optionally a bounded line range) from an approved root.
    Refuses secret/credential-shaped paths and enforces a size cap."""
    return audit_tools.audit_read_file(path, root, start_line, end_line, max_lines)


@mcp.tool()
def audit_lookup_whatsapp_messages(phone: str | int = "", platform: str = "", is_processed: bool | None = None, message_id: int | None = None, limit: int = 20) -> dict:
    """Filtered WhatsApp message lookup for incident investigation: by phone
    number, platform (bridge1/bridge2/bridge3/meta/whatsapp), processed
    status, or message_id. Phone-shaped fields are PII-masked per policy."""
    return audit_tools.audit_lookup_whatsapp_messages(phone, platform, is_processed, message_id, limit)


@mcp.tool()
def resolve_identity(phone: str | int, text: str = "") -> dict:
    """Resolve a phone number's role/identity using fazle-core's live
    identity_brain — the same resolver every real WhatsApp message goes
    through. Use this instead of manually cross-referencing get_contacts/
    get_employees, which are unfiltered listings and can miss FPE-linked
    employees. Returns role, identity_confidence (0-100), identity_source,
    and employee context when matched. text is optional — pass the actual
    message for better text-hint-only resolution."""
    return identity_tools.resolve_identity(phone, text)


@mcp.tool()
def classify_intent(text: str) -> dict:
    """Classify a message's intent using fazle-core's deterministic
    keyword/regex/fuzzy classifier. Note: real message_router routing is
    LLM-first and only falls back to this classifier when the LLM returns
    "unknown" — this is an approximation of live routing, not an exact
    replay. Use lookup_decisions() for the actual intent+method a specific
    past message was routed with. No LLM call here."""
    return identity_tools.classify_intent(text)


@mcp.tool()
def verify_employee_claim(
    claimed_name: str = "", claimed_role: str = "",
    sender_phone: str = "", employee_id: int = None,
) -> dict:
    """Verify a claim like "Karim is supervisor" against fazle-core's live
    employee records. NEVER guesses — ambiguous/not-found is returned
    explicitly, never a picked "best" match. Use this before treating any
    THIRD-PARTY name/role claim mentioned in message content as true —
    do NOT use resolve_identity() for this (that resolves the message
    SENDER's own phone-backed identity, a different question). Returns
    one of: not_found, ambiguous (+ candidates list), role_mismatch (+
    claimed vs actual designation), verified, verified_inactive (role
    matches but the employee is not currently active)."""
    return claim_verification_tools.verify_employee_claim(
        claimed_name, claimed_role, sender_phone, employee_id,
    )


@mcp.tool()
def lookup_decisions(phone: str | int = "", trace_id: str | int = "",
                      chosen_action: str = "", limit: int = 20) -> dict:
    """Look up past routing decisions from fazle-core's live
    hermes_decision_audit_log — one row per real message turn since
    2026-08-17. chosen_action is the actual reply-permission decision
    (reply/draft/clarification/escalation/no_action); explanation is a
    free-text workflow/domain tag (e.g. "escort-slip-review",
    "employee-payroll"); intent_method shows whether intent came from the
    LLM-first classifier or its deterministic fallback for that message.
    Filter by phone and/or trace_id and/or chosen_action; all optional."""
    return kernel_tools.lookup_decisions(phone, trace_id, chosen_action, limit)


@mcp.tool()
def lookup_kernel_events(trace_id: str | int, limit: int = 50) -> dict:
    """trace_id is the ONLY parameter -- there is no "query"/phone/name
    param, do not invent one. Look up the standard kernel event trail
    (EVT-001 message.received, etc.) for one decision's trace_id, from
    fazle-core's hermes_event_log. Get trace_id from lookup_decisions()
    first."""
    return kernel_tools.lookup_kernel_events(trace_id, limit)


@mcp.tool()
def get_employee_ledger(emp_id: int, periods: int = 12) -> dict:
    """Per-accounting-period ledger for one employee: opening_balance,
    total_earned, total_paid, total_advance, closing_balance, txn_count,
    last_updated — most recent period first (periods capped at 36). Use
    this after approve_payment_draft to independently verify a payment's
    financial effect actually landed."""
    return ledger_tools.get_employee_ledger(emp_id, periods)


@mcp.tool()
def get_employee_transaction_history(
    emp_id: int, page: int = 1, page_size: int = 20,
    date_from: str = "", date_to: str = "",
) -> dict:
    """Paginated transaction history for one employee (aggregated across
    soft-merged duplicate records), with an employee summary and
    total/first/last transaction stats. date_from/date_to are YYYY-MM-DD,
    optional. Excludes reversed transactions."""
    return ledger_tools.get_employee_transaction_history(
        emp_id, page, page_size, date_from, date_to
    )


@mcp.tool()
def audit_git_status(repo: str = "assistant-platform") -> dict:
    """Read-only git status for an approved repo ('assistant-platform' or 'fazle-core')."""
    return audit_tools.audit_git_status(repo)


@mcp.tool()
def audit_recent_commits(repo: str = "assistant-platform", limit: int = 10) -> dict:
    """Read-only recent commit log for an approved repo."""
    return audit_tools.audit_recent_commits(repo, limit)


@mcp.tool()
def audit_get_auto_reply_settings() -> dict:
    """Read the LIVE auto-reply runtime toggles (auto_reply.all,
    auto_reply.recruitment, auto_reply.employee, etc. — fazle_runtime_settings)
    that actually govern WhatsApp auto-reply behavior right now. This is the
    authoritative live value — distinct from, and NOT reliably inferable
    from, the .env static defaults or app/config.py's Settings class
    defaults (a .env/code-default value is a starting point, never proof of
    the current live state). Use this before making any claim about whether
    auto-reply/SAFE MODE is currently on or off for any role."""
    return audit_tools.audit_get_auto_reply_settings()


@mcp.tool()
def get_settings_status() -> dict:
    """Non-secret STATIC fazle-core config: which AI provider is
    configured, model names, DRAFT_QUALITY_GATE, scheduler timezone, and a
    handful of other feature flags — resolved once at process start, NOT
    the live DB toggles (use audit_get_auto_reply_settings for those).
    Allowlist-only, no secrets, no filesystem paths, no phone numbers."""
    return audit_tools.get_settings_status()


@mcp.tool()
def audit_get_drafts(phone: str | int = "", status: str = "pending,pending_selfie,edited", limit: int = 20) -> dict:
    """Read-only: list pending WhatsApp draft replies (fazle_draft_replies),
    including the FULL reply_text — not just the draft ID. Use this before
    recommending or describing what a draft says; do not guess or
    reconstruct a draft's content from other tools. optional phone filter.
    This can never approve/send/edit/delete anything — see approve_draft
    for the separate, explicitly-gated action that actually sends."""
    return audit_tools.audit_get_drafts(phone, status, limit)


# ── Task 5 — controlled scheduler task execution ─────────────────────────
# Allowlist-only, mode-gated (reads hermes-runner's own mode file), and
# reuses Hermes's existing confirm-before-destructive gate for DESTRUCTIVE-
# tier jobs. See scheduler_tools.py for the full allowlist/risk tiers and
# the mode-gating rationale.

@mcp.tool()
def run_scheduled_task(job_name: str, confirm: bool = False) -> dict:
    """Trigger one of fazle-core's existing, pre-approved scheduler jobs by
    name — NOT arbitrary execution. Requires at least BUILD mode; DESTRUCTIVE
    jobs (e.g. daily_payroll_compute, payment_reconciliation) require RUN
    mode AND confirm=true, which must only be set after asking the user."""
    return scheduler_tools.run_scheduled_task(job_name, confirm)


@mcp.tool()
def list_available_tasks() -> dict:
    """List every scheduler job Hermes is allowed to trigger, with its risk tier."""
    return scheduler_tools.list_available_tasks()


# ── Phase 4 — Hermes -> OpenCode handoff (2026-08-10) ─────────────────────
# Mode-gated the same way as run_scheduled_task above (RUN mode + explicit
# confirm=true for the write-capable call). See opencode_tools.py for the
# full auth/mode-gating rationale.

@mcp.tool()
def opencode_dispatch(prompt: str, approved_action_id: int, confirm: bool = False) -> dict:
    """Hand a diagnosed problem to OpenCode (a separate, sandboxed coding
    agent, full-VPS-scope) to implement — NOT Earth writing code directly.
    Requires an approved_action_id from propose_action(action_type=
    "opencode_dispatch", ...) that the admin has explicitly approved
    ("APPROVE ACTION <id>"), AND RUN mode, AND confirm=true. Review the
    result afterward with audit_git_status / audit_recent_commits, then
    call record_execution_result."""
    return opencode_tools.opencode_dispatch(prompt, approved_action_id, confirm)


@mcp.tool()
def opencode_check(session_id: str) -> dict:
    """Read-only: fetch an OpenCode session's message history — follow up
    on an opencode_dispatch call or review a past result. No gating."""
    return opencode_tools.opencode_check(session_id)


# ── Capability Expansion Level 1 — WhatsApp reply drafting (2026-08-10) ──
# No mode-gate: this can only ever create a *pending* draft, never send —
# see draft_tools.py for why that makes it categorically different from
# opencode_dispatch/run_scheduled_task above.

@mcp.tool()
def draft_whatsapp_reply(
    recipient: str,
    bridge: str,
    draft_text: str,
    role: str = "unknown",
    intent: str = "hermes_suggested",
    context: str = "",
) -> dict:
    """Propose a WhatsApp reply as a pending draft for human review — this
    NEVER sends anything. recipient: phone number. bridge: bridge1/bridge2/
    bridge3/meta (which inbound channel this relates to). The draft is
    approved/edited/sent only by a human via the existing drafts dashboard.
    Do not tell the user you've sent something — say you've drafted it for
    their review."""
    return draft_tools.draft_whatsapp_reply(recipient, bridge, draft_text, role, intent, context)


# ── Capability Expansion Level 2 — admin-commanded WhatsApp send ─────────
# Requires RUN mode + confirm=true, same pattern as opencode_dispatch.
# NOT part of the customer-facing auto-reply pipeline — see
# send_whatsapp_tools.py for the full design rationale.

@mcp.tool()
def send_whatsapp_message(
    recipient: str,
    body: str,
    source_bridge: str = "bridge2",
    admin_instruction: str = "",
    confirm: bool = False,
) -> dict:
    """Send ONE WhatsApp message to ONE recipient — ONLY when the admin
    has explicitly instructed this send in the current turn. Requires RUN
    mode AND confirm=true, set only after that explicit instruction. Pass
    admin_instruction (the admin's own words) for the audit trail. This is
    NOT for automated customer replies — use draft_whatsapp_reply for
    anything that isn't a direct admin command to send right now."""
    return send_whatsapp_tools.send_whatsapp_message(
        recipient, body, source_bridge, admin_instruction, confirm
    )


@mcp.tool()
def approve_draft(draft_id: int, admin_instruction: str = "", confirm: bool = False) -> dict:
    """Approve and send ONE existing pending draft reply, by ID — ONLY
    when the admin has explicitly instructed this send in the current
    turn. Requires RUN mode AND confirm=true, same gate as
    send_whatsapp_message. ALWAYS call audit_get_drafts first and actually
    read the draft's reply_text before approving it — never approve a
    draft you have not read this turn. Reuses fazle-core's own existing
    draft-approval endpoint (the same one the admin dashboard's Approve
    button uses), so nothing about the draft/approval system is
    duplicated."""
    return send_whatsapp_tools.approve_draft(draft_id, admin_instruction, confirm)


# ── Task 2, 2026-08-19 — payment draft (fazle_payment_drafts) actions ────
# Closes the headline capability-exposure gap from
# HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md: the
# canonical approve/edit/reject chain existed and was production-tested
# but was only reachable via the admin's own WhatsApp text commands
# (APPROVED/DREDIT/DREJECT). Mutations require RUN mode + confirm=true,
# same risk tier as send_whatsapp_message — this moves real money.

@mcp.tool()
def get_payment_drafts(phone: str | int = "", status: str = "pending", limit: int = 20) -> dict:
    """Read-only. List fazle_payment_drafts (a different table from
    fazle_draft_replies -- use audit_get_drafts for reply drafts). status
    defaults to "pending" (pass "" for all). ALWAYS call this and read a
    draft's full row before approving/editing/rejecting it."""
    return payment_draft_tools.get_payment_drafts(phone, status, limit)


@mcp.tool()
def get_payment_draft_audit(draft_id: int) -> dict:
    """Read-only. All audit events recorded for one payment draft."""
    return payment_draft_tools.get_payment_draft_audit(draft_id)


@mcp.tool()
def get_payment_draft_employee_balance(employee_id: int, period: str = "") -> dict:
    """Read-only. One employee's ledger balance for a given accounting
    period (defaults to current period), sourced from fpe_employee_ledger."""
    return payment_draft_tools.get_payment_draft_employee_balance(employee_id, period)


@mcp.tool()
def approve_payment_draft(draft_id: int, amount: float, method: str, confirm: bool = False) -> dict:
    """Approve ONE pending payment draft -- creates the canonical
    transaction + ledger entry, exactly the same as the WhatsApp
    `APPROVED <id> <amount> <method>` admin command. Requires RUN mode AND
    confirm=true. ALWAYS call get_payment_drafts first and read the
    draft's full row before approving it. After approving, use
    get_employee_ledger/get_employee_transaction_history to independently
    verify the transaction and ledger entry actually appeared."""
    return payment_draft_tools.approve_payment_draft(draft_id, amount, method, confirm)


@mcp.tool()
def edit_payment_draft(
    draft_id: int,
    new_amount: float = None,
    new_method: str = None,
    new_payout_mobile: str = None,
    reason: str = None,
    confirm: bool = False,
) -> dict:
    """Edit ONE pending payment draft (version increment, before/after
    state saved). Does NOT create a transaction -- approve_payment_draft
    is still required afterward. Requires RUN mode AND confirm=true."""
    return payment_draft_tools.edit_payment_draft(
        draft_id, new_amount, new_method, new_payout_mobile, reason, confirm
    )


@mcp.tool()
def reject_payment_draft(draft_id: int, reason: str = None, confirm: bool = False) -> dict:
    """Reject ONE pending payment draft. No transaction, no ledger.
    Requires RUN mode AND confirm=true."""
    return payment_draft_tools.reject_payment_draft(draft_id, reason, confirm)


# ── Task 4, 2026-08-19 — attendance/employee/escort-roster/accounting ────
# Thin pass-throughs to existing, unmodified Fazle-Core routes. Every
# mutation requires RUN mode + confirm=true, same risk tier as
# send_whatsapp_message/approve_payment_draft. See each tool module's own
# docstring for the exact underlying route.

@mcp.tool()
def create_attendance(
    employee_id: int, attendance_date: str, duty_status: str,
    remarks: str = None, confirm: bool = False,
) -> dict:
    """Create a final attendance record. attendance_date is YYYY-MM-DD.
    Requires RUN mode AND confirm=true. Prefer create_attendance_draft
    unless the admin explicitly asked for a direct, final record."""
    return attendance_tools.create_attendance(employee_id, attendance_date, duty_status, remarks, confirm)


@mcp.tool()
def create_attendance_draft(
    employee_id: int, attendance_date: str, duty_status: str,
    remarks: str = None, confirm: bool = False,
) -> dict:
    """Create a PENDING attendance draft, never a final record. Requires
    RUN mode AND confirm=true."""
    return attendance_tools.create_attendance_draft(employee_id, attendance_date, duty_status, remarks, confirm)


@mcp.tool()
def update_attendance(
    attendance_id: int, duty_status: str = None, remarks: str = None, confirm: bool = False,
) -> dict:
    """Update an existing attendance record. Requires RUN mode AND
    confirm=true."""
    return attendance_tools.update_attendance(attendance_id, duty_status, remarks, confirm)


@mcp.tool()
def delete_attendance(attendance_id: int, confirm: bool = False) -> dict:
    """Delete an attendance record. Requires RUN mode AND confirm=true."""
    return attendance_tools.delete_attendance(attendance_id, confirm)


@mcp.tool()
def remember_business_rule(
    subject_type: str, subject_key: str, directive_text: str,
    category: str = "general", confirm: bool = False,
) -> dict:
    """Persist an admin's explicit, current business-rule instruction as an
    active directive that overrides the matching hardcoded default at its
    read site (e.g. subject_type="recruitment_rule",
    subject_key="recruitment_minimum_age", directive_text="23"). Call this
    ONLY when the admin has just stated a clear, current instruction to
    change a standing rule -- never infer this from a vague, historical,
    or hypothetical statement. subject_key must match a read site's known
    key -- ask which key applies rather than guessing a new one. Requires
    RUN mode AND confirm=true. Reversible via revoke_directive."""
    return admin_directive_tools.remember_business_rule(subject_type, subject_key, directive_text, category, confirm)


@mcp.tool()
def revoke_directive(directive_id: int, confirm: bool = False) -> dict:
    """Revoke a previously saved admin directive by id, reverting its
    subject_key's read site back to its hardcoded default. Requires RUN
    mode AND confirm=true."""
    return admin_directive_tools.revoke_directive(directive_id, confirm)


@mcp.tool()
def get_directives(subject_key: str = None, status: str = "active", limit: int = 50) -> dict:
    """Read-only: list saved admin directives, optionally filtered by
    subject_key."""
    return admin_directive_tools.get_directives(subject_key, status, limit)


@mcp.tool()
def create_employee(
    full_name: str, employee_mobile: str = None, role: str = None,
    status: str = "active", confirm: bool = False,
) -> dict:
    """Create a new employee record. Requires RUN mode AND confirm=true."""
    return employee_tools.create_employee(full_name, employee_mobile, role, status, confirm)


@mcp.tool()
def update_employee(
    emp_id: int, full_name: str = None, primary_phone: str = None,
    employee_id_phone: str = None, aliases: list = None, confirm: bool = False,
) -> dict:
    """Update an existing employee's name/phone/aliases. Cannot touch
    cash transactions or hard-delete the employee. Requires RUN mode AND
    confirm=true."""
    return employee_tools.update_employee(emp_id, full_name, primary_phone, employee_id_phone, aliases, confirm)


@mcp.tool()
def approve_employee_edit_request(req_id: int, reviewer: str, confirm: bool = False) -> dict:
    """Approve a pending employee edit request. reviewer is required.
    Requires RUN mode AND confirm=true."""
    return employee_tools.approve_employee_edit_request(req_id, reviewer, confirm)


@mcp.tool()
def reject_employee_edit_request(req_id: int, reviewer: str, reason: str, confirm: bool = False) -> dict:
    """Reject a pending employee edit request. reviewer and reason are
    both required. Requires RUN mode AND confirm=true."""
    return employee_tools.reject_employee_edit_request(req_id, reviewer, reason, confirm)


@mcp.tool()
def list_roster(
    page: int = 1, page_size: int = 50, search: str = None, status: str = None,
    start_from: str = None, start_to: str = None,
) -> dict:
    """Read-only. Paginated, searchable, filterable escort roster list."""
    return escort_roster_tools.list_roster(page, page_size, search, status, start_from, start_to)


@mcp.tool()
def create_roster_entry(
    mother_vessel: str, lighter_vessel: str = None, master_mobile: str = None,
    escort_name: str = None, escort_mobile: str = None, destination: str = None,
    start_date: str = None, start_shift: str = None, end_date: str = None,
    end_shift: str = None, release_point: str = None, conveyance: float = None,
    notes: str = None, roster_status: str = "draft", confirm: bool = False,
) -> dict:
    """Create a new escort roster entry. mother_vessel is required.
    Requires RUN mode AND confirm=true."""
    return escort_roster_tools.create_roster_entry(
        mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
        destination, start_date, start_shift, end_date, end_shift, release_point,
        conveyance, notes, roster_status, confirm,
    )


@mcp.tool()
def patch_roster_entry(program_id: int, confirm: bool = False, **fields) -> dict:
    """Update an existing escort roster entry (any subset of its fields).
    *** This account is superadmin-tier -- this call mutates the roster
    entry DIRECTLY AND IMMEDIATELY, skipping the pending-change-request
    review step an operator-tier human would go through. Only call with
    confirm=true after the admin has explicitly and specifically
    instructed this exact change. *** Requires RUN mode AND confirm=true."""
    return escort_roster_tools.patch_roster_entry(program_id, confirm, **fields)


@mcp.tool()
def delete_roster_entry(program_id: int, confirm: bool = False) -> dict:
    """Delete an escort roster entry. *** Mutates the roster DIRECTLY AND
    IMMEDIATELY -- same superadmin-tier direct-mutation warning as
    patch_roster_entry. *** Requires RUN mode AND confirm=true."""
    return escort_roster_tools.delete_roster_entry(program_id, confirm)


@mcp.tool()
def recalculate_roster_entry(program_id: int, confirm: bool = False) -> dict:
    """Recalculate one roster entry's derived figures. Requires RUN mode
    AND confirm=true."""
    return escort_roster_tools.recalculate_roster_entry(program_id, confirm)


@mcp.tool()
def approve_roster_change_request(request_id: int, confirm: bool = False) -> dict:
    """Approve a pending roster change request. Requires RUN mode AND
    confirm=true."""
    return escort_roster_tools.approve_roster_change_request(request_id, confirm)


@mcp.tool()
def reject_roster_change_request(request_id: int, note: str = None, confirm: bool = False) -> dict:
    """Reject a pending roster change request. Requires RUN mode AND
    confirm=true."""
    return escort_roster_tools.reject_roster_change_request(request_id, note, confirm)


@mcp.tool()
def get_operator_pending(limit: int = 50, offset: int = 0) -> dict:
    """Read-only. Pending FPE operator-review submissions -- use this to
    find a pending_id for approve_operator_pending/reject_operator_pending."""
    return accounting_tools.get_operator_pending(limit, offset)


@mcp.tool()
def approve_operator_pending(
    pending_id: int, employee_id: int, reviewer: str,
    payout_method: str = None, txn_category: str = "salary",
    amount_override: float = None, note: str = None, confirm: bool = False,
) -> dict:
    """Approve a pending FPE operator submission -- promotes it to a
    final transaction and updates the employee ledger. employee_id and
    reviewer are required. Requires RUN mode AND confirm=true."""
    return accounting_tools.approve_operator_pending(
        pending_id, employee_id, reviewer, payout_method, txn_category,
        amount_override, note, confirm,
    )


@mcp.tool()
def reject_operator_pending(pending_id: int, reviewer: str, reason: str, confirm: bool = False) -> dict:
    """Reject a pending FPE operator submission. reviewer and reason are
    both required. Requires RUN mode AND confirm=true."""
    return accounting_tools.reject_operator_pending(pending_id, reviewer, reason, confirm)


@mcp.tool()
def create_manual_transaction(
    employee_id: int, amount: float, payout_method: str, txn_date: str,
    payout_phone: str = None, txn_category: str = "salary", reason: str = "",
    confirm: bool = False,
) -> dict:
    """Create a manual cash transaction for an employee -- bypasses the
    normal message-parsing pipeline. txn_date is YYYY-MM-DD. Requires RUN
    mode AND confirm=true."""
    return accounting_tools.create_manual_transaction(
        employee_id, amount, payout_method, txn_date, payout_phone,
        txn_category, reason, confirm,
    )


@mcp.tool()
def reverse_transaction(txn_id: int, reason: str, created_by: str = "admin", confirm: bool = False) -> dict:
    """Reverse an existing cash transaction. Use
    get_employee_transaction_history first to confirm which transaction
    is actually being reversed. Requires RUN mode AND confirm=true."""
    return accounting_tools.reverse_transaction(txn_id, reason, created_by, confirm)


# ── Phase 5A — proactive monitoring (Detect -> Investigate -> Report) ────
# On-demand only — this tool call IS the trigger, nothing polls or
# schedules it. Read-only (GET /scheduler/status, existing audit tools),
# never sends/triggers anything in fazle-core, so it cannot duplicate an
# alert fazle-core already sent via its own unchanged WhatsApp path.

@mcp.tool()
def get_monitoring_status() -> dict:
    """Current state (ok/alerting/stale/never_run) of every monitored
    fazle-core scheduler job (dlq_alert, health_summary,
    agent_incident_summary, backup_staleness_alert, bridge_watchdog,
    daily_admin_digest) — read-only, reflects fazle-core's own existing
    /scheduler/status."""
    return monitoring_tools.get_monitoring_status()


@mcp.tool()
def get_monitoring_report() -> dict:
    """Detect -> Investigate -> Report: checks monitored scheduler jobs,
    and for any alerting/stale job gathers evidence via the audit toolkit
    (last_error text, matching log lines). If no real evidence is found
    for an alerting job, says so explicitly rather than guessing a root
    cause. Never proposes or applies a fix — report only."""
    return monitoring_tools.get_monitoring_report()


# ── Phase 1 — complete operational visibility (2026-08-04) ──────────────
# Main WhatsApp bridge monitoring, DLQ, bridge health, system metrics.
# Read-only, always-available (no mode gate).

@mcp.tool()
def get_bridge_message_stats(hours: int = 24) -> dict:
    """Per-bridge (bridge1/bridge2/bridge3) + Meta WhatsApp message counts:
    total, in-window, unprocessed, and stuck-over-1h. Aggregate ONLY, no
    phone/recipient parameter — cannot answer per-number traffic
    questions, use audit_lookup_whatsapp_messages(phone=...) for that.
    Separate from get_social_status (that's the external Meta/Facebook
    channel only)."""
    return operational_tools.get_bridge_message_stats(hours)


@mcp.tool()
def get_dlq_status(limit: int = 20) -> dict:
    """Dead-letter queue depth and recent failed outbound message entries."""
    return operational_tools.get_dlq_status(limit)


@mcp.tool()
def get_bridge_diagnostics() -> dict:
    """Bridge-specific health, lag, dedup, and failover diagnostics."""
    return operational_tools.get_bridge_diagnostics()


@mcp.tool()
def get_system_health() -> dict:
    """DB, bridge heartbeats, outbound queue, disk, memory, CPU."""
    return operational_tools.get_system_health()


# ── Phase 2 — domain operational reports (2026-08-04) ────────────────────
# Recruitment/escort/payroll reuse the existing curated fazleBridge.js
# routes (same data as get_recruitment_leads/get_escort_programs/
# get_payroll_runs above); whatsapp-ops/bridge-health/system-health compose
# Phase 1's tools. All read-only, always-available (no mode gate).

@mcp.tool()
def get_recruitment_report(limit: int = 100) -> dict:
    """Structured recruitment funnel report: lead counts by stage and source."""
    return domain_reports.get_recruitment_report(limit)


@mcp.tool()
def get_escort_report(limit: int = 100) -> dict:
    """Structured escort duty report: program counts by status, unassigned flags."""
    return domain_reports.get_escort_report(limit)


@mcp.tool()
def get_payroll_report(limit: int = 50) -> dict:
    """Structured payroll run report: run counts by status, sampled net-salary total."""
    return domain_reports.get_payroll_report(limit)


@mcp.tool()
def get_cash_report(limit: int = 100) -> dict:
    """Structured cash transaction report: totals/counts by category and status, sampled amount total."""
    return domain_reports.get_cash_report(limit)


@mcp.tool()
def get_whatsapp_ops_report(hours: int = 24) -> dict:
    """Structured WhatsApp operations report: per-bridge message stats + DLQ depth."""
    return domain_reports.get_whatsapp_ops_report(hours)


@mcp.tool()
def get_bridge_health_report() -> dict:
    """Structured bridge health report: per-bridge state/lag/failover diagnostics."""
    return domain_reports.get_bridge_health_report()


@mcp.tool()
def get_system_health_report() -> dict:
    """Structured system health report: DB, bridges, queue, disk, memory, CPU."""
    return domain_reports.get_system_health_report()


# ── Persistent Task/Execution State + Action Approval (2026-08-19) ──────────
# See task_tools.py's own module docstring for the full design rationale.

@mcp.tool()
def get_tasks(status: str = None, owner: str = None, parent_task_id: int = None, limit: int = 50) -> dict:
    """Read-only: list persistent tasks. At the start of a new conversation,
    check get_tasks(owner="earth", status="IN_PROGRESS") (and
    WAITING_APPROVAL/VERIFYING) for unfinished work before assuming a fresh
    start -- tasks survive a conversation ending."""
    return task_tools.get_tasks(status, owner, parent_task_id, limit)


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Read-only: one task's full row plus its complete event history."""
    return task_tools.get_task(task_id)


@mcp.tool()
def create_task(
    title: str, description: str = None, parent_task_id: int = None,
    priority: str = "normal", execution_mode: str = None, created_from: str = None,
    trace_id: str = None, depends_on: list = None,
) -> dict:
    """Create a persistent task (BACKLOG by default). Use parent_task_id to
    build a goal -> task -> subtask hierarchy."""
    return task_tools.create_task(title, description, parent_task_id, priority, execution_mode, created_from, trace_id, depends_on)


@mcp.tool()
def claim_task(task_id: int, owner: str = "earth") -> dict:
    """Claim a READY/BACKLOG task, moving it to IN_PROGRESS."""
    return task_tools.claim_task(task_id, owner)


@mcp.tool()
def update_task_status(task_id: int, status: str, next_action: str = None, failure_reason: str = None, verification: dict = None) -> dict:
    """Move a task to a new status. Only specific transitions are legal --
    an illegal one is rejected with an explanation of what's actually
    allowed from the current state. verification should carry real
    evidence (e.g. a re-fetched read-back), not a bare claim."""
    return task_tools.update_task_status(task_id, status, next_action, failure_reason, verification)


@mcp.tool()
def authorize_build(task_id: int, repos: list, ttl_hours: int = 4) -> dict:
    """Grant broad, task-scoped BUILD authorization -- covers many
    subsequent file edits/tests within the named repo path(s), without a
    separate approval per edit. Call ONLY after the admin's real, current
    instruction ("fix it", "go ahead", "implement it")."""
    return task_tools.authorize_build(task_id, repos, ttl_hours)


@mcp.tool()
def authorize_action(
    action_type: str, summary: str, task_id: int = None, files_changed: list = None,
    diff: str = None, command: str = None, risk: str = "medium",
    expected_effect: str = None, rollback_plan: str = None, confirm: bool = False,
) -> dict:
    """Fused propose+approve for a specific consequential action (git
    commit / service restart / migration / deploy / production write /
    opencode_dispatch). Call ONLY after the admin has just given a real,
    current instruction naming this exact action ("commit it", "commit,
    push and deploy") -- never infer a broader action than what was said.
    For git_commit, diff should be the real `git diff --cached` output.
    Requires confirm=true, AND either RUN mode or a live, unexpired BUILD
    authorization on this exact task_id (call authorize_build first if you
    haven't already for this task)."""
    return task_tools.authorize_action(action_type, summary, task_id, files_changed, diff, command, risk, expected_effect, rollback_plan, confirm)


@mcp.tool()
def get_pending_actions(task_id: int = None) -> dict:
    """Read-only: list pending action-approval requests, optionally scoped to one task."""
    return task_tools.get_pending_actions(task_id)


@mcp.tool()
def get_action(action_id: int) -> dict:
    """Read-only: one action-approval request's full row (status, action_type, diff, risk, reviewed_by, execution_result, ...)."""
    return task_tools.get_action(action_id)


@mcp.tool()
def propose_action(
    action_type: str, summary: str, task_id: int = None, files_changed: list = None,
    diff: str = None, command: str = None, risk: str = "medium",
    expected_effect: str = None, rollback_plan: str = None,
) -> dict:
    """Propose a file mutation / git commit / deploy / migration / restart /
    production write / opencode_dispatch call BEFORE doing it -- required
    for every one of those action types, even for your own direct
    BUILD/RUN-mode file edits. No RUN/confirm gate on proposing itself; the
    gate is on approve_action, a separate step only the admin's explicit
    instruction triggers."""
    return task_tools.propose_action(action_type, summary, task_id, files_changed, diff, command, risk, expected_effect, rollback_plan)


@mcp.tool()
def approve_action(action_id: int, confirm: bool = False) -> dict:
    """Authorize a pending action-approval request to proceed (does NOT
    execute anything itself). Call ONLY after the admin has said
    'APPROVE ACTION <id>' naming this exact id -- never on a general
    'yes'/'go ahead'. Requires RUN mode AND confirm=true."""
    return task_tools.approve_action(action_id, confirm)


@mcp.tool()
def reject_action(action_id: int, reason: str = None, confirm: bool = False) -> dict:
    """Reject a pending action-approval request (moves its parent task, if
    any, to BLOCKED). Requires RUN mode AND confirm=true."""
    return task_tools.reject_action(action_id, reason, confirm)


@mcp.tool()
def record_execution_result(action_id: int, result: dict, verification: dict = None) -> dict:
    """Record the real outcome of an approved+executed action. Pull the
    actual diff/commit hash via audit_git_status/audit_recent_commits
    first -- this is where the verified evidence gets captured, not the
    pre-approval proposal text."""
    return task_tools.record_execution_result(action_id, result, verification)


if __name__ == "__main__":
    mcp.run(transport="stdio")
