import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attendance_tools


class AttendanceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("attendance_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestCreateAttendance(AttendanceTestBase):
    @patch("attendance_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = attendance_tools.create_attendance(1, "2026-08-19", "present", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("attendance_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = attendance_tools.create_attendance(1, "2026-08-19", "present")
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("attendance_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 99, "duty_status": "present"}
        result = attendance_tools.create_attendance(1, "2026-08-19", "present", remarks="ok", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/admin/attendance", {
            "employee_id": 1, "attendance_date": "2026-08-19",
            "duty_status": "present", "remarks": "ok",
        })


class TestCreateAttendanceDraft(AttendanceTestBase):
    @patch("attendance_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 100, "status": "pending"}
        result = attendance_tools.create_attendance_draft(1, "2026-08-19", "present", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/admin/attendance/draft", {
            "employee_id": 1, "attendance_date": "2026-08-19",
            "duty_status": "present", "remarks": None,
        })


class TestUpdateAttendance(AttendanceTestBase):
    @patch("attendance_tools.core.put")
    def test_denied_in_read_mode(self, mock_put):
        self._set_mode("READ")
        result = attendance_tools.update_attendance(99, duty_status="absent", confirm=True)
        self.assertFalse(result["ok"])
        mock_put.assert_not_called()

    @patch("attendance_tools.core.put")
    def test_allowed_in_run_mode_with_confirm(self, mock_put):
        self._set_mode("RUN")
        mock_put.return_value = {"id": 99, "duty_status": "absent"}
        result = attendance_tools.update_attendance(99, duty_status="absent", confirm=True)
        self.assertTrue(result["ok"])
        mock_put.assert_called_once_with("/admin/attendance/99", {
            "duty_status": "absent", "remarks": None,
        })


class TestDeleteAttendance(AttendanceTestBase):
    @patch("attendance_tools.core.delete")
    def test_denied_without_confirm(self, mock_delete):
        self._set_mode("RUN")
        result = attendance_tools.delete_attendance(99)
        self.assertFalse(result["ok"])
        mock_delete.assert_not_called()

    @patch("attendance_tools.core.delete")
    def test_allowed_in_run_mode_with_confirm(self, mock_delete):
        self._set_mode("RUN")
        mock_delete.return_value = {"ok": True, "id": 99}
        result = attendance_tools.delete_attendance(99, confirm=True)
        self.assertTrue(result["ok"])
        mock_delete.assert_called_once_with("/admin/attendance/99")


if __name__ == "__main__":
    unittest.main()
