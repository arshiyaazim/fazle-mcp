from unittest.mock import Mock, patch
import httpx
import business_read_client
import output_privacy_client as client
import server


def test_output_gate_allow_and_fail_closed():
    allowed = Mock(status_code=200); allowed.json.return_value = {"decision": "ALLOW", "payload": {"answer": "ok"}, "reply_to": {"phone": "8801", "channel": "bridge3"}}
    with patch.object(business_read_client, "BEARER", "service"), patch.object(client.httpx, "post", return_value=allowed):
        result = server.gate_output_privacy("8801", "bridge3", "PUBLIC", {"answer": "ok"})
    assert result["decision"] == "ALLOW"
    denied = Mock(status_code=403)
    with patch.object(business_read_client, "BEARER", "service"), patch.object(client.httpx, "post", return_value=denied):
        assert client.gate(requester_phone="8801", requester_channel="bridge3", data_classification="ADMIN_REPORT", payload={})["decision"] == "DENY"


def test_output_gate_missing_config_transport_and_malformed_response_fail_closed():
    body = dict(requester_phone="8801", requester_channel="bridge3", data_classification="PUBLIC", payload={})
    with patch.object(business_read_client, "BEARER", ""):
        assert client.gate(**body)["decision"] == "DENY"
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", side_effect=httpx.ConnectError("offline")
    ):
        assert client.gate(**body)["decision"] == "DENY"
    malformed = Mock(status_code=200)
    malformed.json.return_value = {"decision": "ALLOW"}
    with patch.object(business_read_client, "BEARER", "service"), patch.object(client.httpx, "post", return_value=malformed):
        assert client.gate(**body)["decision"] == "DENY"
