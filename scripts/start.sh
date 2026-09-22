#!/usr/bin/env bash
# Start the server (web UI + API) on http://127.0.0.1:8765
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
exec backend/.venv/bin/python backend/run.py
