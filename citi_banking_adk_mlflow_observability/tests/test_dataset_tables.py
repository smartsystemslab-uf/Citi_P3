import json

from bank_adk import dataset_tables


def _manifest():
    return {
        "cases": [
            {
                "case_id": "front_office_chatbot__normal__baseline__r001",
                "base_scenario": "front_office_chatbot",
                "split": "normal",
                "failure_type": "baseline",
                "repeat_index": 1,
                "expected_anomaly": False,
                "message": "dispute question",
            },
            {
                "case_id": "front_office_chatbot__failure__format_stress__r001",
                "base_scenario": "front_office_chatbot",
                "split": "failure",
                "failure_type": "format_stress",
                "repeat_index": 1,
                "expected_anomaly": True,
                "message": "bad json",
            },
        ]
    }


def _result():
    return [
        {"event": "transfer", "to": "front_office_router", "agent": "root"},
        {"event": "transfer", "to": "customer_service_chatbot_workflow", "agent": "front"},
        {"event": "tool_call", "tool": "search_faqs", "args": {"query": "dispute"}, "agent": "knowledge"},
        {"event": "result", "key": "fo_knowledge_answer", "value": "dispute within 60 days", "agent": "knowledge"},
    ]


def test_csv_cell_helpers_and_table_building():
    assert dataset_tables._json_cell(None) == ""
    assert dataset_tables._json_cell(True) == "1"
    assert dataset_tables._json_cell(2) == "2"
    assert json.loads(dataset_tables._json_cell({"b": 2, "a": 1})) == {"a": 1, "b": 2}
    assert dataset_tables._scalar_or_json("x") == "x"
    assert dataset_tables._manifest_case_map(None) == {}
    assert dataset_tables._ordered_fieldnames([{"z": 1, "case_id": "c"}]) == ["case_id", "z"]
    assert dataset_tables._rows_to_csv([]) == ""
    assert dataset_tables._rows_to_csv([{"case_id": "c", "nested": [1]}]).splitlines()[0] == "case_id,nested"
    original_dumps = dataset_tables.json.dumps
    dataset_tables.json.dumps = lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad"))
    try:
        assert dataset_tables._json_cell([1]) == "[1]"
    finally:
        dataset_tables.json.dumps = original_dumps

    failed = {
        "case_id": "front_office_chatbot__failure__format_stress__r001",
        "base_scenario": "front_office_chatbot",
        "split": "failure",
        "failure_type": "format_stress",
        "error_type": "JSONDecodeError",
        "error_message": "bad json",
        "logged_artifact": True,
    }
    tables = dataset_tables.build_dataset_tables(
        manifest=_manifest(),
        completed_case_ids=["front_office_chatbot__normal__baseline__r001"],
        completed_results=[_result()],
        failed_cases=[failed],
    )
    assert len(tables["trace_features"]) == 2
    assert len(tables["trace_labels"]) == 2
    assert len(tables["trace_evaluations"]) == 1
    assert len(tables["trace_errors"]) == 1
    assert tables["trace_features"][1]["malformed_json_argument_count"] == 1
    summary = dataset_tables.summarize_tables(tables)
    assert summary["metrics"]["case_count"] == 2
    assert summary["metrics"]["execution_failed_count"] == 1
    assert dataset_tables.summarize_tables({})["metrics"]["case_count"] == 0


def test_log_dataset_tables(monkeypatch):
    calls = []
    fake_mlflow = type(
        "FakeMlflow",
        (),
        {
            "log_text": staticmethod(lambda value, path: calls.append(("text", path, value))),
            "log_dict": staticmethod(lambda value, path: calls.append(("dict", path, value))),
            "log_metric": staticmethod(lambda key, value: calls.append(("metric", key, value))),
        },
    )
    monkeypatch.setattr(dataset_tables, "mlflow", fake_mlflow)
    summary = dataset_tables.log_dataset_tables(
        manifest=_manifest(),
        completed_case_ids=["front_office_chatbot__normal__baseline__r001"],
        completed_results=[_result()],
        failed_cases=[],
    )
    assert summary["metrics"]["case_count"] == 1
    assert sum(call[0] == "text" for call in calls) == 4
    assert any(call[0] == "metric" for call in calls)
