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


class TestRecordExecutionResult(TaskToolsTestBase):
    @patch("task_tools.core.post")
    def test_no_mode_gate_needed(self, mock_post):
        mock_post.return_value = {"ok": True, "action_id": 7, "status": "executed"}
        self._set_mode("READ")
        result = task_tools.record_execution_result(7, {"commit": "abc123"})
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
