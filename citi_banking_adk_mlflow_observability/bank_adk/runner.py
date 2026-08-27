import io
import json
from contextlib import redirect_stderr, redirect_stdout
from typing import Any
import os

from google.adk.runners import InMemoryRunner

from bank_adk.app import app
from bank_adk.observability import (
    log_request_artifacts,
    start_request_trace,
)

IMPORTANT_RESULT_KEYS = {
    "fo_decision_report",
    "fo_knowledge_answer",
    "fo_transaction_execution",
    "mid_office_planning_result",
    "mid_office_representative_result",
    "bo_report_output",
    "bo_exception_output", 
    "aml_investigation_summary",
    "aml_sar_draft",
    "aml_office_router_result",
}

TOOL_RESULT_WHITELIST = {
    "execute_transfer",
    "submit_manual_review",
    "store_report",
    "create_exception_case",
}



def _maybe_json(value: Any) -> Any:
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
        return text



def _compact_event(event: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    author = getattr(event, "author", None) or "unknown"
    actions = getattr(event, "actions", None)

    transfer_to = getattr(actions, "transfer_to_agent", None) if actions else None
    if transfer_to:
        return [{"agent": author, "event": "transfer", "to": transfer_to}]

    parts = getattr(getattr(event, "content", None), "parts", None) or []
    state_delta = getattr(actions, "state_delta", None) if actions else None

    for part in parts:
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            name = getattr(function_call, "name", None)
            args = getattr(function_call, "args", None)
            if name and name != "transfer_to_agent":
                items.append({
                    "agent": author,
                    "event": "tool_call",
                    "tool": name,
                    "args": args,
                })

        function_response = getattr(part, "function_response", None)
        if function_response is not None:
            name = getattr(function_response, "name", None)
            response = getattr(function_response, "response", None)
            if name and name in TOOL_RESULT_WHITELIST:
                if isinstance(response, dict):
                    result = response.get("result")
                else:
                    result = getattr(response, "result", response)
                items.append({
                    "agent": author,
                    "event": "tool_result",
                    "tool": name,
                    "result": _maybe_json(result),
                })

    if state_delta:
        for key, value in state_delta.items():
            if key in IMPORTANT_RESULT_KEYS:
                items.append({
                    "agent": author,
                    "event": "result",
                    "key": key,
                    "value": _maybe_json(value),
                })

    return items



def compact_debug_trace(events: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        out.extend(_compact_event(event))
    return out


async def run_message(
    message: str,
    compact: bool = True,
    *,
    scenario_name: str | None = None,
    zero_trust: bool = False,
    zt_refresh: bool = False,
):
    if zero_trust:
        os.environ["ZERO_TRUST_ENABLED"] = "true"
    if zt_refresh:
        os.environ["ZT_REFRESH"] = "true"
        
    runner = InMemoryRunner(app=app)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with start_request_trace(message, scenario_name=scenario_name, compact=compact) as span:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            events = await runner.run_debug(message)

        compact_result = compact_debug_trace(events)
        log_request_artifacts(
            message=message,
            scenario_name=scenario_name,
            compact_result=compact_result,
            raw_event_count=len(events),
        )

    return compact_result if compact else events


async def run_many(messages: list[str], compact: bool = True, *, scenario_names: list[str] | None = None):
    results = []
    for idx, message in enumerate(messages):
        scenario_name = scenario_names[idx] if scenario_names and idx < len(scenario_names) else None
        results.append(await run_message(message, compact=compact, scenario_name=scenario_name))
    return results
