#!/bin/sh
set -e

PORT="${APP_PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-1}"

if [ "$WORKERS" -lt 1 ] 2>/dev/null; then
  WORKERS=1
fi

exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS"
