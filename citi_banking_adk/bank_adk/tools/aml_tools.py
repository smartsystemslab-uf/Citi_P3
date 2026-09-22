import json
import unicodedata
from datetime import date, datetime, timedelta

import networkx as nx

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


def check_threshold_anomaly(account_id: str, transaction_id: str, amount: float) -> dict:
    """Evaluates if a transaction deviates sharply (>5x peak) from the customer's baseline.

    Note: uses a hardcoded baseline rather than real CustomerRiskProfile data — a known
    limitation carried over as-is, not fixed as part of this move.
    """
    profile = {"peak_transfer_amount": 1000.0}
    threshold = profile["peak_transfer_amount"] * 5
    is_anomalous = amount > threshold
    if is_anomalous:
        return {"flagged": True, "reason": "Exceeded 5x peak transfer amount", "status": "review"}
    return {"flagged": False, "reason": "Within normal bounds"}


def find_mule_rings_for_customer(customer_id: str, max_depth: int = 5) -> list[list[str]]:
    """Builds a directed graph from a customer's own transaction flows and detects closed
    loops (mule rings) among the accounts visible in that history.

    Note: scoped to one customer's transaction history, so it can only see ring segments
    where this customer's own accounts appear as an endpoint — a ring entirely between
    other customers' accounts would be invisible to this call. A system-wide query would
    close that gap but is out of scope for this pass.
    """
    transactions = repo.get_customer_transactions(customer_id)

    graph = nx.DiGraph()
    for tx in transactions:
        source = tx.get("source_account")
        destination = tx.get("destination_account")
        if source and destination:
            graph.add_edge(source, destination)

    try:
        cycles = list(nx.simple_cycles(graph))
        return [cycle for cycle in cycles if 2 <= len(cycle) <= max_depth]
    except nx.NetworkXNoCycle:
        return []


def _normalize_name(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(unicodedata.normalize("NFC", value).casefold().split())
    return normalized if any(char.isalpha() for char in normalized) else ""


def _name_similarity(submitted: str, reference: str) -> float:
    """Return one minus Levenshtein edit distance divided by the longer length."""
    if not submitted or not reference:
        return 0.0
    if submitted == reference:
        return 1.0
    if len(submitted) < len(reference):
        submitted, reference = reference, submitted

    previous = list(range(len(reference) + 1))
    for row, left in enumerate(submitted, start=1):
        current = [row]
        for column, right in enumerate(reference, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left != right),
            ))
        previous = current
    return 1.0 - previous[-1] / len(submitted)


def _parse_dob(value: str) -> date | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    # Enforce YYYY-MM-DD even on Python versions accepting other ISO formats.
    return parsed if parsed.isoformat() == value else None


def _normalize_id(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if any(char.isalnum() for char in value) else ""


def verify_identity_document(
    submitted_name: str,
    submitted_dob: str,
    submitted_id_number: str,
    reference_name: str,
    reference_dob: str,
    reference_id_number: str,
) -> dict:
    """Score submitted identity fields against caller-supplied trusted fields.

    Names use Unicode NFC, case folding, collapsed whitespace, and normalized
    Levenshtein similarity. DOBs must be real calendar dates in YYYY-MM-DD format and
    match exactly. IDs match exactly, preserving case, spacing, and punctuation.
    Missing, non-string, or invalid values earn zero for that field.

    Returns a dictionary with status="success", field_scores for name/dob/id_number,
    and match_confidence, all scores in [0, 1]. The total is
    0.30 * name + 0.30 * dob + 0.40 * id_number.
    """
    name_score = round(_name_similarity(
        _normalize_name(submitted_name), _normalize_name(reference_name)
    ), 4)
    document_dob = _parse_dob(submitted_dob)
    customer_dob = _parse_dob(reference_dob)
    dob_score = float(document_dob is not None and document_dob == customer_dob)
    document_id = _normalize_id(submitted_id_number)
    customer_id = _normalize_id(reference_id_number)
    id_score = float(bool(document_id) and document_id == customer_id)

    return {
        "status": "success",
        "match_confidence": round(0.30 * name_score + 0.30 * dob_score + 0.40 * id_score, 4),
        "field_scores": {"name": name_score, "dob": dob_score, "id_number": id_score},
    }
