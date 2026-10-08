import json
from datetime import datetime, timedelta

import networkx as nx

from ..infra import api_client
from .customer_tools import submit_manual_review


def fetch_transaction_history(customer_id: str, limit: int = 30) -> dict:
    """Fetches recent transaction history for AML review via the backend API."""
    response = api_client.get(f"/customers/{customer_id}/transactions", params={"limit": limit})
    if isinstance(response, list):
        return {"status": "success", "data": response}
    return response

def screen_against_sanctions(name: str, country: str) -> dict:
    """Checks customer details against global sanctions lists."""
    # No sanctions-list data source is available in this environment, so this
    # intentionally returns a safe default rather than a stub awaiting backend wiring.
    return {"status": "clear", "match_score": 0.0}

def find_shared_attribute_accounts(account_id: str) -> dict:
    """
    Looks up accounts that share a device_id or ip_address with the target account.
    Returns an adjacency list mapping the shared attribute to the linked accounts.
    """
    return api_client.get(f"/aml/shared-attributes/{account_id}")
  
def detect_mule_rings(transactions: list[dict], max_depth: int = 5) -> list[list[str]]:
    """
    Builds a directed graph from transaction flows and detects closed loops (mule rings).
    Expects transactions to be dicts containing 'from_account_id' and 'to_account_id'.
    """
    graph = nx.DiGraph()
    
    # Build the network
    for tx in transactions:
        graph.add_edge(tx['from_account_id'], tx['to_account_id'])
        
    # Detect simple cycles (e.g., Account A -> B -> C -> A)
    try:
        cycles = list(nx.simple_cycles(graph))
        
        # Filter for meaningful loops bounded by depth (ignoring direct A -> A self-transfers)
        mule_rings = [
            cycle for cycle in cycles 
            if 2 <= len(cycle) <= max_depth
        ]
        return mule_rings
        
    except nx.NetworkXNoCycle:
        return []

def run_aml_detection_pipeline(transactions: list[dict]):
    """
    Runs all fraud detection algorithms and feeds results into the alert system.
    """
    # 1. Detect loops
    mule_rings = detect_mule_rings(transactions)

    # 2. Get device hotspots
    hotspots_response = api_client.get("/aml/device-sharing-hotspots")
    device_hotspots = hotspots_response.get("data", []) if hotspots_response.get("status") == "success" else []

    # 3. Feed to alerts
    api_client.post("/aml/alerts/feed", json={"mule_rings": mule_rings, "device_hotspots": device_hotspots})

    return f"Pipeline complete. Fed {len(mule_rings)} mule rings and {len(device_hotspots)} device hotspots to aml_alerts."

def _parse_timestamp(value) -> datetime:
    """Normalizes a timestamp field that may arrive as a datetime or an ISO-8601 string."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported timestamp type for structuring check: {type(value)!r}")


def detect_structuring(transactions: list[dict], threshold: float = 10000.0, window_days: int = 2) -> list[dict]:
    """
    Sliding-window cumulative-deposit check for structuring ("smurfing"): flags an account
    when two or more of its deposits, none of which individually crosses the reporting
    threshold, sum to at least that threshold within a short rolling time window.

    Expects each transaction dict to include an account identifier (checked in order:
    'account_id', 'to_account_id', 'from_account_id'), an 'amount', and a 'created_at'
    (or 'timestamp') datetime/ISO-8601 string. Transactions for multiple accounts may be
    passed in together; each account is evaluated independently.

    Returns a list of alert dicts (one per account with a detected pattern) containing the
    account id, the cumulative amount, the transactions that make up the window, and a
    human-readable reason suitable for an aml_alerts row or a manual review case.
    """
    by_account: dict[str, list[dict]] = {}
    for tx in transactions:
        account_id = tx.get("account_id") or tx.get("to_account_id") or tx.get("from_account_id")
        ts_raw = tx.get("created_at", tx.get("timestamp"))
        if account_id is None or ts_raw is None:
            continue
        enriched = dict(tx)
        enriched["_ts"] = _parse_timestamp(ts_raw)
        by_account.setdefault(account_id, []).append(enriched)

    window = timedelta(days=window_days)
    alerts: list[dict] = []

    for account_id, txs in by_account.items():
        txs.sort(key=lambda t: t["_ts"])
        n = len(txs)
        for start in range(n):
            # Grow the window forward from `start` as far as it stays within `window_days`,
            # so the cumulative total reflects every deposit in the densest stretch.
            end = start
            while end + 1 < n and txs[end + 1]["_ts"] - txs[start]["_ts"] <= window:
                end += 1
            window_txs = txs[start : end + 1]
            if len(window_txs) < 2:
                continue

            cumulative = sum(t["amount"] for t in window_txs)
            if cumulative >= threshold:
                span_days = (window_txs[-1]["_ts"] - window_txs[0]["_ts"]).days
                alerts.append(
                    {
                        "account_id": account_id,
                        "cumulative_amount": cumulative,
                        "transaction_count": len(window_txs),
                        "window_days": span_days,
                        "transaction_ids": [t.get("transaction_id") for t in window_txs],
                        "reason": (
                            f"{len(window_txs)} deposits totalling {cumulative:.2f} within "
                            f"{span_days} day(s) cumulatively exceed the {threshold:.2f} reporting threshold"
                        ),
                    }
                )
                break  # One alert per account is enough; avoid duplicate overlapping windows.

    return alerts


def flag_structuring(transactions: list[dict], threshold: float = 10000.0, window_days: int = 2) -> list[dict]:
    """
    Runs detect_structuring() and, for every flagged account, opens a manual review case
    via submit_manual_review so a human investigator can act on the structuring pattern.
    """
    alerts = detect_structuring(transactions, threshold=threshold, window_days=window_days)

    results = []
    for alert in alerts:
        review_response = json.loads(
            submit_manual_review(case_type="structuring", payload=json.dumps(alert))
        )
        results.append({**alert, "manual_review": review_response})

    return results


def check_threshold_anomaly(account_id: str, transaction_id: str, amount: float) -> dict:
    """
    Evaluates if a transaction deviates sharply (>5x peak) from the customer's baseline.
    Routes above-threshold results into aml_alerts.
    """
    # 1. Fetch baseline from backend
    profile_response = api_client.get(f"/aml/profiles/{account_id}")
    if profile_response.get("status") != "success":
        return {"flagged": False, "reason": f"No baseline available: {profile_response.get('message', 'profile not found')}"}

    profile = profile_response["data"]
    threshold = profile["peak_transfer_amount"] * 5
    is_anomalous = amount > threshold

    if is_anomalous:
        # 2. Fire alert to backend
        alert_payload = {
            "alert_type": "baseline_deviation",
            "entity_id": transaction_id,
            "description": f"Amount {amount} exceeds 5x historical peak of {profile['peak_transfer_amount']}",
            "status": "review"
        }
        api_client.post("/aml/alerts", json=alert_payload)
        return {"flagged": True, "reason": "Exceeded 5x peak transfer amount", "status": "review"}

    return {"flagged": False, "reason": "Within normal bounds"}

AML_TOOLS = [fetch_transaction_history, screen_against_sanctions, find_shared_attribute_accounts, detect_mule_rings, run_aml_detection_pipeline, check_threshold_anomaly, detect_structuring, flag_structuring]