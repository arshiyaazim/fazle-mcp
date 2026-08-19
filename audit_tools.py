"""
Task 4 — read-only audit toolkit: lets Hermes investigate the VPS/repos
without shell/terminal access. Every function here is read-only, rooted to
an explicit allowlist, and uses argv-list subprocess calls (shell=False) —
no string ever reaches a shell, so there is no command-injection surface
regardless of what a caller passes as a search query or path, by
construction rather than by sanitization alone.

Joins the existing always-on "fazle-core" MCP toolset (no mode gate) —
these tools are inherently safe (read-only, rooted, bounded), matching
Chat's existing read-only philosophy for the same class of operation.
"""

import os
import re
import subprocess

import fazle_core_client as core
import pii_mask

# See metrics_tools.py's identical constant/comment: Hermes's entire surface
# is admin-gated end-to-end today (hermes.js requireAdmin) — this is only
# correct as long as that holds.
_HERMES_IS_ADMIN_CONTEXT = True

AUDIT_ROOTS = {
    "assistant-platform": "/home/azim/assistant-platform",
    "fazle-core": "/home/azim/core",
}
KB_ROOT = "/home/azim/core/knowledge_base"

# Substring match on the resolved absolute path — deny if any of these
# appear anywhere in it, not just as a filename.
DENY_PATH_SUBSTRINGS = [
    ".env", "secrets/", "secret/", "private_key", ".pem", ".key",
    "id_rsa", "id_ed25519", "credentials", ".git/",
]

EXCLUDE_DIRS = {
    ".git", "node_modules", "venv", "__pycache__", ".pytest_cache", "dist", "build",
    # Found live 2026-08-04: auto-generated coverage reports are enormous
    # HTML dumps of every source line — grep matches them technically
    # correctly but they drown out real results with noise.
    "coverage_html", "htmlcov",
}

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]+-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{10,}"),
]

_PHONE_RE = re.compile(r"(\+?880|0)1[0-9]{9}\b")

MAX_FILE_BYTES = 2 * 1024 * 1024

# Found live 2026-08-04: the original design assumed all three logs were
# plain files. Only fazle-core's actually is — assistant-backend runs in
# Docker (its own log driver, no file on the host) and hermes-runner.service
# has no StandardError= file redirect (defaults to the systemd journal, not
# a runner.log file, despite a stale comment in server.py claiming
# otherwise). Each source now describes its own real retrieval mechanism.
LOG_SOURCES = {
    "backend": {"type": "docker", "service": "assistant-backend", "cwd": "/home/azim/assistant-platform"},
    "fazle-core": {"type": "file", "path": "/home/azim/core/logs/fazle-core.log"},
    "hermes-runner": {"type": "journal", "unit": "hermes-runner.service"},
}


def _redact_line(line):
    for pat in SECRET_PATTERNS:
        line = pat.sub("[REDACTED]", line)
    return _PHONE_RE.sub(lambda m: m.group(0)[:1] + "X" * (len(m.group(0)) - 5) + m.group(0)[-4:], line)


def _resolve_in_root(root_key, rel_or_abs_path=""):
    """Resolve a path and verify it's genuinely inside the named allowlisted
    root (realpath, so no ../ or symlink can escape it). Returns (abs_path,
    error) — error is a string if the path isn't allowed, else None."""
    if root_key not in AUDIT_ROOTS:
        return None, f"unknown root: {root_key!r}. allowed: {list(AUDIT_ROOTS)}"
    root = os.path.realpath(AUDIT_ROOTS[root_key])
    candidate = rel_or_abs_path or root
    if not os.path.isabs(candidate):
        candidate = os.path.join(root, candidate)
    resolved = os.path.realpath(candidate)
    if resolved != root and not resolved.startswith(root + os.sep):
        return None, "path escapes the allowed root"
    for bad in DENY_PATH_SUBSTRINGS:
        if bad in resolved:
            return None, "path is denylisted (looks like a secret/credential location)"
    return resolved, None


def _run(argv, timeout=10, cwd=None):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, "command timed out"
    except OSError as e:
        return None, f"failed to run command: {e}"
    return result, None


def audit_search_code(query: str, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search source code for a literal string (grep -rn), rooted to an
    approved repo. root: 'assistant-platform' or 'fazle-core'."""
    return _grep(query, root, max_results, docs_only=False)


def audit_search_docs(query: str, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search markdown docs for a literal string, rooted to an approved repo."""
    return _grep(query, root, max_results, docs_only=True)


def audit_search_kb(query: str, max_results: int = 20) -> dict:
    """Search fazle-core's knowledge_base/ directory for a literal string."""
    if not query or not isinstance(query, str):
        return {"error": "query required"}
    max_results = min(max(int(max_results or 20), 1), 100)
    exclude_args = []
    for d in EXCLUDE_DIRS:
        exclude_args += ["--exclude-dir", d]
    argv = ["grep", "-rn", *exclude_args, "--", query, KB_ROOT]
    result, err = _run(argv)
    if err:
        return {"error": err}
    if result.returncode not in (0, 1):
        return {"error": f"search failed: {(result.stderr or '').strip()[:500]}"}
    lines = (result.stdout or "").splitlines()[:max_results]
    return {"matches": lines, "truncated": len((result.stdout or "").splitlines()) > max_results}


def _grep(query, root, max_results, docs_only):
    if not query or not isinstance(query, str):
        return {"error": "query required"}
    root_path, err = _resolve_in_root(root)
    if err:
        return {"error": err}
    max_results = min(max(int(max_results or 20), 1), 100)
    exclude_args = []
    for d in EXCLUDE_DIRS:
        exclude_args += ["--exclude-dir", d]
    argv = ["grep", "-rn", *exclude_args]
    if docs_only:
        argv += ["--include=*.md"]
    argv += ["--", query, root_path]
    result, err = _run(argv)
    if err:
        return {"error": err}
    if result.returncode not in (0, 1):
        return {"error": f"search failed: {(result.stderr or '').strip()[:500]}"}
    all_lines = (result.stdout or "").splitlines()
    return {"matches": all_lines[:max_results], "truncated": len(all_lines) > max_results}


def audit_search_logs(query: str, log: str = "backend", max_lines: int = 100, context_lines: int = 0) -> dict:
    """Search a known, allowlisted log source (log: 'backend' — Docker,
    'fazle-core' — file, or 'hermes-runner' — systemd journal) for a
    literal string. Output has secrets and phone numbers redacted before
    being returned. context_lines (file source only): include N lines
    *after* each match — found live 2026-08-04 investigating a real
    Python traceback in fazle-core's log: a bare grep match on the
    "File ..., line N, in func" line alone doesn't include the actual
    exception type/message a few lines below it, which is usually the
    part that actually matters for root-causing."""
    if log not in LOG_SOURCES:
        return {"error": f"unknown log: {log!r}. allowed: {list(LOG_SOURCES)}"}
    source = LOG_SOURCES[log]
    max_lines = min(max(int(max_lines or 100), 1), 500)
    context_lines = min(max(int(context_lines or 0), 0), 30)

    if source["type"] == "file":
        path = source["path"]
        if not os.path.isfile(path):
            return {"matches": [], "note": "log file does not exist (nothing logged yet, or path changed)"}
        if query and context_lines:
            argv = ["grep", "-n", "-A", str(context_lines), "--", query, path]
        elif query:
            argv = ["grep", "-n", "--", query, path]
        else:
            argv = ["tail", "-n", str(max_lines), path]
        result, err = _run(argv)
        if err:
            return {"error": err}
        if result.returncode not in (0, 1):
            return {"error": f"log search failed: {(result.stderr or '').strip()[:500]}"}
        lines = (result.stdout or "").splitlines()[:max_lines]
        return {"matches": [_redact_line(l) for l in lines]}

    if source["type"] == "docker":
        argv = ["docker", "compose", "logs", "--no-color", "--tail", str(max_lines), source["service"]]
        result, err = _run(argv, timeout=15, cwd=source["cwd"])
        if err:
            return {"error": err}
        if result.returncode != 0:
            return {"error": f"docker logs failed: {(result.stderr or '').strip()[:500]}"}
        lines = (result.stdout or "").splitlines()
        if query:
            lines = [l for l in lines if query.lower() in l.lower()]
        return {"matches": [_redact_line(l) for l in lines[:max_lines]]}

    if source["type"] == "journal":
        argv = ["journalctl", "--user", "-u", source["unit"], "-n", str(max_lines), "--no-pager"]
        if query:
            argv += ["-g", query]
        result, err = _run(argv, timeout=15)
        if err:
            return {"error": err}
        if result.returncode != 0:
            return {"error": f"journalctl failed: {(result.stderr or '').strip()[:500]}"}
        lines = (result.stdout or "").splitlines()[:max_lines]
        return {"matches": [_redact_line(l) for l in lines]}

    return {"error": f"unsupported log source type: {source['type']!r}"}


def audit_read_file(path: str, root: str = "assistant-platform", start_line: int = None, end_line: int = None, max_lines: int = 500) -> dict:
    """Read a file (optionally a bounded line range) from an approved root.
    Denies secrets/credential-shaped paths and enforces a size cap."""
    resolved, err = _resolve_in_root(root, path)
    if err:
        return {"error": err}
    if not os.path.isfile(resolved):
        return {"error": "not a file (or does not exist)"}
    size = os.path.getsize(resolved)
    max_lines = min(max(int(max_lines or 500), 1), 2000)
    if size > MAX_FILE_BYTES and start_line is None and end_line is None:
        return {"error": f"file too large ({size} bytes) — pass start_line/end_line for a bounded read"}
    try:
        with open(resolved, "r", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as e:
        return {"error": f"could not read file: {e}"}
    start = max((start_line or 1) - 1, 0)
    end = min(end_line, len(all_lines)) if end_line else min(start + max_lines, len(all_lines))
    return {
        "path": resolved,
        "total_lines": len(all_lines),
        "start_line": start + 1,
        "end_line": end,
        "content": "".join(all_lines[start:end]),
    }


def audit_lookup_whatsapp_messages(
    phone: str = "",
    platform: str = "",
    is_processed: bool = None,
    message_id: int = None,
    limit: int = 20,
) -> dict:
    """Phase 3 (2026-08-04): filtered WhatsApp message lookup for incident
    investigation — trace a specific phone number's message history, check
    a specific message_id, or find unprocessed messages on one bridge.
    Complements get_bridge_message_stats (aggregate only, no phone filter
    at all) and get_recent_messages (unfiltered, most-recent-N only). All
    filters are bound parameters on fazle-core's side — no arbitrary SQL.
    Phone-shaped fields are masked per PII policy (same admin-context
    caveat as metrics_tools.py).

    phone MUST be a string (e.g. "01712345678"), never a bare number/int
    — normalization (canonical form or last-10-digit fallback match) only
    runs on a real string value. Passing a fabricated/placeholder number
    to "test" a hunch and then treating a resulting empty result as proof
    of anything is a real mistake — an empty result for a fake number
    means nothing; only query real, known numbers."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0:
        limit = 20
    limit = min(limit, 100)
    params = {"limit": limit}
    if phone:
        params["phone"] = phone
    if platform:
        params["platform"] = platform
    if is_processed is not None:
        params["is_processed"] = is_processed
    if message_id is not None:
        params["message_id"] = message_id
    result = core.get("/api/whatsapp/messages/lookup", params)
    if "error" in result:
        return result
    result["messages"] = pii_mask.mask_pii(result.get("messages", []), _HERMES_IS_ADMIN_CONTEXT)
    return result


def audit_git_status(repo: str = "assistant-platform") -> dict:
    """Read-only `git status --porcelain` for an approved repo."""
    root_path, err = _resolve_in_root(repo)
    if err:
        return {"error": err}
    result, err = _run(["git", "-C", root_path, "status", "--porcelain"])
    if err:
        return {"error": err}
    if result.returncode != 0:
        return {"error": f"git status failed: {(result.stderr or '').strip()[:500]}"}
    return {"changes": (result.stdout or "").splitlines()}


def audit_recent_commits(repo: str = "assistant-platform", limit: int = 10) -> dict:
    """Read-only recent commit log for an approved repo (git log --oneline)."""
    root_path, err = _resolve_in_root(repo)
    if err:
        return {"error": err}
    limit = min(max(int(limit or 10), 1), 100)
    result, err = _run(["git", "-C", root_path, "log", "-n", str(limit), "--oneline"])
    if err:
        return {"error": err}
    if result.returncode != 0:
        return {"error": f"git log failed: {(result.stderr or '').strip()[:500]}"}
    return {"commits": (result.stdout or "").splitlines()}
