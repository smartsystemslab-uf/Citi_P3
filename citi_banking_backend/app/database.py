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

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
