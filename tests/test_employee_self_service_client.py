from unittest.mock import Mock, patch

import business_read_client
import employee_self_service_client as client
import server


def _response(payload, status=200):
    response = Mock(status_code=status, is_success=200 <= status < 300)
    response.json.return_value = payload
    return response


def test_query_has_no_target_employee_parameter_and_preserves_channel():
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", return_value=_response({
            "employee_id": 10, "payload": {"basic_salary": 9000},
            "reply_to": {"phone": "8801811111111", "channel": "bridge3"},
        }),
    ) as post:
        result = server.read_my_employee_information(
            "8801811111111", "bridge3", "salary",
        )
    assert result["employee_id"] == 10
    body = post.call_args.kwargs["json"]
    assert body["requester_channel"] == "bridge3"
    assert "employee_id" not in body and "target" not in body


def test_gate_requires_explicit_allow_and_same_channel_target():
    allowed = {
        "decision": "ALLOW", "payload": {"reply": "ok"},
        "reply_to": {"phone": "8801811111111", "channel": "bridge3"},
    }
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", return_value=_response(allowed),
    ):
        assert server.gate_my_employee_reply(
            {"reply": "ok"}, "8801811111111", "bridge3", "salary", "signed-context",
        ) == allowed


def test_denial_never_returns_candidate_payload():
    with patch.object(business_read_client, "BEARER", "service"), patch.object(
        client.httpx, "post", return_value=_response({"decision": "DENY"}, 403),
    ):
        result = client.gate_reply(
            {"private": "must-not-escape"}, requester_phone="8801999999999",
            requester_channel="bridge3", topic="salary", context_token="signed-context",
        )
    assert result["decision"] == "DENY"
    assert "must-not-escape" not in str(result)


def test_missing_credential_fails_closed():
    with patch.object(business_read_client, "BEARER", ""):
        result = client.query(
            requester_phone="8801811111111", requester_channel="bridge3", topic="salary",
        )
    assert result["decision"] == "DENY"
