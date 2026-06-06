-- Persist per-user resource library state without changing the core resource schema.

CREATE TABLE IF NOT EXISTS app.user_resource_state (
  user_id          UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  resource_id      UUID NOT NULL REFERENCES app.learning_resource(id) ON DELETE CASCADE,
  is_favorite      BOOLEAN NOT NULL DEFAULT FALSE,
  progress_percent INT NOT NULL DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
  completed        BOOLEAN NOT NULL DEFAULT FALSE,
  last_study_at    TIMESTAMPTZ,
  metadata_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_user_resource_state_user_favorite
ON app.user_resource_state(user_id, is_favorite, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_resource_state_user_study
ON app.user_resource_state(user_id, last_study_at DESC);

ALTER TABLE app.user_resource_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_user_resource_state_rw ON app.user_resource_state;
CREATE POLICY p_user_resource_state_rw ON app.user_resource_state
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP TRIGGER IF EXISTS trg_user_resource_state_touch_updated_at ON app.user_resource_state;
CREATE TRIGGER trg_user_resource_state_touch_updated_at
BEFORE UPDATE ON app.user_resource_state
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

ANALYZE app.user_resource_state;
