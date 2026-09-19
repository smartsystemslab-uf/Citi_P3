import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


_TEST_DIR = Path(tempfile.mkdtemp(prefix="citi-p3-backend-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'backend.db'}"

AML_TEST_DB = _TEST_DIR / "aml.db"
os.environ["AML_DB_PATH"] = str(AML_TEST_DB)

with sqlite3.connect(AML_TEST_DB) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aml_alerts (
            alert_id TEXT PRIMARY KEY,
            customer_id TEXT,
            alert_type TEXT,
            detection_window_params TEXT,
            cumulative_total REAL,
            threshold REAL,
            status TEXT,
            raw_payload TEXT,
            created_at TEXT
        )
        """
    )

@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

