import io
import json
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

from google.adk.runners import InMemoryRunner
from bank_adk.app import create_app


IMPORTANT_RESULT_KEYS = {
    "fo_decision_report",
    "fo_knowledge_answer",
    "fo_transaction_execution",
    "mid_office_planning_result",
    "mid_office_representative_result",
    "bo_report_output",
    "bo_exception_output",
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


def _zero_trust_plugin(app: Any):
    for plugin in list(getattr(app, "plugins", None) or getattr(app, "_citi_zero_trust_plugins", None) or []):
        if getattr(plugin, "name", "") == "citi_zero_trust_backplane":
            return plugin
    return None


async def run_message(
    message: str,
    mcp_session=None,
    mcp_tools=None,
    compact: bool = True,
    zero_trust_config=None,
    include_zero_trust_status: bool = False,
):
    # Build the original CITI-MAS App. With Zero Trust enabled, the native ADK
    # App itself carries the enforcement plugin.
    app = create_app(
        mcp_session=mcp_session,
        mcp_tools=mcp_tools,
        zero_trust_config=zero_trust_config,
    )
    zt_plugin = _zero_trust_plugin(app)

    # Modern ADK: plugins are part of App. Compatibility fallback: older ADK
    # releases accepted plugins on Runner instead. Do not silently construct an
    # unprotected Runner if App ignored the plugins field.
    configured_plugins = list(getattr(app, "_citi_zero_trust_plugins", None) or [])
    app_plugins = list(getattr(app, "plugins", None) or [])
    if configured_plugins and not app_plugins:
        # Legacy ADK compatibility: Runner cannot receive both `app=` and
        # `plugins=` at the same time.  Use the original root agent/app name
        # while keeping the same native execution graph.
        runner = InMemoryRunner(
            agent=app.root_agent,
            app_name=app.name,
            plugins=configured_plugins,
        )
    else:
        runner = InMemoryRunner(app=app)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            events = await runner.run_debug(message)
        result: Any = compact_debug_trace(events) if compact else events
        if zt_plugin is not None:
            result = zt_plugin.protect_final_output(result)
        if include_zero_trust_status and zt_plugin is not None:
            return {
                "result": result,
                "zero_trust": zt_plugin.trace_summary(),
            }
        return result
    finally:
        close = getattr(runner, "close", None)
        if callable(close):
            maybe = close()
            if hasattr(maybe, "__await__"):
                await maybe


async def run_many(
    messages: list[str],
    mcp_session=None,
    compact: bool = True,
    zero_trust_config=None,
):
    results = []
    available_tools = []
    if mcp_session:
        tools_response = await mcp_session.list_tools()
        available_tools = tools_response.tools

    for message in messages:
        results.append(
            await run_message(
                message,
                mcp_session=mcp_session,
                mcp_tools=available_tools,
                compact=compact,
                zero_trust_config=zero_trust_config,
            )
        )
    return results
