import os, json
from pathlib import Path
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session
from dotenv import load_dotenv
from datetime import datetime, timedelta
load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "citi_banking.db"
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def get_shared_attribute_accounts(db: Session, target_account_id: str):
    from .models import Transaction  # Import inside to prevent circular loops
    
    # 1. Find the distinct devices and IPs used by the target account as the sender
    target_attributes = db.query(Transaction.device_id, Transaction.ip_address)\
        .filter(Transaction.from_account_id == target_account_id).all()
        
    device_ids = [row.device_id for row in target_attributes if row.device_id]
    ip_addresses = [row.ip_address for row in target_attributes if row.ip_address]
    
    if not device_ids and not ip_addresses:
        return {}
    
    # 2. Join to find other senders using those exact same attributes
    shared_accounts_query = db.query(Transaction.from_account_id, Transaction.device_id, Transaction.ip_address)\
        .filter(Transaction.from_account_id != target_account_id)\
        .filter(
            (Transaction.device_id.in_(device_ids)) | 
            (Transaction.ip_address.in_(ip_addresses))
        ).distinct()
        
    # 3. Format as adjacency list
    adjacency_list = {}
    for row in shared_accounts_query:
        if row.device_id in device_ids:
            # FIX: Using from_account_id instead of account_id
            adjacency_list.setdefault(row.device_id, set()).add(row.from_account_id)
        if row.ip_address in ip_addresses:
            # FIX: Using from_account_id instead of account_id
            adjacency_list.setdefault(row.ip_address, set()).add(row.from_account_id)
            
    return {k: list(v) for k, v in adjacency_list.items()}


def flag_device_sharing_rings(db: Session, hours: int = 24, threshold: int = 3):
    from .models import Transaction  # FIX: Missing import added here
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    time_limit = datetime.utcnow() - timedelta(hours=hours)
    
    flagged_devices = (
        db.query(
            Transaction.device_id, 
            func.count(func.distinct(Transaction.from_account_id)).label('account_count')
        )
        .filter(Transaction.created_at >= time_limit)
        .filter(Transaction.device_id.isnot(None))
        .group_by(Transaction.device_id)
        .having(func.count(func.distinct(Transaction.from_account_id)) > threshold)
    ).all()
    
    return [{"device_id": row.device_id, "account_count": row.account_count} for row in flagged_devices]


def feed_aml_alerts(db: Session, mule_rings: list[list[str]], device_hotspots: list[dict]):
    from .models import AMLAlert  # Import inside to prevent circular loops
    import json
    
    new_alerts = []
    
    for ring in mule_rings:
        alert = AMLAlert(
            alert_type="mule_ring",
            entity_id=ring[0], 
            description=f"Closed-loop transaction pattern detected involving accounts: {json.dumps(ring)}"
        )
        new_alerts.append(alert)
        
    for hotspot in device_hotspots:
        alert = AMLAlert(
            alert_type="device_sharing",
            entity_id=hotspot["device_id"],
            description=f"High-risk device sharing: {hotspot['account_count']} distinct accounts used this device within 24 hours."
        )
        new_alerts.append(alert)
        
    if new_alerts:
        db.add_all(new_alerts)
        db.commit()
        
    return len(new_alerts)


def refresh_customer_baselines(db: Session):
    from .models import Transaction, CustomerRiskProfile
    from collections import defaultdict
    import json

    transactions = db.query(Transaction).all()
    
    # Process account histories
    account_data = defaultdict(lambda: {"total_vol": 0, "peak": 0, "mccs": defaultdict(int), "first_tx": None, "last_tx": None})
    
    for tx in transactions:
        data = account_data[tx.from_account_id]
        data["total_vol"] += tx.amount
        if tx.amount > data["peak"]:
            data["peak"] = tx.amount
        if getattr(tx, 'mcc', None):
            data["mccs"][tx.mcc] += 1
            
        if not data["first_tx"] or tx.created_at < data["first_tx"]:
            data["first_tx"] = tx.created_at
        if not data["last_tx"] or tx.created_at > data["last_tx"]:
            data["last_tx"] = tx.created_at

    updated_count = 0
    for acc_id, data in account_data.items():
        # Calculate weekly average
        days_active = (data["last_tx"] - data["first_tx"]).days if data["last_tx"] and data["first_tx"] else 0
        weeks = max(1, days_active / 7.0)
        avg_weekly = data["total_vol"] / weeks
        
        # Determine top 3 MCCs
        sorted_mccs = sorted(data["mccs"].items(), key=lambda x: x[1], reverse=True)
        top_3 = [m[0] for m in sorted_mccs[:3]]

        # Upsert logic (Idempotency)
        profile = db.query(CustomerRiskProfile).filter(CustomerRiskProfile.account_id == acc_id).first()
        if not profile:
            profile = CustomerRiskProfile(account_id=acc_id)
            db.add(profile)
            
        profile.avg_weekly_volume = avg_weekly
        profile.peak_transfer_amount = data["peak"]
        profile.top_mccs = json.dumps(top_3)
        updated_count += 1
        
    db.commit()
    return updated_count
class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
