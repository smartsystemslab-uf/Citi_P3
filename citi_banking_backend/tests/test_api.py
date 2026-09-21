from datetime import date
from http import client
from uuid import uuid4


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def test_health_and_complete_crud_workflow(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "citi-p3-banking-api"}

    customer_id = _id("CUST")
    customer = {
        "customer_id": customer_id,
        "full_name": "Unit Test Customer",
        "email": "unit@example.com",
        "phone": "555-0100",
        "address": "Gainesville, FL",
        "kyc_status": "verified",
        "risk_level": "low",
    }
    created = client.post("/customers", json=customer)
    assert created.status_code == 200
    assert created.json()["customer_id"] == customer_id
    assert client.post("/customers", json=customer).status_code == 409
    assert client.get(f"/customers/{customer_id}").json()["full_name"] == "Unit Test Customer"
    assert client.get("/customers/DOES-NOT-EXIST").status_code == 404
    assert client.get("/customers/DOES-NOT-EXIST/context").status_code == 404

    account_id = _id("ACC")
    destination_id = _id("DEST")
    account = {
        "account_id": account_id,
        "customer_id": customer_id,
        "account_type": "checking",
        "balance": 1000.0,
        "currency": "USD",
        "status": "active",
    }
    destination = {**account, "account_id": destination_id, "balance": 10.0}
    assert client.post("/accounts", json={**account, "customer_id": "missing"}).status_code == 404
    assert client.post("/accounts", json=account).status_code == 200
    assert client.post("/accounts", json=destination).status_code == 200
    assert client.post("/accounts", json=account).status_code == 409
    assert client.get(f"/accounts/{account_id}").json()["balance"] == 1000.0
    assert client.get("/accounts/DOES-NOT-EXIST").status_code == 404
    assert len(client.get(f"/customers/{customer_id}/accounts").json()) == 2

    context = client.get(f"/customers/{customer_id}/context")
    assert context.status_code == 200
    assert {item["account_id"] for item in context.json()["accounts"]} == {account_id, destination_id}
    assert context.json()["recent_transactions"] == []
    assert client.get("/customers/NO-ACCOUNTS/transactions").json() == []

    payload = {
        "from_account_id": account_id,
        "to_account_id": destination_id,
        "amount": 125.5,
        "currency": "USD",
    }
    evaluated = client.post("/monitoring/evaluate_transaction", json=payload)
    assert evaluated.status_code == 200
    assert evaluated.json()["decision"] == "approve"
    assert client.post("/monitoring/evaluate_transaction", json={**payload, "amount": 0}).status_code == 422

    transfer = client.post(
        "/banking-api/transfer",
        json={**payload, "actor": "unit-test", "memo": "test", "customer_id": customer_id},
    )
    assert transfer.status_code == 200
    transaction = transfer.json()
    assert transaction["status"] == "completed"
    assert transaction["amount"] == 125.5
    transaction_id = transaction["transaction_id"]
    assert client.get(f"/transactions/{transaction_id}").json()["status"] == "completed"
    assert client.get("/transactions/DOES-NOT-EXIST").status_code == 404
    assert any(item["transaction_id"] == transaction_id for item in client.get("/transactions?limit=5").json())
    assert any(
        item["transaction_id"] == transaction_id
        for item in client.get(f"/customers/{customer_id}/transactions?limit=5").json()
    )

    status_update = {"status": "reviewed", "decision_reason": "verified by unit test"}
    updated = client.patch(f"/transactions/{transaction_id}/status", json=status_update)
    assert updated.status_code == 200
    assert updated.json()["status"] == "reviewed"
    assert updated.json()["decision_reason"] == "verified by unit test"
    assert client.patch("/transactions/missing/status", json=status_update).status_code == 404


def test_policy_and_rule_endpoints(client):
    policy_id = _id("POL")
    policy = {
        "policy_id": policy_id,
        "title": "Unique Unit Testing Policy",
        "category": "Testing",
        "severity": "low",
        "content": "UnitKeyword applies to deterministic tests.",
        "is_active": True,
    }
    assert client.post("/policies", json=policy).status_code == 200
    assert client.post("/policies", json=policy).status_code == 409
    assert any(item["policy_id"] == policy_id for item in client.get("/policies?category=Testing").json())
    assert any(item["policy_id"] == policy_id for item in client.get("/policies?active_only=false").json())
    assert any(item["policy_id"] == policy_id for item in client.get("/policies/search?q=UnitKeyword").json())
    assert any(item["policy_id"] == policy_id for item in client.get("/policies/search").json())

    rule_id = _id("RULE")
    rule = {
        "rule_id": rule_id,
        "name": "Unit amount rule",
        "category": "Testing",
        "field_name": "amount",
        "operator": ">=",
        "threshold_value": "999999",
        "risk_score_delta": 10,
        "action": "review",
        "description": "Only used by unit tests",
        "is_active": True,
    }
    assert client.post("/rules", json=rule).status_code == 200
    assert client.post("/rules", json=rule).status_code == 409
    assert any(item["rule_id"] == rule_id for item in client.get("/rules?category=Testing").json())
    assert any(item["rule_id"] == rule_id for item in client.get("/rules?active_only=false").json())


def test_high_risk_and_nonexecuting_transfer_paths(client):
    high = client.post(
        "/monitoring/evaluate_transaction",
        json={
            "from_account_id": "CHK-9001",
            "to_account_id": "EXT-998",
            "amount": 60000,
            "currency": "USD",
        },
    )
    assert high.status_code == 200
    assert high.json()["decision"] == "manual_review"
    assert high.json()["risk_score"] == 100

    held = client.post(
        "/banking-api/transfer",
        json={
            "from_account_id": "CHK-002",
            "to_account_id": "EXT-998",
            "amount": 10,
            "execute_if_low_risk": False,
        },
    )
    assert held.status_code == 200
    assert held.json()["status"] == "approve"


def test_mid_and_back_office_mcp_contract_endpoints(client):
    busy = client.get("/mid-office/workload/BR-001")
    fallback = client.get("/mid-office/workload/UNKNOWN")
    assert busy.json()["queue_length"] == 18
    assert fallback.json()["queue_length"] == 8

    proposed = client.post(
        "/mid-office/rebalance",
        json={"from": "BR-002", "to": "BR-001", "count": 10},
    )
    assert proposed.json()["workload_balance_score"] == 1.0
    assert client.post(
        "/mid-office/rebalance",
        json={"from": "BR-002", "to": "BR-001", "count": 0},
    ).status_code == 422

    high_balance = client.get("/mid-office/affinity/CUST-001")
    low_balance = client.get("/mid-office/affinity/CUST-002")
    assert "High-Yield Savings" in high_balance.json()["products"]
    assert "Automatic Savings" in low_balance.json()["products"]
    assert client.get("/mid-office/affinity/missing").status_code == 404

    review_report = client.post("/back-office/reconcile", params={"date": date.today().isoformat()})
    clean_report = client.post("/back-office/reconcile", params={"date": "1999-01-01"})
    assert review_report.status_code == 200
    assert review_report.json()["status"] in {"review_required", "reconciled"}
    assert clean_report.json()["status"] == "reconciled"

    report_id = review_report.json()["report_id"]
    mismatch = client.get(f"/back-office/mismatches/{report_id}")
    assert mismatch.json()["report_id"] == report_id
    assert client.get("/back-office/mismatches/missing").status_code == 404

    correction = client.post(
        "/back-office/resolve/TXN-1001",
        json={"correction_amount": 4.25, "note": "unit correction"},
    )
    assert correction.json()["status"] == "recorded"
    assert client.post(
        "/back-office/resolve/missing",
        json={"correction_amount": 4.25, "note": "unit correction"},
    ).status_code == 404

def test_aml_verify_identity_endpoint(client):
    exact = client.post(
        "/aml/verify-identity",
        json={
            "customer_id": "CUST-001",
            "submitted_name": "Ava Patel",
            "submitted_dob": "2000-02-29",
            "submitted_id_number": "FL-001234",
        },
    )


    assert exact.status_code == 200

    body = exact.json()

    assert body["customer_id"] == "CUST-001"
    assert body["match_confidence"] == 1.0
    assert body["field_scores"] == {
        "name": 1.0,
        "dob": 1.0,
        "id_number": 1.0,
    }

    stored = client.get("/customers/CUST-001")
    assert stored.status_code == 200
    assert stored.json()["identity_match_confidence"] == 1.0

    mismatch = client.post(
        "/aml/verify-identity",
        json={
            "customer_id": "CUST-001",
            "submitted_name": "Wrong Person",
            "submitted_dob": "1999-01-01",
            "submitted_id_number": "WRONG-ID",
        },
    )

    assert mismatch.status_code == 200
    assert mismatch.json()["match_confidence"] < 0.5

    mismatch_score = mismatch.json()["match_confidence"]

    stored_after_mismatch = client.get("/customers/CUST-001")

    assert stored_after_mismatch.status_code == 200
    assert (
        stored_after_mismatch.json()["identity_match_confidence"]
        == mismatch_score
    )

    missing = client.post(
        "/aml/verify-identity",
        json={
            "customer_id": "DOES-NOT-EXIST",
            "submitted_name": "Ava Patel",
            "submitted_dob": "2000-02-29",
            "submitted_id_number": "FL-001234",
        },
    )

    assert missing.status_code == 404

def test_aml_alert_list_and_filters(client):
    import sqlite3

    from app.aml_alert_repository import AML_DB_PATH

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.execute("DELETE FROM aml_alerts")

        conn.executemany(
            """
            INSERT INTO aml_alerts (
                alert_id,
                customer_id,
                alert_type,
                detection_window_params,
                cumulative_total,
                threshold,
                status,
                raw_payload,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "ALT-TEST001",
                    "CUST-001",
                    "structuring",
                    "7d",
                    12000.0,
                    10000.0,
                    "open",
                    "{}",
                    "2026-09-19T12:00:00+00:00",
                ),
                (
                    "ALT-TEST002",
                    "CUST-002",
                    "structuring",
                    "7d",
                    15000.0,
                    10000.0,
                    "open",
                    "{}",
                    "2026-09-19T13:00:00+00:00",
                ),
                (
                    "ALT-TEST003",
                    "CUST-001",
                    "sanctions",
                    "single",
                    5000.0,
                    0.0,
                    "cleared",
                    "{}",
                    "2026-09-19T14:00:00+00:00",
                ),
            ],
        )

    response = client.get("/aml/alerts")
    assert response.status_code == 200
    assert len(response.json()) == 3

    response = client.get("/aml/alerts?status=open")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(alert["status"] == "open" for alert in response.json())

    response = client.get("/aml/alerts?customer_id=CUST-001")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(
        alert["customer_id"] == "CUST-001"
        for alert in response.json()
    )

    response = client.get("/aml/alerts?alert_type=structuring")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(
        alert["alert_type"] == "structuring"
        for alert in response.json()
    )

    response = client.get(
        "/aml/alerts"
        "?customer_id=CUST-001"
        "&status=open"
        "&alert_type=structuring"
    )

    assert response.status_code == 200
    alerts = response.json()

    assert len(alerts) == 1
    assert alerts[0]["alert_id"] == "ALT-TEST001"

def test_aml_alert_status_transitions(client):
    import sqlite3

    from app.aml_alert_repository import AML_DB_PATH

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.execute("DELETE FROM aml_alerts")

        conn.executemany(
            """
            INSERT INTO aml_alerts (
                alert_id,
                customer_id,
                alert_type,
                detection_window_params,
                cumulative_total,
                threshold,
                status,
                raw_payload,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "ALT-TRANSITION-1",
                    "CUST-001",
                    "structuring",
                    "7d",
                    12000.0,
                    10000.0,
                    "open",
                    "{}",
                    "2026-09-19T12:00:00+00:00",
                ),
                (
                    "ALT-TRANSITION-2",
                    "CUST-002",
                    "structuring",
                    "7d",
                    14000.0,
                    10000.0,
                    "open",
                    "{}",
                    "2026-09-19T13:00:00+00:00",
                ),
            ],
        )

    response = client.patch(
        "/aml/alerts/ALT-TRANSITION-1/status",
        json={"status": "in-investigation"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "in-investigation"

    response = client.patch(
        "/aml/alerts/ALT-TRANSITION-1/status",
        json={"status": "cleared"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cleared"

    response = client.patch(
        "/aml/alerts/ALT-TRANSITION-2/status",
        json={"status": "cleared"},
    )

    assert response.status_code == 409
    assert "open -> cleared" in response.json()["detail"]

    response = client.patch(
        "/aml/alerts/DOES-NOT-EXIST/status",
        json={"status": "in-investigation"},
    )

    assert response.status_code == 404

def test_aml_alert_status_changes_are_logged(client):
    import sqlite3

    from app.aml_alert_repository import AML_DB_PATH

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.execute("DELETE FROM aml_alerts")
        conn.execute("DROP TABLE IF EXISTS aml_alert_status_history")

        conn.execute(
            """
            INSERT INTO aml_alerts (
                alert_id,
                customer_id,
                alert_type,
                detection_window_params,
                cumulative_total,
                threshold,
                status,
                raw_payload,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ALT-HISTORY-1",
                "CUST-001",
                "structuring",
                "7d",
                12000.0,
                10000.0,
                "open",
                "{}",
                "2026-09-19T12:00:00+00:00",
            ),
        )

    response = client.patch(
        "/aml/alerts/ALT-HISTORY-1/status",
        json={"status": "in-investigation"},
    )

    assert response.status_code == 200

    response = client.patch(
        "/aml/alerts/ALT-HISTORY-1/status",
        json={"status": "cleared"},
    )

    assert response.status_code == 200

    # This transition is invalid and must not create history.
    response = client.patch(
        "/aml/alerts/ALT-HISTORY-1/status",
        json={"status": "open"},
    )

    assert response.status_code == 409

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        history = conn.execute(
            """
            SELECT *
            FROM aml_alert_status_history
            WHERE alert_id = ?
            ORDER BY changed_at ASC
            """,
            ("ALT-HISTORY-1",),
        ).fetchall()

    assert len(history) == 2

    assert history[0]["previous_status"] == "open"
    assert history[0]["new_status"] == "in-investigation"
    assert history[0]["changed_at"]

    assert history[1]["previous_status"] == "in-investigation"
    assert history[1]["new_status"] == "cleared"
    assert history[1]["changed_at"]

def test_sar_draft_human_review_preserves_original(client):
    created = client.post(
        "/aml/sar-drafts",
        json={
            "case_id": "CASE-001",
            "original_draft": "Original generated SAR narrative.",
        }, 
    )

    assert created.status_code == 200

    draft_id = created.json()["draft_id"]

    fetched = client.get(f"/aml/sar-drafts/{draft_id}")

    assert fetched.status_code == 200
    assert (
        fetched.json()["original_draft"]
        == "Original generated SAR narrative."
    )

    reviewed = client.patch(
        f"/aml/sar-drafts/{draft_id}/review",
        json={
            "edited_draft": "Human-edited SAR narrative.",
            "reviewer": "reviewer-001",
            "approved": True,
        },
    )

    assert reviewed.status_code == 200

    body = reviewed.json()

    assert body["original_draft"] == "Original generated SAR narrative."
    assert body["edited_draft"] == "Human-edited SAR narrative."
    assert body["reviewer"] == "reviewer-001"
    assert body["approved"] is True
    assert body["reviewed_at"] is not None

    fetched_again = client.get(f"/aml/sar-drafts/{draft_id}")
    stored = fetched_again.json()

    assert stored["original_draft"] == "Original generated SAR narrative."
    assert stored["edited_draft"] == "Human-edited SAR narrative."

