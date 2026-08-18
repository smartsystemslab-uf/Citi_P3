import json
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from fastapi import Depends, FastAPI, HTTPException, Query
from .database import get_db
from .models import Account, AuditLog, Customer, Policy, Rule, Transaction
from .schemas import (
    AccountCreate, AccountOut, CustomerCreate, CustomerOut, PolicyCreate, PolicyOut,
    RuleCreate, RuleOut, RiskDecision, TransactionEvaluateRequest, TransactionOut,
    LedgerCorrectionRequest, StaffingAdjustmentRequest, TransferRequest,
    TransactionStatusUpdate,
)
from .banking_logic import evaluate_transaction, execute_transfer
from .seed import init_db

app = FastAPI(
    title="Citi P3 Banking API",
    description="Minimal banking backend for user context, policy/rules database, and transaction APIs used by multi-agent workflows.",
    version="0.1.0",
)

@app.on_event("startup")
def startup_event():
    init_db(seed=True)

@app.get("/health")
def health():
    return {"status": "ok", "service": "citi-p3-banking-api"}

def _customer_context_dict(customer: Customer, db: Session) -> dict:
    accounts = db.scalars(select(Account).where(Account.customer_id == customer.customer_id)).all()
    account_ids = [account.account_id for account in accounts]
    txs = []
    if account_ids:
        txs = db.scalars(
            select(Transaction)
            .where(Transaction.from_account_id.in_(account_ids))
            .order_by(Transaction.created_at.desc())
            .limit(10)
        ).all()

    return {
        "customer_id": customer.customer_id,
        "full_name": customer.full_name,
        "email": customer.email,
        "phone": customer.phone,
        "address": customer.address,
        "kyc_status": customer.kyc_status,
        "risk_level": customer.risk_level,
        "accounts": [
            {
                "account_id": account.account_id,
                "account_type": account.account_type,
                "balance": account.balance,
                "currency": account.currency,
                "status": account.status,
            }
            for account in accounts
        ],
        "recent_transactions": [
            {
                "transaction_id": tx.transaction_id,
                "from_account_id": tx.from_account_id,
                "to_account_id": tx.to_account_id,
                "amount": tx.amount,
                "currency": tx.currency,
                "tx_type": tx.tx_type,
                "status": tx.status,
                "risk_score": tx.risk_score,
                "decision_reason": tx.decision_reason,
                "created_at": tx.created_at,
            }
            for tx in txs
        ],
    }


@app.post("/customers", response_model=CustomerOut)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    exists = db.scalar(select(Customer).where(Customer.customer_id == payload.customer_id))
    if exists:
        raise HTTPException(status_code=409, detail="customer_id already exists")
    customer = Customer(**payload.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer

@app.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer

@app.get("/customers/{customer_id}/accounts", response_model=list[AccountOut])
def list_customer_accounts(customer_id: str, db: Session = Depends(get_db)):
    return db.scalars(select(Account).where(Account.customer_id == customer_id)).all()


@app.get("/customers/{customer_id}/context")
def get_customer_context(customer_id: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return _customer_context_dict(customer, db)


@app.get("/customers/{customer_id}/transactions", response_model=list[TransactionOut])
def list_customer_transactions(customer_id: str, limit: int = Query(default=5, le=50), db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).where(Account.customer_id == customer_id)).all()
    account_ids = [account.account_id for account in accounts]
    if not account_ids:
        return []
    return db.scalars(
        select(Transaction)
        .where(Transaction.from_account_id.in_(account_ids))
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    ).all()


@app.post("/accounts", response_model=AccountOut)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.customer_id == payload.customer_id))
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    exists = db.scalar(select(Account).where(Account.account_id == payload.account_id))
    if exists:
        raise HTTPException(status_code=409, detail="account_id already exists")
    account = Account(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account

@app.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(account_id: str, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.account_id == account_id))
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return account

@app.post("/monitoring/evaluate_transaction", response_model=RiskDecision)
def monitoring_evaluate_transaction(payload: TransactionEvaluateRequest, db: Session = Depends(get_db)):
    return evaluate_transaction(db, payload.from_account_id, payload.to_account_id, payload.amount, payload.currency)

@app.post("/banking-api/transfer", response_model=TransactionOut)
def banking_api_transfer(payload: TransferRequest, db: Session = Depends(get_db)):
    return execute_transfer(
        db=db,
        from_account_id=payload.from_account_id,
        to_account_id=payload.to_account_id,
        amount=payload.amount,
        currency=payload.currency,
        actor=payload.actor,
        execute_if_low_risk=payload.execute_if_low_risk,
        memo=payload.memo,
        customer_id=payload.customer_id,
    )

@app.get("/transactions/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, db: Session = Depends(get_db)):
    tx = db.scalar(select(Transaction).where(Transaction.transaction_id == transaction_id))
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")
    return tx

@app.get("/transactions", response_model=list[TransactionOut])
def list_transactions(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    return db.scalars(select(Transaction).order_by(Transaction.created_at.desc()).limit(limit)).all()

@app.post("/policies", response_model=PolicyOut)
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)):
    exists = db.scalar(select(Policy).where(Policy.policy_id == payload.policy_id))
    if exists:
        raise HTTPException(status_code=409, detail="policy_id already exists")
    policy = Policy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy

@app.get("/policies", response_model=list[PolicyOut])
def list_policies(category: str | None = None, active_only: bool = True, db: Session = Depends(get_db)):
    stmt = select(Policy)
    if category:
        stmt = stmt.where(Policy.category == category)
    if active_only:
        stmt = stmt.where(Policy.is_active == True)  # noqa: E712
    return db.scalars(stmt).all()

@app.get("/policies/search", response_model=list[PolicyOut])
def search_policies(q: str = "", db: Session = Depends(get_db)):
    tokens = [token.strip() for token in q.split() if token.strip()]

    stmt = select(Policy).where(Policy.is_active == True)  # noqa: E712

    if tokens:
        token_filters = []
        for token in tokens:
            pattern = f"%{token}%"
            token_filters.append(
                or_(
                    Policy.policy_id.ilike(pattern),
                    Policy.title.ilike(pattern),
                    Policy.category.ilike(pattern),
                    Policy.severity.ilike(pattern),
                    Policy.content.ilike(pattern),
                )
            )

        stmt = stmt.where(and_(*token_filters))

    return db.scalars(stmt).all()

@app.post("/rules", response_model=RuleOut)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    exists = db.scalar(select(Rule).where(Rule.rule_id == payload.rule_id))
    if exists:
        raise HTTPException(status_code=409, detail="rule_id already exists")
    rule = Rule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

@app.get("/rules", response_model=list[RuleOut])
def list_rules(category: str | None = None, active_only: bool = True, db: Session = Depends(get_db)):
    stmt = select(Rule)
    if category:
        stmt = stmt.where(Rule.category == category)
    if active_only:
        stmt = stmt.where(Rule.is_active == True)  # noqa: E712
    return db.scalars(stmt).all()

#handle the AI's final decision
@app.patch("/transactions/{transaction_id}/status", response_model=TransactionOut)
def update_transaction_status(
    transaction_id: str, 
    payload: TransactionStatusUpdate, 
    db: Session = Depends(get_db)
):
    tx = db.scalar(select(Transaction).where(Transaction.transaction_id == transaction_id))
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")
    
    # Update the status and attach the AI's rationale
    tx.status = payload.status
    tx.decision_reason = payload.decision_reason
    
    db.commit()
    db.refresh(tx)
    return tx


# Synthetic operations endpoints consumed by the MCP mid-office tools. They use
# the same customer/account database as the rest of the demo so cross-service
# integration tests exercise a real persistence boundary.
@app.get("/mid-office/workload/{branch_id}")
def get_branch_workload(branch_id: str):
    branch_profiles = {
        "BR-001": {"queue_length": 18, "average_wait_minutes": 24, "available_tellers": 2, "total_staff": 7},
        "BR-002": {"queue_length": 4, "average_wait_minutes": 7, "available_tellers": 5, "total_staff": 7},
    }
    profile = branch_profiles.get(
        branch_id,
        {"queue_length": 8, "average_wait_minutes": 12, "available_tellers": 3, "total_staff": 5},
    )
    return {"branch_id": branch_id, **profile}


@app.post("/mid-office/rebalance")
def propose_staffing_adjustment(payload: StaffingAdjustmentRequest):
    improvement = min(0.1 * payload.staff_count, 0.5)
    return {
        "from_branch": payload.from_branch,
        "to_branch": payload.to_branch,
        "staff_count": payload.staff_count,
        "workload_balance_score": round(0.5 + improvement, 2),
        "status": "proposed",
    }


@app.get("/mid-office/affinity/{customer_id}")
def get_customer_product_affinity(customer_id: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.customer_id == customer_id))
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    accounts = db.scalars(select(Account).where(Account.customer_id == customer_id)).all()
    total_balance = sum(account.balance for account in accounts)
    products = ["High-Yield Savings"] if total_balance >= 25000 else ["Automatic Savings"]
    if customer.risk_level in {"low", "medium"}:
        products.append("Home Equity Consultation")
    return {
        "customer_id": customer_id,
        "products": products,
        "basis": {"total_balance_band": "25000_plus" if total_balance >= 25000 else "under_25000", "risk_level": customer.risk_level},
    }


@app.post("/back-office/reconcile")
def trigger_eod_reconciliation(date: date = Query(...), db: Session = Depends(get_db)):
    start = datetime.combine(date, time.min)
    end = start + timedelta(days=1)
    transactions = db.scalars(
        select(Transaction).where(and_(Transaction.created_at >= start, Transaction.created_at < end))
    ).all()
    review_items = [
        {
            "transaction_id": tx.transaction_id,
            "account_id": tx.from_account_id,
            "amount": tx.amount,
            "reason": tx.decision_reason,
        }
        for tx in transactions
        if tx.status.lower() in {"manual_review", "pending_review", "rejected"}
    ]
    report_id = f"RECON-{date.isoformat()}-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "report_id": report_id,
        "business_date": date.isoformat(),
        "transaction_count": len(transactions),
        "transaction_total": round(sum(tx.amount for tx in transactions), 2),
        "status": "review_required" if review_items else "reconciled",
        "mismatches": review_items,
    }
    db.add(AuditLog(event_type="reconciliation_report", actor=report_id, payload=json.dumps(payload)))
    db.commit()
    return payload


@app.get("/back-office/mismatches/{report_id}")
def get_reconciliation_mismatches(report_id: str, db: Session = Depends(get_db)):
    audit = db.scalar(
        select(AuditLog).where(
            and_(AuditLog.event_type == "reconciliation_report", AuditLog.actor == report_id)
        )
    )
    if not audit:
        raise HTTPException(status_code=404, detail="reconciliation report not found")
    payload = json.loads(audit.payload)
    return {"report_id": report_id, "mismatches": payload.get("mismatches", [])}


@app.post("/back-office/resolve/{transaction_id}")
def resolve_ledger_entry(
    transaction_id: str,
    payload: LedgerCorrectionRequest,
    db: Session = Depends(get_db),
):
    tx = db.scalar(select(Transaction).where(Transaction.transaction_id == transaction_id))
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")
    correction = {
        "transaction_id": transaction_id,
        "correction_amount": payload.correction_amount,
        "note": payload.note,
        "status": "recorded",
    }
    db.add(AuditLog(event_type="ledger_correction", actor="back_office", payload=json.dumps(correction)))
    db.commit()
    return correction
