import unittest
from unittest.mock import patch

import draft_tools


class TestDraftWhatsappReply(unittest.TestCase):
    def test_missing_recipient_rejected_without_calling_core(self):
        with patch("draft_tools.core.post") as mock_post:
            result = draft_tools.draft_whatsapp_reply("", "bridge1", "hello")
        self.assertEqual(result, {"ok": False, "error": "recipient is required"})
        mock_post.assert_not_called()

    def test_missing_draft_text_rejected_without_calling_core(self):
        with patch("draft_tools.core.post") as mock_post:
            result = draft_tools.draft_whatsapp_reply("8801700000000", "bridge1", "   ")
        self.assertEqual(result, {"ok": False, "error": "draft_text is required"})
        mock_post.assert_not_called()

    def test_happy_path_posts_to_drafts_endpoint_with_all_fields(self):
        with patch(
            "draft_tools.core.post",
            return_value={"ok": True, "draft_id": 42, "status": "pending"},
        ) as mock_post:
            result = draft_tools.draft_whatsapp_reply(
                recipient="8801700000000",
                bridge="bridge2",
                draft_text="আপনার বেতন ৫ তারিখে পাঠানো হবে।",
                role="employee",
                intent="salary_query",
                context="asked about salary date",
            )
        self.assertEqual(result, {"ok": True, "draft_id": 42, "status": "pending"})
        mock_post.assert_called_once_with(
            "/api/drafts",
            {
                "recipient": "8801700000000",
                "bridge": "bridge2",
                "draft_text": "আপনার বেতন ৫ তারিখে পাঠানো হবে।",
                "role": "employee",
                "intent": "salary_query",
                "context": "asked about salary date",
            },
        )

    def test_defaults_role_intent_context_when_omitted(self):
        with patch("draft_tools.core.post", return_value={"ok": True}) as mock_post:
            draft_tools.draft_whatsapp_reply("8801700000001", "bridge1", "hi")
        _, kwargs_or_args = mock_post.call_args
        body = mock_post.call_args.args[1]
        self.assertEqual(body["role"], "unknown")
        self.assertEqual(body["intent"], "hermes_suggested")
        self.assertEqual(body["context"], "")

    def test_upstream_error_passed_through_unchanged(self):
        with patch(
            "draft_tools.core.post",
            return_value={"error": "fazle-core unreachable: connection refused"},
        ):
            result = draft_tools.draft_whatsapp_reply("8801700000000", "bridge1", "hi")
        self.assertIn("error", result)
        self.assertIn("unreachable", result["error"])


if __name__ == "__main__":
    unittest.main()
