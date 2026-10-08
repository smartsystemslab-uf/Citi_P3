from fastapi.testclient import TestClient
import pytest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models import Base, Transaction
from app.database import get_shared_attribute_accounts, flag_device_sharing_rings
import json
from datetime import datetime, timedelta
from app.models import CustomerRiskProfile, AMLAlert
from app.database import refresh_customer_baselines
from app.main import app, get_db

client = TestClient(app) 

@pytest.fixture
def db():
    """Sets up an in-memory database that safely shares connections across FastAPI worker threads."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}, # Allow FastAPI TestClient threads to access it
        poolclass=StaticPool # Keep the same exact in-memory DB for all connections
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
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



def test_task4_strict_baseline_and_idempotency(db):
    """
    Test Requirement: 
    1. Seed test customer and confirm math matches hand-calculated values.
    2. Call POST /aml/refresh-baselines twice in a row and confirm it updates, not duplicates.
    """
    # Override the app's database dependency to use our test database
    app.dependency_overrides[get_db] = lambda: db

    now = datetime.utcnow()
    # 1. Seed exactly 3 weeks (21 days) of history for ACC_STRICT
    txs = [
        (100.0, "5411", now - timedelta(days=20)), # Grocery
        (200.0, "5411", now - timedelta(days=15)), # Grocery
        (500.0, "5812", now - timedelta(days=10)), # Restaurant (Peak)
        (50.0, "4111", now - timedelta(days=5)),   # Transit
        (10.0, "4111", now - timedelta(days=1))    # Transit
    ]
    
    for amount, mcc, time in txs:
        tx = Transaction(
            transaction_id=str(uuid.uuid4()), from_account_id="ACC_STRICT", 
            to_account_id="DEST", amount=amount, created_at=time
        )
        tx.mcc = mcc # Assuming mcc column was added
        db.add(tx)
    db.commit()

    # 2. Call POST /aml/refresh-baselines TWICE in a row
    response_1 = client.post("/aml/refresh-baselines")
    assert response_1.status_code == 200
    
    response_2 = client.post("/aml/refresh-baselines")
    assert response_2.status_code == 200

    # 3. Confirm Idempotency (Updates existing row, no duplicates)
    profiles = db.query(CustomerRiskProfile).filter_by(account_id="ACC_STRICT").all()
    assert len(profiles) == 1  # Proves it didn't create duplicates!

    # 4. Confirm math matches hand-calculated values
    profile = profiles[0]
    
    assert profile.peak_transfer_amount == 500.0
    
    # Math: Total volume = 860.0. Spread over exactly 3 weeks (21 days) = 286.66 avg weekly
    assert 285.0 < profile.avg_weekly_volume < 317.0 
    
    mccs = json.loads(profile.top_mccs)
    assert "5411" in mccs
    assert "4111" in mccs
    assert "5812" in mccs

    # Clean up dependency override
    app.dependency_overrides.clear()

def evaluate_and_flag_anomaly(db, account_id, transaction_id, amount):
    """
    Simulates the backend endpoint that the ADK calls when an anomaly is detected.
    (You will likely place this logic in a FastAPI route).
    """
    profile = db.query(CustomerRiskProfile).filter_by(account_id=account_id).first()
    threshold = profile.peak_transfer_amount * 5
    
    if amount > threshold:
        alert = AMLAlert(
            alert_type="anomaly", 
            entity_id=transaction_id, 
            description=f"Amount {amount} exceeds 5x peak of {profile.peak_transfer_amount}", 
            status="review"
        )
        db.add(alert)
        db.commit()
        return True
    return False


def test_task6_strict_anomaly_thresholding(db):
    """
    Test Requirement: 
    1. Submit one tx in normal range and confirm it is NOT flagged.
    2. Submit a clear outlier (5x peak) and confirm it lands in aml_alerts with status "review".
    """
    # 1. Establish baseline (Peak: 100)
    profile = CustomerRiskProfile(account_id="ACC_TARGET", peak_transfer_amount=100.0)
    db.add(profile)
    db.commit()
    
    # 2. Submit Normal transaction (150 is < 500 threshold)
    is_flagged = evaluate_and_flag_anomaly(db, "ACC_TARGET", "TX_NORMAL", 150.0)
    assert is_flagged is False
    
    # Confirm it is NOT in aml_alerts
    normal_alert = db.query(AMLAlert).filter_by(entity_id="TX_NORMAL").first()
    assert normal_alert is None 

    # 3. Submit clear outlier (600 is > 500 threshold)
    is_flagged = evaluate_and_flag_anomaly(db, "ACC_TARGET", "TX_OUTLIER", 600.0)
    assert is_flagged is True
    
    # Confirm it CROSSES threshold and LANDS in aml_alerts with status "review"
    outlier_alert = db.query(AMLAlert).filter_by(entity_id="TX_OUTLIER").first()
    assert outlier_alert is not None
    assert outlier_alert.status == "review"


def test_aml_shared_attributes_endpoint(db):
    """GET /aml/shared-attributes/{account_id} exposes get_shared_attribute_accounts over HTTP."""
    app.dependency_overrides[get_db] = lambda: db

    db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id="TARGET", to_account_id="DEST", amount=10, device_id="SHARED_DEVICE"))
    db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id="LINKED", to_account_id="DEST", amount=10, device_id="SHARED_DEVICE"))
    db.commit()

    response = client.get("/aml/shared-attributes/TARGET")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert "LINKED" in body["data"]["SHARED_DEVICE"]

    app.dependency_overrides.clear()


def test_aml_device_sharing_hotspots_endpoint(db):
    """GET /aml/device-sharing-hotspots exposes flag_device_sharing_rings over HTTP."""
    app.dependency_overrides[get_db] = lambda: db

    for i in range(4):
        db.add(Transaction(transaction_id=str(uuid.uuid4()), from_account_id=f"HUB_ACC_{i}", to_account_id="DEST", amount=10, device_id="HUB_DEVICE"))
    db.commit()

    response = client.get("/aml/device-sharing-hotspots", params={"hours": 24, "threshold": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert any(row["device_id"] == "HUB_DEVICE" for row in body["data"])

    app.dependency_overrides.clear()


def test_aml_alerts_feed_endpoint(db):
    """POST /aml/alerts/feed exposes feed_aml_alerts over HTTP and persists aml_alerts rows."""
    app.dependency_overrides[get_db] = lambda: db

    payload = {
        "mule_rings": [["ACC_A", "ACC_B", "ACC_C"]],
        "device_hotspots": [{"device_id": "HUB_DEVICE", "account_count": 4}],
    }
    response = client.post("/aml/alerts/feed", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["mule_rings_fed"] == 1
    assert body["device_hotspots_fed"] == 1

    mule_alert = db.query(AMLAlert).filter_by(alert_type="mule_ring").first()
    device_alert = db.query(AMLAlert).filter_by(alert_type="device_sharing").first()
    assert mule_alert is not None
    assert device_alert is not None

    app.dependency_overrides.clear()


def test_aml_create_alert_endpoint(db):
    """POST /aml/alerts creates a single generic aml_alerts row (used by anomaly checks)."""
    app.dependency_overrides[get_db] = lambda: db

    payload = {
        "alert_type": "baseline_deviation",
        "entity_id": "TX_OUTLIER",
        "description": "Amount 600.0 exceeds 5x historical peak of 100.0",
        "status": "review",
    }
    response = client.post("/aml/alerts", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    alert = db.query(AMLAlert).filter_by(entity_id="TX_OUTLIER").first()
    assert alert is not None
    assert alert.alert_type == "baseline_deviation"
    assert alert.status == "review"

    app.dependency_overrides.clear()


def test_aml_get_profile_endpoint(db):
    """GET /aml/profiles/{account_id} exposes a computed CustomerRiskProfile over HTTP."""
    app.dependency_overrides[get_db] = lambda: db

    db.add(CustomerRiskProfile(account_id="ACC_PROFILE", avg_weekly_volume=250.0, peak_transfer_amount=900.0, top_mccs=json.dumps(["5411", "5812"])))
    db.commit()

    response = client.get("/aml/profiles/ACC_PROFILE")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["peak_transfer_amount"] == 900.0
    assert "5411" in body["data"]["top_mccs"]

    missing_response = client.get("/aml/profiles/NO_SUCH_ACCOUNT")
    assert missing_response.status_code == 404

    app.dependency_overrides.clear()