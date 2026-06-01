-- 用户学习路径图：节点（知识点）+ 边（依赖关系）
-- 节点 canonical_key 与 learner_feature.canonical_key 对齐

CREATE TABLE IF NOT EXISTS app.learner_knowledge_node (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  canonical_key   TEXT NOT NULL,
  topic           TEXT NOT NULL,
  mastery_score   FLOAT NOT NULL DEFAULT 0.0 CHECK (mastery_score BETWEEN 0 AND 1),
  node_status     TEXT NOT NULL DEFAULT 'NOT_STARTED'
                  CHECK (node_status IN ('NOT_STARTED', 'IN_PROGRESS', 'MASTERED', 'WEAK')),
  source          TEXT NOT NULL DEFAULT 'PROFILE'
                  CHECK (source IN ('PROFILE', 'PRACTICE', 'EVALUATION', 'MANUAL')),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, canonical_key)
);

CREATE TABLE IF NOT EXISTS app.learner_knowledge_edge (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  from_key        TEXT NOT NULL,
  to_key          TEXT NOT NULL,
  relation_type   TEXT NOT NULL DEFAULT 'PREREQUISITE'
                  CHECK (relation_type IN ('PREREQUISITE', 'RELATED', 'PART_OF')),
  weight          FLOAT NOT NULL DEFAULT 1.0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, from_key, to_key, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_lkn_user_status
ON app.learner_knowledge_node(user_id, node_status);

CREATE INDEX IF NOT EXISTS idx_lkn_user_key
ON app.learner_knowledge_node(user_id, canonical_key);

CREATE INDEX IF NOT EXISTS idx_lke_user_from
ON app.learner_knowledge_edge(user_id, from_key);

CREATE INDEX IF NOT EXISTS idx_lke_user_to
ON app.learner_knowledge_edge(user_id, to_key);

-- RLS
ALTER TABLE app.learner_knowledge_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.learner_knowledge_edge ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_lkn_rw ON app.learner_knowledge_node;
CREATE POLICY p_lkn_rw ON app.learner_knowledge_node
  USING (user_id::text = current_setting('app.current_user_uuid', true))
  WITH CHECK (user_id::text = current_setting('app.current_user_uuid', true));

DROP POLICY IF EXISTS p_lke_rw ON app.learner_knowledge_edge;
CREATE POLICY p_lke_rw ON app.learner_knowledge_edge
  USING (user_id::text = current_setting('app.current_user_uuid', true))
  WITH CHECK (user_id::text = current_setting('app.current_user_uuid', true));

ANALYZE app.learner_knowledge_node;
ANALYZE app.learner_knowledge_edge;
