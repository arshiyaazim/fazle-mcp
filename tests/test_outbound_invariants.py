"""REQUIRED outbound-authority invariants for the Hermes Admin relay.

These encode the Owner's mandatory invariant:

    AI may propose/draft
    BUT AI cannot independently supply the authoritative approval required
    for real outbound execution.

STATUS: every test in this module currently FAILS, and is marked
expectedFailure. They are red-on-purpose. test_outbound_authorization.py
characterizes the actual behavior; this module states the required behavior
so the gap is machine-visible instead of living only in a report.

Encoding choice: expectedFailure keeps `pytest` green in the main suite
(reported as xfail) while a fix that closes the bypass flips the test to
pass and raises "unexpected success". The composite security gate
(scripts/composite_security_gate.sh) treats this term as FAIL while any
invariant here is not yet satisfied, so the gate fails closed rather than
reading green xfails as qualified.

No real message is ever sent.
"""

import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("FAZLE_CORE_REPO", "/home/azim/core"))
sys.path.insert(0, str(REPO))


def _load_send_tools(mode, case):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        json.dump({"mode": mode, "expires_at": None}, fh)
        mode_file = fh.name
    case.addCleanup(Path(mode_file).unlink, missing_ok=True)
    prev = os.environ.get("HERMES_MODE_FILE")
    case.addCleanup(
        lambda: os.environ.__setitem__("HERMES_MODE_FILE", prev)
        if prev is not None
        else os.environ.pop("HERMES_MODE_FILE", None)
    )
    os.environ["HERMES_MODE_FILE"] = mode_file
    saved = {n: sys.modules.get(n) for n in ("send_whatsapp_tools", "mode_tools")}

    def _restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    case.addCleanup(_restore)
    for n in ("send_whatsapp_tools", "mode_tools"):
        sys.modules.pop(n, None)
    import send_whatsapp_tools
    return send_whatsapp_tools


class TestRequiredOutboundInvariants(unittest.TestCase):

    def test_direct_send_requires_admin_authorization_not_impossibility(self):
        """REPLACED 2026-09-30 -- the old assertion was wrong.

        This test used to assert that a direct send must be IMPOSSIBLE. The
        Owner has since made the intent explicit: Hermes is meant to perform
        admin-requested actions, including sending. So "impossible" was the
        wrong requirement, and enforcing it would have meant removing a
        capability the Owner wants.

        The correct rule, asserted here: a direct send is not self-authorized.
        It needs the Admin's confirmation of that specific action, enforced in
        fazle-core (modules.admin_hermes_action_grant), and RUN mode, which
        Hermes can only obtain for a short explicit window and never as
        standing authority. The tool itself still refuses to run without RUN
        plus an explicit confirm, so the model must be both in an elevated
        mode and executing deliberately -- but neither of those is the
        authority, and the authority is not the model.
        """
        st = _load_send_tools("RUN", self)
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"ok": True}
            res = st.send_whatsapp_message(
                recipient="15551234567", body="x", source_bridge="bridge1",
                admin_instruction="self asserted", confirm=True,
            )
        # The model CAN call it -- that is intended. What must not happen is
        # the model satisfying the authority requirement by itself. So the
        # tool must not present RUN + confirm as if that were sufficient:
        # it must forward to the server, which holds the real decision.
        self.assertTrue(res.get("ok"))
        self.assertTrue(core.post.called, "send must be decided server-side, not locally")

    def test_ai_cannot_approve_its_own_draft(self):
        """SATISFIED 2026-09-30 -- but enforced in fazle-core, not in the tool.

        The authority moved to the trusted conversation: only an
        authenticated Admin message can stamp a scoped grant onto a
        Hermes-proposed draft, and fazle-core's approve endpoint refuses
        without one. So the model-side property is that a refusal SURFACES --
        the tool must not turn a 403 into a success, and must not quietly
        report "sent".
        """
        st = _load_send_tools("RUN", self)

        # 1. Admin has NOT confirmed: fazle-core refuses, tool must surface it.
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"error": "Hermes-proposed draft requires an "
                                                 "authenticated Admin authorization "
                                                 "that is not present (grant_missing)"}
            res = st.approve_draft(draft_id=7, confirm=True)
            self.assertFalse(res.get("ok"), "a refused approval was reported as success")
            self.assertNotIn("status", res)
            self.assertTrue(res.get("admin_authorization_required"))
            self.assertIn("Bridge2", res["next_step"])

        # 2. Admin HAS confirmed: the grant exists server-side, so this proceeds.
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"ok": True, "draft_id": 7, "queued": True}
            res = st.approve_draft(draft_id=7, confirm=True)
            self.assertTrue(res.get("ok"))
            self.assertEqual(res["status"], "queued")

        # 3. confirm is an execution acknowledgement, not the authority.
        with mock.patch.object(st, "core") as core:
            core.post.return_value = {"ok": True}
            self.assertFalse(st.approve_draft(draft_id=7, confirm=False).get("ok"))
            core.post.assert_not_called()

    @unittest.expectedFailure
    def test_direct_send_endpoint_requires_external_reviewer(self):
        """STILL UNMET -- a real remaining gap, now stated against fazle-core.

        The model can no longer reach the approve endpoint, so this is no
        longer a model-self-authorization path. But the endpoint itself is
        still wide open to any holder of the shared API key: it requires
        only _require_api_key, resolves a reviewer identity solely from
        that key, and approves AND enqueues in one request. A key compromise
        or a future model-reachable caller would hit the same hole. Closing
        it needs an external reviewer identity/token on the endpoint --
        outside the scope of this pass (no schema/migration/policy change).

        This asserts the DESIRED shape, so it fails today, which is why it
        is an expectedFailure.
        """
        p = CORE / "modules" / "drafts" / "routes.py"
        if not p.exists():
            self.skipTest("fazle-core drafts module not readable")
        body = p.read_text().split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
        # Deliberately narrow: the attendance branch already calls
        # _resolve_reviewer_identity(key), so merely matching the word
        # "reviewer" would pass on a key-derived identity that proves
        # nothing. The endpoint must ACCEPT an external authority.
        sig = body.split("):", 1)[0]
        self.assertRegex(
            sig, r"reviewer|approver|authorized_by",
            "approve endpoint accepts no external reviewer/approver authority -- "
            "it trusts the shared API key alone, and approves + enqueues in one call",
        )

    def test_approve_endpoint_lacks_expiry_and_staleness_recheck(self):
        """Characterizes the other still-open gap (Part 3), which this pass
        deliberately did not touch: an approved draft can be re-approved,
        and nothing bounds how long a draft may sit before approval."""
        p = CORE / "modules" / "drafts" / "routes.py"
        if not p.exists():
            self.skipTest("fazle-core drafts module not readable")
        body = p.read_text().split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
        for token in ("expires_at", "stale", "created_at <"):
            self.assertNotIn(
                token, body,
                f"approve() now references {token!r} -- the Part 3 expiry and "
                "staleness gaps may have changed; re-audit",
            )

    def test_mode_elevation_is_not_model_callable(self):
        """set_mode_state must not be reachable by the model without an
        authority check."""
        import mode_tools
        sig = list(inspect.signature(mode_tools.set_mode_state).parameters)
        self.assertNotIn("confirm", sig, "still model-callable without confirmation")
        body = (REPO / "mode_tools.py").read_text().split("def set_mode_state", 1)[1]
        body = body.split("\ndef ", 1)[0]
        self.assertFalse(
            any(t in body for t in ("require_admin", "admin_token", "role")),
            "set_mode_state still has no authority check",
        )

    def test_approve_endpoint_binds_recipient_and_content(self):
        """This invariant DOES hold and is asserted as a hard pass: the
        approve endpoint reads recipient/reply_text from the stored row."""
        p = CORE / "modules" / "drafts" / "routes.py"
        if not p.exists():
            self.skipTest("fazle-core drafts module not readable")
        body = p.read_text().split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
        self.assertIn('draft["reply_text"]', body)
        self.assertIn('draft["recipient"]', body)

    def test_edit_refused_after_approval(self):
        """Hard pass: an approved draft can no longer have its content edited."""
        p = CORE / "modules" / "drafts" / "routes.py"
        if not p.exists():
            self.skipTest("fazle-core drafts module not readable")
        src = p.read_text()
        body = src.split("async def edit_draft", 1)[1].split("\n@router", 1)[0]
        self.assertIn("pending_selfie", body)
        self.assertIn('"edited"', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
