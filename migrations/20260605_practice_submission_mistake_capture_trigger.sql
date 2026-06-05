-- Ensure incorrect practice submissions are automatically captured in the mistake book.

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
