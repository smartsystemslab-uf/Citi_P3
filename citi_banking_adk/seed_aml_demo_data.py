"""One-off local seed script for PI demo recording.

Inserts two synthetic customers into the real banking.db (bank_adk/.runtime/banking.db)
so detect_structuring and find_mule_rings_for_customer have real data to trigger against
during a live --zero-trust demo run, instead of coming back clean. Safe to re-run
(customer/account rows use INSERT OR REPLACE); safe to delete afterwards — this file is
not part of the application and is not imported by anything else.

Usage (from citi_banking_adk/, with the venv active):
    python3 seed_aml_demo_data.py
"""
import json
from datetime import datetime, timedelta, timezone

from bank_adk.infra.repository import repo

repo.initialize()


def seed_structuring_case():
    customer_id = "CUST-STRUCT"
    account_id = "CHK-STRUCT"

    with repo.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                "Renee Castillo",
                "medium",
                "verified",
                "Consultant",
                "US",
                json.dumps({"behavior_notes": ["AML demo customer - structuring case"]}),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, customer_id, "checking", "USD", 0.0, "active"),
        )

    # Three deposits just under the $10k CTR line, all within the same 7-day window,
    # summing to $27,600 — a textbook structuring pattern. Mirrors the pattern already
    # verified by tests/test_repository_and_tools.py::test_structuring_detection_positive_case.
    deposits = [(9200.0, 0), (9200.0, 1), (9200.0, 2)]
    for idx, (amount, day_offset) in enumerate(deposits):
        created_at = (datetime.now(timezone.utc) - timedelta(days=day_offset)).isoformat()
        with repo.connect() as conn:
            conn.execute(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"TXN-STRUCT-{idx}",
                    customer_id,
                    f"EXT-CASH-{idx}",
                    account_id,
                    amount,
                    "USD",
                    "branch_teller",
                    "cash_deposit",
                    "Cash Deposit",
                    "posted",
                    json.dumps({"deposit_note": "AML demo structuring case"}),
                    created_at,
                ),
            )

    print(f"Seeded structuring case: customer_id={customer_id}, 3 deposits of $9,200 over 3 days")


def seed_mule_ring_case():
    customer_id = "CUST-MULE"
    home_account = "ACCT-MULE-1"

    with repo.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                "Omar Delgado",
                "high",
                "verified",
                "Unemployed",
                "US",
                json.dumps({"behavior_notes": ["AML demo customer - mule ring case"]}),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
            (home_account, customer_id, "checking", "USD", 0.0, "active"),
        )

    # A closed loop: MULE-1 -> MULE-2 -> MULE-3 -> back to MULE-1.
    # find_mule_rings_for_customer only sees transactions attributed to this customer_id,
    # so all three hops are recorded under CUST-MULE, exactly as an investigator pulling
    # one customer's transaction history would see them.
    ring = [
        ("ACCT-MULE-1", "ACCT-MULE-2", 4000.0),
        ("ACCT-MULE-2", "ACCT-MULE-3", 4000.0),
        ("ACCT-MULE-3", "ACCT-MULE-1", 4000.0),
    ]
    now = datetime.now(timezone.utc)
    for idx, (source, destination, amount) in enumerate(ring):
        created_at = (now - timedelta(hours=idx)).isoformat()
        with repo.connect() as conn:
            conn.execute(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"TXN-MULE-{idx}",
                    customer_id,
                    source,
                    destination,
                    amount,
                    "USD",
                    "wire_transfer",
                    "outgoing_transfer",
                    "Shell Transfer",
                    "posted",
                    json.dumps({"deposit_note": "AML demo mule ring case"}),
                    created_at,
                ),
            )

    print(f"Seeded mule ring case: customer_id={customer_id}, 3-hop cycle MULE-1 -> MULE-2 -> MULE-3 -> MULE-1")


if __name__ == "__main__":
    seed_structuring_case()
    seed_mule_ring_case()
    print("Done. Demo prompts:")
    print('  python3 run_cli.py --zero-trust --message "investigate CUST-STRUCT for structuring"')
    print('  python3 run_cli.py --zero-trust --message "check if CUST-MULE is part of a money mule network"')
