"""
Tool-argument coercion helpers for Earth (2026-08-19, P0 of the reliability-
hardening pass -- see core/HERMES_SYSTEM_MESSAGE_ROOTCAUSE_AGENTIC_BEHAVIOR_
AUDIT_2026-08-19.md and the same-day WhatsApp session forensics that found a
3rd same-day occurrence of this exact bug class).

The MCP SDK auto-generates each tool's JSON schema from its Python type
hints -- a plain `phone: str` param rejects a JSON int with a raw Pydantic
validation error. Confirmed live 3 times in one day: the model sends a
phone/query value as a bare number (e.g. `phone: 8801958122329`) instead of
a quoted string, and the call fails outright rather than succeeding with
the obviously-intended value.

Fix philosophy: coerce at the schema layer, not via an LLM retry loop.
Widening the type hint to `str | int | float | None` (so the tool's own
generated schema accepts either shape) plus one coercion line at the top of
the function body eliminates the error class entirely -- no wasted turn,
no chance of the model repeating the same mistake on retry (which the live
transcript showed it does).
"""


def as_str(value) -> str:
    """Coerce an int/float the model sent for a string-typed identifier
    param (phone, query, trace_id, ...) back to the string the tool
    actually needs. None/empty stays as "" (matches every affected tool's
    existing empty-string-means-absent convention). Never raises."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
