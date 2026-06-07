WITH latest_plan AS (
  SELECT p.plan_json
  FROM app.learning_plan p
  WHERE p.user_id = :'user_id'::uuid
    AND p.status = 'ACTIVE'
  ORDER BY p.updated_at DESC
  LIMIT 1
),
latest_task_path AS (
  SELECT t.response_summary -> 'learningPath' AS learning_path
  FROM app.smart_engine_task t
  WHERE t.user_id = :'user_id'::uuid
    AND t.service_type = 'PERSONALIZED_LEARNING'
    AND t.task_status::text = 'COMPLETED'
    AND jsonb_exists(t.response_summary, 'learningPath')
  ORDER BY t.created_at DESC
  LIMIT 1
),
current_learning_path AS (
  SELECT COALESCE(
    (SELECT plan_json FROM latest_plan WHERE jsonb_typeof(plan_json) = 'object'),
    (SELECT learning_path FROM latest_task_path WHERE jsonb_typeof(learning_path) = 'object'),
    '{}'::jsonb
  ) AS learning_path
),
path_steps AS (
  SELECT step_item.step,
         step_item.ordinality,
         upper(regexp_replace(COALESCE(step_item.step ->> 'status', ''), '[^A-Za-z0-9]+', '_', 'g')) AS normalized_status
  FROM current_learning_path clp
  CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(clp.learning_path -> 'steps') = 'array'
      THEN clp.learning_path -> 'steps'
      ELSE '[]'::jsonb
    END
  ) WITH ORDINALITY AS step_item(step, ordinality)
),
active_step AS (
  SELECT step
  FROM path_steps
  ORDER BY
    CASE
      WHEN normalized_status = 'IN_PROGRESS'
        OR normalized_status = 'ACTIVE'
        OR normalized_status LIKE '%RUNNING%'
        OR normalized_status LIKE '%PROGRESS%'
      THEN 0
      WHEN normalized_status = '' THEN 1
      WHEN normalized_status IN ('COMPLETED', 'DONE', 'PENDING', 'INACTIVE', 'NOT_STARTED')
        OR normalized_status LIKE 'NOT_%'
        OR normalized_status LIKE '%INACTIVE%'
      THEN 3
      ELSE 2
    END,
    ordinality
  LIMIT 1
),
profile_context AS (
  SELECT up.profile_json
  FROM app.user_profile_current up
  WHERE up.user_id = :'user_id'::uuid
  LIMIT 1
),
learning_context AS (
  SELECT lower(concat_ws(' ',
    astep.step ->> 'title',
    astep.step ->> 'objective',
    astep.step ->> 'checkpoint',
    astep.step ->> 'successCriteria',
    (
      SELECT string_agg(kp.value, ' ')
      FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(astep.step -> 'targetKnowledgePoints') = 'array'
          THEN astep.step -> 'targetKnowledgePoints'
          ELSE '[]'::jsonb
        END
      ) AS kp(value)
    )
  )) AS active_step_text,
  lower(concat_ws(' ',
    clp.learning_path ->> 'goal',
    clp.learning_path ->> 'summary',
    clp.learning_path ->> 'summaryText',
    astep.step ->> 'title',
    astep.step ->> 'objective',
    astep.step ->> 'checkpoint',
    astep.step ->> 'successCriteria',
    (
      SELECT string_agg(kp.value, ' ')
      FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(astep.step -> 'targetKnowledgePoints') = 'array'
          THEN astep.step -> 'targetKnowledgePoints'
          ELSE '[]'::jsonb
        END
      ) AS kp(value)
    ),
    pc.profile_json ->> 'learningGoal'
  )) AS context_text
  FROM current_learning_path clp
  LEFT JOIN active_step astep ON TRUE
  LEFT JOIN profile_context pc ON TRUE
),
context_terms AS (
  SELECT DISTINCT term
  FROM (
    SELECT lower(raw_term) AS term
    FROM learning_context lc
    CROSS JOIN LATERAL regexp_split_to_table(
      COALESCE(NULLIF(lc.active_step_text, ''), lc.context_text),
      '[\s/、,，;；:：>《》()（）【】\[\]''"“”]+'
    ) AS raw(raw_term)
    UNION ALL
    SELECT 'java'
    FROM learning_context lc
    WHERE COALESCE(NULLIF(lc.active_step_text, ''), lc.context_text) ~* '(java线程|java并发|thread类|runnable接口|synchronized|volatile|thread[.]sleep|(^|[^[:alnum:]_])java([^[:alnum:]_]|$))'
    UNION ALL
    SELECT 'thread'
    FROM learning_context lc
    WHERE COALESCE(NULLIF(lc.active_step_text, ''), lc.context_text) ~* '(线程|thread)'
    UNION ALL
    SELECT 'runnable'
    FROM learning_context lc
    WHERE COALESCE(NULLIF(lc.active_step_text, ''), lc.context_text) ~* '(runnable|runnable接口)'
  ) raw_terms
  WHERE char_length(term) >= 2
    AND term NOT IN (
      '学习', '概念', '基础', '入门', '阶段', '掌握', '理解', '能够', '准确', '方式', '区别',
      '识别', '代码', '正确', '当前', '重点', 'learning', 'basic', 'concepts', 'foundation',
      'foundations', 'course', 'resource', 'resources'
    )
)
SELECT lr.title,
       lr.metadata_json ->> 'csCategory' AS category,
       lr.metadata_json ->> 'csSubcategory' AS subcategory,
       lr.metadata_json ->> 'sourceName' AS source,
       lr.metadata_json ->> 'sourceUrl' AS url
FROM app.learning_resource lr
WHERE lr.status = 'ACTIVE'
  AND lr.access_scope::text = 'GLOBAL'
  AND COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) <> 'NOTE'
  AND lr.resource_type::text NOT IN ('QUIZ', 'PRACTICE')
  AND COALESCE(NULLIF(upper(lr.metadata_json ->> 'displayType'), ''), lr.resource_type::text) NOT IN ('QUIZ', 'PRACTICE')
  AND COALESCE(lr.metadata_json ->> 'sourceUrl', '') ~* '^https?://'
  AND COALESCE(lr.metadata_json ->> 'accessibilityStatus', '') = 'ACCESSIBLE'
  AND (
    NOT EXISTS (SELECT 1 FROM context_terms)
    OR (
      EXISTS (
        SELECT 1
        FROM context_terms ct
        WHERE (ct.term = 'java' AND lower(concat_ws(' ', lr.title, COALESCE(lr.summary_text, ''), lr.tags::text, COALESCE(lr.metadata_json ->> 'csSubcategory', ''), COALESCE(lr.metadata_json ->> 'sourceName', ''))) ~ '(^|[^a-z0-9_+.-])java([^a-z0-9_+.-]|$)')
           OR (ct.term <> 'java' AND lower(concat_ws(' ', lr.title, COALESCE(lr.summary_text, ''), lr.tags::text, COALESCE(lr.metadata_json ->> 'csSubcategory', ''), COALESCE(lr.metadata_json ->> 'sourceName', ''))) LIKE '%' || ct.term || '%')
      )
      AND (
        upper(COALESCE(NULLIF(lr.metadata_json ->> 'csCategory', ''), 'GENERAL_CS')) = 'PROGRAMMING_LANGUAGES'
        OR upper(COALESCE(NULLIF(lr.metadata_json ->> 'csCategory', ''), 'GENERAL_CS')) = 'BACKEND_SYSTEMS'
      )
    )
  )
ORDER BY lr.updated_at DESC
LIMIT 5;
