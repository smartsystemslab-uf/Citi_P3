import json

from ..infra.repository import repo
from ..settings import settings
from ..infra.api_client import BankingApiError, api_client, json_dumps
from datetime import datetime
from typing import Any


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

def detect_structuring(
    customer_id: str,
    window_days: int = 7,
    threshold_amount: float = 10000.0,
    auto_trigger_review: bool = True
) -> str:
    """
    Evaluates deposit patterns over a sliding time window.
    On detection, persists an entry in aml_alerts and opens a manual_review case.
    """
    # 1. Execute sliding window check
    detection_result = _evaluate_sliding_window(customer_id, window_days, threshold_amount)
    
    if not detection_result["structuring_detected"]:
        return json.dumps({
            "customer_id": customer_id,
            "structuring_detected": False,
            "threshold": threshold_amount,
            "window_days": window_days
        }, indent=2)

    peak_hit = detection_result["peak_hit"]

    # 2. Persist machine alert record in aml_alerts
    alert_res = repo.create_aml_alert(
        customer_id=customer_id,
        alert_type="structuring",
        detection_window_params=f"{window_days}d",
        cumulative_total=peak_hit["cumulative_total"],
        threshold=threshold_amount,
        raw_payload=json.dumps(detection_result["hits"])
    )
    alert_id = alert_res["alert_id"]

    # 3. Create human manual review case using existing tool wrapper
    case_id = None
    if auto_trigger_review:
        review_payload = json.dumps({
            "alert_id": alert_id,
            "customer_id": customer_id,
            "detection_type": "structuring",
            "cumulative_total": peak_hit["cumulative_total"],
            "threshold": threshold_amount,
            "contributing_transactions": peak_hit["contributing_transactions"]
        })
        review_res = json.loads(submit_manual_review("structuring", review_payload))
        case_id = review_res["case_id"]

    # 4. Return operational result containing forensic and workflow pointers
    return json.dumps({
        "customer_id": customer_id,
        "structuring_detected": True,
        "alert_id": alert_id,
        "manual_review_case_id": case_id,
        "threshold": threshold_amount,
        "window_days": window_days,
        "peak_cumulative_total": peak_hit["cumulative_total"],
        "hits": detection_result["hits"]
    }, indent=2)

from datetime import datetime, timedelta

def _evaluate_sliding_window(customer_id: str, window_days: int, threshold_amount: float):
    """
    Check for structuring by summing only inbound deposit-like transactions in a
    rolling time window.

    Returns:
    {
      "structuring_detected": bool,
      "hits": [...],
      "peak_hit": {...} | None
    }
    """
    transactions = repo.get_customer_transactions(customer_id)
    if not transactions:
        return {
            "structuring_detected": False,
            "hits": [],
            "peak_hit": None,
        }

    customer = repo.get_customer(customer_id) or {}
    customer_account_ids = {acct["account_id"] for acct in customer.get("accounts", [])}

    deposits = []
    for txn in transactions:
        try:
            amount = float(txn.get("amount") or 0)
        except (TypeError, ValueError):
            continue

        if amount <= 0:
            continue

        txn_type = (txn.get("txn_type") or "").lower()
        source_account = txn.get("source_account")
        destination_account = txn.get("destination_account")

        is_deposit_like = (
            txn_type in {"deposit", "cash_deposit", "credit", "wire_in", "incoming_transfer"}
            or (
                destination_account in customer_account_ids
                and source_account not in customer_account_ids
            )
        )

        if not is_deposit_like:
            continue

        created_at = txn.get("created_at")
        if not created_at:
            continue

        try:
            ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue

        deposits.append({
            "transaction_id": txn.get("transaction_id"),
            "amount": amount,
            "created_at": ts,
            "source_account": source_account,
            "destination_account": destination_account,
            "merchant_or_counterparty": txn.get("merchant_or_counterparty"),
            "channel": txn.get("channel"),
            "txn_type": txn.get("txn_type"),
        })

    deposits.sort(key=lambda x: x["created_at"])

    hits = []
    peak_hit = None
    rolling_total = 0.0
    rolling_items = []

    for txn in deposits:
        rolling_total += txn["amount"]
        rolling_items.append(txn)

        current_time = txn["created_at"]
        cutoff = current_time - timedelta(days=window_days)

        while rolling_items and rolling_items[0]["created_at"] < cutoff:
            rolling_total -= rolling_items[0]["amount"]
            rolling_items.pop(0)

        if rolling_total >= threshold_amount:
            contributing = [
                {
                    "transaction_id": item["transaction_id"],
                    "amount": item["amount"],
                    "created_at": item["created_at"].isoformat(),
                    "source_account": item["source_account"],
                    "destination_account": item["destination_account"],
                }
                for item in rolling_items
            ]

            hit = {
                "window_start": rolling_items[0]["created_at"].isoformat(),
                "window_end": current_time.isoformat(),
                "cumulative_total": round(rolling_total, 2),
                "threshold": threshold_amount,
                "contributing_transactions": contributing,
            }
            hits.append(hit)

            if peak_hit is None or rolling_total > peak_hit["cumulative_total"]:
                peak_hit = hit

    return {
        "structuring_detected": bool(hits),
        "hits": hits,
        "peak_hit": peak_hit,
    }