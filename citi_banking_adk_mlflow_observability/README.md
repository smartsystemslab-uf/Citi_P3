# Citi Banking ADK Observability Testbed

This repository contains a Google ADK based multi-agent banking testbed with an MLflow and OpenTelemetry observability layer. The project models front-office, mid-office, and back-office banking workflows, captures ADK execution traces, evaluates whether each workflow followed the expected path, and builds CSV datasets that can later be used for anomaly detection or system-behavior analysis.

The implementation is intentionally a lab/demo system. It uses synthetic customer, transaction, operations, product, policy, and end-of-day banking data. It is not connected to real Citi systems or production banking infrastructure.

## What this project does

At a high level, the system has three layers:

1. **Banking multi-agent workflows**: Google ADK agents route requests through front-office, mid-office, and back-office workflows.
2. **Grounded tool and data layer**: agents call local tools backed by SQLite and JSON files so the workflow is grounded in repeatable synthetic data.
3. **Observability and dataset layer**: OpenTelemetry exports ADK traces to MLflow, and the code logs trace artifacts, extracted features, deterministic evaluation results, labels, and error records.

This makes the repository useful for demonstrating how an agentic banking system can be instrumented, evaluated, and converted into a dataset for later monitoring or anomaly-detection experiments.

## Main workflows

### 1. Front office

The front-office side handles customer-facing and transaction-facing requests.

- **Transaction monitoring workflow**: reviews a transaction, gathers customer context, checks recent activity, runs fraud and compliance reasoning, and decides whether the transaction should be approved or sent to manual review.
- **Customer chatbot workflow**: handles informational customer-service questions using the local FAQ and support data.
- **Transaction execution workflow**: handles service/action requests such as transferring money between accounts in the synthetic banking database.

Key examples:

- `front_office_monitoring`
- `front_office_chatbot`
- `front_office_transaction_execution`

### 2. Mid office

The mid-office side models internal operational planning and representative assistance.

- **Planning workflow**: analyzes operations data, forecasts support workload, proposes staffing, validates the schedule, and provides support guidance.
- **Representative assist workflow**: gathers customer financial context, recommends products, provides loan guidance, and performs policy/risk checks for a customer representative.

Key examples:

- `mid_office_planning`
- `mid_office_rep_assist`

### 3. Back office

The back-office side models end-of-day banking operations.

- **Clean EOD workflow**: validates readiness, runs processing, applies ledger updates, performs reconciliation, and generates a report.
- **Mismatch EOD workflow**: follows the same processing path but creates an exception case when reconciliation does not match.

Key examples:

- `back_office_clean`
- `back_office_mismatch`

## Architecture overview

```text
User request
   |
   v
Root User Proxy Agent
   |
   +--> Front Office Router
   |       +--> Transaction monitoring, fraud, compliance, decision, manual review
   |       +--> Customer chatbot, FAQ/search, service handling, transaction execution
   |
   +--> Mid Office Router
   |       +--> Operations analysis, forecasting, scheduling, validation, support guidance
   |       +--> Representative assist, financial data, recommendations, loans, risk checks
   |
   +--> Back Office Router
           +--> EOD validation, processing, ledger update, reconciliation, reporting, exceptions

Grounded tools and data
   |
   +--> SQLite runtime database for customers, accounts, transactions, reviews, schedules, batches, reports
   +--> JSON knowledge stores for FAQs, policies, products, playbooks, and operations data

Observability layer
   |
   +--> ADK OpenTelemetry traces
   +--> OTLP export to MLflow
   +--> MLflow run metrics, tags, JSON artifacts, CSV tables, and trace IDs
```

## Repository structure

```text
citi_banking_adk_mlflow_ready/
├─ bank_adk/
│  ├─ agents/                 # ADK agent definitions and workflow orchestration
│  ├─ tools/                  # Grounded banking, policy, knowledge, and operations tools
│  ├─ infra/                  # SQLite repository and bootstrap logic
│  ├─ data/                   # Synthetic JSON data stores
│  ├─ app.py                  # ADK app construction
│  ├─ callbacks.py            # ADK callback helpers
│  ├─ dataset_tables.py       # Consolidated CSV table generation
│  ├─ evaluation.py           # Rule-based workflow evaluator
│  ├─ failure_scenarios.py    # Normal and failure-injected dataset cases
│  ├─ llm.py                  # LiteLLM model wrapper
│  ├─ observability.py        # MLflow and OpenTelemetry setup/logging
│  ├─ runner.py               # ADK runner and compact trace conversion
│  ├─ scenarios.py            # Demo scenario prompts
│  ├─ settings.py             # Environment-driven configuration
│  └─ trace_features.py       # Trace feature extraction
├─ run_cli.py                 # Run one custom request
├─ run_scenarios.py           # Run predefined demo scenarios
├─ run_trace_dataset.py       # Generate trace dataset and log artifacts to MLflow
├─ requirements.txt           # Python dependencies
├─ pyproject.toml             # Package metadata
├─ .env.example               # Environment variable template
└─ tests/                     # Basic tests
```

## Observability design

The observability layer is implemented in `bank_adk/observability.py`.

When tracing is enabled, the code:

1. Configures MLflow tracking using `MLFLOW_TRACKING_URI` and `MLFLOW_EXPERIMENT_NAME`.
2. Creates an OpenTelemetry tracer provider with service metadata such as app name, version, and environment.
3. Exports ADK OpenTelemetry spans to MLflow through the OTLP endpoint.
4. Wraps batch runs in an MLflow run so scenario-level metrics and artifacts are grouped together.
5. Logs compact trace artifacts, extracted feature artifacts, evaluation artifacts, labels, error records, and dataset summaries.

The important point is that the ADK workflow owns the execution trace. The repository exports those traces to MLflow and also creates additional structured artifacts that make the traces easier to inspect and convert into a dataset.

## What gets logged to MLflow

For individual requests and scenario runs, the system logs:

- **Trace JSON**: compact event list showing agent transfers, tool calls, selected tool results, and final workflow results.
- **Feature JSON**: numerical and categorical features extracted from the trace.
- **Evaluation JSON**: deterministic checks that verify expected tools, transfers, result keys, decisions, statuses, and safety conditions.
- **Trace ID JSON**: the MLflow trace identifier when available.
- **Metrics**: event counts, tool-call counts, transfer counts, pass/fail metrics, correctness score, and dataset-level summaries.

For dataset generation, the system also logs consolidated CSV tables under `artifacts/tables/`:

- `trace_features.csv`: one row per case with extracted observability features.
- `trace_labels.csv`: expected and observed anomaly labels, failure type, pass/fail status, and execution-failure flags.
- `trace_evaluations.csv`: flattened evaluator checks and correctness metrics for completed cases.
- `trace_errors.csv`: error metadata for failed executions.
- `dataset_tables_summary.json`: dataset-level counts and rates.

These CSV tables are the analysis-ready output for later anomaly detection, plotting, thresholding, or ML experiments.

## Important feature categories

The trace feature extractor records features such as:

- number of raw ADK events and compact events
- number of agent transfers
- number of tool calls and tool results
- number of final result events
- unique agents, tools, and transfer targets seen
- repeated tool-call patterns
- repeated agent-event patterns
- malformed JSON-like tool arguments
- final response length
- tool argument size
- whether a final result exists
- whether a tool call exists
- whether an agent transfer exists
- case metadata such as base scenario, split, failure type, and repeat ID

These features are useful because abnormal agent behavior often shows up structurally before it is obvious from the final answer. For example, a tool omission, wrong route, repeated retries, missing final result, malformed argument, or unexpected transfer pattern can become a measurable signal.

## Rule-based evaluator

The evaluator in `bank_adk/evaluation.py` does not use an LLM judge. It uses deterministic checks so the results are repeatable.

For each known workflow, the evaluator checks items such as:

- expected tools were called
- expected agent transfers happened
- required final result keys were produced
- forbidden tools were not used
- expected decision or status was produced
- required terms appeared in the final result
- reconciliation mismatch amount remained consistent
- malformed JSON-like arguments were not present
- obvious sensitive-data patterns were not exposed in the final response

The output includes an `overall_pass` flag, `correctness_score`, individual checks, findings, and observed tools/transfers/result keys.

## Setup

### 1. Create and activate a virtual environment

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
python -m venv .venv
.\.venv\Scripts\activate.bat
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```bash
UF_NAVIGATOR_API_KEY=replace_me
UF_NAVIGATOR_BASE_URL=https://api.ai.it.ufl.edu
UF_NAVIGATOR_MODEL=gpt-oss-20b
BANKING_ADK_APP_NAME=citi_banking_adk
BANKING_ADK_APP_VERSION=0.1.0
BANKING_ADK_USER_ID=demo_user
BANKING_ADK_SESSION_ID=demo_session

MLFLOW_ENABLE_TRACING=true
MLFLOW_TRACKING_URI=http://127.0.0.1:5000
MLFLOW_EXPERIMENT_NAME=citi-banking-adk
MLFLOW_ENVIRONMENT=dev
MLFLOW_OTLP_ENDPOINT=
```

The model wrapper normalizes the base URL to an OpenAI-compatible `/v1` endpoint.

Do not commit your real `.env` file to GitHub.

## Running the project

### 1. Start MLflow

Run this from the repository root:

```bash
mlflow server --backend-store-uri sqlite:///mlflow.db --default-artifact-root ./mlruns --host 127.0.0.1 --port 5000
```

Then open:

```text
http://127.0.0.1:5000
```

### 2. Run a single request

```bash
python run_cli.py --message "Review transaction TXN-1001 for customer CUST-001 and run the front-office transaction workflow."
```

To print the full raw ADK debug trace instead of the compact trace:

```bash
python run_cli.py --message "Review transaction TXN-1001 for customer CUST-001 and run the front-office transaction workflow." --raw
```

### 3. Run all demo scenarios

```bash
python run_scenarios.py --scenario all
```

Run one scenario:

```bash
python run_scenarios.py --scenario front_office_monitoring
```

Available scenarios:

- `front_office_monitoring`
- `front_office_chatbot`
- `front_office_transaction_execution`
- `mid_office_planning`
- `mid_office_rep_assist`
- `back_office_clean`
- `back_office_mismatch`

### 4. Generate a trace dataset

Small smoke test:

```bash
python run_trace_dataset.py --normal-repeats 1 --failure-repeats 1 --max-cases 10
```

Default demo dataset:

```bash
python run_trace_dataset.py --scenario all --normal-repeats 5 --failure-repeats 2
```

The default dataset is built from:

```text
7 workflows x (5 normal baseline repeats + 6 injected failure types x 2 repeats) = 119 cases
```

The generated dataset contains both normal and failure-injected traces. Failure injection is used to create diverse behavior patterns for observability and anomaly-detection experiments. The current implementation does not yet perform root-cause analysis.

## Seeded demo IDs

The local SQLite database is created automatically under `bank_adk/.runtime/` when the ADK app starts.

Seeded IDs include:

- Customers: `CUST-001`, `CUST-002`
- Accounts: `CHK-001`, `SVG-001`, `CHK-002`, `EXT-998`
- Transactions: `TXN-1001`, `TXN-1002`
- EOD batches: `EOD-2026-04-21-CLEAN`, `EOD-2026-04-21-MISMATCH`

## Running tests

```bash
pytest
```

The current test coverage is minimal and only checks basic data-path availability. The main verification logic is in the deterministic evaluator used during scenario and dataset runs.

## GitHub hygiene

The repository should track source code, config examples, and synthetic JSON seed data. It should not track local secrets or generated runtime artifacts.

Recommended ignored files:

```text
.env
.venv/
__pycache__/
*.pyc
mlflow.db
mlruns/
bank_adk/.runtime/
```

If any of these files were already added to Git, untrack them without deleting your local copies:

```bash
git rm -r --cached .venv __pycache__ mlruns bank_adk/.runtime || true
git rm --cached .env mlflow.db || true
```

On Windows PowerShell, run the same commands without `|| true`, or remove each path separately if Git reports that it is not tracked.

## Troubleshooting

### No traces appear in MLflow

Check that:

- MLflow server is running on `http://127.0.0.1:5000`
- `.env` has `MLFLOW_ENABLE_TRACING=true`
- `.env` has `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`
- the script was started after MLflow was already running
- the run completed far enough for `flush_observability()` to execute

### API/model errors

Check that:

- `UF_NAVIGATOR_API_KEY` is set correctly
- `UF_NAVIGATOR_BASE_URL` points to the OpenAI-compatible endpoint base
- `UF_NAVIGATOR_MODEL` is available to your account
- your network/VPN has access to the endpoint

### Port already in use

Use a different MLflow port:

```bash
mlflow server --backend-store-uri sqlite:///mlflow.db --default-artifact-root ./mlruns --host 127.0.0.1 --port 5001
```

Then update `.env`:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5001
```

## Current limitations

- The banking data is synthetic and intended for lab demonstration only.
- The evaluator is rule-based and checks expected workflow behavior, not business correctness in a production sense.
- Failure-injected cases are designed to create diverse traces, not to simulate every possible real banking failure.
- The current dataset pipeline supports feature extraction and labels, but root-cause explanation is not implemented yet.
- The local SQLite database is for repeatable demo execution, not production storage.
