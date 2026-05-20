import json
import operator
import uuid
from datetime import datetime, timedelta
from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from .models import Account, AuditLog, Customer, Rule, Transaction

OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

RISK_LOW_RISK_LIMIT = 40
RISK_MANUAL_REVIEW_LIMIT = 70

def _get_context(db: Session, from_account_id: str, to_account_id: str, amount: float, currency: str) -> dict:
    from_account = db.scalar(select(Account).where(Account.account_id == from_account_id))
    to_account = db.scalar(select(Account).where(Account.account_id == to_account_id))
    customer = None
    if from_account:
        customer = db.scalar(select(Customer).where(Customer.customer_id == from_account.customer_id))

    since = datetime.utcnow() - timedelta(days=1)
    recent_txs = db.scalars(
        select(Transaction).where(
            and_(Transaction.from_account_id == from_account_id, Transaction.created_at >= since)
        )
    ).all()

    return {
        "amount": amount,
        "currency": currency,
        "from_account_status": from_account.status if from_account else "missing",
        "to_account_status": to_account.status if to_account else "missing",
        "from_balance": from_account.balance if from_account else 0,
        "daily_tx_count": len(recent_txs),
        "daily_total_amount": sum(t.amount for t in recent_txs),
        "customer_risk_level": customer.risk_level if customer else "unknown",
        "kyc_status": customer.kyc_status if customer else "unknown",
    }

def _compare(value, op: str, threshold: str) -> bool:
    if op not in OPERATORS:
        return False
    try:
        numeric_value = float(value)
        numeric_threshold = float(threshold)
        return OPERATORS[op](numeric_value, numeric_threshold)
    except (TypeError, ValueError):
        return OPERATORS[op](str(value), threshold)

def evaluate_transaction(db: Session, from_account_id: str, to_account_id: str, amount: float, currency: str) -> dict:
    context = _get_context(db, from_account_id, to_account_id, amount, currency)
    rules = db.scalars(select(Rule).where(Rule.is_active == True)).all()  # noqa: E712

    score = 0
    reasons: list[str] = []
    matched_rules: list[str] = []

    for rule in rules:
        value = context.get(rule.field_name)
        if value is not None and _compare(value, rule.operator, rule.threshold_value):
            score += rule.risk_score_delta
            matched_rules.append(rule.rule_id)
            reasons.append(f"{rule.name}: {rule.description}")

    if context["from_account_status"] != "active":
        score += 100
        reasons.append("Source account is not active or does not exist.")
    if context["to_account_status"] != "active":
        score += 100
        reasons.append("Destination account is not active or does not exist.")
    if context["from_balance"] < amount:
        score += 100
        reasons.append("Insufficient funds.")
    if context["kyc_status"] != "verified":
        score += 50
        reasons.append("Customer KYC status is not verified.")

    score = min(score, 100)
    suspicious = score >= RISK_LOW_RISK_LIMIT
    if score >= RISK_MANUAL_REVIEW_LIMIT:
        decision = "manual_review"
    elif suspicious:
        decision = "suspicious_review"
    else:
        decision = "approve"

    if not reasons:
        reasons.append("No active rule was triggered. Transaction is low risk.")

    return {
        "risk_score": score,
        "suspicious": suspicious,
        "decision": decision,
        "reasons": reasons,
        "matched_rules": matched_rules,
    }

def execute_transfer(
    db: Session,
    from_account_id: str,
    to_account_id: str,
    amount: float,
    currency: str,
    actor: str,
    execute_if_low_risk: bool = True,
    memo: str | None = None,
    customer_id: str | None = None,
) -> Transaction:
    decision = evaluate_transaction(db, from_account_id, to_account_id, amount, currency)
    tx_id = f"TX-{uuid.uuid4().hex[:12].upper()}"

    tx_status = decision["decision"]
    if decision["decision"] == "approve" and execute_if_low_risk:
        from_account = db.scalar(select(Account).where(Account.account_id == from_account_id))
        to_account = db.scalar(select(Account).where(Account.account_id == to_account_id))
        if from_account and to_account:
            from_account.balance -= amount
            to_account.balance += amount
            tx_status = "completed"

    tx = Transaction(
        transaction_id=tx_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=amount,
        currency=currency,
        tx_type="transfer",
        status=tx_status,
        risk_score=decision["risk_score"],
        decision_reason=" | ".join(decision["reasons"]),
    )
    db.add(tx)
    db.add(AuditLog(event_type="transfer_request", actor=actor, payload=json.dumps({
        "transaction_id": tx_id,
        "decision": decision,
        "memo": memo or "",
        "customer_id": customer_id,
    })))
    db.commit()
    db.refresh(tx)
    return tx
