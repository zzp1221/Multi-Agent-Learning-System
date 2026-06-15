ALTER TABLE IF EXISTS app.user_profile_snapshot
  DROP COLUMN IF EXISTS source_message_ref_id;

ALTER TABLE IF EXISTS app.smart_engine_task
  DROP COLUMN IF EXISTS smart_session_id;

DROP TABLE IF EXISTS app.smart_engine_task_event;
DROP TABLE IF EXISTS app.smart_engine_session;
DROP TABLE IF EXISTS app.qna_message_ref;
DROP TABLE IF EXISTS app.note_ai_artifact;
DROP TABLE IF EXISTS app.note_resource_link;
DROP TABLE IF EXISTS app.tutoring_session;
DROP TABLE IF EXISTS app.assessment_result;
