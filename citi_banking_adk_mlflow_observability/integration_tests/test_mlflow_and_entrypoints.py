from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from bank_adk.failure_scenarios import build_trace_cases, dataset_manifest
from bank_adk.observability import (
    log_batch_evaluation_summary,
    log_dataset_csv_tables,
    log_dataset_manifest,
    log_dataset_run_summary,
    log_request_artifacts,
)

from test_workflow_dataset_pipeline import passing_trace


ROOT = Path(__file__).resolve().parents[1]


def test_observability_artifacts_and_metrics_reach_a_real_mlflow_file_store(tmp_path, monkeypatch):
    import bank_adk.observability as observability

    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment(
        "citi-integration-observability",
        artifact_location=(tmp_path / "artifacts").as_uri(),
    )
    experiment = mlflow.set_experiment(experiment_id=experiment_id)
    monkeypatch.setattr(observability, "observability_enabled", lambda: True)

    cases = build_trace_cases(selected_scenarios=["front_office_chatbot"], normal_repeats=1, failure_repeats=0)
    case = cases[0]
    trace = passing_trace(case.base_scenario)
    manifest = dataset_manifest(cases)

    with mlflow.start_run(run_name="integration-observability") as active:
        run_id = active.info.run_id
        log_dataset_manifest(manifest)
        evaluation = log_request_artifacts(
            message=case.message,
            scenario_name=case.case_id,
            compact_result=trace,
            raw_event_count=len(trace),
        )
        batch = log_batch_evaluation_summary([case.case_id], [trace])
        table_summary = log_dataset_csv_tables(
            manifest=manifest,
            completed_case_ids=[case.case_id],
            completed_results=[trace],
            failed_cases=[],
        )
        log_dataset_run_summary(
            {"completed_count": 1, "failed_count": 0, "success_rate": 1.0, "table_summary": table_summary}
        )

    assert evaluation and evaluation["overall_pass"] is True
    assert batch and batch["metrics"]["pass_rate"] == 1.0
    assert table_summary and table_summary["metrics"]["case_count"] == 1

    run = client.get_run(run_id)
    assert run.info.experiment_id == experiment.experiment_id
    assert run.data.metrics["dataset.success_rate"] == 1.0
    artifact_paths = {
        item.path
        for directory in client.list_artifacts(run_id, "artifacts")
        for item in ([directory] if not directory.is_dir else client.list_artifacts(run_id, directory.path))
    }
    assert "artifacts/dataset_manifest.json" in artifact_paths
    assert any(path.endswith("_trace.json") for path in artifact_paths)
    assert any(path.startswith("artifacts/tables/") for path in artifact_paths)


def test_all_observability_entrypoints_import_and_parse_help(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "BANKING_ADK_RUNTIME_DIR": str(tmp_path / "runtime"),
            "MLFLOW_ENABLE_TRACING": "false",
            "UF_NAVIGATOR_API_KEY": "integration-test-key-not-used",
        }
    )
    for script in ("run_cli.py", "run_scenarios.py", "run_trace_dataset.py"):
        completed = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert completed.returncode == 0, f"{script}: {completed.stderr}"
        assert "usage:" in completed.stdout.lower()
