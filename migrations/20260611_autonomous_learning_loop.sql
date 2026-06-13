-- Persist autonomous planning loops without changing existing learning-plan tables.

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

ALTER TABLE app.autonomous_learning_loop ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_learning_subgoal ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_planning_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.autonomous_replan_event ENABLE ROW LEVEL SECURITY;

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

DROP TRIGGER IF EXISTS trg_autonomous_learning_loop_touch_updated_at ON app.autonomous_learning_loop;
CREATE TRIGGER trg_autonomous_learning_loop_touch_updated_at
BEFORE UPDATE ON app.autonomous_learning_loop
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

DROP TRIGGER IF EXISTS trg_autonomous_learning_subgoal_touch_updated_at ON app.autonomous_learning_subgoal;
CREATE TRIGGER trg_autonomous_learning_subgoal_touch_updated_at
BEFORE UPDATE ON app.autonomous_learning_subgoal
FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

ANALYZE app.autonomous_learning_loop;
ANALYZE app.autonomous_learning_subgoal;
ANALYZE app.autonomous_planning_checkpoint;
ANALYZE app.autonomous_replan_event;
