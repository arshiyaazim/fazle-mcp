"""task_action_policy plugin — code-level enforcement for Earth's task/
action-approval control plane (2026-08-19, Owner-directed "Claude-Code-
level autonomous coding" pass).

Registers on the `pre_tool_call` hook (same real, already-shipped
mechanism the bundled `security-guidance` plugin already uses -- see
`plugins/security-guidance/__init__.py` in this same hermes-agent
checkout for the reference shape this file follows: a callback taking
`tool_name`/`args`/`**_`, returning `None` to allow or
`{"action": "block", "message": "..."}` to veto the call before it ever
executes).

What this gates, and what it deliberately does NOT gate (Owner's explicit
"do not over-gate the coding loop" requirement):

  - Any tool other than `write_file`/`patch`/`terminal` -> always allowed,
    zero overhead (search, read, MCP tools, execute_code -- MCP tools are
    already gated at fazle-mcp's own layer, execute_code is treated as
    lower-risk sandboxed execution, not further gated this pass).
  - `write_file`/`patch` (WORKSPACE_MUTATION) -> allowed only inside a
    live, unexpired BUILD-authorization scope covering the target path
    (checked via fazle-core's GET /api/tasks/authorizing-path). Reversible
    via git -- gating every edit would defeat the agent.
  - `terminal` -> the command string is classified (action_policy.py,
    byte-identical copy of fazle-mcp/action_policy.py -- keep both in
    sync when editing either):
      READ_ONLY / SAFE_EXECUTION -> allowed, no network call at all.
      REPOSITORY_MUTATION (git commit) -> the real `git diff --cached`
        in the command's own workdir is hashed and compared against an
        approved hermes_action_approvals row's stored diff -- exact
        content match required, not just "some approval exists".
      SERVICE_MUTATION / DATABASE_MUTATION / DEPLOYMENT -> allowed only
        if a matching approved+unexecuted action of that action_type
        exists (a smaller guarantee than the commit case -- no diff
        concept applies to `systemctl restart`).
      DESTRUCTIVE -> always blocked, no approval path exists for this
        category at all (defense-in-depth on top of the CLI's own
        tools/approval.py HARDLINE_PATTERNS/DANGEROUS_PATTERNS, which
        remain the primary defense and are untouched by this plugin).

Fails closed (blocks) only for a gated category whose authorization can't
be verified (network error, fazle-core unreachable, ambiguous match) --
never fails open on a mutation. Fails open (allows, zero latency) for
everything not in a gated category -- this plugin must never become the
reason an ordinary read/search/test command is slow or flaky.

Auth: reads core/.env's INTERNAL_API_KEY directly (same-host, same-user
file, root-owned-directory-adjacent, 0600-equivalent permissions already
relied on by this repo's own ad-hoc scripts) -- no new credential.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from typing import Any, Dict, Optional

from . import action_policy

logger = logging.getLogger(__name__)

_GATED_TERMINAL_TOOL = "terminal"
_FILE_MUTATION_TOOLS = ("write_file", "patch")

# execute_code is gated on the pre-existing generic "production_write"
# approval type -- the same bucket action_policy.action_type_for() returns
# for an unrecognized mutation class. Deliberately not a new action_type:
# this audit must not grow the approval vocabulary.
_CODE_EXECUTION_TOOL = "execute_code"
_CODE_EXECUTION_ACTION_TYPE = "production_write"


def _normalize_command(cmd: str) -> str:
    """Whitespace/quote-insensitive-ish comparison for target binding.

    Deliberately conservative: collapse runs of whitespace and strip. It is
    NOT a shell parser, so it can only ever make two *identical-looking*
    commands match -- it never widens the set of commands an approval
    covers beyond a literal comparison.
    """
    return " ".join((cmd or "").split())

_FAZLE_CORE_URL = os.environ.get("FAZLE_CORE_URL", "http://127.0.0.1:8200")
_CORE_ENV_PATH = os.path.expanduser("~/core/.env")
_HTTP_TIMEOUT_S = 5


def _plugin_disabled() -> bool:
    return os.environ.get("TASK_ACTION_POLICY_DISABLE", "").lower() in {"1", "true", "yes", "on"}


def _internal_api_key() -> str:
    try:
        with open(_CORE_ENV_PATH) as f:
            for line in f:
                if line.startswith("INTERNAL_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError as e:
        logger.error("task_action_policy: could not read %s: %s", _CORE_ENV_PATH, e)
    return ""


def _fazle_core_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """Read-only GET to fazle-core. Returns None on any failure (network,
    auth, non-200, non-JSON) -- callers treat None as "could not verify",
    which for a gated category means block (fail closed)."""
    key = _internal_api_key()
    if not key:
        return None
    try:
        import httpx
        resp = httpx.get(
            f"{_FAZLE_CORE_URL}{path}", params=params or {},
            headers={"X-Internal-Key": key}, timeout=_HTTP_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        logger.warning("task_action_policy: fazle-core GET %s failed: %s", path, e)
        return None


def _block(message: str) -> Dict[str, str]:
    return {"action": "block", "message": message}


def _check_workspace_mutation(args: Any) -> Optional[Dict[str, str]]:
    path = ""
    if isinstance(args, dict):
        path = args.get("path") or args.get("file_path") or ""
    if not path:
        # No path to check -- fail closed for this gated category rather
        # than guess.
        return _block(
            "task_action_policy: could not determine the target file path for this write -- "
            "cannot verify BUILD authorization. Retry with an explicit path."
        )
    result = _fazle_core_get("/api/tasks/authorizing-path", {"path": path})
    if result is None:
        return _block(
            f"task_action_policy: could not reach fazle-core to verify BUILD authorization for "
            f"{path!r} -- failing closed. If the admin has already said 'fix it'/'go ahead', call "
            "authorize_build(task_id=..., repos=[...]) first; otherwise investigate and report before editing."
        )
    if not result.get("authorized"):
        return _block(
            f"task_action_policy: {path!r} is not inside any currently-authorized BUILD scope. "
            "Call authorize_build(task_id=<current task>, repos=[...]) only after the admin has "
            "actually said something like 'fix it'/'go ahead'/'implement it' for this task -- "
            "otherwise investigate and report first, don't edit yet."
        )
    return None


def _check_git_commit(cmd: str, workdir: Optional[str]) -> Optional[Dict[str, str]]:
    if not workdir:
        return _block(
            "task_action_policy: 'git commit' requires an explicit workdir so the real diff can be "
            "verified against an approved action -- retry with workdir set to the target repo."
        )
    try:
        diff_result = subprocess.run(
            ["git", "-C", workdir, "diff", "--cached"],
            capture_output=True, text=True, timeout=10,
        )
        real_diff = diff_result.stdout or ""
    except Exception as e:
        return _block(f"task_action_policy: could not compute the real staged diff in {workdir!r}: {e}")
    # 2026-08-20, live-discovered: a model retyping the diff into a tool-call
    # argument (rather than piping the literal captured stdout through
    # byte-for-byte) reliably drops the trailing newline `git diff --cached`
    # always ends with -- a real, harmless transcription artifact, not a
    # content difference. Normalize ONLY trailing newlines/whitespace on
    # both sides before hashing; do not strip/normalize anything else --
    # any other discrepancy (added/changed/reordered lines) must still
    # block, exactly as it did when this same live test caught a third
    # attempt where the model appended fabricated trailing text to the diff.
    real_hash = hashlib.sha256(real_diff.rstrip().encode("utf-8", errors="replace")).hexdigest()

    result = _fazle_core_get("/api/actions", {"status": "approved", "action_type": "git_commit"})
    if result is None:
        return _block(
            "task_action_policy: could not reach fazle-core to verify a commit authorization -- "
            "failing closed. Call authorize_action(action_type='git_commit', diff=<the real "
            "`git diff --cached` output>, ...) only after the admin has said 'commit it' for this "
            "exact change."
        )
    for action in result.get("actions", []):
        approved_diff = action.get("diff") or ""
        if hashlib.sha256(approved_diff.rstrip().encode("utf-8", errors="replace")).hexdigest() == real_hash:
            return None  # exact content match (ignoring trailing whitespace) -- allow
    return _block(
        "task_action_policy: no approved commit authorization matches the actual staged diff in "
        f"{workdir!r}. The approval must cover EXACTLY this diff -- if the working tree changed "
        "since the admin said 'commit it', call authorize_action(action_type='git_commit', "
        "diff=<the current real diff>, ...) again with the up-to-date diff."
    )


def _check_category_matched(action_type: str, workdir: Optional[str], cmd: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Does a CURRENT, UNEXPIRED approval of this action_type authorize it?

    2026-09-29 (Admin Canary capability-boundary audit) -- correctness fix,
    not a policy change. This used to call ``/api/actions?status=approved``
    and allow whenever the list came back non-empty. That endpoint is a
    *discovery* helper whose own docstring states it "Does NOT filter out
    expired rows here -- callers that need an authorization decision should
    use is_action_approved(action_id) ... which does enforce expiry". The
    gate was using the discovery helper as the decision, so every expired
    approval row ever left behind authorized its action forever. Live
    evidence: 9 approved rows, all expired in Aug 2026, which meant
    ``systemctl restart``, ``git push`` and ``psql ... INSERT`` were all
    passing this "enforcement layer".

    It now calls ``/api/actions/authorization-state``, which fazle-core
    answers by delegating to the existing ``is_action_approved`` for every
    candidate -- the canonical expiry check, reused rather than reimplemented
    here. The expiry decision stays in exactly one place.

    Target binding: when a currently-valid approval recorded the concrete
    command, the command about to run must be one of them. Only approvals
    that carry a command constrain; if none do, consent stays at
    action-type granularity (the historical behavior, now genuinely
    expiry-scoped).
    """
    result = _fazle_core_get("/api/actions/authorization-state", {"action_type": action_type})
    if result is None:
        return _block(
            f"task_action_policy: could not reach fazle-core to verify a {action_type} authorization "
            "-- failing closed. Call authorize_action(action_type=%r, ...) only after the admin has "
            "given an explicit, current instruction naming this exact action." % action_type
        )
    if not result.get("authorized"):
        rejected = result.get("rejected") or []
        detail = ""
        if rejected:
            detail = " (%d candidate approval(s) found but none are current: %s)" % (
                len(rejected),
                ", ".join(
                    "#%s status=%s expires_at=%s" % (r.get("id"), r.get("status"), r.get("expires_at"))
                    for r in rejected[:5]
                ),
            )
        return _block(
            f"task_action_policy: no CURRENT {action_type} authorization found.{detail} "
            f"Call authorize_action(action_type={action_type!r}, ...) only after the admin has given an "
            "explicit, current instruction naming this exact action -- never infer it from a general "
            "'do whatever's necessary'."
        )

    approved_commands = [c for c in (result.get("commands") or []) if c]
    if approved_commands and cmd is not None:
        if _normalize_command(cmd) not in {_normalize_command(c) for c in approved_commands}:
            return _block(
                f"task_action_policy: a current {action_type} approval exists, but none of them covers "
                f"this exact command. Authorized command(s): {approved_commands!r}. Re-authorize with "
                "the command you actually intend to run."
            )
    return None


def _check_workspace_mutation_command(cmd: str, workdir: Optional[str]) -> Optional[Dict[str, str]]:
    """Terminal-based workspace mutation now needs a current file_mutation approval.

    2026-09-29 (Admin Canary capability-boundary audit). ``sed -i``,
    ``echo > file``, ``dd of=...``, ``chmod`` and ``python3 -c "open(...).write()"``
    all classify as WORKSPACE_MUTATION, and WORKSPACE_MUTATION was
    returned "allowed" for the terminal tool with no gate at all -- so a
    single shell command could mutate the working tree with no
    authorization whatsoever, while the identical mutation through
    ``write_file`` correctly required a BUILD scope. That asymmetry was the
    bypass.

    This routes the terminal form through the SAME existing approval plane
    (``file_mutation`` approvals, which is what action_type_for()
    already maps this category to), rather than introducing a new gate or
    trying to extract and authorize individual paths out of arbitrary shell.
    It cannot be a false-negative-prone path parser, so it does not attempt
    to be one: unparseable-but-mutating commands land in WORKSPACE_MUTATION
    (the classifier's conservative default) and now fail closed here.
    """
    return _check_category_matched(
        action_policy.action_type_for("WORKSPACE_MUTATION"), workdir, cmd
    )


def _check_code_execution(args: Any) -> Optional[Dict[str, str]]:
    """``execute_code`` is a mutation entry point, and was entirely ungated.

    2026-09-29. Two independent reasons this had to be closed:

    1. The sandboxed child reaches Hermes tools over RPC
       (``SANDBOX_ALLOWED_TOOLS`` includes write_file / patch / terminal),
       and those RPC dispatches do fire this hook -- so they were at least
       governed. The script's OWN body was not: hermes' "sandbox" is
       environment-scrubbing only, with no bwrap/seccomp/namespace/chroot,
       so plain ``open(path,'w').write(...)`` or ``os.remove(...)`` inside
       the submitted code runs directly in the child, never reaches the RPC
       allowlist, and never fires any hook.

    2. Per the Owner's instruction for this audit, a tool that cannot be
       reliably distinguished as read-only vs mutating *before* execution is
       treated as mutation. Python source is exactly that.

    Gated on the existing generic ``production_write`` approval type -- the
    bucket action_type_for() already returns for unrecognized mutation
    classes. No new action type, no new approval store, no parallel plane.
    """
    result = _fazle_core_get("/api/actions/authorization-state", {"action_type": _CODE_EXECUTION_ACTION_TYPE})
    if result is None:
        return _block(
            "task_action_policy: could not reach fazle-core to verify a production_write authorization "
            "-- failing closed before running any submitted code."
        )
    if not result.get("authorized"):
        return _block(
            "task_action_policy: execute_code is treated as a MUTATION entry point and needs a CURRENT "
            "production_write approval. That is deliberate: hermes' execute_code sandbox only scrubs the "
            "child environment (no bwrap/seccomp/namespace), so code can touch the filesystem directly "
            "without ever reaching the tool RPC allowlist or this hook. For a genuinely read-only "
            "question, use the read/search tools or a READ_ONLY terminal command instead -- if the admin "
            "has really asked for code to be run, call authorize_action(action_type='production_write', "
            "summary=...) for this specific task first."
        )
    return None


def check_action_policy(
    tool_name: str = "", args: Any = None, **_: Any,
) -> Optional[Dict[str, str]]:
    if _plugin_disabled():
        return None

    if tool_name in _FILE_MUTATION_TOOLS:
        return _check_workspace_mutation(args)

    if tool_name == _CODE_EXECUTION_TOOL:
        return _check_code_execution(args)

    if tool_name != _GATED_TERMINAL_TOOL:
        return None  # not a tool this plugin governs -- allow, zero overhead

    cmd = (args or {}).get("command", "") if isinstance(args, dict) else ""
    workdir = (args or {}).get("workdir") if isinstance(args, dict) else None
    category = action_policy.classify_terminal_command(cmd)

    if category == "READ_ONLY":
        return None

    if category == "SAFE_EXECUTION":
        return None

    if category == "WORKSPACE_MUTATION":
        # 2026-09-29: previously returned None here (free pass). A raw shell
        # mutation and the same mutation via write_file must be governed
        # identically; see _check_workspace_mutation_command.
        return _check_workspace_mutation_command(cmd, workdir)

    if category == "DESTRUCTIVE":
        return _block(
            "task_action_policy: this command is classified DESTRUCTIVE -- no approval can "
            "authorize it. If this is genuinely necessary, it needs a separate, explicit Owner "
            "decision outside this control plane, not a task/action authorization."
        )

    if category == "REPOSITORY_MUTATION":
        return _check_git_commit(cmd, workdir)

    if category in ("SERVICE_MUTATION", "DATABASE_MUTATION", "DEPLOYMENT"):
        return _check_category_matched(action_policy.action_type_for(category), workdir, cmd)

    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", check_action_policy)
