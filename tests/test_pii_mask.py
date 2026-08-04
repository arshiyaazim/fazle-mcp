import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pii_mask import mask_phone, mask_phones_in_text, mask_pii, PII_FIELDS, TEXT_SCAN_FIELDS


class TestMaskPhone(unittest.TestCase):
    def test_local_0_prefixed(self):
        masked = mask_phone("01712345678")
        self.assertEqual(masked, "0XXXXXX5678")
        self.assertEqual(len(masked), len("01712345678"))

    def test_plus880_prefixed(self):
        masked = mask_phone("+8801712345678")
        self.assertTrue(masked.startswith("+880"))
        self.assertTrue(masked.endswith("5678"))
        self.assertEqual(len(masked), len("+8801712345678"))

    def test_non_string_passes_through(self):
        self.assertIsNone(mask_phone(None))
        self.assertEqual(mask_phone(12345), 12345)

    def test_short_string_passes_through(self):
        self.assertEqual(mask_phone("123"), "123")
        self.assertEqual(mask_phone(""), "")

    def test_never_reveals_middle_digits(self):
        masked = mask_phone("01798765432")
        self.assertNotIn("987654", masked)
        self.assertTrue(masked.endswith("5432"))


class TestMaskPii(unittest.TestCase):
    def setUp(self):
        self.row = {"contact_id": 1, "display_name": "Rahim", "whatsapp_number": "01712345678"}

    def test_admin_unmasked(self):
        result = mask_pii(self.row, is_admin=True)
        self.assertEqual(result["whatsapp_number"], "01712345678")

    def test_non_admin_masked(self):
        result = mask_pii(self.row, is_admin=False)
        self.assertIn("X", result["whatsapp_number"])
        self.assertNotEqual(result["whatsapp_number"], "01712345678")

    def test_non_pii_fields_untouched(self):
        result = mask_pii(self.row, is_admin=False)
        self.assertEqual(result["display_name"], "Rahim")

    def test_list_of_rows_masked(self):
        rows = [self.row, {**self.row, "contact_id": 2}]
        result = mask_pii(rows, is_admin=False)
        for r in result:
            self.assertIn("X", r["whatsapp_number"])

    def test_nested_object_masked(self):
        nested = {"items": [self.row], "count": 1}
        result = mask_pii(nested, is_admin=False)
        self.assertIn("X", result["items"][0]["whatsapp_number"])
        self.assertEqual(result["count"], 1)

    def test_none_and_primitives_safe(self):
        self.assertIsNone(mask_pii(None, is_admin=False))
        self.assertEqual(mask_pii(42, is_admin=False), 42)

    def test_pii_fields_cover_audit_findings(self):
        for f in ["whatsapp_number", "sender_number", "escort_mobile", "phone", "target_id"]:
            self.assertIn(f, PII_FIELDS)

    def test_message_body_embedded_phone_masked(self):
        row = {"sender_number": "01712345678", "message_body": "নগদ পার্সোনাল : 01339620136"}
        result = mask_pii(row, is_admin=False)
        self.assertIn("X", result["sender_number"])
        self.assertNotIn("01339620136", result["message_body"])
        self.assertIn("X", result["message_body"])
        self.assertTrue(result["message_body"].startswith("নগদ পার্সোনাল"))

    def test_message_body_without_phone_untouched(self):
        row = {"message_body": "ভাই কেমন আছেন?"}
        result = mask_pii(row, is_admin=False)
        self.assertEqual(result["message_body"], "ভাই কেমন আছেন?")

    def test_message_body_unmasked_for_admin(self):
        row = {"message_body": "call me at 01712345678"}
        result = mask_pii(row, is_admin=True)
        self.assertEqual(result["message_body"], "call me at 01712345678")

    def test_text_scan_fields_includes_message_body(self):
        self.assertIn("message_body", TEXT_SCAN_FIELDS)


class TestMaskPhonesInText(unittest.TestCase):
    def test_masks_single_embedded_number(self):
        result = mask_phones_in_text("reach me at 01712345678 anytime")
        self.assertNotIn("01712345678", result)
        self.assertIn("X", result)
        self.assertTrue(result.startswith("reach me at"))
        self.assertTrue(result.endswith("anytime"))

    def test_masks_multiple_embedded_numbers(self):
        result = mask_phones_in_text("call 01712345678 or 01898765432")
        self.assertNotIn("01712345678", result)
        self.assertNotIn("01898765432", result)

    def test_no_phone_number_unchanged(self):
        self.assertEqual(mask_phones_in_text("no numbers here"), "no numbers here")

    def test_non_string_passes_through(self):
        self.assertIsNone(mask_phones_in_text(None))
        self.assertEqual(mask_phones_in_text(42), 42)

    def test_plus880_embedded_number(self):
        result = mask_phones_in_text("my number is +8801712345678 ok")
        self.assertNotIn("1712345678", result)
        self.assertIn("+880", result)


if __name__ == "__main__":
    unittest.main()
