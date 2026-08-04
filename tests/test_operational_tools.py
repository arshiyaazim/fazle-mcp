import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import operational_tools


class TestGetBridgeMessageStats(unittest.TestCase):
    @patch("operational_tools.core.get")
    def test_success(self, mock_get):
        mock_get.return_value = {
            "hours": 24,
            "by_platform": [
                {"platform": "bridge1", "total": 3809, "in_window": 9, "unprocessed_total": 33, "stuck_over_1h": 33},
                {"platform": "bridge2", "total": 7688, "in_window": 51, "unprocessed_total": 22, "stuck_over_1h": 22},
            ],
        }
        result = operational_tools.get_bridge_message_stats()
        self.assertEqual(len(result["by_platform"]), 2)
        mock_get.assert_called_once_with("/api/whatsapp/message-stats", {"hours": 24})

    @patch("operational_tools.core.get")
    def test_default_hours(self, mock_get):
        mock_get.return_value = {"by_platform": []}
        operational_tools.get_bridge_message_stats()
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["hours"], 24)

    @patch("operational_tools.core.get")
    def test_invalid_hours_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"by_platform": []}
        operational_tools.get_bridge_message_stats(hours="not-a-number")
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["hours"], 24)

    @patch("operational_tools.core.get")
    def test_hours_clamped_to_max(self, mock_get):
        mock_get.return_value = {"by_platform": []}
        operational_tools.get_bridge_message_stats(hours=999999)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["hours"], 24 * 30)

    @patch("operational_tools.core.get")
    def test_zero_hours_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"by_platform": []}
        operational_tools.get_bridge_message_stats(hours=0)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["hours"], 24)

    @patch("operational_tools.core.get")
    def test_negative_hours_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"by_platform": []}
        operational_tools.get_bridge_message_stats(hours=-5)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["hours"], 24)

    @patch("operational_tools.core.get")
    def test_error_passthrough_no_crash(self, mock_get):
        mock_get.return_value = {"error": "fazle-core error (status 404)"}
        result = operational_tools.get_bridge_message_stats()
        self.assertIn("error", result)


class TestGetDlqStatus(unittest.TestCase):
    @patch("operational_tools.core.get")
    def test_success(self, mock_get):
        mock_get.return_value = {"items": [], "count": 0, "total": 0}
        result = operational_tools.get_dlq_status(limit=5)
        self.assertEqual(result["total"], 0)
        mock_get.assert_called_once_with("/api/queue/dead-letters", {"limit": 5})

    @patch("operational_tools.core.get")
    def test_invalid_limit_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"items": []}
        operational_tools.get_dlq_status(limit="bad")
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 20)

    @patch("operational_tools.core.get")
    def test_limit_clamped_to_max(self, mock_get):
        mock_get.return_value = {"items": []}
        operational_tools.get_dlq_status(limit=99999)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 100)

    @patch("operational_tools.core.get")
    def test_zero_limit_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"items": []}
        operational_tools.get_dlq_status(limit=0)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 20)

    @patch("operational_tools.core.get")
    def test_unreachable(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: Connection refused"}
        result = operational_tools.get_dlq_status()
        self.assertIn("error", result)


class TestGetBridgeDiagnostics(unittest.TestCase):
    @patch("operational_tools.core.get")
    def test_success(self, mock_get):
        mock_get.return_value = {"bridges": {"bridge1": {"state": "healthy"}, "bridge2": {"state": "healthy"}}}
        result = operational_tools.get_bridge_diagnostics()
        self.assertIn("bridge2", result["bridges"])
        mock_get.assert_called_once_with("/api/bridges/diagnostics")

    @patch("operational_tools.core.get")
    def test_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle-core error (status 500)"}
        result = operational_tools.get_bridge_diagnostics()
        self.assertIn("error", result)


class TestGetSystemHealth(unittest.TestCase):
    @patch("operational_tools.core.get")
    def test_success(self, mock_get):
        mock_get.return_value = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok", "load_pct": 53.0}}}
        result = operational_tools.get_system_health()
        self.assertEqual(result["status"], "ok")
        self.assertIn("cpu", result["probes"])
        mock_get.assert_called_once_with("/health")

    @patch("operational_tools.core.get")
    def test_degraded_passthrough(self, mock_get):
        mock_get.return_value = {"status": "degraded", "probes": {"cpu": {"status": "degraded", "load_pct": 88.0}}}
        result = operational_tools.get_system_health()
        self.assertEqual(result["status"], "degraded")

    @patch("operational_tools.core.get")
    def test_unreachable(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: Connection refused"}
        result = operational_tools.get_system_health()
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
