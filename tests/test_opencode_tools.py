import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import opencode_tools

_APPROVED_ACTION = {"id": 7, "status": "approved", "action_type": "opencode_dispatch"}


class OpencodeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("opencode_tools.MODE_FILE", self.mode_file)
        self._token_patch = patch("opencode_tools.SVC_TOKEN", "test-token")
        self._mode_patch.start()
        self._token_patch.start()
        # Default: approved_action_id always resolves to a genuinely
        # approved opencode_dispatch action, so existing mode/confirm-gate
        # tests (below) don't also need to think about the new hard gate --
        # each test that specifically exercises the hard gate overrides this.
        self._action_patch = patch("task_tools.get_action", return_value=dict(_APPROVED_ACTION))
        self._action_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        self._token_patch.stop()
        self._action_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestApprovedActionHardGate(OpencodeTestBase):
    """2026-08-19: opencode_dispatch is the highest-blast-radius tool in the
    surface (full-VPS-scope OpenCode) -- it requires a genuinely approved
    hermes_action_approvals row before even the RUN-mode/confirm check."""

    def test_missing_approved_action_id_denied(self):
        self._set_mode("RUN")
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=0, confirm=True)
        self.assertEqual(result["status"], "denied")
        self.assertIn("approved_action_id", result["error"])

    @patch("opencode_tools._request")
    def test_pending_not_approved_action_denied(self, mock_request):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 7, "status": "pending", "action_type": "opencode_dispatch"}):
            self._set_mode("RUN")
            result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "denied")
        self.assertIn("not approved", result["error"])
        mock_request.assert_not_called()
        self._action_patch.start()

    @patch("opencode_tools._request")
    def test_wrong_action_type_denied_even_if_approved(self, mock_request):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 7, "status": "approved", "action_type": "file_mutation"}):
            self._set_mode("RUN")
            result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "denied")
        self.assertIn("action_type", result["error"])
        mock_request.assert_not_called()
        self._action_patch.start()

    @patch("opencode_tools._request")
    def test_unknown_action_id_denied(self, mock_request):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"error": "action not found"}):
            self._set_mode("RUN")
            result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=999, confirm=True)
        self.assertEqual(result["status"], "denied")
        mock_request.assert_not_called()
        self._action_patch.start()

    @patch("opencode_tools._request")
    def test_approved_action_passes_the_gate(self, mock_request):
        self._set_mode("RUN")
        mock_request.side_effect = [{"id": "sess-123"}, {"reply": "done"}]
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["approved_action_id"], 7)


class TestDispatchModeGate(OpencodeTestBase):
    @patch("opencode_tools._request")
    def test_denied_in_read_mode(self, mock_request):
        self._set_mode("READ")
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "denied")
        mock_request.assert_not_called()

    @patch("opencode_tools._request")
    def test_denied_in_build_mode_even_with_confirm(self, mock_request):
        self._set_mode("BUILD")
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "denied")
        mock_request.assert_not_called()

    @patch("opencode_tools._request")
    def test_denied_in_run_mode_without_confirm(self, mock_request):
        self._set_mode("RUN")
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=False)
        self.assertEqual(result["status"], "denied")
        mock_request.assert_not_called()

    def test_empty_prompt_rejected_before_mode_check(self):
        # Not even a mode file exists in this case — confirms the prompt
        # validation runs first and doesn't depend on _read_mode() at all.
        result = opencode_tools.opencode_dispatch("   ", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("prompt", result["error"])

    @patch("opencode_tools._request")
    def test_allowed_in_run_mode_with_confirm_dispatches_session_then_prompt(self, mock_request):
        self._set_mode("RUN")
        mock_request.side_effect = [
            {"id": "sess-123"},
            {"reply": "done, see commit abc123", "model": {"providerID": "omniroute", "id": "groq/llama-3.3-70b-versatile"}},
        ]
        result = opencode_tools.opencode_dispatch("fix the stuck-message backlog", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["session_id"], "sess-123")
        self.assertEqual(result["reply"], "done, see commit abc123")
        self.assertEqual(mock_request.call_count, 2)
        first_call, second_call = mock_request.call_args_list
        self.assertEqual(first_call.args[:2], ("POST", "/session"))
        self.assertEqual(second_call.args[:2], ("POST", "/session/sess-123/prompt"))

    @patch("opencode_tools._request")
    def test_session_creation_failure_surfaces_cleanly(self, mock_request):
        self._set_mode("RUN")
        mock_request.return_value = {"error": "opencode unreachable: connection refused"}
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("unreachable", result["error"])

    @patch("opencode_tools._request")
    def test_missing_session_id_in_response_surfaces_cleanly(self, mock_request):
        self._set_mode("RUN")
        mock_request.return_value = {"not_id": "whatever"}
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("session id", result["error"])

    @patch("opencode_tools._request")
    def test_prompt_failure_after_session_created_still_reports_session_id(self, mock_request):
        self._set_mode("RUN")
        mock_request.side_effect = [
            {"id": "sess-456"},
            {"error": "opencode timed out waiting for a reply"},
        ]
        result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["session_id"], "sess-456")


class TestDispatchTokenGate(OpencodeTestBase):
    def test_unconfigured_token_returns_clear_error_not_a_crash(self):
        self._token_patch.stop()  # restore real (unset-in-tests) SVC_TOKEN
        with patch("opencode_tools.SVC_TOKEN", ""):
            self._set_mode("RUN")
            result = opencode_tools.opencode_dispatch("fix the bug", approved_action_id=7, confirm=True)
            self.assertEqual(result["status"], "error")
            self.assertIn("not configured", result["error"])
        self._token_patch.start()


class TestCheck(OpencodeTestBase):
    @patch("opencode_tools._request")
    def test_check_is_ungated_even_in_read_mode(self, mock_request):
        self._set_mode("READ")
        mock_request.return_value = [{"id": "m1", "type": "assistant", "content": []}]
        result = opencode_tools.opencode_check("sess-123")
        self.assertEqual(result["status"], "success")
        mock_request.assert_called_once_with("GET", "/session/sess-123/messages")

    def test_missing_session_id_rejected(self):
        result = opencode_tools.opencode_check("")
        self.assertEqual(result["status"], "error")
        self.assertIn("session_id", result["error"])

    @patch("opencode_tools._request")
    def test_upstream_error_surfaces_cleanly(self, mock_request):
        mock_request.return_value = {"error": "opencode unreachable: connection refused"}
        result = opencode_tools.opencode_check("sess-123")
        self.assertEqual(result["status"], "error")
        self.assertIn("unreachable", result["error"])


class TestReadMode(OpencodeTestBase):
    def test_missing_mode_file_defaults_to_read(self):
        self.assertEqual(opencode_tools._read_mode(), "READ")

    def test_garbage_mode_file_defaults_to_read(self):
        with open(self.mode_file, "w") as f:
            f.write("not a real mode")
        self.assertEqual(opencode_tools._read_mode(), "READ")

    def test_legacy_bare_word_mode_file(self):
        self._set_mode("RUN")
        self.assertEqual(opencode_tools._read_mode(), "RUN")

    def test_json_ttl_form_respected(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": None}, f)
        self.assertEqual(opencode_tools._read_mode(), "RUN")

    def test_expired_ttl_falls_back_to_read(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": "2020-01-01T00:00:00Z"}, f)
        self.assertEqual(opencode_tools._read_mode(), "READ")


if __name__ == "__main__":
    unittest.main()
