import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bank_adk.infra.api_client import BankingApiError
from bank_adk.infra.repository import BankingRepository
from bank_adk.tools import aml_tools, back_office_tools, csr_tools, customer_tools, knowledge_tools, ops_tools
from bank_adk.tools.search import rank_records


@pytest.fixture()
def repository(tmp_path):
    repo = BankingRepository(tmp_path / "banking.db")
    repo.initialize()
    return repo


def test_repository_complete_workflow(repository):
    customer = repository.get_customer("CUST-001")
    assert customer["notes_json"]["typical_txn_band"] == "50-3500"
    assert len(customer["accounts"]) == 2
    assert repository.get_customer("missing") is None
    assert repository.get_transaction("TXN-1001")["metadata_json"]["device_change"] is True
    assert repository.get_transaction("missing") is None
    assert len(repository.list_recent_transactions("CUST-001", 1)) == 1

    result = repository.execute_transfer("CHK-002", "NEW-DEST", 100, "unit", "CUST-002")
    assert result["status"] == "posted"
    assert result["source_balance_after"] == pytest.approx(9520.13)
    assert result["destination_balance_after"] == 100
    with pytest.raises(ValueError, match="Unknown source"):
        repository.execute_transfer("missing", "X", 1, "")
    with pytest.raises(ValueError, match="Insufficient funds"):
        repository.execute_transfer("CHK-002", "X", 999999, "")

    review = repository.create_manual_review("fraud", "{}")
    assert review["case_id"].startswith("MR-")
    schedule = repository.save_schedule("{}")
    assert schedule["status"] == "validated"

    batch = repository.get_eod_batch("EOD-2026-04-21-CLEAN")
    assert batch["notes_json"]["expected_items"] == 43
    assert repository.get_eod_batch("missing") is None
    assert repository.apply_ledger_updates(batch["batch_id"], "post")["status"] == "ledger_updated"
    with pytest.raises(ValueError, match="Unknown batch_id"):
        repository.apply_ledger_updates("missing", "post")
    assert repository.create_exception(batch["batch_id"], "difference")["status"] == "open"
    assert repository.save_report(batch["batch_id"], "done")["status"] == "generated"
    repository.initialize()


def _seed_customer_with_deposits(repository, customer_id: str, account_id: str, deposits: list[tuple[float, int]]):
    with repository.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (customer_id, "AML Test User", "medium", "verified", "Analyst", "US", json.dumps({"behavior_notes": ["AML test customer"]}))
        )
        conn.execute(
            "INSERT OR REPLACE INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, customer_id, "checking", "USD", 0.0, "active")
        )

    for idx, (amount, day_offset) in enumerate(deposits):
        created_at = (datetime.now(timezone.utc) - timedelta(days=day_offset)).isoformat()
        with repository.connect() as conn:
            conn.execute(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"TXN-AML-{customer_id}-{idx}",
                    customer_id,
                    f"EXT-{idx}",
                    account_id,
                    amount,
                    "USD",
                    "cash_deposit",
                    "cash_deposit",
                    "AML Test Deposit",
                    "posted",
                    json.dumps({"deposit_note": "structured deposit test"}),
                    created_at,
                )
            )


def test_structuring_detection_positive_case(repository, monkeypatch):
    monkeypatch.setattr(aml_tools, "repo", repository)
    monkeypatch.setattr(customer_tools, "repo", repository)

    _seed_customer_with_deposits(
        repository,
        "CUST-AML-POS",
        "CHK-AML-POS",
        [(9200.0, 0), (9200.0, 1), (9200.0, 2)],
    )

    result = json.loads(aml_tools.detect_structuring("CUST-AML-POS", window_days=7, threshold_amount=10000, auto_trigger_review=True))

    assert result["structuring_detected"] is True
    assert result["alert_id"].startswith("ALT-")
    assert result["manual_review_case_id"].startswith("MR-")

    alert = repository.get_aml_alert(result["alert_id"])
    assert alert["alert_type"] == "structuring"
    assert alert["status"] == "open"


def test_structuring_detection_single_deposit_not_flagged(repository, monkeypatch):
    monkeypatch.setattr(aml_tools, "repo", repository)
    monkeypatch.setattr(customer_tools, "repo", repository)

    _seed_customer_with_deposits(
        repository,
        "CUST-AML-NEG1",
        "CHK-AML-NEG1",
        [(9200.0, 0)],
    )

    result = json.loads(aml_tools.detect_structuring("CUST-AML-NEG1", window_days=7, threshold_amount=10000, auto_trigger_review=True))

    assert result["structuring_detected"] is False
    assert repository.list_aml_alerts("CUST-AML-NEG1") == []


def test_structuring_detection_spread_out_deposits_not_flagged(repository, monkeypatch):
    monkeypatch.setattr(aml_tools, "repo", repository)
    monkeypatch.setattr(customer_tools, "repo", repository)

    _seed_customer_with_deposits(
        repository,
        "CUST-AML-NEG2",
        "CHK-AML-NEG2",
        [(9200.0, 0), (9200.0, 15), (9200.0, 30)],
    )

    result = json.loads(aml_tools.detect_structuring("CUST-AML-NEG2", window_days=7, threshold_amount=10000, auto_trigger_review=True))

    assert result["structuring_detected"] is False
    assert repository.list_aml_alerts("CUST-AML-NEG2") == []


def test_rank_records_scoring_and_fallback():
    rows = [{"text": "fraud fraud review"}, {"text": "normal account"}]
    assert rank_records("", rows, ["text"], 1) == rows[:1]
    assert rank_records("fraud", rows, ["text"], 2)[0] == rows[0]
    assert rank_records("unmatched", rows, ["text"], 1) == rows[:1]


def test_local_tools(repository, monkeypatch):
    local_settings = SimpleNamespace(use_external_banking_api=False, data_dir=customer_tools.settings.data_dir)
    for module in (customer_tools, csr_tools, knowledge_tools):
        monkeypatch.setattr(module, "settings", local_settings)
    for module in (customer_tools, csr_tools, ops_tools, back_office_tools):
        if hasattr(module, "repo"):
            monkeypatch.setattr(module, "repo", repository)

    assert json.loads(customer_tools.get_customer_context("CUST-001"))["customer_id"] == "CUST-001"
    assert "not found" in customer_tools.get_customer_context("missing")
    assert json.loads(customer_tools.get_transaction_context("TXN-1001"))["transaction_id"] == "TXN-1001"
    assert "not found" in customer_tools.get_transaction_context("missing")
    assert len(json.loads(customer_tools.list_recent_transactions("CUST-001", 1))) == 1
    assert json.loads(customer_tools.submit_manual_review("fraud", "{}"))["status"] == "open"
    assert json.loads(customer_tools.execute_transfer("CHK-002", "EXT-998", 10))["status"] == "posted"

    assert json.loads(csr_tools.get_customer_financial_profile("CUST-001"))["customer_id"] == "CUST-001"
    assert "not found" in csr_tools.get_customer_financial_profile("missing")
    assert json.loads(csr_tools.search_product_catalog("cash"))
    assert json.loads(csr_tools.search_loan_playbooks("home"))
    assert json.loads(knowledge_tools.search_policies("AML"))
    assert json.loads(knowledge_tools.search_faqs("dispute"))

    assert json.loads(ops_tools.get_operations_snapshot("2026-04-21"))
    assert json.loads(ops_tools.get_operations_snapshot("1900-01-01"))
    assert json.loads(ops_tools.persist_validated_schedule("{}"))["status"] == "validated"
    assert json.loads(ops_tools.get_support_playbooks("staffing"))

    clean = "EOD-2026-04-21-CLEAN"
    assert json.loads(back_office_tools.validate_eod_readiness(clean))["ready"] is True
    assert "not found" in back_office_tools.validate_eod_readiness("missing")
    assert json.loads(back_office_tools.get_eod_batch(clean))["batch_id"] == clean
    assert "not found" in back_office_tools.get_eod_batch("missing")
    assert json.loads(back_office_tools.get_reconciliation_data(clean))["difference"] == 0
    assert "not found" in back_office_tools.get_reconciliation_data("missing")
    assert json.loads(back_office_tools.apply_ledger_updates(clean, "post"))["status"] == "ledger_updated"
    assert json.loads(back_office_tools.create_exception_case(clean, "bad"))["status"] == "open"
    assert json.loads(back_office_tools.store_report(clean, "report"))["status"] == "generated"


def test_external_tool_success_and_fallback(repository, monkeypatch):
    external_settings = SimpleNamespace(use_external_banking_api=True, data_dir=customer_tools.settings.data_dir)
    for module in (customer_tools, csr_tools, knowledge_tools):
        monkeypatch.setattr(module, "settings", external_settings)
    for module in (customer_tools, csr_tools):
        monkeypatch.setattr(module, "repo", repository)

    api = SimpleNamespace(
        get_customer_context=lambda customer_id: {"customer_id": customer_id, "external": True},
        get_transaction=lambda transaction_id: {"transaction_id": transaction_id},
        list_recent_transactions=lambda customer_id, limit: [{"limit": limit}],
        execute_transfer=lambda **kwargs: {"status": "completed", **kwargs},
        search_policies=lambda query: [{"query": query}],
    )
    monkeypatch.setattr(customer_tools, "api_client", api)
    monkeypatch.setattr(csr_tools, "api_client", api)
    monkeypatch.setattr(knowledge_tools, "api_client", api)
    assert json.loads(customer_tools.get_customer_context("C1"))["external"] is True
    assert json.loads(customer_tools.get_transaction_context("T1"))["transaction_id"] == "T1"
    assert json.loads(customer_tools.list_recent_transactions("C1", 2))[0]["limit"] == 2
    assert json.loads(customer_tools.execute_transfer("A", "B", 1))["status"] == "completed"
    assert json.loads(csr_tools.get_customer_financial_profile("C1"))["external"] is True
    assert json.loads(knowledge_tools.search_policies("AML"))[0]["query"] == "AML"

    def fail(*args, **kwargs):
        raise BankingApiError("offline")

    failing = SimpleNamespace(
        get_customer_context=fail,
        get_transaction=fail,
        list_recent_transactions=fail,
        execute_transfer=fail,
        search_policies=fail,
    )
    monkeypatch.setattr(customer_tools, "api_client", failing)
    monkeypatch.setattr(csr_tools, "api_client", failing)
    monkeypatch.setattr(knowledge_tools, "api_client", failing)
    assert "backend_warning" in json.loads(customer_tools.get_customer_context("CUST-001"))
    assert json.loads(customer_tools.get_customer_context("missing"))["error"] == "offline"
    assert "backend_warning" in json.loads(customer_tools.get_transaction_context("TXN-1001"))
    assert json.loads(customer_tools.get_transaction_context("missing"))["error"] == "offline"
    assert "transactions" in json.loads(customer_tools.list_recent_transactions("CUST-001", 2))
    assert json.loads(customer_tools.list_recent_transactions("missing", 2))["error"] == "offline"
    assert json.loads(customer_tools.execute_transfer("A", "B", 1))["status"] == "backend_error"
    assert "backend_warning" in json.loads(csr_tools.get_customer_financial_profile("CUST-001"))
    assert json.loads(csr_tools.get_customer_financial_profile("missing"))["error"] == "offline"
    assert "local_results" in json.loads(knowledge_tools.search_policies("AML"))

def test_list_aml_alerts_filters(tmp_path):
    repository = BankingRepository(tmp_path / "alerts.db")
    repository.initialize()

    repository.create_aml_alert(
        customer_id="CUST-001",
        alert_type="structuring",
        detection_window_params="7d",
        cumulative_total=12000.0,
        threshold=10000.0,
        raw_payload="{}",
        status="open",
    )

    repository.create_aml_alert(
        customer_id="CUST-002",
        alert_type="structuring",
        detection_window_params="7d",
        cumulative_total=15000.0,
        threshold=10000.0,
        raw_payload="{}",
        status="open",
    )

    repository.create_aml_alert(
        customer_id="CUST-001",
        alert_type="sanctions",
        detection_window_params="single",
        cumulative_total=5000.0,
        threshold=0.0,
        raw_payload="{}",
        status="cleared",
    )

    open_alerts = repository.list_aml_alerts(status="open")
    assert len(open_alerts) == 2

    customer_alerts = repository.list_aml_alerts(
        customer_id="CUST-001"
    )
    assert len(customer_alerts) == 2

    structuring_alerts = repository.list_aml_alerts(
        alert_type="structuring"
    )
    assert len(structuring_alerts) == 2

    filtered = repository.list_aml_alerts(
        customer_id="CUST-001",
        status="open",
        alert_type="structuring",
    )

    assert len(filtered) == 1
    assert filtered[0]["customer_id"] == "CUST-001"
    assert filtered[0]["status"] == "open"
    assert filtered[0]["alert_type"] == "structuring"