-- AI 笔记本：独立笔记域、版本、AI 缓存与资源关联。
CREATE TABLE IF NOT EXISTS app.note_folder (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  parent_id   UUID REFERENCES app.note_folder(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  sort_order  INT NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, parent_id, name)
);

CREATE INDEX IF NOT EXISTS idx_note_folder_user_sort
ON app.note_folder(user_id, parent_id, sort_order, updated_at DESC);

CREATE TABLE IF NOT EXISTS app.note (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  folder_id          UUID REFERENCES app.note_folder(id) ON DELETE SET NULL,
  title              TEXT NOT NULL,
  markdown_content   TEXT NOT NULL DEFAULT '',
  plain_text         TEXT NOT NULL DEFAULT '',
  content_hash       TEXT NOT NULL DEFAULT '',
  word_count         INT NOT NULL DEFAULT 0 CHECK (word_count >= 0),
  reading_minutes    INT NOT NULL DEFAULT 1 CHECK (reading_minutes >= 1),
  status             TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ARCHIVED', 'DELETED')),
  last_saved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  rag_resource_id    UUID REFERENCES app.learning_resource(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_note_user_status_updated
ON app.note(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_note_folder_updated
ON app.note(user_id, folder_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS app.note_tag (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  color      TEXT NOT NULL DEFAULT '#4f46e5',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_note_tag_user_name
ON app.note_tag(user_id, name);

CREATE TABLE IF NOT EXISTS app.note_tag_link (
  note_id UUID NOT NULL REFERENCES app.note(id) ON DELETE CASCADE,
  tag_id  UUID NOT NULL REFERENCES app.note_tag(id) ON DELETE CASCADE,
  PRIMARY KEY (note_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_note_tag_link_tag
ON app.note_tag_link(tag_id, note_id);

CREATE TABLE IF NOT EXISTS app.note_version (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  note_id          UUID NOT NULL REFERENCES app.note(id) ON DELETE CASCADE,
  user_id          UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  version_no       INT NOT NULL,
  title            TEXT NOT NULL,
  markdown_content TEXT NOT NULL,
  plain_text       TEXT NOT NULL DEFAULT '',
  content_hash     TEXT NOT NULL DEFAULT '',
  change_summary   TEXT NOT NULL DEFAULT '',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (note_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_note_version_note_created
ON app.note_version(note_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.note_ai_artifact (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  note_id       UUID NOT NULL REFERENCES app.note(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('SUMMARY', 'KEYWORDS', 'TODOS')),
  input_hash    TEXT NOT NULL,
  result_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  provider      TEXT NOT NULL DEFAULT '',
  model         TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (note_id, artifact_type, input_hash)
);

CREATE INDEX IF NOT EXISTS idx_note_ai_artifact_note_type
ON app.note_ai_artifact(note_id, artifact_type, created_at DESC);

CREATE TABLE IF NOT EXISTS app.note_resource_link (
  note_id       UUID NOT NULL REFERENCES app.note(id) ON DELETE CASCADE,
  resource_id   UUID NOT NULL REFERENCES app.learning_resource(id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL DEFAULT 'RELATED' CHECK (relation_type IN ('RELATED', 'CITED', 'GENERATED_FROM')),
  score         NUMERIC(6,4) NOT NULL DEFAULT 0,
  reason        TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (note_id, resource_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_note_resource_link_note_score
ON app.note_resource_link(note_id, score DESC);

DROP TRIGGER IF EXISTS trg_note_folder_touch_updated_at ON app.note_folder;
CREATE TRIGGER trg_note_folder_touch_updated_at
BEFORE UPDATE ON app.note_folder
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_note_touch_updated_at ON app.note;
CREATE TRIGGER trg_note_touch_updated_at
BEFORE UPDATE ON app.note
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_note_tag_touch_updated_at ON app.note_tag;
CREATE TRIGGER trg_note_tag_touch_updated_at
BEFORE UPDATE ON app.note_tag
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
