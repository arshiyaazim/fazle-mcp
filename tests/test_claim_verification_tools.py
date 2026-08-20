import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claim_verification_tools


class TestVerifyEmployeeClaim(unittest.TestCase):
    """Read-only, no mode gate."""

    @patch("claim_verification_tools.core.get")
    def test_passes_all_params_through(self, mock_get):
        mock_get.return_value = {"outcome": "verified"}
        claim_verification_tools.verify_employee_claim(
            claimed_name="Karim", claimed_role="supervisor", sender_phone="", employee_id=None,
        )
        mock_get.assert_called_once_with("/api/identity/verify-claim", {
            "claimed_name": "Karim", "claimed_role": "supervisor", "sender_phone": "",
        })

    @patch("claim_verification_tools.core.get")
    def test_employee_id_none_is_omitted_not_sent_as_empty_string(self, mock_get):
        """2026-08-20 regression: a live smoke test found this exact call
        shape 422'd against fazle-core -- httpx serializes a None param
        value as an empty string, and Optional[int] can't coerce "".
        employee_id must be OMITTED from the params dict when None, not
        passed through as None."""
        mock_get.return_value = {"outcome": "not_found"}
        claim_verification_tools.verify_employee_claim(claimed_name="Karim")
        args, _ = mock_get.call_args
        self.assertNotIn("employee_id", args[1])

    @patch("claim_verification_tools.core.get")
    def test_surfaces_ambiguous_outcome_unchanged(self, mock_get):
        mock_get.return_value = {"outcome": "ambiguous", "candidates": [{"employee_id": 1}, {"employee_id": 2}]}
        result = claim_verification_tools.verify_employee_claim(claimed_name="Karim")
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)

    @patch("claim_verification_tools.core.get")
    def test_not_found_outcome_passthrough(self, mock_get):
        mock_get.return_value = {"outcome": "not_found", "message": "..."}
        result = claim_verification_tools.verify_employee_claim(claimed_name="Nonexistent")
        self.assertEqual(result["outcome"], "not_found")

    @patch("claim_verification_tools.core.get")
    def test_role_mismatch_outcome_passthrough(self, mock_get):
        mock_get.return_value = {
            "outcome": "role_mismatch", "actual_designation": "Security Guard", "claimed_role": "supervisor",
        }
        result = claim_verification_tools.verify_employee_claim(claimed_name="Karim", claimed_role="supervisor")
        self.assertEqual(result["outcome"], "role_mismatch")
        self.assertEqual(result["actual_designation"], "Security Guard")

    @patch("claim_verification_tools.core.get")
    def test_surfaces_client_error_unchanged(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
        result = claim_verification_tools.verify_employee_claim(claimed_name="Karim")
        self.assertIn("error", result)

    @patch("claim_verification_tools.core.get")
    def test_employee_id_param_passed_as_int(self, mock_get):
        mock_get.return_value = {"outcome": "verified"}
        claim_verification_tools.verify_employee_claim(employee_id=42)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["employee_id"], 42)


if __name__ == "__main__":
    unittest.main()
