from google.adk.agents import LlmAgent
from ..llm import build_model
from ..callbacks import sanitize_tool_call_names
from ..tools.aml_tools import fetch_transaction_history, screen_against_sanctions
from .workflow_agents import AmlInvestigationWorkflowAgent


def build_aml_office_router() -> LlmAgent:
    aml_investigator = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="aml_investigator", 
        model=build_model(), 
        description="Analyzes customer transactions for structuring or sanctions hits.",
        instruction="You are an AML Investigator. Use your tools to analyze the requested entity. Return a compact JSON string with keys investigation_status, findings, and risk_score.", 
        tools=[fetch_transaction_history, screen_against_sanctions], 
        output_key="aml_investigation_summary"
    )
    
    aml_reporter = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="aml_reporter", 
        model=build_model(), 
        description="Drafts a Suspicious Activity Report (SAR) based on investigation findings.",
        instruction="You are an AML Reporter. Read the investigation summary from {aml_investigation_summary}. If suspicious activity is confirmed, draft a SAR. Return a compact JSON string with keys sar_required, sar_draft, and next_steps.", 
        tools=[], 
        output_key="aml_sar_draft"
    )

    aml_workflow_agent = AmlInvestigationWorkflowAgent(
        name="aml_investigation_workflow",
        investigator_agent=aml_investigator,
        reporter_agent=aml_reporter,
    )
    
    return LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="aml_office_router", 
        model=build_model(), 
        description="Routes compliance, fraud, and suspicious activity requests into the AML investigation workflow.",
        instruction="Use aml_investigation_workflow for investigations regarding money laundering, sanctions checks, and suspicious transactions.", 
        sub_agents=[aml_workflow_agent], 
        output_key="aml_office_router_result"
    )