from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from .config import ZeroTrustConfig


_WORKFLOW_BY_AGENT = {
    "front_office_transaction_workflow": "workflow.front_office.fraud_review",
    "customer_service_chatbot_workflow": "workflow.front_office.customer_transaction",
    "back_office_eod_workflow": "workflow.back_office.eod_processing",
}

# ADK exposes LLM-driven agent delegation (root -> office router, router ->
# workflow) as a synthetic function-tool call rather than a direct agent
# invocation. Task 3 never generates a tool PEP for this framework-internal
# control-flow primitive because it is not a scanned business tool, so
# routing it through the generic tool-boundary default-deny logic fails
# closed on every real multi-agent handoff before the target agent is ever
# entered. These names are authorized against the agent-route placement
# instead (see _authorize_agent_transfer below).
_AGENT_TRANSFER_TOOL_NAMES = frozenset({"transfer_to_agent"})


@dataclass
class InvocationState:
    invocation_id: str
    trace_id: str
    workflow_instance_id: str
    input_denied: bool = False
    input_reason: str | None = None
    pending_tools: dict[str, Any] = field(default_factory=dict)


class ZeroTrustBackplanePlugin(BasePlugin):
    """Native ADK Zero Trust enforcement.

    This plugin is registered on the *real* CITI-MAS App. It therefore sees the
    actual user message, actual agent executions selected by ADK, and actual
    function-tool calls selected by the LLM/runtime. It does not replay a
    pre-scripted workflow.
    """

    def __init__(self, *, config: ZeroTrustConfig, root_agent: Any) -> None:
        super().__init__(name="citi_zero_trust_backplane")
        self.config = config
        self.root_agent = root_agent
        self.parent_by_agent: dict[str, str] = {}
        self.workflow_by_agent: dict[str, str] = {}
        self.runtime_agent_edges: list[tuple[str, str]] = []
        self._agents_by_name: dict[str, Any] = {}
        self._index_agent_tree(root_agent)
        self._agent_key_paths = self._load_agent_key_paths(config.agent_key_map_path)
        self._states: dict[str, InvocationState] = {}
        self._last_trace_summary: dict[str, Any] | None = None

        backplane_root = config.backplane_root.resolve()
        if not (backplane_root / "native_mas_runtime").exists():
            raise FileNotFoundError(
                "Native Backplane runtime package not found under "
                f"{backplane_root}. Apply the Backplane-side native runtime patch "
                "first, or pass --zt-backplane-root to point at the correct "
                "checkout. Zero Trust enforcement is on by default; if you "
                "intend to run this message with no protection at all "
                "(local debugging/comparison only), pass --no-zero-trust "
                "explicitly instead of removing this file."
            )
        if str(backplane_root) not in sys.path:
            sys.path.insert(0, str(backplane_root))
        from native_mas_runtime import NativeMasBackplane

        self.engine = NativeMasBackplane(
            backplane_root=backplane_root,
            mas_repo_root=config.mas_repo_root,
            output_dir=config.output_dir,
            mode=config.mode,
            refresh=config.refresh,
            human_approval=config.human_approval,
            secondary_authorization=config.secondary_authorization,
            change_ticket=config.change_ticket,
            shadow_allow_critical=config.shadow_allow_critical,
            runtime_agent_edges=self.runtime_agent_edges,
            identity_registry_path=config.identity_registry_path,
            identity_registry_root_public_key_path=config.identity_registry_root_public_key_path,
            identity_registry_root_key_id=config.identity_registry_root_key_id,
        )
        # NativeMasBackplane installs its configured Task 6 source path.
        from zt_task6.signing import sign_request

        self._sign_request = sign_request

    # ---------------------------------------------------------------
    # Real ADK topology
    # ---------------------------------------------------------------
    def _index_agent_tree(self, root_agent: Any) -> None:
        def walk(agent: Any, parent: str | None, inherited_workflow: str | None) -> None:
            name = str(getattr(agent, "name", "") or "")
            if not name:
                return
            if name in self._agents_by_name:
                raise ValueError(f"Duplicate runtime agent name: {name}")
            self._agents_by_name[name] = agent
            workflow = _WORKFLOW_BY_AGENT.get(name, inherited_workflow)
            if workflow:
                self.workflow_by_agent[name] = workflow
            if parent is not None:
                self.parent_by_agent[name] = parent
                self.runtime_agent_edges.append((parent, name))
            for child in list(getattr(agent, "sub_agents", None) or []):
                walk(child, name, workflow)

        walk(root_agent, None, None)

    def _load_agent_key_paths(self, path: Path | None) -> dict[str, Path]:
        """Snapshot an operator-owned map, never a path supplied by a tool call."""
        if path is None:
            return {}

        def unique_pairs(pairs):
            result = {}
            for identity, value in pairs:
                if identity in result:
                    raise ValueError("Agent key map contains a duplicate identity")
                result[identity] = value
            return result

        path = Path(path).resolve()
        entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
        if not isinstance(entries, dict):
            raise ValueError("Agent key map must be a JSON object of runtime names to key paths")
        result = {}
        for identity, value in entries.items():
            if identity not in self._agents_by_name:
                raise ValueError(f"Agent key map identity is not in the configured agent tree: {identity}")
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("Agent key map paths must be non-empty strings without surrounding whitespace")
            key_path = Path(value)
            result[identity] = (key_path if key_path.is_absolute() else path.parent / key_path).resolve()
        return result

    def _trusted_tool_agent_name(self, tool_context: Any) -> str:
        # ADK constructs this context from the running agent. Bind to that exact
        # object, not a caller assertion, session value, or LLM tool argument.
        context = getattr(tool_context, "_invocation_context", None)
        agent = getattr(context, "agent", None)
        name = str(getattr(agent, "name", "") or "")
        if (
            not name
            or self._agents_by_name.get(name) is not agent
            or getattr(tool_context, "agent_name", None) != name
        ):
            raise ValueError("Tool context does not identify a configured runtime agent")
        return name

    @staticmethod
    def _invocation_id(context: Any) -> str:
        return str(getattr(context, "invocation_id", "") or "unknown-invocation")

    def _ensure_state(self, invocation_id: str) -> InvocationState:
        state = self._states.get(invocation_id)
        if state is None:
            state = InvocationState(
                invocation_id=invocation_id,
                trace_id=f"trace-native-{uuid4()}",
                workflow_instance_id=f"workflow-native-{uuid4()}",
            )
            self._states[invocation_id] = state
        return state

    @staticmethod
    def _content_text(content: Any) -> str:
        parts = list(getattr(content, "parts", None) or [])
        return "".join(str(getattr(part, "text", "") or "") for part in parts)

    @staticmethod
    def _deny_content(kind: str, reason: str, trace_id: str) -> types.Content:
        payload = {
            "status": "zero_trust_denied",
            "boundary": kind,
            "reason": reason,
            "trace_id": trace_id,
        }
        return types.Content(
            role="model",
            parts=[types.Part(text=json.dumps(payload, ensure_ascii=False))],
        )

    def _workflow_for(self, agent_name: str) -> str | None:
        return self.workflow_by_agent.get(agent_name)

    # ---------------------------------------------------------------
    # Input boundary: original CLI message before native ADK invocation
    # ---------------------------------------------------------------
    async def on_user_message_callback(self, *, invocation_context: Any, user_message: Any):
        invocation_id = self._invocation_id(invocation_context)
        state = self._ensure_state(invocation_id)
        text = self._content_text(user_message)
        placement = self.engine.find_input_placement()
        auth = self.engine.authorize(
            placement,
            parameters={
                "message_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "message_length": len(text),
            },
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            event_type="INPUT_AUTHORIZATION",
            caller_runtime_id="cli_user",
            target_runtime_id=str(getattr(self.root_agent, "name", "user_proxy_agent")),
        )
        if not self.engine.should_execute(auth, critical=False):
            state.input_denied = True
            state.input_reason = auth.reason
        return None

    async def before_run_callback(self, *, invocation_context: Any):
        state = self._ensure_state(self._invocation_id(invocation_context))
        self.engine.append_runtime_event(
            "NATIVE_RUN_START",
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            invocation_id=state.invocation_id,
            mode=self.config.mode,
        )
        if state.input_denied and self.config.mode == "enforce":
            return self._deny_content("input", state.input_reason or "DENIED", state.trace_id)
        return None

    # ---------------------------------------------------------------
    # Agent boundary: actual ADK agent/sub-agent execution
    # ---------------------------------------------------------------
    async def before_agent_callback(self, *, agent: Any, callback_context: Any):
        invocation_id = self._invocation_id(callback_context)
        state = self._ensure_state(invocation_id)
        target = str(getattr(agent, "name", "") or "")
        root_name = str(getattr(self.root_agent, "name", "") or "")
        if target == root_name:
            # The CLI input PEP already protects the root entry.
            return None

        caller = self.parent_by_agent.get(target)
        placement = (
            self.engine.find_route_placement(caller, target)
            if caller is not None
            else None
        )
        workflow_id = self._workflow_for(target) or (self._workflow_for(caller) if caller else None)
        auth = self.engine.authorize(
            placement,
            parameters={"target_agent": target},
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            workflow_id=workflow_id,
            event_type="AGENT_ROUTE_AUTHORIZATION",
            caller_runtime_id=caller,
            target_runtime_id=target,
        )
        if not self.engine.should_execute(auth, critical=False):
            return self._deny_content("agent_route", auth.reason, state.trace_id)
        return None

    async def after_agent_callback(self, *, agent: Any, callback_context: Any):
        invocation_id = self._invocation_id(callback_context)
        state = self._ensure_state(invocation_id)
        self.engine.append_runtime_event(
            "AGENT_EXECUTION_COMPLETE",
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            invocation_id=state.invocation_id,
            agent_runtime_id=str(getattr(agent, "name", "") or ""),
        )
        return None

    # ---------------------------------------------------------------
    # Tool boundary: actual tool call before the function executes
    # ---------------------------------------------------------------
    @staticmethod
    def _tool_call_key(tool_context: Any, tool_name: str) -> str:
        call_id = str(getattr(tool_context, "function_call_id", "") or "")
        invocation_id = str(getattr(tool_context, "invocation_id", "") or "")
        return f"{invocation_id}:{call_id or tool_name}"

    def _authorize_agent_transfer(
        self, *, agent_name: str, tool_args: dict[str, Any], state: InvocationState
    ) -> dict[str, Any] | None:
        """Authorize an ADK `transfer_to_agent` handoff at the tool-call
        boundary, using the same agent-route placement/decision as
        `before_agent_callback`. Returning a dict here short-circuits the
        transfer (ADK never enters the target agent); returning None lets
        the transfer proceed, and `before_agent_callback` still re-checks
        the same route independently when the target agent actually starts,
        since some sub-agents are entered by direct orchestration
        (SequentialAgent/ParallelAgent/custom workflow agents) rather than
        through this tool.
        """
        target_agent = str(tool_args.get("agent_name", "") or "")
        placement = (
            self.engine.find_route_placement(agent_name, target_agent)
            if agent_name and target_agent
            else None
        )
        workflow_id = self._workflow_for(target_agent) or self._workflow_for(agent_name)
        auth = self.engine.authorize(
            placement,
            parameters={"target_agent": target_agent},
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            workflow_id=workflow_id,
            event_type="AGENT_ROUTE_AUTHORIZATION",
            caller_runtime_id=agent_name,
            target_runtime_id=target_agent,
        )
        if not self.engine.should_execute(auth, critical=False):
            return {
                "status": "zero_trust_denied",
                "tool": "transfer_to_agent",
                "agent": agent_name,
                "target_agent": target_agent,
                "reason": auth.reason,
                "trace_id": state.trace_id,
            }
        return None

    async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any):
        invocation_id = self._invocation_id(tool_context)
        state = self._ensure_state(invocation_id)
        agent_name = str(getattr(tool_context, "agent_name", "") or "")
        tool_name = str(getattr(tool, "name", "") or "")

        if tool_name in _AGENT_TRANSFER_TOOL_NAMES:
            return self._authorize_agent_transfer(
                agent_name=agent_name, tool_args=tool_args, state=state
            )

        call_key = self._tool_call_key(tool_context, tool_name)
        try:
            agent_name = self._trusted_tool_agent_name(tool_context)
            key_path = self._agent_key_paths.get(agent_name)
            if key_path is None:
                raise ValueError("No private key is configured for the runtime agent")
            signed_identity_request = self._sign_request(
                caller_runtime_id=agent_name,
                target_tool=tool_name,
                private_key_path=key_path,
            )
        except (OSError, ValueError) as exc:
            # Missing/unreadable/malformed keys and untrusted callers are hard
            # denials even in shadow mode. Do not send an unsigned request.
            state.pending_tools.pop(call_key, None)
            self.engine.append_runtime_event(
                "IDENTITY_VERIFICATION_FAILED",
                trace_id=state.trace_id,
                workflow_instance_id=state.workflow_instance_id,
                caller_runtime_id=agent_name,
                target_runtime_id=tool_name,
                reason="Caller identity signing failed",
                error_type=type(exc).__name__,
            )
            return {
                "status": "zero_trust_denied",
                "tool": tool_name,
                "agent": agent_name,
                "reason": "IDENTITY_VERIFICATION_FAILED",
                "trace_id": state.trace_id,
            }
        workflow_id = self._workflow_for(agent_name)
        placement = self.engine.find_tool_placement(
            agent_name,
            tool_name,
            workflow_id=workflow_id,
        )
        pending = self.engine.begin_tool(
            placement,
            tool_args=dict(tool_args),
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            workflow_id=workflow_id,
            idempotency_key=f"adk:{call_key}",
            caller_runtime_id=agent_name,
            tool_name=tool_name,
            signed_identity_request=signed_identity_request,
        )
        state.pending_tools[call_key] = pending
        critical = bool(placement and placement.get("critical"))
        if (
            pending.authorization.reason == "IDENTITY_VERIFICATION_FAILED"
            or not self.engine.should_execute(pending.authorization, critical=critical)
        ):
            state.pending_tools.pop(call_key, None)
            return {
                "status": "zero_trust_denied",
                "tool": tool_name,
                "agent": agent_name,
                "reason": pending.authorization.reason,
                "trace_id": state.trace_id,
            }
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ):
        state = self._ensure_state(self._invocation_id(tool_context))
        tool_name = str(getattr(tool, "name", "") or "")
        call_key = self._tool_call_key(tool_context, tool_name)
        pending = state.pending_tools.pop(call_key, None)
        if pending is None:
            return None
        if pending.authorization.allowed:
            protected = self.engine.finish_tool(pending, result)
            return protected if protected != result else None
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ):
        state = self._ensure_state(self._invocation_id(tool_context))
        tool_name = str(getattr(tool, "name", "") or "")
        call_key = self._tool_call_key(tool_context, tool_name)
        pending = state.pending_tools.pop(call_key, None)
        self.engine.abort_tool(pending)
        self.engine.append_runtime_event(
            "TOOL_EXECUTION_ERROR",
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            invocation_id=state.invocation_id,
            agent_runtime_id=str(getattr(tool_context, "agent_name", "") or ""),
            tool=tool_name,
            error=f"{type(error).__name__}: {error}",
        )
        return None

    async def on_event_callback(self, *, invocation_context: Any, event: Any):
        state = self._ensure_state(self._invocation_id(invocation_context))
        self.engine.append_runtime_event(
            "ADK_EVENT",
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            invocation_id=state.invocation_id,
            author=str(getattr(event, "author", "") or ""),
            event_id=str(getattr(event, "id", "") or ""),
            final_response=bool(
                getattr(event, "is_final_response", lambda: False)()
                if callable(getattr(event, "is_final_response", None))
                else False
            ),
        )
        return None

    async def after_run_callback(self, *, invocation_context: Any):
        invocation_id = self._invocation_id(invocation_context)
        state = self._ensure_state(invocation_id)
        self.engine.append_runtime_event(
            "NATIVE_RUN_COMPLETE",
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            invocation_id=state.invocation_id,
            mode=self.config.mode,
        )
        self._last_trace_summary = {
            "trace_id": state.trace_id,
            "workflow_instance_id": state.workflow_instance_id,
            "invocation_id": invocation_id,
            "mode": self.config.mode,
            "audit_path": str(self.engine.audit_path),
            "policy_version": self.engine.task4_runtime.engine.bundle.version,
        }
        return None

    # ---------------------------------------------------------------
    # Final native runner output boundary
    # ---------------------------------------------------------------
    def protect_final_output(self, value: Any) -> Any:
        if not self._states:
            return value
        # run_message creates one invocation per App/Runner call. The most recent
        # state therefore corresponds to the output being serialized by run_cli.
        state = next(reversed(self._states.values()))
        raw = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        placement = self.engine.find_output_placement()
        auth = self.engine.authorize(
            placement,
            parameters={
                "output_sha256": hashlib.sha256(raw).hexdigest(),
                "output_bytes": len(raw),
            },
            trace_id=state.trace_id,
            workflow_instance_id=state.workflow_instance_id,
            event_type="OUTPUT_AUTHORIZATION",
            caller_runtime_id=str(getattr(self.root_agent, "name", "user_proxy_agent")),
            target_runtime_id="mas_output_channel",
        )
        if not self.engine.should_execute(auth, critical=False):
            return {
                "status": "zero_trust_denied",
                "boundary": "output",
                "reason": auth.reason,
                "trace_id": state.trace_id,
            }
        return value

    def trace_summary(self) -> dict[str, Any] | None:
        return dict(self._last_trace_summary) if self._last_trace_summary else None

    async def close(self) -> None:
        self.engine.close()
