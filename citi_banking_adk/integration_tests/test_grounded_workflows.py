from __future__ import annotations

import json

from bank_adk.infra.api_client import BankingApiClient
from bank_adk.infra.repository import BankingRepository
from bank_adk.tools import back_office_tools, csr_tools, customer_tools, knowledge_tools, ops_tools

from conftest import RunningBackend


def test_external_backend_client_and_front_office_tools(live_backend: RunningBackend, monkeypatch):
    client = BankingApiClient(base_url=live_backend.base_url, timeout=5)
    assert client.health()["status"] == "ok"
    assert client.get_customer_context("CUST-001")["full_name"] == "Ava Patel"
    assert client.get_transaction("TXN-1001")["status"] == "pending_review"
    assert client.list_recent_transactions("CUST-001", limit=2)
    assert client.search_policies("AML large transfer")[0]["category"] == "AML"
    assert client.list_rules("AML")

    for module in (customer_tools, csr_tools, knowledge_tools):
        if hasattr(module, "api_client"):
            monkeypatch.setattr(module, "api_client", client)

    customer = json.loads(customer_tools.get_customer_context("CUST-001"))
    transaction = json.loads(customer_tools.get_transaction_context("TXN-1001"))
    profile = json.loads(csr_tools.get_customer_financial_profile("CUST-002"))
    policies = json.loads(knowledge_tools.search_policies("AML large transfer"))
    assert customer["customer_id"] == "CUST-001"
    assert transaction["transaction_id"] == "TXN-1001"
    assert profile["customer_id"] == "CUST-002"
    assert policies[0]["policy_id"] == "POL-AML-001"

    transfer = json.loads(customer_tools.execute_transfer("CHK-002", "EXT-998", 25.5, "integration", "CUST-002"))
    assert transfer["status"] == "completed"
    assert client.get_transaction(transfer["transaction_id"])["amount"] == 25.5


def test_local_mid_and_back_office_tools_share_a_real_repository(tmp_path, monkeypatch):
    repository = BankingRepository(tmp_path / "workflow.db")
    repository.initialize()
    monkeypatch.setattr(ops_tools, "repo", repository)
    monkeypatch.setattr(back_office_tools, "repo", repository)

    operations = json.loads(ops_tools.get_operations_snapshot("2026-04-21"))
    assert operations and all(row["date"] == "2026-04-21" for row in operations)
    schedule = json.loads(ops_tools.persist_validated_schedule('{"branch":"BR-001","staff":7}'))
    assert schedule["status"] == "validated"
    assert json.loads(ops_tools.get_support_playbooks("queue staffing"))

    readiness = json.loads(back_office_tools.validate_eod_readiness("EOD-2026-04-21-CLEAN"))
    assert readiness["ready"] is True
    ledger = json.loads(back_office_tools.apply_ledger_updates("EOD-2026-04-21-CLEAN", "post 43 items"))
    assert ledger["status"] == "ledger_updated"
    reconciliation = json.loads(back_office_tools.get_reconciliation_data("EOD-2026-04-21-CLEAN"))
    assert reconciliation["difference"] == 0.0
    report = json.loads(back_office_tools.store_report("EOD-2026-04-21-CLEAN", "Batch reconciled."))
    assert report["status"] == "generated"

    mismatch = json.loads(back_office_tools.get_reconciliation_data("EOD-2026-04-21-MISMATCH"))
    assert mismatch["difference"] == 190.0
    exception = json.loads(back_office_tools.create_exception_case("EOD-2026-04-21-MISMATCH", "190.00 mismatch"))
    assert exception["status"] == "open"

    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM saved_schedules").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 1


def test_json_knowledge_stores_are_integrated_with_search():
    faqs = json.loads(knowledge_tools.search_faqs("dispute debit card timeline"))
    products = json.loads(csr_tools.search_product_catalog("idle cash savings"))
    loans = json.loads(csr_tools.search_loan_playbooks("home equity"))
    assert faqs and "dispute" in faqs[0]["question"].lower()
    assert products and "savings" in products[0]["name"].lower()
    assert loans and "equity" in loans[0]["title"].lower()
