import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import action_policy


class TestClassifyTerminalCommand(unittest.TestCase):
    def test_destructive_rm_rf(self):
        self.assertEqual(action_policy.classify_terminal_command("rm -rf /home/azim/core"), "DESTRUCTIVE")

    def test_destructive_git_reset_hard(self):
        self.assertEqual(action_policy.classify_terminal_command("git reset --hard HEAD~1"), "DESTRUCTIVE")

    def test_destructive_force_push(self):
        self.assertEqual(action_policy.classify_terminal_command("git push --force origin main"), "DESTRUCTIVE")

    def test_destructive_drop_table(self):
        self.assertEqual(action_policy.classify_terminal_command("psql -c 'DROP TABLE fazle_tasks'"), "DESTRUCTIVE")

    def test_destructive_delete_without_where(self):
        self.assertEqual(action_policy.classify_terminal_command("DELETE FROM hermes_tasks"), "DESTRUCTIVE")

    def test_service_mutation_systemctl_restart(self):
        self.assertEqual(action_policy.classify_terminal_command("sudo systemctl restart fazle-core.service"), "SERVICE_MUTATION")

    def test_service_mutation_restart_gate(self):
        self.assertEqual(action_policy.classify_terminal_command("./scripts/restart_gate.sh --restart"), "SERVICE_MUTATION")

    def test_database_mutation_migrate(self):
        self.assertEqual(action_policy.classify_terminal_command("python3 db/migrate.py"), "DATABASE_MUTATION")

    def test_deployment_plain_push(self):
        self.assertEqual(action_policy.classify_terminal_command("git push origin main"), "DEPLOYMENT")

    def test_repository_mutation_commit(self):
        self.assertEqual(action_policy.classify_terminal_command('git commit -m "fix bug"'), "REPOSITORY_MUTATION")

    def test_safe_execution_pytest(self):
        self.assertEqual(action_policy.classify_terminal_command("pytest tests/unit/test_hermes_tasks.py"), "SAFE_EXECUTION")

    def test_read_only_git_status(self):
        self.assertEqual(action_policy.classify_terminal_command("git status"), "READ_ONLY")

    def test_read_only_git_diff(self):
        self.assertEqual(action_policy.classify_terminal_command("git diff --cached"), "READ_ONLY")

    def test_read_only_grep(self):
        self.assertEqual(action_policy.classify_terminal_command("grep -rn 'def foo' modules/"), "READ_ONLY")

    def test_read_only_cat(self):
        self.assertEqual(action_policy.classify_terminal_command("cat app/main.py"), "READ_ONLY")

    def test_empty_command_is_read_only(self):
        self.assertEqual(action_policy.classify_terminal_command(""), "READ_ONLY")

    def test_unrecognized_command_defaults_to_workspace_mutation(self):
        self.assertEqual(action_policy.classify_terminal_command("some-unknown-tool --flag"), "WORKSPACE_MUTATION")

    def test_destructive_check_runs_before_weaker_categories_even_if_it_also_matches_git(self):
        # A command matching BOTH "git commit"-shaped text and a destructive
        # pattern must classify as DESTRUCTIVE -- descending risk order.
        self.assertEqual(
            action_policy.classify_terminal_command("git commit -m 'x' && rm -rf /"),
            "DESTRUCTIVE",
        )


class TestClassifyFileOp(unittest.TestCase):
    def test_read_tool_is_read_only(self):
        self.assertEqual(action_policy.classify_file_op("read_file", "/home/azim/core/app/main.py"), "READ_ONLY")

    def test_search_tool_is_read_only(self):
        self.assertEqual(action_policy.classify_file_op("grep_search", "/home/azim/core"), "READ_ONLY")

    def test_write_tool_is_workspace_mutation(self):
        self.assertEqual(action_policy.classify_file_op("write_file", "/home/azim/core/app/main.py"), "WORKSPACE_MUTATION")

    def test_edit_tool_is_workspace_mutation(self):
        self.assertEqual(action_policy.classify_file_op("edit", "/home/azim/core/app/main.py"), "WORKSPACE_MUTATION")


class TestRequiresGatedApproval(unittest.TestCase):
    def test_read_only_never_gated(self):
        self.assertFalse(action_policy.requires_gated_approval("READ_ONLY"))

    def test_safe_execution_never_gated(self):
        self.assertFalse(action_policy.requires_gated_approval("SAFE_EXECUTION"))

    def test_workspace_mutation_not_action_gated(self):
        # Gated by BUILD-scope authorization instead -- a different mechanism.
        self.assertFalse(action_policy.requires_gated_approval("WORKSPACE_MUTATION"))

    def test_destructive_never_approvable(self):
        self.assertFalse(action_policy.requires_gated_approval("DESTRUCTIVE"))

    def test_repository_mutation_gated(self):
        self.assertTrue(action_policy.requires_gated_approval("REPOSITORY_MUTATION"))

    def test_service_mutation_gated(self):
        self.assertTrue(action_policy.requires_gated_approval("SERVICE_MUTATION"))

    def test_database_mutation_gated(self):
        self.assertTrue(action_policy.requires_gated_approval("DATABASE_MUTATION"))

    def test_deployment_gated(self):
        self.assertTrue(action_policy.requires_gated_approval("DEPLOYMENT"))


class TestActionTypeFor(unittest.TestCase):
    def test_repository_mutation_maps_to_git_commit(self):
        self.assertEqual(action_policy.action_type_for("REPOSITORY_MUTATION"), "git_commit")

    def test_service_mutation_maps_to_restart(self):
        self.assertEqual(action_policy.action_type_for("SERVICE_MUTATION"), "restart")

    def test_database_mutation_maps_to_migration(self):
        self.assertEqual(action_policy.action_type_for("DATABASE_MUTATION"), "migration")

    def test_deployment_maps_to_deploy(self):
        self.assertEqual(action_policy.action_type_for("DEPLOYMENT"), "deploy")


if __name__ == "__main__":
    unittest.main()
