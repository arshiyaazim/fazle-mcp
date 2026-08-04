"""
Reusable structured-report shape for Hermes tools — one schema/formatter
instead of ad-hoc report text scattered per tool. Any tool that wants to
hand Hermes a deterministic, pre-formatted report (rather than relying on
LLM narration quality for something structured) builds one via
build_report() below. Adapted from the task brief's illustrative example,
not copied verbatim — field set kept close to it since it's already a
reasonable, readable shape.
"""

from datetime import datetime, timezone

STATUSES = {"HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"}


def build_report(
    title,
    status,
    summary,
    metrics=None,
    evidence=None,
    findings=None,
    problems=None,
    risk=None,
    recommendations=None,
    action_required=None,
    next_steps=None,
):
    """Returns {"structured": {...}, "text": "..."} — structured for future
    automation, text for direct display in chat/WhatsApp/UI."""
    status = (status or "UNKNOWN").upper()
    if status not in STATUSES:
        status = "UNKNOWN"

    structured = {
        "title": title,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "summary": summary or "",
        "metrics": metrics or {},
        "evidence": evidence or [],
        "findings": findings or [],
        "problems": problems or [],
        "risk": risk or "Unknown",
        "recommendations": recommendations or [],
        "action_required": action_required or "None",
        "next_steps": next_steps or [],
    }

    lines = [f"STATUS: {status}", "", "SUMMARY:", structured["summary"] or "(none)"]

    def _section(label, items):
        lines.append("")
        lines.append(f"{label}:")
        if not items:
            lines.append("(none)")
        elif isinstance(items, dict):
            for k, v in items.items():
                lines.append(f"{k}: {v}")
        else:
            for item in items:
                lines.append(f"- {item}")

    _section("METRICS", structured["metrics"])
    _section("FINDINGS", structured["findings"])
    _section("PROBLEMS", structured["problems"])

    lines.append("")
    lines.append(f"RISK: {structured['risk']}")

    _section("RECOMMENDATIONS", structured["recommendations"])

    lines.append("")
    lines.append(f"ACTION REQUIRED: {structured['action_required']}")

    _section("NEXT STEPS", structured["next_steps"])

    return {"structured": structured, "text": "\n".join(lines)}
