import json
import math
from types import SimpleNamespace

import pytest

from bank_adk import observability as obs


def _event(**kwargs):
    defaults = dict(
        trace_id="trace",
        scenario_name="front_office_monitoring",
        run_index=1,
        sequence=1,
        timestamp_utc="2026-01-01T00:00:00Z",
        workflow="front_office",
        agent="agent",
        event_type="tool_call",
    )
    defaults.update(kwargs)
    return obs.ObservabilityEvent(**defaults)


def test_serialization_text_and_numeric_helpers():
    assert obs.stable_trace_id("scenario", 1, "message") == obs.stable_trace_id("scenario", 1, "message")
    assert len(obs.stable_trace_id("scenario", 1, "message")) == 16
    assert obs.json_dumps({"é": object()})
    assert obs.json_size_bytes({"a": 1}) > 0
    assert obs.maybe_json({"a": 1}) == {"a": 1}
    assert obs.maybe_json(None) is None
    assert obs.maybe_json(object()).startswith("<object")
    assert obs.maybe_json("  ") == ""
    assert obs.maybe_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert obs.maybe_json("not json") == "not json"
    assert obs.flatten_text(None) == ""
    assert obs.flatten_text("a\n b") == "a b"
    assert obs.flatten_text({"a": 1}) == '{"a":1}'
    assert obs.flatten_text("x" * 20, 10) == "xxxxxxx..."

    assert obs.clamp01("bad") == 0
    assert obs.clamp01(math.nan) == 0
    assert obs.clamp01(math.inf) == 0
    assert obs.clamp01(-1) == 0
    assert obs.clamp01(2) == 1
    assert obs.confidence_to_risk(None) == 0
    assert obs.confidence_to_risk("low") == 0.9
    assert obs.confidence_to_risk("medium") == 0.5
    assert obs.confidence_to_risk("high") == 0.1
    assert obs.confidence_to_risk("bad") == 0
    assert obs.confidence_to_risk(80) == pytest.approx(0.2)
    assert obs.risk_level_to_score(None) == 0
    assert obs.risk_level_to_score("critical") == 1
    assert obs.risk_level_to_score("high") == 0.9
    assert obs.risk_level_to_score("review") == 0.6
    assert obs.risk_level_to_score("clean") == 0.2
    assert obs.risk_level_to_score("other") == 0

    assert obs.infer_workflow("back", "a", {}) == "back_office"
    assert obs.infer_workflow("mid", "a", {}) == "mid_office"
    assert obs.infer_workflow("front", "a", {}) == "front_office"
    assert obs.infer_workflow("none", "a", {}) == "unknown"
    assert obs.dict_get_deep([], ["a"]) is None
    assert obs.dict_get_deep({"b": 2}, ["a", "b"]) == 2
    assert obs.dict_get_deep({}, ["a"]) is None


def test_score_and_select_events_cover_risk_decision_and_cost_branches():
    critical = _event(
        event_type="exception",
        tool_name="execute_transfer",
        state_key="fo_decision_report",
        payload={
            "risk_level": "high",
            "manual_review_needed": True,
            "suspicious": True,
            "matched": False,
            "confidence": "low",
            "latency_ms": 6000,
            "tokens": 9000,
        },
        payload_summary="fraud mismatch failed low confidence",
    )
    obs.select_event(obs.score_event(critical))
    assert critical.risk_score == 1
    assert critical.state_change_score == 1
    assert critical.decision_impact_score == 0.9
    assert critical.anomaly_score == 1
    assert critical.confidence_risk_score == 0.9
    assert critical.cost_latency_score == 1
    assert critical.selected_for_monitoring
    assert {"risk", "state_change", "decision", "anomaly", "low_confidence"} <= set(critical.tags)
    assert "critical_event_type" in critical.keep_reason

    posted = _event(event_type="tool_result", payload_summary="record updated", payload={"duration_ms": 2500, "total_tokens": 3500})
    obs.score_event(posted)
    assert posted.state_change_score == 0.8
    assert posted.decision_impact_score == 0.2
    assert posted.cost_latency_score == 0.6

    routing = _event(event_type="transfer", payload={"ready": True})
    obs.score_event(routing)
    assert routing.decision_impact_score == 0.9
    plain_transfer = _event(event_type="transfer", payload="not-json")
    obs.score_event(plain_transfer)
    assert plain_transfer.decision_impact_score == 0.7

    invalid_cost = _event(payload={"elapsed_ms": "bad", "token_usage": "bad"})
    obs.score_event(invalid_cost)
    assert invalid_cost.cost_latency_score == 0
    obs.select_event(invalid_cost, threshold=1)
    assert not invalid_cost.selected_for_monitoring
    assert invalid_cost.keep_reason == ""


def test_event_conversion_and_full_normalization():
    object_event = SimpleNamespace(
        id="E1",
        author="object-agent",
        timestamp=1,
        invocation_id="I1",
        branch="main",
        actions=SimpleNamespace(
            transfer_to_agent="fraud_agent",
            state_delta={"fo_decision_report": '{"decision":"manual_review"}', "other": 3},
            requested_auth_configs=None,
            escalate=False,
        ),
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    text="model text",
                    function_call=SimpleNamespace(name="execute_transfer", args={"amount": 1}),
                    function_response=None,
                ),
                SimpleNamespace(
                    text=None,
                    function_call=None,
                    function_response=SimpleNamespace(name="submit_manual_review", response='{"status":"created"}'),
                ),
            ]
        ),
    )
    base = obs._event_obj_to_base(object_event)
    assert base["author"] == "object-agent"
    assert base["actions"]["transfer_to_agent"] == "fraud_agent"
    assert len(base["parts"]) == 2
    assert obs._event_obj_to_base({"author": "mapping"}) == {"author": "mapping"}
    assert "raw" in obs._event_obj_to_base(object())

    class ExplodingAttributes:
        def __getattribute__(self, name):
            if name in {"id", "actions", "content"}:
                raise RuntimeError(name)
            return object.__getattribute__(self, name)

        def __str__(self):
            return "exploding"

    assert obs._event_obj_to_base(ExplodingAttributes()) == {"raw": "exploding"}

    raw_events = [
        object_event,
        {
            "author": "back-agent",
            "parts": [
                {"function_response": {"name": "create_exception_case", "response": {"status": "created"}}},
                {"function_response": {"name": "apply_ledger_updates", "response": {"status": "updated"}}},
                {"function_response": {"name": "health", "response": {"status": "ok"}}},
                {"text": "reconciliation mismatch error"},
            ],
        },
        {"author": "plain", "value": 1},
    ]
    records = obs.normalize_events(
        raw_events,
        scenario_name="front_office_monitoring",
        run_index=2,
        message="message",
        threshold=0.2,
    )
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert {record.event_type for record in records} >= {
        "transfer",
        "result",
        "state_change",
        "tool_call",
        "manual_review",
        "exception",
        "ledger_update",
        "tool_result",
        "model_text",
        "raw_event",
    }
    assert all(len(record.trace_id) == 16 for record in records)


def test_summaries_and_export_formats():
    assert obs.summarize_records([]) == {
        "total_events": 0,
        "selected_events": 0,
        "raw_size_bytes": 0,
        "selected_size_bytes": 0,
        "compression_ratio": 0.0,
        "selection_rate": 0.0,
        "critical_event_recall": 1.0,
    }
    selected = _event(event_type="error", raw_size_bytes=100, importance_score=0.9, selected_for_monitoring=True, keep_reason="risk,anomaly")
    dropped = _event(sequence=2, event_type="model_text", raw_size_bytes=300, importance_score=0.1)
    summary = obs.summarize_records([selected, dropped])
    assert summary["total_events"] == 2
    assert summary["selected_events"] == 1
    assert summary["compression_ratio"] == 0.25
    assert summary["critical_event_recall"] == 1
    assert summary["event_type_counts"] == {"error": 1, "model_text": 1}

    runs = [
        {"scenario_name": "a", "total_events": 2, "runtime_seconds": 1.0},
        {"scenario_name": "a", "total_events": 4, "runtime_seconds": 3.0},
        {"scenario_name": "b", "total_events": 1},
    ]
    assert obs.summarize_runs([]) == {}
    aggregate = obs.summarize_runs(runs)
    assert aggregate["overall"]["runs"] == 3
    assert aggregate["by_scenario"]["a"]["avg_total_events"] == 3

    without_payload = selected.to_dict(include_payload=False)
    assert "payload" not in without_payload
    assert obs.records_to_jsonl([]) == ""
    line = obs.records_to_jsonl([selected], include_payload=True)
    assert json.loads(line)["event_type"] == "error"
    rows = obs.records_to_csv_rows([selected])
    assert rows[0]["keep_reason"] == "risk,anomaly"
