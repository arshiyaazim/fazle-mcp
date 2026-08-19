import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import employee_tools


class EmployeeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("employee_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestCreateEmployee(EmployeeTestBase):
    @patch("employee_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = employee_tools.create_employee("Sumon", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("employee_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "created", "employee": {"id": 7}}
        result = employee_tools.create_employee("Sumon", employee_mobile="01711234567", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/fpe/employees", {
            "full_name": "Sumon", "employee_mobile": "01711234567",
            "role": None, "status": "active",
        })


class TestUpdateEmployee(EmployeeTestBase):
    @patch("employee_tools.core.patch")
    def test_denied_without_confirm(self, mock_patch):
        self._set_mode("RUN")
        result = employee_tools.update_employee(7, full_name="New Name")
        self.assertFalse(result["ok"])
        mock_patch.assert_not_called()

    @patch("employee_tools.core.patch")
    def test_allowed_in_run_mode_with_confirm_only_sends_provided_fields(self, mock_patch):
        self._set_mode("RUN")
        mock_patch.return_value = {"status": "updated"}
        result = employee_tools.update_employee(7, full_name="New Name", confirm=True)
        self.assertTrue(result["ok"])
        mock_patch.assert_called_once_with("/api/fpe/employees/7", {"full_name": "New Name"})


class TestApproveEmployeeEditRequest(EmployeeTestBase):
    @patch("employee_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = employee_tools.approve_employee_edit_request(3, reviewer="owner", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("employee_tools.core.post")
    def test_allowed_passes_reviewer_as_query_param(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "approved", "request_id": 3}
        result = employee_tools.approve_employee_edit_request(3, reviewer="owner", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/fpe/employees/edit-requests/3/approve", params={"reviewer": "owner"}
        )


class TestRejectEmployeeEditRequest(EmployeeTestBase):
    @patch("employee_tools.core.post")
    def test_allowed_passes_reviewer_and_reason(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "rejected", "request_id": 3}
        result = employee_tools.reject_employee_edit_request(3, reviewer="owner", reason="duplicate", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/fpe/employees/edit-requests/3/reject",
            params={"reviewer": "owner", "reason": "duplicate"},
        )


if __name__ == "__main__":
    unittest.main()
