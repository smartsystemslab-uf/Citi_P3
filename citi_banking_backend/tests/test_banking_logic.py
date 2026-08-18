from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import banking_logic
from app.database import Base
from app.models import Account, AuditLog, Customer, Rule, Transaction


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'logic.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_compare_supports_numeric_text_and_invalid_operators():
    assert banking_logic._compare(5, ">", "4")
    assert banking_logic._compare(5, ">=", "5")
    assert banking_logic._compare(4, "<", "5")
    assert banking_logic._compare(5, "<=", "5")
    assert banking_logic._compare("high", "==", "high")
    assert banking_logic._compare("low", "!=", "high")
    assert not banking_logic._compare(1, "contains", "1")


def test_evaluate_transaction_decision_branches(tmp_path):
    db = _session(tmp_path)
    try:
        db.add(Customer(customer_id="C1", full_name="One", kyc_status="verified", risk_level="low"))
        db.add(Customer(customer_id="C2", full_name="Two", kyc_status="verified", risk_level="low"))
        db.add(Account(account_id="A1", customer_id="C1", balance=1000, status="active"))
        db.add(Account(account_id="A2", customer_id="C2", balance=100, status="active"))
        db.add(
            Rule(
                rule_id="R1",
                name="medium amount",
                category="test",
                field_name="amount",
                operator=">=",
                threshold_value="500",
                risk_score_delta=45,
                description="amount is elevated",
                is_active=True,
            )
        )
        db.commit()

        low = banking_logic.evaluate_transaction(db, "A1", "A2", 10, "USD")
        assert low == {
            "risk_score": 0,
            "suspicious": False,
            "decision": "approve",
            "reasons": ["No active rule was triggered. Transaction is low risk."],
            "matched_rules": [],
        }

        suspicious = banking_logic.evaluate_transaction(db, "A1", "A2", 500, "USD")
        assert suspicious["decision"] == "suspicious_review"
        assert suspicious["matched_rules"] == ["R1"]

        insufficient = banking_logic.evaluate_transaction(db, "A1", "A2", 2000, "USD")
        assert insufficient["decision"] == "manual_review"
        assert "Insufficient funds." in insufficient["reasons"]

        missing = banking_logic.evaluate_transaction(db, "missing", "also-missing", 1, "USD")
        assert missing["risk_score"] == 100
        assert missing["decision"] == "manual_review"
    finally:
        db.close()


def test_execute_transfer_posts_balances_transaction_and_audit(tmp_path, monkeypatch):
    db = _session(tmp_path)
    try:
        db.add(Customer(customer_id="C1", full_name="One", kyc_status="verified", risk_level="low"))
        db.add(Customer(customer_id="C2", full_name="Two", kyc_status="verified", risk_level="low"))
        db.add(Account(account_id="A1", customer_id="C1", balance=100, status="active"))
        db.add(Account(account_id="A2", customer_id="C2", balance=10, status="active"))
        db.commit()

        monkeypatch.setattr(
            banking_logic,
            "evaluate_transaction",
            lambda *args, **kwargs: {
                "risk_score": 0,
                "decision": "approve",
                "reasons": ["ok"],
                "matched_rules": [],
                "suspicious": False,
            },
        )
        tx = banking_logic.execute_transfer(db, "A1", "A2", 25, "USD", "tester", memo="memo", customer_id="C1")
        assert tx.status == "completed"
        assert db.query(Account).filter_by(account_id="A1").one().balance == 75
        assert db.query(Account).filter_by(account_id="A2").one().balance == 35
        assert db.query(Transaction).count() == 1
        audit = db.query(AuditLog).one()
        assert '"memo": "memo"' in audit.payload

        held = banking_logic.execute_transfer(db, "A1", "A2", 5, "USD", "tester", execute_if_low_risk=False)
        assert held.status == "approve"

        missing_destination = banking_logic.execute_transfer(db, "A1", "NOPE", 5, "USD", "tester")
        assert missing_destination.status == "approve"
        assert db.query(Account).filter_by(account_id="A1").one().balance == 75
    finally:
        db.close()
