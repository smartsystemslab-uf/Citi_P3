import json
import os
import httpx

BASE_URL = os.getenv("BANKING_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2))

def main():
    with httpx.Client(timeout=10) as client:
        pretty("Health", client.get(f"{BASE_URL}/health").json())
        pretty("Customer CUST-001 context", client.get(f"{BASE_URL}/customers/CUST-001/context").json())
        pretty("Policy search AML", client.get(f"{BASE_URL}/policies/search", params={"q": "AML"}).json())
        low_risk_payload = {
            "from_account_id": "CHK-002",
            "to_account_id": "EXT-998",
            "amount": 100.00,
            "currency": "USD"
        }
        pretty("Evaluate low risk transaction", client.post(f"{BASE_URL}/monitoring/evaluate_transaction", json=low_risk_payload).json())
        pretty("Execute low risk transfer", client.post(f"{BASE_URL}/banking-api/transfer", json=low_risk_payload).json())
        high_risk_payload = {
            "from_account_id": "CHK-9001",
            "to_account_id": "EXT-998",
            "amount": 60000.00,
            "currency": "USD"
        }
        pretty("Evaluate high risk transaction", client.post(f"{BASE_URL}/monitoring/evaluate_transaction", json=high_risk_payload).json())


if __name__ == "__main__":
    main()
