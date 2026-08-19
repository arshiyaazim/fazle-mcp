import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import identity_tools


class TestResolveIdentity(unittest.TestCase):
    def test_passes_phone_and_text_through(self):
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"role": "employee", "identity_role": "employee", "identity_confidence": 88}
            result = identity_tools.resolve_identity("01711234567", "amar betoner")
        mock_get.assert_called_once_with(
            "/api/identity/resolve", {"phone": "01711234567", "text": "amar betoner"}
        )
        self.assertEqual(result["identity_role"], "employee")

    def test_text_defaults_to_empty_string(self):
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"role": "unknown"}
            identity_tools.resolve_identity("01711234567")
        mock_get.assert_called_once_with(
            "/api/identity/resolve", {"phone": "01711234567", "text": ""}
        )

    def test_surfaces_client_error_unchanged(self):
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
            result = identity_tools.resolve_identity("01711234567")
        self.assertIn("error", result)

    def test_int_phone_coerced_to_string_2026_08_19_regression(self):
        # Live failure, 2026-08-19: a real call with phone=8801958122329
        # (a JSON int, not a quoted string) raised a raw Pydantic
        # validation error instead of succeeding with the obviously
        # intended value. Confirmed the same bug hit this exact tool
        # twice in one day (Alim/1596 case this morning, 2329 tonight).
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"role": "unknown"}
            identity_tools.resolve_identity(8801958122329)
        mock_get.assert_called_once_with(
            "/api/identity/resolve", {"phone": "8801958122329", "text": ""}
        )


class TestClassifyIntent(unittest.TestCase):
    def test_passes_text_through(self):
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"intent": "salary_query"}
            result = identity_tools.classify_intent("amar betoner koto")
        mock_get.assert_called_once_with("/api/intent/classify", {"text": "amar betoner koto"})
        self.assertEqual(result, {"intent": "salary_query"})

    def test_surfaces_client_error_unchanged(self):
        with patch("identity_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
            result = identity_tools.classify_intent("hello")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
