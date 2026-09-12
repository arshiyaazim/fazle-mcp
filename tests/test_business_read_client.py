from unittest.mock import Mock, patch
from pathlib import Path

import business_read_client as client


def test_core_hermes_forwards_named_query_and_pagination_only():
    response = Mock(status_code=200, is_success=True)
    response.json.return_value = {"dataset": "payroll", "rows": [{"run_id": 1}]}
    with patch.object(client, "BEARER", "service-secret"), patch.object(client.httpx, "post", return_value=response) as post:
        result = client.query("payroll", filters={"year": 2026}, limit=5000, offset=5000)
    assert result["rows"] == [{"run_id": 1}]
    kwargs = post.call_args.kwargs
    assert kwargs["headers"] == {"Authorization": "Bearer service-secret"}
    assert kwargs["json"]["dataset"] == "payroll"
    assert kwargs["json"]["limit"] == 5000 and kwargs["json"]["offset"] == 5000
    assert "sql" not in kwargs["json"]


def test_missing_or_rejected_service_credential_fails_closed():
    with patch.object(client, "BEARER", ""):
        assert "error" in client.query("employee")
    response = Mock(status_code=403, is_success=False)
    with patch.object(client, "BEARER", "bad"), patch.object(client.httpx, "post", return_value=response):
        assert client.query("employee") == {"error": "Core business read rejected the service credential"}


def test_mcp_server_registers_the_fixed_query_client():
    source = (Path(__file__).parents[1] / "server.py").read_text()
    assert "def read_core_business(" in source
    assert "return business_read_client.query(" in source
