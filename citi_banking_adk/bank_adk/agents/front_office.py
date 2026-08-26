from google.adk.agents import LlmAgent
from ..llm import build_model
from ..callbacks import sanitize_tool_call_names
from ..tools.customer_tools import execute_transfer, get_customer_context, get_transaction_context, list_recent_transactions, submit_manual_review
from ..tools.knowledge_tools import search_faqs, search_policies
from .workflow_agents import CustomerServiceChatbotWorkflowAgent, FrontOfficeTransactionWorkflowAgent


def build_front_office_router() -> LlmAgent:
    transaction_monitoring_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="transaction_monitoring_agent",
        model=build_model(),
        description="Performs first-pass anomaly screening using customer context, transaction details, and recent activity.",
        instruction="You are the transaction monitoring agent. Always gather the transaction and customer context before you conclude. Assess amount, channel, timing, velocity, counterparty novelty, and device changes. Return a compact JSON string with keys suspicious, risk_level, confidence, triggers, summary, and recommended_next_step. Do not issue the final approval decision. When calling a tool, use the exact tool name only. Never include commentary, channel markers, or prefixes such as to=functions.",
        tools=[get_customer_context, get_transaction_context, list_recent_transactions],
        output_key="fo_monitoring_report",
    )
    fraud_detection_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="fraud_detection_agent",
        model=build_model(),
        description="Performs deeper fraud analysis on transactions that were flagged by monitoring.",
        instruction="You are the fraud detection agent. Use the monitoring report from {fo_monitoring_report} plus any transaction or customer data you need. Focus on account takeover signs, mule behavior, unusual geography, social engineering indicators, and mismatch to historical behavior. Return a compact JSON string with keys fraud_risk, confidence, evidence, rationale, and recommended_controls. When calling a tool, use the exact tool name only. Never include commentary, channel markers, or prefixes such as to=functions.",
        tools=[get_customer_context, get_transaction_context, list_recent_transactions],
        output_key="fo_fraud_report",
    )
    compliance_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="compliance_agent",
        model=build_model(),
        description="Retrieves and applies compliance rules, policy, and regulatory guidance to the transaction review.",
        instruction="You are the compliance agent. Read the transaction and customer context, retrieve relevant policies, and evaluate AML, KYC, sanctions, and escalation requirements. Use the policy search tool before responding. Return a compact JSON string with keys compliance_risk, confidence, relevant_policies, obligations, and recommendation. When calling a tool, use the exact tool name only. Never include commentary, channel markers, or prefixes such as to=functions.",
        tools=[search_policies, get_customer_context, get_transaction_context],
        output_key="fo_compliance_report",
    )
    decision_making_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="decision_making_agent",
        model=build_model(),
        description="Combines monitoring, fraud, and compliance outputs into a final recommendation with confidence handling.",
        instruction="You are the final decision making agent for the front-office transaction risk workflow. Review monitoring {fo_monitoring_report}, fraud {fo_fraud_report}, and compliance {fo_compliance_report}. Return a compact JSON string with keys decision, confidence, manual_review_needed, approval_scope, rationale, and next_step. If confidence is low or the case is high risk, call the submit_manual_review tool before you finish. When calling a tool, use the exact tool name only. Never include commentary, channel markers, or prefixes such as to=functions.",
        tools=[submit_manual_review],
        output_key="fo_decision_report",
    )
    transaction_workflow_agent = FrontOfficeTransactionWorkflowAgent(
        name="front_office_transaction_workflow",
        monitoring_agent=transaction_monitoring_agent,
        fraud_agent=fraud_detection_agent,
        compliance_agent=compliance_agent,
        decision_agent=decision_making_agent,
    )
    knowledge_base_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="knowledge_base_agent",
        model=build_model(),
        description="Answers informational customer questions by retrieving FAQs and customer-facing policies.",
        instruction="You are the knowledge base agent for the customer chatbot workflow. Always use the FAQ search tool when answering. Return a direct customer-facing answer grounded in the retrieved FAQ and policy content. When calling a tool, use the exact tool name only.",
        tools=[search_faqs],
        output_key="fo_knowledge_answer",
    )
    transaction_processing_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="transaction_processing_agent",
        model=build_model(),
        description="Executes approved customer transactions through the banking API layer.",
        instruction="You are the transaction processing agent. Read the structured service plan from {fo_service_result}. If execution_required is true and the plan contains source_account, destination_account, amount, and optional memo/customer_id, call the execute_transfer tool. Return a compact JSON string with keys status, execution_performed, transaction_id, outcome, and customer_message. If execution is not required or the fields are incomplete, return a compact JSON string explaining that no backend posting was performed. If the execute_transfer tool result has status \"zero_trust_denied\", the transfer was blocked before any funds moved: set execution_performed to false, transaction_id to an empty string, outcome to the denial reason, and customer_message to a truthful statement that the transfer was not completed and requires manual review. Never report execution_performed as true unless the tool result confirms the transfer actually posted. When calling a tool, use the exact tool name only.",
        tools=[execute_transfer],
        output_key="fo_transaction_execution",
    )
    customer_service_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="customer_service_agent",
        model=build_model(),
        description="Handles service-oriented routing and prepares requests for backend transaction execution.",
        instruction="You are the customer service agent. Decide whether the user's need is a pure service/information handling case or a real transaction execution request. Do not call backend banking tools yourself. Instead, return a compact JSON string. For an execution request, return keys service_type, execution_required, customer_id, source_account, destination_account, amount, memo, and customer_message. For a non-execution service case, return keys service_type, execution_required, customer_message, and next_step. Use exact field names. execution_required must be true or false.",
        output_key="fo_service_result",
    )
    chatbot_intent_agent = LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="chatbot_intent_agent",
        model=build_model(),
        description="Determines whether a customer chatbot request is informational or requires service or action handling.",
        instruction="You are the chatbot intent triage agent. Classify the current user request into exactly one route: knowledge or service. Return compact JSON with keys route, confidence, and rationale.",
        output_key="fo_chatbot_intent",
    )
    chatbot_workflow_agent = CustomerServiceChatbotWorkflowAgent(
        name="customer_service_chatbot_workflow",
        intent_agent=chatbot_intent_agent,
        knowledge_agent=knowledge_base_agent,
        service_agent=customer_service_agent,
        processing_agent=transaction_processing_agent,
    )
    return LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="front_office_router",
        model=build_model(),
        description="Routes customer-facing work into either the transaction risk workflow or the customer-service chatbot workflow.",
        instruction="Route the request to the correct front-office workflow. Use front_office_transaction_workflow for suspicious transaction review, fraud screening, compliance checks, or transaction monitoring. Use customer_service_chatbot_workflow for customer Q and A, service requests, and approved customer transaction execution.",
        sub_agents=[transaction_workflow_agent, chatbot_workflow_agent],
        output_key="front_office_router_result",
    )
