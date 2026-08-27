def fetch_transaction_history(customer_id: str) -> dict:
    """Fetches the last 30 days of transaction history for AML review."""
    # Mock implementation
    return {"status": "success", "data": []}

def screen_against_sanctions(name: str, country: str) -> dict:
    """Checks customer details against global sanctions lists."""
    # Mock implementation
    return {"status": "clear", "match_score": 0.0}

AML_TOOLS = [fetch_transaction_history, screen_against_sanctions]