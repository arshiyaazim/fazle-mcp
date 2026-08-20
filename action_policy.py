"""
Canonical action-classification taxonomy for Earth (2026-08-19, Owner-
directed "Claude-Code-level autonomous coding" pass).

Owner's explicit requirement: distinguish operations that are inherently
safe/read-only from operations with real side effects, via ONE canonical
policy layer rather than scattering string checks across tools. This module
is that layer.

Duplicated, not shared, at two locations -- same "deliberately duplicated"
convention already established repeatedly in this codebase for
`_read_mode()` etc., because the two callers run in genuinely different
Python processes/environments with no shared import path:
  - fazle-mcp/action_policy.py (this file) -- used by task_tools.py when
    composing action proposals, for consistency.
  - ~/.hermes/plugins/task_action_policy/action_policy.py -- used by the
    CLI's own pre_tool_call hook plugin to classify a raw terminal command
    or file operation BEFORE it becomes a fazle-mcp tool call at all.
Keep both copies byte-identical when editing either one.
"""
from __future__ import annotations

import re

CATEGORIES = (
    "READ_ONLY", "SAFE_EXECUTION", "WORKSPACE_MUTATION", "REPOSITORY_MUTATION",
    "SERVICE_MUTATION", "DATABASE_MUTATION", "DEPLOYMENT", "DESTRUCTIVE",
)

# Order matters -- first match wins. DESTRUCTIVE patterns are checked first
# (deliberately overlapping the Hermes CLI's own tools/approval.py
# HARDLINE_PATTERNS/DANGEROUS_PATTERNS blocklist -- defense in depth, not a
# replacement for it) so nothing below can accidentally shadow a dangerous
# command into a weaker category.
_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f\b", r"\brm\s+-[a-z]*f[a-z]*r\b",           # rm -rf / -fr (any flag order)
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f[a-z]*d\b", r"\bgit\s+clean\s+-[a-z]*d[a-z]*f\b",
    r"\bgit\s+push\s+.*--force\b", r"\bgit\s+push\s+.*-f\b",
    r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)",   # DELETE FROM ... with no WHERE clause
    r"\bmkfs\b", r"\bdd\s+.*of=/dev/",
]

_SERVICE_MUTATION_PATTERNS = [
    r"\bsystemctl\s+(restart|stop|start|reload)\b",
    r"restart_gate\.sh\s+--restart\b",
    r"\bnginx\s+-s\s+reload\b",
    r"\bsystemctl\s+--user\s+restart\b",
]
_DATABASE_MUTATION_PATTERNS = [
    r"\bdb/migrate\.py\b",
    r"\bpsql\b.*\b(INSERT|UPDATE|DELETE)\b",
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b",
]
_DEPLOYMENT_PATTERNS = [
    r"\bdeploy\.sh\b", r"\bgit\s+push\b(?!.*--force)",  # a plain push is deployment-adjacent, not destructive
]
_REPOSITORY_MUTATION_PATTERNS = [
    r"\bgit\s+commit\b",
]
_SAFE_EXECUTION_PATTERNS = [
    r"\bpytest\b", r"\bpython3?\s+-m\s+pytest\b", r"\bnpm\s+test\b",
    r"\bruff\b", r"\bflake8\b", r"\bmypy\b", r"\bpylint\b", r"\beslint\b",
    r"\bpython3?\s+-c\s+.*ast\.parse\b",  # syntax-check idiom used throughout this repo today
    r"\bnode\s+--check\b",
]
_READ_ONLY_PATTERNS = [
    r"^\s*cat\b", r"^\s*grep\b", r"^\s*rg\b", r"^\s*find\b", r"^\s*ls\b",
    r"^\s*sed\s+-n\b",  # sed used as a read/print, not in-place edit (-i)
    r"\bgit\s+(status|log|diff|show|branch)\b",
    r"\bjournalctl\b",
    r"\bcurl\b.*\b(health|/api/)\w*\b",
    r"\bSELECT\b",
]


def classify_terminal_command(cmd: str) -> str:
    """Classify a raw shell command string. First matching category wins,
    checked in descending risk order so nothing dangerous falls through to
    a weaker bucket by accident. Unrecognized commands default to
    WORKSPACE_MUTATION (the conservative middle -- gated the same as a
    file edit, i.e. requires an authorized BUILD scope, never treated as
    either fully-free or as DESTRUCTIVE)."""
    if not cmd or not cmd.strip():
        return "READ_ONLY"
    for pattern in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "DESTRUCTIVE"
    for pattern in _SERVICE_MUTATION_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "SERVICE_MUTATION"
    for pattern in _DATABASE_MUTATION_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "DATABASE_MUTATION"
    for pattern in _DEPLOYMENT_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "DEPLOYMENT"
    for pattern in _REPOSITORY_MUTATION_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "REPOSITORY_MUTATION"
    for pattern in _SAFE_EXECUTION_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "SAFE_EXECUTION"
    for pattern in _READ_ONLY_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "READ_ONLY"
    return "WORKSPACE_MUTATION"


def classify_file_op(tool_name: str, path: str = "") -> str:
    """File read/search tools -> READ_ONLY; file write/edit/create/delete
    tools -> WORKSPACE_MUTATION (reversible via git; gated by an
    authorized BUILD scope, not by a per-edit approval -- see the CLI
    plugin's own logic for why)."""
    name = (tool_name or "").lower()
    if any(k in name for k in ("read", "search", "grep", "find", "list", "stat")):
        return "READ_ONLY"
    return "WORKSPACE_MUTATION"


def requires_gated_approval(category: str) -> bool:
    """True for the categories that need an approved hermes_action_approvals
    row before proceeding. False for READ_ONLY/SAFE_EXECUTION (never gated)
    and WORKSPACE_MUTATION (gated by BUILD-scope authorization instead, a
    different, broader mechanism -- see find_authorizing_task_for_path).
    DESTRUCTIVE is deliberately False here too -- it is never approvable
    around, only hard-blocked."""
    return category in ("REPOSITORY_MUTATION", "SERVICE_MUTATION", "DATABASE_MUTATION", "DEPLOYMENT")


def action_type_for(category: str) -> str:
    """Maps a classification category to hermes_action_approvals.action_type."""
    return {
        "REPOSITORY_MUTATION": "git_commit",
        "SERVICE_MUTATION": "restart",
        "DATABASE_MUTATION": "migration",
        "DEPLOYMENT": "deploy",
        "WORKSPACE_MUTATION": "file_mutation",
    }.get(category, "production_write")
