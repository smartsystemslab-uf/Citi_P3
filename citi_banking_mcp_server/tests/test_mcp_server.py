import runpy

import httpx
import pytest

import mcp_server


class Response:
    def __init__(self, *, text="text", json_data=None, status=200):
        self.text = text
        self._json = json_data if json_data is not None else {"ok": True}
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://backend")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("bad", request=request, response=response)


class AsyncClient:
    calls = []
    response = Response()
    error = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    AsyncClient.calls = []
    AsyncClient.response = Response()
    AsyncClient.error = None
    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", AsyncClient)


@pytest.mark.asyncio
async def test_front_office_tools_success_validation_and_errors():
    AsyncClient.response = Response(json_data={"transaction_id": "T1"})
    assert "T1" in await mcp_server.get_transaction("T1")
    assert AsyncClient.calls[-1][1].endswith("/transactions/T1")

    payload_result = await mcp_server.run_fraud_and_compliance_workflow("A", "B", 12.5, "USD")
    assert "Fraud Evaluation Result" in payload_result
    assert AsyncClient.calls[-1][2]["json"]["amount"] == 12.5

    invalid = await mcp_server.update_transaction_status("T1", "PENDING", "why")
    assert "Status must be one of" in invalid
    success = await mcp_server.update_transaction_status("T1", "APPROVED", "why")
    assert "marked as APPROVED" in success
    assert AsyncClient.calls[-1][2]["json"]["decision_reason"] == "why"

    AsyncClient.response = Response(status=404)
    assert "Transaction not found. Status 404" in await mcp_server.get_transaction("missing")
    assert "Error running fraud check" in await mcp_server.run_fraud_and_compliance_workflow("A", "B", 1, "USD")
    assert "Error updating status" in await mcp_server.update_transaction_status("T1", "REJECTED", "why")


@pytest.mark.asyncio
async def test_mid_and_back_office_proxy_tools():
    AsyncClient.response = Response(text="backend response")
    assert await mcp_server.get_branch_workload("BR1") == "backend response"
    assert await mcp_server.propose_staffing_adjustment("A", "B", 2) == "backend response"
    assert AsyncClient.calls[-1][2]["json"] == {"from": "A", "to": "B", "count": 2}
    assert await mcp_server.get_customer_product_affinity("C1") == "backend response"
    assert await mcp_server.trigger_eod_reconciliation("2026-01-01") == "backend response"
    assert AsyncClient.calls[-1][2]["params"] == {"date": "2026-01-01"}
    assert await mcp_server.get_reconciliation_mismatches("R1") == "backend response"
    assert await mcp_server.resolve_ledger_entry("T1", 10.5, "correction") == "backend response"
    assert AsyncClient.calls[-1][2]["json"] == {"correction_amount": 10.5, "note": "correction"}


def test_main_starts_stdio_server(monkeypatch):
    calls = []
    monkeypatch.setattr(type(mcp_server.mcp), "run", lambda self, **kwargs: calls.append(kwargs))
    runpy.run_module("mcp_server", run_name="__main__")
    assert calls == [{"transport": "stdio"}]

