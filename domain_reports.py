"""
Phase 2 — domain operational reports (2026-08-04).

Recruitment, escort, payroll: fetch curated data via assistant_bridge_client
(the same fazleBridge.js proxy the existing get_recruitment_leads /
get_escort_programs / get_payroll_runs tools already use in server.py — no
new fazle-core routes, no raw SQL, no unrestricted DB access). WhatsApp-ops,
bridge-health, system-health: compose Phase 1's operational_tools functions
into one deterministic report each instead of leaving summarization to the
model.

Every report reuses report_schema.build_report() (Task 2's shape).
"""

import assistant_bridge_client as bridge
import operational_tools
from report_schema import build_report


def _clamp(value, default, maximum):
    # Same convention as operational_tools._clamp / metrics_tools._clamp_limit
    # — deliberately duplicated per-module rather than shared (see
    # fazle_core_client.py architecture notes).
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def _count_by(rows, field):
    counts = {}
    for r in rows:
        key = r.get(field) or "(unknown)"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _error_report(title, err):
    return build_report(
        title=title,
        status="UNKNOWN",
        summary=f"Could not retrieve data for {title.lower()}.",
        problems=[err],
        risk="Unknown — data unavailable",
        action_required="Check fazle-core / assistant-backend connectivity.",
    )


def get_recruitment_report(limit: int = 100) -> dict:
    """Recruitment funnel snapshot: lead counts by funnel stage and source."""
    limit = _clamp(limit, 100, 100)
    rows = bridge.get("/recruitment-leads", {"limit": limit})
    if isinstance(rows, dict) and "error" in rows:
        return _error_report("Recruitment Funnel Report", rows["error"])

    by_stage = _count_by(rows, "funnel_stage")
    by_source = _count_by(rows, "source")
    return build_report(
        title="Recruitment Funnel Report",
        status="HEALTHY",
        summary=f"{len(rows)} recruitment lead(s) in the sampled window (limit={limit}).",
        metrics={"total_leads": len(rows), "by_funnel_stage": by_stage, "by_source": by_source},
        evidence=[f"sample size: {len(rows)} of up to {limit}"],
        risk="Low",
        recommendations=["No action needed."],
    )


def get_escort_report(limit: int = 100) -> dict:
    """Escort duty snapshot: program counts by status, most recent first."""
    limit = _clamp(limit, 100, 100)
    rows = bridge.get("/escort-programs", {"limit": limit})
    if isinstance(rows, dict) and "error" in rows:
        return _error_report("Escort Duty Report", rows["error"])

    by_status = _count_by(rows, "status")
    problems = []
    unassigned = sum(1 for r in rows if not r.get("escort_name"))
    if unassigned:
        problems.append(f"{unassigned} escort program(s) with no escort assigned.")

    return build_report(
        title="Escort Duty Report",
        status="DEGRADED" if problems else "HEALTHY",
        summary=f"{len(rows)} escort program(s) in the sampled window (limit={limit}).",
        metrics={"total_programs": len(rows), "by_status": by_status, "unassigned": unassigned},
        evidence=[f"sample size: {len(rows)} of up to {limit}"],
        findings=problems,
        problems=problems,
        risk="Medium" if problems else "Low",
        recommendations=["Review unassigned escort programs."] if problems else ["No action needed."],
        action_required="Owner review needed." if problems else "None",
    )


def get_payroll_report(limit: int = 50) -> dict:
    """Payroll run snapshot: run counts by status for the most recent periods."""
    limit = _clamp(limit, 50, 100)
    rows = bridge.get("/payroll-runs", {"limit": limit})
    if isinstance(rows, dict) and "error" in rows:
        return _error_report("Payroll Run Report", rows["error"])

    by_status = _count_by(rows, "status")
    total_net = sum(float(r["net_salary"]) for r in rows if r.get("net_salary") is not None)
    return build_report(
        title="Payroll Run Report",
        status="HEALTHY",
        summary=f"{len(rows)} payroll run record(s) in the sampled window (limit={limit}).",
        metrics={"total_runs": len(rows), "by_status": by_status, "sampled_net_salary_total": round(total_net, 2)},
        evidence=[f"sample size: {len(rows)} of up to {limit}"],
        risk="Low",
        recommendations=["No action needed."],
    )


def get_whatsapp_ops_report(hours: int = 24) -> dict:
    """WhatsApp message-flow ops: per-bridge counts + DLQ depth in one report."""
    stats = operational_tools.get_bridge_message_stats(hours)
    dlq = operational_tools.get_dlq_status(limit=20)

    if isinstance(stats, dict) and "error" in stats:
        return _error_report("WhatsApp Operations Report", stats["error"])

    by_platform = stats.get("by_platform", [])
    stuck_total = sum(p.get("stuck_over_1h", 0) for p in by_platform)
    dlq_count = dlq.get("total", dlq.get("count", 0)) if isinstance(dlq, dict) and "error" not in dlq else None

    problems = []
    if stuck_total:
        problems.append(f"{stuck_total} message(s) unprocessed and stuck over 1h across all bridges.")
    if dlq_count:
        problems.append(f"{dlq_count} item(s) in the dead-letter queue.")

    return build_report(
        title="WhatsApp Operations Report",
        status="DEGRADED" if problems else "HEALTHY",
        summary="WhatsApp message flow has issues that need attention." if problems else "WhatsApp message flow is operating normally.",
        metrics={"by_platform": by_platform, "dlq_count": dlq_count, "stuck_over_1h_total": stuck_total},
        evidence=[f"hours window: {stats.get('hours', hours)}"],
        findings=problems,
        problems=problems,
        risk="Medium" if problems else "Low",
        recommendations=["Investigate stuck messages via get_bridge_message_stats and audit_search_logs."] if problems else ["No action needed."],
        action_required="Owner review needed." if problems else "None",
    )


def get_bridge_health_report() -> dict:
    """Bridge-specific health/lag/failover diagnostics as one report."""
    diag = operational_tools.get_bridge_diagnostics()
    if isinstance(diag, dict) and "error" in diag:
        return _error_report("Bridge Health Report", diag["error"])

    bridges = diag.get("bridges", {})
    problems = []
    for name, b in bridges.items():
        if b.get("state") != "healthy":
            problems.append(f"{name} state is '{b.get('state')}' (consecutive_failures={b.get('consecutive_failures')}).")

    return build_report(
        title="Bridge Health Report",
        status="CRITICAL" if problems else "HEALTHY",
        summary="One or more bridges are unhealthy." if problems else "All bridges are healthy.",
        metrics={name: {"state": b.get("state"), "avg_lag_ms": b.get("avg_lag_ms"), "retry_queue_depth": b.get("retry_queue_depth")} for name, b in bridges.items()},
        findings=problems,
        problems=problems,
        risk="High" if problems else "Low",
        recommendations=["Investigate unhealthy bridge via audit_search_logs."] if problems else ["No action needed."],
        action_required="Owner review needed." if problems else "None",
    )


def get_system_health_report() -> dict:
    """DB / bridge heartbeats / outbound queue / disk / memory / CPU as one report."""
    health = operational_tools.get_system_health()
    if isinstance(health, dict) and "error" in health:
        return _error_report("System Health Report", health["error"])

    probes = health.get("probes", {})
    problems = [f"{name}: {p.get('status')}" for name, p in probes.items() if isinstance(p, dict) and p.get("status") not in ("ok", None)]

    overall = health.get("status", "unknown")
    report_status = {"ok": "HEALTHY", "degraded": "DEGRADED", "critical": "CRITICAL"}.get(overall, "UNKNOWN")

    return build_report(
        title="System Health Report",
        status=report_status,
        summary="System is operating normally." if report_status == "HEALTHY" else "System has degraded or critical probes.",
        metrics=probes,
        findings=problems,
        problems=problems,
        risk={"HEALTHY": "Low", "DEGRADED": "Medium", "CRITICAL": "High"}.get(report_status, "Unknown"),
        recommendations=["Investigate degraded/critical probes."] if problems else ["No action needed."],
        action_required="Owner review needed." if report_status == "CRITICAL" else "None",
    )
