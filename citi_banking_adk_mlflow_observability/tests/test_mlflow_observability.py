from contextlib import contextmanager
from types import SimpleNamespace

from bank_adk import observability as obs


class FakeMlflow:
    def __init__(self):
        self.calls = []
        self.active = object()
        self.trace_id = "trace-1"

    def set_tracking_uri(self, value):
        self.calls.append(("tracking", value))

    def set_experiment(self, value):
        self.calls.append(("experiment", value))
        return SimpleNamespace(experiment_id="exp-1")

    @contextmanager
    def start_run(self, **kwargs):
        run = SimpleNamespace(info="run")
        self.calls.append(("start_run", kwargs))
        yield run

    def set_tags(self, value):
        self.calls.append(("tags", value))

    def set_tag(self, key, value):
        self.calls.append(("tag", key, value))

    def log_metric(self, key, value):
        self.calls.append(("metric", key, value))

    def log_dict(self, value, path):
        self.calls.append(("dict", path, value))

    def active_run(self):
        return self.active

    def get_active_trace_id(self):
        return self.trace_id


def _settings(enabled):
    return SimpleNamespace(
        mlflow_enable_tracing=enabled,
        mlflow_tracking_uri="file:///tmp/mlruns",
        mlflow_experiment_name="unit",
        mlflow_environment="test",
        app_name="app",
        app_version="1",
        user_id="user",
        session_id="session",
        resolved_mlflow_otlp_endpoint="http://otel/v1/traces",
    )


def test_names_domains_and_disabled_noops(monkeypatch):
    monkeypatch.setattr(obs, "settings", _settings(False))
    assert obs.observability_enabled() is False
    assert obs._safe_name(" bad/name ", default="x") == "bad_name"
    assert obs._safe_name("...", default="x") == "x"
    assert obs._infer_domain("front_office_chatbot", "") == "front_office"
    assert obs._infer_domain("mid_office_planning", "") == "mid_office"
    assert obs._infer_domain("back_office_clean", "") == "back_office"
    assert obs._infer_domain(None, "customer asks") == "front_office"
    assert obs._infer_domain(None, "representative staffing") == "mid_office"
    assert obs._infer_domain(None, "end-of-day reconciliation") == "back_office"
    assert obs._infer_domain(None, "nothing") == "unknown"
    assert obs.init_observability() is None
    with obs.start_batch_run("disabled") as run:
        assert run is None
    with obs.start_request_trace("message") as span:
        assert span is None
    assert obs.log_request_artifacts(message="m", scenario_name=None, compact_result=[], raw_event_count=0) is None
    assert obs.log_error_artifact(case_id="c", message="m", error=ValueError()) is None
    assert obs.log_batch_evaluation_summary([], []) is None
    assert obs.log_dataset_manifest({}) is None
    assert obs.log_dataset_run_summary({}) is None
    assert obs.log_dataset_csv_tables(manifest={}, completed_case_ids=[], completed_results=[], failed_cases=[]) is None


def test_initialization_batch_and_request_context(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setattr(obs, "settings", _settings(True))
    monkeypatch.setattr(obs, "mlflow", fake_mlflow)
    monkeypatch.setattr(obs, "_INITIALIZED", False)
    monkeypatch.setattr(obs, "_PROVIDER", None)
    monkeypatch.setattr(obs, "_EXPERIMENT_ID", None)
    monkeypatch.setattr(obs.Resource, "create", lambda value: value)
    monkeypatch.setattr(obs, "OTLPSpanExporter", lambda **kwargs: SimpleNamespace(kwargs=kwargs))

    class Provider:
        def __init__(self, resource):
            self.resource = resource
            self.processors = []

        def add_span_processor(self, processor):
            self.processors.append(processor)

    monkeypatch.setattr(obs, "TracerProvider", Provider)
    monkeypatch.setattr(obs, "SimpleSpanProcessor", lambda exporter: ("processor", exporter))
    providers = []
    monkeypatch.setattr(obs.otel_trace, "set_tracer_provider", providers.append)
    registered = []
    monkeypatch.setattr(obs.atexit, "register", registered.append)

    state = obs.init_observability()
    assert state.experiment_id == "exp-1"
    assert providers and registered == [obs.shutdown_observability]
    assert obs.init_observability() == state
    with obs.start_batch_run("batch", scenario_names=["a", "b"]) as run:
        assert run.info == "run"
    assert ("metric", "scenario_count", 2) in fake_mlflow.calls
    with obs.start_batch_run("empty"):
        pass
    with obs.start_request_trace("message", scenario_name="a") as span:
        assert span is None


def test_artifact_evaluation_error_and_dataset_logging(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setattr(obs, "settings", _settings(True))
    monkeypatch.setattr(obs, "mlflow", fake_mlflow)
    events = [
        {"event": "transfer", "to": "front_office_router", "agent": "root"},
        {"event": "transfer", "to": "customer_service_chatbot_workflow", "agent": "front"},
        {"event": "tool_call", "tool": "search_faqs", "args": {"query": "dispute"}},
        {"event": "result", "key": "fo_knowledge_answer", "value": "dispute within 60 days"},
    ]
    evaluation = obs.log_request_artifacts(
        message="question", scenario_name="front_office_chatbot", compact_result=events, raw_event_count=7
    )
    assert evaluation["overall_pass"] is True
    assert any(call[:2] == ("dict", "artifacts/features/front_office_chatbot_features.json") for call in fake_mlflow.calls)

    fake_mlflow.trace_id = None
    obs.log_request_artifacts(message="x", scenario_name=None, compact_result="not-list", raw_event_count=1)
    payload = obs.log_error_artifact(
        case_id="case/bad", message="m", error=ValueError("boom"), case_metadata={"split": "failure"}
    )
    assert payload["error_type"] == "ValueError"

    summary = obs.log_batch_evaluation_summary(["front_office_chatbot", "ignored"], [events, "not-list"])
    assert summary["metrics"]["scenario_count"] == 1
    assert ("tag", "evaluation.failed_scenarios", "none") in fake_mlflow.calls
    failed_summary = obs.log_batch_evaluation_summary(["unknown"], [[]])
    assert failed_summary["metrics"]["failed_scenario_count"] == 1
    assert any(call[0] == "tag" and call[2] == "unknown" for call in fake_mlflow.calls)

    obs.log_dataset_manifest({"case_count": 2, "normal_case_count": 1, "failure_case_count": 1, "text": "skip"})
    obs.log_dataset_run_summary({"completed_count": 1, "failed_count": 1, "success_rate": 0.5})
    monkeypatch.setattr(obs, "_log_dataset_tables", lambda **kwargs: {"logged": kwargs})
    table = obs.log_dataset_csv_tables(manifest={}, completed_case_ids=[], completed_results=[], failed_cases=[])
    assert "logged" in table


def test_metric_failures_flush_and_shutdown(monkeypatch):
    class SometimesFail:
        calls = []

        @classmethod
        def log_metric(cls, key, value):
            cls.calls.append((key, value))
            if key == "bad":
                raise RuntimeError("metric failure")

    monkeypatch.setattr(obs, "mlflow", SometimesFail)
    obs._log_numeric_metrics({"good": 1, "bad": 2})
    assert SometimesFail.calls == [("good", 1.0), ("bad", 2.0)]

    class Provider:
        def __init__(self, fail=False):
            self.fail = fail
            self.flushes = 0
            self.shutdowns = 0

        def force_flush(self):
            self.flushes += 1
            if self.fail:
                raise RuntimeError("flush")

        def shutdown(self):
            self.shutdowns += 1
            if self.fail:
                raise RuntimeError("shutdown")

    monkeypatch.setattr(obs, "_PROVIDER", None)
    obs.flush_observability()
    obs.shutdown_observability()
    provider = Provider()
    monkeypatch.setattr(obs, "_PROVIDER", provider)
    obs.flush_observability()
    obs.shutdown_observability()
    assert provider.flushes == 2 and provider.shutdowns == 1
    monkeypatch.setattr(obs, "_PROVIDER", Provider(fail=True))
    obs.flush_observability()
    obs.shutdown_observability()

