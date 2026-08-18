from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("BANKING_ADK_APP_NAME", "citi_banking_adk")
    app_version: str = os.getenv("BANKING_ADK_APP_VERSION", "0.1.0")
    user_id: str = os.getenv("BANKING_ADK_USER_ID", "demo_user")
    session_id: str = os.getenv("BANKING_ADK_SESSION_ID", "demo_session")

    api_key: str = os.getenv("UF_NAVIGATOR_API_KEY", "")
    base_url: str = os.getenv("UF_NAVIGATOR_BASE_URL", "https://api.ai.it.ufl.edu")
    model_name: str = os.getenv("UF_NAVIGATOR_MODEL", "gpt-oss-20b")

    mlflow_enable_tracing: bool = os.getenv("MLFLOW_ENABLE_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}
    mlflow_tracking_uri: str = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    mlflow_experiment_name: str = os.getenv("MLFLOW_EXPERIMENT_NAME", "citi-banking-adk")
    mlflow_environment: str = os.getenv("MLFLOW_ENVIRONMENT", "dev")
    mlflow_otlp_endpoint: str = os.getenv("MLFLOW_OTLP_ENDPOINT", "").strip()

    package_dir: Path = _PACKAGE_DIR
    data_dir: Path = Path(os.getenv("BANKING_ADK_DATA_DIR", str(_PACKAGE_DIR / "data"))).expanduser()
    runtime_dir: Path = Path(os.getenv("BANKING_ADK_RUNTIME_DIR", str(_PACKAGE_DIR / ".runtime"))).expanduser()

    @property
    def api_base(self) -> str:
        normalized = self.base_url.rstrip("/")
        return normalized if normalized.endswith("/v1") else f"{normalized}/v1"

    @property
    def llm_model(self) -> str:
        raw = self.model_name.strip()
        return raw if raw.startswith("openai/") else f"openai/{raw}"

    @property
    def resolved_mlflow_otlp_endpoint(self) -> str:
        if self.mlflow_otlp_endpoint:
            return self.mlflow_otlp_endpoint
        return f"{self.mlflow_tracking_uri.rstrip('/')}/v1/traces"

    @property
    def sqlite_path(self) -> Path:
        return self.runtime_dir / "banking.db"


settings = Settings()
