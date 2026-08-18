from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx

from conftest import RunningBackend


ROOT = Path(__file__).resolve().parents[1]


def test_customer_risk_transfer_and_audit_flow(live_backend: RunningBackend):
    with httpx.Client(base_url=live_backend.base_url, timeout=5) as client:
        assert client.get("/health").json()["status"] == "ok"

        customer = {
            "customer_id": "CUST-INT-001",
            "full_name": "Integration Customer",
            "kyc_status": "verified",
            "risk_level": "low",
        }
        assert client.post("/customers", json=customer).status_code == 200
        for account_id, balance in (("INT-SOURCE", 1000.0), ("INT-DEST", 100.0)):
            response = client.post(
                "/accounts",
                json={
                    "account_id": account_id,
                    "customer_id": customer["customer_id"],
                    "account_type": "checking",
                    "balance": balance,
                    "currency": "USD",
                    "status": "active",
                },
            )
            assert response.status_code == 200, response.text

        decision = client.post(
            "/monitoring/evaluate_transaction",
            json={"from_account_id": "INT-SOURCE", "to_account_id": "INT-DEST", "amount": 250, "currency": "USD"},
        ).json()
        assert decision == {
            "risk_score": 0,
            "suspicious": False,
            "decision": "approve",
            "reasons": ["No active rule was triggered. Transaction is low risk."],
            "matched_rules": [],
        }

        transfer = client.post(
            "/banking-api/transfer",
            json={
                "from_account_id": "INT-SOURCE",
                "to_account_id": "INT-DEST",
                "amount": 250,
                "currency": "USD",
                "actor": "integration_test",
                "memo": "end-to-end transfer",
                "customer_id": customer["customer_id"],
            },
        )
        assert transfer.status_code == 200, transfer.text
        transaction = transfer.json()
        assert transaction["status"] == "completed"

        assert client.get("/accounts/INT-SOURCE").json()["balance"] == 750.0
        assert client.get("/accounts/INT-DEST").json()["balance"] == 350.0
        context = client.get(f"/customers/{customer['customer_id']}/context").json()
        assert transaction["transaction_id"] in {row["transaction_id"] for row in context["recent_transactions"]}

        updated = client.patch(
            f"/transactions/{transaction['transaction_id']}/status",
            json={"status": "APPROVED", "decision_reason": "integration review complete"},
        )
        assert updated.json()["decision_reason"] == "integration review complete"

    with sqlite3.connect(live_backend.db_path) as conn:
        audit_payload = conn.execute(
            "SELECT payload FROM audit_logs WHERE event_type = 'transfer_request' AND actor = 'integration_test'"
        ).fetchone()
        assert audit_payload is not None
        assert json.loads(audit_payload[0])["transaction_id"] == transaction["transaction_id"]


def test_mcp_operations_contract_uses_the_persistent_backend(live_backend: RunningBackend):
    today = date.today().isoformat()
    with httpx.Client(base_url=live_backend.base_url, timeout=5) as client:
        workload = client.get("/mid-office/workload/BR-001")
        assert workload.status_code == 200
        assert workload.json()["queue_length"] > 0

        rebalance = client.post(
            "/mid-office/rebalance",
            json={"from": "BR-002", "to": "BR-001", "count": 2},
        )
        assert rebalance.json()["status"] == "proposed"
        assert rebalance.json()["workload_balance_score"] == 0.7

        affinity = client.get("/mid-office/affinity/CUST-001")
        assert affinity.status_code == 200
        assert "High-Yield Savings" in affinity.json()["products"]

        report = client.post("/back-office/reconcile", params={"date": today})
        assert report.status_code == 200, report.text
        report_id = report.json()["report_id"]
        mismatches = client.get(f"/back-office/mismatches/{report_id}")
        assert mismatches.status_code == 200
        assert mismatches.json()["report_id"] == report_id

        correction = client.post(
            "/back-office/resolve/TXN-1001",
            json={"correction_amount": 10.5, "note": "integration correction"},
        )
        assert correction.json()["status"] == "recorded"

    with sqlite3.connect(live_backend.db_path) as conn:
        event_types = {
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM audit_logs WHERE event_type IN ('reconciliation_report', 'ledger_correction')"
            )
        }
        assert event_types == {"reconciliation_report", "ledger_correction"}


def test_init_and_smoke_scripts_run_against_real_processes(tmp_path, live_backend: RunningBackend):
    init_db_path = tmp_path / "initialized.db"
    init_env = os.environ.copy()
    init_env["DATABASE_URL"] = f"sqlite:///{init_db_path}"
    initialized = subprocess.run(
        [sys.executable, "scripts/init_db.py"],
        cwd=ROOT,
        env=init_env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert initialized.returncode == 0, initialized.stderr
    with sqlite3.connect(init_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] >= 6
        assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] >= 5

    smoke_env = os.environ.copy()
    smoke_env["BANKING_BACKEND_URL"] = live_backend.base_url
    smoke = subprocess.run(
        [sys.executable, "tests/smoke_test.py"],
        cwd=ROOT,
        env=smoke_env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert "Evaluate low risk transaction" in smoke.stdout
    assert "Evaluate high risk transaction" in smoke.stdout
