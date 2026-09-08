import json
import re
from typing import Any, AsyncGenerator, Optional

from google.adk.agents import Agent, BaseAgent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from citi_banking_adk_mlflow_observability.bank_adk.tools.aml_tools import check_threshold_anomaly

def _state_get(ctx: InvocationContext, key: str) -> Any:
    return ctx.session.state.get(key)


def _extract_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}

    text = str(value).strip()
    if not text:
        return {}

    # 去掉 markdown code fence
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    # 去掉错误的反斜杠，只保留 JSON 合法转义
    repaired = re.sub(r'\\(?!["\\/bfnrtu])', '', text)

    try:
        return json.loads(repaired)
    except Exception:
        pass

    match = re.search(r"\{.*\}", repaired, flags=re.DOTALL)
    if match:
        candidate = re.sub(r'\\(?!["\\/bfnrtu])', '', match.group(0))
        try:
            return json.loads(candidate)
        except Exception:
            return {}

    return {}

class FrontOfficeTransactionWorkflowAgent(BaseAgent):
    monitoring_agent: LlmAgent
    fraud_agent: LlmAgent
    compliance_agent: LlmAgent
    decision_agent: LlmAgent

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        name: str,
        monitoring_agent: LlmAgent,
        fraud_agent: LlmAgent,
        compliance_agent: LlmAgent,
        decision_agent: LlmAgent,
    ):
        super().__init__(
            name=name,
            monitoring_agent=monitoring_agent,
            fraud_agent=fraud_agent,
            compliance_agent=compliance_agent,
            decision_agent=decision_agent,
            description="Deterministically orchestrates the front-office risk workflow while specialist work is performed by LLM agents.",
            sub_agents=[monitoring_agent, fraud_agent, compliance_agent, decision_agent],
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        async for event in self.monitoring_agent.run_async(ctx):
            yield event

        raw_monitoring = _state_get(ctx, "fo_monitoring_report")
        if raw_monitoring is None:
            ctx.session.state["fo_monitoring_report"] = "{}"
        monitoring = _extract_json(_state_get(ctx, "fo_monitoring_report"))
        risk = str(monitoring.get("risk_level", "")).lower()
        suspicious = bool(monitoring.get("suspicious", False))
        should_run_fraud = suspicious or risk in {"medium", "high"} or (raw_monitoring is not None and not monitoring)
        if should_run_fraud:
            async for event in self.fraud_agent.run_async(ctx):
                yield event
        ctx.session.state.setdefault("fo_fraud_report", "{}")

        async for event in self.compliance_agent.run_async(ctx):
            yield event
        ctx.session.state.setdefault("fo_compliance_report", "{}")

        async for event in self.decision_agent.run_async(ctx):
            yield event


class CustomerServiceChatbotWorkflowAgent(BaseAgent):
    intent_agent: LlmAgent
    knowledge_agent: LlmAgent
    service_agent: LlmAgent
    processing_agent: LlmAgent

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        name: str,
        intent_agent: LlmAgent,
        knowledge_agent: LlmAgent,
        service_agent: LlmAgent,
        processing_agent: LlmAgent,
    ):
        super().__init__(
            name=name,
            intent_agent=intent_agent,
            knowledge_agent=knowledge_agent,
            service_agent=service_agent,
            processing_agent=processing_agent,
            description="Routes chatbot requests between informational and service/action handling without relying on nested agent tool-calls.",
            sub_agents=[intent_agent, knowledge_agent, service_agent, processing_agent],
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        async for event in self.intent_agent.run_async(ctx):
            yield event
        intent = _extract_json(_state_get(ctx, "fo_chatbot_intent"))
        route = str(intent.get("route", "")).lower()
        if route == "knowledge":
            async for event in self.knowledge_agent.run_async(ctx):
                yield event
            return

        async for event in self.service_agent.run_async(ctx):
            yield event

        service_result = _extract_json(_state_get(ctx, "fo_service_result"))
        service_type = str(service_result.get("service_type", "")).lower()
        execution_required = bool(service_result.get("execution_required", False))
        has_transfer_fields = all(
            key in service_result for key in ["source_account", "destination_account", "amount"]
        )
        if execution_required or service_type == "transaction_execution" or has_transfer_fields:
            async for event in self.processing_agent.run_async(ctx):
                yield event


class BackOfficeEodWorkflowAgent(BaseAgent):
    validation_agent: LlmAgent
    processing_agent: LlmAgent
    ledger_update_agent: LlmAgent
    reconciliation_agent: LlmAgent
    report_agent: LlmAgent
    exception_agent: LlmAgent

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        name: str,
        validation_agent: LlmAgent,
        processing_agent: LlmAgent,
        ledger_update_agent: LlmAgent,
        reconciliation_agent: LlmAgent,
        report_agent: LlmAgent,
        exception_agent: LlmAgent,
    ):
        super().__init__(
            name=name,
            validation_agent=validation_agent,
            processing_agent=processing_agent,
            ledger_update_agent=ledger_update_agent,
            reconciliation_agent=reconciliation_agent,
            report_agent=report_agent,
            exception_agent=exception_agent,
            description="Conditionally orchestrates the EOD workflow while keeping the analytical and writing steps LLM-driven.",
            sub_agents=[
                validation_agent,
                processing_agent,
                ledger_update_agent,
                reconciliation_agent,
                report_agent,
                exception_agent,
            ],
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        async for event in self.validation_agent.run_async(ctx):
            yield event
        validation = _extract_json(_state_get(ctx, "bo_validation_report"))
        if not bool(validation.get("ready", False)):
            return

        async for event in self.processing_agent.run_async(ctx):
            yield event
        async for event in self.ledger_update_agent.run_async(ctx):
            yield event
        async for event in self.reconciliation_agent.run_async(ctx):
            yield event

        reconciliation = _extract_json(_state_get(ctx, "bo_reconciliation_report"))
        if bool(reconciliation.get("matched", False)):
            async for event in self.report_agent.run_async(ctx):
                yield event
        else:
            async for event in self.exception_agent.run_async(ctx):
                yield event


def build_mid_office_planning_workflow(
    data_analysis_agent: LlmAgent,
    forecasting_agent: LlmAgent,
    staff_scheduling_agent: LlmAgent,
    validation_agent: LlmAgent,
    support_guidance_agent: LlmAgent,
    planning_summary_agent: LlmAgent,
) -> SequentialAgent:
    guidance_parallel = ParallelAgent(
        name="mid_office_planning_parallel",
        sub_agents=[validation_agent, support_guidance_agent],
        description="Runs schedule validation and support guidance in parallel after the schedule draft exists.",
    )
    return SequentialAgent(
        name="mid_office_planning_workflow",
        sub_agents=[
            data_analysis_agent,
            forecasting_agent,
            staff_scheduling_agent,
            guidance_parallel,
            planning_summary_agent,
        ],
        description="Strict planning pipeline with deterministic execution order and LLM specialists.",
    )


def build_representative_assist_workflow(
    financial_data_agent: LlmAgent,
    recommender_agent: LlmAgent,
    loan_agent: LlmAgent,
    risk_compliance_agent: LlmAgent,
    merger_agent: LlmAgent,
) -> SequentialAgent:
    specialist_fanout = ParallelAgent(
        name="representative_assist_parallel",
        sub_agents=[
            financial_data_agent,
            recommender_agent,
            loan_agent,
            risk_compliance_agent,
        ],
        description="Runs representative-assist specialists in parallel.",
    )
    return SequentialAgent(
        name="representative_assistant_workflow",
        sub_agents=[specialist_fanout, merger_agent],
        description="Fan-out/fan-in representative assist workflow.",
    )
class AmlInvestigationWorkflowAgent(BaseAgent):
    investigator_agent: LlmAgent
    reporter_agent: LlmAgent

    aml_investigation_workflow = Agent(
    name="aml_investigator",
    description="Specialist step: Reviews baseline profiles and checks for threshold anomalies.",
    tools=[
        # ... your other tools (e.g., fetch_transaction_history) ...,
        check_threshold_anomaly
    ]
)
    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        name: str,
        investigator_agent: LlmAgent,
        reporter_agent: LlmAgent,
    ):
        super().__init__(
            name=name,
            investigator_agent=investigator_agent,
            reporter_agent=reporter_agent,
            description="Orchestrates the AML investigation and conditional SAR (Suspicious Activity Report) generation.",
            sub_agents=[
                investigator_agent,
                reporter_agent,
            ],
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        
        # Step 1: Run the investigator agent
        async for event in self.investigator_agent.run_async(ctx):
            yield event
            
        # Step 2: Extract investigator output from session state
        investigation = _extract_json(_state_get(ctx, "aml_investigation_summary"))
        
        # Step 3: Conditionally run the reporter agent based on findings
        risk_score = investigation.get("risk_score", 0)
        status = str(investigation.get("investigation_status", "")).lower()
        
        # Trigger the reporter if risk is elevated or explicitly marked suspicious
        is_high_risk = isinstance(risk_score, (int, float)) and risk_score >= 50
        is_suspicious = "suspicious" in status
        
        if is_high_risk or is_suspicious:
            async for event in self.reporter_agent.run_async(ctx):
                yield event
