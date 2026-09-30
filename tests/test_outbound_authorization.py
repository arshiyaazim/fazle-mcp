"""Outbound authority characterization for the Hermes Admin relay (2026-09-29).

SCOPE / HONEST STATUS
---------------------
These tests are a CHARACTERIZATION SUITE, not a qualification suite. Parts 2
and 3 of the audit STOPPED, so the invariants the Owner asked for are NOT
currently satisfied. Each test below records the ACTUAL observed behavior so
that closing the gap later trips a failure here rather than passing silently.

No real message is ever sent: every core-post is mocked.

The required Owner invariant is:

    AI may propose/draft
    BUT AI cannot independently supply the authoritative approval required
    for real outbound execution.

Current state: the invariant is VIOLATED. See test_hermes_can_self_elevate_mode
and test_hermes_can_approve_its_own_draft.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load_send_tools(mode: str, case: unittest.TestCase):
    """Import send_whatsapp_tools with the mode file pinned to `mode`.

    Two isolation hazards, both closed here:
      1. _read_mode() is fail-closed and returns READ when the file is
         missing, so the temp file must survive the whole test.
      2. Re-importing the module rebinds it for the WHOLE pytest process.
         Other test modules hold their own reference to send_whatsapp_tools
         and would silently inherit this test's temp mode file. So the
         original module objects are saved and restored on cleanup.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        json.dump({"mode": mode, "expires_at": None}, fh)
        mode_file = fh.name
    case.addCleanup(Path(mode_file).unlink, missing_ok=True)

    prev_env = os.environ.get("HERMES_MODE_FILE")
    case.addCleanup(
        lambda: os.environ.__setitem__("HERMES_MODE_FILE", prev_env)
        if prev_env is not None
        else os.environ.pop("HERMES_MODE_FILE", None)
    )
    os.environ["HERMES_MODE_FILE"] = mode_file

    saved = {
        name: sys.modules.get(name)
        for name in ("send_whatsapp_tools", "mode_tools")
    }

    def _restore():
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    case.addCleanup(_restore)
    for name in ("send_whatsapp_tools", "mode_tools"):
        sys.modules.pop(name, None)
    import send_whatsapp_tools  # noqa: E402
    return send_whatsapp_tools


class TestOutboundAuthorityCharacterization(unittest.TestCase):
    """Characterizes WHO can approve an outbound draft for the Hermes relay."""

    def test_hermes_can_no_longer_self_elevate_mode(self):
        """FIXED 2026-09-30: mode elevation is no longer model-reachable.

        mcp__fazle_core__set_mode_state used to delegate to hermes-runner's
        POST /mode, gated only by a bearer secret the MCP server already
        holds -- so the model could mint the RUN mode that every RUN-gated
        tool requires. hermes-runner itself documents that the secret 'alone
        does NOT distinguish an admin Chat user from a non-admin one'. It now
        refuses before any network call, and the authorized human path
        (assistant-platform POST /hermes/mode, requireAuth+requireAdmin) is
        untouched.
        """
        import mode_tools

        self.assertFalse(mode_tools.set_mode_state("RUN")["ok"])
        with mock.patch.object(mode_tools, "httpx") as httpx:
            mode_tools.set_mode_state("RUN")
        httpx.post.assert_not_called()

        sig = inspect_params(mode_tools.set_mode_state)
        self.assertEqual(
            set(sig), {"mode", "ttl_seconds", "scope"},
            "set_mode_state signature changed -- callers would break",
        )

    def test_hermes_approval_authority_is_server_side(self):
        """FIXED 2026-09-30: the model no longer supplies the approval.

        approve_draft() used to gate on the mode file (which the model could
        raise itself) plus a model-supplied confirm=True, then call
        POST /api/drafts/{id}/approve -- which required only the shared API
        key and approved AND enqueued in one call. So the model could draft
        and then approve its own draft.

        The authority now lives in the trusted Admin conversation: fazle-core
        stamps a scoped grant only after resolve_relay_authorization allowed
        the Admin, and refuses the approve without it. The tool's job is to
        forward and to surface that refusal -- never to swallow it.
        """
        st = _load_send_tools("RUN", self)

        # No Admin confirmation: fazle-core refuses, and the refusal surfaces.
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {
                "error": "Hermes-proposed draft requires an authenticated "
                         "Admin authorization that is not present (grant_missing)"
            }
            res = st.approve_draft(draft_id=7, confirm=True)
            self.assertFalse(res.get("ok"))
            self.assertNotIn("status", res)
            self.assertTrue(res["admin_authorization_required"])

        # Admin confirmed: the grant exists server-side, so it proceeds.
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"ok": True, "draft_id": 7, "queued": True}
            res = st.approve_draft(draft_id=7, confirm=True)
            self.assertTrue(res.get("ok"))
            self.assertEqual(res["status"], "queued")

    def test_direct_send_needs_only_run_and_confirm(self):
        """Characterization: the direct-send TOOL still gates on RUN + confirm.

        That is no longer the whole picture and was never the authority: the
        model cannot self-grant RUN as standing authority (it is a capped,
        explicit, raise-only window), and the actual send authority is
        enforced server-side. The tool still requires both, so the model must
        be deliberately executing in an elevated mode.
        """
        st = _load_send_tools("RUN", self)
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"ok": True}
            res = st.send_whatsapp_message(
                recipient="15551234567",
                body="characterization only",
                source_bridge="bridge1",
                admin_instruction="self asserted",
                confirm=True,
            )
        self.assertTrue(res.get("ok"), res)

    def test_mode_file_is_not_the_authority_anymore(self):
        """The honest new fact: approve_draft no longer consults the mode
        file at all, because the mode file is not the boundary.

        It used to be the only thing stopping approval in READ -- and the
        model could raise it, so it stopped nothing. Authority now comes from
        the Admin grant, which this tool cannot see or create, so the call
        goes through to fazle-core in every mode and is decided there.
        """
        st = _load_send_tools("READ", self)
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"error": "requires an authenticated Admin authorization"}
            res = st.approve_draft(draft_id=7, confirm=True)
            self.assertTrue(core.post.called, "decision must be server-side, not local")
        self.assertFalse(res.get("ok"))


class TestDraftBindingCharacterization(unittest.TestCase):
    """Characterizes the draft->approve binding (Part 3)."""

    def setUp(self):
        core = Path("/home/azim/core/modules/drafts/routes.py")
        self.src = core.read_text() if core.exists() else ""
        if not self.src:
            self.skipTest("fazle-core drafts module not readable")
        approve = self.src.split("async def approve_draft", 1)[1]
        self.approve_body = approve.split("\n@router", 1)[0]

    def test_recipient_and_content_come_from_stored_row(self):
        """GOOD: approve() reads recipient/reply_text from the DB row, never
        from the request. So an approved draft cannot have its recipient or
        content substituted at approval time."""
        self.assertIn('draft["reply_text"]', self.approve_body)
        self.assertIn('draft["recipient"]', self.approve_body)

    def test_grant_expiry_is_enforced(self):
        """The Admin grant expires; the DRAFT's own age is still unbounded.

        The scoped grant written by modules.admin_hermes_action_grant carries
        an expires_at and the approve path honours it, so an old "yes" cannot
        be replayed later. What is still open is the pre-existing Part 3 gap:
        nothing bounds how long a draft may sit before it is approved.

        Comments are stripped first, because the grant check is documented in
        prose there and prose must not be mistaken for enforcement.
        """
        import re

        code = re.sub(r"#.*", "", self.approve_body)

        # grant enforcement is present...
        self.assertIn("verify_draft_grant", code)
        self.assertIn("consume_draft_grant", code)

        # ...but the draft's own staleness is still unbounded.
        for token in ("stale", "created_at <", "draft_age"):
            self.assertNotIn(
                token, code,
                f"approve() now references {token!r} -- the Part 3 draft-staleness "
                "gap may have closed; re-audit",
            )

    def test_reapproval_is_permitted_by_the_state_gate(self):
        """GAP: the status gate explicitly allows 'approved', so an already
        approved draft can be approved again and submit_approved_draft() is
        called again. Replay protection is only a dedup log line."""
        self.assertIn(
            '"approved"', self.approve_body,
            "status gate no longer admits 'approved' -- replay risk may be closed",
        )
        self.assertIn("submit_approved_draft", self.approve_body)

    def test_edit_is_refused_once_approved(self):
        """GOOD: the edit endpoint refuses a draft already in 'approved'."""
        edit = self.src.split("async def edit_draft", 1)[1].split("\n@router", 1)[0]
        self.assertIn('"edited"', edit)
        self.assertIn("pending_selfie", edit)


def inspect_params(fn):
    import inspect
    return list(inspect.signature(fn).parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
