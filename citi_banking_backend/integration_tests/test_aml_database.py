import pytest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, Transaction
from app.database import get_shared_attribute_accounts, flag_device_sharing_rings

@pytest.fixture
def db():
    """Sets up an in-memory database specifically for these tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_task7_shared_attribute_accounts(db):
    """
    Test Requirement: Seed two test accounts sharing the same device_id/IP and one unrelated account; 
    call find_shared_attribute_accounts on one of the linked accounts and confirm it returns the 
    other linked account but not the unrelated one.
    """
    # 1. Seed two test accounts sharing the same device/IP
    db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id="ACC_1", to_account_id="DEST", amount=100, device_id="SHARED_DEV", ip_address="1.1.1.1"))
    db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id="ACC_2", to_account_id="DEST", amount=100, device_id="SHARED_DEV", ip_address="1.1.1.1"))
    
    # 2. Seed one unrelated account
    db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id="ACC_3", to_account_id="DEST", amount=100, device_id="SAFE_DEV", ip_address="9.9.9.9"))
    db.commit()

    # 3. Call find_shared_attribute_accounts on one of the linked accounts (ACC_1)
    result = get_shared_attribute_accounts(db, "ACC_1")

    # 4. Confirm it returns the other linked account (ACC_2) but not the unrelated one (ACC_3)
    assert "ACC_2" in result.get("SHARED_DEV", [])
    assert "ACC_3" not in result.get("SHARED_DEV", [])
    assert "SAFE_DEV" not in result

def test_task8_device_sharing_query(db):
    """
    Test Requirement: Seed 4 accounts sharing one device within 24h and confirm the GROUP BY/HAVING 
    query flags them; seed only 2 sharing a device and confirm it does not.
    """
    now = datetime.utcnow()
    
    # 1. Seed 4 accounts sharing ONE device within 24h
    for i in range(4):
        db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id=f"FRAUD_ACC_{i}", to_account_id="DEST", amount=10, device_id="HUB_DEVICE", created_at=now))
    
    # 2. Seed only 2 sharing a device
    for i in range(2):
        db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id=f"SAFE_ACC_{i}", to_account_id="DEST", amount=10, device_id="NORMAL_DEVICE", created_at=now))
    db.commit()

    # 3. Run the query
    results = flag_device_sharing_rings(db, hours=24, threshold=3)
    flagged_devices = [r["device_id"] for r in results]
    
    # 4. Confirm it flags the 4-account device, but NOT the 2-account device
    assert "HUB_DEVICE" in flagged_devices
    assert "NORMAL_DEVICE" not in flagged_devices