from google.adk.apps import App
from .agents.root import build_root_agent
from .infra.bootstrap import bootstrap_runtime
bootstrap_runtime()
root_agent = build_root_agent()
app = App(name="citi_banking_adk", root_agent=root_agent)
