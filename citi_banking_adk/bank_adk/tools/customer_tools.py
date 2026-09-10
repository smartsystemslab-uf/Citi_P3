import json

from ..infra.repository import repo
from ..settings import settings
from ..infra.api_client import BankingApiError, api_client, json_dumps
from datetime import datetime


def get_customer_context(customer_id: str) -> str:
    """Return full customer context, profile, linked accounts, and recent transactions for a customer id."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(api_client.get_customer_context(customer_id))
        except BankingApiError as exc:
            # Keep the demo runnable even if the backend is not started.
            local = repo.get_customer(customer_id)
            if local:
                local["backend_warning"] = str(exc)
                return json_dumps(local)
            return json_dumps({"error": str(exc), "customer_id": customer_id})
    return json.dumps(repo.get_customer(customer_id) or {"error": f"customer {customer_id} not found"}, indent=2)


def get_transaction_context(transaction_id: str) -> str:
    """Return transaction details, metadata, channel, current status, and decision context for a transaction id."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(api_client.get_transaction(transaction_id))
        except BankingApiError as exc:
            local = repo.get_transaction(transaction_id)
            if local:
                local["backend_warning"] = str(exc)
                return json_dumps(local)
            return json_dumps({"error": str(exc), "transaction_id": transaction_id})
    return json.dumps(repo.get_transaction(transaction_id) or {"error": f"transaction {transaction_id} not found"}, indent=2)


def list_recent_transactions(customer_id: str, limit: int = 5) -> str:
    """Return recent transactions for a customer to support anomaly screening and fraud investigation."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(api_client.list_recent_transactions(customer_id, limit))
        except BankingApiError as exc:
            local = repo.list_recent_transactions(customer_id, limit)
            if local:
                return json_dumps({"backend_warning": str(exc), "transactions": local})
            return json_dumps({"error": str(exc), "customer_id": customer_id})
    return json.dumps(repo.list_recent_transactions(customer_id, limit), indent=2)


def submit_manual_review(case_type: str, payload: str) -> str:
    """Open a manual review case when an automated decision is low-confidence or high-risk."""
    # Manual review remains local for now because the current backend is focused on customers, policies/rules, and banking APIs.
    return json.dumps(repo.create_manual_review(case_type, payload), indent=2)


def execute_transfer(source_account: str, destination_account: str, amount: float, memo: str = "", customer_id: str = "") -> str:
    """Execute a banking transfer through the external banking API layer and return the posting result."""
    if settings.use_external_banking_api:
        try:
            return json_dumps(
                api_client.execute_transfer(
                    source_account=source_account,
                    destination_account=destination_account,
                    amount=amount,
                    memo=memo,
                    customer_id=customer_id or None,
                )
            )
        except BankingApiError as exc:
            return json_dumps({"status": "backend_error", "error": str(exc)})
    return json.dumps(repo.execute_transfer(source_account, destination_account, amount, memo, customer_id or None), indent=2)

def detect_structuring( customer_id: str, window_days: int = 7, threshold_amount: float = 10000.0) -> str:
    """Analyzes customer deposit transactions over a sliding time window to identify
    structuring patterns. Persists results to aml_alerts if a threshold is breached."""


    raw_txns = repo.get_customer_transactions(customer_id)
    if not raw_txns:
        return json.dumps({
            "customer_id": customer_id,
            "structuring_detected": False,
            "reason": "No transactions found"
        }, indent=2)

    deposits = []
    for txn in raw_txns:
        dt = datetime.fromisoformat(txn["created_at"])
        deposits.append({
            "transaction_id": txn["transaction_id"],
            "amount": float(txn["amount"]),
            "timestamp": dt,
            "created_at": txn["created_at"]
        })

        window_delta = timedelta(days=window_days)
    detected_hits = []

    for i, end_txn in enumerate(deposits):
        window_start = end_txn["timestamp"] - window_delta
        contributing = [
            t for t in deposits[:i + 1]
            if t["timestamp"] >= window_start
        ]
        total_amount = sum(t["amount"] for t in contributing)

        if total_amount >= threshold_amount:
            detected_hits.append({
                "window_end_txn": end_txn["transaction_id"],
                "cumulative_total": total_amount,
                "contributing_transactions": [
                    {
                        "transaction_id": t["transaction_id"],
                        "amount": t["amount"],
                        "created_at": t["created_at"]
                    }
                    for t in contributing
                ]
            })

    structuring_detected = len(detected_hits) > 0
    alert_id = None

    if structuring_detected:
        peak_hit = max(detected_hits, key=lambda x: x["cumulative_total"])
        payload = {
            "window_days": window_days,
            "hits_count": len(detected_hits),
            "peak_window": peak_hit
        }
        alert_res = repo.create_aml_alert(
            customer_id=customer_id,
            alert_type="structuring",
            detection_window_params=f"{window_days}d",
            cumulative_total=peak_hit["cumulative_total"],
            threshold=threshold_amount,
            raw_payload=json.dumps(payload)
        )
        alert_id = alert_res["alert_id"]

    return json.dumps({
        "customer_id": customer_id,
        "structuring_detected": structuring_detected,
        "threshold": threshold_amount,
        "window_days": window_days,
        "alert_id": alert_id,
        "hits": detected_hits
    }, indent=2)