import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


def test_returns_every_registered_tool_including_itself():
    result = server.list_my_capabilities()
    assert "tool_count" in result
    assert result["tool_count"] == len(server.mcp._tool_manager._tools)
    names = {t["name"] for t in result["tools"]}
    assert "list_my_capabilities" in names
    assert "get_contacts" in names
    assert "draft_whatsapp_reply" in names
    assert "opencode_dispatch" in names
    assert "run_scheduled_task" in names


def test_each_entry_has_name_description_and_required_params_fields():
    result = server.list_my_capabilities()
    for entry in result["tools"]:
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["description"], str)
        assert isinstance(entry["required_params"], list)


def test_required_params_reflect_actual_tool_signatures():
    result = server.list_my_capabilities()
    by_name = {t["name"]: t for t in result["tools"]}
    # audit_read_file's `path` has no default -> required
    assert "path" in by_name["audit_read_file"]["required_params"]
    # get_contacts's `limit` has a default -> not required
    assert by_name["get_contacts"]["required_params"] == []
    # draft_whatsapp_reply: recipient/bridge/draft_text have no defaults
    for p in ("recipient", "bridge", "draft_text"):
        assert p in by_name["draft_whatsapp_reply"]["required_params"]


def test_reflects_registry_changes_not_a_hardcoded_list():
    """Regression guard for the whole point of this tool: register a throwaway
    tool at runtime and confirm it shows up without any code change here."""
    @server.mcp.tool()
    def _throwaway_test_tool_xyz(a: int) -> int:
        """a test tool that should not normally exist."""
        return a

    try:
        result = server.list_my_capabilities()
        names = {t["name"] for t in result["tools"]}
        assert "_throwaway_test_tool_xyz" in names
    finally:
        server.mcp._tool_manager._tools.pop("_throwaway_test_tool_xyz", None)
