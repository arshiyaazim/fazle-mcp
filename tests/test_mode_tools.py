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


# ── set_mode_state tests (2026-08-24, Task 19) ──────────────────────────


class SetModeTestBase(unittest.TestCase):
    """Tests for set_mode_state().  Uses unittest.mock to patch the httpx
    call and the module-level _RUNNER_URL/_RUNNER_SECRET — never makes a
    real HTTP request, never touches the real mode file."""

    def setUp(self):
        self._url_patch = patch("mode_tools._RUNNER_URL", "http://fake-runner:8093")
        self._secret_patch = patch("mode_tools._RUNNER_SECRET", "fake-secret")
        self._url_patch.start()
        self._secret_patch.start()

    def tearDown(self):
        self._url_patch.stop()
        self._secret_patch.stop()

    def _mock_response(self, status_code=200, json_body=None):
        """Build a mock httpx.Response."""
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        return resp


class TestSetModeInputValidation(SetModeTestBase):
    """Client-side validation that fires BEFORE any HTTP call."""

    def test_invalid_mode_returns_error(self):
        result = mode_tools.set_mode_state("DESTROY")
        self.assertFalse(result["ok"])
        self.assertIn("mode must be one of", result["error"])

    def test_empty_mode_returns_error(self):
        result = mode_tools.set_mode_state("")
        self.assertFalse(result["ok"])

    def test_invalid_scope_returns_error(self):
        with patch("mode_tools.httpx") as _:
            result = mode_tools.set_mode_state("RUN", scope="FOREVER")
        self.assertFalse(result["ok"])
        self.assertIn("scope must be one of", result["error"])

    def test_mode_is_case_insensitive(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {
                "mode": "RUN", "set_at": "x", "expires_at": None,
                "scope": None, "set_by": "admin", "seconds_remaining": 1800,
            })
            result = mode_tools.set_mode_state("run", ttl_seconds=1800)
        self.assertTrue(result["ok"])
        # Verify the payload sent uppercase
        call_kwargs = mock_post.call_args
        self.assertEqual(call_kwargs.kwargs["json"]["mode"], "RUN")


class TestSetModeMissingConfig(unittest.TestCase):
    """Fails closed when env vars are missing."""

    def test_missing_runner_url(self):
        with patch("mode_tools._RUNNER_URL", ""), patch("mode_tools._RUNNER_SECRET", "x"):
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("HERMES_RUNNER_URL", result["error"])

    def test_missing_runner_secret(self):
        with patch("mode_tools._RUNNER_URL", "http://x"), patch("mode_tools._RUNNER_SECRET", ""):
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("HERMES_RUNNER_SECRET", result["error"])


class TestSetModeHTTPSuccess(SetModeTestBase):
    """Happy path — hermes-runner returns 200."""

    def test_run_mode_success(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {
                "mode": "RUN", "set_at": "2026-08-24T00:00:00+00:00",
                "expires_at": "2026-08-24T00:30:00+00:00",
                "scope": "TASK", "set_by": "admin", "seconds_remaining": 1800,
            })
            result = mode_tools.set_mode_state("RUN", ttl_seconds=1800, scope="TASK")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "RUN")
        self.assertEqual(result["ttl_seconds_remaining"], 1800)
        self.assertEqual(result["scope"], "TASK")
        self.assertEqual(result["source"], "current_mode_file")

    def test_read_mode_success(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {
                "mode": "READ", "set_at": "2026-08-24T00:00:00+00:00",
                "expires_at": None, "scope": None, "set_by": "admin",
                "seconds_remaining": None,
            })
            result = mode_tools.set_mode_state("READ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "READ")
        self.assertIsNone(result["expires_at"])

    def test_payload_omits_optional_fields_when_none(self):
        """ttl_seconds and scope should NOT be in the payload when None,
        so hermes-runner's own defaults apply."""
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {
                "mode": "BUILD", "set_at": "x", "expires_at": "y",
                "scope": None, "set_by": "admin", "seconds_remaining": 1800,
            })
            mode_tools.set_mode_state("BUILD")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload, {"mode": "BUILD"})
        self.assertNotIn("ttl_seconds", payload)
        self.assertNotIn("scope", payload)

    def test_bearer_auth_header_sent(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {"mode": "RUN"})
            mode_tools.set_mode_state("RUN")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer fake-secret")


class TestSetModeHTTPErrors(SetModeTestBase):
    """Error paths — hermes-runner rejects or is unreachable."""

    def test_400_validation_error(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(400, {
                "error": "ttl_seconds must be between 60 and 86400"
            })
            result = mode_tools.set_mode_state("RUN", ttl_seconds=5)
        self.assertFalse(result["ok"])
        self.assertIn("ttl_seconds", result["error"])

    def test_401_unauthorized(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(401, {"error": "unauthorized"})
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("unauthorized", result["error"])

    def test_timeout(self):
        import httpx as real_httpx
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.side_effect = real_httpx.TimeoutException("timed out")
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("did not respond", result["error"].lower())

    def test_connection_error(self):
        import httpx as real_httpx
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.side_effect = real_httpx.ConnectError("refused")
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("ConnectError", result["error"])

    def test_non_json_response(self):
        with patch("mode_tools.httpx.post") as mock_post:
            resp = self._mock_response(200)
            resp.json.side_effect = ValueError("not json")
            mock_post.return_value = resp
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("non-JSON", result["error"])


class TestSetModeNoPathLeak(SetModeTestBase):
    """Ensures no filesystem path leaks in the response, same standard
    as get_mode_state's own tests."""

    def test_success_response_has_no_path(self):
        with patch("mode_tools.httpx.post") as mock_post:
            mock_post.return_value = self._mock_response(200, {
                "mode": "RUN", "set_at": "x", "expires_at": "y",
                "scope": "TASK", "set_by": "admin", "seconds_remaining": 600,
            })
            result = mode_tools.set_mode_state("RUN", ttl_seconds=600)
        serialized = json.dumps(result)
        self.assertNotIn("hermes-runner", serialized)
        self.assertNotIn("current_mode.txt", serialized)
        self.assertNotIn(os.path.expanduser("~"), serialized)

    def test_error_response_has_no_path(self):
        result = mode_tools.set_mode_state("INVALID")
        serialized = json.dumps(result)
        self.assertNotIn("hermes-runner", serialized)
        self.assertNotIn("current_mode.txt", serialized)


if __name__ == "__main__":
    unittest.main()
