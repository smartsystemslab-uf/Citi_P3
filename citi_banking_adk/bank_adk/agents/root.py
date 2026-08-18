from google.adk.agents import LlmAgent
from ..llm import build_model
from ..callbacks import sanitize_tool_call_names
from .front_office import build_front_office_router
from .mid_office import build_mid_office_router
from .back_office import build_back_office_router

def build_root_agent(mcp_session=None, mcp_tools=None) -> LlmAgent:
    agent_tools = []

    if mcp_session and mcp_tools:
        # Factory function prevents Python loop late-binding issues
        def _make_tool_wrapper(tool_name: str, tool_desc: str):
            async def mcp_bridge_wrapper(**kwargs):
                # The ADK executes this, routing the request over SSE to the microservice
                result = await mcp_session.call_tool(tool_name, arguments=kwargs)
                
                # Extract text content from the MCP response object
                if hasattr(result, 'content'):
                    return "\n".join([c.text for c in result.content if hasattr(c, 'text')])
                return str(result)
            
            # Apply metadata so the LLM framework knows how to prompt the tool
            mcp_bridge_wrapper.__name__ = tool_name
            mcp_bridge_wrapper.__doc__ = tool_desc
            return mcp_bridge_wrapper

        # Dynamically generate and append all available tools
        for mcp_tool in mcp_tools:
            wrapper = _make_tool_wrapper(mcp_tool.name, mcp_tool.description)
            agent_tools.append(wrapper)

    return LlmAgent(
        after_model_callback=sanitize_tool_call_names,
        name="user_proxy_agent",
        model=build_model(),
        description="Primary entry-point agent that routes requests into front office, mid office, or back office banking workflows.",
        instruction="You are the shared user proxy and conversable entry point for the banking operations platform. Route customer-facing requests to front_office_router. Route internal planning and representative support to mid_office_router. Route scheduled operational processing, reconciliation, and reporting to back_office_router. Do not solve domain tasks yourself when a specialist workflow exists.",
        tools=agent_tools,
        sub_agents=[build_front_office_router(), build_mid_office_router(), build_back_office_router()],
        output_key="root_routing_result",
    )
