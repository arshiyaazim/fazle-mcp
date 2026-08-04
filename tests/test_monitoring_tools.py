import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_tools


def _iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _row(job_name, status="ok", error=None, minutes_ago=0):
    return {
        "job_name": job_name,
        "last_run_at": _iso(minutes_ago),
        "last_status": status,
        "last_duration_ms": 50,
        "last_error": error,
        "next_run_at": _iso(-10),
        "run_count": 10,
    }


def _all_healthy_response():
    return {
        "enabled": True,
        "tz": "UTC",
        "jobs": [
            _row("dlq_alert", minutes_ago=5),
            _row("health_summary", minutes_ago=60),
            _row("agent_incident_summary", minutes_ago=60),
            _row("backup_staleness_alert", minutes_ago=120),
            _row("bridge_watchdog", minutes_ago=2),
            _row("daily_admin_digest", minutes_ago=120),
        ],
    }


class TestGetMonitoringStatus(unittest.TestCase):
    @patch("monitoring_tools.core.get")
    def test_all_jobs_ok(self, mock_get):
        mock_get.return_value = _all_healthy_response()
        result = monitoring_tools.get_monitoring_status()
        self.assertEqual(len(result["jobs"]), 6)
        self.assertTrue(all(j["state"] == "ok" for j in result["jobs"]))
        mock_get.assert_called_once_with("/scheduler/status")

    @patch("monitoring_tools.core.get")
    def test_error_status_marks_alerting(self, mock_get):
        resp = _all_healthy_response()
        resp["jobs"][0] = _row("dlq_alert", status="error", error="connection refused", minutes_ago=1)
        mock_get.return_value = resp
        result = monitoring_tools.get_monitoring_status()
        dlq = next(j for j in result["jobs"] if j["job_name"] == "dlq_alert")
        self.assertEqual(dlq["state"], "alerting")
        self.assertEqual(dlq["last_error"], "connection refused")

    @patch("monitoring_tools.core.get")
    def test_stale_job_detected(self, mock_get):
        resp = _all_healthy_response()
        resp["jobs"][1] = _row("health_summary", status="ok", minutes_ago=2000)  # way over 720min threshold
        mock_get.return_value = resp
        result = monitoring_tools.get_monitoring_status()
        hs = next(j for j in result["jobs"] if j["job_name"] == "health_summary")
        self.assertEqual(hs["state"], "stale")

    @patch("monitoring_tools.core.get")
    def test_missing_job_is_never_run(self, mock_get):
        resp = _all_healthy_response()
        resp["jobs"] = [j for j in resp["jobs"] if j["job_name"] != "bridge_watchdog"]
        mock_get.return_value = resp
        result = monitoring_tools.get_monitoring_status()
        bw = next(j for j in result["jobs"] if j["job_name"] == "bridge_watchdog")
        self.assertEqual(bw["state"], "never_run")

    @patch("monitoring_tools.core.get")
    def test_upstream_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable: refused"}
        result = monitoring_tools.get_monitoring_status()
        self.assertIn("error", result)

    def test_stale_thresholds_are_not_borderline_for_normal_operation(self):
        # A job that just ran a moment ago must never be "stale" — sanity
        # check on the threshold table itself.
        for job_name, minutes in monitoring_tools.MONITORED_JOBS.items():
            self.assertGreater(minutes, 0, f"{job_name} has a non-positive stale threshold")


class TestGetMonitoringReport(unittest.TestCase):
    # Test 1 — everything healthy.
    @patch("monitoring_tools.core.get")
    def test_all_healthy_report(self, mock_get):
        mock_get.return_value = _all_healthy_response()
        report = monitoring_tools.get_monitoring_report()
        self.assertEqual(report["structured"]["status"], "HEALTHY")
        self.assertIn("All monitored jobs healthy", report["text"])
        self.assertEqual(report["structured"]["problems"], [])

    # Test 2 — one job failed, real evidence available (last_error +
    # matching log line) -> report must cite the evidence and give a
    # recommendation, not just say "something's wrong."
    @patch("monitoring_tools.audit_tools.audit_search_logs")
    @patch("monitoring_tools.core.get")
    def test_one_failed_job_with_evidence(self, mock_get, mock_logs):
        resp = _all_healthy_response()
        resp["jobs"][1] = _row("health_summary", status="error", error="probe db timed out", minutes_ago=1)
        mock_get.return_value = resp
        mock_logs.return_value = {"matches": ["2026-08-04 health_summary probe db timed out after 5s"]}

        report = monitoring_tools.get_monitoring_report()
        structured = report["structured"]
        self.assertEqual(structured["status"], "CRITICAL")
        self.assertTrue(any("health_summary" in f for f in structured["findings"]))
        self.assertTrue(any("probe db timed out" in e for e in structured["evidence"]))
        self.assertTrue(len(structured["recommendations"]) > 0)
        self.assertNotIn("No action needed", structured["recommendations"])
        self.assertEqual(structured["action_required"], "Owner review needed.")

    # Test 3 — unknown failure: no last_error, no matching log lines.
    # Must NOT invent a root cause.
    @patch("monitoring_tools.audit_tools.audit_search_logs")
    @patch("monitoring_tools.core.get")
    def test_unknown_failure_does_not_hallucinate(self, mock_get, mock_logs):
        resp = _all_healthy_response()
        resp["jobs"][3] = _row("backup_staleness_alert", status="error", error=None, minutes_ago=1)
        mock_get.return_value = resp
        mock_logs.return_value = {"matches": []}

        report = monitoring_tools.get_monitoring_report()
        structured = report["structured"]
        self.assertEqual(structured["status"], "CRITICAL")
        joined_findings = " ".join(structured["findings"]).lower()
        self.assertIn("insufficient evidence to determine root cause", joined_findings)
        self.assertEqual(structured["evidence"], [])

    # Test 4 — this tool must never be capable of sending/duplicating an
    # alert, regardless of job state. Structural guarantee: core.post is
    # never called anywhere in a full report generation.
    @patch("monitoring_tools.audit_tools.audit_search_logs")
    @patch("monitoring_tools.core.post")
    @patch("monitoring_tools.core.get")
    def test_never_sends_or_triggers_anything(self, mock_get, mock_post, mock_logs):
        resp = _all_healthy_response()
        resp["jobs"][0] = _row("dlq_alert", status="error", error="dlq backed up", minutes_ago=1)
        mock_get.return_value = resp
        mock_logs.return_value = {"matches": ["dlq backed up, 40 items"]}

        monitoring_tools.get_monitoring_report()
        mock_post.assert_not_called()

    @patch("monitoring_tools.core.get")
    def test_stale_job_reported_as_degraded_not_critical(self, mock_get):
        resp = _all_healthy_response()
        resp["jobs"][4] = _row("bridge_watchdog", status="ok", minutes_ago=999)  # way over 15min threshold
        mock_get.return_value = resp
        report = monitoring_tools.get_monitoring_report()
        self.assertEqual(report["structured"]["status"], "DEGRADED")

    @patch("monitoring_tools.core.get")
    def test_never_run_job_reported(self, mock_get):
        resp = _all_healthy_response()
        resp["jobs"] = [j for j in resp["jobs"] if j["job_name"] != "daily_admin_digest"]
        mock_get.return_value = resp
        report = monitoring_tools.get_monitoring_report()
        self.assertTrue(any("never run" in f for f in report["structured"]["findings"]))

    @patch("monitoring_tools.core.get")
    def test_scheduler_disabled_is_critical(self, mock_get):
        resp = _all_healthy_response()
        resp["enabled"] = False
        mock_get.return_value = resp
        report = monitoring_tools.get_monitoring_report()
        self.assertEqual(report["structured"]["status"], "CRITICAL")
        self.assertTrue(any("disabled" in p for p in report["structured"]["problems"]))

    @patch("monitoring_tools.core.get")
    def test_upstream_unreachable_yields_unknown_report(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable"}
        report = monitoring_tools.get_monitoring_report()
        self.assertEqual(report["structured"]["status"], "UNKNOWN")

    @patch("monitoring_tools.audit_tools.audit_search_logs")
    @patch("monitoring_tools.core.get")
    def test_multiple_simultaneous_issues(self, mock_get, mock_logs):
        resp = _all_healthy_response()
        resp["jobs"][0] = _row("dlq_alert", status="error", error="dlq backed up", minutes_ago=1)
        resp["jobs"][4] = _row("bridge_watchdog", status="ok", minutes_ago=999)
        mock_get.return_value = resp
        mock_logs.return_value = {"matches": ["dlq backed up"]}
        report = monitoring_tools.get_monitoring_report()
        structured = report["structured"]
        self.assertEqual(structured["status"], "CRITICAL")  # worst wins
        self.assertEqual(len(structured["problems"]), 2)


if __name__ == "__main__":
    unittest.main()
