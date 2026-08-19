import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import payment_draft_tools


class PaymentDraftTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("payment_draft_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestGetPaymentDrafts(unittest.TestCase):
    """Read-only, no mode gate."""

    @patch("payment_draft_tools.core.get")
    def test_filters_by_status_and_phone_client_side(self, mock_get):
        mock_get.return_value = {
            "count": 3,
            "payment_drafts": [
                {"id": 1, "status": "pending", "payout_mobile": "01711234567"},
                {"id": 2, "status": "completed", "payout_mobile": "01711234567"},
                {"id": 3, "status": "pending", "employee_mobile": "01899998888"},
            ],
        }
        result = payment_draft_tools.get_payment_drafts(phone="01711234567")
        mock_get.assert_called_once_with("/admin/payment-drafts", {"limit": 20})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["payment_drafts"][0]["id"], 1)

    @patch("payment_draft_tools.core.get")
    def test_empty_status_returns_all(self, mock_get):
        mock_get.return_value = {"payment_drafts": [
            {"id": 1, "status": "pending"}, {"id": 2, "status": "completed"},
        ]}
        result = payment_draft_tools.get_payment_drafts(status="")
        self.assertEqual(result["count"], 2)

    @patch("payment_draft_tools.core.get")
    def test_surfaces_client_error_unchanged(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
        result = payment_draft_tools.get_payment_drafts()
        self.assertIn("error", result)


class TestApprovePaymentDraft(PaymentDraftTestBase):
    @patch("payment_draft_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = payment_draft_tools.approve_payment_draft(1, 3000, "bkash", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("payment_draft_tools.core.post")
    def test_denied_in_run_mode_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = payment_draft_tools.approve_payment_draft(1, 3000, "bkash", confirm=False)
        self.assertFalse(result["ok"])
        self.assertIn("confirmation", result["error"])
        mock_post.assert_not_called()

    @patch("payment_draft_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"ok": True, "draft_id": 1, "transaction_id": 55}
        result = payment_draft_tools.approve_payment_draft(1, 3000, "bkash", confirm=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction_id"], 55)
        mock_post.assert_called_once_with(
            "/api/payment-drafts/1/approve", {"amount": 3000, "method": "bkash"}
        )

    @patch("payment_draft_tools.core.post")
    def test_upstream_rejection_surfaces_cleanly(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"error": "Draft #1 already completed"}
        result = payment_draft_tools.approve_payment_draft(1, 3000, "bkash", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("already completed", result["error"])


class TestEditPaymentDraft(PaymentDraftTestBase):
    @patch("payment_draft_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = payment_draft_tools.edit_payment_draft(1, new_amount=4000)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("payment_draft_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"ok": True, "draft_id": 1, "version": 2}
        result = payment_draft_tools.edit_payment_draft(
            1, new_amount=4000, new_method="cash", reason="correction", confirm=True
        )
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/payment-drafts/1/edit",
            {"new_amount": 4000, "new_method": "cash", "new_payout_mobile": None, "reason": "correction"},
        )


class TestRejectPaymentDraft(PaymentDraftTestBase):
    @patch("payment_draft_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = payment_draft_tools.reject_payment_draft(1, reason="dup", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("payment_draft_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"ok": True, "draft_id": 1, "status": "rejected"}
        result = payment_draft_tools.reject_payment_draft(1, reason="dup", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/payment-drafts/1/reject", {"reason": "dup"})


class TestReadOnlyAuditAndBalanceTools(unittest.TestCase):
    @patch("payment_draft_tools.core.get")
    def test_get_payment_draft_audit(self, mock_get):
        mock_get.return_value = {"draft_id": 1, "events": []}
        payment_draft_tools.get_payment_draft_audit(1)
        mock_get.assert_called_once_with("/api/payment-drafts/1/audit")

    @patch("payment_draft_tools.core.get")
    def test_get_employee_balance_without_period(self, mock_get):
        mock_get.return_value = {"employee_id": 5}
        payment_draft_tools.get_payment_draft_employee_balance(5)
        mock_get.assert_called_once_with("/api/payment-drafts/employee/5/balance", None)

    @patch("payment_draft_tools.core.get")
    def test_get_employee_balance_with_period(self, mock_get):
        mock_get.return_value = {"employee_id": 5}
        payment_draft_tools.get_payment_draft_employee_balance(5, period="2026-07")
        mock_get.assert_called_once_with(
            "/api/payment-drafts/employee/5/balance", {"period": "2026-07"}
        )


if __name__ == "__main__":
    unittest.main()
