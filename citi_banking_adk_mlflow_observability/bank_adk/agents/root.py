from google.adk.agents import LlmAgent
from ..llm import build_model
from ..callbacks import sanitize_tool_call_names
from .front_office import build_front_office_router
from .mid_office import build_mid_office_router
from .back_office import build_back_office_router

def build_root_agent() -> LlmAgent:
    return LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="user_proxy_agent",
        model=build_model(),
        description="Primary entry-point agent that routes requests into front office, mid office, or back office banking workflows.",
        instruction="You are the shared user proxy and conversable entry point for the banking operations platform. Route customer-facing requests to front_office_router. Route internal planning and representative support to mid_office_router. Route scheduled operational processing, reconciliation, and reporting to back_office_router. Do not solve domain tasks yourself when a specialist workflow exists.",
        sub_agents=[build_front_office_router(), build_mid_office_router(), build_back_office_router()],
        output_key="root_routing_result",
    )
