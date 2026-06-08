#!/bin/sh
set -e

load_env_override() {
  key="$1"
  file="$2"
  [ -f "$file" ] || return 0
  bom="$(printf '\357\273\277')"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#$bom}"
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
load_env_override "MODEL_PROVIDER" "/app/.env"
load_env_override "ACTIVE_PROVIDER" "/app/.env"
load_env_override "FALLBACK_PROVIDER" "/app/.env"
load_env_override "MODEL_ROUTING_CONFIG_PATH" "/app/.env"
load_env_override "MODEL_NAME" "/app/.env"
load_env_override "FAST_MODEL_NAME" "/app/.env"
load_env_override "REASONING_MODEL_NAME" "/app/.env"
load_env_override "CODE_MODEL_NAME" "/app/.env"
load_env_override "CODE_FAST_MODEL_NAME" "/app/.env"
load_env_override "OMNI_MODEL_NAME" "/app/.env"
load_env_override "OMNI_REALTIME_MODEL_NAME" "/app/.env"
load_env_override "EMBEDDING_MODEL_NAME" "/app/.env"
load_env_override "RERANK_MODEL_NAME" "/app/.env"
load_env_override "SAFETY_MODEL_NAME" "/app/.env"
load_env_override "OPENAI_COMPATIBLE_API_KEY" "/app/.env"
load_env_override "OPENAI_COMPATIBLE_BASE_URL" "/app/.env"
load_env_override "EMBEDDING_API_KEY" "/app/.env"
load_env_override "DASHSCOPE_API_KEY" "/app/.env"
load_env_override "KNOWLEDGE_EMBEDDING_MODEL_NAME" "/app/.env"

PORT="${APP_PORT:-8000}"
WORKERS="${UVICORN_WORKERS:-1}"

if [ "$WORKERS" -lt 1 ] 2>/dev/null; then
  WORKERS=1
fi

exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS"
