from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from fastapi import Depends, FastAPI, HTTPException, Query
from .database import get_db
from .models import Account, Customer, Policy, Rule, Transaction
from .schemas import (
    AccountCreate, AccountOut, CustomerCreate, CustomerOut, PolicyCreate, PolicyOut,
    RuleCreate, RuleOut, RiskDecision, TransactionEvaluateRequest, TransactionOut,
    TransferRequest, TransactionStatusUpdate
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
