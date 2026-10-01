# Citi Banking ADK

Google ADK implementation of the uploaded banking architecture document.

## Included workflows

- Root user proxy for front office, mid office, back office, and AML office routing
- Front office transaction monitoring, fraud, compliance, decision, manual review
- Front office customer chatbot, knowledge retrieval, service handling, transaction execution
- Mid office operations analysis, forecasting, scheduling, validation, support guidance
- Mid office representative assist with financial data, product recommendation, loan guidance, and risk compliance
- Back office validation checkpoint, EOD processing, ledger update, reconciliation, report writing, exception handling
- AML office investigation and Suspicious Activity Report (SAR) drafting

Every business-role agent is an ADK `LlmAgent` wired to your supplied model target through `LiteLlm`. The tool layer is used only for grounded systems such as the local banking API, policy store, schedule persistence, and ledger/report records.

## AML office

The AML office investigates a customer on request and, when the findings are serious enough, drafts a formal Suspicious Activity Report — without a human needing to decide whether to escalate.

- **`aml_investigator`** analyzes the requested customer using real detection tools against the SQLite-backed banking data: `detect_structuring` (sliding-window detection of deposits split to stay under the $10,000 reporting threshold), `find_mule_rings_for_customer` (graph-cycle detection over a customer's transaction flows, via `networkx`), `check_threshold_anomaly`, `verify_identity_document` (fuzzy name/DOB/ID matching), plus `get_customer_context` and `list_recent_transactions`. It returns a risk score (0-100) and structured case facts.
- **`sar_drafting_agent`** only runs when the investigation is serious enough — `risk_score >= 50` or the investigation status contains "suspicious" — and drafts the SAR narrative from the investigator's findings. It has no tools of its own; it can only work from facts the investigator already established, and its instruction explicitly forbids inventing missing case facts.
- A real structuring finding also creates a persisted `aml_alerts` record and automatically opens a manual review case (`repository.py`'s `create_aml_alert` / `submit_manual_review`) — not just a response, an actual compliance artifact.

This whole flow runs under the same Zero Trust enforcement as every other office — every agent handoff and every tool call is individually authorized by the Backplane's PDP before it's allowed to proceed (see `Citi_P3_Zero_Trust_Backplane/`).

Example:

```bash
python run_cli.py --zero-trust --message "investigate CUST-001 for potential structuring"
```

## Structure

```text
citi_banking_adk/
├─ bank_adk/
│  ├─ agents/
│  ├─ tools/
│  ├─ infra/
│  ├─ data/
│  ├─ settings.py
│  ├─ llm.py
│  ├─ app.py
│  └─ runner.py
├─ run_cli.py
├─ run_scenarios.py
├─ requirements.txt
└─ .env.example
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with your real key:

```bash
UF_NAVIGATOR_API_KEY=your_real_key
UF_NAVIGATOR_BASE_URL=https://api.ai.it.ufl.edu
UF_NAVIGATOR_MODEL=gpt-oss-20b
```

The code normalizes the base URL to `.../v1` for the OpenAI-compatible endpoint.

## Run

Single message:

```bash
python run_cli.py --message "Review transaction TXN-1001 for customer CUST-001 and run the front-office transaction workflow."
```

All scenarios:

```bash
python run_scenarios.py --scenario all
```

## Sample seeded ids

- Customers: `CUST-001`, `CUST-002`
- Accounts: `CHK-001`, `SVG-001`, `CHK-002`, `EXT-998`
- Transactions: `TXN-1001`, `TXN-1002`
- EOD batches: `EOD-2026-04-21-CLEAN`, `EOD-2026-04-21-MISMATCH`
