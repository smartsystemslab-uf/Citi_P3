from __future__ import annotations

import json

from bank_adk.dataset_tables import build_dataset_tables, summarize_tables
from bank_adk.evaluation import SCENARIO_EXPECTATIONS, evaluate_compact_trace
from bank_adk.failure_scenarios import build_trace_cases, dataset_manifest
from bank_adk.infra.repository import BankingRepository
from bank_adk.scenarios import SCENARIOS
from bank_adk.tools import back_office_tools, csr_tools, customer_tools, knowledge_tools, ops_tools


RESULTS = {
    "front_office_monitoring": ("fo_decision_report", {"decision": "manual_review", "summary": "Send to manual review."}),
    "front_office_chatbot": ("fo_knowledge_answer", {"answer": "File a dispute within 60 days."}),
    "front_office_transaction_execution": ("fo_transaction_execution", {"status": "posted", "summary": "125.50 posted."}),
    "mid_office_planning": ("mid_office_planning_result", {"status": "validated", "summary": "Schedule validated."}),
    "mid_office_rep_assist": ("mid_office_representative_result", {"summary": "Loan guidance includes policy and risk checks."}),
    "back_office_clean": ("bo_report_output", {"status": "generated", "summary": "Batch reconciled."}),
    "back_office_mismatch": ("bo_exception_output", {"status": "open", "summary": "Mismatch 190.00 recorded as an exception."}),
}


def passing_trace(scenario_name: str) -> list[dict]:
    expectation = SCENARIO_EXPECTATIONS[scenario_name]
    events = [
        {"agent": "router", "event": "transfer", "to": target}
        for target in expectation.required_transfers
    ]
    events.extend(
        {"agent": "specialist", "event": "tool_call", "tool": tool, "args": {}}
        for tool in expectation.required_tools
    )
    key, value = RESULTS[scenario_name]
    events.append({"agent": "workflow", "event": "result", "key": key, "value": value})
    return events


def test_grounded_workflows_persist_across_tools_and_sqlite(tmp_path, monkeypatch):
    repository = BankingRepository(tmp_path / "observability-workflow.db")
    repository.initialize()
    for module in (customer_tools, csr_tools, ops_tools, back_office_tools):
        monkeypatch.setattr(module, "repo", repository)

    customer = json.loads(customer_tools.get_customer_context("CUST-001"))
    transaction = json.loads(customer_tools.get_transaction_context("TXN-1001"))
    recent = json.loads(customer_tools.list_recent_transactions("CUST-001", 1))
    review = json.loads(customer_tools.submit_manual_review("fraud", '{"transaction_id":"TXN-1001"}'))
    transfer = json.loads(customer_tools.execute_transfer("CHK-002", "EXT-998", 20, "integration", "CUST-002"))
    assert customer["customer_id"] == "CUST-001"
    assert transaction["transaction_id"] == "TXN-1001"
    assert len(recent) == 1
    assert review["status"] == "open"
    assert transfer["status"] == "posted"

    assert json.loads(knowledge_tools.search_policies("AML transfer"))
    assert json.loads(knowledge_tools.search_faqs("dispute timeline"))
    assert json.loads(csr_tools.search_product_catalog("idle cash"))
    assert json.loads(csr_tools.search_loan_playbooks("home equity"))
    assert json.loads(ops_tools.get_operations_snapshot("2026-04-21"))
    schedule = json.loads(ops_tools.persist_validated_schedule('{"status":"validated"}'))
    assert schedule["status"] == "validated"

    json.loads(back_office_tools.apply_ledger_updates("EOD-2026-04-21-CLEAN", "post all items"))
    report = json.loads(back_office_tools.store_report("EOD-2026-04-21-CLEAN", "Reconciled."))
    exception = json.loads(back_office_tools.create_exception_case("EOD-2026-04-21-MISMATCH", "190.00 mismatch"))
    assert report["status"] == "generated"
    assert exception["status"] == "open"

    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM manual_reviews").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM saved_schedules").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 1


def test_all_scenarios_flow_through_failure_plans_evaluation_features_and_tables():
    normal_cases = build_trace_cases(
        selected_scenarios=SCENARIOS.keys(),
        normal_repeats=1,
        failure_repeats=0,
    )
    failure_case = build_trace_cases(
        selected_scenarios=["front_office_chatbot"],
        normal_repeats=0,
        failure_repeats=1,
        failure_types=["tool_omission"],
    )[0]
    manifest = dataset_manifest([*normal_cases, failure_case])
    results = [passing_trace(case.base_scenario) for case in normal_cases]

    evaluations = [
        evaluate_compact_trace(case.case_id, result, message=case.message)
        for case, result in zip(normal_cases, results)
    ]
    assert all(evaluation["overall_pass"] for evaluation in evaluations)

    tables = build_dataset_tables(
        manifest=manifest,
        completed_case_ids=[case.case_id for case in normal_cases],
        completed_results=results,
        failed_cases=[
            {
                "case_id": failure_case.case_id,
                "base_scenario": failure_case.base_scenario,
                "split": failure_case.split,
                "failure_type": failure_case.failure_type,
                "error_type": "IntegrationInjectedFailure",
                "error_message": "tool was intentionally omitted",
                "logged_artifact": True,
            }
        ],
    )
    summary = summarize_tables(tables)
    assert summary["metrics"]["case_count"] == 8
    assert summary["metrics"]["execution_failed_count"] == 1
    assert len(tables["trace_features"]) == 8
    assert len(tables["trace_evaluations"]) == 7
    assert len(tables["trace_errors"]) == 1
