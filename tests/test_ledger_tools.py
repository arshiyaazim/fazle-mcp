import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger_tools


class TestGetEmployeeLedger(unittest.TestCase):
    def test_passes_emp_id_and_periods_through(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"employee_id": 42, "ledger": []}
            ledger_tools.get_employee_ledger(42, periods=6)
        mock_get.assert_called_once_with("/api/fpe/ledger/42", {"periods": 6})

    def test_periods_defaults_to_12(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"employee_id": 42, "ledger": []}
            ledger_tools.get_employee_ledger(42)
        mock_get.assert_called_once_with("/api/fpe/ledger/42", {"periods": 12})

    def test_emp_id_coerced_to_int_in_path(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"employee_id": 42, "ledger": []}
            ledger_tools.get_employee_ledger("42")
        mock_get.assert_called_once_with("/api/fpe/ledger/42", {"periods": 12})

    def test_surfaces_client_error_unchanged(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core unreachable: timeout"}
            result = ledger_tools.get_employee_ledger(42)
        self.assertIn("error", result)


class TestGetEmployeeTransactionHistory(unittest.TestCase):
    def test_passes_page_and_page_size_through(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"transactions": []}
            ledger_tools.get_employee_transaction_history(42, page=2, page_size=10)
        mock_get.assert_called_once_with(
            "/api/fpe/employees/42/transactions", {"page": 2, "page_size": 10}
        )

    def test_defaults(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"transactions": []}
            ledger_tools.get_employee_transaction_history(42)
        mock_get.assert_called_once_with(
            "/api/fpe/employees/42/transactions", {"page": 1, "page_size": 20}
        )

    def test_date_filters_map_to_from_to_query_aliases(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"transactions": []}
            ledger_tools.get_employee_transaction_history(
                42, date_from="2026-07-01", date_to="2026-07-31"
            )
        mock_get.assert_called_once_with(
            "/api/fpe/employees/42/transactions",
            {"page": 1, "page_size": 20, "from": "2026-07-01", "to": "2026-07-31"},
        )

    def test_empty_date_filters_omitted_not_sent_as_blank(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"transactions": []}
            ledger_tools.get_employee_transaction_history(42)
        params = mock_get.call_args[0][1]
        self.assertNotIn("from", params)
        self.assertNotIn("to", params)

    def test_surfaces_client_error_unchanged(self):
        with patch("ledger_tools.core.get") as mock_get:
            mock_get.return_value = {"error": "fazle-core API not configured (FAZLE_CORE_API_KEY unset)"}
            result = ledger_tools.get_employee_transaction_history(42)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
