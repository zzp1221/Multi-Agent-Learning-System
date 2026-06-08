#!/bin/sh
set -e

load_env_override() {
  key="$1"
  file="$2"
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$key="*)
        value="${line#*=}"
        value="$(printf '%s' "$value" | tr -d '\r')"
        [ -n "$value" ] && export "$key=$value"
        return 0
        ;;
    esac
  done < "$file"
}

load_env_override "TAVILY_API_KEY" "/app/.env"
load_env_override "PYTHON_AGENT_INTERNAL_TOKEN" "/app/.env"
load_env_override "EMBEDDING_API_KEY" "/app/.env"
load_env_override "DASHSCOPE_API_KEY" "/app/.env"
load_env_override "KNOWLEDGE_EMBEDDING_MODEL_NAME" "/app/.env"

PORT="${APP_PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-1}"

if [ "$WORKERS" -lt 1 ] 2>/dev/null; then
  WORKERS=1
fi

exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS"
