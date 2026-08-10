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
import assistant_bridge_client
import audit_tools
import domain_reports
import metrics_tools
import monitoring_tools
import opencode_tools
import operational_tools
import scheduler_tools

# Thin alias — this module's original inline _get() was extracted to
# assistant_bridge_client.py (Phase 2, 2026-08-04) so domain_reports.py can
# reuse the same authenticated client. Kept as _get here to avoid touching
# every call site below for a pure rename.
_get = assistant_bridge_client.get


mcp = MCPServer("fazle-core")


@mcp.tool()
def get_contacts(limit: int = 30) -> list:
    """Look up contacts known to fazle-core: name, WhatsApp number, relation, company."""
    return _get("/contacts", {"limit": limit})


@mcp.tool()
def get_employees(status: str = "active") -> list:
    """List fazle-core employees with designation and status. status: 'active' or 'inactive'."""
    return _get("/employees", {"status": status})


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
    """NOT YET AVAILABLE. Intended to list real cash transactions (salary,
    advances, bonuses, deductions, corrections) from fpe_cash_transactions —
    the sole canonical cash ledger (Owner Directive 2026-06-29), never
    mixing in wbom_cash_transactions (legacy/archive only). date: optional
    YYYY-MM-DD. status: optional transaction_status filter.

    Disabled 2026-08-10: the backing view (ai_read_cash_transactions) was
    never created — no migration exists for it, only the proposal
    (proposal_ai_read_cash_transactions_20260802.md). Creating it is a
    fazle-core production DB DDL change and needs its own separate
    proposal + Owner approval, not something a tool call should trigger.
    Calling this previously round-tripped to assistant-backend's
    /api/fazle/cash-transactions and 502/503'd there; short-circuiting
    here instead gives a clear, immediate reason so a tool-calling loop
    doesn't retry it as if it were a transient failure."""
    return {
        "error": "get_cash_transactions is not yet available: its backing "
        "Postgres view (ai_read_cash_transactions) has not been created. "
        "This requires a separate, Owner-approved fazle-core DB migration "
        "— do not retry this call, and do not attempt to query "
        "fpe_cash_transactions or wbom_cash_transactions directly instead."
    }


@mcp.tool()
def get_escort_programs(limit: int = 30) -> list:
    """List escort programs (vessel escort assignments), most recent first."""
    return _get("/escort-programs", {"limit": limit})


@mcp.tool()
def get_module_bridge_status() -> list:
    """Check fazle-core's internal service heartbeats (last seen, queue depth)."""
    return _get("/module-bridge-status")


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
def audit_search_code(query: str, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search source code for a literal string. root: 'assistant-platform' or 'fazle-core'."""
    return audit_tools.audit_search_code(query, root, max_results)


@mcp.tool()
def audit_search_docs(query: str, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search markdown documentation for a literal string."""
    return audit_tools.audit_search_docs(query, root, max_results)


@mcp.tool()
def audit_search_kb(query: str, max_results: int = 20) -> dict:
    """Search fazle-core's knowledge_base/ for a literal string."""
    return audit_tools.audit_search_kb(query, max_results)


@mcp.tool()
def audit_search_logs(query: str = "", log: str = "backend", max_lines: int = 100) -> dict:
    """Search a known log ('backend', 'fazle-core', or 'hermes-runner') for
    a literal string (or tail it if query is empty). Secrets and phone
    numbers are redacted from results."""
    return audit_tools.audit_search_logs(query, log, max_lines)


@mcp.tool()
def audit_read_file(path: str, root: str = "assistant-platform", start_line: int = None, end_line: int = None, max_lines: int = 500) -> dict:
    """Read a file (optionally a bounded line range) from an approved root.
    Refuses secret/credential-shaped paths and enforces a size cap."""
    return audit_tools.audit_read_file(path, root, start_line, end_line, max_lines)


@mcp.tool()
def audit_lookup_whatsapp_messages(phone: str = "", platform: str = "", is_processed: bool = None, message_id: int = None, limit: int = 20) -> dict:
    """Filtered WhatsApp message lookup for incident investigation: by phone
    number, platform (bridge1/bridge2/bridge3/meta/whatsapp), processed
    status, or message_id. Phone-shaped fields are PII-masked per policy."""
    return audit_tools.audit_lookup_whatsapp_messages(phone, platform, is_processed, message_id, limit)


@mcp.tool()
def audit_git_status(repo: str = "assistant-platform") -> dict:
    """Read-only git status for an approved repo ('assistant-platform' or 'fazle-core')."""
    return audit_tools.audit_git_status(repo)


@mcp.tool()
def audit_recent_commits(repo: str = "assistant-platform", limit: int = 10) -> dict:
    """Read-only recent commit log for an approved repo."""
    return audit_tools.audit_recent_commits(repo, limit)


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
def opencode_dispatch(prompt: str, confirm: bool = False) -> dict:
    """Hand a diagnosed problem to OpenCode (a separate, sandboxed coding
    agent) to implement — NOT Hermes writing code directly. Requires RUN
    mode AND confirm=true, which must only be set after explicitly asking
    the user to authorize this specific implementation step. Review the
    result afterward with audit_git_status / audit_recent_commits."""
    return opencode_tools.opencode_dispatch(prompt, confirm)


@mcp.tool()
def opencode_check(session_id: str) -> dict:
    """Read-only: fetch an OpenCode session's message history — follow up
    on an opencode_dispatch call or review a past result. No gating."""
    return opencode_tools.opencode_check(session_id)


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
    total, in-window, unprocessed, and stuck-over-1h. Separate from
    get_social_status (that's the external Meta/Facebook channel only)."""
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
