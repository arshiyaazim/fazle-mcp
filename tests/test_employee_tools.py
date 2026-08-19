import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import employee_tools


class TestSearchEmployees(unittest.TestCase):
    """Read-only, no mode gate."""

    @patch("employee_tools.core.get")
    def test_passes_query_and_limit_through(self, mock_get):
        mock_get.return_value = {"query": "Alim", "count": 0, "results": []}
        employee_tools.search_employees("Alim", limit=10)
        mock_get.assert_called_once_with("/api/fpe/employees/search", {"q": "Alim", "limit": 10})

    @patch("employee_tools.core.get")
    def test_limit_defaults_to_20(self, mock_get):
        mock_get.return_value = {"query": "Alim", "count": 0, "results": []}
        employee_tools.search_employees("Alim")
        mock_get.assert_called_once_with("/api/fpe/employees/search", {"q": "Alim", "limit": 20})

    @patch("employee_tools.core.get")
    def test_surfaces_client_error_unchanged(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
        result = employee_tools.search_employees("Alim")
        self.assertIn("error", result)


class TestSuggestEmployees(unittest.TestCase):
    """Read-only, no mode gate."""

    @patch("employee_tools.core.get")
    def test_passes_query_and_limit_through(self, mock_get):
        mock_get.return_value = {"query": "alim", "results": []}
        employee_tools.suggest_employees("alim", limit=5)
        mock_get.assert_called_once_with("/api/fpe/employees/suggest", {"q": "alim", "limit": 5})

    @patch("employee_tools.core.get")
    def test_limit_defaults_to_8(self, mock_get):
        mock_get.return_value = {"query": "alim", "results": []}
        employee_tools.suggest_employees("alim")
        mock_get.assert_called_once_with("/api/fpe/employees/suggest", {"q": "alim", "limit": 8})

    @patch("employee_tools.core.get")
    def test_partial_phone_query_forwarded_as_is(self, mock_get):
        mock_get.return_value = {"query": "1596", "results": []}
        employee_tools.suggest_employees("1596")
        mock_get.assert_called_once_with("/api/fpe/employees/suggest", {"q": "1596", "limit": 8})

    @patch("employee_tools.core.get")
    def test_surfaces_client_error_unchanged(self, mock_get):
        mock_get.return_value = {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
        result = employee_tools.suggest_employees("alim")
        self.assertIn("error", result)


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
