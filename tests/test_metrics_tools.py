import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics_tools


class TestGetSocialStatus(unittest.TestCase):
    @patch("metrics_tools.core.get")
    def test_success(self, mock_get):
        mock_get.return_value = {"enabled": True, "paused": False, "pending": 3, "failed": 0, "flagged_open": 1, "sent_24h": 10}
        result = metrics_tools.get_social_status()
        self.assertEqual(result["pending"], 3)
        mock_get.assert_called_once_with("/api/social/status")

    @patch("metrics_tools.core.get")
    def test_unauthorized(self, mock_get):
        mock_get.return_value = {"error": "fazle-core rejected the API key (unauthorized)"}
        result = metrics_tools.get_social_status()
        self.assertIn("error", result)

    @patch("metrics_tools.core.get")
    def test_unreachable(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: Connection refused"}
        result = metrics_tools.get_social_status()
        self.assertIn("error", result)

    @patch("metrics_tools.core.get")
    def test_timeout(self, mock_get):
        mock_get.return_value = {"error": "fazle-core request timed out"}
        result = metrics_tools.get_social_status()
        self.assertIn("timed out", result["error"])


class TestGetSocialQueueFlagged(unittest.TestCase):
    # metrics_tools._HERMES_IS_ADMIN_CONTEXT is True today (Hermes's whole
    # surface is admin-gated end-to-end — hermes.js requireAdmin), so the
    # correct, intentional current behavior is NO masking. These tests
    # confirm that documented default AND, separately, that the masking
    # pathway itself is genuinely wired (not dead code) by flipping the
    # constant — protecting the invariant if that context assumption ever
    # changes (see the module docstring's explicit warning about this).

    @patch("metrics_tools.core.get")
    def test_queue_success_unmasked_in_current_admin_only_context(self, mock_get):
        mock_get.return_value = {"items": [{"id": 1, "platform": "whatsapp", "target_id": "01712345678", "status": "pending"}]}
        result = metrics_tools.get_social_queue(limit=10)
        self.assertEqual(result["items"][0]["target_id"], "01712345678")
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], "/api/social/queue")
        self.assertEqual(args[1]["limit"], 10)

    @patch("metrics_tools._HERMES_IS_ADMIN_CONTEXT", False)
    @patch("metrics_tools.core.get")
    def test_queue_masks_target_id_if_admin_context_flag_is_ever_flipped(self, mock_get):
        mock_get.return_value = {"items": [{"id": 1, "target_id": "01712345678", "status": "pending"}]}
        result = metrics_tools.get_social_queue(limit=10)
        self.assertIn("X", result["items"][0]["target_id"])
        self.assertNotEqual(result["items"][0]["target_id"], "01712345678")

    @patch("metrics_tools._HERMES_IS_ADMIN_CONTEXT", False)
    @patch("metrics_tools.core.get")
    def test_flagged_masks_target_id_if_admin_context_flag_is_ever_flipped(self, mock_get):
        mock_get.return_value = {"items": [{"id": 2, "target_id": "01898765432", "reason": "spam"}]}
        result = metrics_tools.get_social_flagged(limit=5)
        self.assertIn("X", result["items"][0]["target_id"])

    @patch("metrics_tools.core.get")
    def test_queue_error_passthrough_no_crash(self, mock_get):
        mock_get.return_value = {"error": "fazle-core error (status 500)"}
        result = metrics_tools.get_social_queue()
        self.assertIn("error", result)

    def test_invalid_limit_falls_back_to_default(self):
        with patch("metrics_tools.core.get", return_value={"items": []}) as mock_get:
            metrics_tools.get_social_queue(limit="not-a-number")
            args, _ = mock_get.call_args
            self.assertEqual(args[1]["limit"], 50)

    def test_limit_clamped_to_max(self):
        with patch("metrics_tools.core.get", return_value={"items": []}) as mock_get:
            metrics_tools.get_social_queue(limit=99999)
            args, _ = mock_get.call_args
            self.assertEqual(args[1]["limit"], 200)


class TestGetSocialReport(unittest.TestCase):
    @patch("metrics_tools.get_social_flagged")
    @patch("metrics_tools.get_social_queue")
    @patch("metrics_tools.get_social_status")
    def test_healthy_report(self, mock_status, mock_queue, mock_flagged):
        mock_status.return_value = {"enabled": True, "paused": False, "pending": 2, "failed": 0, "flagged_open": 0, "sent_24h": 5}
        mock_queue.return_value = {"items": []}
        mock_flagged.return_value = {"items": []}
        report = metrics_tools.get_social_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")
        self.assertIn("STATUS: HEALTHY", report["text"])

    @patch("metrics_tools.get_social_flagged")
    @patch("metrics_tools.get_social_queue")
    @patch("metrics_tools.get_social_status")
    def test_degraded_report_on_failures(self, mock_status, mock_queue, mock_flagged):
        mock_status.return_value = {"enabled": True, "paused": False, "pending": 2, "failed": 2, "flagged_open": 1, "sent_24h": 5}
        mock_queue.return_value = {"items": []}
        mock_flagged.return_value = {"items": []}
        report = metrics_tools.get_social_report()
        self.assertIn(report["structured"]["status"], ("DEGRADED", "CRITICAL"))
        self.assertTrue(len(report["structured"]["findings"]) > 0)

    @patch("metrics_tools.get_social_status")
    def test_status_unavailable_yields_unknown_report(self, mock_status):
        mock_status.return_value = {"error": "fazle-core unreachable"}
        report = metrics_tools.get_social_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")
        self.assertIn("problems", report["structured"])


if __name__ == "__main__":
    unittest.main()
