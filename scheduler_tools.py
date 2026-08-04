"""
Task 5 — controlled, allowlisted triggering of fazle-core's existing
scheduler jobs (modules/scheduler/__init__.py, 16 jobs). NOT arbitrary
execution — every job_name is validated against JOB_REGISTRY below before
anything happens, and the risk tier drives what Hermes mode is required.

Mode gate: reads hermes-runner's own current_mode.txt directly rather than
sharing state with that process (they're independent processes — see the
approved plan's architecture decision #2). Fail-closed to READ on any
problem, identical logic to hermes-runner/server.py's read_current_mode(),
deliberately duplicated (matches this codebase's existing cross-process
duplication convention).

Confirmation: reuses Hermes's existing confirm-before-destructive gate —
SYSTEM_PREAMBLE already instructs Hermes to ask the user yes/no before a
destructive action. `confirm=True` here is only ever set by Hermes after
that real confirmation happened in conversation; this tool does not invent
a second, separate confirmation UI.

Known pre-existing gap in fazle-core itself (documented, not fixed here —
out of scope, would need fazle-core's own KB-first governance process):
fazle-core's own `/scheduler/run/{job_name}` HTTP route only checks
`require_api_key`, not `rbac.check_permission()` — unlike the WhatsApp
`RUN JOB` admin command, which is genuinely RBAC-gated. The allowlist +
mode + confirm layer here is what actually constrains what Hermes
specifically can trigger; it does not change fazle-core's own route.
"""

import json
import os
import time

import fazle_core_client as core

MODES = ["READ", "BUILD", "RUN"]
MODE_FILE = os.environ.get("HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt"))

READ_ONLY = "READ_ONLY"
LOW_RISK = "LOW_RISK_ACTION"
DESTRUCTIVE = "DESTRUCTIVE"

# Categorized from a full read of modules/scheduler/__init__.py (audit,
# see the approved plan). Owner confirmed daily_payroll_compute should be
# included, tiered DESTRUCTIVE (2026-08-04) — today it's dry-run-only
# behind fazle-core's own office_payroll_authoritative_enabled flag, but
# the gate here is written for what happens if that flag is ever enabled.
JOB_REGISTRY = {
    "dlq_alert": READ_ONLY,
    "health_summary": READ_ONLY,
    "agent_incident_summary": READ_ONLY,
    "backup_staleness_alert": READ_ONLY,
    "bridge_watchdog": READ_ONLY,
    "daily_admin_digest": READ_ONLY,
    "rag_rebuild": READ_ONLY,
    "stale_escort_reminder": LOW_RISK,
    "lock_cleanup": LOW_RISK,
    "draft_ttl_cleanup": LOW_RISK,
    "combined_draft_cleanup": LOW_RISK,
    "daily_db_backup": LOW_RISK,
    "daily_memory_review": LOW_RISK,
    "daily_contact_sync": LOW_RISK,
    "daily_payroll_compute": DESTRUCTIVE,
    "payment_reconciliation": DESTRUCTIVE,
}

_STATE_DIR = os.path.expanduser("~/fazle-mcp/state/last_run")
_DUPLICATE_WINDOW_SECONDS = 15


def _read_mode():
    """Fail-closed mode read — identical shape to hermes-runner's own
    read_current_mode(). Any problem (missing file, unreadable, garbage,
    or the newer JSON TTL format from hermes-runner's Task 6 work having
    already expired) lands on READ, never BUILD/RUN by default."""
    try:
        with open(MODE_FILE, "r") as f:
            raw = f.read().strip()
    except OSError:
        return "READ"
    if not raw:
        return "READ"
    # hermes-runner's mode file may be the newer TTL-aware JSON form
    # ({"mode": ..., "expires_at": ...}) or the legacy bare word — handle
    # both, since these are two independently-deployed processes.
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


def _duplicate_guard(job_name):
    """On-disk debounce — fazle-mcp is spawned fresh per Hermes turn (no
    long-lived process to hold in-memory state), so this has to survive
    across process spawns. Returns True if this job was already
    successfully triggered within the debounce window."""
    os.makedirs(_STATE_DIR, exist_ok=True)
    path = os.path.join(_STATE_DIR, f"{job_name}.json")
    now = time.time()
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                last = json.load(f).get("triggered_at", 0)
            if now - last < _DUPLICATE_WINDOW_SECONDS:
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    with open(path, "w") as f:
        json.dump({"triggered_at": now}, f)
    return False


def run_scheduled_task(job_name: str, confirm: bool = False) -> dict:
    """Trigger one of fazle-core's existing, pre-approved scheduler jobs by
    name. NOT arbitrary execution — job_name must be in the allowlist.
    Destructive-tier jobs additionally require RUN mode and confirm=True
    (only set after the user has explicitly confirmed in conversation)."""
    started_at = time.time()

    if job_name not in JOB_REGISTRY:
        return {
            "job_name": job_name,
            "status": "error",
            "error": f"unknown or non-allowlisted job. allowed: {sorted(JOB_REGISTRY)}",
        }

    risk_tier = JOB_REGISTRY[job_name]
    mode = _read_mode()

    if mode == "READ":
        return {
            "job_name": job_name,
            "risk_tier": risk_tier,
            "mode_at_execution": mode,
            "status": "denied",
            "error": "READ mode cannot trigger any scheduled task — switch to BUILD or RUN first.",
        }

    if risk_tier == DESTRUCTIVE:
        if mode != "RUN":
            return {
                "job_name": job_name,
                "risk_tier": risk_tier,
                "mode_at_execution": mode,
                "status": "denied",
                "error": "this job is DESTRUCTIVE and requires RUN mode.",
            }
        if not confirm:
            return {
                "job_name": job_name,
                "risk_tier": risk_tier,
                "mode_at_execution": mode,
                "status": "denied",
                "error": "this job is DESTRUCTIVE and requires explicit confirmation (confirm=true) after asking the user.",
            }

    if _duplicate_guard(job_name):
        return {
            "job_name": job_name,
            "risk_tier": risk_tier,
            "mode_at_execution": mode,
            "status": "denied",
            "error": f"this job was already triggered in the last {_DUPLICATE_WINDOW_SECONDS}s — avoiding a duplicate run.",
        }

    result = core.post(f"/scheduler/run/{job_name}")
    finished_at = time.time()
    status = "error" if "error" in result and "result" not in result else "success"

    return {
        "job_name": job_name,
        "risk_tier": risk_tier,
        "mode_at_execution": mode,
        "confirmed": confirm,
        "status": status,
        "result": result,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": round((finished_at - started_at) * 1000, 1),
    }


def list_available_tasks() -> dict:
    """List every allowlisted scheduler job and its risk tier — lets
    Hermes/the user see what's triggerable without guessing job names."""
    return {"jobs": JOB_REGISTRY}
