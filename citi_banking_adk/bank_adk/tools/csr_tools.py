import json
from pathlib import Path

from ..infra.repository import repo
from ..settings import settings
from ..infra.api_client import BankingApiError, api_client, json_dumps
from .search import rank_records


def _load(name: str):
    return json.loads((Path(settings.data_dir) / name).read_text(encoding="utf-8"))


def get_customer_financial_profile(customer_id: str) -> str:
    """Return the customer's financial profile, balances, risk tier, and notes for representative support."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(api_client.get_customer_context(customer_id))
        except BankingApiError as exc:
            local = repo.get_customer(customer_id)
            if local:
                local["backend_warning"] = str(exc)
                return json_dumps(local)
            return json_dumps({"error": str(exc), "customer_id": customer_id})
    return json.dumps(repo.get_customer(customer_id) or {"error": f"customer {customer_id} not found"}, indent=2)


def search_product_catalog(query: str) -> str:
    """Search product offerings and next-best-action candidates relevant to the customer's needs."""
    return json.dumps(rank_records(query, _load("product_catalog.json"), ["name", "summary", "eligibility", "tags"], 4), indent=2)


def search_loan_playbooks(query: str) -> str:
    """Search loan guidance, underwriting steps, and internal process playbooks."""
    return json.dumps(rank_records(query, _load("loan_playbooks.json"), ["title", "body", "tags"], 4), indent=2)
