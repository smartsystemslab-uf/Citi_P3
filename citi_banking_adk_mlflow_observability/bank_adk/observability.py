from __future__ import annotations

import atexit
import os
import re
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import mlflow
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from .evaluation import aggregate_evaluations, evaluate_compact_trace, flatten_metrics
from .scenarios import normalize_scenario_name
from .settings import settings
from .trace_features import extract_trace_features, flatten_feature_metrics
from .dataset_tables import log_dataset_tables as _log_dataset_tables

_SAFE_ARTIFACT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_INITIALIZED = False
_PROVIDER: Optional[TracerProvider] = None
_EXPERIMENT_ID: Optional[str] = None


@dataclass(frozen=True)
class ObservabilityState:
    experiment_id: str
    tracking_uri: str
    otlp_endpoint: str


def observability_enabled() -> bool:
    return settings.mlflow_enable_tracing


def _safe_name(value: str, *, default: str) -> str:
    cleaned = _SAFE_ARTIFACT_RE.sub("_", (value or "").strip()).strip("._")
    return cleaned or default


def _infer_domain(scenario_name: Optional[str], message: str) -> str:
    key = normalize_scenario_name(scenario_name).lower()
    if key.startswith("front_office"):
        return "front_office"
    if key.startswith("mid_office"):
        return "mid_office"
    if key.startswith("back_office"):
        return "back_office"

    text = message.lower()
    if "front-office" in text or "customer" in text:
        return "front_office"
    if "mid-office" in text or "representative" in text or "staffing" in text:
        return "mid_office"
    if "back-office" in text or "end-of-day" in text or "reconciliation" in text:
        return "back_office"
    return "unknown"


def init_observability() -> Optional[ObservabilityState]:
    global _INITIALIZED, _PROVIDER, _EXPERIMENT_ID

    if not observability_enabled():
        return None
    if _INITIALIZED and _PROVIDER is not None and _EXPERIMENT_ID is not None:
        return ObservabilityState(
            experiment_id=_EXPERIMENT_ID,
            tracking_uri=settings.mlflow_tracking_uri,
            otlp_endpoint=settings.resolved_mlflow_otlp_endpoint,
        )

    # ADK owns the trace. We only export ADK OpenTelemetry spans to MLflow via OTLP.
    # Do not call mlflow.tracing.set_destination(...), because that creates a second write path.
    os.environ.setdefault("MLFLOW_USE_DEFAULT_TRACER_PROVIDER", "false")

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    experiment = mlflow.set_experiment(settings.mlflow_experiment_name)
    _EXPERIMENT_ID = experiment.experiment_id

    resource = Resource.create(
        {
            "service.name": settings.app_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.mlflow_environment,
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.resolved_mlflow_otlp_endpoint,
        headers={"x-mlflow-experiment-id": str(_EXPERIMENT_ID)},
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    _PROVIDER = provider
    _INITIALIZED = True
    atexit.register(shutdown_observability)
    return ObservabilityState(
        experiment_id=_EXPERIMENT_ID,
        tracking_uri=settings.mlflow_tracking_uri,
        otlp_endpoint=settings.resolved_mlflow_otlp_endpoint,
    )


@contextmanager
def start_batch_run(run_name: str, *, scenario_names: list[str] | None = None) -> Iterator[Any]:
    if not observability_enabled():
        yield None
        return

    init_observability()
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags(
            {
                "app.name": settings.app_name,
                "app.version": settings.app_version,
                "app.component": "google_adk",
                "deployment.environment": settings.mlflow_environment,
                "user.id": settings.user_id,
                "session.id": settings.session_id,
                "observability.transport": "adk_opentelemetry_otlp_to_mlflow",
                "evaluation.enabled": "true",
                "trace.features.enabled": "true",
            }
        )
        if scenario_names:
            mlflow.set_tag("scenario.names", ",".join(scenario_names))
            mlflow.log_metric("scenario_count", len(scenario_names))
        yield run


@contextmanager
def start_request_trace(
    message: str,
    *,
    scenario_name: Optional[str] = None,
    compact: bool = True,
) -> Iterator[Any]:
    """No-op request context.

    ADK already creates the root trace through OpenTelemetry. This context only makes
    sure observability is initialized before ADK runs. Keeping it no-op avoids duplicate
    root spans or duplicate trace rows in MLflow.
    """
    if observability_enabled():
        init_observability()
    yield None


def _log_numeric_metrics(metrics: dict[str, float]) -> None:
    for key, value in metrics.items():
        try:
            mlflow.log_metric(key, float(value))
        except Exception:
            # Metric logging should never break the agent workflow.
            pass


def log_request_artifacts(
    *,
    message: str,
    scenario_name: Optional[str],
    compact_result: Any,
    raw_event_count: int,
) -> dict[str, Any] | None:
    if not observability_enabled() or mlflow.active_run() is None:
        return None

    artifact_stem = _safe_name(scenario_name or "request", default="request")
    base_scenario = normalize_scenario_name(scenario_name)
    trace_id = mlflow.get_active_trace_id()
    domain = _infer_domain(scenario_name, message)

    payload = {
        "case_id": scenario_name,
        "base_scenario": base_scenario,
        "workflow_domain": domain,
        "trace_id": trace_id,
        "message": message,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": compact_result,
    }
    mlflow.log_dict(payload, f"artifacts/traces/{artifact_stem}_trace.json")

    features = extract_trace_features(
        scenario_name=scenario_name,
        message=message,
        compact_result=compact_result,
        raw_event_count=raw_event_count,
        trace_id=trace_id,
    )
    mlflow.log_dict(features, f"artifacts/features/{artifact_stem}_features.json")
    _log_numeric_metrics(flatten_feature_metrics(f"{artifact_stem}.features", features))

    if isinstance(compact_result, list):
        num_tool_calls = sum(1 for item in compact_result if isinstance(item, dict) and item.get("event") == "tool_call")
        num_transfers = sum(1 for item in compact_result if isinstance(item, dict) and item.get("event") == "transfer")
        num_results = sum(1 for item in compact_result if isinstance(item, dict) and item.get("event") == "result")
        mlflow.log_metric(f"{artifact_stem}.tool_calls", num_tool_calls)
        mlflow.log_metric(f"{artifact_stem}.transfers", num_transfers)
        mlflow.log_metric(f"{artifact_stem}.results", num_results)

    mlflow.log_metric(f"{artifact_stem}.raw_event_count", raw_event_count)

    evaluation = evaluate_compact_trace(scenario_name, compact_result, message=message)
    mlflow.log_dict(evaluation, f"artifacts/evaluations/{artifact_stem}_evaluation.json")
    _log_numeric_metrics(flatten_metrics(f"{artifact_stem}.eval", evaluation.get("metrics", {})))

    if trace_id:
        mlflow.log_dict({"trace_id": trace_id}, f"artifacts/trace_ids/{artifact_stem}_trace_id.json")

    return evaluation


def log_error_artifact(
    *,
    case_id: str,
    message: str,
    error: BaseException,
    case_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Log failed dataset cases without aborting the entire batch."""
    if not observability_enabled() or mlflow.active_run() is None:
        return None

    artifact_stem = _safe_name(case_id, default="failed_case")
    trace_id = mlflow.get_active_trace_id()
    payload = {
        "case_id": case_id,
        "base_scenario": normalize_scenario_name(case_id),
        "trace_id": trace_id,
        "message": message,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc(),
        "case_metadata": case_metadata or {},
    }
    mlflow.log_dict(payload, f"artifacts/errors/{artifact_stem}_error.json")
    mlflow.log_metric(f"{artifact_stem}.error", 1)
    return payload


def log_batch_evaluation_summary(scenario_names: list[str], results: list[Any]) -> dict[str, Any] | None:
    if not observability_enabled() or mlflow.active_run() is None:
        return None

    evaluations: list[dict[str, Any]] = []
    for scenario_name, result in zip(scenario_names, results):
        if isinstance(result, list):
            evaluations.append(evaluate_compact_trace(scenario_name, result))

    summary = aggregate_evaluations(evaluations)
    mlflow.log_dict(summary, "artifacts/evaluation_summary.json")
    _log_numeric_metrics(flatten_metrics("batch.eval", summary.get("metrics", {})))

    failed = [row["scenario_name"] for row in summary.get("scenarios", []) if not row.get("overall_pass")]
    if failed:
        mlflow.set_tag("evaluation.failed_scenarios", ",".join(str(item) for item in failed))
    else:
        mlflow.set_tag("evaluation.failed_scenarios", "none")

    return summary


def log_dataset_manifest(manifest: dict[str, Any]) -> None:
    if not observability_enabled() or mlflow.active_run() is None:
        return
    mlflow.log_dict(manifest, "artifacts/dataset_manifest.json")
    metrics = {
        "dataset.case_count": manifest.get("case_count", 0),
        "dataset.normal_case_count": manifest.get("normal_case_count", 0),
        "dataset.failure_case_count": manifest.get("failure_case_count", 0),
    }
    _log_numeric_metrics({key: float(value) for key, value in metrics.items() if isinstance(value, int | float)})


def log_dataset_run_summary(summary: dict[str, Any]) -> None:
    if not observability_enabled() or mlflow.active_run() is None:
        return
    mlflow.log_dict(summary, "artifacts/dataset_run_summary.json")
    numeric = {
        "dataset.completed_count": summary.get("completed_count", 0),
        "dataset.failed_count": summary.get("failed_count", 0),
        "dataset.success_rate": summary.get("success_rate", 0.0),
    }
    _log_numeric_metrics({key: float(value) for key, value in numeric.items() if isinstance(value, int | float)})



def log_dataset_csv_tables(
    *,
    manifest: dict[str, Any],
    completed_case_ids: list[str],
    completed_results: list[Any],
    failed_cases: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not observability_enabled() or mlflow.active_run() is None:
        return None
    return _log_dataset_tables(
        manifest=manifest,
        completed_case_ids=completed_case_ids,
        completed_results=completed_results,
        failed_cases=failed_cases,
    )

def flush_observability() -> None:
    if _PROVIDER is None:
        return
    try:
        _PROVIDER.force_flush()
    except Exception:
        pass


def shutdown_observability() -> None:
    if _PROVIDER is None:
        return
    try:
        _PROVIDER.force_flush()
    except Exception:
        pass
    try:
        _PROVIDER.shutdown()
    except Exception:
        pass
