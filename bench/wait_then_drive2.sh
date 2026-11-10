#!/bin/bash
# Session-8 helper: wait for a still-running blind job, then start drive2.py.
# The original hardcoded PID and 13:09 PT cutoff are left as comments; pass a PID
# to wait on it, otherwise just start drive2.
# usage: bench/wait_then_drive2.sh [pid]
set -e
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)
PY=${PYTHON:-$ROOT/.venv/bin/python}
[ -x "$PY" ] || PY=python3
PID=${1:-}
if [ -n "$PID" ]; then
  while kill -0 "$PID" 2>/dev/null; do sleep 10; done
fi
exec "$PY" drive2.py
