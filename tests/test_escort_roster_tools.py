import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import escort_roster_tools


class EscortRosterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("escort_roster_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestListRoster(unittest.TestCase):
    @patch("escort_roster_tools.core.get")
    def test_no_gate_and_default_paging(self, mock_get):
        mock_get.return_value = {"items": []}
        escort_roster_tools.list_roster()
        mock_get.assert_called_once_with("/api/escort-roster", {"page": 1, "page_size": 50})

    @patch("escort_roster_tools.core.get")
    def test_optional_filters_included_when_set(self, mock_get):
        mock_get.return_value = {"items": []}
        escort_roster_tools.list_roster(search="Al-Amin", status="active")
        mock_get.assert_called_once_with(
            "/api/escort-roster", {"page": 1, "page_size": 50, "search": "Al-Amin", "status": "active"}
        )


class TestCreateRosterEntry(EscortRosterTestBase):
    @patch("escort_roster_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = escort_roster_tools.create_roster_entry("MV Test", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("escort_roster_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 55, "mother_vessel": "MV Test"}
        result = escort_roster_tools.create_roster_entry("MV Test", confirm=True)
        self.assertTrue(result["ok"])
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "/api/escort-roster")
        self.assertEqual(args[1]["mother_vessel"], "MV Test")


class TestPatchRosterEntry(EscortRosterTestBase):
    @patch("escort_roster_tools.core.patch")
    def test_denied_without_confirm(self, mock_patch):
        self._set_mode("RUN")
        result = escort_roster_tools.patch_roster_entry(55, roster_status="confirmed")
        self.assertFalse(result["ok"])
        mock_patch.assert_not_called()

    @patch("escort_roster_tools.core.patch")
    def test_allowed_passes_arbitrary_fields_through(self, mock_patch):
        self._set_mode("RUN")
        mock_patch.return_value = {"id": 55, "roster_status": "confirmed"}
        result = escort_roster_tools.patch_roster_entry(55, roster_status="confirmed", notes="ok", confirm=True)
        self.assertTrue(result["ok"])
        mock_patch.assert_called_once_with(
            "/api/escort-roster/55", {"roster_status": "confirmed", "notes": "ok"}
        )


class TestDeleteRosterEntry(EscortRosterTestBase):
    @patch("escort_roster_tools.core.delete")
    def test_denied_in_read_mode(self, mock_delete):
        self._set_mode("READ")
        result = escort_roster_tools.delete_roster_entry(55, confirm=True)
        self.assertFalse(result["ok"])
        mock_delete.assert_not_called()

    @patch("escort_roster_tools.core.delete")
    def test_allowed_in_run_mode_with_confirm(self, mock_delete):
        self._set_mode("RUN")
        mock_delete.return_value = {"ok": True, "id": 55}
        result = escort_roster_tools.delete_roster_entry(55, confirm=True)
        self.assertTrue(result["ok"])
        mock_delete.assert_called_once_with("/api/escort-roster/55")


class TestRecalculateRosterEntry(EscortRosterTestBase):
    @patch("escort_roster_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 55, "conveyance": 400.0}
        result = escort_roster_tools.recalculate_roster_entry(55, confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/escort-roster/55/recalculate")


class TestChangeRequestApproveReject(EscortRosterTestBase):
    @patch("escort_roster_tools.core.post")
    def test_approve_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = escort_roster_tools.approve_roster_change_request(9)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("escort_roster_tools.core.post")
    def test_approve_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 9, "status": "approved"}
        result = escort_roster_tools.approve_roster_change_request(9, confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/escort-roster/change-requests/9/approve")

    @patch("escort_roster_tools.core.post")
    def test_reject_passes_optional_note(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 9, "status": "rejected"}
        result = escort_roster_tools.reject_roster_change_request(9, note="not needed", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/escort-roster/change-requests/9/reject", params={"note": "not needed"}
        )

    @patch("escort_roster_tools.core.post")
    def test_reject_without_note(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 9, "status": "rejected"}
        escort_roster_tools.reject_roster_change_request(9, confirm=True)
        mock_post.assert_called_once_with(
            "/api/escort-roster/change-requests/9/reject", params=None
        )


if __name__ == "__main__":
    unittest.main()
