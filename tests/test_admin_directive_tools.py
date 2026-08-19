import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin_directive_tools


class DirectiveTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("admin_directive_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestRememberBusinessRule(DirectiveTestBase):
    @patch("admin_directive_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = admin_directive_tools.remember_business_rule(
            "recruitment_rule", "recruitment_minimum_age", "23", confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("admin_directive_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = admin_directive_tools.remember_business_rule(
            "recruitment_rule", "recruitment_minimum_age", "23",
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("admin_directive_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"id": 42}
        result = admin_directive_tools.remember_business_rule(
            "recruitment_rule", "recruitment_minimum_age", "23", confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], 42)
        mock_post.assert_called_once_with("/admin/directives", {
            "directive_text": "23", "category": "general",
            "subject_type": "recruitment_rule", "subject_key": "recruitment_minimum_age",
        })

    @patch("admin_directive_tools.core.post")
    def test_core_error_surfaces(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"error": "directive_text must not be empty"}
        result = admin_directive_tools.remember_business_rule(
            "recruitment_rule", "recruitment_minimum_age", "", confirm=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("directive_text", result["error"])


class TestRevokeDirective(DirectiveTestBase):
    @patch("admin_directive_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = admin_directive_tools.revoke_directive(42, confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("admin_directive_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"revoked": True}
        result = admin_directive_tools.revoke_directive(42, confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/admin/directives/42/revoke", {})


class TestGetDirectives(unittest.TestCase):
    @patch("admin_directive_tools.core.get")
    def test_no_mode_gate_needed(self, mock_get):
        mock_get.return_value = {"rows": [], "count": 0}
        result = admin_directive_tools.get_directives(subject_key="recruitment_minimum_age")
        mock_get.assert_called_once_with(
            "/admin/directives",
            params={"status": "active", "limit": 50, "subject_key": "recruitment_minimum_age"},
        )
        self.assertEqual(result["count"], 0)

    @patch("admin_directive_tools.core.get")
    def test_subject_key_omitted_when_none(self, mock_get):
        mock_get.return_value = {"rows": [], "count": 0}
        admin_directive_tools.get_directives()
        mock_get.assert_called_once_with("/admin/directives", params={"status": "active", "limit": 50})


if __name__ == "__main__":
    unittest.main()
