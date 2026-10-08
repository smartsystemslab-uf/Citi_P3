"""Tests for the AML tools that now call the backend via bank_adk.infra.api_client
instead of returning hard-coded mock data (fetch_transaction_history,
find_shared_attribute_accounts, run_aml_detection_pipeline, check_threshold_anomaly).

These exercise the ADK-side wiring in isolation by monkeypatching the api_client
module, since the citi_banking_backend FastAPI service runs in a separate Python
environment and isn't available inside this project's test venv.
"""

import pytest

from citi_banking_adk_mlflow_observability.bank_adk.infra import api_client
from citi_banking_adk_mlflow_observability.bank_adk.tools import aml_tools


def test_find_shared_attribute_accounts_calls_backend(monkeypatch):
    captured = {}

    def fake_get(path, params=None, timeout=5.0):
        captured["path"] = path
        return {"status": "success", "data": {"SHARED_DEVICE": ["LINKED_ACC"]}}

    monkeypatch.setattr(aml_tools.api_client, "get", fake_get)

    result = aml_tools.find_shared_attribute_accounts("TARGET_ACC")

    assert captured["path"] == "/aml/shared-attributes/TARGET_ACC"
    assert result["data"]["SHARED_DEVICE"] == ["LINKED_ACC"]


def test_fetch_transaction_history_wraps_list_response(monkeypatch):
    monkeypatch.setattr(aml_tools.api_client, "get", lambda path, params=None, timeout=5.0: [{"transaction_id": "TX1"}])

    result = aml_tools.fetch_transaction_history("CUST_1")

    assert result == {"status": "success", "data": [{"transaction_id": "TX1"}]}


def test_fetch_transaction_history_passes_through_errors(monkeypatch):
    error_response = {"status": "error", "message": "connection refused", "data": {}}
    monkeypatch.setattr(aml_tools.api_client, "get", lambda path, params=None, timeout=5.0: error_response)

    result = aml_tools.fetch_transaction_history("CUST_1")

    assert result == error_response


def test_run_aml_detection_pipeline_feeds_mule_rings_and_hotspots(monkeypatch):
    posted = {}

    monkeypatch.setattr(
        aml_tools.api_client, "get",
        lambda path, params=None, timeout=5.0: {"status": "success", "data": [{"device_id": "HUB", "account_count": 4}]},
    )

    def fake_post(path, json=None, timeout=5.0):
        posted["path"] = path
        posted["json"] = json
        return {"status": "success"}

    monkeypatch.setattr(aml_tools.api_client, "post", fake_post)

    transactions = [
        {"from_account_id": "A", "to_account_id": "B"},
        {"from_account_id": "B", "to_account_id": "A"},
    ]
    summary = aml_tools.run_aml_detection_pipeline(transactions)

    assert posted["path"] == "/aml/alerts/feed"
    assert posted["json"]["device_hotspots"] == [{"device_id": "HUB", "account_count": 4}]
    assert len(posted["json"]["mule_rings"]) == 1
    assert "1 mule rings" in summary
    assert "1 device hotspots" in summary


def test_check_threshold_anomaly_flags_and_alerts_when_over_5x_peak(monkeypatch):
    monkeypatch.setattr(
        aml_tools.api_client, "get",
        lambda path, params=None, timeout=5.0: {"status": "success", "data": {"peak_transfer_amount": 100.0}},
    )

    posted = {}
    monkeypatch.setattr(aml_tools.api_client, "post", lambda path, json=None, timeout=5.0: posted.update(path=path, json=json) or {"status": "success"})

    result = aml_tools.check_threshold_anomaly("ACC_TARGET", "TX_OUTLIER", 600.0)

    assert result["flagged"] is True
    assert result["status"] == "review"
    assert posted["path"] == "/aml/alerts"
    assert posted["json"]["entity_id"] == "TX_OUTLIER"


def test_check_threshold_anomaly_does_not_flag_within_bounds(monkeypatch):
    monkeypatch.setattr(
        aml_tools.api_client, "get",
        lambda path, params=None, timeout=5.0: {"status": "success", "data": {"peak_transfer_amount": 100.0}},
    )
    monkeypatch.setattr(aml_tools.api_client, "post", lambda path, json=None, timeout=5.0: pytest.fail("should not alert"))

    result = aml_tools.check_threshold_anomaly("ACC_TARGET", "TX_NORMAL", 150.0)

    assert result["flagged"] is False


def test_check_threshold_anomaly_handles_missing_baseline(monkeypatch):
    monkeypatch.setattr(
        aml_tools.api_client, "get",
        lambda path, params=None, timeout=5.0: {"status": "error", "message": "404", "data": {}},
    )

    result = aml_tools.check_threshold_anomaly("ACC_UNKNOWN", "TX_1", 999.0)

    assert result["flagged"] is False
    assert "No baseline available" in result["reason"]


def test_api_client_get_handles_unreachable_backend_gracefully():
    """Sanity check: a real network failure is caught and returned as an error payload, not raised."""
    result = api_client.get("/health", timeout=0.2)
    # Whether or not something happens to be listening on localhost:8000 in this
    # environment, the call must not raise and must always return a dict.
    assert isinstance(result, dict)
