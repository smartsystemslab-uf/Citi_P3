from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from .scenarios import normalize_scenario_name

_SAFE_METRIC_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_JSONISH_RE = re.compile(r"^\s*(?:\{|\[).*(?:\}|\])\s*$", re.DOTALL)


def _safe_metric_key(value: str) -> str:
    return _SAFE_METRIC_RE.sub("_", value).strip("._") or "metric"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _iter_events(compact_result: Any) -> Iterable[dict[str, Any]]:
    if isinstance(compact_result, list):
        for item in compact_result:
            if isinstance(item, dict):
                yield item


def _count_malformed_json_arguments(events: list[dict[str, Any]]) -> int:
    malformed = 0
    for event in events:
        args = event.get("args")
        if not isinstance(args, dict):
            continue
        for value in args.values():
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not _JSONISH_RE.match(text):
                continue
            try:
                json.loads(text)
            except Exception:
                malformed += 1
    return malformed


def _case_fields(case_id: str) -> dict[str, str]:
    # Expected format: <base>__<split>__<failure_type>__r001
    parts = case_id.split("__")
    if len(parts) >= 4:
        return {
            "case_id": case_id,
            "base_scenario": normalize_scenario_name(case_id),
            "split": parts[-3],
            "failure_type": parts[-2],
            "repeat_id": parts[-1],
        }
    return {
        "case_id": case_id,
        "base_scenario": normalize_scenario_name(case_id),
        "split": "unknown",
        "failure_type": "unknown",
        "repeat_id": "unknown",
    }


def extract_trace_features(
    *,
    scenario_name: Optional[str],
    message: str,
    compact_result: Any,
    raw_event_count: int,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """Extract trace-level features from the compact ADK event list.

    These are intentionally domain-light MAS observability features. They are useful
    later for anomaly detection without needing to parse every span directly.
    """
    case_id = scenario_name or "request"
    case = _case_fields(case_id)
    events = list(_iter_events(compact_result))

    transfers = [event for event in events if event.get("event") == "transfer"]
    tool_calls = [event for event in events if event.get("event") == "tool_call"]
    tool_results = [event for event in events if event.get("event") == "tool_result"]
    final_results = [event for event in events if event.get("event") == "result"]

    agents = [str(event.get("agent")) for event in events if event.get("agent")]
    tools = [str(event.get("tool")) for event in events if event.get("tool")]
    transfer_targets = [str(event.get("to")) for event in transfers if event.get("to")]
    final_text = "\n".join(_as_text(event.get("value")) for event in final_results)
    args_text = "\n".join(_as_text(event.get("args")) for event in tool_calls)

    tool_call_counts: dict[str, int] = {}
    for event in tool_calls:
        tool = str(event.get("tool", "unknown"))
        tool_call_counts[tool] = tool_call_counts.get(tool, 0) + 1

    agent_counts: dict[str, int] = {}
    for agent in agents:
        agent_counts[agent] = agent_counts.get(agent, 0) + 1

    features: dict[str, Any] = {
        **case,
        "trace_id": trace_id,
        "message_char_count": len(message or ""),
        "raw_event_count": raw_event_count,
        "compact_event_count": len(events),
        "transfer_count": len(transfers),
        "tool_call_count": len(tool_calls),
        "tool_result_count": len(tool_results),
        "final_result_count": len(final_results),
        "unique_agent_count": len(set(agents)),
        "unique_tool_count": len(set(tools)),
        "unique_transfer_target_count": len(set(transfer_targets)),
        "max_repeated_tool_call_count": max(tool_call_counts.values(), default=0),
        "max_repeated_agent_event_count": max(agent_counts.values(), default=0),
        "malformed_json_argument_count": _count_malformed_json_arguments(events),
        "final_response_char_count": len(final_text),
        "tool_argument_char_count": len(args_text),
        "has_final_result": int(len(final_results) > 0),
        "has_tool_call": int(len(tool_calls) > 0),
        "has_transfer": int(len(transfers) > 0),
        "tools_seen": sorted(set(tools)),
        "agents_seen": sorted(set(agents)),
        "transfer_targets_seen": transfer_targets,
        "tool_call_counts": tool_call_counts,
    }
    return features


def flatten_feature_metrics(prefix: str, features: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    safe_prefix = _safe_metric_key(prefix)
    for key, value in features.items():
        if isinstance(value, bool):
            out[f"{safe_prefix}.{_safe_metric_key(key)}"] = float(int(value))
        elif isinstance(value, int | float):
            out[f"{safe_prefix}.{_safe_metric_key(key)}"] = float(value)
    return out
