from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bank_adk.app import create_app

from conftest import MCP_SERVER, ROOT, RunningBackend


@pytest.mark.asyncio
async def test_adk_discovers_and_bridges_live_mcp_tools(live_backend: RunningBackend):
    env = os.environ.copy()
    env.update({"BANKING_BACKEND_URL": live_backend.base_url, "MCP_MODE": "all"})
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            names = {tool.name for tool in response.tools}
            assert names == {
                "get_transaction",
                "run_fraud_and_compliance_workflow",
                "update_transaction_status",
                "get_branch_workload",
                "propose_staffing_adjustment",
                "get_customer_product_affinity",
                "trigger_eod_reconciliation",
                "get_reconciliation_mismatches",
                "resolve_ledger_entry",
            }

            result = await session.call_tool("get_transaction", {"transaction_id": "TXN-1001"})
            assert result.isError is False
            assert "TXN-1001" in result.content[0].text

            app = create_app(mcp_session=session, mcp_tools=response.tools)
            root_tool_names = {getattr(tool, "__name__", "") for tool in app.root_agent.tools}
            assert root_tool_names == names


def test_backend_connection_and_cli_entrypoints_start_cleanly(tmp_path, live_backend: RunningBackend):
    env = os.environ.copy()
    env.update(
        {
            "BANKING_BACKEND_BASE_URL": live_backend.base_url,
            "BANKING_BACKEND_URL": live_backend.base_url,
            "BANKING_ADK_RUNTIME_DIR": str(tmp_path / "runtime"),
            "UF_NAVIGATOR_API_KEY": "integration-test-key-not-used",
        }
    )
    connection = subprocess.run(
        [sys.executable, "test_backend_connection.py"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert connection.returncode == 0, connection.stderr
    assert "BANKING BACKEND HEALTH" in connection.stdout
    assert '"status": "ok"' in connection.stdout
    assert "EVALUATE HIGH-RISK TRANSFER" in connection.stdout

    for script in ("run_cli.py", "run_scenarios.py", "run_log_observability_study.py"):
        completed = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, f"{script}: {completed.stderr}"
        assert "usage:" in completed.stdout.lower()
