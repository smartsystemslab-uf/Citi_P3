import os
import tempfile
from pathlib import Path

import pytest


_TEST_DIR = Path(tempfile.mkdtemp(prefix="citi-p3-backend-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'backend.db'}"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

