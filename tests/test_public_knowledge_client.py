from unittest.mock import Mock, patch

import business_read_client
import public_knowledge_client as client
import server


def test_public_knowledge_query_and_gate():
    query_response = Mock(status_code=200)
    query_response.json.return_value = {"approved_context": {"topic": "vacancy"}, "context_token": "signed"}
    gate_response = Mock(status_code=200)
    gate_response.json.return_value = {"decision": "ALLOW", "payload": {"answer": "ok"}, "reply_to": {"phone": "8801", "channel": "bridge3"}}
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", side_effect=[query_response, gate_response],
    ):
        found = server.read_approved_public_knowledge("8801", "bridge3", "vacancy", "vacancy আছে?")
        gated = server.gate_approved_public_reply({"answer": "ok"}, "8801", "bridge3", "vacancy", "signed")
    assert found["approved_context"]["topic"] == "vacancy"
    assert gated["decision"] == "ALLOW"


def test_denial_and_missing_configuration_fail_closed():
    denied = Mock(status_code=403)
    with patch.object(business_read_client, "BEARER", "service"), patch.object(client.httpx, "post", return_value=denied):
        assert client.query(requester_phone="8801", requester_channel="bridge3", topic="vacancy", question="সব employee salary")["decision"] == "DENY"
    with patch.object(business_read_client, "BEARER", ""):
        assert client.query(requester_phone="8801", requester_channel="bridge3", topic="vacancy", question="vacancy")["decision"] == "DENY"
