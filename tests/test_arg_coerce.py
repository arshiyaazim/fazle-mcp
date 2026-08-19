import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arg_coerce import as_str


class TestAsStr(unittest.TestCase):
    def test_string_passes_through_unchanged(self):
        self.assertEqual(as_str("01712345678"), "01712345678")

    def test_int_coerced_to_string(self):
        self.assertEqual(as_str(8801958122329), "8801958122329")

    def test_float_coerced_to_string(self):
        self.assertEqual(as_str(1596.0), "1596.0")

    def test_none_becomes_empty_string(self):
        self.assertEqual(as_str(None), "")

    def test_empty_string_stays_empty(self):
        self.assertEqual(as_str(""), "")

    def test_zero_int_coerced_not_treated_as_falsy_none(self):
        # 0 is a legitimate (if unusual) string value once coerced -- must
        # not collapse to "" the way `str(x) if x else ""` would.
        self.assertEqual(as_str(0), "0")


if __name__ == "__main__":
    unittest.main()
