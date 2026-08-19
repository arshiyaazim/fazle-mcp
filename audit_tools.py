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
from arg_coerce import as_str

# See metrics_tools.py's identical constant/comment: Hermes's entire surface
# is admin-gated end-to-end today (hermes.js requireAdmin) — this is only
# correct as long as that holds.
_HERMES_IS_ADMIN_CONTEXT = True

AUDIT_ROOTS = {
    "assistant-platform": "/home/azim/assistant-platform",
    "fazle-core": "/home/azim/core",
    # Added 2026-08-14 (re-audit finding): Hermes's own home repos were
    # missing from this allowlist, so investigating them required
    # RUN-mode's unrestricted terminal instead of this read-only toolkit.
    "hermes-runner": "/home/azim/hermes-runner",
    "fazle-mcp": "/home/azim/fazle-mcp",
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


def _is_denylisted(resolved_path):
    """Same substring check _resolve_in_root() applies to a single path —
    factored out (2026-08-14) so _grep()/audit_search_kb() can apply the
    identical policy per-file, instead of maintaining a second copy of it."""
    return any(bad in resolved_path for bad in DENY_PATH_SUBSTRINGS)


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
    if _is_denylisted(resolved):
        return None, "path is denylisted (looks like a secret/credential location)"
    return resolved, None


def _walk_searchable_files(root_path, docs_only):
    """Enumerate files under root_path that _grep()/audit_search_kb() are
    allowed to hand to grep -- same EXCLUDE_DIRS pruning grep's own
    --exclude-dir used to do, plus _is_denylisted() per file (2026-08-14 fix:
    the old grep -r invocation had no per-file equivalent of
    _resolve_in_root()'s denylist check, so .env/secret-shaped files were
    searchable even though audit_read_file() already refused to open them).
    Building the file list ourselves, rather than filtering grep's matched
    output afterward, means a denylisted file's contents are never read by
    grep in the first place."""
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            if docs_only and not fname.endswith(".md"):
                continue
            full = os.path.realpath(os.path.join(dirpath, fname))
            if not _is_denylisted(full):
                yield full


def _run(argv, timeout=10, cwd=None):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, "command timed out"
    except OSError as e:
        return None, f"failed to run command: {e}"
    return result, None


def audit_search_code(query: str | int, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search source code for a literal string (grep -rn), rooted to an
    approved repo. root: 'assistant-platform' or 'fazle-core' — pick the
    repo that actually contains what you're looking for (fazle-core's
    Python modules/ are almost always the right root for business-logic/
    template questions; assistant-platform is the separate Node/React
    admin UI — a query that's really about fazle-core will silently find
    nothing if searched under the wrong root)."""
    return _grep(as_str(query), root, max_results, docs_only=False)


def audit_search_docs(query: str | int, root: str = "assistant-platform", max_results: int = 20) -> dict:
    """Search markdown docs for a literal string, rooted to an approved repo."""
    return _grep(as_str(query), root, max_results, docs_only=True)


def audit_search_kb(query: str | int, max_results: int = 20) -> dict:
    """Search fazle-core's knowledge_base/ directory for a literal string."""
    query = as_str(query)
    if not query:
        return {"error": "query required"}
    max_results = min(max(int(max_results or 20), 1), 100)
    return _grep_files(query, _walk_searchable_files(KB_ROOT, docs_only=False), max_results)


def _grep(query, root, max_results, docs_only):
    query = as_str(query)
    if not query:
        return {"error": "query required"}
    root_path, err = _resolve_in_root(root)
    if err:
        return {"error": err}
    max_results = min(max(int(max_results or 20), 1), 100)
    return _grep_files(query, _walk_searchable_files(root_path, docs_only), max_results)


def _grep_files(query, files, max_results):
    """Run grep -n across an already-denylist-filtered file list (see
    _walk_searchable_files) instead of grep -r on the whole tree, so
    protected files are never opened by grep at all."""
    files = list(files)
    if not files:
        return {"matches": [], "truncated": False}
    result, err = _run(["grep", "-n", "-H", "--", query, *files])
    if err:
        return {"error": err}
    if result.returncode not in (0, 1):
        return {"error": f"search failed: {(result.stderr or '').strip()[:500]}"}
    all_lines = (result.stdout or "").splitlines()
    return {"matches": all_lines[:max_results], "truncated": len(all_lines) > max_results}


def audit_search_logs(query: str | int, log: str = "backend", max_lines: int = 100, context_lines: int = 0) -> dict:
    """Search a known, allowlisted log source (log: 'backend' — Docker,
    'fazle-core' — file, or 'hermes-runner' — systemd journal) for a
    literal string. Output has secrets and phone numbers redacted before
    being returned. context_lines (file source only): include N lines
    *after* each match — found live 2026-08-04 investigating a real
    Python traceback in fazle-core's log: a bare grep match on the
    "File ..., line N, in func" line alone doesn't include the actual
    exception type/message a few lines below it, which is usually the
    part that actually matters for root-causing. query must be a real
    literal string, not a paraphrase/description — search for the exact
    text you expect to appear (a Bengali reply template, an error
    message), not a summary of what it means."""
    query = as_str(query)
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


def audit_read_file(path: str, root: str = "assistant-platform", start_line: int | None = None, end_line: int | None = None, max_lines: int = 500) -> dict:
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
    phone: str | int = "",
    platform: str = "",
    is_processed: bool | None = None,
    message_id: int | None = None,
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
    phone = as_str(phone)
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


def audit_get_auto_reply_settings() -> dict:
    """2026-08-14: read-only visibility into the LIVE auto-reply runtime
    toggles (fazle_runtime_settings, keys 'auto_reply.*') that actually
    govern WhatsApp auto-reply behavior -- distinct from, and authoritative
    over, both the .env static defaults (AUTO_REPLY_ENABLED,
    RECRUITMENT_AUTOREPLY_ENABLED) and the app/config.py Settings class
    defaults. Neither of those was previously checkable by Hermes at all:
    .env is denylisted (secrets-shaped path), and no tool exposed this DB
    table. A forensic report that cited the .env/code-default value as if
    it were the live one was the direct, confirmed cause of a real
    incorrect root-cause claim (2026-08-14 incident).

    Reuses fazle-core's existing GET /api/wa/settings endpoint (already
    authenticated, already scoped to auto_reply.% keys only, already used
    by the admin WhatsApp settings UI) via fazle_core_client — no new
    fazle-core route, no new DB connection from this process, no secret
    values (every returned value is a boolean toggle or a timestamp).

    Returns {"toggles": {key: bool}, "labels": {key: display_name},
    "updated_at": {key: iso_timestamp}} verified live from the database at
    call time, or {"error": ...} if the endpoint could not be reached."""
    result = core.get("/api/wa/settings")
    if "error" in result:
        return result
    return {
        "toggles": result.get("toggles", {}),
        "labels": result.get("labels", {}),
        "updated_at": result.get("updated_at", {}),
        "source": "live database (fazle_runtime_settings via GET /api/wa/settings)",
    }


def get_settings_status() -> dict:
    """2026-08-16 (Owner-approved proposal, core/knowledge_base/00_governance/
    proposal_get_settings_status_20260816.md): non-secret STATIC config —
    app.config.Settings fields + a couple of standalone env vars (which AI
    provider is configured, DRAFT_QUALITY_GATE, scheduler timezone, etc.),
    resolved once at fazle-core process start.

    NOT the same thing as audit_get_auto_reply_settings() above — that's
    the LIVE, admin-editable DB toggle; this is the static default,
    unchanged until someone edits config/env and restarts. This tool's own
    fazle-core endpoint names the overlapping field
    auto_reply_enabled_static_default (not auto_reply_enabled) specifically
    so the two can never be confused in a report — use
    audit_get_auto_reply_settings for "is auto-reply on right now",
    this tool for "what's the configured AI provider / is the draft
    quality gate on / etc."

    fazle-core is the sole source of truth: this wrapper does not read
    .env, does not independently inspect any config file, and cannot
    return anything outside fazle-core's own fixed allowlist — it only
    forwards GET /api/settings/status's response through, unchanged, via
    the existing fazle_core_client (no new fazle-core route beyond that
    one, no new DB connection from this process).

    Returns {"settings": {key: {"value", "type", "provenance"}},
    "source": "static_config"} or {"error": ...} if the endpoint could not
    be reached."""
    result = core.get("/api/settings/status")
    if "error" in result:
        return result
    return {
        "settings": result.get("settings", {}),
        "source": result.get("source", "static_config"),
    }


def audit_get_drafts(phone: str | int = "", status: str = "pending,pending_selfie,edited", limit: int = 20) -> dict:
    """2026-08-15: read-only visibility into pending fazle_draft_replies rows
    -- lets Hermes actually READ a draft's full text before recommending
    whether to approve it, instead of only being able to say a draft ID
    exists (confirmed live gap, same-day incident: Hermes could see draft
    IDs 2941/2935 via other tools but had no way to read their content).

    Reuses fazle-core's existing GET /api/drafts endpoint -- the same
    listing API the admin drafts-review dashboard itself calls -- via
    fazle_core_client. No new fazle-core route, no direct DB connection
    from this process. Read-only: this can never create, edit, approve, or
    send anything -- see approve_draft (send_whatsapp_tools.py) for the
    separate, RUN-mode-and-confirm-gated action that actually sends.

    phone: optional filter (matched against the draft's recipient field,
    client-side -- the underlying endpoint has no phone filter of its own).
    status: comma-separated fazle_draft_replies.status values to include
    (default matches the dashboard's own default: pending/pending_selfie/
    edited -- i.e. "still awaiting a decision"). limit: capped at 100.

    Returns {"drafts": [...], "count": int} with each draft's id, recipient,
    reply_text (full, un-truncated), status, intent, source, created_at,
    role_detected, contact_name -- or {"error": ...} if unreachable. Hermes
    runs in an existing admin-only context (see _HERMES_IS_ADMIN_CONTEXT),
    so phone numbers are not masked here, matching audit_lookup_whatsapp_
    messages' identical existing precedent."""
    phone = as_str(phone)
    try:
        limit = min(max(int(limit or 20), 1), 100)
    except (TypeError, ValueError):
        limit = 20
    result = core.get("/api/drafts", {"status": status, "limit": limit})
    if "error" in result:
        return result
    drafts = result.get("drafts", [])
    if phone:
        digits = "".join(ch for ch in phone if ch.isdigit())
        drafts = [d for d in drafts if digits and digits in (d.get("recipient") or "")]
    # Same admin-context precedent as audit_lookup_whatsapp_messages -- a
    # no-op today (Hermes's surface is admin-gated end-to-end), kept for
    # consistency and so this stays correct if that assumption ever changes.
    # Explicit fields/text_fields: PII_FIELDS/TEXT_SCAN_FIELDS' own defaults
    # were built for wbom_whatsapp_messages' column names (sender_number,
    # message_body), not fazle_draft_replies' (recipient, reply_text).
    drafts = pii_mask.mask_pii(
        drafts, _HERMES_IS_ADMIN_CONTEXT,
        fields={"recipient"}, text_fields={"reply_text"},
    )
    return {"drafts": drafts, "count": len(drafts)}
