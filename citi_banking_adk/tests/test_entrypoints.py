import argparse
import builtins
import asyncio
from email.mime import message
import json
import runpy
import sys
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest

import run_cli
import run_log_observability_study as log_study
import run_scenarios
import test_backend_connection


@pytest.mark.asyncio
async def test_run_cli_main(monkeypatch, capsys):
    async def fake_run(message, compact, zero_trust_config=None, include_zero_trust_status=False):
        return {"message": message, "compact": compact}


    monkeypatch.setattr(run_cli, "run_message", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_cli.py", "--message", "hello", "--raw"])
    await run_cli.main()
    assert json.loads(capsys.readouterr().out) == {"message": "hello", "compact": False}
    assert run_cli._ser(SimpleNamespace(value=1)).startswith("namespace")


@pytest.mark.asyncio
async def test_run_scenarios_success_and_error(monkeypatch, capsys):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="remote")])

    @asynccontextmanager
    async def stdio(_params):
        yield "read", "write"

    async def fake_many(messages, **kwargs):
        assert kwargs["mcp_session"] is not None
        return [[{"message": message}] for message in messages]

    monkeypatch.setattr(run_scenarios, "stdio_client", stdio)
    monkeypatch.setattr(run_scenarios, "ClientSession", lambda read, write: Session())
    monkeypatch.setattr(run_scenarios, "run_many", fake_many)
    monkeypatch.setattr(sys, "argv", ["run_scenarios.py", "--scenario", "front_office_chatbot"])
    await run_scenarios.main()
    output = capsys.readouterr().out
    assert "Connected" in output
    assert "front_office_chatbot" in output
    assert run_scenarios._ser(SimpleNamespace(value=1)).startswith("namespace")

    @asynccontextmanager
    async def broken_stdio(_params):
        raise RuntimeError("cannot launch")
        yield

    monkeypatch.setattr(run_scenarios, "stdio_client", broken_stdio)
    await run_scenarios.main()
    assert "Failed to connect" in capsys.readouterr().out


def test_backend_connection_script(monkeypatch, capsys):
    fake = SimpleNamespace(
        health=lambda: {"ok": True},
        get_customer_context=lambda value: {"customer": value},
        search_policies=lambda value: {"query": value},
        evaluate_transaction=lambda *args: {"amount": args[2]},
    )
    monkeypatch.setattr(test_backend_connection, "api_client", fake)
    test_backend_connection.main()
    output = capsys.readouterr().out
    assert "BANKING BACKEND HEALTH" in output
    assert "60000.0" in output


def test_log_study_file_helpers_and_arguments(tmp_path, monkeypatch):
    json_path = tmp_path / "result.json"
    log_study.write_json(json_path, {"value": object()})
    assert "value" in json.loads(json_path.read_text())

    empty_csv = tmp_path / "empty.csv"
    log_study.write_csv(empty_csv, [])
    assert empty_csv.read_text() == ""
    csv_path = tmp_path / "data.csv"
    log_study.write_csv(csv_path, [{"a": 1, "b": 2}])
    assert csv_path.read_text().splitlines() == ["a,b", "1,2"]
    assert log_study.selected_scenarios("all") is run_scenarios.SCENARIOS
    assert list(log_study.selected_scenarios("front_office_chatbot")) == ["front_office_chatbot"]

    monkeypatch.setattr(sys, "argv", ["study", "--scenario", "front_office_chatbot", "--repeats", "2", "--mlflow"])
    args = log_study.parse_args()
    assert args.repeats == 2
    assert args.mlflow is True


def test_serializer_fallbacks_and_main_guards(monkeypatch):
    original_cli_dumps = run_cli.json.dumps
    original_scenario_dumps = run_scenarios.json.dumps
    monkeypatch.setattr(run_cli.json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad")))
    assert run_cli._ser("value") == "value"
    monkeypatch.setattr(run_cli.json, "dumps", original_cli_dumps)
    monkeypatch.setattr(run_scenarios.json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad")))
    assert run_scenarios._ser("value") == "value"
    monkeypatch.setattr(run_scenarios.json, "dumps", original_scenario_dumps)

    def close_coroutine(coro):
        coro.close()

    monkeypatch.setattr(asyncio, "run", close_coroutine)
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    runpy.run_path(str(root / "run_cli.py"), run_name="__main__")
    runpy.run_path(str(root / "run_scenarios.py"), run_name="__main__")
    monkeypatch.setattr(sys, "argv", ["run_log_observability_study.py"])
    runpy.run_path(str(root / "run_log_observability_study.py"), run_name="__main__")

    from bank_adk.infra import api_client as api_module

    fake = SimpleNamespace(
        health=lambda: {},
        get_customer_context=lambda value: {},
        search_policies=lambda value: [],
        evaluate_transaction=lambda *args: {},
    )
    monkeypatch.setattr(api_module, "api_client", fake)
    runpy.run_path(str(root / "test_backend_connection.py"), run_name="__main__")


def test_optional_mlflow_logging_missing_and_available(tmp_path, monkeypatch, capsys):
    original_import = builtins.__import__

    def missing_import(name, *args, **kwargs):
        if name == "mlflow":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_import)
    log_study.try_log_to_mlflow(tmp_path, {}, [])
    assert "not installed" in capsys.readouterr().out
    monkeypatch.setattr(builtins, "__import__", original_import)

    calls = []

    @contextmanager
    def start_run(**kwargs):
        calls.append(("start_run", kwargs))
        yield

    fake_mlflow = SimpleNamespace(
        set_tracking_uri=lambda value: calls.append(("uri", value)),
        set_experiment=lambda value: calls.append(("experiment", value)),
        start_run=start_run,
        log_metric=lambda key, value: calls.append(("metric", key, value)),
        log_param=lambda key, value: calls.append(("param", key, value)),
        log_artifact=lambda path: calls.append(("artifact", path)),
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "file:///tmp/mlruns")
    (tmp_path / "aggregate_summary.json").write_text("{}")
    log_study.try_log_to_mlflow(
        tmp_path,
        {"overall": {"runs": 1, "label": "ignored"}, "by_scenario": {"a": {}}},
        [{"scenario_name": "a"}],
    )
    assert ("metric", "runs", 1.0) in calls
    assert any(call[0] == "artifact" for call in calls)


@pytest.mark.asyncio
async def test_run_study_writes_outputs_for_success_and_error(tmp_path, monkeypatch, capsys):
    outcomes = iter([[{"author": "agent", "parts": [{"text": "ok"}]}], RuntimeError("boom")])

    async def fake_run(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(log_study, "run_message", fake_run)
    monkeypatch.setattr(
        log_study,
        "selected_scenarios",
        lambda name: {"unit": "message"},
    )
    mlflow_calls = []
    monkeypatch.setattr(log_study, "try_log_to_mlflow", lambda *args: mlflow_calls.append(args))
    args = argparse.Namespace(
        output_dir=str(tmp_path),
        scenario="all",
        repeats=2,
        threshold=0.2,
        include_payload=True,
        mlflow=True,
    )
    await log_study.run_study(args)
    summaries = json.loads((tmp_path / "run_summaries.json").read_text())
    assert [row["status"] for row in summaries] == ["ok", "error"]
    assert (tmp_path / "selected_events.jsonl").exists()
    assert (tmp_path / "all_events.csv").exists()
    assert len(mlflow_calls) == 1
    assert "Study complete" in capsys.readouterr().out
