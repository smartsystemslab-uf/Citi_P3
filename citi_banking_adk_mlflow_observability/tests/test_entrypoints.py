import asyncio
import json
import runpy
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import run_cli
import run_scenarios
import run_trace_dataset
from bank_adk.failure_scenarios import TraceCase


@contextmanager
def _batch(*args, **kwargs):
    yield SimpleNamespace()


@pytest.mark.asyncio
async def test_cli_and_scenario_entrypoints(monkeypatch, capsys):
    lifecycle = []
    monkeypatch.setattr(run_cli, "init_observability", lambda: lifecycle.append("init"))
    monkeypatch.setattr(run_cli, "flush_observability", lambda: lifecycle.append("flush"))
    monkeypatch.setattr(run_cli, "start_batch_run", _batch)

    async def one(message, **kwargs):
        return {"message": message, **kwargs}

    monkeypatch.setattr(run_cli, "run_message", one)
    monkeypatch.setattr(sys, "argv", ["run_cli", "--message", "hello", "--raw"])
    await run_cli.main()
    assert json.loads(capsys.readouterr().out)["compact"] is False
    assert lifecycle == ["init", "flush"]
    assert run_cli._ser(SimpleNamespace(a=1)).startswith("namespace")

    lifecycle.clear()
    evaluations = []
    monkeypatch.setattr(run_scenarios, "init_observability", lambda: lifecycle.append("init"))
    monkeypatch.setattr(run_scenarios, "flush_observability", lambda: lifecycle.append("flush"))
    monkeypatch.setattr(run_scenarios, "start_batch_run", _batch)
    monkeypatch.setattr(run_scenarios, "log_batch_evaluation_summary", lambda *args: evaluations.append(args))

    async def many(messages, **kwargs):
        return [[{"message": message}] for message in messages]

    monkeypatch.setattr(run_scenarios, "run_many", many)
    monkeypatch.setattr(sys, "argv", ["run_scenarios", "--scenario", "front_office_chatbot"])
    await run_scenarios.main()
    assert "front_office_chatbot" in capsys.readouterr().out
    assert len(evaluations) == 1
    monkeypatch.setattr(sys, "argv", ["run_scenarios", "--scenario", "front_office_chatbot", "--raw"])
    await run_scenarios.main()
    assert len(evaluations) == 1
    assert run_scenarios._ser(SimpleNamespace(a=1)).startswith("namespace")


@pytest.mark.asyncio
async def test_trace_dataset_continues_after_error(monkeypatch, capsys):
    cases = [
        TraceCase("front_office_chatbot__normal__baseline__r001", "front_office_chatbot", "normal", "baseline", 1, False, "ok"),
        TraceCase("front_office_chatbot__failure__tool_omission__r001", "front_office_chatbot", "failure", "tool_omission", 1, True, "bad"),
    ]
    monkeypatch.setattr(run_trace_dataset, "init_observability", lambda: None)
    monkeypatch.setattr(run_trace_dataset, "flush_observability", lambda: None)
    monkeypatch.setattr(run_trace_dataset, "start_batch_run", _batch)
    monkeypatch.setattr(run_trace_dataset, "build_trace_cases", lambda **kwargs: cases)
    monkeypatch.setattr(run_trace_dataset.mlflow, "set_tags", lambda value: None)
    monkeypatch.setattr(run_trace_dataset, "log_dataset_manifest", lambda value: None)
    monkeypatch.setattr(run_trace_dataset, "log_batch_evaluation_summary", lambda *args: {"evaluated": len(args[0])})
    monkeypatch.setattr(run_trace_dataset, "log_dataset_csv_tables", lambda **kwargs: {"rows": len(kwargs["completed_case_ids"])})
    summaries = []
    monkeypatch.setattr(run_trace_dataset, "log_dataset_run_summary", summaries.append)
    monkeypatch.setattr(
        run_trace_dataset,
        "log_error_artifact",
        lambda **kwargs: {"logged": kwargs["case_id"]},
    )

    async def run(message, **kwargs):
        if message == "bad":
            raise RuntimeError("boom")
        return [{"event": "result", "key": "x", "value": "ok"}]

    monkeypatch.setattr(run_trace_dataset, "run_message", run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_trace_dataset", "--scenario", "front_office_chatbot", "--normal-repeats", "1", "--failure-repeats", "1"],
    )
    await run_trace_dataset.main()
    assert summaries[0]["completed_count"] == 1
    assert summaries[0]["failed_count"] == 1
    assert summaries[0]["success_rate"] == 0.5
    assert "ERROR: RuntimeError" in capsys.readouterr().out
    assert run_trace_dataset._jsonable(SimpleNamespace(a=1)).startswith("namespace")


@pytest.mark.asyncio
async def test_trace_dataset_max_cases_and_stop_on_error(monkeypatch):
    case = TraceCase("case", "front_office_chatbot", "failure", "tool_omission", 1, True, "bad")
    monkeypatch.setattr(run_trace_dataset, "init_observability", lambda: None)
    monkeypatch.setattr(run_trace_dataset, "start_batch_run", _batch)
    monkeypatch.setattr(run_trace_dataset, "build_trace_cases", lambda **kwargs: [case, case])
    monkeypatch.setattr(run_trace_dataset.mlflow, "set_tags", lambda value: None)
    monkeypatch.setattr(run_trace_dataset, "log_dataset_manifest", lambda value: None)
    monkeypatch.setattr(run_trace_dataset, "log_error_artifact", lambda **kwargs: None)

    async def fail(*args, **kwargs):
        raise ValueError("stop")

    monkeypatch.setattr(run_trace_dataset, "run_message", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_trace_dataset", "--scenario", "front_office_chatbot", "--max-cases", "1", "--failure-types", "tool_omission", "--stop-on-error"],
    )
    with pytest.raises(ValueError, match="stop"):
        await run_trace_dataset.main()


def test_serializer_fallbacks_and_main_guards(monkeypatch):
    original_dumps = json.dumps
    monkeypatch.setattr(json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad")))
    assert run_cli._ser("value") == "value"
    assert run_scenarios._ser("value") == "value"
    assert run_trace_dataset._jsonable("value") == "value"
    monkeypatch.setattr(json, "dumps", original_dumps)

    def close_coroutine(coro):
        coro.close()

    monkeypatch.setattr(asyncio, "run", close_coroutine)
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    runpy.run_path(str(root / "run_cli.py"), run_name="__main__")
    runpy.run_path(str(root / "run_scenarios.py"), run_name="__main__")
    runpy.run_path(str(root / "run_trace_dataset.py"), run_name="__main__")
