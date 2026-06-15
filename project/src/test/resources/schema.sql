CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.user_llm_config (
  user_id UUID PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  active_provider TEXT NOT NULL DEFAULT '',
  fallback_provider TEXT NOT NULL DEFAULT '',
  provider_config_json TEXT NOT NULL DEFAULT '{}',
  component_overrides_json TEXT NOT NULL DEFAULT '{}',
  encrypted_secrets_json TEXT NOT NULL DEFAULT '{}',
  secret_meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
