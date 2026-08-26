from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


class FakePlugin:
    name = "citi_zero_trust_backplane"

    def __init__(self):
        self.protected = []
        self._summary = {"trace_id": "trace-native-test"}

    def protect_final_output(self, value):
        self.protected.append(value)
        return {"protected": value}

    def trace_summary(self):
        return self._summary


class FakeRunner:
    last = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.message = None
        self.closed = False
        FakeRunner.last = self

    async def run_debug(self, message):
        self.message = message
        return []

    async def close(self):
        self.closed = True


def _load_runner(monkeypatch, *, with_plugin: bool):
    google = types.ModuleType("google")
    adk = types.ModuleType("google.adk")
    runners = types.ModuleType("google.adk.runners")
    runners.InMemoryRunner = FakeRunner
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", adk)
    monkeypatch.setitem(sys.modules, "google.adk.runners", runners)

    plugin = FakePlugin() if with_plugin else None
    app = SimpleNamespace(
        name="citi_banking_adk",
        root_agent=SimpleNamespace(name="user_proxy_agent"),
        plugins=[plugin] if plugin else [],
        _citi_zero_trust_plugins=[plugin] if plugin else [],
    )
    received = {}

    fake_app_module = types.ModuleType("bank_adk.app")
    def create_app(**kwargs):
        received.update(kwargs)
        return app
    fake_app_module.create_app = create_app
    bank_adk = types.ModuleType("bank_adk")
    bank_adk.__path__ = []
    monkeypatch.setitem(sys.modules, "bank_adk", bank_adk)
    monkeypatch.setitem(sys.modules, "bank_adk.app", fake_app_module)

    path = Path(__file__).resolve().parents[1] / "bank_adk" / "runner.py"
    spec = importlib.util.spec_from_file_location("native_runner_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, plugin, received


def test_run_message_keeps_original_runner_and_adds_output_pep(monkeypatch):
    module, plugin, received = _load_runner(monkeypatch, with_plugin=True)
    config = object()
    result = asyncio.run(
        module.run_message(
            "Review TXN-1001",
            compact=True,
            zero_trust_config=config,
            include_zero_trust_status=True,
        )
    )
    assert received["zero_trust_config"] is config
    assert FakeRunner.last.kwargs["app"].name == "citi_banking_adk"
    assert FakeRunner.last.message == "Review TXN-1001"
    assert plugin.protected == [[]]
    assert result == {
        "result": {"protected": []},
        "zero_trust": {"trace_id": "trace-native-test"},
    }
    assert FakeRunner.last.closed is True


def test_zero_trust_disabled_preserves_original_result_shape(monkeypatch):
    module, plugin, received = _load_runner(monkeypatch, with_plugin=False)
    result = asyncio.run(module.run_message("hello", compact=True))
    assert plugin is None
    assert result == []
    assert received["zero_trust_config"] is None
