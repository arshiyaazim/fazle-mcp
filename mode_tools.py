"""
get_mode_state / set_mode_state (2026-08-16 / 2026-08-24) — Hermes's own
read AND write of hermes-runner's current READ/BUILD/RUN mode and
remaining Break-Glass TTL.

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

import httpx

logger = logging.getLogger("fazle_mcp.mode_tools")

MODE_FILE = os.environ.get(
    "HERMES_MODE_FILE", os.path.expanduser("~/hermes-runner/current_mode.txt")
)
MODES = ["READ", "BUILD", "RUN"]
SCOPES = ["TIME", "TASK", "SESSION"]

# hermes-runner HTTP endpoint — used only by set_mode_state() to delegate
# all validation, TTL enforcement, and audit logging to hermes-runner's
# own write_mode_state() rather than duplicating that logic here.
# Deliberately separate from MODE_FILE (which is a direct file read for
# the read-only get_mode_state, credential-free).  set_mode_state needs
# the Bearer secret because it mutates state.
_RUNNER_URL = (os.environ.get("HERMES_RUNNER_URL") or "").rstrip("/")
_RUNNER_SECRET = os.environ.get("HERMES_RUNNER_SECRET") or ""

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


# ── set_mode_state (2026-08-24, Task 19) ─────────────────────────────────
# Calls hermes-runner's own POST /mode endpoint so all validation, TTL
# enforcement, audit logging, and atomic file-write logic stays in one
# canonical place (hermes-runner/server.py::write_mode_state).  This tool
# is a thin HTTP relay, not a second mode-write implementation.
#
# Requires HERMES_RUNNER_URL + HERMES_RUNNER_SECRET in fazle-mcp's env
# block (~/.hermes/config.yaml).  Fails closed (clear error) when either
# is missing — never falls through to a direct file write.

_SET_MODE_TIMEOUT_SECONDS = 10.0

# 2026-09-30 (Owner decision). Mode elevation is a privilege grant, and this
# function is reachable only from the model (its single caller is the
# set_mode_state MCP tool), so for one pass it was denied outright. That was
# too blunt: the Owner is the intended requester, and an Admin asking Hermes
# to do something in the Bridge2 -> Bridge1 control conversation should not
# have to leave that conversation to switch modes.
#
# The distinction that matters is scope, not the word RUN. What is restored
# here is a SHORT-LIVED, EXPLICITLY REQUESTED window -- the relay always
# already holds the RUN toolset (hermes-runner forces it for
# readonly:whatsapp_relay), so this function is not what grants capability.
# It only aligns the persisted mode with the Admin's instruction so the
# RUN-gated tools read a consistent state.
#
# It is deliberately NOT standing authority:
#
#   * it only ever *raises* mode, never lowers it back to READ, so it cannot
#     be used to quietly revoke a real administrative downgrade;
#   * callers get a hard ceiling (MAX_MODE_TTL_SECONDS) well under the
#     hermes-runner 24h maximum, so a mistaken request cannot park the
#     system in RUN for a day;
#   * an omitted ttl_seconds is refused rather than defaulted, because the
#     30-minute hermes-runner default is far longer than "the Admin just
#     confirmed this one action" should ever mean;
#   * and crucially, RUN alone still confers no outbound authority. Every
#     real send still needs the Admin authorization grant enforced in
#     fazle-core (modules.admin_hermes_action_grant), so elevating the mode
#     cannot become a back door to sending.
#
# The authorized human path is untouched: assistant-platform's hermes router
# applies requireAuth+requireAdmin and relays POST /mode straight to
# hermes-runner for a full administrative mode change. This is the narrow
# conversational window, not a replacement for it.
MAX_MODE_TTL_SECONDS = 600

_SHORT_TTL_ERROR = (
    "set_mode_state requires an explicit ttl_seconds for a conversational "
    f"elevation, and it must be {MAX_MODE_TTL_SECONDS} seconds or less. This "
    "is a short window for the action the Admin just confirmed, not a "
    "standing mode change. For a real mode change the Admin uses the admin "
    "UI (POST /hermes/mode), which is admin-authenticated and not routed "
    "through you."
)


def set_mode_state(
    mode: str,
    ttl_seconds: int | None = None,
    scope: str | None = None,
) -> dict:
    """Elevate Hermes mode for a short, explicitly requested window.

    Delegates to hermes-runner's own POST /mode endpoint, so all validation,
    TTL enforcement, audit logging, and atomic file-write logic stays in one
    canonical place (hermes-runner/server.py::write_mode_state).

    Refuses to lower the mode, refuses a TTL longer than
    MAX_MODE_TTL_SECONDS, and refuses an absent TTL. See the comment above
    for why the mode is not the authority boundary: outbound authority comes
    from the Admin authorization grant enforced in fazle-core, not from here.

    Returns a dict with the new mode state on success, or an error dict on
    failure. Never raises.
    """
    mode_upper = (mode or "").strip().upper()
    if mode_upper not in MODES:
        return {"ok": False, "error": f"mode must be one of {MODES}, got {mode!r}"}

    if mode_upper == "READ":
        return {
            "ok": False,
            "error": (
                "set_mode_state cannot be used to lower the mode. Lowering it "
                "is an administrative action and must go through the admin UI "
                "(POST /hermes/mode), not through a model-reachable tool."
            ),
        }

    if ttl_seconds is None:
        return {"ok": False, "error": _SHORT_TTL_ERROR}
    try:
        ttl_seconds = int(ttl_seconds)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"ttl_seconds must be an integer, got {ttl_seconds!r}"}
    if ttl_seconds < 1 or ttl_seconds > MAX_MODE_TTL_SECONDS:
        return {
            "ok": False,
            "error": (
                f"ttl_seconds must be between 1 and {MAX_MODE_TTL_SECONDS} for a "
                f"conversational elevation, got {ttl_seconds}."
            ),
        }

    if not _RUNNER_URL:
        return {
            "ok": False,
            "error": "HERMES_RUNNER_URL not configured in fazle-mcp environment",
        }
    if not _RUNNER_SECRET:
        return {
            "ok": False,
            "error": "HERMES_RUNNER_SECRET not configured in fazle-mcp environment",
        }

    payload: dict = {"mode": mode_upper, "ttl_seconds": ttl_seconds}
    if scope is not None:
        scope_upper = scope.strip().upper()
        if scope_upper not in SCOPES:
            return {"ok": False, "error": f"scope must be one of {SCOPES}, got {scope!r}"}
        payload["scope"] = scope_upper

    try:
        resp = httpx.post(
            f"{_RUNNER_URL}/mode",
            headers={"Authorization": f"Bearer {_RUNNER_SECRET}"},
            json=payload,
            timeout=_SET_MODE_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        logger.warning("[mode_tools] set_mode_state: hermes-runner timed out")
        return {"ok": False, "error": "hermes-runner did not respond in time"}
    except Exception as exc:
        logger.warning("[mode_tools] set_mode_state: request failed: %s", exc)
        return {"ok": False, "error": f"could not reach hermes-runner: {exc.__class__.__name__}"}

    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "error": f"hermes-runner returned non-JSON (status {resp.status_code})"}

    if resp.status_code != 200:
        return {"ok": False, "error": data.get("error", f"hermes-runner returned status {resp.status_code}")}

    return {
        "ok": True,
        "mode": data.get("mode"),
        "set_at": data.get("set_at"),
        "expires_at": data.get("expires_at"),
        "ttl_seconds_remaining": data.get("seconds_remaining"),
        "scope": data.get("scope"),
        "set_by": data.get("set_by"),
        "source": _SOURCE_LABEL,
        "note": (
            "A short conversational window only. It is not standing RUN "
            "authority: outbound still requires the Admin to confirm the "
            "specific action in the Bridge2 control conversation."
        ),
    }
