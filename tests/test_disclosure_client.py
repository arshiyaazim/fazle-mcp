from unittest.mock import Mock, patch

import business_read_client
import disclosure_client
import server


def test_core_hermes_releases_only_core_allowed_payload():
    response = Mock(status_code=200, is_success=True)
    response.json.return_value = {"decision": "ALLOW", "payload": {"salary": 20000}}
    with patch.object(business_read_client, "BEARER", "service-secret"), patch.object(
        disclosure_client.httpx, "post", return_value=response,
    ) as post:
        result = server.gate_human_disclosure(
            {"salary": 20000}, "8801700000010", "salary", 10,
        )
    assert result == {"decision": "ALLOW", "payload": {"salary": 20000}}
    assert post.call_args.kwargs["json"]["subject_employee_id"] == 10


def test_core_hermes_denial_never_returns_candidate_payload():
    response = Mock(status_code=403, is_success=False)
    with patch.object(business_read_client, "BEARER", "service-secret"), patch.object(
        disclosure_client.httpx, "post", return_value=response,
    ):
        result = disclosure_client.gate(
            {"private": "must-not-escape"}, requester_phone="8801999999999",
            information_type="payroll_register",
        )
    assert result == {"decision": "DENY", "error": "Human disclosure denied"}
    assert "must-not-escape" not in str(result)


def test_missing_service_credential_fails_closed():
    with patch.object(business_read_client, "BEARER", ""):
        result = disclosure_client.gate({}, requester_phone="8801", information_type="job_details")
    assert result["decision"] == "DENY"


def test_success_status_without_explicit_allow_does_not_release_payload():
    response = Mock(status_code=200, is_success=True)
    response.json.return_value = {"decision": "DENY", "payload": {"private": "must-not-escape"}}
    with patch.object(business_read_client, "BEARER", "service-secret"), patch.object(
        disclosure_client.httpx, "post", return_value=response,
    ):
        result = disclosure_client.gate(
            {"private": "must-not-escape"}, requester_phone="8801999999999",
            information_type="payroll_register",
        )
    assert result == {"decision": "DENY", "error": "Human disclosure denied"}
    assert "must-not-escape" not in str(result)
