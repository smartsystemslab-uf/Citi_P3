import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

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
    build_mid_office_planning_workflow,
    build_representative_assist_workflow,
)
from bank_adk.infra.repository import BankingRepository
from bank_adk.llm import build_model
from bank_adk.settings import Settings
from bank_adk.tools import back_office_tools, csr_tools, customer_tools, knowledge_tools, ops_tools
from bank_adk.tools.search import rank_records
from google.adk.agents import LlmAgent


@pytest.fixture()
def repository(tmp_path):
    repo = BankingRepository(tmp_path / "banking.db")
    repo.initialize()
    return repo


def test_settings_callbacks_and_agent_factories():
    settings = Settings(base_url="https://example.test/", model_name="model", mlflow_tracking_uri="http://mlflow/")
    assert settings.api_base == "https://example.test/v1"
    assert settings.llm_model == "openai/model"
    assert Settings(model_name="openai/model").llm_model == "openai/model"
    assert settings.resolved_mlflow_otlp_endpoint == "http://mlflow/v1/traces"
    assert Settings(mlflow_otlp_endpoint="http://otel").resolved_mlflow_otlp_endpoint == "http://otel"
    assert settings.sqlite_path.name == "banking.db"
    assert build_model().model.startswith("openai/")

    assert callbacks._clean_tool_name("to=functions.search<|channel|>") == "search"
    response = SimpleNamespace(content=None, custom_metadata=None)
    assert callbacks.sanitize_tool_call_names(None, response) is response
    call = SimpleNamespace(name="to=functions.search<|channel|>")
    response.content = SimpleNamespace(
        parts=[
            SimpleNamespace(function_call=call),
            SimpleNamespace(function_call=None),
            SimpleNamespace(function_call=SimpleNamespace(name=None)),
        ]
    )
    callbacks.sanitize_tool_call_names(None, response)
    assert call.name == "search"
    assert response.custom_metadata["tool_name_sanitized"] is True

    assert _extract_json('```json\n{"ready":true}\n```') == {"ready": True}
    assert _extract_json({"ready": True}) == {"ready": True}
    assert _extract_json(None) == {}
    assert _extract_json("  ") == {}
    assert _extract_json("prefix {\"a\":1} suffix") == {"a": 1}
    assert _extract_json("bad") == {}
    assert _extract_json("prefix {broken} suffix") == {}
    assert build_front_office_router().name == "front_office_router"
    assert build_mid_office_router().name == "mid_office_router"
    assert build_back_office_router().name == "back_office_router"
    assert len(build_root_agent().sub_agents) == 3
    agents = [LlmAgent(name=f"a{i}") for i in range(6)]
    assert len(build_mid_office_planning_workflow(*agents).sub_agents) == 5
    agents = [LlmAgent(name=f"b{i}") for i in range(5)]
    assert len(build_representative_assist_workflow(*agents).sub_agents) == 2


def test_repository_and_all_tool_modules(repository, monkeypatch):
    for module in (customer_tools, csr_tools, ops_tools, back_office_tools):
        monkeypatch.setattr(module, "repo", repository)
    assert repository.get_customer("CUST-001")["accounts"]
    assert repository.get_customer("missing") is None
    assert repository.get_transaction("TXN-1001")["metadata_json"]["device_change"] is True
    assert repository.get_transaction("missing") is None
    assert repository.list_recent_transactions("CUST-001", 1)
    transfer = repository.execute_transfer("CHK-002", "NEW", 10, "memo")
    assert transfer["status"] == "posted"
    with pytest.raises(ValueError, match="Unknown source"):
        repository.execute_transfer("missing", "X", 1, "")
    with pytest.raises(ValueError, match="Insufficient"):
        repository.execute_transfer("CHK-002", "X", 999999, "")
    assert repository.create_manual_review("risk", "{}")["status"] == "open"
    assert repository.save_schedule("{}")["status"] == "validated"
    clean = "EOD-2026-04-21-CLEAN"
    assert repository.get_eod_batch(clean)["notes_json"]
    assert repository.get_eod_batch("missing") is None
    assert repository.apply_ledger_updates(clean, "post")["status"] == "ledger_updated"
    with pytest.raises(ValueError, match="Unknown batch"):
        repository.apply_ledger_updates("missing", "post")
    assert repository.create_exception(clean, "bad")["status"] == "open"
    assert repository.save_report(clean, "done")["status"] == "generated"
    repository.initialize()

    assert json.loads(customer_tools.get_customer_context("CUST-001"))["customer_id"] == "CUST-001"
    assert "not found" in customer_tools.get_customer_context("missing")
    assert json.loads(customer_tools.get_transaction_context("TXN-1001"))["transaction_id"] == "TXN-1001"
    assert "not found" in customer_tools.get_transaction_context("missing")
    assert customer_tools.list_recent_transactions("CUST-001", 1)
    assert json.loads(customer_tools.submit_manual_review("risk", "{}"))["status"] == "open"
    assert json.loads(customer_tools.execute_transfer("CHK-002", "EXT-998", 1))["status"] == "posted"
    assert json.loads(csr_tools.get_customer_financial_profile("CUST-001"))["customer_id"] == "CUST-001"
    assert "not found" in csr_tools.get_customer_financial_profile("missing")
    assert json.loads(csr_tools.search_product_catalog("cash"))
    assert json.loads(csr_tools.search_loan_playbooks("home"))
    assert json.loads(knowledge_tools.search_policies("AML"))
    assert json.loads(knowledge_tools.search_faqs("dispute"))
    assert json.loads(ops_tools.get_operations_snapshot("2026-04-21"))
    assert json.loads(ops_tools.get_operations_snapshot("missing"))
    assert json.loads(ops_tools.persist_validated_schedule("{}"))["status"] == "validated"
    assert json.loads(ops_tools.get_support_playbooks("staff"))
    assert json.loads(back_office_tools.validate_eod_readiness(clean))["ready"] is True
    assert "not found" in back_office_tools.validate_eod_readiness("missing")
    assert json.loads(back_office_tools.get_eod_batch(clean))["batch_id"] == clean
    assert "not found" in back_office_tools.get_eod_batch("missing")
    assert json.loads(back_office_tools.get_reconciliation_data(clean))["difference"] == 0
    assert "not found" in back_office_tools.get_reconciliation_data("missing")
    assert json.loads(back_office_tools.apply_ledger_updates(clean, "post"))["status"] == "ledger_updated"
    assert json.loads(back_office_tools.create_exception_case(clean, "bad"))["status"] == "open"
    assert json.loads(back_office_tools.store_report(clean, "body"))["status"] == "generated"
    rows = [{"text": "fraud fraud"}, {"text": "normal"}]
    assert rank_records("fraud", rows, ["text"])[0] == rows[0]
    assert rank_records("missing", rows, ["text"], 1) == rows[:1]
    assert rank_records("", rows, ["text"], 1) == rows[:1]


def test_runner_compaction():
    transfer = SimpleNamespace(author="a", actions=SimpleNamespace(transfer_to_agent="b", state_delta=None), content=None)
    assert runner._compact_event(transfer)[0]["event"] == "transfer"
    parts = [
        SimpleNamespace(function_call=SimpleNamespace(name="execute_transfer", args={"a": 1}), function_response=None),
        SimpleNamespace(function_call=None, function_response=SimpleNamespace(name="execute_transfer", response={"result": '{"ok":true}'})),
    ]
    event = SimpleNamespace(
        author="a",
        actions=SimpleNamespace(transfer_to_agent=None, state_delta={"fo_decision_report": '{"decision":"approve"}'}),
        content=SimpleNamespace(parts=parts),
    )
    assert [item["event"] for item in runner.compact_debug_trace([event])] == ["tool_call", "tool_result", "result"]
    assert runner._maybe_json('```json\n{"a":1}\n```') == {"a": 1}
    assert runner._maybe_json("plain") == "plain"
    assert runner._maybe_json({"a": 1}) == {"a": 1}
    assert runner._maybe_json(object()).startswith("<object")
    assert runner._maybe_json("  ") == ""
    object_response = SimpleNamespace(name="store_report", response=SimpleNamespace(result='{"stored":true}'))
    object_event = SimpleNamespace(
        author="agent",
        actions=None,
        content=SimpleNamespace(parts=[SimpleNamespace(function_call=None, function_response=object_response)]),
    )
    assert runner._compact_event(object_event)[0]["result"] == {"stored": True}


@pytest.mark.asyncio
async def test_runner_executes_and_logs(monkeypatch):
    events = [SimpleNamespace(author="a", actions=None, content=None)]

    class FakeRunner:
        def __init__(self, app):
            pass

        async def run_debug(self, message):
            return events

    @contextmanager
    def trace(*args, **kwargs):
        yield None

    logged = []
    monkeypatch.setattr(runner, "InMemoryRunner", FakeRunner)
    monkeypatch.setattr(runner, "start_request_trace", trace)
    monkeypatch.setattr(runner, "log_request_artifacts", lambda **kwargs: logged.append(kwargs))
    assert await runner.run_message("m", compact=False, scenario_name="s") == events
    assert await runner.run_message("m", compact=True, scenario_name="s") == []
    assert logged[-1]["raw_event_count"] == 1

    calls = []

    async def fake_run(message, **kwargs):
        calls.append((message, kwargs))
        return message

    monkeypatch.setattr(runner, "run_message", fake_run)
    assert await runner.run_many(["a", "b"], scenario_names=["one"], compact=False) == ["a", "b"]
    assert calls[0][1]["scenario_name"] == "one"
    assert calls[1][1]["scenario_name"] is None


@pytest.mark.asyncio
async def test_conditional_workflow_agents(monkeypatch):
    updates = {}

    async def fake_run(self, ctx):
        if self.name in updates:
            key, value = updates[self.name]
            ctx.session.state[key] = value
        yield self.name

    monkeypatch.setattr(LlmAgent, "run_async", fake_run)

    async def collect(generator):
        return [item async for item in generator]

    front = FrontOfficeTransactionWorkflowAgent(
        name="front",
        monitoring_agent=LlmAgent(name="monitor"),
        fraud_agent=LlmAgent(name="fraud"),
        compliance_agent=LlmAgent(name="compliance"),
        decision_agent=LlmAgent(name="decision"),
    )
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await collect(front._run_async_impl(ctx)) == ["monitor", "compliance", "decision"]
    updates["monitor"] = ("fo_monitoring_report", '{"risk_level":"high"}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert "fraud" in await collect(front._run_async_impl(ctx))
    updates["monitor"] = ("fo_monitoring_report", "malformed")
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert "fraud" in await collect(front._run_async_impl(ctx))

    chatbot = CustomerServiceChatbotWorkflowAgent(
        name="chat",
        intent_agent=LlmAgent(name="intent"),
        knowledge_agent=LlmAgent(name="knowledge"),
        service_agent=LlmAgent(name="service"),
        processing_agent=LlmAgent(name="processing"),
    )
    updates["intent"] = ("fo_chatbot_intent", '{"route":"knowledge"}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await collect(chatbot._run_async_impl(ctx)) == ["intent", "knowledge"]
    updates["intent"] = ("fo_chatbot_intent", '{"route":"service"}')
    updates["service"] = ("fo_service_result", '{"execution_required":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await collect(chatbot._run_async_impl(ctx)) == ["intent", "service"]
    updates["service"] = ("fo_service_result", '{"source_account":"A","destination_account":"B","amount":1}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert (await collect(chatbot._run_async_impl(ctx)))[-1] == "processing"

    back = BackOfficeEodWorkflowAgent(
        name="back",
        validation_agent=LlmAgent(name="validation"),
        processing_agent=LlmAgent(name="eod"),
        ledger_update_agent=LlmAgent(name="ledger"),
        reconciliation_agent=LlmAgent(name="reconciliation"),
        report_agent=LlmAgent(name="report"),
        exception_agent=LlmAgent(name="exception"),
    )
    updates["validation"] = ("bo_validation_report", '{"ready":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert await collect(back._run_async_impl(ctx)) == ["validation"]
    updates["validation"] = ("bo_validation_report", '{"ready":true}')
    updates["reconciliation"] = ("bo_reconciliation_report", '{"matched":true}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert (await collect(back._run_async_impl(ctx)))[-1] == "report"
    updates["reconciliation"] = ("bo_reconciliation_report", '{"matched":false}')
    ctx = SimpleNamespace(session=SimpleNamespace(state={}))
    assert (await collect(back._run_async_impl(ctx)))[-1] == "exception"
