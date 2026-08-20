import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch_tools

_APPROVED_ACTION = {"id": 42, "status": "approved", "action_type": "dispatch_assignment"}


class DispatchTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("dispatch_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()
        # Default: approved_action_id always resolves to a genuinely approved
        # dispatch_assignment action, matching opencode_tools.py's own test
        # pattern -- each test that exercises the hard gate itself overrides this.
        self._action_patch = patch("task_tools.get_action", return_value=dict(_APPROVED_ACTION))
        self._action_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        self._action_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestReadOnlyToolsAreUngated(unittest.TestCase):
    """These never mutate anything -- no mode file needed at all."""

    @patch("dispatch_tools.core.get")
    def test_check_availability_default_params(self, mock_get):
        mock_get.return_value = {"items": []}
        dispatch_tools.dispatch_check_availability()
        mock_get.assert_called_once_with("/api/dispatch/availability", {"page": 1, "page_size": 50})

    @patch("dispatch_tools.core.get")
    def test_check_availability_optional_filters(self, mock_get):
        mock_get.return_value = {"items": []}
        dispatch_tools.dispatch_check_availability(employee_id=5, available_only=True)
        mock_get.assert_called_once_with(
            "/api/dispatch/availability", {"page": 1, "page_size": 50, "employee_id": 5, "available_only": True}
        )

    @patch("dispatch_tools.core.post")
    def test_check_conflict_posts_structured_body(self, mock_post):
        mock_post.return_value = {"available": True, "conflicts": []}
        result = dispatch_tools.dispatch_check_conflict(5, "2026-09-01", "D", "2026-09-03", "N")
        self.assertTrue(result["available"])
        mock_post.assert_called_once_with(
            "/api/dispatch/check-conflict",
            {"escort_employee_id": 5, "start_date": "2026-09-01", "start_shift": "D",
             "end_date": "2026-09-03", "end_shift": "N"},
        )

    @patch("dispatch_tools.core.get")
    def test_get_suggestions_includes_destination_when_set(self, mock_get):
        mock_get.return_value = {"items": []}
        dispatch_tools.dispatch_get_suggestions("2026-09-01", "D", "2026-09-03", "N", destination="MV X")
        mock_get.assert_called_once_with(
            "/api/dispatch/suggestions",
            {"start_date": "2026-09-01", "start_shift": "D", "end_date": "2026-09-03",
             "end_shift": "N", "limit": 10, "destination": "MV X"},
        )

    @patch("dispatch_tools.core.get")
    def test_list_overlaps_default_paging(self, mock_get):
        mock_get.return_value = {"items": []}
        dispatch_tools.dispatch_list_overlaps()
        mock_get.assert_called_once_with("/api/dispatch/overlaps", {"page": 1, "page_size": 20})


class TestApprovedActionHardGate(DispatchTestBase):
    """2026-08-21: every dispatch mutation requires a genuinely approved
    hermes_action_approvals row (action_type='dispatch_assignment') before
    even the RUN-mode/confirm check -- identical shape to opencode_dispatch."""

    def test_missing_approved_action_id_denied(self):
        self._set_mode("RUN")
        result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=0, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("approved_action_id", result["error"])

    @patch("dispatch_tools.core.post")
    def test_pending_not_approved_action_denied(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 42, "status": "pending", "action_type": "dispatch_assignment"}):
            self._set_mode("RUN")
            result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=42, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("not approved", result["error"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("dispatch_tools.core.post")
    def test_wrong_action_type_denied_even_if_approved(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 42, "status": "approved", "action_type": "billing_mutation"}):
            self._set_mode("RUN")
            result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=42, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("action_type", result["error"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("dispatch_tools.core.post")
    def test_unknown_action_id_denied(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"error": "action not found"}):
            self._set_mode("RUN")
            result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=999, confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("dispatch_tools.core.post")
    def test_denied_in_read_mode_even_with_approved_action(self, mock_post):
        self._set_mode("READ")
        result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=42, confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("dispatch_tools.core.post")
    def test_denied_in_run_mode_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=42, confirm=False)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("dispatch_tools.core.post")
    def test_approved_run_confirmed_calls_through(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {
            "program_id": 10, "previous_employee_id": None, "new_employee_id": 5,
            "employee_name": "Karim", "status": "confirmed",
        }
        result = dispatch_tools.dispatch_assign_program(
            10, 5, approved_action_id=42, expected_current_employee_id=None, confirm=True
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_employee_id"], 5)
        mock_post.assert_called_once_with("/api/dispatch/programs/10/assign", {"escort_employee_id": 5})


class TestConflictSurfacing(DispatchTestBase):
    """A genuine scheduling conflict must be reported with the actual
    conflicting programs, not swallowed into a generic error string."""

    @patch("dispatch_tools.core.post")
    def test_dispatch_conflict_surfaces_code_and_conflicts(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {
            "error": "fazle-core error (status 409)",
            "error_detail": {
                "detail": {
                    "detail": "escort employee has an overlapping assignment",
                    "code": "dispatch_conflict",
                    "conflicts": [{"program_id": 7, "status": "confirmed"}],
                }
            },
        }
        result = dispatch_tools.dispatch_assign_program(10, 5, approved_action_id=42, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "dispatch_conflict")
        self.assertEqual(result["conflicts"], [{"program_id": 7, "status": "confirmed"}])
        self.assertIn("overlapping assignment", result["error"])


class TestUnassignAndReplace(DispatchTestBase):
    @patch("dispatch_tools.core.post")
    def test_unassign_requires_approved_action(self, mock_post):
        self._set_mode("RUN")
        result = dispatch_tools.dispatch_unassign_program(
            10, expected_current_employee_id=5, reason="wrong assignment", approved_action_id=0, confirm=True
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("dispatch_tools.core.post")
    def test_unassign_calls_through_when_approved(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"program_id": 10, "status": "confirmed"}
        result = dispatch_tools.dispatch_unassign_program(
            10, expected_current_employee_id=5, reason="wrong assignment", approved_action_id=42, confirm=True
        )
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/dispatch/programs/10/unassign",
            {"expected_current_employee_id": 5, "reason": "wrong assignment"},
        )

    @patch("dispatch_tools.core.post")
    def test_replace_escort_calls_through_when_approved(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"program_id": 11, "status": "confirmed"}
        result = dispatch_tools.dispatch_replace_escort(
            10, mother_vessel="MV X", lighter_vessel="LV Y", escort_name="Karim",
            escort_mobile="01712345678", start_date="2026-09-01", start_shift="D",
            approved_action_id=42, confirm=True,
        )
        self.assertTrue(result["ok"])
        args, body = mock_post.call_args[0]
        self.assertEqual(args, "/api/dispatch/programs/10/replace-escort")
        self.assertEqual(body["mother_vessel"], "MV X")


class TestReadMode(DispatchTestBase):
    def test_missing_mode_file_defaults_to_read(self):
        self.assertEqual(dispatch_tools._read_mode(), "READ")

    def test_json_ttl_form_respected(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": None}, f)
        self.assertEqual(dispatch_tools._read_mode(), "RUN")

    def test_expired_ttl_falls_back_to_read(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": "2020-01-01T00:00:00Z"}, f)
        self.assertEqual(dispatch_tools._read_mode(), "READ")


if __name__ == "__main__":
    unittest.main()
