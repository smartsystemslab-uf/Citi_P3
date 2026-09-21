import json
from datetime import datetime, timedelta

from ..infra.repository import repo


def _evaluate_sliding_window(customer_id: str, window_days: int, threshold_amount: float):
    """Detect structuring by summing only inbound deposit-like transactions."""
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

    deposits.sort(key=lambda item: item["created_at"])

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


def detect_structuring(customer_id: str, window_days: int = 7, threshold_amount: float = 10000.0, auto_trigger_review: bool = True) -> str:
    """Evaluate inbound cash-deposit structuring and optionally create a review case."""
    from .customer_tools import submit_manual_review

    detection_result = _evaluate_sliding_window(customer_id, window_days, threshold_amount)

    if not detection_result["structuring_detected"]:
        return json.dumps({
            "customer_id": customer_id,
            "structuring_detected": False,
            "threshold": threshold_amount,
            "window_days": window_days,
        }, indent=2)

    peak_hit = detection_result["peak_hit"]
    alert_res = repo.create_aml_alert(
        customer_id=customer_id,
        alert_type="structuring",
        detection_window_params=f"{window_days}d",
        cumulative_total=peak_hit["cumulative_total"],
        threshold=threshold_amount,
        raw_payload=json.dumps(detection_result["hits"]),
    )
    alert_id = alert_res["alert_id"]

    case_id = None
    if auto_trigger_review:
        review_payload = json.dumps({
            "alert_id": alert_id,
            "customer_id": customer_id,
            "detection_type": "structuring",
            "cumulative_total": peak_hit["cumulative_total"],
            "threshold": threshold_amount,
            "contributing_transactions": peak_hit["contributing_transactions"],
        })
        review_res = json.loads(submit_manual_review("structuring", review_payload))
        case_id = review_res["case_id"]

    return json.dumps({
        "customer_id": customer_id,
        "structuring_detected": True,
        "alert_id": alert_id,
        "manual_review_case_id": case_id,
        "threshold": threshold_amount,
        "window_days": window_days,
        "peak_cumulative_total": peak_hit["cumulative_total"],
        "hits": detection_result["hits"],
    }, indent=2)
