import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler_tools


class SchedulerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self.state_dir = os.path.join(self.tmp_dir, "last_run")
        self._mode_patch = patch("scheduler_tools.MODE_FILE", self.mode_file)
        self._state_patch = patch("scheduler_tools._STATE_DIR", self.state_dir)
        self._mode_patch.start()
        self._state_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        self._state_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestModeGate(SchedulerTestBase):
    @patch("scheduler_tools.core.post")
    def test_read_only_job_allowed_in_build_mode(self, mock_post):
        self._set_mode("BUILD")
        mock_post.return_value = {"job": "health_summary", "result": {"status": "ok"}}
        result = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(result["status"], "success")

    @patch("scheduler_tools.core.post")
    def test_read_only_job_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(result["status"], "denied")
        mock_post.assert_not_called()

    def test_unknown_job_rejected(self):
        self._set_mode("RUN")
        result = scheduler_tools.run_scheduled_task("delete_everything")
        self.assertEqual(result["status"], "error")
        self.assertIn("unknown", result["error"])

    @patch("scheduler_tools.core.post")
    def test_destructive_job_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = scheduler_tools.run_scheduled_task("daily_payroll_compute", confirm=False)
        self.assertEqual(result["status"], "denied")
        mock_post.assert_not_called()

    @patch("scheduler_tools.core.post")
    def test_destructive_job_denied_in_build_mode_even_with_confirm(self, mock_post):
        self._set_mode("BUILD")
        result = scheduler_tools.run_scheduled_task("daily_payroll_compute", confirm=True)
        self.assertEqual(result["status"], "denied")
        mock_post.assert_not_called()

    @patch("scheduler_tools.core.post")
    def test_destructive_job_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"job": "daily_payroll_compute", "result": {"status": "ok"}}
        result = scheduler_tools.run_scheduled_task("daily_payroll_compute", confirm=True)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["confirmed"])

    @patch("scheduler_tools.core.post")
    def test_corrupted_mode_file_fails_closed_to_deny(self, mock_post):
        with open(self.mode_file, "w") as f:
            f.write("!!!not a mode!!!")
        result = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(result["status"], "denied")
        mock_post.assert_not_called()

    @patch("scheduler_tools.core.post")
    def test_missing_mode_file_fails_closed_to_deny(self, mock_post):
        # mode_file was never created in this test
        result = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(result["status"], "denied")
        mock_post.assert_not_called()


class TestExecutionResult(SchedulerTestBase):
    @patch("scheduler_tools.core.post")
    def test_fazle_core_failure_surfaces_as_structured_error(self, mock_post):
        self._set_mode("BUILD")
        mock_post.return_value = {"error": "fazle-core unreachable: connection refused"}
        result = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(result["status"], "error")
        self.assertIn("error", result["result"])

    @patch("scheduler_tools.core.post")
    def test_successful_result_includes_metadata(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"job": "dlq_alert", "result": {"actionable": 0}}
        result = scheduler_tools.run_scheduled_task("dlq_alert")
        for field in ["job_name", "risk_tier", "mode_at_execution", "status", "result", "started_at", "finished_at", "duration_ms"]:
            self.assertIn(field, result)


class TestDuplicateGuard(SchedulerTestBase):
    @patch("scheduler_tools.core.post")
    def test_duplicate_trigger_within_window_denied(self, mock_post):
        self._set_mode("BUILD")
        mock_post.return_value = {"job": "health_summary", "result": {"status": "ok"}}
        first = scheduler_tools.run_scheduled_task("health_summary")
        second = scheduler_tools.run_scheduled_task("health_summary")
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "denied")
        self.assertIn("duplicate", second["error"])
        self.assertEqual(mock_post.call_count, 1)

    @patch("scheduler_tools.core.post")
    def test_different_jobs_not_blocked_by_each_others_debounce(self, mock_post):
        self._set_mode("BUILD")
        mock_post.return_value = {"job": "x", "result": {"status": "ok"}}
        first = scheduler_tools.run_scheduled_task("health_summary")
        second = scheduler_tools.run_scheduled_task("dlq_alert")
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")


class TestListAvailableTasks(unittest.TestCase):
    def test_lists_all_registry_jobs(self):
        result = scheduler_tools.list_available_tasks()
        self.assertIn("daily_payroll_compute", result["jobs"])
        self.assertEqual(result["jobs"]["daily_payroll_compute"], scheduler_tools.DESTRUCTIVE)


if __name__ == "__main__":
    unittest.main()
