from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
BACKEND_ROOT = PROJECT_ROOT / "citi_banking_backend"
MCP_SERVER = ROOT / "mcp_server.py"


@dataclass(frozen=True)
class RunningBackend:
    base_url: str
    db_path: Path


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_backend(tmp_path_factory: pytest.TempPathFactory) -> RunningBackend:
    work_dir = tmp_path_factory.mktemp("mcp-backend")
    db_path = work_dir / "banking.db"
    log_path = work_dir / "backend.log"
    port = _unused_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({"DATABASE_URL": f"sqlite:///{db_path}", "PYTHONUNBUFFERED": "1"})

    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        started = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    started = True
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        if not started:
            process.terminate()
            pytest.fail(f"Backend did not start. Log:\n{log_path.read_text(errors='replace')}")

        yield RunningBackend(base_url=base_url, db_path=db_path)

        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
