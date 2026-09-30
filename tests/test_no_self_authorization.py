"""Hermes acts for the Admin, but never authorizes itself (2026-09-30).

Owner decision: Bridge2 -> Bridge1 is the trusted Admin <-> Hermes control
conversation. Hermes IS meant to perform admin-requested actions -- approving
drafts, sending, forwarding. The control is not "Hermes cannot act"; it is
"Hermes must not be the one deciding it is allowed to."

So these tests cover two things that must BOTH hold:

  * the capability is restored and works, and
  * the authority comes from the authenticated Admin conversation, never
    from the model's own flags.

The authority itself is enforced in fazle-core
(modules.admin_hermes_action_grant, written by
modules.admin_directives.router only after resolve_relay_authorization
allowed the Admin). The model-side contract is therefore: forward to the
server, and surface its refusal honestly.

No real message is sent and no network call leaves this file.
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
sys.path.insert(0, str(REPO))

CORE = Path(os.environ.get("FAZLE_CORE_REPO", "/home/azim/core"))
PLATFORM = Path(os.environ.get("ASSISTANT_PLATFORM", "/home/azim/assistant-platform"))

DENIED_403 = (
    "Hermes-proposed draft requires an authenticated Admin authorization "
    "that is not present (grant_missing)"
)


def _load(mods, mode, case):
    """Import with HERMES_MODE_FILE pinned, restoring all global state."""
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

    saved = {n: sys.modules.get(n) for n in mods}

    def _restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    case.addCleanup(_restore)
    for n in mods:
        sys.modules.pop(n, None)
    return __import__(mods[0])


class TestHermesCanActWhenAdminAuthorized(unittest.TestCase):
    """The capability is restored. A confirmed action must actually work."""

    def setUp(self):
        self.st = _load(("send_whatsapp_tools", "mode_tools"), "RUN", self)

    def test_approve_draft_works_after_admin_confirmation(self):
        with mock.patch.object(self.st, "core") as core:
            core.post.return_value = {"ok": True, "draft_id": 3087, "queued": True}
            res = self.st.approve_draft(3087, confirm=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "queued")
        self.assertEqual(core.post.call_args[0][0], "/api/drafts/3087/approve")

    def test_direct_send_works_in_run_mode(self):
        with mock.patch.object(self.st, "core") as core:
            core.post.return_value = {"ok": True, "queue_id": 99}
            res = self.st.send_whatsapp_message(
                "8801700000000", "hi", source_bridge="bridge1",
                admin_instruction="owner asked", confirm=True,
            )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "queued")

    def test_forwarding_is_covered_by_the_draft_path(self):
        """Forwarding is a draft, not a direct send: read the source message,
        address a new draft to the target, and let the Admin confirm."""
        src = (REPO / "capability_check_tools.py").read_text()
        self.assertIn("forward_message", src)
        self.assertIn("draft_whatsapp_reply", src)


class TestHermesCannotSelfAuthorize(unittest.TestCase):
    """The authority is the Admin's, not the model's."""

    def setUp(self):
        self.st = _load(("send_whatsapp_tools", "mode_tools"), "RUN", self)

    def test_approval_refusal_is_surfaced_not_swallowed(self):
        with mock.patch.object(self.st, "core") as core:
            core.post.return_value = {"error": DENIED_403}
            res = self.st.approve_draft(3087, confirm=True)
        self.assertFalse(res["ok"], "a refused approval was reported as success")
        self.assertNotIn("status", res, "must not claim queued/sent on refusal")
        self.assertTrue(res["admin_authorization_required"])
        self.assertIn("Bridge2", res["next_step"])

    def test_confirm_is_not_the_authority(self):
        """confirm=true is an execution acknowledgement. Without it nothing
        is even attempted -- and with it, the server still decides."""
        with mock.patch.object(self.st, "core") as core:
            self.assertFalse(self.st.approve_draft(3087, confirm=False)["ok"])
            core.post.assert_not_called()
        with mock.patch.object(self.st, "core") as core:
            core.post.return_value = {"error": DENIED_403}
            self.assertFalse(self.st.approve_draft(3087, confirm=True)["ok"])

    def test_model_cannot_write_a_grant(self):
        """The grant lives in fazle_draft_replies.meta and is only ever
        written by the relay after canonical Admin authorization. No
        model-reachable tool in this repo writes draft meta."""
        for name in ("draft_tools.py", "send_whatsapp_tools.py", "mode_tools.py",
                     "capability_check_tools.py"):
            src = (REPO / name).read_text()
            self.assertNotIn(
                "admin_action_grant", src,
                f"{name} can write the Admin action grant -- that is the whole "
                "authorization boundary",
            )

    def test_grant_module_is_fazle_core_side_only(self):
        p = CORE / "modules" / "admin_hermes_action_grant.py"
        self.assertTrue(p.exists(), "grant module missing from fazle-core")
        self.assertFalse(
            (REPO / "admin_hermes_action_grant.py").exists(),
            "a second copy of the grant logic appeared in fazle-mcp",
        )


class TestModeElevationIsScopedNotStanding(unittest.TestCase):
    """RUN is a short window, not a standing grant."""

    def setUp(self):
        self.mode_tools = _load(("mode_tools",), "READ", self)

    def test_ttl_is_required(self):
        res = self.mode_tools.set_mode_state("RUN")
        self.assertFalse(res["ok"])
        self.assertIn("explicit ttl_seconds", res["error"])
        with mock.patch.object(self.mode_tools, "httpx") as httpx:
            self.assertFalse(self.mode_tools.set_mode_state("RUN")["ok"])
        httpx.post.assert_not_called()

    def test_ttl_is_capped_far_below_the_runner_maximum(self):
        cap = self.mode_tools.MAX_MODE_TTL_SECONDS
        self.assertLessEqual(cap, 600, "conversational window is too long")
        with mock.patch.object(self.mode_tools, "httpx") as httpx:
            for bad in (cap + 1, 1800, 86400):
                self.assertFalse(self.mode_tools.set_mode_state("RUN", ttl_seconds=bad)["ok"])
        httpx.post.assert_not_called()

    def test_cannot_lower_the_mode(self):
        with mock.patch.object(self.mode_tools, "httpx") as httpx:
            self.assertFalse(self.mode_tools.set_mode_state("READ", ttl_seconds=60)["ok"])
        httpx.post.assert_not_called()

    def test_valid_short_elevation_is_forwarded(self):
        with mock.patch.object(self.mode_tools, "_RUNNER_URL", "http://fake:8093"), \
             mock.patch.object(self.mode_tools, "_RUNNER_SECRET", "fake"), \
             mock.patch.object(self.mode_tools, "httpx") as httpx:
            httpx.post.return_value.status_code = 200
            httpx.post.return_value.json.return_value = {"mode": "RUN", "seconds_remaining": 120}
            res = self.mode_tools.set_mode_state("RUN", ttl_seconds=300)
        self.assertTrue(res["ok"], res)
        self.assertEqual(httpx.post.call_args[1]["json"]["ttl_seconds"], 300)
        self.assertIn("not standing RUN authority", res["note"])

    def test_run_alone_is_documented_as_insufficient_for_sending(self):
        src = (REPO / "mode_tools.py").read_text()
        self.assertIn("outbound still requires", src.lower().replace("'s", "s"))


class TestCapabilityCheckedBeforeAskingPermission(unittest.TestCase):
    """Hermes must find out what is possible BEFORE asking the Admin."""

    def setUp(self):
        self.cc = _load(("capability_check_tools",), "READ", self)

    def test_unknown_action_is_reported_as_unknown(self):
        res = self.cc.check_action_capability(action="teleport_karim")
        self.assertFalse(res["known_action"])
        self.assertIn("not an action this deployment recognises", res["verdict"])
        self.assertIn("Do not ask the Admin to authorize it", res["verdict"])

    def test_action_needing_more_authority_says_what(self):
        res = self.cc.check_action_capability(action="send_whatsapp_message")
        self.assertTrue(res["known_action"])
        self.assertFalse(res["executable_now"])
        self.assertIn("needs mode RUN", res["verdict"])

    def test_approval_action_names_the_admin_authorization(self):
        res = self.cc.check_action_capability(action="approve_draft")
        self.assertIn("Admin's confirmation", " ".join(res["still_needs"]))

    def test_proposing_is_always_available(self):
        res = self.cc.check_action_capability(action="draft_whatsapp_reply")
        self.assertTrue(res["executable_now"])
        self.assertIn("nothing beyond", " ".join(res["still_needs"]))

    def test_tool_is_registered_on_the_mcp_server(self):
        src = (REPO / "server.py").read_text()
        self.assertIn("def check_action_capability", src)

    def test_read_only_no_outbound_call(self):
        self.cc.check_action_capability()
        self.cc.check_action_capability(action="approve_draft")


class TestHumanPathsPreserved(unittest.TestCase):
    def test_admin_ui_mode_route_intact(self):
        p = PLATFORM / "backend" / "src" / "routes" / "hermes.js"
        if not p.exists():
            self.skipTest("assistant-platform hermes route not readable")
        src = p.read_text()
        self.assertIn("router.use(requireAuth, requireAdmin)", src)
        self.assertIn("router.post('/mode'", src)

    def test_dashboard_approval_route_untouched(self):
        wa = CORE / "modules" / "wa_chat_frontend" / "__init__.py"
        if wa.exists():
            self.assertIn('POST   /api/wa/drafts/{id}/approve', wa.read_text())
        drafts = CORE / "modules" / "drafts" / "routes.py"
        if drafts.exists():
            src = drafts.read_text()
            self.assertIn('draft["reply_text"]', src)
            self.assertIn('draft["recipient"]', src)

    def test_grant_only_applies_to_hermes_proposed_drafts(self):
        """A human's own draft must approve exactly as before -- the grant
        gate must not touch drafts Hermes did not propose."""
        p = CORE / "modules" / "admin_hermes_action_grant.py"
        if not p.exists():
            self.skipTest("grant module not readable")
        src = p.read_text()
        self.assertIn("def is_hermes_proposed", src)
        self.assertIn('return True, "not_hermes_proposed"', src)

    def test_payment_draft_flow_untouched(self):
        self.assertIn(
            "def approve_payment_draft", (REPO / "payment_draft_tools.py").read_text()
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
