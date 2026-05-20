import json
from ..infra.repository import repo

def get_customer_context(customer_id: str) -> str:
    """Return full customer context, profile, and linked accounts for a customer id."""
    return json.dumps(repo.get_customer(customer_id) or {"error": f"customer {customer_id} not found"}, indent=2)

def get_transaction_context(transaction_id: str) -> str:
    """Return transaction details, metadata, channel, and current status for a transaction id."""
    return json.dumps(repo.get_transaction(transaction_id) or {"error": f"transaction {transaction_id} not found"}, indent=2)

def list_recent_transactions(customer_id: str, limit: int = 5) -> str:
    """Return recent transactions for a customer to support anomaly screening and fraud investigation."""
    return json.dumps(repo.list_recent_transactions(customer_id, limit), indent=2)

def submit_manual_review(case_type: str, payload: str) -> str:
    """Open a manual review case when an automated decision is low-confidence or high-risk."""
    return json.dumps(repo.create_manual_review(case_type, payload), indent=2)

def execute_transfer(source_account: str, destination_account: str, amount: float, memo: str = "", customer_id: str = "") -> str:
    """Execute a banking transfer through the local banking API layer and return the posting result."""
    return json.dumps(repo.execute_transfer(source_account, destination_account, amount, memo, customer_id or None), indent=2)
