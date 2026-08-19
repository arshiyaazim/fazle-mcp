import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kernel_tools


class TestLookupDecisions(unittest.TestCase):
    def test_passes_all_filters_through(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"count": 0, "decisions": []}
            kernel_tools.lookup_decisions(
                phone="01711234567", trace_id="abc-123", chosen_action="draft", limit=5
            )
        mock_get.assert_called_once_with(
            "/api/hermes/decisions/lookup",
            {"phone": "01711234567", "trace_id": "abc-123", "chosen_action": "draft", "limit": 5},
        )

    def test_all_filters_default_to_empty(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"count": 0, "decisions": []}
            kernel_tools.lookup_decisions()
        mock_get.assert_called_once_with(
            "/api/hermes/decisions/lookup",
            {"phone": "", "trace_id": "", "chosen_action": "", "limit": 20},
        )

    def test_surfaces_client_error_unchanged(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
            result = kernel_tools.lookup_decisions(phone="01711234567")
        self.assertIn("error", result)


class TestLookupKernelEvents(unittest.TestCase):
    def test_passes_trace_id_and_limit_through(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"count": 0, "events": []}
            kernel_tools.lookup_kernel_events("abc-123", limit=10)
        mock_get.assert_called_once_with(
            "/api/hermes/kernel/events", {"trace_id": "abc-123", "limit": 10}
        )

    def test_limit_defaults_to_50(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"count": 0, "events": []}
            kernel_tools.lookup_kernel_events("abc-123")
        mock_get.assert_called_once_with(
            "/api/hermes/kernel/events", {"trace_id": "abc-123", "limit": 50}
        )

    def test_surfaces_client_error_unchanged(self):
        with patch("kernel_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
            result = kernel_tools.lookup_kernel_events("abc-123")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
