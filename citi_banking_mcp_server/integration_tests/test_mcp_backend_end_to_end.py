from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mcp_server

from conftest import MCP_SERVER, RunningBackend


@pytest.mark.asyncio
async def test_all_proxy_functions_call_the_real_banking_backend(live_backend: RunningBackend, monkeypatch):
    monkeypatch.setattr(mcp_server, "BACKEND_API_URL", live_backend.base_url)

    transaction = await mcp_server.get_transaction("TXN-1001")
    assert "TXN-1001" in transaction

    evaluation = await mcp_server.run_fraud_and_compliance_workflow("CHK-9001", "EXT-998", 60000, "USD")
    assert "manual_review" in evaluation
    status = await mcp_server.update_transaction_status("TXN-1001", "MANUAL_REVIEW", "integration rationale")
    assert "marked as MANUAL_REVIEW" in status

    workload = json.loads(await mcp_server.get_branch_workload("BR-001"))
    rebalance = json.loads(await mcp_server.propose_staffing_adjustment("BR-002", "BR-001", 2))
    affinity = json.loads(await mcp_server.get_customer_product_affinity("CUST-001"))
    assert workload["branch_id"] == "BR-001"
    assert rebalance["status"] == "proposed"
    assert affinity["products"]

    report = json.loads(await mcp_server.trigger_eod_reconciliation(date.today().isoformat()))
    mismatches = json.loads(await mcp_server.get_reconciliation_mismatches(report["report_id"]))
    correction = json.loads(await mcp_server.resolve_ledger_entry("TXN-1001", 10.5, "integration correction"))
    assert mismatches["report_id"] == report["report_id"]
    assert correction["status"] == "recorded"


@pytest.mark.asyncio
async def test_stdio_protocol_registers_modes_and_executes_cross_service_calls(live_backend: RunningBackend):
    expected_by_mode = {
        "front": {"get_transaction", "run_fraud_and_compliance_workflow", "update_transaction_status"},
        "mid": {"get_branch_workload", "propose_staffing_adjustment", "get_customer_product_affinity"},
        "back": {"trigger_eod_reconciliation", "get_reconciliation_mismatches", "resolve_ledger_entry"},
    }

    for mode, expected in expected_by_mode.items():
        env = os.environ.copy()
        env.update({"BANKING_BACKEND_URL": live_backend.base_url, "MCP_MODE": mode})
        params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == expected

                if mode == "front":
                    result = await session.call_tool("get_transaction", {"transaction_id": "TXN-1002"})
                    assert "TXN-1002" in result.content[0].text
                elif mode == "mid":
                    result = await session.call_tool("get_branch_workload", {"branch_id": "BR-002"})
                    assert "BR-002" in result.content[0].text
                else:
                    result = await session.call_tool("trigger_eod_reconciliation", {"date": date.today().isoformat()})
                    assert "report_id" in result.content[0].text
