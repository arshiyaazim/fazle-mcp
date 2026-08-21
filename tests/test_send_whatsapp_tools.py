import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import send_whatsapp_tools


class SendWhatsappTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("send_whatsapp_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        with open(self.mode_file, "w") as f:
            f.write(mode + "\n")


class TestModeAndConfirmGate(SendWhatsappTestBase):
    @patch("send_whatsapp_tools.core.post")
    def test_denied_in_read_mode(self, mock_post):
        self._set_mode("READ")
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", source_bridge="bridge1", confirm=True
        )
        self.assertFalse(result["ok"])
        self.assertIn("RUN mode", result["error"])
        mock_post.assert_not_called()

    @patch("send_whatsapp_tools.core.post")
    def test_denied_in_build_mode_even_with_confirm(self, mock_post):
        self._set_mode("BUILD")
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", confirm=True
        )
        self.assertFalse(result["ok"])
        mock_post.assert_not_called()

    @patch("send_whatsapp_tools.core.post")
    def test_denied_in_run_mode_without_confirm(self, mock_post):
        self._set_mode("RUN")
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", source_bridge="bridge1", confirm=False
        )
        self.assertFalse(result["ok"])
        self.assertIn("confirmation", result["error"])
        mock_post.assert_not_called()

    def test_empty_recipient_rejected_before_mode_check(self):
        # No mode file exists in this case — confirms validation runs first.
        result = send_whatsapp_tools.send_whatsapp_message("", "hi", confirm=True)
        self.assertFalse(result["ok"])
        self.assertIn("recipient", result["error"])

    def test_empty_body_rejected_before_mode_check(self):
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "   ", confirm=True
        )
        self.assertFalse(result["ok"])
        self.assertIn("body", result["error"])

    def test_missing_source_bridge_rejected_before_mode_check(self):
        # 2026-08-15: no more silent "bridge2" default -- see
        # HERMES_MESSAGE_CONTEXT_REPLY_EXECUTION_GAP_AUDIT_2026-08-19.md.
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", confirm=True
        )
        self.assertFalse(result["ok"])
        self.assertIn("source_bridge", result["error"])

    def test_unknown_source_bridge_rejected(self):
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", source_bridge="not_a_real_bridge", confirm=True
        )
        self.assertFalse(result["ok"])
        self.assertIn("source_bridge", result["error"])

    @patch("send_whatsapp_tools.core.post")
    def test_allowed_in_run_mode_with_confirm_posts_all_fields(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"ok": True, "queue_id": 123}
        result = send_whatsapp_tools.send_whatsapp_message(
            recipient="8801700000000",
            body="Amar number e EKTA test message",
            source_bridge="bridge2",
            admin_instruction="Amar number e EKTA test message pathao",
            confirm=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["queue_id"], 123)
        self.assertTrue(result["confirmed"])
        mock_post.assert_called_once_with(
            "/admin/send-whatsapp",
            {
                "recipient": "8801700000000",
                "body": "Amar number e EKTA test message",
                "source_bridge": "bridge2",
                "admin_instruction": "Amar number e EKTA test message pathao",
            },
        )

    @patch("send_whatsapp_tools.core.post")
    def test_upstream_rbac_rejection_surfaces_cleanly(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"error": "fazle-core rejected the API key (unauthorized)"}
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", source_bridge="bridge1", confirm=True
        )
        self.assertFalse(result["ok"])
        self.assertIn("unauthorized", result["error"])


class TestQueuedNotSentOverclaim(SendWhatsappTestBase):
    """2026-08-21, urgent bridge3 recruitment-reply recovery, Task 5:
    enqueue success != delivery success. A successful send_whatsapp_message/
    approve_draft return must say "queued", never claim delivery."""

    @patch("send_whatsapp_tools.core.post")
    def test_send_whatsapp_message_reports_queued_not_sent(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"queue_id": 2371, "deduped": False}
        result = send_whatsapp_tools.send_whatsapp_message(
            "8801700000000", "hi", source_bridge="bridge3", confirm=True
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        self.assertNotEqual(result.get("status"), "sent")
        self.assertIn("check_outbound_status", result["note"])

    @patch("send_whatsapp_tools.core.post")
    def test_approve_draft_reports_queued_not_sent(self, mock_post):
        self._set_mode("RUN")
        mock_post.return_value = {"draft_id": 3087, "recipient": "8801865499694"}
        result = send_whatsapp_tools.approve_draft(3087, confirm=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        self.assertIn("check_outbound_status", result["note"])


class TestCheckOutboundStatus(unittest.TestCase):
    @patch("send_whatsapp_tools.core.get")
    def test_requires_queue_id_or_recipient(self, mock_get):
        result = send_whatsapp_tools.check_outbound_status()
        self.assertFalse(result["ok"])
        mock_get.assert_not_called()

    @patch("send_whatsapp_tools.core.get")
    def test_queue_id_lookup(self, mock_get):
        mock_get.return_value = {
            "items": [{"id": 2371, "status": "dlq", "delivered": False,
                       "in_flight": False, "definitively_failed": True}],
            "count": 1,
        }
        result = send_whatsapp_tools.check_outbound_status(queue_id=2371)
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"][0]["status"], "dlq")
        self.assertFalse(result["items"][0]["delivered"])
        mock_get.assert_called_once_with("/api/outbound/status", {"queue_id": 2371})

    @patch("send_whatsapp_tools.core.get")
    def test_recipient_lookup(self, mock_get):
        mock_get.return_value = {"items": [], "count": 0}
        send_whatsapp_tools.check_outbound_status(recipient="8801700000000")
        mock_get.assert_called_once_with("/api/outbound/status", {"recipient": "8801700000000"})

    @patch("send_whatsapp_tools.core.get")
    def test_error_passthrough(self, mock_get):
        mock_get.return_value = {"error": "fazle-core unreachable"}
        result = send_whatsapp_tools.check_outbound_status(queue_id=1)
        self.assertFalse(result["ok"])
        self.assertIn("unreachable", result["error"])


class TestReadMode(SendWhatsappTestBase):
    def test_missing_mode_file_defaults_to_read(self):
        self.assertEqual(send_whatsapp_tools._read_mode(), "READ")

    def test_legacy_bare_word_mode_file(self):
        self._set_mode("RUN")
        self.assertEqual(send_whatsapp_tools._read_mode(), "RUN")

    def test_expired_ttl_falls_back_to_read(self):
        with open(self.mode_file, "w") as f:
            json.dump({"mode": "RUN", "expires_at": "2020-01-01T00:00:00Z"}, f)
        self.assertEqual(send_whatsapp_tools._read_mode(), "READ")


if __name__ == "__main__":
    unittest.main()
