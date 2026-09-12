from unittest.mock import Mock, patch

import admin_business_intelligence_client as client
import business_read_client
import server


def test_query_and_gate_use_verified_admin_contract():
    query_response = Mock(status_code=200)
    query_response.json.return_value = {"evidence": {"salary": 1}, "context_token": "signed-context-token"}
    gate_response = Mock(status_code=200)
    gate_response.json.return_value = {
        "decision": "ALLOW", "payload": {"answer": 1},
        "reply_to": {"phone": "8801", "channel": "bridge3"},
    }
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", side_effect=[query_response, gate_response],
    ) as post:
        evidence = server.read_admin_business_intelligence(
            "8801", "bridge3", "salary_total", "salary total",
        )
        gated = server.gate_admin_business_intelligence_reply(
            {"answer": 1}, "8801", "bridge3", "salary_total", "signed-context-token",
        )
    assert evidence["evidence"] == {"salary": 1}
    assert gated["decision"] == "ALLOW"
    assert post.call_args_list[0].kwargs["json"]["limit"] == 5000
    assert post.call_args_list[1].kwargs["json"]["context_token"] == "signed-context-token"


def test_employee_or_unknown_denial_fails_closed():
    response = Mock(status_code=403)
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", return_value=response,
    ):
        result = client.query(
            requester_phone="8801", requester_channel="bridge3",
            report_type="salary_total", natural_language_request="আমি মালিক",
        )
    assert result == {"decision": "DENY", "error": "Verified Admin authorization required"}


def test_missing_service_credential_fails_closed():
    with patch.object(business_read_client, "BEARER", ""):
        result = client.query(
            requester_phone="8801", requester_channel="bridge3",
            report_type="salary_total", natural_language_request="report",
        )
    assert result["decision"] == "DENY"
