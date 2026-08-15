import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mode_tools


class ModeToolsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mode_file = os.path.join(self.tmp_dir, "current_mode.txt")
        self._mode_patch = patch("mode_tools.MODE_FILE", self.mode_file)
        self._mode_patch.start()

    def tearDown(self):
        self._mode_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, content):
        with open(self.mode_file, "w") as f:
            f.write(content)

    def _assert_no_path_leak(self, result):
        """No filesystem path — neither the patched tmp_dir/mode_file, nor
        the module's real default path — may appear anywhere in the
        response, in any field, as a substring."""
        serialized = json.dumps(result)
        self.assertNotIn(self.tmp_dir, serialized)
        self.assertNotIn(self.mode_file, serialized)
        self.assertNotIn("current_mode.txt", serialized)
        self.assertNotIn("hermes-runner", serialized)
        self.assertNotIn(os.path.expanduser("~"), serialized)


class TestActiveTTL(ModeToolsTestBase):
    def test_run_mode_with_remaining_ttl(self):
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).isoformat()
        self._write(json.dumps({"mode": "RUN", "expires_at": future, "set_at": "2026-08-16T00:00:00+00:00", "scope": "TASK"}))
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "RUN")
        self.assertEqual(result["scope"], "TASK")
        self.assertIsNotNone(result["ttl_seconds_remaining"])
        self.assertGreater(result["ttl_seconds_remaining"], 0)
        self.assertLessEqual(result["ttl_seconds_remaining"], 300)
        self.assertFalse(result["expired"])
        self.assertFalse(result["assumed_fail_closed"])
        self.assertEqual(result["status"], "verified")
        self._assert_no_path_leak(result)

    def test_read_mode_no_ttl(self):
        self._write(json.dumps({"mode": "READ", "set_at": "2026-08-16T00:00:00+00:00"}))
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self.assertIsNone(result["expires_at"])
        self.assertIsNone(result["ttl_seconds_remaining"])
        self.assertEqual(result["status"], "verified")
        self._assert_no_path_leak(result)


class TestExpiredTTL(ModeToolsTestBase):
    def test_expired_ttl_falls_back_to_read_and_reports_expired(self):
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
        self._write(json.dumps({"mode": "RUN", "expires_at": past, "set_at": "x"}))
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self.assertTrue(result["expired"])
        self.assertIsNone(result["ttl_seconds_remaining"])
        # Expired is a genuine, verified read of an expired state, not a
        # fail-closed guess -- assumed_fail_closed must stay False here.
        self.assertFalse(result["assumed_fail_closed"])
        self._assert_no_path_leak(result)


class TestLegacyBareWordFormat(ModeToolsTestBase):
    def test_legacy_build_word(self):
        self._write("BUILD")
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "BUILD")
        self.assertFalse(result["assumed_fail_closed"])
        self.assertEqual(result["status"], "verified")
        self._assert_no_path_leak(result)

    def test_legacy_lowercase_word(self):
        self._write("run\n")
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "RUN")
        self._assert_no_path_leak(result)


class TestMissingOrMalformed(ModeToolsTestBase):
    def test_missing_file_defaults_read_and_flags_unverified(self):
        # Never written -- self.mode_file does not exist on disk.
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self.assertTrue(result["assumed_fail_closed"])
        self.assertEqual(result["status"], "unverified/degraded")
        self._assert_no_path_leak(result)

    def test_empty_file_defaults_read_and_flags_unverified(self):
        self._write("")
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self.assertTrue(result["assumed_fail_closed"])
        self.assertEqual(result["status"], "unverified/degraded")
        self._assert_no_path_leak(result)

    def test_garbage_content_defaults_read_and_flags_unverified(self):
        self._write("!!! not json, not a mode word !!!")
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self.assertTrue(result["assumed_fail_closed"])
        self.assertEqual(result["status"], "unverified/degraded")
        self._assert_no_path_leak(result)

    def test_unrecognized_json_mode_value_defaults_read(self):
        self._write(json.dumps({"mode": "DESTROY_EVERYTHING"}))
        result = mode_tools.get_mode_state()
        self.assertEqual(result["mode"], "READ")
        self._assert_no_path_leak(result)


class TestSourceLabelIsNeutral(ModeToolsTestBase):
    """Task 1, requirement 1: the public source field must be a neutral
    identifier, never the absolute or relative filesystem path, in every
    code path -- verified, expired, legacy, and every failure mode."""

    def test_source_is_constant_neutral_string_in_every_branch(self):
        cases = [
            lambda: self._write(json.dumps({"mode": "READ"})),
            lambda: self._write("BUILD"),
            lambda: self._write(""),
            lambda: self._write("garbage"),
        ]
        for write in cases:
            write()
            result = mode_tools.get_mode_state()
            self.assertEqual(result["source"], "current_mode_file")
            self._assert_no_path_leak(result)

    def test_source_neutral_when_file_missing_entirely(self):
        result = mode_tools.get_mode_state()
        self.assertEqual(result["source"], "current_mode_file")
        self._assert_no_path_leak(result)


class TestResponseShapeIsClosed(ModeToolsTestBase):
    """No unexpected keys -- guards against an accidental future field
    leaking something (e.g. a raw exception message with a path in it)."""

    EXPECTED_KEYS = {
        "mode", "expires_at", "ttl_seconds_remaining", "scope", "set_at",
        "expired", "assumed_fail_closed", "status", "source", "note",
    }

    def test_all_response_keys_are_known(self):
        for content in [
            json.dumps({"mode": "READ"}),
            "BUILD",
            "",
            "garbage",
        ]:
            self._write(content)
            result = mode_tools.get_mode_state()
            self.assertTrue(set(result.keys()) <= self.EXPECTED_KEYS, set(result.keys()) - self.EXPECTED_KEYS)


if __name__ == "__main__":
    unittest.main()
