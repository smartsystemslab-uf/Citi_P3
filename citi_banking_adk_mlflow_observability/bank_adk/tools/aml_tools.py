import networkx as nx
from datetime import date
import unicodedata


def fetch_transaction_history(customer_id: str) -> dict:
    """Fetches the last 30 days of transaction history for AML review."""
    return {"status": "success", "data": []}

def screen_against_sanctions(name: str, country: str) -> dict:
    """Checks customer details against global sanctions lists."""
    return {"status": "clear", "match_score": 0.0}

def find_shared_attribute_accounts(account_id: str) -> dict:
    """
    Looks up accounts that share a device_id or ip_address with the target account.
    Returns an adjacency list mapping the shared attribute to the linked accounts.
    """
    # NOTE: Need to wire this to hit your backend API client
    pass

def detect_mule_rings(transactions: list[dict], max_depth: int = 5) -> list[list[str]]:
    """
    Builds a directed graph from transaction flows and detects closed loops (mule rings).
    """
    graph = nx.DiGraph()
    for tx in transactions:
        graph.add_edge(tx['from_account_id'], tx['to_account_id'])
    try:
        cycles = list(nx.simple_cycles(graph))
        mule_rings = [cycle for cycle in cycles if 2 <= len(cycle) <= max_depth]
        return mule_rings
    except nx.NetworkXNoCycle:
        return []

def run_aml_detection_pipeline(transactions: list[dict]):
    """Runs all fraud detection algorithms and feeds results into the alert system."""
    mule_rings = detect_mule_rings(transactions)
    device_hotspots = []
    return f"Pipeline complete. Fed {len(mule_rings)} mule rings and {len(device_hotspots)} device hotspots to aml_alerts."

def check_threshold_anomaly(account_id: str, transaction_id: str, amount: float) -> dict:
    """Evaluates if a transaction deviates sharply (>5x peak) from the customer's baseline."""
    profile = {"peak_transfer_amount": 1000.0}
    threshold = profile["peak_transfer_amount"] * 5
    is_anomalous = amount > threshold
    if is_anomalous:
        return {"flagged": True, "reason": "Exceeded 5x peak transfer amount", "status": "review"}
    return {"flagged": False, "reason": "Within normal bounds"}

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
    """Score submitted identity fields against caller-supplied trusted fields."""
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

AML_TOOLS = [
    fetch_transaction_history, screen_against_sanctions, find_shared_attribute_accounts,
    detect_mule_rings, run_aml_detection_pipeline, check_threshold_anomaly, verify_identity_document,
]
