from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types as pytypes
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakePart:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[_FakePart]) -> None:
        self.role = role
        self.parts = parts


class _FakeBasePlugin:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeAuth:
    def __init__(self, allowed: bool, reason: str = "VALID_PERMIT_LEASE") -> None:
        self.allowed = allowed
        self.reason = reason
        self.decision = "ALLOW" if allowed else "DENY"
        self.policy_version = "policy-test"
        self.permit_id = "permit-test" if allowed else None


class _FakePending:
    def __init__(self, auth: _FakeAuth) -> None:
        self.authorization = auth


class _FakeEngine:
    instances: list["_FakeEngine"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.audit_path = Path(kwargs["output_dir"]) / "native_mas_audit.jsonl"
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.task4_runtime = SimpleNamespace(
            engine=SimpleNamespace(bundle=SimpleNamespace(version="policy-test"))
        )
        self.events = []
        self.calls = []
        self.deny_input = False
        self.deny_route = False
        self.deny_tools: set[str] = set()
        self.deny_output = False
        self.closed = False
        self.__class__.instances.append(self)

    def find_input_placement(self):
        return {"placement_id": "pep.native.cli_input", "critical": False}

    def find_output_placement(self):
        return {"placement_id": "pep.native.cli_output", "critical": False}

    def find_route_placement(self, caller, target):
        self.calls.append(("find_route", caller, target))
        if caller is None:
            return None
        return {
            "placement_id": f"pep.route.{caller}.{target}",
            "critical": False,
        }

    def find_tool_placement(self, agent, tool, workflow_id=None):
        self.calls.append(("find_tool", agent, tool, workflow_id))
        return {
            "placement_id": f"pep.tool.{agent}.{tool}",
            "critical": tool == "execute_transfer",
        }

    def authorize(
        self,
        placement,
        *,
        parameters,
        trace_id,
        workflow_instance_id,
        event_type,
        caller_runtime_id=None,
        target_runtime_id=None,
        **kwargs,
    ):
        self.calls.append(
            (
                "authorize",
                event_type,
                placement and placement.get("placement_id"),
                caller_runtime_id,
                target_runtime_id,
                dict(parameters),
            )
        )
        deny = placement is None
        if event_type == "INPUT_AUTHORIZATION":
            deny = deny or self.deny_input
        elif event_type == "AGENT_ROUTE_AUTHORIZATION":
            deny = deny or self.deny_route
        elif event_type == "OUTPUT_AUTHORIZATION":
            deny = deny or self.deny_output
        return _FakeAuth(not deny, "TEST_DENY" if deny else "VALID_PERMIT_LEASE")

    def should_execute(self, auth, *, critical=False):
        if auth.allowed:
            return True
        return self.kwargs.get("mode") == "shadow" and not critical

    def begin_tool(
        self,
        placement,
        *,
        tool_args,
        trace_id,
        workflow_instance_id,
        workflow_id,
        idempotency_key,
        caller_runtime_id,
        tool_name,
    ):
        self.calls.append(
            (
                "begin_tool",
                placement and placement.get("placement_id"),
                caller_runtime_id,
                tool_name,
                workflow_id,
                dict(tool_args),
                idempotency_key,
            )
        )
        deny = placement is None or tool_name in self.deny_tools
        return _FakePending(_FakeAuth(not deny, "TEST_TOOL_DENY" if deny else "VALID_PERMIT_LEASE"))

    def finish_tool(self, pending, result):
        self.calls.append(("finish_tool", result))
        if isinstance(result, dict):
            return {**result, "zero_trust_postprocessed": True}
        return result

    def abort_tool(self, pending):
        self.calls.append(("abort_tool", pending is not None))

    def append_runtime_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def close(self):
        self.closed = True


def _install_fake_google_modules(monkeypatch):
    modules = {
        "google": pytypes.ModuleType("google"),
        "google.adk": pytypes.ModuleType("google.adk"),
        "google.adk.plugins": pytypes.ModuleType("google.adk.plugins"),
        "google.adk.plugins.base_plugin": pytypes.ModuleType("google.adk.plugins.base_plugin"),
        "google.genai": pytypes.ModuleType("google.genai"),
    }
    modules["google.adk.plugins.base_plugin"].BasePlugin = _FakeBasePlugin
    modules["google.genai"].types = SimpleNamespace(Content=_FakeContent, Part=_FakePart)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def _load_plugin_module(monkeypatch):
    _install_fake_google_modules(monkeypatch)
    fake_native = pytypes.ModuleType("native_mas_runtime")
    fake_native.NativeMasBackplane = _FakeEngine
    monkeypatch.setitem(sys.modules, "native_mas_runtime", fake_native)

    package_dir = Path(__file__).resolve().parents[1] / "bank_adk" / "zero_trust"
    pkg_name = "native_zt_testpkg"
    pkg = pytypes.ModuleType(pkg_name)
    pkg.__path__ = [str(package_dir)]
    monkeypatch.setitem(sys.modules, pkg_name, pkg)

    for mod in ("config", "plugin"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{mod}", package_dir / f"{mod}.py"
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"{pkg_name}.{mod}", module)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return sys.modules[f"{pkg_name}.plugin"], sys.modules[f"{pkg_name}.config"]


class _Agent:
    def __init__(self, name: str, sub_agents=None) -> None:
        self.name = name
        self.sub_agents = list(sub_agents or [])


class _Ctx:
    def __init__(self, invocation_id="inv-1", *, agent_name="", function_call_id=""):
        self.invocation_id = invocation_id
        self.agent_name = agent_name
        self.function_call_id = function_call_id


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture()
def native_plugin(tmp_path, monkeypatch):
    _FakeEngine.instances.clear()
    plugin_mod, config_mod = _load_plugin_module(monkeypatch)
    bp = tmp_path / "backplane"
    (bp / "native_mas_runtime").mkdir(parents=True)
    mas = tmp_path / "mas"
    mas.mkdir()
    config = config_mod.ZeroTrustConfig.build(
        enabled=True,
        backplane_root=bp,
        mas_repo_root=mas,
        output_dir=tmp_path / "out",
        mode="enforce",
    )
    leaf = _Agent("transaction_processing_agent")
    workflow = _Agent("customer_service_chatbot_workflow", [leaf])
    router = _Agent("front_office_router", [workflow])
    root = _Agent("user_proxy_agent", [router])
    plugin = plugin_mod.ZeroTrustBackplanePlugin(config=config, root_agent=root)
    return plugin, _FakeEngine.instances[-1], plugin_mod


def test_indexes_real_agent_tree_and_passes_runtime_edges(native_plugin):
    plugin, engine, _ = native_plugin
    assert plugin.parent_by_agent["front_office_router"] == "user_proxy_agent"
    assert plugin.parent_by_agent["customer_service_chatbot_workflow"] == "front_office_router"
    assert plugin.parent_by_agent["transaction_processing_agent"] == "customer_service_chatbot_workflow"
    assert plugin.workflow_by_agent["transaction_processing_agent"] == "workflow.front_office.customer_transaction"
    assert ("user_proxy_agent", "front_office_router") in engine.kwargs["runtime_agent_edges"]


def test_original_cli_message_hits_input_pep_before_run(native_plugin):
    plugin, engine, _ = native_plugin
    ctx = _Ctx()
    user_message = _FakeContent(role="user", parts=[_FakePart("Review TXN-1001")])
    asyncio.run(plugin.on_user_message_callback(invocation_context=ctx, user_message=user_message))
    assert any(call[0:2] == ("authorize", "INPUT_AUTHORIZATION") for call in engine.calls)

    engine.deny_input = True
    ctx2 = _Ctx("inv-2")
    asyncio.run(plugin.on_user_message_callback(invocation_context=ctx2, user_message=user_message))
    denied = asyncio.run(plugin.before_run_callback(invocation_context=ctx2))
    assert isinstance(denied, _FakeContent)
    payload = json.loads(denied.parts[0].text)
    assert payload["boundary"] == "input"
    assert payload["status"] == "zero_trust_denied"


def test_actual_agent_execution_hits_route_pep(native_plugin):
    plugin, engine, _ = native_plugin
    child = _Agent("front_office_router")
    result = asyncio.run(plugin.before_agent_callback(agent=child, callback_context=_Ctx()))
    assert result is None
    assert ("find_route", "user_proxy_agent", "front_office_router") in engine.calls

    engine.deny_route = True
    denied = asyncio.run(plugin.before_agent_callback(agent=child, callback_context=_Ctx("inv-3")))
    assert isinstance(denied, _FakeContent)
    assert json.loads(denied.parts[0].text)["boundary"] == "agent_route"


def test_denied_real_tool_call_is_short_circuited(native_plugin):
    plugin, engine, _ = native_plugin
    engine.deny_tools.add("execute_transfer")
    context = _Ctx(agent_name="transaction_processing_agent", function_call_id="fc-1")
    tool = _Tool("execute_transfer")
    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"source_account": "A", "destination_account": "B", "amount": 100.0},
            tool_context=context,
        )
    )
    assert blocked["status"] == "zero_trust_denied"
    assert blocked["tool"] == "execute_transfer"
    begin = next(call for call in engine.calls if call[0] == "begin_tool")
    assert begin[2] == "transaction_processing_agent"
    assert begin[3] == "execute_transfer"
    assert begin[4] == "workflow.front_office.customer_transaction"


def test_allowed_tool_is_postprocessed_after_actual_execution(native_plugin):
    plugin, engine, _ = native_plugin
    context = _Ctx(agent_name="transaction_processing_agent", function_call_id="fc-2")
    tool = _Tool("get_transaction_context")
    assert asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"transaction_id": "TXN-1001"},
            tool_context=context,
        )
    ) is None
    replacement = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"transaction_id": "TXN-1001"},
            tool_context=context,
            result={"transaction_id": "TXN-1001"},
        )
    )
    assert replacement["zero_trust_postprocessed"] is True


def test_final_json_output_hits_output_pep(native_plugin):
    plugin, engine, _ = native_plugin
    ctx = _Ctx()
    user_message = _FakeContent(role="user", parts=[_FakePart("hello")])
    asyncio.run(plugin.on_user_message_callback(invocation_context=ctx, user_message=user_message))
    original = [{"agent": "x", "event": "result"}]
    assert plugin.protect_final_output(original) == original
    assert any(call[0:2] == ("authorize", "OUTPUT_AUTHORIZATION") for call in engine.calls)

    engine.deny_output = True
    blocked = plugin.protect_final_output(original)
    assert blocked["status"] == "zero_trust_denied"
    assert blocked["boundary"] == "output"


def test_transfer_to_agent_is_authorized_as_agent_route_not_generic_tool(native_plugin):
    """Regression test: `transfer_to_agent` is how ADK exposes LLM-driven
    delegation as a function-tool call. Task 3 never generates a tool PEP for
    this framework-internal primitive, so it must be authorized against the
    agent-route placement (like `before_agent_callback` does), not the
    generic tool boundary -- otherwise every real multi-agent handoff fails
    closed with NO_GENERATED_PEP_FOR_NATIVE_PATH before the target agent is
    ever entered.
    """
    plugin, engine, _ = native_plugin
    context = _Ctx(agent_name="user_proxy_agent", function_call_id="fc-transfer-1")
    tool = _Tool("transfer_to_agent")

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"agent_name": "front_office_router"},
            tool_context=context,
        )
    )
    assert allowed is None
    assert ("find_route", "user_proxy_agent", "front_office_router") in engine.calls
    assert any(
        call[0:2] == ("authorize", "AGENT_ROUTE_AUTHORIZATION") for call in engine.calls
    )
    # This must not be treated as a generic business tool call.
    assert not any(call[0] == "begin_tool" for call in engine.calls)

    engine.deny_route = True
    context2 = _Ctx(agent_name="user_proxy_agent", function_call_id="fc-transfer-2")
    denied = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"agent_name": "front_office_router"},
            tool_context=context2,
        )
    )
    assert denied["status"] == "zero_trust_denied"
    assert denied["tool"] == "transfer_to_agent"
    assert denied["target_agent"] == "front_office_router"


def test_tool_error_aborts_task5_reservation(native_plugin):
    plugin, engine, _ = native_plugin
    context = _Ctx(agent_name="transaction_processing_agent", function_call_id="fc-3")
    tool = _Tool("get_transaction_context")
    asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"transaction_id": "TXN-1001"},
            tool_context=context,
        )
    )
    asyncio.run(
        plugin.on_tool_error_callback(
            tool=tool,
            tool_args={"transaction_id": "TXN-1001"},
            tool_context=context,
            error=RuntimeError("boom"),
        )
    )
    assert any(call[0] == "abort_tool" for call in engine.calls)
    assert any(event[0] == "TOOL_EXECUTION_ERROR" for event in engine.events)
