# Integration Tests

The integration suites exercise the project through real local boundaries:

- FastAPI server process, HTTP requests, and temporary SQLite databases
- ADK grounded tools connected to the live banking backend
- ADK-to-MCP discovery and tool calls over the MCP stdio protocol
- Mid-office and back-office repository workflows with persistent state
- Trace case generation, deterministic evaluation, feature extraction, and CSV tables
- MLflow metrics and artifacts stored in a temporary SQLite-backed tracking store
- Executable database, smoke-test, backend-connection, and CLI entrypoints

Remote LLM calls and remote MLflow servers are intentionally excluded. The tests
are deterministic, do not require credentials, and do not create paid API usage.

## Install

Install the requirements for the component being tested, followed by the shared
test requirements:

```bash
python -m pip install -r requirements-test.txt
python -m pip install -r citi_banking_backend/requirements.txt
python -m pip install -r citi_banking_adk/requirements.txt
python -m pip install -r citi_banking_adk_mlflow_observability/requirements.txt
python -m pip install -r citi_banking_mcp_server/requirements.txt
```

## Run

Run each command from the repository root:

```bash
python -m pytest -q citi_banking_backend/integration_tests
python -m pytest -q citi_banking_adk/integration_tests
python -m pytest -q citi_banking_adk_mlflow_observability/integration_tests
python -m pytest -q citi_banking_mcp_server/integration_tests
```

The server-based suites bind only to a temporary `127.0.0.1` port and place all
databases, runtime state, logs, and MLflow artifacts in pytest temporary folders.
