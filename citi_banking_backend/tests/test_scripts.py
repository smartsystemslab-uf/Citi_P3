import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import smoke_test


def test_smoke_script_uses_all_expected_endpoints(monkeypatch, capsys):
    calls = []

    class Client:
        def __init__(self, timeout):
            assert timeout == 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return SimpleNamespace(json=lambda: {"ok": True})

        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return SimpleNamespace(json=lambda: {"ok": True})

    monkeypatch.setattr(smoke_test.httpx, "Client", Client)
    smoke_test.main()
    assert len(calls) == 6
    assert calls[0][1].endswith("/health")
    assert calls[-1][1].endswith("/monitoring/evaluate_transaction")
    assert "Health" in capsys.readouterr().out


def test_run_server_shell_script_default_and_overridden_address(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    output = tmp_path / "uvicorn-args.txt"
    fake_uvicorn = fake_bin / "uvicorn"
    fake_uvicorn.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$UVICORN_TEST_OUTPUT"\n')
    fake_uvicorn.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_server.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["UVICORN_TEST_OUTPUT"] = str(output)
    subprocess.run(["bash", str(script)], check=True, env=env)
    assert output.read_text().splitlines() == ["app.main:app", "--host", "0.0.0.0", "--port", "8000"]

    env["API_HOST"] = "127.0.0.1"
    env["API_PORT"] = "9000"
    subprocess.run(["bash", str(script)], check=True, env=env)
    assert output.read_text().splitlines()[-4:] == ["--host", "127.0.0.1", "--port", "9000"]
