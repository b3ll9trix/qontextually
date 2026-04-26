#!/usr/bin/env bash
# Launch the Qontextually API server fully detached from the caller's
# process group so it survives the parent exiting.
set -eu
cd "$(dirname "$0")/.."
pkill -9 -f "uvicorn lib.api" 2>/dev/null || true
sleep 1
LOG=/tmp/qontext_api.log
nohup setsid .venv/bin/python -m uvicorn lib.api:app \
    --host 127.0.0.1 --port 8000 --workers 1 \
    >>"$LOG" 2>&1 </dev/null &
disown || true
sleep 2
pgrep -af "uvicorn lib.api" || { echo "server failed to start; tail log:"; tail -20 "$LOG"; exit 1; }
echo "API running on http://127.0.0.1:8000 (log: $LOG)"
