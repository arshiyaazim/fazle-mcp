import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import domain_reports


class TestRecruitmentReport(unittest.TestCase):
    @patch("domain_reports.bridge.get")
    def test_success(self, mock_get):
        mock_get.return_value = [
            {"id": 1, "funnel_stage": "applied", "source": "referral"},
            {"id": 2, "funnel_stage": "interview", "source": "facebook"},
            {"id": 3, "funnel_stage": "applied", "source": "referral"},
        ]
        report = domain_reports.get_recruitment_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")
        self.assertEqual(report["structured"]["metrics"]["total_leads"], 3)
        self.assertEqual(report["structured"]["metrics"]["by_funnel_stage"]["applied"], 2)

    @patch("domain_reports.bridge.get")
    def test_empty(self, mock_get):
        mock_get.return_value = []
        report = domain_reports.get_recruitment_report()
        self.assertEqual(report["structured"]["metrics"]["total_leads"], 0)

    @patch("domain_reports.bridge.get")
    def test_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle bridge unreachable: Connection refused"}
        report = domain_reports.get_recruitment_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")

    @patch("domain_reports.bridge.get")
    def test_invalid_limit_falls_back_to_default(self, mock_get):
        mock_get.return_value = []
        domain_reports.get_recruitment_report(limit="bad")
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 100)


class TestEscortReport(unittest.TestCase):
    @patch("domain_reports.bridge.get")
    def test_healthy_all_assigned(self, mock_get):
        mock_get.return_value = [
            {"program_id": 1, "status": "completed", "escort_name": "Karim"},
            {"program_id": 2, "status": "pending", "escort_name": "Rahim"},
        ]
        report = domain_reports.get_escort_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")
        self.assertEqual(report["structured"]["metrics"]["unassigned"], 0)

    @patch("domain_reports.bridge.get")
    def test_degraded_on_unassigned(self, mock_get):
        mock_get.return_value = [
            {"program_id": 1, "status": "pending", "escort_name": None},
            {"program_id": 2, "status": "pending", "escort_name": ""},
        ]
        report = domain_reports.get_escort_report()
        self.assertEqual(report["structured"]["status"], "DEGRADED")
        self.assertEqual(report["structured"]["metrics"]["unassigned"], 2)
        self.assertTrue(len(report["structured"]["problems"]) > 0)

    @patch("domain_reports.bridge.get")
    def test_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle bridge error (status 503)"}
        report = domain_reports.get_escort_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")


class TestPayrollReport(unittest.TestCase):
    @patch("domain_reports.bridge.get")
    def test_success(self, mock_get):
        mock_get.return_value = [
            {"employee_name": "A", "status": "paid", "net_salary": "1000.50"},
            {"employee_name": "B", "status": "pending", "net_salary": "2000.00"},
        ]
        report = domain_reports.get_payroll_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")
        self.assertEqual(report["structured"]["metrics"]["sampled_net_salary_total"], 3000.50)

    @patch("domain_reports.bridge.get")
    def test_handles_missing_net_salary(self, mock_get):
        mock_get.return_value = [{"employee_name": "A", "status": "paid", "net_salary": None}]
        report = domain_reports.get_payroll_report()
        self.assertEqual(report["structured"]["metrics"]["sampled_net_salary_total"], 0)

    @patch("domain_reports.bridge.get")
    def test_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle bridge unreachable: timeout"}
        report = domain_reports.get_payroll_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")


class TestWhatsappOpsReport(unittest.TestCase):
    @patch("domain_reports.operational_tools.get_dlq_status")
    @patch("domain_reports.operational_tools.get_bridge_message_stats")
    def test_healthy(self, mock_stats, mock_dlq):
        mock_stats.return_value = {"hours": 24, "by_platform": [{"platform": "bridge2", "total": 100, "stuck_over_1h": 0}]}
        mock_dlq.return_value = {"total": 0, "items": []}
        report = domain_reports.get_whatsapp_ops_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")

    @patch("domain_reports.operational_tools.get_dlq_status")
    @patch("domain_reports.operational_tools.get_bridge_message_stats")
    def test_degraded_on_stuck_messages(self, mock_stats, mock_dlq):
        mock_stats.return_value = {"hours": 24, "by_platform": [{"platform": "bridge1", "total": 100, "stuck_over_1h": 33}]}
        mock_dlq.return_value = {"total": 0, "items": []}
        report = domain_reports.get_whatsapp_ops_report()
        self.assertEqual(report["structured"]["status"], "DEGRADED")
        self.assertEqual(report["structured"]["metrics"]["stuck_over_1h_total"], 33)

    @patch("domain_reports.operational_tools.get_dlq_status")
    @patch("domain_reports.operational_tools.get_bridge_message_stats")
    def test_error_passthrough(self, mock_stats, mock_dlq):
        mock_stats.return_value = {"error": "fazle-core error (status 404)"}
        mock_dlq.return_value = {"total": 0}
        report = domain_reports.get_whatsapp_ops_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")


class TestBridgeHealthReport(unittest.TestCase):
    @patch("domain_reports.operational_tools.get_bridge_diagnostics")
    def test_all_healthy(self, mock_diag):
        mock_diag.return_value = {"bridges": {"bridge1": {"state": "healthy", "consecutive_failures": 0}}}
        report = domain_reports.get_bridge_health_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")

    @patch("domain_reports.operational_tools.get_bridge_diagnostics")
    def test_critical_on_unhealthy_bridge(self, mock_diag):
        mock_diag.return_value = {"bridges": {"bridge1": {"state": "down", "consecutive_failures": 5}}}
        report = domain_reports.get_bridge_health_report()
        self.assertEqual(report["structured"]["status"], "CRITICAL")
        self.assertTrue(len(report["structured"]["problems"]) > 0)

    @patch("domain_reports.operational_tools.get_bridge_diagnostics")
    def test_error_passthrough(self, mock_diag):
        mock_diag.return_value = {"error": "fazle-core error (status 500)"}
        report = domain_reports.get_bridge_health_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")


class TestSystemHealthReport(unittest.TestCase):
    @patch("domain_reports.operational_tools.get_system_health")
    def test_ok(self, mock_health):
        mock_health.return_value = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        report = domain_reports.get_system_health_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")

    @patch("domain_reports.operational_tools.get_system_health")
    def test_degraded(self, mock_health):
        mock_health.return_value = {"status": "degraded", "probes": {"cpu": {"status": "degraded"}}}
        report = domain_reports.get_system_health_report()
        self.assertEqual(report["structured"]["status"], "DEGRADED")
        self.assertIn("cpu: degraded", report["structured"]["problems"])

    @patch("domain_reports.operational_tools.get_system_health")
    def test_error_passthrough(self, mock_health):
        mock_health.return_value = {"error": "fazle-core unreachable: Connection refused"}
        report = domain_reports.get_system_health_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
