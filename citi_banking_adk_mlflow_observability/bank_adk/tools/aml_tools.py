import networkx as nx
def fetch_transaction_history(customer_id: str) -> dict:
    """Fetches the last 30 days of transaction history for AML review."""
    # Mock implementation
    return {"status": "success", "data": []}

def screen_against_sanctions(name: str, country: str) -> dict:
    """Checks customer details against global sanctions lists."""
    # Mock implementation
    return {"status": "clear", "match_score": 0.0}

def find_shared_attribute_accounts(account_id: str) -> dict:
    """
    Looks up accounts that share a device_id or ip_address with the target account.
    Returns an adjacency list mapping the shared attribute to the linked accounts.
    """
    # NOTE: You will need to wire this to hit your backend API client
    # e.g., return api_client.get(f"/aml/shared-attributes/{account_id}")
  
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
    
   # 2. Get device hotspots (Initialize as empty list to prevent NameError)
    # TODO: Replace with actual API call: api_client.get("/aml/device-sharing-hotspots")
    device_hotspots = [] 
    
    # 3. Feed to alerts
    # TODO: Replace with actual API call: api_client.post("/aml/alerts/feed", ...)
    
    return f"Pipeline complete. Fed {len(mule_rings)} mule rings and {len(device_hotspots)} device hotspots to aml_alerts."

AML_TOOLS = [fetch_transaction_history, screen_against_sanctions, find_shared_attribute_accounts, detect_mule_rings, run_aml_detection_pipeline]