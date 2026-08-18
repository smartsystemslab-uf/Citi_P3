# Citi Banking MCP Server

This folder contains the MCP server for the Citi Banking P3 project.

The MCP server exposes banking backend functions as callable tools for AI agents. It acts as the bridge between the ADK banking agents and the backend banking API.

## What This Module Does

This module registers MCP tools for different banking workflow domains:

- Front Office
- Mid Office
- Back Office

Agents can call these tools to retrieve transaction data, run fraud and compliance checks, update transaction status, query branch workload, trigger end-of-day reconciliation, and resolve ledger mismatches.

## Important Note

This is not a standalone web application.

It runs as an MCP server through standard input/output:

```bash
python mcp_server.py
```

In normal usage, this server should be launched by an MCP-compatible client or by the ADK agent system.

## Dependencies

Install the required dependencies first:

```bash
pip install -r requirements.txt
```

The main dependencies include:

- `mcp`
- `httpx`
- `python-dotenv`

## Environment Variables

This module uses environment variables to control backend connection and tool registration.

### `BANKING_BACKEND_URL`

This variable defines the backend banking API base URL.

Default value:

```bash
http://localhost:8000
```

Example:

```bash
BANKING_BACKEND_URL=http://localhost:8000
```

### `MCP_MODE`

This variable controls which group of MCP tools will be registered.

Available values:

```bash
front
mid
back
all
```

Default value:

```bash
all
```

Example:

```bash
MCP_MODE=front
```

## Available Tool Groups

### Front Office Tools

These tools are used for customer-facing transaction monitoring, fraud analysis, and compliance workflows.

Typical tools include:

- `get_transaction`
- `run_fraud_and_compliance_workflow`
- `update_transaction_status`

### Mid Office Tools

These tools are used for internal operations, workload analysis, and customer product recommendation.

Typical tools include:

- `get_branch_workload`
- `propose_staffing_adjustment`
- `get_customer_product_affinity`

### Back Office Tools

These tools are used for end-of-day processing, reconciliation, and ledger correction.

Typical tools include:

- `trigger_eod_reconciliation`
- `get_reconciliation_mismatches`
- `resolve_ledger_entry`

## How to Run

Before running this MCP server, make sure the backend banking API is already running.

Then start the MCP server:

```bash
python mcp_server.py
```

By default, the server runs with stdio transport:

```python
mcp.run(transport="stdio")
```

This means the server communicates with an MCP client through standard input and output.

## Recommended Usage Order

```text
Start backend banking API
        ↓
Start MCP server
        ↓
Connect ADK agents to MCP server
        ↓
Agents call backend tools through MCP
```

## Notes for Users

- The backend API must be running before the MCP tools can work.
- This server only exposes tools; it does not store banking data by itself.
- Tool results come from the backend banking API.
- Use `MCP_MODE` if you only want to expose tools for a specific workflow domain.
- Use `MCP_MODE=all` if you want to expose all available tools.
- Detailed integration with the ADK agent system should be configured from the agent-side MCP client settings.

## Folder Role in the Full Project

In the full Citi Banking P3 project, this folder sits between the agent system and the backend service.

```text
ADK Banking Agents
        ↓
Citi Banking MCP Server
        ↓
Backend Banking API
        ↓
Database / Banking Data
```

The main purpose of this module is to make backend banking functions safely and consistently available to AI agents as structured tools.
