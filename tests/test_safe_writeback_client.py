from unittest.mock import Mock, patch
import business_read_client
import safe_writeback_client as client
import server


def test_propose_confirm_and_fail_closed():
    proposed = Mock(status_code=200); proposed.json.return_value = {"action_id": 7, "status": "pending"}
    executed = Mock(status_code=200); executed.json.return_value = {"action_id": 7, "status": "executed", "memory_update": {"kind": "core_evidence"}}
    with patch.object(business_read_client, "BEARER", "service"), patch.object(client.httpx, "post", side_effect=[proposed, executed]):
        p = server.propose_safe_core_writeback("8801", "bridge3", "attendance_correction", {"attendance_id": 1, "duty_status": "Absent"}, "message-1", [{"type": "message_id", "value": "1"}], "correction")
        e = server.confirm_safe_core_writeback("8801", "bridge3", p["action_id"], True)
    assert p["status"] == "pending" and e["status"] == "executed"
    with patch.object(business_read_client, "BEARER", ""):
        assert client.confirm(requester_phone="8801", requester_channel="bridge3", action_id=7, confirmation=True)["ok"] is False
