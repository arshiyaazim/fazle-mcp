"""
get_mode_state (2026-08-16) — Hermes's own read of hermes-runner's current
READ/BUILD/RUN mode and remaining Break-Glass TTL.

Real gap this closes: Hermes had no tool of its own to answer "what mode
are you in / how much time is left" — it could only infer from which tools
happened to be available that turn. A live admin test (2026-08-15) hit
exactly this: asked "current mode/TTL?", Hermes had to guess.

Reads hermes-runner's own current_mode.txt directly rather than calling its
HTTP /api/hermes/mode endpoint — these are two independently-deployed
processes (matches the existing cross-process duplication convention, see
scheduler_tools.py's _read_mode(), opencode_tools.py, send_whatsapp_tools.py
for the same MODE_FILE pattern already used three times over). Deliberately
does NOT call the HTTP endpoint: that requires HERMES_RUNNER_SECRET, and a
read-only introspection tool has no business needing a credential fazle-mcp
doesn't otherwise hold. No .env read, no secret of any kind touches this
tool — current_mode.txt is a plain, non-secret status file.

Credential-free AND path-free (2026-08-16 hardening, Owner-directed): the
absolute filesystem path (HERMES_MODE_FILE / its default under the user's
home directory) is an internal implementation detail — where hermes-runner
happens to keep its state file isn't something a model-facing tool result
needs to reveal, and a path leak is a (mild, but real) information-disclosure
smell in its own right. The public "source" field and every user-facing
note/error string now say only "current_mode_file" — never the path itself.
Only the internal `logger.debug` calls below may mention the real path, and
only to a local log file, never to the tool's return value.
"""

import datetime
import json
import logging
import os

logger = logging.getLogger("fazle_mcp.mode_tools")

MODE_FILE = os.environ.get(
    "HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt")
)
MODES = ["READ", "BUILD", "RUN"]

# The only string ever surfaced to the model/caller for "where did this come
# from" — deliberately not the real path (see module docstring).
_SOURCE_LABEL = "current_mode_file"


def get_mode_state() -> dict:
    """Current Hermes operating mode (READ/BUILD/RUN) and, if a Break-Glass
    TTL is active, how many seconds remain before it auto-reverts to READ.
    Zero arguments, read-only, no secrets, no filesystem paths in the
    response — safe to call in any mode."""
    try:
        with open(MODE_FILE, "r") as f:
            raw = f.read().strip()
    except OSError as e:
        # Fail-closed, matching hermes-runner's own read_current_mode() and
        # every other MODE_FILE reader in this codebase — but say so
        # honestly rather than reporting READ as if it were a confirmed
        # live read (2026-08-15 charter: no "verified" claim without
        # evidence). Path goes to the log only, never the return value.
        logger.debug("get_mode_state: could not read %s (%s)", MODE_FILE, e.__class__.__name__)
        return {
            "mode": "READ",
            "expires_at": None,
            "ttl_seconds_remaining": None,
            "scope": None,
            "set_at": None,
            "assumed_fail_closed": True,
            "status": "unverified/degraded",
            "note": f"mode state was unreadable ({e.__class__.__name__}); defaulted to READ, not a confirmed live read",
            "source": _SOURCE_LABEL,
        }

    if not raw:
        logger.debug("get_mode_state: %s is empty", MODE_FILE)
        return {
            "mode": "READ",
            "expires_at": None,
            "ttl_seconds_remaining": None,
            "scope": None,
            "set_at": None,
            "assumed_fail_closed": True,
            "status": "unverified/degraded",
            "note": "mode state is empty; defaulted to READ, not a confirmed live read",
            "source": _SOURCE_LABEL,
        }

    # hermes-runner's mode file may be the newer TTL-aware JSON form
    # ({"mode": ..., "expires_at": ..., "set_at": ..., "scope": ...}) or
    # the legacy bare word — handle both, same as scheduler_tools._read_mode.
    try:
        data = json.loads(raw)
        mode = str(data.get("mode", "")).upper()
        if mode not in MODES:
            mode = "READ"
        expires_at = data.get("expires_at")
        ttl_seconds_remaining = None
        expired = False
        if expires_at:
            try:
                exp = datetime.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                remaining = (exp - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                if remaining <= 0:
                    expired = True
                    mode = "READ"
                else:
                    ttl_seconds_remaining = int(remaining)
            except (ValueError, AttributeError):
                pass
        return {
            "mode": mode,
            "expires_at": expires_at,
            "ttl_seconds_remaining": ttl_seconds_remaining,
            "scope": data.get("scope"),
            "set_at": data.get("set_at"),
            "expired": expired,
            "assumed_fail_closed": False,
            "status": "verified",
            "source": _SOURCE_LABEL,
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Legacy bare-word form (just the mode name, no TTL metadata) is a
        # genuine read, not a fallback. Content that's neither valid JSON
        # NOR a recognized bare mode word is actually unreadable garbage —
        # flag that honestly instead of silently reporting READ as if it
        # were a confirmed live read.
        mode = raw.upper()
        recognized = mode in MODES
        result = {
            "mode": mode if recognized else "READ",
            "expires_at": None,
            "ttl_seconds_remaining": None,
            "scope": None,
            "set_at": None,
            "assumed_fail_closed": not recognized,
            "status": "verified" if recognized else "unverified/degraded",
            "source": _SOURCE_LABEL,
        }
        if not recognized:
            logger.debug("get_mode_state: %s contents unrecognized: %r", MODE_FILE, raw[:80])
            result["note"] = "mode state contents were neither valid JSON nor a recognized mode word; defaulted to READ, not a confirmed live read"
        return result
