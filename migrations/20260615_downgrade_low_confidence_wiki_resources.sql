WITH low_confidence_resources AS (
  SELECT id
  FROM app.learning_resource
  WHERE metadata_json ->> 'ingestedBy' = 'wiki_resource_importer'
    AND COALESCE(summary_text, '') ~* 'generic lexical score [0-9]+[.][0-9]+'
    AND COALESCE(
          NULLIF((regexp_match(COALESCE(summary_text, ''), 'generic lexical score ([0-9]+[.][0-9]+)', 'i'))[1], '')::numeric,
          0
        ) < 0.60
),
updated_resources AS (
  UPDATE app.learning_resource lr
  SET
    tags = (
      SELECT COALESCE(jsonb_agg(tag.value ORDER BY tag.ordinality), '[]'::jsonb)
      FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(lr.tags) = 'array' THEN lr.tags ELSE '[]'::jsonb END
      ) WITH ORDINALITY AS tag(value, ordinality)
      WHERE tag.value NOT IN ('wiki-bound-resource', 'metadata-search-fallback', 'metadata-index-match')
        AND tag.value <> COALESCE(lr.metadata_json ->> 'wikiTitle', '')
        AND tag.value <> trim(both '"' from COALESCE(lr.metadata_json ->> 'wikiTitle', ''))
        AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(lr.metadata_json -> 'wikiAliases') = 'array'
              THEN lr.metadata_json -> 'wikiAliases'
              ELSE '[]'::jsonb
            END
          ) AS alias(value)
          WHERE tag.value = alias.value
        )
    ),
    metadata_json = (
      lr.metadata_json
      - 'wikiSlug'
      - 'wikiTitle'
      - 'wikiSourceRef'
      - 'wikiAliases'
    ) || jsonb_build_object(
      'ingestedBy', 'external_resource_importer',
      'wikiBindingStatus', 'LOW_CONFIDENCE_DROPPED'
    ),
    updated_at = now()
  FROM low_confidence_resources low
  WHERE lr.id = low.id
  RETURNING lr.id
)
UPDATE rag.resource_document rd
SET
  source_ref = COALESCE(rd.metadata_json ->> 'sourceUrl', rd.source_ref),
  metadata_json = (
    rd.metadata_json
    - 'wikiSlug'
    - 'wikiTitle'
    - 'wikiSourceRef'
    - 'wikiAliases'
  ) || jsonb_build_object(
    'ingestedBy', 'external_resource_importer',
    'wikiBindingStatus', 'LOW_CONFIDENCE_DROPPED'
  ),
  updated_at = now()
FROM updated_resources updated
WHERE rd.resource_id = updated.id;
