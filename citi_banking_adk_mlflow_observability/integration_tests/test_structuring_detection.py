import json
from datetime import datetime, timedelta

import pytest

from citi_banking_adk_mlflow_observability.bank_adk.infra.repository import repo
from citi_banking_adk_mlflow_observability.bank_adk.tools.aml_tools import detect_structuring, flag_structuring


@pytest.fixture(autouse=True, scope="module")
def _ensure_repo_initialized():
    """Ensures the shared sqlite-backed repository (and its manual_reviews table) exists."""
    repo.initialize()


def test_task5_synthetic_structuring_sequence_is_flagged():
    """
    Test Requirement: Feed a synthetic sequence that structures deposits (three $9,200
    deposits within 2 days) and confirm detect_structuring flags it.
    """
    now = datetime(2026, 1, 10, 9, 0, 0)
    transactions = [
        {"account_id": "ACC_STRUCTURE", "amount": 9200.0, "created_at": now},
        {"account_id": "ACC_STRUCTURE", "amount": 9200.0, "created_at": now + timedelta(hours=20)},
        {"account_id": "ACC_STRUCTURE", "amount": 9200.0, "created_at": now + timedelta(hours=40)},
    ]

    alerts = detect_structuring(transactions, threshold=10000.0, window_days=2)

    assert len(alerts) == 1
    assert alerts[0]["account_id"] == "ACC_STRUCTURE"
    assert alerts[0]["cumulative_amount"] == 27600.0
    assert alerts[0]["transaction_count"] == 3


def test_task5_single_deposit_is_not_flagged():
    """
    Test Requirement: A single $9,200 deposit should NOT trigger detect_structuring.
    """
    now = datetime(2026, 1, 10, 9, 0, 0)
    transactions = [{"account_id": "ACC_SINGLE", "amount": 9200.0, "created_at": now}]

    alerts = detect_structuring(transactions, threshold=10000.0, window_days=2)

    assert alerts == []


def test_task5_deposits_spread_over_weeks_are_not_flagged():
    """
    Test Requirement: The same three $9,200 deposits spread over 3 weeks should NOT
    trigger detect_structuring, since no 2-day window contains enough cumulative volume.
    """
    now = datetime(2026, 1, 10, 9, 0, 0)
    transactions = [
        {"account_id": "ACC_SPREAD", "amount": 9200.0, "created_at": now},
        {"account_id": "ACC_SPREAD", "amount": 9200.0, "created_at": now + timedelta(weeks=1)},
        {"account_id": "ACC_SPREAD", "amount": 9200.0, "created_at": now + timedelta(weeks=3)},
    ]

    alerts = detect_structuring(transactions, threshold=10000.0, window_days=2)

    assert alerts == []


def test_task5_flagged_structuring_invokes_submit_manual_review():
    """
    Test Requirement: Confirm a flagged case invokes submit_manual_review (which persists
    a row that downstream auditors can review).
    """
    now = datetime(2026, 1, 10, 9, 0, 0)
    transactions = [
        {"account_id": "ACC_MANUAL_REVIEW", "transaction_id": "TXN-A", "amount": 9200.0, "created_at": now},
        {"account_id": "ACC_MANUAL_REVIEW", "transaction_id": "TXN-B", "amount": 9200.0, "created_at": now + timedelta(hours=10)},
        {"account_id": "ACC_MANUAL_REVIEW", "transaction_id": "TXN-C", "amount": 9200.0, "created_at": now + timedelta(hours=20)},
    ]

    results = flag_structuring(transactions, threshold=10000.0, window_days=2)

    assert len(results) == 1
    review = results[0]["manual_review"]
    assert review["status"] == "open"
    assert review["case_type"] == "structuring"

    # Confirm the manual review case was actually persisted (not just returned in-memory).
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT * FROM manual_reviews WHERE case_id = ?", (review["case_id"],)
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["account_id"] == "ACC_MANUAL_REVIEW"
