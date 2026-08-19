import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounting_tools


class AccountingTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("accounting_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestGetOperatorPending(unittest.TestCase):
    @patch("accounting_tools.core.get")
    def test_passes_limit_and_offset(self, mock_get):
        mock_get.return_value = {"total": 0, "items": []}
        accounting_tools.get_operator_pending(limit=10, offset=5)
        mock_get.assert_called_once_with("/api/fpe/operator/pending", {"limit": 10, "offset": 5})


class TestApproveOperatorPending(AccountingTestBase):
    @patch("accounting_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = accounting_tools.approve_operator_pending(1, employee_id=5, reviewer="owner", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("accounting_tools.core.post")
    def test_allowed_passes_required_params(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "approved", "pending_id": 1, "txn_id": 88}
        result = accounting_tools.approve_operator_pending(1, employee_id=5, reviewer="owner", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/fpe/operator/1/approve",
            params={"employee_id": 5, "reviewer": "owner", "txn_category": "salary"},
        )

    @patch("accounting_tools.core.post")
    def test_optional_fields_only_included_when_set(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "approved"}
        accounting_tools.approve_operator_pending(
            1, employee_id=5, reviewer="owner", payout_method="bkash",
            amount_override=1500, note="corrected amount", confirm=True,
        )
        mock_post.assert_called_once_with(
            "/api/fpe/operator/1/approve",
            params={
                "employee_id": 5, "reviewer": "owner", "txn_category": "salary",
                "payout_method": "bkash", "amount_override": 1500, "note": "corrected amount",
            },
        )


class TestRejectOperatorPending(AccountingTestBase):
    @patch("accounting_tools.core.post")
    def test_allowed_passes_reviewer_and_reason(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "rejected", "pending_id": 1}
        result = accounting_tools.reject_operator_pending(1, reviewer="owner", reason="unmatched", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with(
            "/api/fpe/operator/1/reject", params={"reviewer": "owner", "reason": "unmatched"}
        )


class TestCreateManualTransaction(AccountingTestBase):
    @patch("accounting_tools.core.post")
    def test_denied_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = accounting_tools.create_manual_transaction(
            5, 3000, "bkash", "2026-08-19", reason="advance"
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("accounting_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "created", "transaction": {"id": 200}}
        result = accounting_tools.create_manual_transaction(
            5, 3000, "bkash", "2026-08-19", reason="advance", confirm=True,
        )
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/fpe/transactions/manual", {
            "employee_id": 5, "amount": 3000, "payout_method": "bkash",
            "payout_phone": None, "txn_date": "2026-08-19",
            "txn_category": "salary", "reason": "advance",
        })


class TestReverseTransaction(AccountingTestBase):
    @patch("accounting_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = accounting_tools.reverse_transaction(200, reason="duplicate", confirm=True)
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("accounting_tools.core.post")
    def test_allowed_in_run_mode_with_confirm(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"status": "reversed", "reversal": {"id": 201}}
        result = accounting_tools.reverse_transaction(200, reason="duplicate", confirm=True)
        self.assertTrue(result["ok"])
        mock_post.assert_called_once_with("/api/fpe/transactions/200/reverse", {
            "txn_id": 200, "reason": "duplicate", "created_by": "admin",
        })


if __name__ == "__main__":
    unittest.main()
