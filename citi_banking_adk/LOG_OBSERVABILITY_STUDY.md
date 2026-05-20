# Log Observability Study for Citi Banking ADK

## 1. Study goal

This study measures the runtime logging footprint of the Citi Banking ADK multi-agent banking system and evaluates a selective event-logging algorithm for monitoring and audit.

The goal is not to keep every raw model output forever. The goal is to preserve high-value events that explain workflow behavior, risk decisions, state changes, tool/API calls, validation failures, reconciliation mismatches, and manual review triggers.

## 2. Why this study matters

The system contains routed front-office, mid-office, and back-office workflows. The architecture already includes natural control points: suspicion check, confidence threshold, validation step, ledger update, reconciliation, report writing, manual review, and exception handling.

These control points are exactly the places where an observability system should capture durable monitoring events.

## 3. Event schema

Each runtime event is normalized into this schema:

```json
{
  "trace_id": "stable workflow run id",
  "scenario_name": "front_office_monitoring",
  "run_index": 1,
  "sequence": 7,
  "timestamp_utc": "2026-05-06T00:00:00Z",
  "workflow": "front_office | mid_office | back_office",
  "agent": "transaction_monitoring_agent",
  "event_type": "tool_call | tool_result | result | decision | manual_review | exception | model_text",
  "tool_name": "get_transaction_context",
  "state_key": "fo_monitoring_report",
  "payload_summary": "short safe summary",
  "raw_size_bytes": 1024,
  "risk_score": 0.0,
  "state_change_score": 0.0,
  "decision_impact_score": 0.0,
  "anomaly_score": 0.0,
  "confidence_risk_score": 0.0,
  "cost_latency_score": 0.0,
  "importance_score": 0.0,
  "selected_for_monitoring": true,
  "keep_reason": "risk,decision_point"
}
```

## 4. Importance scoring algorithm

Each event receives an importance score:

```text
Importance(e) =
0.30 * RiskScore
+ 0.20 * StateChange
+ 0.15 * DecisionImpact
+ 0.15 * AnomalyScore
+ 0.10 * ConfidenceRisk
+ 0.10 * CostLatencyImpact
```

### Score meanings

| Score | Meaning |
|---|---|
| RiskScore | Fraud, compliance, AML/KYC, suspicious behavior, manual review, high-risk transaction, reconciliation mismatch |
| StateChange | Transfer execution, ledger update, report creation, exception creation, manual review case creation |
| DecisionImpact | Approval, rejection, escalation, validation readiness, reconciliation match/mismatch, route change |
| AnomalyScore | Error, exception, mismatch, timeout, retry loop, validation failure |
| ConfidenceRisk | Low confidence, near-threshold decision, uncertain model output |
| CostLatencyImpact | Long latency, high token usage, expensive repeated calls |

## 5. Selection policy

An event is selected for durable monitoring if:

```text
error or exception event
OR state-changing banking operation
OR risk_score >= 0.8
OR anomaly_score >= 0.8
OR decision-impact event with enough importance
OR importance_score >= threshold
```

Default threshold: `0.45`.

This means the monitor keeps critical events, but drops low-value conversational or debug text.

## 6. Scenarios measured

The script reuses the current scenarios in `run_scenarios.py`:

| Scenario | Purpose |
|---|---|
| front_office_monitoring | Transaction monitoring, fraud, compliance, manual review decision |
| front_office_chatbot | Customer FAQ / knowledge-base path |
| front_office_transaction_execution | Customer transaction execution through banking API/tool |
| mid_office_planning | Operations analysis, forecasting, scheduling, validation, support guidance |
| mid_office_rep_assist | Internal representative assistant with financial/product/loan/risk checks |
| back_office_clean | EOD processing, ledger update, clean reconciliation, report writing |
| back_office_mismatch | EOD processing, ledger update, reconciliation mismatch, exception handling |

## 7. Metrics produced

The study outputs the following metrics per run and per scenario:

```text
total_events
selected_events
dropped_events
selection_rate
raw_size_bytes
selected_size_bytes
storage_reduction_rate
compression_ratio
critical_events
critical_events_selected
critical_event_recall
avg_importance_score
p95_importance_score
event_type_counts
selected_event_type_counts
agent_counts
workflow_counts
keep_reason_counts
runtime_seconds
```

The most important evaluation metrics are:

1. **Storage reduction rate**: how much log volume is removed.
2. **Critical event recall**: whether important risk/state/anomaly events are retained.
3. **Selection rate**: how aggressive the filter is.
4. **Per-agent event counts**: which agent produces most monitoring data.
5. **Per-scenario footprint**: which workflow is most expensive to observe.

## 8. How to run

From the project root:

```bash
python run_log_observability_study.py --scenario all --repeats 3 --threshold 0.45 --output-dir observability_runs/latest
```

To run only one scenario:

```bash
python run_log_observability_study.py --scenario back_office_mismatch --repeats 5
```

To log artifacts to MLflow:

```bash
python run_log_observability_study.py --scenario all --repeats 3 --mlflow
```

Optional environment variables:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000
MLFLOW_EXPERIMENT_NAME=citi_p3_log_observability_study
```

## 9. Output files

The script writes:

```text
observability_runs/latest/
├─ aggregate_summary.json
├─ run_summaries.json
├─ all_events.jsonl
├─ selected_events.jsonl
├─ all_events.csv
└─ selected_events.csv
```

Recommended use:

- Use `aggregate_summary.json` for the report.
- Use `selected_events.jsonl` as the monitoring event stream.
- Use `all_events.csv` to debug the scoring algorithm.
- Use `selected_events.csv` to inspect what the monitor actually keeps.

## 10. Suggested report wording

The log observability study instruments each multi-agent workflow and converts ADK runtime traces into structured monitoring events. We measure the raw event volume generated by each banking scenario, then apply a selective event-retention algorithm. The algorithm prioritizes events involving risk, decision impact, state changes, anomalies, confidence issues, and latency/cost pressure. This allows the monitoring system to retain audit-relevant workflow behavior while reducing low-value debug text.

## 11. Expected conclusion

The expected result is that normal chatbot and low-risk workflow paths produce many low-value intermediate events, while transaction risk review and EOD mismatch paths produce fewer but more important events. The selected monitoring stream should retain nearly all manual review, fraud/compliance, ledger update, validation, reconciliation, and exception events while significantly reducing storage compared with raw trace retention.


# Log Observability Study Results

This section summarizes the current log observability experiment for the multi-agent banking workflow. The experiment ran 7 scenarios, 3 runs per scenario, for a total of 21 workflow runs.

## Overall Results

| Metric | Value |
| --- | --- |
| Total runs | 21 |
| Average total events / run | 29.71 |
| Average selected events / run | 9.57 |
| Average dropped events / run | 20.14 |
| Average raw log size / run | 20,111.95 bytes |
| Average selected log size / run | 7,423.24 bytes |
| Average storage reduction | 62.92% |
| Average compression ratio | 37.08% |
| Average selection rate | 30.60% |
| Average critical event recall | 64.27% |
| Average runtime / run | 19.00s |

## Scenario-Level Results

| Scenario | Runs | Avg Events | Selected | Dropped | Raw Size Bytes | Selected Size Bytes | Storage Reduction | Selection Rate | Critical Recall | Runtime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `front_office_monitoring` | 3 | 34.00 | 17.33 | 16.67 | 20,446.00 | 13,719.33 | 32.88% | 51.04% | 89.65% | 26.83s |
| `front_office_chatbot` | 3 | 18.00 | 1.00 | 17.00 | 7,064.33 | 240.00 | 96.52% | 5.59% | 23.33% | 6.21s |
| `front_office_transaction_execution` | 3 | 19.00 | 5.67 | 13.33 | 7,646.00 | 2,188.67 | 69.83% | 29.22% | 71.85% | 9.40s |
| `mid_office_planning` | 3 | 34.00 | 5.00 | 29.00 | 37,483.33 | 7,780.00 | 79.39% | 14.73% | 51.85% | 33.10s |
| `mid_office_rep_assist` | 3 | 35.00 | 7.00 | 28.00 | 33,452.33 | 10,380.33 | 68.88% | 20.00% | 63.44% | 17.12s |
| `back_office_clean` | 3 | 38.00 | 7.67 | 30.33 | 19,656.67 | 4,121.00 | 79.38% | 20.17% | 60.47% | 21.88s |
| `back_office_mismatch` | 3 | 30.00 | 23.33 | 6.67 | 15,035.00 | 13,533.33 | 13.58% | 73.43% | 89.29% | 18.47s |

## Event-Type Selection Behavior

| Event Type | Total Events | Selected Events | Selection Rate | Raw Size Bytes | Raw Size Share |
| --- | --- | --- | --- | --- | --- |
| `model_text` | 280 | 73 | 26.07% | 259,887 | 61.53% |
| `result` | 50 | 28 | 56.00% | 47,421 | 11.23% |
| `state_change` | 36 | 36 | 100.00% | 43,866 | 10.39% |
| `tool_result` | 99 | 30 | 30.30% | 39,005 | 9.24% |
| `transfer` | 42 | 0 | 0.00% | 14,852 | 3.52% |
| `tool_call` | 108 | 25 | 23.15% | 13,342 | 3.16% |
| `ledger_update` | 5 | 5 | 100.00% | 3,106 | 0.74% |
| `manual_review` | 3 | 3 | 100.00% | 646 | 0.15% |
| `exception` | 1 | 1 | 100.00% | 226 | 0.05% |

## Keep Reason Counts

| Keep Reason | Count |
| --- | --- |
| `anomaly` | 108 |
| `state_change` | 66 |
| `risk` | 63 |
| `importance_threshold` | 42 |
| `decision_point` | 20 |
| `critical_event_type` | 4 |

## Storage Scaling Estimate

| Workflows / Day | Raw Logs / Day | Selected Logs / Day |
| --- | --- | --- |
| 1,000 | 20.11 MB | 7.42 MB |
| 10,000 | 201.12 MB | 74.23 MB |
| 100,000 | 2.01 GB | 742.32 MB |
| 1,000,000 | 20.11 GB | 7.42 GB |

## Short Analysis

The selective observability layer reduced the average log size from **20.11 KB** to **7.42 KB** per workflow run, giving an average storage reduction of **62.92%**.

The selector shows risk-aware behavior. Low-risk chatbot workflows are compressed heavily: `front_office_chatbot` keeps only **5.59%** of events and reduces storage by **96.52%**. High-risk or abnormal workflows keep more trace detail: `back_office_mismatch` keeps **73.43%** of events and reaches **89.29%** critical event recall.

The most important business events are preserved. `state_change`, `ledger_update`, `manual_review`, and `exception` all have a **100% selection rate**.

The largest log source is `model_text`, which contributes **61.53%** of raw log size. This shows that future production logging should store structured summaries instead of full model text for routine cases.

Current `critical_event_recall` should be treated as conservative because some normal `tool_result` events are counted as critical. The metric can be improved by redefining critical events around business impact, such as ledger updates, state changes, fraud/compliance flags, manual review, validation failure, reconciliation mismatch, and exceptions.

