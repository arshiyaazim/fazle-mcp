import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import task_tools


class TaskToolsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("task_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestReadTools(TaskToolsTestBase):
    @patch("task_tools.core.get")
    def test_get_tasks_passes_filters(self, mock_get):
        mock_get.return_value = {"tasks": []}
        task_tools.get_tasks(status="IN_PROGRESS", owner="earth", limit=10)
        args, kwargs = mock_get.call_args
        params = args[1]
        self.assertEqual(params["status"], "IN_PROGRESS")
        self.assertEqual(params["owner"], "earth")
        self.assertEqual(params["limit"], 10)

    @patch("task_tools.core.get")
    def test_get_task_hits_correct_path(self, mock_get):
        mock_get.return_value = {"id": 5}
        task_tools.get_task(5)
        mock_get.assert_called_once_with("/api/tasks/5")

    @patch("task_tools.core.get")
    def test_get_pending_actions_no_task_id_omits_params(self, mock_get):
        mock_get.return_value = {"actions": []}
        task_tools.get_pending_actions()
        mock_get.assert_called_once_with("/api/actions", None)

    @patch("task_tools.core.get")
    def test_get_action_hits_correct_path(self, mock_get):
        mock_get.return_value = {"id": 7, "status": "approved"}
        result = task_tools.get_action(7)
        mock_get.assert_called_once_with("/api/actions/7")
        self.assertEqual(result["status"], "approved")


class TestCreateTask(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_no_mode_gate_needed(self, mock_post):
        # create_task is Tier B -- no RUN/confirm gate, works from any mode.
        mock_post.return_value = {"ok": True, "task_id": 1}
        self._set_mode("READ")
        result = task_tools.create_task("Fix the thing")
        self.assertTrue(result["ok"])
        mock_post.assert_called_once()

    @patch("task_tools.core.post")
    def test_int_trace_id_coerced_to_string(self, mock_post):
        mock_post.return_value = {"ok": True, "task_id": 1}
        task_tools.create_task("Title", trace_id=12345)
        body = mock_post.call_args.args[1]
        self.assertEqual(body["trace_id"], "12345")


class TestProposeAction(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_no_mode_gate_needed(self, mock_post):
        mock_post.return_value = {"ok": True, "action_id": 7}
        self._set_mode("READ")
        result = task_tools.propose_action("file_mutation", "fix typo", risk="low")
        self.assertTrue(result["ok"])


class TestApproveRejectActionGating(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_approve_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = task_tools.approve_action(7, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("task_tools.core.post")
    def test_approve_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = task_tools.approve_action(7)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("task_tools.core.post")
    def test_approve_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"action_id": 7, "status": "approved"}
        result = task_tools.approve_action(7, confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/actions/7/approve", {})

    @patch("task_tools.core.post")
    def test_reject_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = task_tools.reject_action(7, reason="too risky", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("task_tools.core.post")
    def test_reject_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"action_id": 7, "status": "rejected"}
        result = task_tools.reject_action(7, reason="too risky", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/actions/7/reject", {"reason": "too risky"})


class TestAuthorizeBuild(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_no_mode_gate_needed(self, mock_post):
        mock_post.return_value = {"ok": True, "task_id": 5, "build_authorized": True}
        self._set_mode("READ")
        result = task_tools.authorize_build(5, ["/home/azim/core"])
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/tasks/5/authorize-build", {"repos": ["/home/azim/core"], "ttl_hours": 4},
        )


class TestAuthorizeActionGating(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = task_tools.authorize_action("git_commit", "commit the fix", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("task_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = task_tools.authorize_action("git_commit", "commit the fix")
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("task_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"action_id": 9, "status": "approved"}
        result = task_tools.authorize_action(
            "git_commit", "commit the routing fix", task_id=5, diff="- old\n+ new", confirm=True,
        )
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/actions/authorize", {
            "action_type": "git_commit", "summary": "commit the routing fix", "risk": "medium",
            "task_id": 5, "diff": "- old\n+ new",
        })


class TestAuthorizeActionTaskScopedBuildAuthorizationSatisfiesRunGate(TaskToolsTestBase):
    """2026-08-20 (Owner-directed): the web /mode dropdown must not be a
    second mandatory authorization source for a WhatsApp Super Admin
    coding task that already has a live authorize_build grant -- covers
    the exact live-discovered gap (authorize_action rejected with
    "current execution mode is READ, not RUN" despite task #10 already
    being build_authorized)."""

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_read_mode_allowed_when_task_has_live_build_authorization(self, mock_post, mock_get):
        self._set_mode("READ")
        mock_get.return_value = {"id": 10, "build_authorized": True, "build_expires_at": None}
        mock_post.return_value = {"action_id": 9, "status": "approved"}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=10, diff="- old\n+ new", confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode_at_execution"], "TASK_BUILD_AUTHORIZED")
        mock_get.assert_called_once_with("/api/tasks/10")
        mock_post.assert_called_once()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_read_mode_still_denied_when_task_has_no_build_authorization(self, mock_post, mock_get):
        """The target task_id itself must carry the authorization -- a
        task that was never authorize_build'd is still denied in READ
        mode, exactly as before this fix."""
        self._set_mode("READ")
        mock_get.return_value = {"id": 11, "build_authorized": False}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=11, diff="x", confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_task_isolation_task_a_authorization_does_not_satisfy_task_b_gate(self, mock_post, mock_get):
        """Task A's build authorization must never leak into Task B's
        authorize_action call -- the lookup is always scoped to the
        specific task_id the caller is trying to authorize an action for,
        never 'is anything, anywhere, authorized'."""
        self._set_mode("READ")
        # Task 12 (the one actually targeted) has NO authorization, even
        # though some other task might.
        mock_get.return_value = {"id": 12, "build_authorized": False}
        result = task_tools.authorize_action(
            "git_commit", "commit into repo B", task_id=12, diff="x", confirm=True,
        )
        self.assertFalse(result["ok"])
        mock_get.assert_called_once_with("/api/tasks/12")
        mock_post.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_read_mode_denied_when_task_build_authorization_expired(self, mock_post, mock_get):
        import datetime
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        self._set_mode("READ")
        mock_get.return_value = {"id": 10, "build_authorized": True, "build_expires_at": past}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=10, diff="x", confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_read_mode_allowed_when_build_authorization_not_yet_expired(self, mock_post, mock_get):
        import datetime
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        self._set_mode("READ")
        mock_get.return_value = {"id": 10, "build_authorized": True, "build_expires_at": future}
        mock_post.return_value = {"action_id": 9, "status": "approved"}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=10, diff="x", confirm=True,
        )
        self.assertTrue(result["ok"])

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_no_task_id_still_requires_true_run_mode(self, mock_post, mock_get):
        """The two-step approve_action/reject_action path, and any
        authorize_action call with no task_id at all, are completely
        unaffected by this fix -- still require real RUN mode."""
        self._set_mode("READ")
        result = task_tools.authorize_action("git_commit", "commit something", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_fazle_core_unreachable_fails_closed(self, mock_post, mock_get):
        self._set_mode("READ")
        mock_get.return_value = {"error": "fazle-core unreachable"}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=10, diff="x", confirm=True,
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_run_mode_never_needs_the_task_lookup(self, mock_post, mock_get):
        """Genuine RUN mode short-circuits before any task-authorization
        lookup -- zero added latency/behavior change for the pre-existing
        RUN-mode path."""
        self._set_mode("RUN")
        mock_post.return_value = {"action_id": 9, "status": "approved"}
        result = task_tools.authorize_action(
            "git_commit", "commit the fix", task_id=10, diff="x", confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode_at_execution"], "RUN")
        mock_get.assert_not_called()

    @patch("task_tools.core.get")
    @patch("task_tools.core.post")
    def test_approve_action_two_step_path_unaffected(self, mock_post, mock_get):
        """approve_action (the secondary, non-task-scoped path used by the
        web UI's buttons and 'APPROVE ACTION <id>') never gets the
        task-authorization fallback -- still pure RUN-mode-gated."""
        self._set_mode("READ")
        result = task_tools.approve_action(7, confirm=True)
        self.assertFalse(result["ok"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()


class TestRecordExecutionResult(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_no_mode_gate_needed(self, mock_post):
        mock_post.return_value = {"ok": True, "action_id": 7, "status": "executed"}
        self._set_mode("READ")
        result = task_tools.record_execution_result(7, {"commit": "abc123"})
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
