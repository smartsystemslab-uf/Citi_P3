import json
from pathlib import Path
from ..infra.repository import repo
from ..settings import settings
from .search import rank_records

def _load(name: str):
    return json.loads((Path(settings.data_dir)/name).read_text(encoding="utf-8"))

def get_customer_financial_profile(customer_id: str) -> str:
    """Return the customer's financial profile, balances, risk tier, and notes for representative support."""
    return json.dumps(repo.get_customer(customer_id) or {"error": f"customer {customer_id} not found"}, indent=2)

def search_product_catalog(query: str) -> str:
    """Search product offerings and next-best-action candidates relevant to the customer's needs."""
    return json.dumps(rank_records(query, _load("product_catalog.json"), ["name","summary","eligibility","tags"], 4), indent=2)

def search_loan_playbooks(query: str) -> str:
    """Search loan guidance, underwriting steps, and internal process playbooks."""
    return json.dumps(rank_records(query, _load("loan_playbooks.json"), ["title","body","tags"], 4), indent=2)
