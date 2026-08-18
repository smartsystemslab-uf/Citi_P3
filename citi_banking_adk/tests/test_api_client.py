from types import SimpleNamespace

import httpx
import pytest

from bank_adk.infra import api_client as module


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_request_success_empty_and_http_errors(monkeypatch):
    response = SimpleNamespace(content=b'{"ok": true}', json=lambda: {"ok": True}, raise_for_status=lambda: None)
    fake = FakeClient(response)
    monkeypatch.setattr(module.httpx, "Client", lambda timeout: fake)
    client = module.BankingApiClient("http://bank/", timeout=3)
    assert client._request("GET", "/health", params={"x": 1}) == {"ok": True}
    assert fake.calls == [("GET", "http://bank/health", {"params": {"x": 1}})]

    response.content = b""
    assert client._request("DELETE", "/empty") == {}

    request = httpx.Request("GET", "http://bank/bad")
    json_response = httpx.Response(409, request=request, json={"detail": "duplicate"})
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda timeout: FakeClient(error=httpx.HTTPStatusError("bad", request=request, response=json_response)),
    )
    with pytest.raises(module.BankingApiError, match="409.*duplicate"):
        client._request("GET", "/bad")

    text_response = httpx.Response(500, request=request, text="broken")
    text_response.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda timeout: FakeClient(error=httpx.HTTPStatusError("bad", request=request, response=text_response)),
    )
    with pytest.raises(module.BankingApiError, match="500.*broken"):
        client._request("GET", "/bad")

    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda timeout: FakeClient(error=httpx.ConnectError("offline", request=request)),
    )
    with pytest.raises(module.BankingApiError, match="Cannot reach Banking API"):
        client._request("GET", "/bad")


def test_endpoint_helpers_and_transfer_shape(monkeypatch):
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/banking-api/transfer":
            return {
                "transaction_id": "T1",
                "status": "completed",
                "from_account_id": "A",
                "to_account_id": "B",
                "amount": 12.5,
                "risk_score": 2,
                "decision_reason": "ok",
            }
        return {"path": path}

    client = module.BankingApiClient("http://bank")
    monkeypatch.setattr(client, "_request", request)
    assert client.health()["path"] == "/health"
    client.get_customer_context("C1")
    client.get_customer("C1")
    client.list_customer_accounts("C1")
    client.get_transaction("T1")
    client.list_recent_transactions("C1", 7)
    client.search_policies("AML")
    client.list_rules()
    client.list_rules("AML")
    client.evaluate_transaction("A", "B", 12.5, "EUR")
    result = client.execute_transfer("A", "B", 12.5, "memo", "C1", "tester")
    assert result["currency"] == "USD"
    assert result["memo"] == "memo"
    assert result["raw_backend_result"]["transaction_id"] == "T1"
    assert ("GET", "/rules", {"params": None}) in calls
    assert ("GET", "/rules", {"params": {"category": "AML"}}) in calls
    assert module.json_dumps({"é": 1}).startswith("{")

