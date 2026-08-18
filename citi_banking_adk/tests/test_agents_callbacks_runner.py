import json
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent

from bank_adk import callbacks, runner
from bank_adk.agents.back_office import build_back_office_router
from bank_adk.agents.front_office import build_front_office_router
from bank_adk.agents.mid_office import build_mid_office_router
from bank_adk.agents.root import build_root_agent
from bank_adk.agents.workflow_agents import (
    BackOfficeEodWorkflowAgent,
    CustomerServiceChatbotWorkflowAgent,
    FrontOfficeTransactionWorkflowAgent,
    _extract_json,
    _state_get,
    build_mid_office_planning_workflow,
    build_representative_assist_workflow,
)
from bank_adk.app import create_app
from bank_adk.llm import build_model
from bank_adk.settings import Settings


def _llm(name):
    return LlmAgent(name=name)


async def _collect(generator):
    return [item async for item in generator]


def test_settings_model_and_callback_sanitization():
    assert Settings(base_url="https://example.test/").api_base == "https://example.test/v1"
    assert Settings(base_url="https://example.test/v1").api_base == "https://example.test/v1"
    assert Settings().sqlite_path.name == "banking.db"
    assert build_model().model.startswith("openai/")

    assert callbacks._clean_tool_name("to=functions.search_policy <|channel|>") == "search_policy"
    assert callbacks._clean_tool_name("  bad-name!  ") == "bad"
    assert callbacks._clean_tool_name("9bad") == "9bad"

    response = SimpleNamespace(content=None, custom_metadata=None)
    assert callbacks.sanitize_tool_call_names(None, response) is response
    response.content = SimpleNamespace(parts=[SimpleNamespace(function_call=None)])
    callbacks.sanitize_tool_call_names(None, response)
    response.content.parts.append(SimpleNamespace(function_call=SimpleNamespace(name=None)))
    callbacks.sanitize_tool_call_names(None, response)
    call = SimpleNamespace(name="to=functions.execute_transfer<|channel|>")
    response.content.parts.append(SimpleNamespace(function_call=call))
    callbacks.sanitize_tool_call_names(None, response)
    assert call.name == "execute_transfer"
    assert response.custom_metadata == {"tool_name_sanitized": True}


def test_json_extraction_and_state_helpers():
    ctx = SimpleNamespace(session=SimpleNamespace(state={"x": 3}))
    assert _state_get(ctx, "x") == 3
    assert _extract_json({"a": 1}) == {"a": 1}
    assert _extract_json(None) == {}
    assert _extract_json("") == {}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('prefix {"a": "bad\\qgood"} suffix') == {"a": "badqgood"}
    assert _extract_json("not json") == {}
    assert _extract_json("prefix {broken} suffix") == {}


@pytest.mark.asyncio
async def test_front_office_workflow_branching(monkeypatch):
    updates = {}

    async def fake_run(self, ctx):
        if self.name in updates:
            key, value = updates[self.name]
            ctx.session.state[key] = value
        yield self.name

    monkeypatch.setattr(LlmAgent, "run_async", fake_run)
    workflow = FrontOfficeTransactionWorkflowAgent(
        name="front_workflow",
        monitoring_agent=_llm("monitor"),
        fraud_agent=_llm("fraud"),
        compliance_agent=_llm("compliance"),
        decision_agent=_llm("decision"),
    )

    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await _collect(workflow._run_async_impl(ctx)) == ["monitor", "compliance", "decision"]
    assert ctx.session.state["fo_monitoring_report"] == "{}"
    assert ctx.session.state["fo_fraud_report"] == "{}"

    updates["monitor"] = ("fo_monitoring_report", '{"suspicious": true}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await _collect(workflow._run_async_impl(ctx)) == ["monitor", "fraud", "compliance", "decision"]

    updates["monitor"] = ("fo_monitoring_report", "malformed")
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert "fraud" in await _collect(workflow._run_async_impl(ctx))


@pytest.mark.asyncio
async def test_chatbot_and_back_office_workflow_branches(monkeypatch):
    updates = {}

    async def fake_run(self, ctx):
        if self.name in updates:
            key, value = updates[self.name]
            ctx.session.state[key] = value
        yield self.name

    monkeypatch.setattr(LlmAgent, "run_async", fake_run)
    chatbot = CustomerServiceChatbotWorkflowAgent(
        name="chat",
        intent_agent=_llm("intent"),
        knowledge_agent=_llm("knowledge"),
        service_agent=_llm("service"),
        processing_agent=_llm("processing"),
    )
    updates["intent"] = ("fo_chatbot_intent", '{"route":"knowledge"}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await _collect(chatbot._run_async_impl(ctx)) == ["intent", "knowledge"]

    updates["intent"] = ("fo_chatbot_intent", '{"route":"service"}')
    updates["service"] = ("fo_service_result", '{"execution_required":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await _collect(chatbot._run_async_impl(ctx)) == ["intent", "service"]
    for payload in (
        '{"execution_required":true}',
        '{"service_type":"transaction_execution"}',
        '{"source_account":"A","destination_account":"B","amount":1}',
    ):
        updates["service"] = ("fo_service_result", payload)
        ctx = SimpleNamespace(session=SimpleNamespace(state={}))
        assert (await _collect(chatbot._run_async_impl(ctx)))[-1] == "processing"

    back = BackOfficeEodWorkflowAgent(
        name="back",
        validation_agent=_llm("validation"),
        processing_agent=_llm("eod"),
        ledger_update_agent=_llm("ledger"),
        reconciliation_agent=_llm("reconciliation"),
        report_agent=_llm("report"),
        exception_agent=_llm("exception"),
    )
    updates["validation"] = ("bo_validation_report", '{"ready":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await _collect(back._run_async_impl(ctx)) == ["validation"]
    updates["validation"] = ("bo_validation_report", '{"ready":true}')
    updates["reconciliation"] = ("bo_reconciliation_report", '{"matched":true}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert (await _collect(back._run_async_impl(ctx)))[-1] == "report"
    updates["reconciliation"] = ("bo_reconciliation_report", '{"matched":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert (await _collect(back._run_async_impl(ctx)))[-1] == "exception"


def test_agent_factories_and_app_structure():
    planning = build_mid_office_planning_workflow(*[_llm(f"p{i}") for i in range(6)])
    assert planning.name == "mid_office_planning_workflow"
    assert len(planning.sub_agents) == 5
    representative = build_representative_assist_workflow(*[_llm(f"r{i}") for i in range(5)])
    assert representative.name == "representative_assistant_workflow"
    assert len(representative.sub_agents[0].sub_agents) == 4

    front = build_front_office_router()
    mid = build_mid_office_router()
    back = build_back_office_router()
    root = build_root_agent()
    assert [front.name, mid.name, back.name] == ["front_office_router", "mid_office_router", "back_office_router"]
    assert [agent.name for agent in root.sub_agents] == [
        "front_office_router",
        "mid_office_router",
        "back_office_router",
    ]
    app = create_app()
    assert app.name == "citi_banking_adk"
    assert app.root_agent.name == "user_proxy_agent"


@pytest.mark.asyncio
async def test_mcp_wrapper_is_attached_and_calls_session():
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(content=[SimpleNamespace(text="one"), SimpleNamespace(text="two"), object()])

    tool = SimpleNamespace(name="remote_lookup", description="Remote lookup")
    root = build_root_agent(mcp_session=Session(), mcp_tools=[tool])
    assert [getattr(item, "__name__", "") for item in root.tools] == ["remote_lookup"]
    assert await root.tools[0](customer_id="C1") == "one\ntwo"
    assert calls == [("remote_lookup", {"customer_id": "C1"})]

    class TextSession:
        async def call_tool(self, name, arguments):
            return "plain result"

    root = build_root_agent(mcp_session=TextSession(), mcp_tools=[tool])
    assert await root.tools[0]() == "plain result"


def test_runner_compaction_helpers():
    assert runner._maybe_json({"a": 1}) == {"a": 1}
    assert runner._maybe_json(object()).startswith("<object")
    assert runner._maybe_json("  ") == ""
    assert runner._maybe_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert runner._maybe_json("plain") == "plain"

    transfer = SimpleNamespace(author="router", actions=SimpleNamespace(transfer_to_agent="next", state_delta=None), content=None)
    assert runner._compact_event(transfer) == [{"agent": "router", "event": "transfer", "to": "next"}]

    call = SimpleNamespace(name="execute_transfer", args={"amount": 1})
    response = SimpleNamespace(name="execute_transfer", response={"result": '{"status":"ok"}'})
    ignored = SimpleNamespace(name="health", response={"result": "ok"})
    parts = [
        SimpleNamespace(function_call=call, function_response=None),
        SimpleNamespace(function_call=None, function_response=response),
        SimpleNamespace(function_call=None, function_response=ignored),
    ]
    event = SimpleNamespace(
        author="agent",
        actions=SimpleNamespace(transfer_to_agent=None, state_delta={"fo_decision_report": '{"decision":"approve"}', "ignored": 1}),
        content=SimpleNamespace(parts=parts),
    )
    compact = runner.compact_debug_trace([event])
    assert [item["event"] for item in compact] == ["tool_call", "tool_result", "result"]
    assert compact[1]["result"] == {"status": "ok"}

    object_response = SimpleNamespace(name="store_report", response=SimpleNamespace(result='{"stored":true}'))
    object_event = SimpleNamespace(
        author="agent",
        actions=None,
        content=SimpleNamespace(parts=[SimpleNamespace(function_call=None, function_response=object_response)]),
    )
    assert runner._compact_event(object_event)[0]["result"] == {"stored": True}


@pytest.mark.asyncio
async def test_runner_execution_and_run_many(monkeypatch):
    events = [SimpleNamespace(author="a", actions=None, content=None)]

    class FakeRunner:
        def __init__(self, app):
            self.app = app

        async def run_debug(self, message):
            print("captured")
            return events

    monkeypatch.setattr(runner, "create_app", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    monkeypatch.setattr(runner, "InMemoryRunner", FakeRunner)
    assert await runner.run_message("hello", compact=False) == events
    assert await runner.run_message("hello", compact=True) == []

    calls = []

    async def fake_run_message(message, **kwargs):
        calls.append((message, kwargs))
        return [message]

    monkeypatch.setattr(runner, "run_message", fake_run_message)
    session = SimpleNamespace(list_tools=lambda: None)

    async def list_tools():
        return SimpleNamespace(tools=[SimpleNamespace(name="remote")])

    session.list_tools = list_tools
    assert await runner.run_many(["a", "b"], mcp_session=session, compact=False) == [["a"], ["b"]]
    assert calls[0][1]["mcp_tools"][0].name == "remote"
    calls.clear()
    assert await runner.run_many(["c"], compact=True) == [["c"]]
    assert calls[0][1]["mcp_tools"] == []
