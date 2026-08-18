from __future__ import annotations

import os
import tempfile


# The ADK app bootstraps during module import. Isolate that real SQLite state
# from the checked-in source tree before pytest imports test modules.
os.environ.setdefault("BANKING_ADK_RUNTIME_DIR", tempfile.mkdtemp(prefix="citi-mlflow-integration-"))
os.environ.setdefault("MLFLOW_ENABLE_TRACING", "false")
os.environ.setdefault("UF_NAVIGATOR_API_KEY", "integration-test-key-not-used")
