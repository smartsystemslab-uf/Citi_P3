#!/usr/bin/env bash
set -euo pipefail
HOST=${API_HOST:-0.0.0.0}
PORT=${API_PORT:-8000}
uvicorn app.main:app --host "$HOST" --port "$PORT"
