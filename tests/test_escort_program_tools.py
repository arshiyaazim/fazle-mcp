import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import escort_program_tools

_APPROVED_ACTION = {"id": 21, "status": "approved", "action_type": "escort_program_creation"}


class ProgramTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("escort_program_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()
        self._action_patch = patch("task_tools.get_action", return_value=dict(_APPROVED_ACTION))
        self._action_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        self._action_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestApprovedActionHardGate(ProgramTestBase):
    def test_missing_approved_action_id_denied(self):
        self._set_mode("RUN")
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=0, confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("approved_action_id", result["error"])

    @patch("escort_program_tools.core.post")
    def test_pending_not_approved_action_denied(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 21, "status": "pending", "action_type": "escort_program_creation"}):
            self._set_mode("RUN")
            result = escort_program_tools.create_escort_program(
                "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
            )
        self.assertFalse(result["ok"])
        self.assertIn("not approved", result["error"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("escort_program_tools.core.post")
    def test_wrong_action_type_denied_even_if_approved(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 21, "status": "approved", "action_type": "dispatch_assignment"}):
            self._set_mode("RUN")
            result = escort_program_tools.create_escort_program(
                "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
            )
        self.assertFalse(result["ok"])
        self.assertIn("action_type", result["error"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("escort_program_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("escort_program_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=False,
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("escort_program_tools.core.post")
    def test_approved_run_confirmed_calls_through(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"program_ids": [1234], "created_by": "test-admin"}
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["program_ids"], [1234])
        mock_post.assert_called_once_with(
            "/api/escort/programs",
            {"mother_vessel": "MV Test", "lighter_vessel": "LV Test", "master_mobile": "01712345678", "shift": "D"},
        )

    @patch("escort_program_tools.core.post")
    def test_optional_fields_included_when_set(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"program_ids": [1235]}
        escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
            destination="Chattogram", capacity="5000 MT", cargo_type="wheat", importer="ABC Co",
            date_hint="2026-09-01",
        )
        args, body = mock_post.call_args[0]
        self.assertEqual(body["destination"], "Chattogram")
        self.assertEqual(body["capacity"], "5000 MT")
        self.assertEqual(body["cargo_type"], "wheat")
        self.assertEqual(body["importer"], "ABC Co")
        self.assertEqual(body["date_hint"], "2026-09-01")

    @patch("escort_program_tools.core.post")
    def test_duplicate_returns_existing_program_id_not_an_error(self, mock_post):
        # save_escort_programs()'s own dedup returns the EXISTING program_id
        # rather than erroring -- confirm this tool passes that straight
        # through as a success, not a false failure.
        self._set_mode("RUN")
        mock_post.return_value = {"program_ids": [999], "created_by": "test-admin"}
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["program_ids"], [999])

    @patch("escort_program_tools.core.post")
    def test_validation_error_surfaces_cleanly(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {
            "error": "fazle-core error (status 422)",
            "error_detail": {"detail": "mother_vessel, lighter_vessel, and master_mobile are all required"},
        }
        result = escort_program_tools.create_escort_program(
            "MV Test", "LV Test", "01712345678", approved_action_id=21, confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("are all required", result["error"])


class TestReadMode(ProgramTestBase):
    def test_missing_mode_file_defaults_to_read(self):
        self.assertEqual(escort_program_tools._read_mode(), "READ")

    def test_json_ttl_form_respected(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": None}, f)
        self.assertEqual(escort_program_tools._read_mode(), "RUN")


if __name__ == "__main__":
    unittest.main()
