"""
Phase 5A — Proactive Monitoring: Detect -> Investigate -> Report, on
demand only. See HERMES_PHASE5_PROACTIVE_MONITORING_PROMPT_20260804.md for
the full design/rationale.

Explicitly NOT built here (by design, Owner-scoped):
- No auto-fix, no propose-fix.
- No polling/cron trigger — this only runs when a tool call happens.
- No new fazle-core code — reads the EXISTING, already-built, already
  admin-API-key-gated GET /scheduler/status.
- No change to fazle-core's existing WhatsApp alerting (outbound.enqueue
  call sites in modules/scheduler/__init__.py) — this module never calls
  anything that sends/notifies; it only ever does GET/read operations
  (fazle_core_client.get, audit_tools's read-only search functions).
  Structurally cannot create a duplicate alert.
- No hallucinated root cause: when a job is alerting but neither
  last_error nor a log search turns up anything, the report says so
  explicitly ("insufficient evidence to determine root cause") instead of
  guessing.
"""

from datetime import datetime, timezone

import audit_tools
import fazle_core_client as core
from report_schema import build_report

# The 6 read-only, already-alerting scheduler jobs (modules/scheduler/
# __init__.py — re-verify this list is still accurate at implementation/
# review time, this is a snapshot). Values are a generous (~2x) multiple
# of each job's real registered interval/cron, to avoid false "stale"
# positives on ordinary scheduling jitter:
#   dlq_alert: IntervalTrigger(minutes=15) -> 30
#   health_summary / agent_incident_summary: IntervalTrigger(hours=6) -> 720
#   backup_staleness_alert: CronTrigger(hour=3) daily -> 1560
#   bridge_watchdog: IntervalTrigger(minutes=5) -> 15
#   daily_admin_digest: CronTrigger(hour=8, default) daily -> 1560
MONITORED_JOBS = {
    "dlq_alert": 30,
    "health_summary": 720,
    "agent_incident_summary": 720,
    "backup_staleness_alert": 1560,
    "bridge_watchdog": 15,
    "daily_admin_digest": 1560,
}


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _job_state(row, stale_minutes):
    """ok / alerting / stale / never_run — never invents a 5th state."""
    if row is None:
        return "never_run"
    if row.get("last_status") == "error" or row.get("last_error"):
        return "alerting"
    last_run = _parse_iso(row.get("last_run_at"))
    if last_run is None:
        return "never_run"
    age_minutes = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
    if age_minutes > stale_minutes:
        return "stale"
    return "ok"


def get_monitoring_status() -> dict:
    """Current state of every monitored, already-alerting scheduler job.
    Read-only: calls fazle-core's existing GET /scheduler/status. No new
    fazle-core code, never sends or triggers anything."""
    result = core.get("/scheduler/status")
    if "error" in result:
        return result

    jobs_by_name = {j.get("job_name"): j for j in result.get("jobs", [])}
    monitored = []
    for job_name, stale_minutes in MONITORED_JOBS.items():
        row = jobs_by_name.get(job_name)
        monitored.append({
            "job_name": job_name,
            "state": _job_state(row, stale_minutes),
            "last_run_at": row.get("last_run_at") if row else None,
            "last_status": row.get("last_status") if row else None,
            "last_error": row.get("last_error") if row else None,
        })

    return {
        "scheduler_enabled": result.get("enabled"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "jobs": monitored,
    }


def _gather_evidence(job_name, last_error):
    """Bounded, deterministic evidence-gathering via the existing audit
    toolkit. Only ever returns what a real search actually found — never
    fabricates a cause. Empty list means genuinely no evidence found."""
    evidence = []
    if last_error:
        evidence.append(f"fazle-core last_error: {last_error}")

    log_hit = audit_tools.audit_search_logs(job_name, log="fazle-core", max_lines=20, context_lines=15)
    if log_hit.get("matches"):
        evidence.extend(f"log: {line}" for line in log_hit["matches"][:16])

    if last_error:
        snippet = str(last_error)[:40].strip()
        if snippet:
            snippet_hit = audit_tools.audit_search_logs(snippet, log="fazle-core", max_lines=20, context_lines=15)
            if snippet_hit.get("matches"):
                evidence.extend(f"log (error match): {line}" for line in snippet_hit["matches"][:16])

    return evidence


def get_monitoring_report() -> dict:
    """Detect -> Investigate -> Report, purely on demand (this function
    call IS the trigger — nothing schedules or polls it). Read-only
    throughout; never sends/triggers anything in fazle-core, so it cannot
    create a duplicate of an alert fazle-core already sent."""
    status = get_monitoring_status()
    if "error" in status:
        return build_report(
            title="Proactive Monitoring Report",
            status="UNKNOWN",
            summary="Could not retrieve fazle-core scheduler status.",
            problems=[status["error"]],
            risk="Unknown — data unavailable",
            action_required="Check fazle-core connectivity / FAZLE_CORE_API_KEY configuration.",
        )

    jobs = status["jobs"]
    findings, problems, evidence_all, recommendations = [], [], [], []
    worst = "HEALTHY"

    if status.get("scheduler_enabled") is False:
        problems.append("fazle-core's scheduler is entirely disabled.")
        worst = "CRITICAL"

    for job in jobs:
        name, state = job["job_name"], job["state"]
        if state == "ok":
            continue

        if state == "alerting":
            ev = _gather_evidence(name, job.get("last_error"))
            evidence_all.extend(ev)
            if ev:
                findings.append(f"{name}: alerting. " + "; ".join(ev[:3]))
                recommendations.append(f"Investigate {name} further (audit_read_file / audit_recent_commits).")
            else:
                findings.append(
                    f"{name}: alerting, but insufficient evidence to determine root cause "
                    "(no last_error text and no matching log lines found)."
                )
                recommendations.append(f"{name} needs manual investigation — no automated evidence available.")
            problems.append(f"{name} is alerting.")
            worst = "CRITICAL"

        elif state == "stale":
            findings.append(f"{name}: has not run since {job.get('last_run_at') or 'never'} (stale).")
            recommendations.append(f"Check whether {name} is still scheduled and running.")
            problems.append(f"{name} appears stale.")
            if worst == "HEALTHY":
                worst = "DEGRADED"

        elif state == "never_run":
            findings.append(f"{name}: has never run.")
            recommendations.append(f"Confirm {name} is registered and the scheduler is running.")
            problems.append(f"{name} has never run.")
            if worst == "HEALTHY":
                worst = "DEGRADED"

    if not recommendations:
        recommendations.append("No action needed.")

    risk = {"HEALTHY": "Low", "DEGRADED": "Medium", "CRITICAL": "High", "UNKNOWN": "Unknown"}[worst]

    return build_report(
        title="Proactive Monitoring Report",
        status=worst,
        summary="All monitored jobs healthy." if worst == "HEALTHY" else "One or more monitored jobs need attention.",
        metrics={j["job_name"]: j["state"] for j in jobs},
        evidence=evidence_all,
        findings=findings,
        problems=problems,
        risk=risk,
        recommendations=recommendations,
        action_required="Owner review needed." if worst == "CRITICAL" else "None",
        next_steps=recommendations,
    )
