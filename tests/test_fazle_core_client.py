import importlib
import os
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fazle_core_client as core


class TestNotConfigured(unittest.TestCase):
    """FAZLE_CORE_API_KEY is genuinely unset in the real deployment right
    now (placeholder in ~/.hermes/config.yaml, pending the Owner minting a
    dedicated key) — this is the actual current-state path, not a
    hypothetical."""

    def setUp(self):
        self._orig = core.FAZLE_CORE_API_KEY
        core.FAZLE_CORE_API_KEY = ""

    def tearDown(self):
        core.FAZLE_CORE_API_KEY = self._orig

    def test_get_returns_clear_not_configured_error(self):
        result = core.get("/api/social/status")
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"])

    def test_post_returns_clear_not_configured_error(self):
        result = core.post("/scheduler/run/health_summary")
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"])

    def test_put_returns_clear_not_configured_error(self):
        result = core.put("/admin/attendance/1")
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"])

    def test_delete_returns_clear_not_configured_error(self):
        result = core.delete("/admin/attendance/1")
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"])

    def test_patch_returns_clear_not_configured_error(self):
        result = core.patch("/api/fpe/employees/1")
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"])


class TestConfiguredCalls(unittest.TestCase):
    def setUp(self):
        self._orig = core.FAZLE_CORE_API_KEY
        core.FAZLE_CORE_API_KEY = "test-key"

    def tearDown(self):
        core.FAZLE_CORE_API_KEY = self._orig

    @patch("httpx.get")
    def test_get_success(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.is_success = True
        mock_get.return_value.json.return_value = {"ok": True}
        result = core.get("/api/social/status")
        self.assertEqual(result, {"ok": True})
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["X-Internal-Key"], "test-key")

    @patch("httpx.get")
    def test_get_unauthorized(self, mock_get):
        mock_get.return_value.status_code = 403
        mock_get.return_value.is_success = False
        result = core.get("/api/social/status")
        self.assertIn("unauthorized", result["error"])

    @patch("httpx.get", side_effect=httpx.TimeoutException("timed out"))
    def test_get_timeout(self, mock_get):
        result = core.get("/api/social/status")
        self.assertIn("timed out", result["error"])

    @patch("httpx.get", side_effect=httpx.ConnectError("refused"))
    def test_get_unreachable(self, mock_get):
        result = core.get("/api/social/status")
        self.assertIn("unreachable", result["error"])

    @patch("httpx.post")
    def test_post_unknown_job_maps_to_404(self, mock_post):
        mock_post.return_value.status_code = 404
        mock_post.return_value.is_success = False
        result = core.post("/scheduler/run/not-a-job")
        self.assertIn("unknown job", result["error"])


if __name__ == "__main__":
    unittest.main()
