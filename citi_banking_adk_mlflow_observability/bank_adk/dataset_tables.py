from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import mlflow

from .evaluation import evaluate_compact_trace
from .trace_features import extract_trace_features


def _json_cell(value: Any) -> str:
    """Return a CSV-safe string for nested values while keeping scalars readable."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float | str):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return str(value)


def _scalar_or_json(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return _json_cell(value)


def _manifest_case_map(manifest: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    cases = (manifest or {}).get("cases", [])
    out: dict[str, dict[str, Any]] = {}
    if isinstance(cases, list):
        for item in cases:
            if isinstance(item, dict) and item.get("case_id"):
                out[str(item["case_id"])] = dict(item)
    return out


def _ordered_fieldnames(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    preferred = [
        "case_id",
        "base_scenario",
        "split",
        "failure_type",
        "repeat_index",
        "repeat_id",
        "expected_anomaly",
        "observed_anomaly",
        "execution_failed",
        "overall_pass",
        "correctness_score",
        "error_type",
        "error_message",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for key in preferred:
        for row in rows:
            if key in row and key not in seen:
                ordered.append(key)
                seen.add(key)
                break
    for row in rows:
        for key in row.keys():
            if key not in seen:
                ordered.append(key)
                seen.add(key)
    return ordered


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    fieldnames = _ordered_fieldnames(rows)
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _json_cell(row.get(key)) for key in fieldnames})
    return output.getvalue()


def _feature_row(
    *,
    case: Mapping[str, Any],
    compact_result: Any,
    message: str,
    raw_event_count: Optional[int] = None,
) -> dict[str, Any]:
    case_id = str(case.get("case_id", "unknown_case"))
    events = compact_result if isinstance(compact_result, list) else []
    features = extract_trace_features(
        scenario_name=case_id,
        message=message,
        compact_result=events,
        raw_event_count=raw_event_count if raw_event_count is not None else len(events),
        trace_id=None,
    )
    row: dict[str, Any] = {
        "case_id": case_id,
        "base_scenario": case.get("base_scenario") or features.get("base_scenario"),
        "split": case.get("split") or features.get("split"),
        "failure_type": case.get("failure_type") or features.get("failure_type"),
        "repeat_index": case.get("repeat_index"),
        "repeat_id": features.get("repeat_id"),
        "expected_anomaly": int(bool(case.get("expected_anomaly", False))),
        "execution_failed": 0,
        **{key: _scalar_or_json(value) for key, value in features.items()},
    }
    return row


def _evaluation_row(*, case: Mapping[str, Any], compact_result: Any, message: str) -> dict[str, Any]:
    case_id = str(case.get("case_id", "unknown_case"))
    evaluation = evaluate_compact_trace(case_id, compact_result if isinstance(compact_result, list) else [], message=message)
    metrics = evaluation.get("metrics", {}) if isinstance(evaluation.get("metrics"), dict) else {}
    checks = evaluation.get("checks", {}) if isinstance(evaluation.get("checks"), dict) else {}
    observed = evaluation.get("observed", {}) if isinstance(evaluation.get("observed"), dict) else {}

    row: dict[str, Any] = {
        "case_id": case_id,
        "base_scenario": case.get("base_scenario") or evaluation.get("base_scenario"),
        "split": case.get("split"),
        "failure_type": case.get("failure_type"),
        "repeat_index": case.get("repeat_index"),
        "expected_anomaly": int(bool(case.get("expected_anomaly", False))),
        "execution_failed": 0,
        "overall_pass": int(bool(evaluation.get("overall_pass", False))),
        "correctness_score": evaluation.get("correctness_score", 0.0),
        "observed_anomaly": int(not bool(evaluation.get("overall_pass", False))),
        "findings": evaluation.get("findings", []),
    }
    for key, value in metrics.items():
        row[f"metric_{key}"] = value
    for key, value in checks.items():
        row[f"check_{key}"] = int(bool(value))
    for key, value in observed.items():
        row[f"observed_{key}"] = value
    return row


def _label_row(*, case: Mapping[str, Any], evaluation_row: Mapping[str, Any] | None = None, failed: bool = False, error: Mapping[str, Any] | None = None) -> dict[str, Any]:
    overall_pass = bool(evaluation_row.get("overall_pass")) if evaluation_row else False
    observed_anomaly = int(failed or not overall_pass)
    return {
        "case_id": case.get("case_id") or (error or {}).get("case_id"),
        "base_scenario": case.get("base_scenario") or (error or {}).get("base_scenario"),
        "split": case.get("split") or (error or {}).get("split"),
        "failure_type": case.get("failure_type") or (error or {}).get("failure_type"),
        "repeat_index": case.get("repeat_index"),
        "expected_anomaly": int(bool(case.get("expected_anomaly", False) or ((error or {}).get("split") == "failure"))),
        "observed_anomaly": observed_anomaly,
        "execution_failed": int(failed),
        "overall_pass": int(overall_pass),
        "correctness_score": evaluation_row.get("correctness_score", 0.0) if evaluation_row else 0.0,
        "error_type": (error or {}).get("error_type", ""),
    }


def _failed_feature_row(case: Mapping[str, Any], error: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id") or error.get("case_id"),
        "base_scenario": case.get("base_scenario") or error.get("base_scenario"),
        "split": case.get("split") or error.get("split"),
        "failure_type": case.get("failure_type") or error.get("failure_type"),
        "repeat_index": case.get("repeat_index"),
        "expected_anomaly": int(bool(case.get("expected_anomaly", False) or error.get("split") == "failure")),
        "observed_anomaly": 1,
        "execution_failed": 1,
        "error_type": error.get("error_type", ""),
        "error_message": error.get("error_message", ""),
        "message_char_count": len(str(case.get("message", "") or error.get("message", ""))),
        "raw_event_count": 0,
        "compact_event_count": 0,
        "transfer_count": 0,
        "tool_call_count": 0,
        "tool_result_count": 0,
        "final_result_count": 0,
        "unique_agent_count": 0,
        "unique_tool_count": 0,
        "unique_transfer_target_count": 0,
        "max_repeated_tool_call_count": 0,
        "max_repeated_agent_event_count": 0,
        "malformed_json_argument_count": 1 if "json" in str(error.get("error_type", "")).lower() or "json" in str(error.get("error_message", "")).lower() else 0,
        "final_response_char_count": 0,
        "tool_argument_char_count": 0,
        "has_final_result": 0,
        "has_tool_call": 0,
        "has_transfer": 0,
        "tools_seen": "[]",
        "agents_seen": "[]",
        "transfer_targets_seen": "[]",
        "tool_call_counts": "{}",
    }


def build_dataset_tables(
    *,
    manifest: Mapping[str, Any],
    completed_case_ids: list[str],
    completed_results: list[Any],
    failed_cases: list[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build consolidated row-per-case tables from trace/eval JSON payloads.

    These CSV-ready tables keep the JSON artifacts for dashboard/RCA inspection,
    while providing a flat dataset for anomaly detection and model training.
    """
    case_map = _manifest_case_map(manifest)

    feature_rows: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []

    for case_id, result in zip(completed_case_ids, completed_results):
        case = case_map.get(case_id, {"case_id": case_id})
        message = str(case.get("message", ""))
        feature = _feature_row(case=case, compact_result=result, message=message)
        evaluation = _evaluation_row(case=case, compact_result=result, message=message)
        label = _label_row(case=case, evaluation_row=evaluation, failed=False)
        # Mirror the observed outcome label in the feature table so feature-only workflows work too.
        feature["observed_anomaly"] = label["observed_anomaly"]
        feature["overall_pass"] = label["overall_pass"]
        feature["correctness_score"] = label["correctness_score"]
        feature_rows.append(feature)
        evaluation_rows.append(evaluation)
        label_rows.append(label)

    for error in failed_cases:
        case_id = str(error.get("case_id", "unknown_failed_case"))
        case = case_map.get(case_id, {"case_id": case_id, **dict(error)})
        feature = _failed_feature_row(case, error)
        label = _label_row(case=case, failed=True, error=error)
        error_row = {
            "case_id": case_id,
            "base_scenario": case.get("base_scenario") or error.get("base_scenario"),
            "split": case.get("split") or error.get("split"),
            "failure_type": case.get("failure_type") or error.get("failure_type"),
            "repeat_index": case.get("repeat_index"),
            "expected_anomaly": label["expected_anomaly"],
            "observed_anomaly": 1,
            "execution_failed": 1,
            "error_type": error.get("error_type", ""),
            "error_message": error.get("error_message", ""),
            "logged_artifact": int(bool(error.get("logged_artifact"))),
        }
        feature_rows.append(feature)
        label_rows.append(label)
        error_rows.append(error_row)

    return {
        "trace_features": feature_rows,
        "trace_labels": label_rows,
        "trace_evaluations": evaluation_rows,
        "trace_errors": error_rows,
    }


def summarize_tables(tables: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    labels = tables.get("trace_labels", [])
    total = len(labels)
    expected_anomalies = sum(int(row.get("expected_anomaly", 0)) for row in labels)
    observed_anomalies = sum(int(row.get("observed_anomaly", 0)) for row in labels)
    failed = sum(int(row.get("execution_failed", 0)) for row in labels)
    normal = total - expected_anomalies

    by_failure_type: dict[str, int] = {}
    by_scenario: dict[str, int] = {}
    for row in labels:
        failure_type = str(row.get("failure_type") or "unknown")
        scenario = str(row.get("base_scenario") or "unknown")
        by_failure_type[failure_type] = by_failure_type.get(failure_type, 0) + 1
        by_scenario[scenario] = by_scenario.get(scenario, 0) + 1

    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "metrics": {
            "case_count": total,
            "normal_case_count": normal,
            "expected_anomaly_count": expected_anomalies,
            "observed_anomaly_count": observed_anomalies,
            "execution_failed_count": failed,
            "expected_anomaly_rate": round(expected_anomalies / total, 4) if total else 0.0,
            "observed_anomaly_rate": round(observed_anomalies / total, 4) if total else 0.0,
            "execution_failure_rate": round(failed / total, 4) if total else 0.0,
        },
        "by_failure_type": by_failure_type,
        "by_base_scenario": by_scenario,
    }


def log_dataset_tables(
    *,
    manifest: Mapping[str, Any],
    completed_case_ids: list[str],
    completed_results: list[Any],
    failed_cases: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create and log CSV tables plus a JSON summary under artifacts/tables/."""
    tables = build_dataset_tables(
        manifest=manifest,
        completed_case_ids=completed_case_ids,
        completed_results=completed_results,
        failed_cases=failed_cases,
    )
    summary = summarize_tables(tables)

    for table_name, rows in tables.items():
        mlflow.log_text(_rows_to_csv(rows), f"artifacts/tables/{table_name}.csv")
        # Also log a small JSON preview for easier MLflow browsing.
        mlflow.log_dict({"rows": rows[:25], "row_count": len(rows)}, f"artifacts/tables/{table_name}_preview.json")

    mlflow.log_dict(summary, "artifacts/tables/dataset_tables_summary.json")
    for key, value in summary.get("metrics", {}).items():
        if isinstance(value, int | float):
            mlflow.log_metric(f"dataset.tables.{key}", float(value))

    return summary
