-- Merge duplicate mistake records that come from regenerated equivalent questions.

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

BEGIN;

CREATE TEMP TABLE tmp_mistake_duplicate_map ON COMMIT DROP AS
WITH keyed AS (
    SELECT
        r.id,
        first_value(r.id) OVER duplicate_window AS keeper_id,
        count(*) OVER duplicate_window AS group_size
    FROM app.mistake_record r
    JOIN app.practice_item i ON i.id = r.practice_item_id
    WHERE lower(regexp_replace(btrim(COALESCE(i.stem, '')), '\s+', '', 'g')) <> ''
      AND lower(regexp_replace(btrim(COALESCE(i.standard_answer ->> 'answer', i.standard_answer::text, '')), '\s+', '', 'g')) <> ''
    WINDOW duplicate_window AS (
        PARTITION BY
            r.user_id,
            i.question_type,
            lower(regexp_replace(btrim(COALESCE(i.stem, '')), '\s+', '', 'g')),
            CASE
                WHEN i.question_type IN ('SINGLE_CHOICE', 'MULTIPLE_CHOICE')
                    THEN lower(regexp_replace(COALESCE(i.options_json::text, '[]'), '\s+', '', 'g'))
                ELSE ''
            END,
            lower(regexp_replace(btrim(COALESCE(i.standard_answer ->> 'answer', i.standard_answer::text, '')), '\s+', '', 'g'))
        ORDER BY r.last_wrong_at DESC, r.updated_at DESC, r.created_at DESC, r.id
    )
)
SELECT id AS duplicate_id, keeper_id
FROM keyed
WHERE group_size > 1
  AND id <> keeper_id;

CREATE TEMP TABLE tmp_mistake_duplicate_summary ON COMMIT DROP AS
WITH grouped_ids AS (
    SELECT keeper_id, keeper_id AS id
    FROM tmp_mistake_duplicate_map
    GROUP BY keeper_id
    UNION ALL
    SELECT keeper_id, duplicate_id AS id
    FROM tmp_mistake_duplicate_map
)
SELECT
    grouped_ids.keeper_id,
    GREATEST(1, SUM(r.wrong_count))::int AS wrong_count,
    SUM(r.review_count)::int AS review_count,
    MIN(r.first_wrong_at) AS first_wrong_at,
    MAX(r.last_wrong_at) AS last_wrong_at,
    CASE WHEN bool_and(r.mastered) THEN MAX(r.next_review_at) ELSE MIN(r.next_review_at) END AS next_review_at,
    bool_and(r.mastered) AS mastered
FROM grouped_ids
JOIN app.mistake_record r ON r.id = grouped_ids.id
GROUP BY grouped_ids.keeper_id;

DELETE FROM app.mistake_review_result rr
USING tmp_mistake_duplicate_map dm
WHERE rr.mistake_record_id = dm.duplicate_id
  AND EXISTS (
    SELECT 1
    FROM app.mistake_review_result existing
    WHERE existing.session_id = rr.session_id
      AND existing.mistake_record_id = dm.keeper_id
  );

UPDATE app.mistake_review_result rr
SET mistake_record_id = dm.keeper_id
FROM tmp_mistake_duplicate_map dm
WHERE rr.mistake_record_id = dm.duplicate_id;

UPDATE app.mistake_review_session s
SET mistake_ids = COALESCE(
    (
        SELECT array_agg(dedup.mapped_id ORDER BY dedup.first_ord)
        FROM (
            SELECT
                COALESCE(dm.keeper_id, item.id) AS mapped_id,
                MIN(item.ord) AS first_ord
            FROM unnest(s.mistake_ids) WITH ORDINALITY AS item(id, ord)
            LEFT JOIN tmp_mistake_duplicate_map dm ON dm.duplicate_id = item.id
            GROUP BY COALESCE(dm.keeper_id, item.id)
        ) dedup
    ),
    ARRAY[]::UUID[]
)
WHERE EXISTS (
    SELECT 1
    FROM unnest(s.mistake_ids) AS item(id)
    JOIN tmp_mistake_duplicate_map dm ON dm.duplicate_id = item.id
);

UPDATE app.mistake_record r
SET wrong_count = summary.wrong_count,
    review_count = summary.review_count,
    first_wrong_at = summary.first_wrong_at,
    last_wrong_at = summary.last_wrong_at,
    next_review_at = summary.next_review_at,
    mastered = summary.mastered,
    updated_at = now()
FROM tmp_mistake_duplicate_summary summary
WHERE r.id = summary.keeper_id;

DELETE FROM app.mistake_record r
USING tmp_mistake_duplicate_map dm
WHERE r.id = dm.duplicate_id;

COMMIT;
