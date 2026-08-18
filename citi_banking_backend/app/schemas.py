from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field

class CustomerCreate(BaseModel):
    customer_id: str
    full_name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    kyc_status: str = "verified"
    risk_level: str = "low"

class CustomerOut(CustomerCreate):
    created_at: datetime
    class Config:
        from_attributes = True

class AccountCreate(BaseModel):
    account_id: str
    customer_id: str
    account_type: str = "checking"
    balance: float = 0.0
    currency: str = "USD"
    status: str = "active"

class AccountOut(AccountCreate):
    created_at: datetime
    class Config:
        from_attributes = True

class PolicyCreate(BaseModel):
    policy_id: str
    title: str
    category: str
    severity: str = "medium"
    content: str
    effective_date: date = Field(default_factory=date.today)
    is_active: bool = True

class PolicyOut(PolicyCreate):
    created_at: datetime
    class Config:
        from_attributes = True

class RuleCreate(BaseModel):
    rule_id: str
    name: str
    category: str
    field_name: str
    operator: str
    threshold_value: str
    risk_score_delta: int = 0
    action: str = "review"
    description: str = ""
    is_active: bool = True

class RuleOut(RuleCreate):
    created_at: datetime
    class Config:
        from_attributes = True

class TransferRequest(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"
    actor: str = "transaction_processing_agent"
    memo: str | None = None
    customer_id: str | None = None
    execute_if_low_risk: bool = True

class TransactionEvaluateRequest(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"

class RiskDecision(BaseModel):
    risk_score: int
    suspicious: bool
    decision: str
    reasons: list[str]
    matched_rules: list[str]

class TransactionOut(BaseModel):
    transaction_id: str
    from_account_id: str
    to_account_id: str
    amount: float
    currency: str
    tx_type: str
    status: str
    risk_score: int
    decision_reason: str
    created_at: datetime
class TransactionStatusUpdate(BaseModel):
    status: str
    decision_reason: str
    class Config:
        from_attributes = True


class StaffingAdjustmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_branch: str = Field(alias="from")
    to_branch: str = Field(alias="to")
    staff_count: int = Field(alias="count", gt=0)


class LedgerCorrectionRequest(BaseModel):
    correction_amount: float
    note: str = Field(min_length=1)
