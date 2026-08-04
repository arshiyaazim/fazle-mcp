import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_schema import build_report


class TestBuildReport(unittest.TestCase):
    def test_required_fields_present(self):
        report = build_report(title="Test", status="HEALTHY", summary="All good")
        structured = report["structured"]
        for field in ["title", "timestamp", "status", "summary", "metrics", "evidence", "findings", "problems", "risk", "recommendations", "action_required", "next_steps"]:
            self.assertIn(field, structured)

    def test_timestamp_is_iso_format(self):
        report = build_report(title="T", status="HEALTHY", summary="s")
        # Should not raise
        import datetime
        datetime.datetime.fromisoformat(report["structured"]["timestamp"])

    def test_invalid_status_falls_back_to_unknown(self):
        report = build_report(title="T", status="NOT_A_REAL_STATUS", summary="s")
        self.assertEqual(report["structured"]["status"], "UNKNOWN")

    def test_text_rendering_healthy_example(self):
        report = build_report(
            title="Social Message Flow Report", status="HEALTHY", summary="Operating normally.",
            metrics={"received": 123, "processed": 117, "flagged": 4, "failed": 2},
            action_required="No owner decision required.",
        )
        text = report["text"]
        self.assertIn("STATUS: HEALTHY", text)
        self.assertIn("SUMMARY:", text)
        self.assertIn("received: 123", text)
        self.assertIn("ACTION REQUIRED: No owner decision required.", text)

    def test_text_rendering_degraded_example(self):
        report = build_report(
            title="T", status="degraded", summary="Issues found",
            findings=["2 failed messages detected."], risk="Medium",
            recommendations=["Investigate failed messages."],
        )
        text = report["text"]
        self.assertIn("STATUS: DEGRADED", text)
        self.assertIn("FINDINGS:", text)
        self.assertIn("- 2 failed messages detected.", text)
        self.assertIn("RISK: Medium", text)

    def test_empty_optional_fields_render_none_not_crash(self):
        report = build_report(title="T", status="HEALTHY", summary="s")
        self.assertIn("(none)", report["text"])

    def test_partial_fields_still_render_every_section(self):
        # Sections always render (with "(none)") even when empty — a
        # predictable, parseable shape beats a report whose structure
        # varies call to call.
        report = build_report(title="T", status="HEALTHY", summary="s", risk="Low")
        self.assertIn("RISK: Low", report["text"])
        self.assertIn("FINDINGS:", report["text"])
        self.assertIn("(none)", report["text"])


if __name__ == "__main__":
    unittest.main()
