from google.adk.agents import LlmAgent
from ..llm import build_model
from ..callbacks import sanitize_tool_call_names
from ..tools.back_office_tools import apply_ledger_updates, create_exception_case, get_eod_batch, get_reconciliation_data, store_report, validate_eod_readiness
from .workflow_agents import BackOfficeEodWorkflowAgent


def build_back_office_router() -> LlmAgent:
    validation_checkpoint_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="validation_checkpoint_agent", model=build_model(), description="Checks readiness before the batch enters ordered end-of-day processing.",
        instruction="You are the validation checkpoint agent for EOD. Always use the readiness tool first. Return a compact JSON string with keys ready, blockers, evidence, and recommendation.", tools=[validate_eod_readiness], output_key="bo_validation_report")
    eod_processing_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="eod_processing_agent", model=build_model(), description="Drives the ordered end-of-day operational sequence and prepares ledger posting instructions.",
        instruction="You are the EOD processing agent. Use the validation result from {bo_validation_report}. Retrieve the batch data and prepare posting instructions for the ledger update step. Return a compact JSON string with keys processing_status, posting_instructions, control_checks, and summary.", tools=[get_eod_batch], output_key="bo_processing_report")
    ledger_update_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="ledger_update_agent", model=build_model(), description="Applies the actual ledger updates prepared during EOD processing.",
        instruction="You are the ledger update agent. Read the posting instructions from {bo_processing_report} and call apply_ledger_updates. Return a compact JSON string with keys ledger_update_status, ledger_reference, affected_accounts, and notes.", tools=[apply_ledger_updates], output_key="bo_ledger_update_report")
    reconciliation_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="reconciliation_agent", model=build_model(), description="Verifies whether ledger results and expected totals are consistent after EOD processing.",
        instruction="You are the reconciliation agent. Always inspect reconciliation data before concluding. Return a compact JSON string with keys matched, difference, confidence, rationale, and recommended_next_step.", tools=[get_reconciliation_data], output_key="bo_reconciliation_report")
    report_writing_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="report_writing_agent", model=build_model(), description="Generates and stores the final end-of-day report for clean batches.",
        instruction="You are the report writing agent. Use the processing output {bo_processing_report}, ledger output {bo_ledger_update_report}, and reconciliation output {bo_reconciliation_report} to draft the final EOD report. Call store_report before you finish. Return a compact JSON string with keys report_summary, report_id, and downstream_notes.", tools=[store_report], output_key="bo_report_output")
    exception_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="exception_agent", model=build_model(), description="Creates and summarizes an exception case when reconciliation fails.",
        instruction="You are the exception handling agent for EOD. Use the processing output {bo_processing_report}, ledger output {bo_ledger_update_report}, and reconciliation output {bo_reconciliation_report}. Call create_exception_case before you finish. Return a compact JSON string with keys exception_case_id, summary, immediate_actions, and escalation_owner.", tools=[create_exception_case], output_key="bo_exception_output")
    eod_workflow_agent = BackOfficeEodWorkflowAgent(
        name="back_office_eod_workflow",
        validation_agent=validation_checkpoint_agent,
        processing_agent=eod_processing_agent,
        ledger_update_agent=ledger_update_agent,
        reconciliation_agent=reconciliation_agent,
        report_agent=report_writing_agent,
        exception_agent=exception_agent,
    )
    return LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="back_office_router", model=build_model(), description="Routes scheduled operational processing into the EOD reconciliation and reporting workflow.",
        instruction="Use back_office_eod_workflow for end-of-day processing, reconciliation, reporting, and exception handling requests.", sub_agents=[eod_workflow_agent], output_key="back_office_router_result")
