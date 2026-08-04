"""
Task 1 — read-only visibility into fazle-core's social/message-flow state,
via its existing /api/social/status|queue|flagged routes (unmodified,
already admin-API-key gated on fazle-core's side). No new fazle-core code;
this only adds a caller.

Also Task 2 — get_social_report() composes the three calls into one
deterministic structured report (report_schema.build_report) rather than
leaving report shape to LLM narration.

PII: `target_id` in queue/flagged rows can be a WhatsApp phone number
(confirmed in audit: modules/social_auto_reply/planner_worker.py resolves
target_id via find_by_mobile() for WhatsApp-sourced rows). Masked here with
role="admin" — correct today because Hermes's entire surface is admin-gated
end-to-end (backend/src/routes/hermes.js requireAdmin); if Hermes is ever
reachable by non-admins, this call site must pass is_admin=False instead.
"""

import fazle_core_client as core
import pii_mask
from report_schema import build_report

# See module docstring: Hermes is admin-only end-to-end today.
_HERMES_IS_ADMIN_CONTEXT = True


def _clamp_limit(value, default, maximum):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def get_social_status() -> dict:
    """Message-flow health snapshot: queue depth, sent/failed/flagged counts,
    rate-limit state, from fazle-core's own social_auto_reply state tracker."""
    return core.get("/api/social/status")


def get_social_queue(limit: int = 50) -> dict:
    """Pending/in-flight outbound social messages, most recent first."""
    limit = _clamp_limit(limit, 50, 200)
    result = core.get("/api/social/queue", {"limit": limit})
    if "error" in result:
        return result
    result["items"] = pii_mask.mask_pii(result.get("items", []), _HERMES_IS_ADMIN_CONTEXT)
    return result


def get_social_flagged(limit: int = 50) -> dict:
    """Flagged/held social messages needing review, most recent first."""
    limit = _clamp_limit(limit, 50, 200)
    result = core.get("/api/social/flagged", {"limit": limit})
    if "error" in result:
        return result
    result["items"] = pii_mask.mask_pii(result.get("items", []), _HERMES_IS_ADMIN_CONTEXT)
    return result


def get_social_report() -> dict:
    """One deterministic report on the social/message-flow pipeline's
    current health — status, queue/flagged counts, findings, risk,
    recommendations — built from get_social_status/queue/flagged rather
    than relying on the model to narrate three separate tool calls
    consistently."""
    status = get_social_status()
    if "error" in status:
        return build_report(
            title="Social Message Flow Report",
            status="UNKNOWN",
            summary="Could not retrieve fazle-core social status.",
            problems=[status["error"]],
            risk="Unknown — data unavailable",
            action_required="Check fazle-core connectivity / FAZLE_CORE_API_KEY configuration.",
        )

    queue = get_social_queue(limit=100)
    flagged = get_social_flagged(limit=100)

    pending = status.get("pending", 0)
    failed = status.get("failed", 0)
    flagged_open = status.get("flagged_open", 0)
    sent_24h = status.get("sent_24h", 0)
    paused = status.get("paused", False)
    enabled = status.get("enabled", False)

    problems = []
    if not enabled:
        problems.append("Social auto-reply is disabled.")
    if paused:
        problems.append("Social auto-reply is currently paused.")
    if failed:
        problems.append(f"{failed} failed message(s) detected.")
    if flagged_open:
        problems.append(f"{flagged_open} open flagged item(s) awaiting review.")

    if not enabled or failed > 5:
        report_status = "CRITICAL"
        risk = "High"
    elif problems:
        report_status = "DEGRADED"
        risk = "Medium"
    else:
        report_status = "HEALTHY"
        risk = "Low"

    recommendations = []
    if failed:
        recommendations.append("Investigate failed messages via get_social_queue.")
    if flagged_open:
        recommendations.append("Review flagged items via get_social_flagged.")
    if not recommendations:
        recommendations.append("No action needed.")

    return build_report(
        title="Social Message Flow Report",
        status=report_status,
        summary=(
            "Social message flow is operating normally."
            if report_status == "HEALTHY"
            else "Social message flow has issues that need attention."
        ),
        metrics={
            "pending": pending,
            "failed": failed,
            "flagged_open": flagged_open,
            "sent_24h": sent_24h,
            "enabled": enabled,
            "paused": paused,
        },
        evidence=[f"queue sample size: {len(queue.get('items', []))}", f"flagged sample size: {len(flagged.get('items', []))}"],
        findings=problems,
        problems=problems,
        risk=risk,
        recommendations=recommendations,
        action_required="Owner review needed." if report_status == "CRITICAL" else "None",
        next_steps=recommendations,
    )
