from google.adk.apps import App
from .agents.root import build_root_agent
from .infra.bootstrap import bootstrap_runtime
bootstrap_runtime()

def create_app(mcp_session=None, mcp_tools=None):
    """
    Factory function to build the ADK App dynamically.
    This allows us to inject the live MCP server connection and tools.
    """
    root_agent = build_root_agent(mcp_session=mcp_session, mcp_tools=mcp_tools)
    return App(name="citi_banking_adk", root_agent=root_agent)
