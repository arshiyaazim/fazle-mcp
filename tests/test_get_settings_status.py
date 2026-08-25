"""
audit_tools.get_settings_status() -- the fazle-mcp wrapper around
fazle-core's GET /api/settings/status (Task 2, 2026-08-16).

Task 2 required test #9: the MCP wrapper returns the endpoint's governed
output without adding sensitive data -- verified here by asserting the
wrapper passes the upstream response through unchanged (no extra keys
added, nothing renamed/reshaped in a way that could hide or inject data)
and that an upstream error is surfaced, not swallowed or replaced with an
invented default.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_tools


class TestGetSettingsStatus(unittest.TestCase):
    @patch("audit_tools.core.get")
    def test_passes_through_upstream_response_unchanged(self, mock_get):
        upstream = {
            "settings": {
                "primary_ai_provider": {"value": "ollama", "type": "str", "provenance": "default"},
                "debug": {"value": False, "type": "bool", "provenance": "default"},
            },
            "source": "static_config",
        }
        mock_get.return_value = upstream
        result = audit_tools.get_settings_status()
        self.assertEqual(result["settings"], upstream["settings"])
        self.assertEqual(result["source"], "static_config")
        # Exactly two top-level keys -- the wrapper must not add anything
        # of its own to what fazle-core already governed and returned.
        self.assertEqual(set(result.keys()), {"settings", "source"})
        mock_get.assert_called_once_with("/api/settings/status")

    @patch("audit_tools.core.get")
    def test_upstream_error_is_surfaced_not_swallowed(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: connection refused"}
        result = audit_tools.get_settings_status()
        self.assertIn("error", result)
        self.assertNotIn("settings", result)

    @patch("audit_tools.core.get")
    def test_missing_source_falls_back_to_static_config_label(self, mock_get):
        # Defensive: even if fazle-core's response shape ever drifts and
        # omits "source", the wrapper must not invent a path or anything
        # other than the same neutral label the endpoint itself uses.
        mock_get.return_value = {"settings": {}}
        result = audit_tools.get_settings_status()
        self.assertEqual(result["source"], "static_config")

    @patch("audit_tools.core.get")
    def test_no_secret_shaped_keys_added_by_the_wrapper_itself(self, mock_get):
        upstream = {"settings": {"log_level": {"value": "INFO", "type": "str", "provenance": "default"}}, "source": "static_config"}
        mock_get.return_value = upstream
        result = audit_tools.get_settings_status()
        import json
        text = json.dumps(result)
        for needle in ("password", "secret", "token", "api_key", "database_url", "/home/"):
            self.assertNotIn(needle, text.lower() if needle.islower() else text)


if __name__ == "__main__":
    unittest.main()
