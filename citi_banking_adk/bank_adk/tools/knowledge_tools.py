import json
from pathlib import Path

from ..settings import settings
from ..infra.api_client import BankingApiError, api_client, json_dumps
from .search import rank_records


def _load(name: str):
    return json.loads((Path(settings.data_dir) / name).read_text(encoding="utf-8"))


def search_policies(query: str) -> str:
    """Search the banking policy and compliance knowledge base for rules relevant to the current case."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(api_client.search_policies(query))
        except BankingApiError as exc:
            local = rank_records(query, _load("policies.json"), ["title", "body", "tags"], 4)
            return json_dumps({"backend_warning": str(exc), "local_results": local})
    return json.dumps(rank_records(query, _load("policies.json"), ["title", "body", "tags"], 4), indent=2)


def search_faqs(query: str) -> str:
    """Search customer FAQs and service policies for informational questions."""
    return json.dumps(rank_records(query, _load("faqs.json"), ["question", "answer", "tags"], 4), indent=2)
