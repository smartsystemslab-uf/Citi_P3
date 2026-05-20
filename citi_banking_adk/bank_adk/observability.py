"""Selective observability utilities for the Citi Banking ADK project.

This module converts ADK debug events into structured monitoring events,
scores their importance, selects high-value events, and summarizes the log
footprint of each workflow run.

The code is intentionally dependency-light. MLflow integration is optional and
is handled by run_log_observability_study.py when mlflow is installed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


CRITICAL_EVENT_TYPES = {
    "error",
    "exception",
    "manual_review",
    "state_change",
    "decision",
    "validation",
    "reconciliation",
    "ledger_update",
    "tool_result",
}

STATE_CHANGE_TOOLS = {
    "execute_transfer",
    "apply_ledger_updates",
    "store_report",
    "create_exception_case",
    "submit_manual_review",
    "save_schedule",
}

RISK_KEYWORDS = {
    "fraud",
    "suspicious",
    "compliance",
    "aml",
    "kyc",
    "sanction",
    "manual_review",
    "high risk",
    "reconciliation",
    "mismatch",
    "exception",
    "validation_fail",
    "low confidence",
}

DECISION_KEYS = {
    "decision",
    "manual_review_needed",
    "recommended_next_step",
    "recommendation",
    "matched",
    "ready",
    "approved",
    "rejected",
    "risk_level",
    "fraud_risk",
    "compliance_risk",
}

IMPORTANT_STATE_KEYS = {
    "fo_monitoring_report",
    "fo_fraud_report",
    "fo_compliance_report",
    "fo_decision_report",
    "fo_knowledge_answer",
    "fo_transaction_execution",
    "mid_office_planning_result",
    "mid_office_representative_result",
    "bo_validation_report",
    "bo_processing_report",
    "bo_ledger_update_report",
    "bo_reconciliation_report",
    "bo_report_output",
    "bo_exception_output",
}


def stable_trace_id(scenario_name: str, run_index: int, message: str) -> str:
    material = f"{scenario_name}|{run_index}|{message}".encode("utf-8")
    return hashlib.sha1(material).hexdigest()[:16]


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))


def json_size_bytes(obj: Any) -> int:
    return len(json_dumps(obj).encode("utf-8"))


def maybe_json(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    if not isinstance(value, str):
        return str(value)
    text = value.strip()
    if not text:
        return text
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        return value


def flatten_text(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json_dumps(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def clamp01(x: Any) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(0.0, min(1.0, v))


def confidence_to_risk(value: Any) -> float:
    """Convert confidence into risk: low confidence -> high risk."""
    if value is None:
        return 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"low", "weak", "uncertain"}:
            return 0.9
        if text in {"medium", "moderate"}:
            return 0.5
        if text in {"high", "strong"}:
            return 0.1
    try:
        conf = float(value)
    except Exception:
        return 0.0
    if conf > 1.0:
        conf = conf / 100.0
    return 1.0 - clamp01(conf)


def risk_level_to_score(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().lower()
    if text in {"critical", "severe"}:
        return 1.0
    if text in {"high", "fraud", "block", "deny", "manual_review"}:
        return 0.9
    if text in {"medium", "moderate", "review"}:
        return 0.6
    if text in {"low", "normal", "clean", "matched", "approved"}:
        return 0.2
    return 0.0


def infer_workflow(scenario_name: str, agent: str, payload: Any) -> str:
    joined = f"{scenario_name} {agent} {flatten_text(payload, 300)}".lower()
    if "back" in joined or "eod" in joined or "ledger" in joined or "reconciliation" in joined:
        return "back_office"
    if "mid" in joined or "forecast" in joined or "schedule" in joined or "representative" in joined:
        return "mid_office"
    if "front" in joined or "fraud" in joined or "compliance" in joined or "transaction" in joined or "chatbot" in joined:
        return "front_office"
    return "unknown"


def dict_get_deep(obj: Any, keys: Iterable[str]) -> Any:
    if not isinstance(obj, Mapping):
        return None
    for key in keys:
        if key in obj:
            return obj[key]
    return None


@dataclass
class ObservabilityEvent:
    trace_id: str
    scenario_name: str
    run_index: int
    sequence: int
    timestamp_utc: str
    workflow: str
    agent: str
    event_type: str
    name: str = ""
    tool_name: str = ""
    state_key: str = ""
    payload_summary: str = ""
    payload: Any = None
    raw_size_bytes: int = 0
    risk_score: float = 0.0
    state_change_score: float = 0.0
    decision_impact_score: float = 0.0
    anomaly_score: float = 0.0
    confidence_risk_score: float = 0.0
    cost_latency_score: float = 0.0
    importance_score: float = 0.0
    keep_reason: str = ""
    selected_for_monitoring: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self, include_payload: bool = True) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if not include_payload:
            data.pop("payload", None)
        return data


def score_event(event: ObservabilityEvent) -> ObservabilityEvent:
    text = " ".join(
        [
            event.event_type,
            event.name,
            event.tool_name,
            event.state_key,
            event.agent,
            event.payload_summary,
            flatten_text(event.payload, 1000),
        ]
    ).lower()

    parsed_payload = maybe_json(event.payload)
    if not isinstance(parsed_payload, Mapping):
        parsed_payload = {}

    # Risk score: keyword + explicit risk/confidence/decision signals.
    keyword_hits = [kw for kw in RISK_KEYWORDS if kw in text]
    keyword_score = min(1.0, len(keyword_hits) / 3.0)
    explicit_risk = max(
        risk_level_to_score(parsed_payload.get("risk_level")),
        risk_level_to_score(parsed_payload.get("fraud_risk")),
        risk_level_to_score(parsed_payload.get("compliance_risk")),
        risk_level_to_score(parsed_payload.get("decision")),
        0.9 if parsed_payload.get("manual_review_needed") is True else 0.0,
        0.9 if parsed_payload.get("suspicious") is True else 0.0,
    )
    if event.event_type in {"error", "exception", "manual_review"}:
        explicit_risk = max(explicit_risk, 1.0)
    event.risk_score = max(keyword_score, explicit_risk)

    # State change score: writes to banking state or persistent control records.
    if event.tool_name in STATE_CHANGE_TOOLS or event.event_type in {"state_change", "ledger_update", "manual_review", "exception"}:
        event.state_change_score = 1.0
    elif event.event_type == "tool_result" and any(word in text for word in ["posted", "updated", "generated", "created", "validated"]):
        event.state_change_score = 0.8
    else:
        event.state_change_score = 0.0

    # Decision impact score: event controls routing/approval/escalation.
    if event.state_key in IMPORTANT_STATE_KEYS and any(k in text for k in DECISION_KEYS):
        event.decision_impact_score = 0.9
    elif any(k in parsed_payload for k in DECISION_KEYS):
        event.decision_impact_score = 0.9
    elif event.event_type in {"transfer", "decision", "validation", "reconciliation"}:
        event.decision_impact_score = 0.7
    else:
        event.decision_impact_score = 0.2 if event.event_type in {"result", "tool_call", "tool_result"} else 0.0

    # Anomaly score: explicit bad states, mismatches, failures, retries.
    anomaly_terms = ["error", "fail", "failed", "exception", "mismatch", "timeout", "retry", "blocker", "not ready"]
    event.anomaly_score = 1.0 if any(term in text for term in anomaly_terms) else 0.0
    if parsed_payload.get("matched") is False or parsed_payload.get("ready") is False:
        event.anomaly_score = max(event.anomaly_score, 0.9)

    # Confidence risk.
    event.confidence_risk_score = max(
        confidence_to_risk(parsed_payload.get("confidence")),
        0.8 if "low confidence" in text else 0.0,
    )

    # Cost / latency: use payload fields if present. If not, keep zero.
    latency_ms = dict_get_deep(parsed_payload, ["latency_ms", "duration_ms", "elapsed_ms"])
    token_usage = dict_get_deep(parsed_payload, ["total_tokens", "token_usage", "tokens"])
    latency_score = 0.0
    try:
        latency_score = 1.0 if float(latency_ms) >= 5000 else 0.6 if float(latency_ms) >= 2000 else 0.0
    except Exception:
        pass
    token_score = 0.0
    try:
        tokens = float(token_usage)
        token_score = 1.0 if tokens >= 8000 else 0.6 if tokens >= 3000 else 0.0
    except Exception:
        pass
    event.cost_latency_score = max(latency_score, token_score)

    event.importance_score = round(
        0.30 * event.risk_score
        + 0.20 * event.state_change_score
        + 0.15 * event.decision_impact_score
        + 0.15 * event.anomaly_score
        + 0.10 * event.confidence_risk_score
        + 0.10 * event.cost_latency_score,
        4,
    )

    tags = set(event.tags)
    if event.risk_score >= 0.8:
        tags.add("risk")
    if event.state_change_score >= 0.8:
        tags.add("state_change")
    if event.decision_impact_score >= 0.8:
        tags.add("decision")
    if event.anomaly_score >= 0.8:
        tags.add("anomaly")
    if event.confidence_risk_score >= 0.6:
        tags.add("low_confidence")
    event.tags = sorted(tags)
    return event


def select_event(event: ObservabilityEvent, threshold: float = 0.45) -> ObservabilityEvent:
    reasons = []
    if event.event_type in {"error", "exception", "manual_review"}:
        reasons.append("critical_event_type")
    if event.state_change_score >= 0.9:
        reasons.append("state_change")
    if event.risk_score >= 0.8:
        reasons.append("risk")
    if event.anomaly_score >= 0.8:
        reasons.append("anomaly")
    if event.decision_impact_score >= 0.8 and event.importance_score >= 0.35:
        reasons.append("decision_point")
    if event.importance_score >= threshold:
        reasons.append("importance_threshold")

    event.selected_for_monitoring = bool(reasons)
    event.keep_reason = ",".join(dict.fromkeys(reasons))
    return event


def _event_obj_to_base(event: Any) -> dict[str, Any]:
    """Extract a stable subset from an ADK Event-like object."""
    if isinstance(event, Mapping):
        return dict(event)

    base: dict[str, Any] = {}
    for attr in ["id", "author", "timestamp", "invocation_id", "branch"]:
        try:
            value = getattr(event, attr, None)
            if value is not None:
                base[attr] = value
        except Exception:
            pass

    try:
        actions = getattr(event, "actions", None)
        if actions:
            action_dict = {}
            for attr in ["transfer_to_agent", "state_delta", "requested_auth_configs", "escalate"]:
                value = getattr(actions, attr, None)
                if value is not None:
                    action_dict[attr] = value
            if action_dict:
                base["actions"] = action_dict
    except Exception:
        pass

    try:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        extracted_parts = []
        for part in parts:
            p: dict[str, Any] = {}
            text = getattr(part, "text", None)
            if text:
                p["text"] = text
            fc = getattr(part, "function_call", None)
            if fc is not None:
                p["function_call"] = {
                    "name": getattr(fc, "name", None),
                    "args": getattr(fc, "args", None),
                }
            fr = getattr(part, "function_response", None)
            if fr is not None:
                p["function_response"] = {
                    "name": getattr(fr, "name", None),
                    "response": getattr(fr, "response", None),
                }
            if p:
                extracted_parts.append(p)
        if extracted_parts:
            base["parts"] = extracted_parts
    except Exception:
        pass

    if not base:
        base["raw"] = str(event)
    return base


def normalize_events(
    raw_events: Iterable[Any],
    *,
    scenario_name: str,
    run_index: int,
    message: str = "",
    trace_id: str | None = None,
    threshold: float = 0.45,
) -> list[ObservabilityEvent]:
    trace_id = trace_id or stable_trace_id(scenario_name, run_index, message)
    now = datetime.now(timezone.utc).isoformat()
    records: list[ObservabilityEvent] = []
    sequence = 0

    for raw in raw_events:
        base = _event_obj_to_base(raw)
        agent = str(base.get("agent") or base.get("author") or "unknown")
        raw_size = json_size_bytes(base)
        actions = base.get("actions") or {}

        transfer_to = actions.get("transfer_to_agent") if isinstance(actions, Mapping) else None
        if transfer_to:
            sequence += 1
            rec = ObservabilityEvent(
                trace_id=trace_id,
                scenario_name=scenario_name,
                run_index=run_index,
                sequence=sequence,
                timestamp_utc=now,
                workflow=infer_workflow(scenario_name, agent, base),
                agent=agent,
                event_type="transfer",
                name="transfer_to_agent",
                payload={"to": transfer_to},
                payload_summary=f"transfer_to={transfer_to}",
                raw_size_bytes=raw_size,
            )
            records.append(select_event(score_event(rec), threshold))

        state_delta = actions.get("state_delta") if isinstance(actions, Mapping) else None
        if isinstance(state_delta, Mapping):
            for key, value in state_delta.items():
                sequence += 1
                parsed = maybe_json(value)
                rec = ObservabilityEvent(
                    trace_id=trace_id,
                    scenario_name=scenario_name,
                    run_index=run_index,
                    sequence=sequence,
                    timestamp_utc=now,
                    workflow=infer_workflow(scenario_name, agent, {key: parsed}),
                    agent=agent,
                    event_type="result" if key in IMPORTANT_STATE_KEYS else "state_change",
                    name="state_delta",
                    state_key=str(key),
                    payload=parsed,
                    payload_summary=flatten_text(parsed),
                    raw_size_bytes=json_size_bytes({key: parsed}),
                )
                records.append(select_event(score_event(rec), threshold))

        for part in base.get("parts", []) or []:
            if "function_call" in part:
                fc = part["function_call"] or {}
                tool = str(fc.get("name") or "")
                args = fc.get("args")
                sequence += 1
                rec = ObservabilityEvent(
                    trace_id=trace_id,
                    scenario_name=scenario_name,
                    run_index=run_index,
                    sequence=sequence,
                    timestamp_utc=now,
                    workflow=infer_workflow(scenario_name, agent, fc),
                    agent=agent,
                    event_type="tool_call",
                    name="tool_call",
                    tool_name=tool,
                    payload=args,
                    payload_summary=flatten_text(args),
                    raw_size_bytes=json_size_bytes(fc),
                )
                records.append(select_event(score_event(rec), threshold))

            if "function_response" in part:
                fr = part["function_response"] or {}
                tool = str(fr.get("name") or "")
                response = fr.get("response")
                parsed_response = maybe_json(response)
                event_type = "tool_result"
                if tool == "submit_manual_review":
                    event_type = "manual_review"
                elif tool == "create_exception_case":
                    event_type = "exception"
                elif tool == "apply_ledger_updates":
                    event_type = "ledger_update"
                sequence += 1
                rec = ObservabilityEvent(
                    trace_id=trace_id,
                    scenario_name=scenario_name,
                    run_index=run_index,
                    sequence=sequence,
                    timestamp_utc=now,
                    workflow=infer_workflow(scenario_name, agent, fr),
                    agent=agent,
                    event_type=event_type,
                    name="tool_result",
                    tool_name=tool,
                    payload=parsed_response,
                    payload_summary=flatten_text(parsed_response),
                    raw_size_bytes=json_size_bytes(fr),
                )
                records.append(select_event(score_event(rec), threshold))

            if "text" in part:
                text = part.get("text")
                if text and str(text).strip():
                    sequence += 1
                    rec = ObservabilityEvent(
                        trace_id=trace_id,
                        scenario_name=scenario_name,
                        run_index=run_index,
                        sequence=sequence,
                        timestamp_utc=now,
                        workflow=infer_workflow(scenario_name, agent, text),
                        agent=agent,
                        event_type="model_text",
                        name="model_text",
                        payload=None,
                        payload_summary=flatten_text(text, 800),
                        raw_size_bytes=len(str(text).encode("utf-8")),
                    )
                    records.append(select_event(score_event(rec), threshold))

        if not transfer_to and not state_delta and not base.get("parts"):
            sequence += 1
            rec = ObservabilityEvent(
                trace_id=trace_id,
                scenario_name=scenario_name,
                run_index=run_index,
                sequence=sequence,
                timestamp_utc=now,
                workflow=infer_workflow(scenario_name, agent, base),
                agent=agent,
                event_type="raw_event",
                name="raw_event",
                payload=base,
                payload_summary=flatten_text(base),
                raw_size_bytes=raw_size,
            )
            records.append(select_event(score_event(rec), threshold))

    return records


def summarize_records(records: list[ObservabilityEvent]) -> dict[str, Any]:
    if not records:
        return {
            "total_events": 0,
            "selected_events": 0,
            "raw_size_bytes": 0,
            "selected_size_bytes": 0,
            "compression_ratio": 0.0,
            "selection_rate": 0.0,
            "critical_event_recall": 1.0,
        }

    selected = [r for r in records if r.selected_for_monitoring]
    raw_size = sum(max(0, r.raw_size_bytes) for r in records)
    selected_size = sum(max(0, r.raw_size_bytes) for r in selected)
    critical = [
        r
        for r in records
        if r.event_type in CRITICAL_EVENT_TYPES
        or r.risk_score >= 0.8
        or r.anomaly_score >= 0.8
        or r.state_change_score >= 0.8
    ]
    critical_selected = [r for r in critical if r.selected_for_monitoring]
    scores = [r.importance_score for r in records]

    return {
        "total_events": len(records),
        "selected_events": len(selected),
        "dropped_events": len(records) - len(selected),
        "selection_rate": round(len(selected) / len(records), 4),
        "raw_size_bytes": raw_size,
        "selected_size_bytes": selected_size,
        "storage_reduction_rate": round(1.0 - (selected_size / raw_size if raw_size else 0.0), 4),
        "compression_ratio": round(selected_size / raw_size, 4) if raw_size else 0.0,
        "critical_events": len(critical),
        "critical_events_selected": len(critical_selected),
        "critical_event_recall": round(len(critical_selected) / len(critical), 4) if critical else 1.0,
        "avg_importance_score": round(statistics.mean(scores), 4),
        "p95_importance_score": round(sorted(scores)[int(0.95 * (len(scores) - 1))], 4),
        "event_type_counts": dict(Counter(r.event_type for r in records)),
        "selected_event_type_counts": dict(Counter(r.event_type for r in selected)),
        "agent_counts": dict(Counter(r.agent for r in records)),
        "workflow_counts": dict(Counter(r.workflow for r in records)),
        "keep_reason_counts": dict(Counter(reason for r in selected for reason in r.keep_reason.split(",") if reason)),
    }


def summarize_runs(run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not run_summaries:
        return {}
    numeric_keys = [
        "total_events",
        "selected_events",
        "dropped_events",
        "raw_size_bytes",
        "selected_size_bytes",
        "storage_reduction_rate",
        "compression_ratio",
        "selection_rate",
        "critical_event_recall",
        "runtime_seconds",
        "logging_overhead_seconds",
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in run_summaries:
        grouped[summary.get("scenario_name", "unknown")].append(summary)

    by_scenario = {}
    for scenario, rows in grouped.items():
        entry = {"runs": len(rows)}
        for key in numeric_keys:
            vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
            if vals:
                entry[f"avg_{key}"] = round(statistics.mean(vals), 4)
                entry[f"min_{key}"] = round(min(vals), 4)
                entry[f"max_{key}"] = round(max(vals), 4)
        by_scenario[scenario] = entry

    all_entry = {"runs": len(run_summaries)}
    for key in numeric_keys:
        vals = [r[key] for r in run_summaries if isinstance(r.get(key), (int, float))]
        if vals:
            all_entry[f"avg_{key}"] = round(statistics.mean(vals), 4)
            all_entry[f"min_{key}"] = round(min(vals), 4)
            all_entry[f"max_{key}"] = round(max(vals), 4)

    return {"overall": all_entry, "by_scenario": by_scenario}


def records_to_jsonl(records: list[ObservabilityEvent], include_payload: bool = False) -> str:
    return "\n".join(json_dumps(r.to_dict(include_payload=include_payload)) for r in records) + ("\n" if records else "")


def records_to_csv_rows(records: list[ObservabilityEvent]) -> list[dict[str, Any]]:
    rows = []
    for r in records:
        rows.append(
            {
                "trace_id": r.trace_id,
                "scenario_name": r.scenario_name,
                "run_index": r.run_index,
                "sequence": r.sequence,
                "workflow": r.workflow,
                "agent": r.agent,
                "event_type": r.event_type,
                "tool_name": r.tool_name,
                "state_key": r.state_key,
                "raw_size_bytes": r.raw_size_bytes,
                "importance_score": r.importance_score,
                "risk_score": r.risk_score,
                "state_change_score": r.state_change_score,
                "decision_impact_score": r.decision_impact_score,
                "anomaly_score": r.anomaly_score,
                "confidence_risk_score": r.confidence_risk_score,
                "selected_for_monitoring": r.selected_for_monitoring,
                "keep_reason": r.keep_reason,
                "tags": ";".join(r.tags),
                "payload_summary": r.payload_summary,
            }
        )
    return rows
