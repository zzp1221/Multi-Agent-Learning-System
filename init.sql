-- =========================================================
-- 智学系统数据库初始化脚本（优化版）
-- PostgreSQL: 关系型 + 向量
-- MongoDB: 对话正文与流式事件
-- RustFS: 资源文件本体（本脚本仅保留其对象元数据）
-- =========================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE SCHEMA IF NOT EXISTS storage;

-- =========================================================
-- 枚举
-- =========================================================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='access_scope' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.access_scope AS ENUM ('GLOBAL', 'COURSE', 'USER');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='conversation_mode' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.conversation_mode AS ENUM ('QNA', 'SMART_ENGINE');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='engine_status' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.engine_status AS ENUM ('IDLE', 'RUNNING', 'COMPLETED', 'FAILED');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='difficulty_level' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.difficulty_level AS ENUM ('BASIC', 'INTERMEDIATE', 'ADVANCED', 'MIXED');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='resource_type' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.resource_type AS ENUM (
      'DOCUMENT',
      'PPT',
      'QUIZ',
      'VIDEO',
      'AUDIO',
      'IMAGE',
      'CODE',
      'MINDMAP',
      'READING',
      'PRACTICE'
    );
  END IF;

  -- 说明：GENERATED 仅保留为扩展来源枚举，不用于多智能体实时生成文件落库
  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='source_kind' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.source_kind AS ENUM ('UPLOAD', 'GENERATED', 'IMPORTED', 'MANUAL', 'WEB');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE t.typname='task_status' AND n.nspname='app'
  ) THEN
    CREATE TYPE app.task_status AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT');
  END IF;
END$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'app' AND t.typname = 'resource_type' AND e.enumlabel = 'SLIDES'
  ) THEN
    ALTER TYPE app.resource_type ADD VALUE 'SLIDES';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'app' AND t.typname = 'resource_type' AND e.enumlabel = 'VIDEO_SCRIPT'
  ) THEN
    ALTER TYPE app.resource_type ADD VALUE 'VIDEO_SCRIPT';
  END IF;
END$$;

-- =========================================================
-- 用户/课程
-- =========================================================
CREATE TABLE IF NOT EXISTS app.users (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  login_id          TEXT UNIQUE NOT NULL,
  password_hash     TEXT NOT NULL,
  full_name         TEXT NOT NULL,
  major_code        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.courses (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  course_code       TEXT UNIQUE NOT NULL,
  course_name       TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.user_course_enrollments (
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id         UUID NOT NULL REFERENCES app.courses(id) ON DELETE CASCADE,
  role_in_course    TEXT NOT NULL CHECK (role_in_course IN ('student', 'ta', 'teacher')),
  PRIMARY KEY (user_id, course_id)
);

-- =========================================================
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

-- RustFS 对象元数据
-- =========================================================
CREATE TABLE IF NOT EXISTS storage.resource_object (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider          TEXT NOT NULL DEFAULT 'RUSTFS' CHECK (provider='RUSTFS'),
  bucket_name       TEXT,
  object_key        TEXT NOT NULL UNIQUE,
  file_name         TEXT NOT NULL,
  mime_type         TEXT,
  size_bytes        BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
  checksum_sha256   TEXT,
  access_mode       TEXT NOT NULL DEFAULT 'PRIVATE' CHECK (access_mode IN ('PRIVATE', 'DIRECT', 'PRESIGNED')),
  storage_url       TEXT,
  uploaded_by       UUID REFERENCES app.users(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================================================
-- 学习资源主表
-- =========================================================
CREATE TABLE IF NOT EXISTS app.learning_resource (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title             TEXT NOT NULL,
  domain            TEXT NOT NULL,
  resource_type     app.resource_type NOT NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  source_kind       app.source_kind NOT NULL,
  access_scope      app.access_scope NOT NULL DEFAULT 'GLOBAL',
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  storage_object_id UUID REFERENCES storage.resource_object(id) ON DELETE SET NULL,
  summary_text      TEXT,
  tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  status            TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('PROCESSING', 'ACTIVE', 'DISABLED', 'FAILED')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_learning_resource_scope_domain
ON app.learning_resource(access_scope, domain, resource_type, difficulty_level);

CREATE INDEX IF NOT EXISTS idx_learning_resource_owner_course
ON app.learning_resource(owner_user_id, course_id);

-- 用户资源库状态：收藏、学习进度、最近学习时间。
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

-- =========================================================
-- AI 笔记本
-- =========================================================
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

-- =========================================================
-- 对话元数据
-- =========================================================
CREATE TABLE IF NOT EXISTS app.qna_session (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  title               TEXT NOT NULL DEFAULT '新对话',
  mongo_thread_id     TEXT NOT NULL UNIQUE,
  entry_source        TEXT NOT NULL DEFAULT 'NEW_CONVERSATION' CHECK (entry_source IN ('NEW_CONVERSATION', 'HISTORY_REOPEN')),
  current_mode        app.conversation_mode NOT NULL DEFAULT 'QNA',
  last_message_at     TIMESTAMPTZ,
  last_message_preview TEXT,
  message_count       INT NOT NULL DEFAULT 0 CHECK (message_count >= 0),
  active_profile_version INT NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qna_session_user_created
ON app.qna_session(user_id, created_at DESC);

-- =========================================================
-- 用户画像（当前态 + 历史 + 画像向量）
-- =========================================================
CREATE TABLE IF NOT EXISTS app.user_profile_snapshot (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  source_session_id     UUID REFERENCES app.qna_session(id) ON DELETE SET NULL,
  version               INT NOT NULL,
  profile_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary_text          TEXT NOT NULL DEFAULT '',
  confidence            NUMERIC(4,3) NOT NULL DEFAULT 0.700 CHECK (confidence >= 0 AND confidence <= 1),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, version)
);

CREATE TABLE IF NOT EXISTS app.user_profile_current (
  user_id             UUID PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
  active_snapshot_id  UUID REFERENCES app.user_profile_snapshot(id) ON DELETE SET NULL,
  profile_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary_text        TEXT NOT NULL DEFAULT '',
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.learner_feature (
  id                    BIGSERIAL PRIMARY KEY,
  user_id               UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  dimension             TEXT NOT NULL,
  feature_key           TEXT NOT NULL,
  canonical_key         TEXT,
  feature_value         JSONB NOT NULL DEFAULT '{}'::jsonb,
  aliases               JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence            REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
  source_type           TEXT NOT NULL DEFAULT 'CONVERSATION',
  source_ref            JSONB,
  reasoning             TEXT NOT NULL DEFAULT '',
  evidence              JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification_count    INT NOT NULL DEFAULT 1,
  decay_enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  stability_period_days INT NOT NULL DEFAULT 30,
  decay_rate            REAL NOT NULL DEFAULT 0.05,
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  status                TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'RESOLVED', 'REGRESSED', 'ARCHIVED')),
  resolved_at           TIMESTAMPTZ,
  resolved_reason       TEXT NOT NULL DEFAULT '',
  resolved_by           JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_observed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  inferred              BOOLEAN NOT NULL DEFAULT FALSE,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, dimension, feature_key)
);

CREATE INDEX IF NOT EXISTS idx_learner_feature_user_dim
ON app.learner_feature(user_id, dimension, is_active, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_learner_feature_confidence
ON app.learner_feature(user_id, confidence DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_learner_feature_canonical_status
ON app.learner_feature(user_id, dimension, canonical_key, status, is_active, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_learner_feature_resolved
ON app.learner_feature(user_id, status, resolved_at DESC);

CREATE TABLE IF NOT EXISTS rag.user_profile_vector (
  id                  BIGSERIAL PRIMARY KEY,
  profile_snapshot_id UUID NOT NULL UNIQUE REFERENCES app.user_profile_snapshot(id) ON DELETE CASCADE,
  user_id             UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  version             INT NOT NULL,
  embedding           VECTOR(1024) NOT NULL,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, version)
);

CREATE INDEX IF NOT EXISTS idx_user_profile_vector_user_active
ON rag.user_profile_vector(user_id, is_active, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_profile_vector_embedding_ivfflat
ON rag.user_profile_vector USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- =========================================================
-- LLM Wiki / 结构化知识层
-- =========================================================
CREATE TABLE IF NOT EXISTS rag.wiki_page (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL,
  title             TEXT NOT NULL,
  domain            TEXT NOT NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  access_scope      app.access_scope NOT NULL DEFAULT 'GLOBAL',
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  aliases           JSONB NOT NULL DEFAULT '[]'::jsonb,
  tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
  frontmatter_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary_text      TEXT,
  markdown_content  TEXT NOT NULL,
  version           INT NOT NULL DEFAULT 1,
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (slug, version),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_scope_domain
ON rag.wiki_page(access_scope, domain, difficulty_level, is_active);

CREATE TABLE IF NOT EXISTS rag.wiki_link (
  id                BIGSERIAL PRIMARY KEY,
  from_page_id      UUID NOT NULL REFERENCES rag.wiki_page(id) ON DELETE CASCADE,
  to_page_id        UUID NOT NULL REFERENCES rag.wiki_page(id) ON DELETE CASCADE,
  relation_type     TEXT NOT NULL CHECK (relation_type IN ('WIKILINK', 'SHARED_TAG', 'SHARED_SOURCE', 'COMMUNITY')),
  weight            NUMERIC(6,3) NOT NULL DEFAULT 1.000 CHECK (weight > 0),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (from_page_id, to_page_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_wiki_link_from_type
ON rag.wiki_link(from_page_id, relation_type, weight DESC);

CREATE INDEX IF NOT EXISTS idx_wiki_link_to_type
ON rag.wiki_link(to_page_id, relation_type, weight DESC);

CREATE TABLE IF NOT EXISTS rag.wiki_page_graph_features (
  page_id            UUID PRIMARY KEY REFERENCES rag.wiki_page(id) ON DELETE CASCADE,
  community_id       BIGINT NOT NULL,
  pagerank_score     NUMERIC(12,8) NOT NULL DEFAULT 0,
  in_degree          INT NOT NULL DEFAULT 0,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_graph_features_community
ON rag.wiki_page_graph_features(community_id, pagerank_score DESC);

CREATE TABLE IF NOT EXISTS rag.term_lexicon (
  id                BIGSERIAL PRIMARY KEY,
  domain            TEXT NOT NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  canonical_term    TEXT NOT NULL,
  normalized_term   TEXT NOT NULL,
  aliases           JSONB NOT NULL DEFAULT '[]'::jsonb,
  term_type         TEXT NOT NULL DEFAULT 'TERM' CHECK (term_type IN ('TERM', 'ALIAS', 'COURSE', 'ABBR')),
  idf_score         NUMERIC(8,4) NOT NULL DEFAULT 1.000 CHECK (idf_score >= 0),
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (domain, course_id, normalized_term)
);

CREATE INDEX IF NOT EXISTS idx_term_lexicon_lookup
ON rag.term_lexicon(domain, course_id, is_active, normalized_term);

CREATE TABLE IF NOT EXISTS rag.synonym_group (
  id                BIGSERIAL PRIMARY KEY,
  domain            TEXT NOT NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  canonical_term    TEXT NOT NULL,
  variants          JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_kind       TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source_kind IN ('MANUAL', 'WIKI_FILTERED', 'IMPORTED')),
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (domain, course_id, canonical_term)
);

CREATE INDEX IF NOT EXISTS idx_synonym_group_lookup
ON rag.synonym_group(domain, course_id, is_active, canonical_term);

-- =========================================================
-- 公共课程知识向量集合
-- =========================================================
CREATE TABLE IF NOT EXISTS rag.knowledge_document (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title             TEXT NOT NULL,
  domain            TEXT NOT NULL,
  source_type       TEXT NOT NULL CHECK (source_type IN ('pdf', 'docx', 'ppt', 'md', 'web', 'manual', 'generated', 'image', 'audio', 'video')),
  source_ref        TEXT,
  external_doc_id   TEXT,
  content_hash      TEXT,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  access_scope      app.access_scope NOT NULL DEFAULT 'GLOBAL',
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  version           INT NOT NULL DEFAULT 1,
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  created_by        TEXT NOT NULL DEFAULT 'offline_ingestor',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_document_external_version
ON rag.knowledge_document(course_id, domain, external_doc_id, version);

CREATE TABLE IF NOT EXISTS rag.knowledge_chunk (
  id                BIGSERIAL PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES rag.knowledge_document(id) ON DELETE CASCADE,
  chunk_no          INT NOT NULL,
  content           TEXT NOT NULL,
  embedding         VECTOR(1024) NOT NULL,
  token_count       INT,
  domain            TEXT NOT NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  access_scope      app.access_scope NOT NULL,
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  quality_score     REAL NOT NULL DEFAULT 0.5 CHECK (quality_score >= 0 AND quality_score <= 1),
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL)),
  UNIQUE (document_id, chunk_no)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_scope_domain
ON rag.knowledge_chunk(access_scope, domain, difficulty_level);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_embedding_ivfflat
ON rag.knowledge_chunk USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- =========================================================
-- 资源路径向量集合
-- =========================================================
CREATE TABLE IF NOT EXISTS rag.resource_document (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id       UUID NOT NULL UNIQUE REFERENCES app.learning_resource(id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  domain            TEXT NOT NULL,
  resource_type     app.resource_type NOT NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  source_kind       app.source_kind NOT NULL,
  source_ref        TEXT,
  summary_text      TEXT,
  transcript_text   TEXT,
  access_scope      app.access_scope NOT NULL,
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS rag.resource_chunk (
  id                BIGSERIAL PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES rag.resource_document(id) ON DELETE CASCADE,
  resource_id       UUID NOT NULL REFERENCES app.learning_resource(id) ON DELETE CASCADE,
  chunk_no          INT NOT NULL,
  content           TEXT NOT NULL,
  embedding         VECTOR(1024) NOT NULL,
  token_count       INT,
  domain            TEXT NOT NULL,
  resource_type     app.resource_type NOT NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  access_scope      app.access_scope NOT NULL,
  owner_user_id     UUID REFERENCES app.users(id) ON DELETE SET NULL,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  quality_score     REAL NOT NULL DEFAULT 0.5 CHECK (quality_score >= 0 AND quality_score <= 1),
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope <> 'USER') OR (owner_user_id IS NOT NULL)),
  CHECK ((access_scope <> 'COURSE') OR (course_id IS NOT NULL)),
  UNIQUE (document_id, chunk_no)
);

CREATE INDEX IF NOT EXISTS idx_resource_chunk_scope_domain
ON rag.resource_chunk(access_scope, domain, resource_type, difficulty_level);

CREATE INDEX IF NOT EXISTS idx_resource_chunk_embedding_ivfflat
ON rag.resource_chunk USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- =========================================================
-- 学习运行态 / 任务链路
-- =========================================================
CREATE TABLE IF NOT EXISTS app.smart_engine_task (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  qna_session_id    UUID REFERENCES app.qna_session(id) ON DELETE SET NULL,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  trace_id          TEXT NOT NULL UNIQUE,
  service_type      TEXT NOT NULL,
  task_status       app.task_status NOT NULL DEFAULT 'PENDING',
  current_stage     TEXT,
  progress_percent  NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
  request_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
  response_summary  JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_code        TEXT,
  error_message     TEXT,
  started_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_smart_engine_task_user_created
ON app.smart_engine_task(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_smart_engine_task_status
ON app.smart_engine_task(task_status, service_type, created_at DESC);

CREATE TABLE IF NOT EXISTS app.generated_artifact (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id           UUID NOT NULL REFERENCES app.smart_engine_task(id) ON DELETE CASCADE,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  resource_type     app.resource_type NOT NULL,
  title             TEXT NOT NULL,
  file_name         TEXT NOT NULL,
  mime_type         TEXT,
  sandbox_path      TEXT NOT NULL,
  size_bytes        BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
  checksum_sha256   TEXT,
  download_token    TEXT NOT NULL UNIQUE,
  expires_at        TIMESTAMPTZ NOT NULL,
  download_count    INT NOT NULL DEFAULT 0 CHECK (download_count >= 0),
  artifact_status   TEXT NOT NULL DEFAULT 'READY' CHECK (artifact_status IN ('READY', 'DOWNLOADED', 'EXPIRED', 'DELETED')),
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_generated_artifact_task
ON app.generated_artifact(task_id, resource_type, created_at DESC);

CREATE TABLE IF NOT EXISTS rag.video_generation_task (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id              UUID UNIQUE REFERENCES app.smart_engine_task(id) ON DELETE CASCADE,
  resource_document_id UUID REFERENCES rag.resource_document(id) ON DELETE SET NULL,
  student_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  trace_id             TEXT UNIQUE,
  title                TEXT NOT NULL,
  topic                TEXT NOT NULL,
  script_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
  script_text          TEXT NOT NULL DEFAULT '',
  status               TEXT NOT NULL DEFAULT 'pending' CHECK (
    status IN (
      'pending',
      'script_generated',
      'speech_synthesized',
      'video_rendering',
      'completed',
      'failed'
    )
  ),
  audio_path           TEXT,
  avatar_video_path    TEXT,
  animation_video_path TEXT,
  final_video_path     TEXT,
  thumbnail_path       TEXT,
  active_provider      TEXT,
  fallback_provider    TEXT,
  tts_provider         TEXT,
  avatar_provider      TEXT,
  duration_seconds     INT CHECK (duration_seconds IS NULL OR duration_seconds > 0),
  video_style          TEXT CHECK (
    video_style IS NULL OR video_style IN ('talking_head', 'animation', 'hybrid')
  ),
  generation_params    JSONB NOT NULL DEFAULT '{}'::jsonb,
  critic_score         NUMERIC(3,2) CHECK (
    critic_score IS NULL OR (critic_score >= 0 AND critic_score <= 1)
  ),
  safety_passed        BOOLEAN NOT NULL DEFAULT FALSE,
  error_message        TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_video_generation_task_status
ON rag.video_generation_task(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_video_generation_task_student
ON rag.video_generation_task(student_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_video_generation_task_provider
ON rag.video_generation_task(active_provider, tts_provider, avatar_provider);

CREATE TABLE IF NOT EXISTS app.learning_plan (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  plan_json         JSONB NOT NULL,
  status            TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.learning_plan_snapshot (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id           UUID NOT NULL REFERENCES app.learning_plan(id) ON DELETE CASCADE,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  version           INT NOT NULL,
  trigger_source    TEXT NOT NULL DEFAULT 'INITIAL' CHECK (trigger_source IN ('INITIAL', 'PROFILE_UPDATE', 'PRACTICE_RESULT', 'EVALUATION', 'MANUAL_REFRESH')),
  plan_json         JSONB NOT NULL,
  summary_text      TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (plan_id, version)
);

CREATE INDEX IF NOT EXISTS idx_learning_plan_snapshot_user
ON app.learning_plan_snapshot(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.autonomous_learning_loop (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id             UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  task_id               UUID REFERENCES app.smart_engine_task(id) ON DELETE SET NULL,
  conversation_id       TEXT,
  goal_text             TEXT NOT NULL DEFAULT '',
  planning_level        TEXT NOT NULL DEFAULT 'goal_loop',
  status                TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'COMPLETED', 'PARTIAL_FAILED', 'BLOCKED', 'ARCHIVED')),
  current_subgoal_order INT NOT NULL DEFAULT 1 CHECK (current_subgoal_order >= 1),
  loop_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autonomous_learning_loop_user_status
ON app.autonomous_learning_loop(user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_autonomous_learning_loop_task
ON app.autonomous_learning_loop(task_id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_autonomous_learning_loop_id_user'
      AND conrelid = 'app.autonomous_learning_loop'::regclass
  ) THEN
    ALTER TABLE app.autonomous_learning_loop
      ADD CONSTRAINT uq_autonomous_learning_loop_id_user UNIQUE(id, user_id);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS app.autonomous_learning_subgoal (
  id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  loop_id                         UUID NOT NULL REFERENCES app.autonomous_learning_loop(id) ON DELETE CASCADE,
  user_id                         UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  order_index                     INT NOT NULL CHECK (order_index >= 1),
  title                           TEXT NOT NULL,
  objective                       TEXT NOT NULL DEFAULT '',
  success_criteria                TEXT NOT NULL DEFAULT '',
  target_knowledge_points_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
  preferred_resource_types_json   JSONB NOT NULL DEFAULT '[]'::jsonb,
  assigned_preset                 TEXT NOT NULL DEFAULT '',
  status                          TEXT NOT NULL DEFAULT 'PENDING'
                                  CHECK (status IN ('PENDING', 'RUNNING', 'ACHIEVED', 'NEEDS_REPLAN', 'BLOCKED', 'SKIPPED')),
  attempt_count                   INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  result_json                     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(loop_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_learning_subgoal_loop_order
ON app.autonomous_learning_subgoal(loop_id, order_index);

CREATE INDEX IF NOT EXISTS idx_autonomous_learning_subgoal_user_status
ON app.autonomous_learning_subgoal(user_id, status, updated_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_autonomous_learning_subgoal_loop_user'
      AND conrelid = 'app.autonomous_learning_subgoal'::regclass
  ) THEN
    ALTER TABLE app.autonomous_learning_subgoal
      ADD CONSTRAINT fk_autonomous_learning_subgoal_loop_user
      FOREIGN KEY (loop_id, user_id)
      REFERENCES app.autonomous_learning_loop(id, user_id)
      ON DELETE CASCADE;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS app.autonomous_planning_checkpoint (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  loop_id           UUID REFERENCES app.autonomous_learning_loop(id) ON DELETE CASCADE,
  subgoal_id        UUID REFERENCES app.autonomous_learning_subgoal(id) ON DELETE SET NULL,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  checkpoint_type   TEXT NOT NULL
                    CHECK (checkpoint_type IN ('PROFILE_COMPLETENESS', 'RETRIEVAL_EVIDENCE', 'RESOURCE_COVERAGE', 'GOAL_CRITIC')),
  trigger_reason    TEXT NOT NULL DEFAULT '',
  action            TEXT NOT NULL DEFAULT '',
  before_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  status            TEXT NOT NULL DEFAULT 'RECORDED'
                    CHECK (status IN ('RECORDED', 'APPLIED', 'SKIPPED', 'FAILED')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autonomous_planning_checkpoint_loop_type
ON app.autonomous_planning_checkpoint(loop_id, checkpoint_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_autonomous_planning_checkpoint_user
ON app.autonomous_planning_checkpoint(user_id, created_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_autonomous_planning_checkpoint_loop_user'
      AND conrelid = 'app.autonomous_planning_checkpoint'::regclass
  ) THEN
    ALTER TABLE app.autonomous_planning_checkpoint
      ADD CONSTRAINT fk_autonomous_planning_checkpoint_loop_user
      FOREIGN KEY (loop_id, user_id)
      REFERENCES app.autonomous_learning_loop(id, user_id)
      ON DELETE CASCADE;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS app.autonomous_replan_event (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  loop_id         UUID NOT NULL REFERENCES app.autonomous_learning_loop(id) ON DELETE CASCADE,
  subgoal_id      UUID REFERENCES app.autonomous_learning_subgoal(id) ON DELETE SET NULL,
  user_id         UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  reason          TEXT NOT NULL DEFAULT '',
  old_plan_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  new_plan_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  attempt_no      INT NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
  accepted        BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autonomous_replan_event_loop
ON app.autonomous_replan_event(loop_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_autonomous_replan_event_user
ON app.autonomous_replan_event(user_id, created_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_autonomous_replan_event_loop_user'
      AND conrelid = 'app.autonomous_replan_event'::regclass
  ) THEN
    ALTER TABLE app.autonomous_replan_event
      ADD CONSTRAINT fk_autonomous_replan_event_loop_user
      FOREIGN KEY (loop_id, user_id)
      REFERENCES app.autonomous_learning_loop(id, user_id)
      ON DELETE CASCADE;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS app.practice_set (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id           UUID REFERENCES app.smart_engine_task(id) ON DELETE SET NULL,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  course_id         UUID REFERENCES app.courses(id) ON DELETE SET NULL,
  source_resource_id UUID REFERENCES app.learning_resource(id) ON DELETE SET NULL,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  question_count    INT NOT NULL DEFAULT 0 CHECK (question_count >= 0),
  set_status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (set_status IN ('OPEN', 'SUBMITTED', 'JUDGED', 'ARCHIVED')),
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_practice_set_user_created
ON app.practice_set(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.practice_item (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  practice_set_id   UUID NOT NULL REFERENCES app.practice_set(id) ON DELETE CASCADE,
  item_no           INT NOT NULL,
  question_type     TEXT NOT NULL CHECK (question_type IN ('SINGLE_CHOICE', 'MULTIPLE_CHOICE', 'FILL_BLANK', 'SHORT_ANSWER', 'CODING')),
  stem              TEXT NOT NULL,
  options_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
  standard_answer   JSONB NOT NULL DEFAULT '{}'::jsonb,
  rubric_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  knowledge_tags    JSONB NOT NULL DEFAULT '[]'::jsonb,
  difficulty_level  app.difficulty_level NOT NULL DEFAULT 'MIXED',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (practice_set_id, item_no)
);

CREATE INDEX IF NOT EXISTS idx_practice_item_set
ON app.practice_item(practice_set_id, item_no);

CREATE TABLE IF NOT EXISTS app.practice_submission (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  practice_set_id   UUID NOT NULL REFERENCES app.practice_set(id) ON DELETE CASCADE,
  practice_item_id  UUID NOT NULL REFERENCES app.practice_item(id) ON DELETE CASCADE,
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  answer_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  score             NUMERIC(6,2),
  is_correct        BOOLEAN,
  judge_mode        TEXT NOT NULL DEFAULT 'RULE_FIRST' CHECK (judge_mode IN ('RULE_FIRST', 'LLM_RUBRIC', 'HYBRID')),
  judge_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  profile_delta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  submitted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (practice_item_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_practice_submission_user
ON app.practice_submission(user_id, submitted_at DESC);

CREATE TABLE IF NOT EXISTS app.mistake_record (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  practice_item_id   UUID NOT NULL REFERENCES app.practice_item(id) ON DELETE CASCADE,
  last_submission_id UUID NOT NULL REFERENCES app.practice_submission(id) ON DELETE CASCADE,
  knowledge_tags     JSONB NOT NULL DEFAULT '[]'::jsonb,
  difficulty_level   app.difficulty_level NOT NULL DEFAULT 'MIXED',
  mistake_type       VARCHAR(32) CHECK (
    mistake_type IS NULL OR mistake_type IN ('conceptual', 'procedural', 'careless')
  ),
  user_note          TEXT NOT NULL DEFAULT '',
  wrong_count        INT NOT NULL DEFAULT 1 CHECK (wrong_count >= 1),
  review_count       INT NOT NULL DEFAULT 0 CHECK (review_count >= 0),
  next_review_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  ease_factor        NUMERIC(3,2) NOT NULL DEFAULT 2.50 CHECK (ease_factor >= 1.30),
  interval_days      INT NOT NULL DEFAULT 1 CHECK (interval_days >= 1),
  mastered           BOOLEAN NOT NULL DEFAULT FALSE,
  first_wrong_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_wrong_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, practice_item_id)
);

CREATE TABLE IF NOT EXISTS app.mistake_review_session (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  mistake_ids   UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
  status        VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'DONE', 'CANCELLED')),
  score         NUMERIC(5,2),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.mistake_review_result (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  session_id        UUID NOT NULL REFERENCES app.mistake_review_session(id) ON DELETE CASCADE,
  mistake_record_id UUID NOT NULL REFERENCES app.mistake_record(id) ON DELETE CASCADE,
  quality           INT NOT NULL CHECK (quality BETWEEN 0 AND 5),
  is_correct        BOOLEAN,
  answer_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  reviewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (session_id, mistake_record_id)
);

CREATE INDEX IF NOT EXISTS idx_mistake_user_next_review
ON app.mistake_record(user_id, next_review_at)
WHERE NOT mastered;

CREATE INDEX IF NOT EXISTS idx_mistake_user_mastered
ON app.mistake_record(user_id, mastered, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_mistake_tags_gin
ON app.mistake_record USING GIN (knowledge_tags);

CREATE INDEX IF NOT EXISTS idx_mistake_review_session_user
ON app.mistake_review_session(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_mistake_review_result_session
ON app.mistake_review_result(session_id, mistake_record_id);

CREATE TABLE IF NOT EXISTS app.audit_log (
  id                BIGSERIAL PRIMARY KEY,
  user_id           UUID REFERENCES app.users(id) ON DELETE SET NULL,
  task_id           UUID REFERENCES app.smart_engine_task(id) ON DELETE SET NULL,
  event_category    TEXT NOT NULL CHECK (
    event_category IN ('AUTH', 'TASK', 'DOWNLOAD', 'SAFETY', 'ADMIN', 'PROVIDER')
  ),
  risk_level        TEXT NOT NULL DEFAULT 'INFO' CHECK (risk_level IN ('INFO', 'LOW', 'MEDIUM', 'HIGH')),
  message           TEXT NOT NULL,
  payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user_created
ON app.audit_log(user_id, created_at DESC);

-- =========================================================
-- RLS
-- 应用层每次请求前设置：SET app.user_id = '<uuid>';
-- =========================================================
CREATE OR REPLACE FUNCTION app.current_user_uuid()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION app.touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

ALTER TABLE app.learning_resource ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_llm_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.qna_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.smart_engine_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.generated_artifact ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_resource_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_profile_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_profile_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.learning_plan ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.learning_plan_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_learning_loop ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_learning_subgoal ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_planning_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_replan_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.practice_set ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.practice_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.practice_submission ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.mistake_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.mistake_review_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.mistake_review_result ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.user_profile_vector ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.wiki_page ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.wiki_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.term_lexicon ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.synonym_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.knowledge_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.knowledge_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.resource_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.resource_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.video_generation_task ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_learning_resource_rw ON app.learning_resource;
CREATE POLICY p_learning_resource_rw ON app.learning_resource
FOR ALL USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = app.learning_resource.course_id
  ))
)
WITH CHECK (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = app.learning_resource.course_id
  ))
);

DROP POLICY IF EXISTS p_qna_session_rw ON app.qna_session;
CREATE POLICY p_qna_session_rw ON app.qna_session
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_smart_engine_task_rw ON app.smart_engine_task;
CREATE POLICY p_smart_engine_task_rw ON app.smart_engine_task
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_generated_artifact_rw ON app.generated_artifact;
CREATE POLICY p_generated_artifact_rw ON app.generated_artifact
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_user_resource_state_rw ON app.user_resource_state;
CREATE POLICY p_user_resource_state_rw ON app.user_resource_state
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_user_profile_snapshot_rw ON app.user_profile_snapshot;
CREATE POLICY p_user_profile_snapshot_rw ON app.user_profile_snapshot
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_user_profile_current_rw ON app.user_profile_current;
CREATE POLICY p_user_profile_current_rw ON app.user_profile_current
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_user_llm_config_rw ON app.user_llm_config;
CREATE POLICY p_user_llm_config_rw ON app.user_llm_config
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_learning_plan_rw ON app.learning_plan;
CREATE POLICY p_learning_plan_rw ON app.learning_plan
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_learning_plan_snapshot_rw ON app.learning_plan_snapshot;
CREATE POLICY p_learning_plan_snapshot_rw ON app.learning_plan_snapshot
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_autonomous_learning_loop_rw ON app.autonomous_learning_loop;
CREATE POLICY p_autonomous_learning_loop_rw ON app.autonomous_learning_loop
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_autonomous_learning_subgoal_rw ON app.autonomous_learning_subgoal;
CREATE POLICY p_autonomous_learning_subgoal_rw ON app.autonomous_learning_subgoal
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_autonomous_planning_checkpoint_rw ON app.autonomous_planning_checkpoint;
CREATE POLICY p_autonomous_planning_checkpoint_rw ON app.autonomous_planning_checkpoint
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_autonomous_replan_event_rw ON app.autonomous_replan_event;
CREATE POLICY p_autonomous_replan_event_rw ON app.autonomous_replan_event
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_practice_set_rw ON app.practice_set;
CREATE POLICY p_practice_set_rw ON app.practice_set
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_practice_item_read ON app.practice_item;
CREATE POLICY p_practice_item_read ON app.practice_item
FOR ALL USING (
  EXISTS (
    SELECT 1
    FROM app.practice_set s
    WHERE s.id = app.practice_item.practice_set_id
      AND s.user_id = app.current_user_uuid()
  )
)
WITH CHECK (
  EXISTS (
    SELECT 1
    FROM app.practice_set s
    WHERE s.id = app.practice_item.practice_set_id
      AND s.user_id = app.current_user_uuid()
  )
);

DROP POLICY IF EXISTS p_practice_submission_rw ON app.practice_submission;
CREATE POLICY p_practice_submission_rw ON app.practice_submission
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_mistake_record_rw ON app.mistake_record;
CREATE POLICY p_mistake_record_rw ON app.mistake_record
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_mistake_review_session_rw ON app.mistake_review_session;
CREATE POLICY p_mistake_review_session_rw ON app.mistake_review_session
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_mistake_review_result_rw ON app.mistake_review_result;
CREATE POLICY p_mistake_review_result_rw ON app.mistake_review_result
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_audit_log_read ON app.audit_log;
CREATE POLICY p_audit_log_read ON app.audit_log
FOR ALL USING (
  user_id = app.current_user_uuid()
  OR user_id IS NULL
)
WITH CHECK (
  user_id = app.current_user_uuid()
  OR user_id IS NULL
);

DROP POLICY IF EXISTS p_user_profile_vector_rw ON rag.user_profile_vector;
CREATE POLICY p_user_profile_vector_rw ON rag.user_profile_vector
FOR ALL USING (user_id = app.current_user_uuid())
WITH CHECK (user_id = app.current_user_uuid());

DROP POLICY IF EXISTS p_wiki_page_read ON rag.wiki_page;
CREATE POLICY p_wiki_page_read ON rag.wiki_page
FOR SELECT USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.wiki_page.course_id
  ))
);

DROP POLICY IF EXISTS p_wiki_link_read ON rag.wiki_link;
CREATE POLICY p_wiki_link_read ON rag.wiki_link
FOR SELECT USING (
  EXISTS (
    SELECT 1
    FROM rag.wiki_page p
    WHERE p.id = rag.wiki_link.from_page_id
      AND (
        p.access_scope = 'GLOBAL'
        OR (p.access_scope = 'USER' AND p.owner_user_id = app.current_user_uuid())
        OR (p.access_scope = 'COURSE' AND EXISTS (
          SELECT 1 FROM app.user_course_enrollments e
          WHERE e.user_id = app.current_user_uuid() AND e.course_id = p.course_id
        ))
      )
  )
);

DROP POLICY IF EXISTS p_term_lexicon_read ON rag.term_lexicon;
CREATE POLICY p_term_lexicon_read ON rag.term_lexicon
FOR SELECT USING (
  course_id IS NULL
  OR EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.term_lexicon.course_id
  )
);

DROP POLICY IF EXISTS p_synonym_group_read ON rag.synonym_group;
CREATE POLICY p_synonym_group_read ON rag.synonym_group
FOR SELECT USING (
  course_id IS NULL
  OR EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.synonym_group.course_id
  )
);

DROP POLICY IF EXISTS p_knowledge_document_read ON rag.knowledge_document;
CREATE POLICY p_knowledge_document_read ON rag.knowledge_document
FOR SELECT USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.knowledge_document.course_id
  ))
);

DROP POLICY IF EXISTS p_knowledge_chunk_read ON rag.knowledge_chunk;
CREATE POLICY p_knowledge_chunk_read ON rag.knowledge_chunk
FOR SELECT USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.knowledge_chunk.course_id
  ))
);

DROP POLICY IF EXISTS p_resource_document_read ON rag.resource_document;
CREATE POLICY p_resource_document_read ON rag.resource_document
FOR ALL USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.resource_document.course_id
  ))
)
WITH CHECK (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.resource_document.course_id
  ))
);

DROP POLICY IF EXISTS p_resource_chunk_read ON rag.resource_chunk;
CREATE POLICY p_resource_chunk_read ON rag.resource_chunk
FOR ALL USING (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.resource_chunk.course_id
  ))
)
WITH CHECK (
  access_scope = 'GLOBAL'
  OR (access_scope = 'USER' AND owner_user_id = app.current_user_uuid())
  OR (access_scope = 'COURSE' AND EXISTS (
    SELECT 1 FROM app.user_course_enrollments e
    WHERE e.user_id = app.current_user_uuid() AND e.course_id = rag.resource_chunk.course_id
  ))
);

DROP POLICY IF EXISTS p_video_generation_task_rw ON rag.video_generation_task;
CREATE POLICY p_video_generation_task_rw ON rag.video_generation_task
FOR ALL USING (student_id = app.current_user_uuid())
WITH CHECK (student_id = app.current_user_uuid());

DROP TRIGGER IF EXISTS trg_learning_resource_touch_updated_at ON app.learning_resource;
CREATE TRIGGER trg_learning_resource_touch_updated_at
BEFORE UPDATE ON app.learning_resource
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_user_resource_state_touch_updated_at ON app.user_resource_state;
CREATE TRIGGER trg_user_resource_state_touch_updated_at
BEFORE UPDATE ON app.user_resource_state
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

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

DROP TRIGGER IF EXISTS trg_qna_session_touch_updated_at ON app.qna_session;
CREATE TRIGGER trg_qna_session_touch_updated_at
BEFORE UPDATE ON app.qna_session
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_smart_engine_task_touch_updated_at ON app.smart_engine_task;
CREATE TRIGGER trg_smart_engine_task_touch_updated_at
BEFORE UPDATE ON app.smart_engine_task
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_user_profile_current_touch_updated_at ON app.user_profile_current;
CREATE TRIGGER trg_user_profile_current_touch_updated_at
BEFORE UPDATE ON app.user_profile_current
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_user_llm_config_touch_updated_at ON app.user_llm_config;
CREATE TRIGGER trg_user_llm_config_touch_updated_at
BEFORE UPDATE ON app.user_llm_config
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_learning_plan_touch_updated_at ON app.learning_plan;
CREATE TRIGGER trg_learning_plan_touch_updated_at
BEFORE UPDATE ON app.learning_plan
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_autonomous_learning_loop_touch_updated_at ON app.autonomous_learning_loop;
CREATE TRIGGER trg_autonomous_learning_loop_touch_updated_at
BEFORE UPDATE ON app.autonomous_learning_loop
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_autonomous_learning_subgoal_touch_updated_at ON app.autonomous_learning_subgoal;
CREATE TRIGGER trg_autonomous_learning_subgoal_touch_updated_at
BEFORE UPDATE ON app.autonomous_learning_subgoal
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_practice_set_touch_updated_at ON app.practice_set;
CREATE TRIGGER trg_practice_set_touch_updated_at
BEFORE UPDATE ON app.practice_set
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_mistake_record_touch_updated_at ON app.mistake_record;
CREATE TRIGGER trg_mistake_record_touch_updated_at
BEFORE UPDATE ON app.mistake_record
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE OR REPLACE FUNCTION app.capture_mistake_from_submission()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  item_record RECORD;
  existing_record_id UUID;
  wrong_at TIMESTAMPTZ;
BEGIN
  IF NEW.is_correct IS DISTINCT FROM FALSE THEN
    RETURN NEW;
  END IF;

  SELECT question_type, stem, options_json, standard_answer, knowledge_tags, difficulty_level
  INTO item_record
  FROM app.practice_item
  WHERE id = NEW.practice_item_id;

  IF NOT FOUND THEN
    RETURN NEW;
  END IF;

  wrong_at := COALESCE(NEW.submitted_at, now());

  SELECT r.id
  INTO existing_record_id
  FROM app.mistake_record r
  JOIN app.practice_item i ON i.id = r.practice_item_id
  WHERE r.user_id = NEW.user_id
    AND (
      r.practice_item_id = NEW.practice_item_id
      OR (
        i.question_type = item_record.question_type
        AND lower(regexp_replace(btrim(COALESCE(i.stem, '')), '\s+', '', 'g'))
            = lower(regexp_replace(btrim(COALESCE(item_record.stem, '')), '\s+', '', 'g'))
        AND (
          i.question_type NOT IN ('SINGLE_CHOICE', 'MULTIPLE_CHOICE')
          OR lower(regexp_replace(COALESCE(i.options_json::text, '[]'), '\s+', '', 'g'))
             = lower(regexp_replace(COALESCE(item_record.options_json::text, '[]'), '\s+', '', 'g'))
        )
        AND lower(regexp_replace(btrim(COALESCE(i.standard_answer ->> 'answer', i.standard_answer::text, '')), '\s+', '', 'g'))
            = lower(regexp_replace(btrim(COALESCE(item_record.standard_answer ->> 'answer', item_record.standard_answer::text, '')), '\s+', '', 'g'))
        AND lower(regexp_replace(btrim(COALESCE(item_record.stem, '')), '\s+', '', 'g')) <> ''
        AND lower(regexp_replace(btrim(COALESCE(item_record.standard_answer ->> 'answer', item_record.standard_answer::text, '')), '\s+', '', 'g')) <> ''
      )
    )
  ORDER BY
    CASE WHEN r.practice_item_id = NEW.practice_item_id THEN 0 ELSE 1 END,
    r.last_wrong_at DESC,
    r.updated_at DESC
  LIMIT 1
  FOR UPDATE OF r;

  IF existing_record_id IS NOT NULL THEN
    UPDATE app.mistake_record
    SET practice_item_id = NEW.practice_item_id,
        last_submission_id = NEW.id,
        knowledge_tags = COALESCE(item_record.knowledge_tags, '[]'::jsonb),
        difficulty_level = COALESCE(item_record.difficulty_level, 'MIXED'::app.difficulty_level),
        wrong_count = wrong_count + 1,
        next_review_at = now(),
        last_wrong_at = wrong_at,
        mastered = FALSE,
        updated_at = now()
    WHERE id = existing_record_id;

    RETURN NEW;
  END IF;

  INSERT INTO app.mistake_record (
    user_id,
    practice_item_id,
    last_submission_id,
    knowledge_tags,
    difficulty_level,
    wrong_count,
    next_review_at,
    first_wrong_at,
    last_wrong_at,
    mastered
  )
  VALUES (
    NEW.user_id,
    NEW.practice_item_id,
    NEW.id,
    COALESCE(item_record.knowledge_tags, '[]'::jsonb),
    COALESCE(item_record.difficulty_level, 'MIXED'::app.difficulty_level),
    1,
    now(),
    wrong_at,
    wrong_at,
    FALSE
  )
  ON CONFLICT (user_id, practice_item_id) DO UPDATE
  SET last_submission_id = EXCLUDED.last_submission_id,
      knowledge_tags = EXCLUDED.knowledge_tags,
      difficulty_level = EXCLUDED.difficulty_level,
      wrong_count = app.mistake_record.wrong_count + 1,
      next_review_at = now(),
      last_wrong_at = EXCLUDED.last_wrong_at,
      mastered = FALSE,
      updated_at = now();

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_capture_mistake_from_submission ON app.practice_submission;
CREATE TRIGGER trg_capture_mistake_from_submission
AFTER INSERT OR UPDATE OF is_correct, judge_result_json, submitted_at ON app.practice_submission
FOR EACH ROW
WHEN (NEW.is_correct IS FALSE)
EXECUTE FUNCTION app.capture_mistake_from_submission();

DROP TRIGGER IF EXISTS trg_wiki_page_touch_updated_at ON rag.wiki_page;
CREATE TRIGGER trg_wiki_page_touch_updated_at
BEFORE UPDATE ON rag.wiki_page
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_document_touch_updated_at ON rag.knowledge_document;
CREATE TRIGGER trg_knowledge_document_touch_updated_at
BEFORE UPDATE ON rag.knowledge_document
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_resource_document_touch_updated_at ON rag.resource_document;
CREATE TRIGGER trg_resource_document_touch_updated_at
BEFORE UPDATE ON rag.resource_document
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_video_generation_task_touch_updated_at ON rag.video_generation_task;
CREATE TRIGGER trg_video_generation_task_touch_updated_at
BEFORE UPDATE ON rag.video_generation_task
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

ANALYZE app.learning_resource;
ANALYZE app.qna_session;
ANALYZE app.smart_engine_task;
ANALYZE app.generated_artifact;
ANALYZE app.user_resource_state;
ANALYZE app.user_profile_snapshot;
ANALYZE app.user_profile_current;
ANALYZE app.user_llm_config;
ANALYZE app.learning_plan;
ANALYZE app.learning_plan_snapshot;
ANALYZE app.autonomous_learning_loop;
ANALYZE app.autonomous_learning_subgoal;
ANALYZE app.autonomous_planning_checkpoint;
ANALYZE app.autonomous_replan_event;
ANALYZE app.practice_set;
ANALYZE app.practice_item;
ANALYZE app.practice_submission;
ANALYZE app.mistake_record;
ANALYZE app.mistake_review_session;
ANALYZE app.mistake_review_result;
ANALYZE app.audit_log;
ANALYZE rag.user_profile_vector;
ANALYZE rag.wiki_page;
ANALYZE rag.wiki_link;
ANALYZE rag.term_lexicon;
ANALYZE rag.synonym_group;
ANALYZE rag.knowledge_document;
ANALYZE rag.knowledge_chunk;
ANALYZE rag.resource_document;
ANALYZE rag.resource_chunk;
ANALYZE rag.video_generation_task;
