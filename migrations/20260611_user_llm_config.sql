CREATE TABLE IF NOT EXISTS app.user_llm_config (
  user_id                   UUID PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
  enabled                   BOOLEAN NOT NULL DEFAULT FALSE,
  active_provider           TEXT NOT NULL DEFAULT '',
  fallback_provider         TEXT NOT NULL DEFAULT '',
  provider_config_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  component_overrides_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  encrypted_secrets_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
  secret_meta_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE app.user_llm_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_user_llm_config_rw ON app.user_llm_config;
CREATE POLICY p_user_llm_config_rw ON app.user_llm_config
USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP TRIGGER IF EXISTS trg_user_llm_config_touch_updated_at ON app.user_llm_config;
CREATE TRIGGER trg_user_llm_config_touch_updated_at
BEFORE UPDATE ON app.user_llm_config
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
