import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import client_billing_tools

_APPROVED_ACTION = {"id": 99, "status": "approved", "action_type": "billing_mutation"}

_PROFILE_A = {"client_id": 1, "client_name": "ABC Shipping", "contact_person_mobile": "8801712340001", "contact_id": 10}
_PROFILE_B = {"client_id": 2, "client_name": "ABC Traders", "contact_person_mobile": "8801712340002", "contact_id": 11}
_PROFILE_C = {"client_id": 3, "client_name": "XYZ Marine", "contact_person_mobile": "8801712340003", "contact_id": 12}


class BillingTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("client_billing_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()
        self._action_patch = patch("task_tools.get_action", return_value=dict(_APPROVED_ACTION))
        self._action_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        self._action_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestClientResolution(unittest.TestCase):
    """Owner instruction: a bare client name must never silently select an
    arbitrary record."""

    @patch("client_billing_tools.core.get")
    def test_resolve_by_phone_exact_match(self, mock_get):
        mock_get.return_value = {"profiles": [_PROFILE_A, _PROFILE_B, _PROFILE_C]}
        result = client_billing_tools.find_client_billing_profile(phone="01712340001")
        self.assertTrue(result["found"])
        self.assertEqual(result["profile"]["client_id"], 1)

    @patch("client_billing_tools.core.get")
    def test_resolve_by_phone_tolerates_880_vs_01_form(self, mock_get):
        mock_get.return_value = {"profiles": [_PROFILE_A]}
        # profile stores 8801712340001; caller passes the 01-form.
        result = client_billing_tools.find_client_billing_profile(phone="01712340001")
        self.assertTrue(result["found"])

    @patch("client_billing_tools.core.get")
    def test_unknown_phone_returns_not_found_never_guesses(self, mock_get):
        mock_get.return_value = {"profiles": [_PROFILE_A, _PROFILE_B, _PROFILE_C]}
        result = client_billing_tools.find_client_billing_profile(phone="01999999999")
        self.assertFalse(result["found"])
        self.assertNotIn("profile", result)

    @patch("client_billing_tools.core.get")
    def test_ambiguous_name_returns_all_candidates_not_a_guess(self, mock_get):
        mock_get.return_value = {"profiles": [_PROFILE_A, _PROFILE_B, _PROFILE_C]}
        result = client_billing_tools.find_client_billing_profile(client_name="ABC")
        self.assertFalse(result["found"])
        self.assertTrue(result["ambiguous"])
        self.assertEqual(len(result["candidates"]), 2)

    @patch("client_billing_tools.core.get")
    def test_unique_name_match_resolves(self, mock_get):
        mock_get.return_value = {"profiles": [_PROFILE_A, _PROFILE_B, _PROFILE_C]}
        result = client_billing_tools.find_client_billing_profile(client_name="XYZ Marine")
        self.assertTrue(result["found"])
        self.assertEqual(result["profile"]["client_id"], 3)

    @patch("client_billing_tools.core.get")
    def test_no_phone_or_name_is_a_clean_error_not_a_crash(self, mock_get):
        result = client_billing_tools.find_client_billing_profile()
        self.assertFalse(result["found"])
        self.assertIn("error", result)
        mock_get.assert_not_called()


class TestReadOnlyToolsAreUngated(unittest.TestCase):
    @patch("client_billing_tools.core.get")
    def test_list_profiles_default_active_only(self, mock_get):
        mock_get.return_value = {"profiles": []}
        client_billing_tools.list_client_billing_profiles()
        mock_get.assert_called_once_with("/admin/client-billing-profiles", {"active": True})

    @patch("client_billing_tools.core.get")
    def test_get_outstanding_scoped_to_contact(self, mock_get):
        mock_get.return_value = {"bills": []}
        client_billing_tools.get_client_outstanding(contact_id=10)
        mock_get.assert_called_once_with("/admin/bills/outstanding", {"contact_id": 10})

    @patch("client_billing_tools.core.get")
    def test_get_outstanding_summary_no_params(self, mock_get):
        mock_get.return_value = {"summary": []}
        client_billing_tools.get_outstanding_summary()
        mock_get.assert_called_once_with("/admin/bills/outstanding/summary")

    @patch("client_billing_tools.core.get")
    def test_dashboard_summary(self, mock_get):
        mock_get.return_value = {"pending_bill_count": 0}
        client_billing_tools.get_billing_dashboard_summary()
        mock_get.assert_called_once_with("/admin/bills/dashboard-summary")


class TestApprovedActionHardGate(BillingTestBase):
    def test_generate_bill_missing_approved_action_id_denied(self):
        self._set_mode("RUN")
        result = client_billing_tools.generate_bill(5, "general", approved_action_id=0, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("approved_action_id", result["error"])

    @patch("client_billing_tools.core.post")
    def test_generate_bill_wrong_action_type_denied(self, mock_post):
        self._action_patch.stop()
        with patch("task_tools.get_action", return_value={"id": 99, "status": "approved", "action_type": "dispatch_assignment"}):
            self._set_mode("RUN")
            result = client_billing_tools.generate_bill(5, "general", approved_action_id=99, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("action_type", result["error"])
        mock_post.assert_not_called()
        self._action_patch.start()

    @patch("client_billing_tools.core.post")
    def test_generate_bill_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = client_billing_tools.generate_bill(5, "general", approved_action_id=99, confirm=False)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("client_billing_tools.core.post")
    def test_generate_bill_calls_through_when_approved(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"bill_id": 1, "bill_number": "AQS-2026-000001", "total_amount": 5000}
        result = client_billing_tools.generate_bill(5, "general", approved_action_id=99, confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/admin/bills/generate", {"program_id": 5, "duty_type": "general", "other_charges": 0}
        )

    @patch("client_billing_tools.core.post")
    def test_mark_bill_paid_requires_approved_action(self, mock_post):
        self._set_mode("RUN")
        result = client_billing_tools.mark_bill_paid(1, approved_action_id=0, confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("client_billing_tools.core.post")
    def test_mark_bill_paid_idempotent_rejection_surfaces(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {
            "error": "fazle-core error (status 409)",
            "error_detail": {"detail": "bill_id 1 is already Paid (paid 2026-08-01)"},
        }
        result = client_billing_tools.mark_bill_paid(1, approved_action_id=99, confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("already Paid", result["error"])

    @patch("client_billing_tools.core.delete")
    def test_delete_profile_requires_approved_action(self, mock_delete):
        self._set_mode("RUN")
        result = client_billing_tools.delete_client_billing_profile(1, approved_action_id=0, confirm=True)
        self.assertFalse(result["ok"])
        mock_delete.assert_not_called()

    @patch("client_billing_tools.core.delete")
    def test_delete_profile_calls_through_when_approved(self, mock_delete):
        self._set_mode("RUN")
        mock_delete.return_value = {"client_id": 1}
        result = client_billing_tools.delete_client_billing_profile(1, approved_action_id=99, confirm=True)
        self.assertTrue(result["ok"])
        mock_delete.assert_called_once_with("/admin/client-billing-profiles/1")


class TestReadMode(BillingTestBase):
    def test_missing_mode_file_defaults_to_read(self):
        self.assertEqual(client_billing_tools._read_mode(), "READ")

    def test_json_ttl_form_respected(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": None}, f)
        self.assertEqual(client_billing_tools._read_mode(), "RUN")


if __name__ == "__main__":
    unittest.main()
