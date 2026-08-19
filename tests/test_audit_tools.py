import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_tools


class TestResolveInRoot(unittest.TestCase):
    def test_unknown_root_rejected(self):
        resolved, err = audit_tools._resolve_in_root("not-a-real-root")
        self.assertIsNone(resolved)
        self.assertIn("unknown root", err)

    def test_root_itself_resolves(self):
        resolved, err = audit_tools._resolve_in_root("assistant-platform")
        self.assertIsNone(err)
        self.assertTrue(resolved.endswith("assistant-platform"))

    def test_relative_path_traversal_rejected(self):
        resolved, err = audit_tools._resolve_in_root("assistant-platform", "../../etc/passwd")
        self.assertIsNone(resolved)
        self.assertIn("escapes", err)

    def test_absolute_path_outside_root_rejected(self):
        resolved, err = audit_tools._resolve_in_root("assistant-platform", "/etc/passwd")
        self.assertIsNone(resolved)
        self.assertIn("escapes", err)

    def test_env_file_denylisted_even_inside_root(self):
        resolved, err = audit_tools._resolve_in_root("assistant-platform", "backend/.env")
        self.assertIsNone(resolved)
        self.assertIn("denylisted", err)

    def test_secrets_dir_denylisted(self):
        resolved, err = audit_tools._resolve_in_root("fazle-core", "secrets/whatever.txt")
        self.assertIsNone(resolved)

    def test_normal_file_path_allowed(self):
        resolved, err = audit_tools._resolve_in_root("assistant-platform", "AI_ROLES_POLICY.md")
        self.assertIsNone(err)
        self.assertTrue(resolved.endswith("AI_ROLES_POLICY.md"))


class TestAuditReadFile(unittest.TestCase):
    def test_read_valid_file(self):
        result = audit_tools.audit_read_file("AI_ROLES_POLICY.md", root="assistant-platform", max_lines=5)
        self.assertNotIn("error", result)
        self.assertIn("content", result)
        self.assertLessEqual(result["end_line"] - result["start_line"] + 1, 5)

    def test_path_traversal_rejected(self):
        result = audit_tools.audit_read_file("../../../etc/passwd", root="assistant-platform")
        self.assertIn("error", result)

    def test_denylisted_file_rejected(self):
        result = audit_tools.audit_read_file("backend/.env", root="assistant-platform")
        self.assertIn("error", result)

    def test_nonexistent_file(self):
        result = audit_tools.audit_read_file("this-file-does-not-exist.md", root="assistant-platform")
        self.assertIn("error", result)

    def test_unauthorized_root(self):
        result = audit_tools.audit_read_file("whatever.md", root="etc")
        self.assertIn("error", result)

    def test_bounded_line_range(self):
        result = audit_tools.audit_read_file("AI_ROLES_POLICY.md", root="assistant-platform", start_line=1, end_line=3)
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 3)

    def test_oversized_file_without_range_is_bounded(self):
        with tempfile.NamedTemporaryFile(mode="w", dir=audit_tools.AUDIT_ROOTS["assistant-platform"], suffix=".txt", delete=False) as f:
            f.write("x" * (audit_tools.MAX_FILE_BYTES + 1000))
            temp_path = f.name
        try:
            rel = os.path.basename(temp_path)
            result = audit_tools.audit_read_file(rel, root="assistant-platform")
            self.assertIn("error", result)
            self.assertIn("too large", result["error"])
        finally:
            os.remove(temp_path)


class TestAuditSearch(unittest.TestCase):
    def test_valid_search_finds_matches(self):
        result = audit_tools.audit_search_docs("AI Roles Policy", root="assistant-platform")
        self.assertIn("matches", result)
        self.assertGreater(len(result["matches"]), 0)

    def test_empty_query_rejected(self):
        result = audit_tools.audit_search_code("", root="assistant-platform")
        self.assertIn("error", result)

    def test_unauthorized_root_rejected(self):
        result = audit_tools.audit_search_code("test", root="not-a-root")
        self.assertIn("error", result)

    def test_no_matches_returns_empty_list_not_error(self):
        result = audit_tools.audit_search_code("zzz_definitely_not_a_real_string_zzz_xyz123", root="assistant-platform")
        self.assertIn("matches", result)
        self.assertEqual(result["matches"], [])

    def test_max_results_respected(self):
        result = audit_tools.audit_search_code("import", root="assistant-platform", max_results=3)
        self.assertLessEqual(len(result["matches"]), 3)

    def test_denylisted_file_contents_not_searchable(self):
        """2026-08-14 fix: audit_search_code()/_grep() must never hand a
        denylisted (.env-shaped) file to grep -- previously audit_read_file()
        alone enforced this, so a value living only in a .env file was still
        findable via search even though it couldn't be read directly."""
        marker = "zzz_denylist_search_probe_secret_zzz"
        with tempfile.NamedTemporaryFile(
            mode="w", dir=audit_tools.AUDIT_ROOTS["assistant-platform"], suffix=".env", delete=False
        ) as f:
            f.write(f"PROBE_SECRET={marker}\n")
            env_path = f.name
        try:
            result = audit_tools.audit_search_code(marker, root="assistant-platform")
            self.assertIn("matches", result)
            self.assertEqual(result["matches"], [])
        finally:
            os.remove(env_path)

    def test_non_denylisted_file_still_searchable(self):
        """Control for the test above: a same-shaped marker in an ordinary
        (non-denylisted) file must still be found -- proves the fix scopes
        to denylisted paths only, not a general search regression."""
        marker = "zzz_normal_search_probe_zzz"
        with tempfile.NamedTemporaryFile(
            mode="w", dir=audit_tools.AUDIT_ROOTS["assistant-platform"], suffix=".txt", delete=False
        ) as f:
            f.write(f"NOT_A_SECRET={marker}\n")
            txt_path = f.name
        try:
            result = audit_tools.audit_search_code(marker, root="assistant-platform")
            self.assertIn("matches", result)
            self.assertEqual(len(result["matches"]), 1)
        finally:
            os.remove(txt_path)


class TestAuditLogs(unittest.TestCase):
    def test_unknown_log_rejected(self):
        result = audit_tools.audit_search_logs("query", log="not-a-real-log")
        self.assertIn("error", result)

    def test_int_query_coerced_to_string_2026_08_19_regression(self):
        # Live failure, 2026-08-19: a real call with query=8801958122329
        # (a JSON int) raised a raw Pydantic validation error instead of
        # searching for that number as text. Only asserting it doesn't
        # raise / doesn't hit the "unknown log" error path -- the log
        # source itself may legitimately have zero matches.
        result = audit_tools.audit_search_logs(8801958122329, log="backend", max_lines=5)
        self.assertNotEqual(result.get("error"), "query required")

    def test_backend_docker_source_returns_matches_not_error(self):
        # assistant-backend runs in Docker (its own log driver, no file on
        # the host) — real source is `docker compose logs`, found live
        # 2026-08-04. Hits the real running container.
        result = audit_tools.audit_search_logs("", log="backend", max_lines=5)
        self.assertNotIn("error", result)
        self.assertIn("matches", result)

    def test_coverage_html_excluded_from_search(self):
        self.assertIn("coverage_html", audit_tools.EXCLUDE_DIRS)

    def test_context_lines_returns_more_than_bare_match(self):
        # Found live 2026-08-04 investigating a real traceback: a bare
        # match on "File ..., line N, in func" doesn't include the actual
        # exception a few lines below. context_lines fixes that.
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("line1\nMATCH_HERE start\nline3 detail\nline4 detail\nline5 unrelated\n")
            temp_path = f.name
        try:
            with unittest.mock.patch.dict(
                audit_tools.LOG_SOURCES, {"fazle-core": {"type": "file", "path": temp_path}}
            ):
                bare = audit_tools.audit_search_logs("MATCH_HERE", log="fazle-core")
                with_context = audit_tools.audit_search_logs("MATCH_HERE", log="fazle-core", context_lines=2)
            self.assertEqual(len(bare["matches"]), 1)
            self.assertGreater(len(with_context["matches"]), len(bare["matches"]))
            self.assertTrue(any("line3 detail" in m for m in with_context["matches"]))
        finally:
            os.remove(temp_path)

    def test_context_lines_negative_clamped_not_rejected(self):
        # A negative/garbage value should be clamped to a safe default,
        # never passed straight through to grep -A.
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("MATCH_HERE\n")
            temp_path = f.name
        try:
            with unittest.mock.patch.dict(
                audit_tools.LOG_SOURCES, {"fazle-core": {"type": "file", "path": temp_path}}
            ):
                result = audit_tools.audit_search_logs("MATCH_HERE", log="fazle-core", context_lines=-5)
            self.assertNotIn("error", result)
        finally:
            os.remove(temp_path)

    def test_hermes_runner_journal_source_returns_matches_not_error(self):
        # hermes-runner.service has no StandardError= file redirect (a
        # stale comment in server.py claimed otherwise) — real source is
        # the systemd journal, found live 2026-08-04. This hits the real
        # `journalctl --user`, matching this file's existing pattern of
        # exercising real system state rather than mocking it.
        result = audit_tools.audit_search_logs("", log="hermes-runner", max_lines=5)
        self.assertNotIn("error", result)
        self.assertIn("matches", result)

    def test_fazle_core_missing_log_file_returns_note_not_error(self):
        with unittest.mock.patch.dict(
            audit_tools.LOG_SOURCES, {"fazle-core": {"type": "file", "path": "/tmp/definitely-not-a-real-log-file.log"}}
        ):
            result = audit_tools.audit_search_logs("query", log="fazle-core")
            self.assertNotIn("error", result)
            self.assertIn("note", result)

    def test_secret_pattern_redacted(self):
        line = audit_tools._redact_line("API_KEY=sk-abcdef1234567890")
        self.assertIn("[REDACTED]", line)
        self.assertNotIn("sk-abcdef1234567890", line)

    def test_phone_number_redacted(self):
        line = audit_tools._redact_line("contact number: 01712345678 called")
        self.assertNotIn("01712345678", line)
        self.assertIn("X", line)

    def test_bearer_token_redacted(self):
        line = audit_tools._redact_line("Authorization: Bearer abcdEFGH12345678")
        self.assertIn("[REDACTED]", line)


class TestAuditGit(unittest.TestCase):
    def test_git_status_valid_repo(self):
        result = audit_tools.audit_git_status("assistant-platform")
        self.assertIn("changes", result)

    def test_git_status_unauthorized_repo(self):
        result = audit_tools.audit_git_status("not-a-repo")
        self.assertIn("error", result)

    def test_recent_commits_no_git_dir(self):
        # fazle-mcp itself isn't in AUDIT_ROOTS/isn't a git repo — use an
        # allowlisted root instead to check the happy path shape.
        result = audit_tools.audit_recent_commits("assistant-platform", limit=3)
        self.assertIn("commits", result)
        self.assertLessEqual(len(result["commits"]), 3)


class TestAuditLookupWhatsappMessages(unittest.TestCase):
    @unittest.mock.patch("audit_tools.core.get")
    def test_success_masks_phone_fields_when_not_admin(self, mock_get):
        mock_get.return_value = {
            "count": 1,
            "messages": [{"message_id": 1, "sender_number": "8801712345678", "canonical_phone": "8801712345678", "platform": "bridge1"}],
        }
        with unittest.mock.patch("audit_tools._HERMES_IS_ADMIN_CONTEXT", False):
            result = audit_tools.audit_lookup_whatsapp_messages(phone="8801712345678")
        self.assertIn("X", result["messages"][0]["sender_number"])
        self.assertIn("X", result["messages"][0]["canonical_phone"])

    @unittest.mock.patch("audit_tools.core.get")
    def test_unmasked_in_current_admin_only_context(self, mock_get):
        mock_get.return_value = {"count": 1, "messages": [{"message_id": 1, "sender_number": "8801712345678"}]}
        result = audit_tools.audit_lookup_whatsapp_messages(phone="8801712345678")
        self.assertEqual(result["messages"][0]["sender_number"], "8801712345678")

    @unittest.mock.patch("audit_tools.core.get")
    def test_filters_passed_through(self, mock_get):
        mock_get.return_value = {"count": 0, "messages": []}
        audit_tools.audit_lookup_whatsapp_messages(phone="01712345678", platform="bridge1", is_processed=False, message_id=42, limit=10)
        args, kwargs = mock_get.call_args
        params = args[1]
        self.assertEqual(params["phone"], "01712345678")
        self.assertEqual(params["platform"], "bridge1")
        self.assertEqual(params["is_processed"], False)
        self.assertEqual(params["message_id"], 42)
        self.assertEqual(params["limit"], 10)

    @unittest.mock.patch("audit_tools.core.get")
    def test_int_phone_coerced_to_string_2026_08_19_regression(self, mock_get):
        # Live failure, 2026-08-19: a real call with phone=8801958122329
        # (a JSON int) raised a raw Pydantic validation error instead of
        # succeeding with the obviously intended value -- confirmed the
        # 2nd of 3 same-day occurrences of this exact bug class.
        mock_get.return_value = {"count": 0, "messages": []}
        audit_tools.audit_lookup_whatsapp_messages(phone=8801958122329, limit=10)
        args, kwargs = mock_get.call_args
        params = args[1]
        self.assertEqual(params["phone"], "8801958122329")

    @unittest.mock.patch("audit_tools.core.get")
    def test_no_filters_omits_optional_params(self, mock_get):
        mock_get.return_value = {"count": 0, "messages": []}
        audit_tools.audit_lookup_whatsapp_messages()
        args, kwargs = mock_get.call_args
        params = args[1]
        self.assertNotIn("phone", params)
        self.assertNotIn("platform", params)
        self.assertNotIn("is_processed", params)
        self.assertNotIn("message_id", params)
        self.assertEqual(params["limit"], 20)

    @unittest.mock.patch("audit_tools.core.get")
    def test_limit_clamped_to_max(self, mock_get):
        mock_get.return_value = {"count": 0, "messages": []}
        audit_tools.audit_lookup_whatsapp_messages(limit=99999)
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 100)

    @unittest.mock.patch("audit_tools.core.get")
    def test_invalid_limit_falls_back_to_default(self, mock_get):
        mock_get.return_value = {"count": 0, "messages": []}
        audit_tools.audit_lookup_whatsapp_messages(limit="bad")
        args, _ = mock_get.call_args
        self.assertEqual(args[1]["limit"], 20)

    @unittest.mock.patch("audit_tools.core.get")
    def test_error_passthrough_no_crash(self, mock_get):
        mock_get.return_value = {"error": "fazle-core error (status 404)"}
        result = audit_tools.audit_lookup_whatsapp_messages(message_id=1)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
