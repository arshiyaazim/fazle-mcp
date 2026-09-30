import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

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


class TestSetModeIsScopedElevation(unittest.TestCase):
    """2026-09-30: set_mode_state is a SHORT conversational window again.

    It was denied outright for one pass on the theory that a model-reachable
    privilege grant is a grant the model can give itself. The Owner's intent
    is that Hermes acts on the Admin's requests, so the control became scope
    rather than refusal: an explicit, capped, short TTL, raise-only, and
    never outbound authority on its own.

    Replaces the former TestSetMode* delegation tests, which covered the HTTP
    delegation, and the denial tests that superseded them.
    """

    def setUp(self):
        self._url = patch("mode_tools._RUNNER_URL", "http://fake-runner:8093")
        self._secret = patch("mode_tools._RUNNER_SECRET", "fake-secret")
        self._url.start()
        self._secret.start()
        self.addCleanup(self._url.stop)
        self.addCleanup(self._secret.stop)

    def _mock_response(self, status_code=200, json_body=None):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        return resp

    def test_ttl_required(self):
        with patch("mode_tools.httpx.post") as post:
            result = mode_tools.set_mode_state("RUN")
        self.assertFalse(result["ok"])
        self.assertIn("explicit ttl_seconds", result["error"])
        post.assert_not_called()

    def test_ttl_capped(self):
        cap = mode_tools.MAX_MODE_TTL_SECONDS
        with patch("mode_tools.httpx.post") as post:
            self.assertFalse(mode_tools.set_mode_state("RUN", ttl_seconds=cap + 1)["ok"])
            self.assertFalse(mode_tools.set_mode_state("RUN", ttl_seconds=86400)["ok"])
        post.assert_not_called()

    def test_cannot_lower_mode(self):
        with patch("mode_tools.httpx.post") as post:
            self.assertFalse(mode_tools.set_mode_state("READ", ttl_seconds=60)["ok"])
        post.assert_not_called()

    def test_invalid_mode_rejected(self):
        with patch("mode_tools.httpx.post") as post:
            self.assertFalse(mode_tools.set_mode_state("DESTROY", ttl_seconds=60)["ok"])
        post.assert_not_called()

    def test_valid_elevation_forwards_bearer_and_ttl(self):
        with patch("mode_tools.httpx.post") as post:
            post.return_value = self._mock_response(
                200, {"mode": "RUN", "seconds_remaining": 120, "scope": "TIME"}
            )
            result = mode_tools.set_mode_state("RUN", ttl_seconds=300, scope="TIME")
        self.assertTrue(result["ok"], result)
        kwargs = post.call_args[1]
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-secret")
        self.assertEqual(kwargs["json"]["ttl_seconds"], 300)
        self.assertEqual(kwargs["json"]["scope"], "TIME")
        self.assertIn("not standing RUN authority", result["note"])

    def test_upstream_error_surfaces(self):
        with patch("mode_tools.httpx.post") as post:
            post.return_value = self._mock_response(400, {"error": "bad mode"})
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad mode")

    # --- 2026-09-30: transport/config error paths. These were dropped when
    # the delegation tests were rewritten around the scoped-elevation
    # contract, but every branch below still exists in set_mode_state. Each
    # one must degrade to a NOT-ok dict and never raise, because this
    # function is reachable from a model-facing MCP tool: an exception here
    # would surface to the model as an opaque crash rather than as "the
    # elevation did not happen". The failure direction matters -- a failed
    # call must leave the mode unelevated (deny by default), never elevated.
    def test_timeout_is_reported_and_never_raises(self):
        with patch("mode_tools.httpx.post", side_effect=httpx.TimeoutException("read timed out")):
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "hermes-runner did not respond in time")
        # A timeout must not be mistaken for a completed elevation.
        self.assertNotIn("mode", result)

    def test_connection_error_is_reported_and_never_raises(self):
        with patch("mode_tools.httpx.post", side_effect=httpx.ConnectError("connection refused")):
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertIn("could not reach hermes-runner", result["error"])
        # The exception CLASS is reported, never its message, so an internal
        # host/port detail cannot leak back to the model.
        self.assertIn("ConnectError", result["error"])
        self.assertNotIn("connection refused", result["error"])
        self.assertNotIn("fake-runner", result["error"])

    def test_non_json_response_is_reported_with_status(self):
        resp = self._mock_response(502, {})
        resp.json.side_effect = ValueError("not json")
        with patch("mode_tools.httpx.post", return_value=resp):
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertIn("non-JSON", result["error"])
        # The status code is included so an upstream HTML error page is
        # diagnosable from the caller's side.
        self.assertIn("502", result["error"])

    def test_missing_runner_url_is_refused_before_any_request(self):
        with patch("mode_tools._RUNNER_URL", ""), \
             patch("mode_tools.httpx.post") as post:
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertIn("HERMES_RUNNER_URL", result["error"])
        post.assert_not_called()

    def test_missing_runner_secret_is_refused_before_any_request(self):
        with patch("mode_tools._RUNNER_SECRET", ""), \
             patch("mode_tools.httpx.post") as post:
            result = mode_tools.set_mode_state("RUN", ttl_seconds=60)
        self.assertFalse(result["ok"])
        self.assertIn("HERMES_RUNNER_SECRET", result["error"])
        post.assert_not_called()

    def test_get_mode_state_still_works(self):
        state = mode_tools.get_mode_state()
        self.assertIn(state.get("mode"), ("READ", "BUILD", "RUN"))


if __name__ == "__main__":
    unittest.main()
