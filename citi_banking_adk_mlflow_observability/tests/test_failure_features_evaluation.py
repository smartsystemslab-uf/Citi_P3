import json

import pytest

from bank_adk import evaluation, failure_scenarios, scenarios, trace_features


def _chatbot_trace():
    return [
        {"agent": "root", "event": "transfer", "to": "front_office_router"},
        {"agent": "front", "event": "transfer", "to": "customer_service_chatbot_workflow"},
        {"agent": "knowledge", "event": "tool_call", "tool": "search_faqs", "args": {"query": "dispute"}},
        {"agent": "knowledge", "event": "tool_result", "tool": "search_faqs", "result": {"ok": True}},
        {
            "agent": "knowledge",
            "event": "result",
            "key": "fo_knowledge_answer",
            "value": "You may dispute the charge within 60 days.",
        },
    ]


def test_scenario_normalization_and_failure_dataset_plan():
    assert scenarios.normalize_scenario_name(None) == "unknown"
    assert scenarios.normalize_scenario_name("front_office_chatbot") == "front_office_chatbot"
    assert scenarios.normalize_scenario_name("front_office_chatbot__failure__tool_omission__r001") == "front_office_chatbot"
    assert scenarios.normalize_scenario_name("custom") == "custom"

    cases = failure_scenarios.build_trace_cases(
        selected_scenarios=["front_office_chatbot"],
        normal_repeats=2,
        failure_repeats=1,
        failure_types=["tool_omission", "format_stress"],
    )
    assert len(cases) == 4
    assert cases[0].case_id.endswith("r001")
    assert cases[0].to_dict()["expected_anomaly"] is False
    assert cases[-1].expected_anomaly is True
    manifest = failure_scenarios.dataset_manifest(cases)
    assert manifest["normal_case_count"] == 2
    assert manifest["failure_case_count"] == 2
    assert manifest["by_base_scenario"] == {"front_office_chatbot": 4}
    assert failure_scenarios.build_trace_cases(normal_repeats=-1, failure_repeats=-1) == []
    with pytest.raises(ValueError, match="Unknown scenario"):
        failure_scenarios.build_trace_cases(selected_scenarios=["missing"])
    with pytest.raises(ValueError, match="Unknown failure type"):
        failure_scenarios.build_trace_cases(
            selected_scenarios=["front_office_chatbot"], normal_repeats=0, failure_types=["missing"]
        )


def test_trace_feature_extraction_and_metric_flattening():
    events = _chatbot_trace() + [
        {"agent": "knowledge", "event": "tool_call", "tool": "search_faqs", "args": {"payload": "{broken}"}},
        "ignored",
    ]
    features = trace_features.extract_trace_features(
        scenario_name="front_office_chatbot__failure__format_stress__r007",
        message="hello",
        compact_result=events,
        raw_event_count=9,
        trace_id="trace",
    )
    assert features["base_scenario"] == "front_office_chatbot"
    assert features["split"] == "failure"
    assert features["repeat_id"] == "r007"
    assert features["tool_call_count"] == 2
    assert features["max_repeated_tool_call_count"] == 2
    assert features["malformed_json_argument_count"] == 1
    assert features["has_final_result"] == 1

    unknown = trace_features.extract_trace_features(
        scenario_name=None, message="", compact_result=None, raw_event_count=0
    )
    assert unknown["split"] == "unknown"
    metrics = trace_features.flatten_feature_metrics("bad prefix!", {"yes": True, "number": 2, "text": "no"})
    assert metrics == {"bad_prefix.yes": 1.0, "bad_prefix.number": 2.0}
    assert trace_features._safe_metric_key("!!!") == "metric"
    assert trace_features._as_text(None) == ""
    original_dumps = trace_features.json.dumps
    trace_features.json.dumps = lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad"))
    try:
        assert trace_features._as_text(3) == "3"
    finally:
        trace_features.json.dumps = original_dumps
    assert trace_features._count_malformed_json_arguments([{"args": {"number": 1}}]) == 0


def test_evaluator_passing_unknown_and_failure_traces():
    passed = evaluation.evaluate_compact_trace("front_office_chatbot", _chatbot_trace(), message="question")
    assert passed["overall_pass"] is True
    assert passed["domain"] == "front_office"
    assert passed["metrics"]["overall_pass"] == 1

    unknown = evaluation.evaluate_compact_trace(None, None)
    assert unknown["overall_pass"] is False
    assert unknown["domain"] == "unknown"
    assert unknown["checks"]["expectation_defined"] is False

    failing = [
        {"event": "tool_call", "tool": "forbidden", "args": {"json": "{broken}"}},
        {
            "event": "result",
            "key": "mid_office_representative_result",
            "value": "Email person@example.com phone 352-555-0101 SSN 123-45-6789 account CHK-001 balance $128,500.00",
        },
    ]
    result = evaluation.evaluate_compact_trace("mid_office_rep_assist", failing)
    assert result["overall_pass"] is False
    assert result["metrics"]["pii_leak_detected"] == 1
    assert result["metrics"]["malformed_json_argument_count"] == 1
    assert any("sensitive data" in finding for finding in result["findings"])

    mismatch = [
        {"event": "result", "key": "bo_exception_output", "value": {"status": "open", "summary": "mismatch 1990.00 exception"}}
    ]
    mismatch_result = evaluation.evaluate_compact_trace("back_office_mismatch", mismatch)
    assert mismatch_result["checks"]["mismatch_amount_consistent"] is False
    assert any("190.00" in finding for finding in mismatch_result["findings"])


def test_evaluation_helpers_aggregation_and_flattening():
    assert evaluation._check_expected_decision({}, None)
    assert evaluation._check_expected_decision({"x": {"decision": "approve"}}, "approve")
    assert evaluation._check_expected_decision({"x": "manual_review selected"}, "manual_review")
    assert not evaluation._check_expected_decision({}, "approve")
    assert evaluation._check_expected_status([], None)
    assert evaluation._check_expected_status([{"result": {"status": "open"}}], "open")
    assert evaluation._check_expected_status([{"value": "generated report"}], "generated")
    assert not evaluation._check_expected_status([], "open")
    assert evaluation._check_mismatch_amount_consistency("190.00", "190.00")
    assert evaluation._check_mismatch_amount_consistency("anything", None)
    pii = evaluation._detect_final_response_pii("person@example.com 352-555-0101")
    assert pii["email_leak_detected"] == 1
    metrics = evaluation.flatten_metrics("bad prefix!", {"flag": True, "count": 2, "text": "skip"})
    assert metrics == {"bad_prefix.flag": 1.0, "bad_prefix.count": 2.0}

    good = evaluation.evaluate_compact_trace("front_office_chatbot", _chatbot_trace())
    aggregate = evaluation.aggregate_evaluations([good, {"scenario_name": "bad", "overall_pass": False, "metrics": "bad"}])
    assert aggregate["metrics"]["scenario_count"] == 2
    assert aggregate["metrics"]["pass_rate"] == 0.5
    assert evaluation.aggregate_evaluations([])["metrics"]["pass_rate"] == 0

    original_dumps = evaluation.json.dumps
    evaluation.json.dumps = lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad"))
    try:
        assert evaluation._as_text(3) == "3"
    finally:
        evaluation.json.dumps = original_dumps
    assert evaluation._count_malformed_json_arguments([{"args": {"number": 1}}]) == 0

    finding_trace = [
        {"event": "tool_call", "tool": "create_exception_case", "args": {}},
        {"event": "result", "key": "other", "value": "reconciled $128,500.00"},
    ]
    findings = evaluation.evaluate_compact_trace("back_office_clean", finding_trace)["findings"]
    assert any("final result keys" in item for item in findings)
    assert any("Forbidden tools" in item for item in findings)
    assert any("allowed for this scenario" in item for item in findings)
