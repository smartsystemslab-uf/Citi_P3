from datetime import date, datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal
from .models import Account, Customer, Policy, Rule, Transaction, AuditLog


# Demo data is synthetic, but uses realistic banking-style fields and scenarios.
# It intentionally includes both the backend IDs (CUST-1001 / ACC-CHK-1001)
# and the original ADK scenario IDs (CUST-001 / CHK-001 / TXN-1001).
SEED_CUSTOMERS = [
    Customer(
        customer_id="CUST-001",
        full_name="Ava Patel",
        email="ava.patel.demo@example.com",
        phone="352-555-0101",
        address="Gainesville, FL",
        kyc_status="verified",
        risk_level="medium",
    ),
    Customer(
        customer_id="CUST-002",
        full_name="Daniel Chen",
        email="daniel.chen.demo@example.com",
        phone="352-555-0102",
        address="Gainesville, FL",
        kyc_status="verified",
        risk_level="low",
    ),
    Customer(
        customer_id="CUST-9001",
        full_name="High Risk Test Customer",
        email="risk.customer.demo@example.com",
        phone="352-555-0199",
        address="Unknown",
        kyc_status="pending",
        risk_level="high",
    ),
    Customer(
        customer_id="CUST-EXT",
        full_name="External Clearing Counterparty",
        email="external.clearing.demo@example.com",
        phone="000-000-0000",
        address="External Network",
        kyc_status="verified",
        risk_level="low",
    ),
    # Compatibility with the earlier backend test names.
    Customer(
        customer_id="CUST-1001",
        full_name="Alice Chen",
        email="alice.chen.demo@example.com",
        phone="352-555-1101",
        address="Gainesville, FL",
        kyc_status="verified",
        risk_level="low",
    ),
    Customer(
        customer_id="CUST-1002",
        full_name="Brian Smith",
        email="brian.smith.demo@example.com",
        phone="352-555-1102",
        address="Gainesville, FL",
        kyc_status="verified",
        risk_level="medium",
    ),
]

SEED_ACCOUNTS = [
    Account(account_id="CHK-001", customer_id="CUST-001", account_type="checking", balance=41850.37, currency="USD", status="active"),
    Account(account_id="SVG-001", customer_id="CUST-001", account_type="savings", balance=128500.00, currency="USD", status="active"),
    Account(account_id="CHK-002", customer_id="CUST-002", account_type="checking", balance=9620.13, currency="USD", status="active"),
    Account(account_id="CHK-9001", customer_id="CUST-9001", account_type="checking", balance=100000.00, currency="USD", status="active"),
    Account(account_id="EXT-998", customer_id="CUST-EXT", account_type="external", balance=0.00, currency="USD", status="active"),
    Account(account_id="ACC-CHK-1001", customer_id="CUST-1001", account_type="checking", balance=25000.00, currency="USD", status="active"),
    Account(account_id="ACC-SAV-1001", customer_id="CUST-1001", account_type="savings", balance=80000.00, currency="USD", status="active"),
    Account(account_id="ACC-CHK-1002", customer_id="CUST-1002", account_type="checking", balance=5000.00, currency="USD", status="active"),
    Account(account_id="ACC-CHK-9001", customer_id="CUST-9001", account_type="checking", balance=100000.00, currency="USD", status="active"),
]

SEED_POLICIES = [
    Policy(
        policy_id="POL-KYC-001",
        title="KYC Verification Requirement",
        category="KYC",
        severity="high",
        content=(
            "Demo internal policy: transactions from customers with pending, failed, or expired KYC status "
            "must be escalated before execution. Customer-facing agents may retrieve context only for verified service flows."
        ),
        effective_date=date(2026, 1, 1),
    ),
    Policy(
        policy_id="POL-AML-001",
        title="Large Transfer AML Review",
        category="AML",
        severity="high",
        content=(
            "Demo internal policy: large transfers, unusual transfer patterns, new external counterparties, "
            "or behavior outside the customer's normal transaction profile should be reviewed by fraud and compliance agents."
        ),
        effective_date=date(2026, 1, 1),
    ),
    Policy(
        policy_id="POL-FRAUD-001",
        title="Account Takeover and Device Change Review",
        category="Fraud",
        severity="high",
        content=(
            "Demo internal policy: after a device change, late-night access, unusual geography, or repeated failed login signals, "
            "the transaction monitoring agent should treat large outgoing transfers as suspicious until additional verification is completed."
        ),
        effective_date=date(2026, 1, 1),
    ),
    Policy(
        policy_id="POL-SANC-001",
        title="Sanctions and Restricted Counterparty Screening",
        category="Sanctions",
        severity="critical",
        content=(
            "Demo internal policy: transfers involving restricted countries, blocked counterparties, or incomplete counterparty information "
            "must not be automatically approved and should be sent to manual review."
        ),
        effective_date=date(2026, 1, 1),
    ),
    Policy(
        policy_id="POL-CS-001",
        title="Customer Service Data Handling",
        category="CustomerService",
        severity="medium",
        content=(
            "Demo internal policy: service agents can retrieve account context for routing and support, "
            "but should avoid exposing sensitive details unless identity verification is complete."
        ),
        effective_date=date(2026, 1, 1),
    ),
    Policy(
        policy_id="POL-LOAN-001",
        title="Loan Guidance and Suitability Check",
        category="Lending",
        severity="medium",
        content=(
            "Demo internal policy: representative-assist workflows should combine customer financial profile, risk tier, "
            "and product suitability before suggesting credit or lending next steps."
        ),
        effective_date=date(2026, 1, 1),
    ),
]

SEED_RULES = [
    Rule(
        rule_id="RULE-AMOUNT-001",
        name="High amount transfer",
        category="AML",
        field_name="amount",
        operator=">=",
        threshold_value="10000",
        risk_score_delta=45,
        action="review",
        description="Transfer amount is equal to or above 10,000 USD.",
    ),
    Rule(
        rule_id="RULE-AMOUNT-002",
        name="Very high amount transfer",
        category="AML",
        field_name="amount",
        operator=">=",
        threshold_value="50000",
        risk_score_delta=75,
        action="manual_review",
        description="Transfer amount is equal to or above 50,000 USD.",
    ),
    Rule(
        rule_id="RULE-FREQ-001",
        name="High daily transaction frequency",
        category="Fraud",
        field_name="daily_tx_count",
        operator=">=",
        threshold_value="5",
        risk_score_delta=35,
        action="review",
        description="The source account has many transfers within the last 24 hours.",
    ),
    Rule(
        rule_id="RULE-RISK-001",
        name="High-risk customer",
        category="Fraud",
        field_name="customer_risk_level",
        operator="==",
        threshold_value="high",
        risk_score_delta=50,
        action="manual_review",
        description="The source customer has high risk level.",
    ),
    Rule(
        rule_id="RULE-KYC-001",
        name="KYC not verified",
        category="KYC",
        field_name="kyc_status",
        operator="!=",
        threshold_value="verified",
        risk_score_delta=50,
        action="manual_review",
        description="The customer KYC status is not verified.",
    ),
]

def _seed_transactions(db: Session) -> None:
    now = datetime.utcnow()
    txns = [
        Transaction(
            transaction_id="TXN-1001",
            from_account_id="CHK-001",
            to_account_id="EXT-998",
            amount=9200.00,
            currency="USD",
            tx_type="wire_transfer",
            status="pending_review",
            risk_score=65,
            decision_reason="New external counterparty, device change, unusual geography, and amount above the customer's normal pattern.",
            created_at=now - timedelta(hours=2),
        ),
        Transaction(
            transaction_id="TXN-1002",
            from_account_id="CHK-002",
            to_account_id="EXT-998",
            amount=220.55,
            currency="USD",
            tx_type="bill_payment",
            status="completed",
            risk_score=5,
            decision_reason="Routine utility-style payment from a low-risk verified customer.",
            created_at=now - timedelta(days=1, hours=1),
        ),
        Transaction(
            transaction_id="TXN-1003",
            from_account_id="CHK-001",
            to_account_id="EXT-998",
            amount=75.21,
            currency="USD",
            tx_type="card_payment",
            status="completed",
            risk_score=5,
            decision_reason="Small recurring merchant transaction.",
            created_at=now - timedelta(days=2),
        ),
        Transaction(
            transaction_id="TXN-9001",
            from_account_id="CHK-9001",
            to_account_id="EXT-998",
            amount=60000.00,
            currency="USD",
            tx_type="wire_transfer",
            status="manual_review",
            risk_score=100,
            decision_reason="High-risk customer, pending KYC, and very high transfer amount.",
            created_at=now - timedelta(hours=4),
        ),
    ]
    for item in txns:
        if not db.scalar(select(Transaction).where(Transaction.transaction_id == item.transaction_id)):
            db.add(item)

def init_db(seed: bool = True) -> None:
    Base.metadata.create_all(bind=engine)
    if not seed:
        return
    # Seed rows are module-level templates and are inspected again on idempotent
    # calls. Keep their loaded attributes available after this session commits.
    db: Session = SessionLocal(expire_on_commit=False)
    try:
        for item in SEED_CUSTOMERS:
            if not db.scalar(select(Customer).where(Customer.customer_id == item.customer_id)):
                db.add(item)
        db.commit()

        for item in SEED_ACCOUNTS:
            if not db.scalar(select(Account).where(Account.account_id == item.account_id)):
                db.add(item)
        db.commit()

        for item in SEED_POLICIES:
            if not db.scalar(select(Policy).where(Policy.policy_id == item.policy_id)):
                db.add(item)
        for item in SEED_RULES:
            if not db.scalar(select(Rule).where(Rule.rule_id == item.rule_id)):
                db.add(item)
        _seed_transactions(db)

        if not db.scalar(select(AuditLog).where(AuditLog.event_type == "seed")):
            db.add(AuditLog(event_type="seed", actor="system", payload='{"message":"demo data loaded"}'))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    init_db(seed=True)
    print("Database initialized and seeded.")
