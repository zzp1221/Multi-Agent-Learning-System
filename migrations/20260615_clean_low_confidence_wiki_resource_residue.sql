WITH low_confidence_resources AS (
  SELECT id
  FROM app.learning_resource
  WHERE metadata_json ->> 'wikiBindingStatus' = 'LOW_CONFIDENCE_DROPPED'
)
UPDATE app.learning_resource lr
SET
  tags = '[]'::jsonb,
  updated_at = now()
FROM low_confidence_resources low
WHERE lr.id = low.id
  AND lr.tags <> '[]'::jsonb;

WITH low_confidence_resources AS (
  SELECT id
  FROM app.learning_resource
  WHERE metadata_json ->> 'wikiBindingStatus' = 'LOW_CONFIDENCE_DROPPED'
)
UPDATE rag.resource_chunk rc
SET
  metadata_json = (
    rc.metadata_json
    - 'wikiSlug'
    - 'wikiTitle'
    - 'wikiSourceRef'
    - 'wikiAliases'
  ) || jsonb_build_object('wikiBindingStatus', 'LOW_CONFIDENCE_DROPPED')
FROM low_confidence_resources low
WHERE rc.resource_id = low.id
  AND (
    rc.metadata_json ? 'wikiSlug'
    OR rc.metadata_json ? 'wikiTitle'
    OR rc.metadata_json ? 'wikiSourceRef'
    OR rc.metadata_json ? 'wikiAliases'
    OR COALESCE(rc.metadata_json ->> 'wikiBindingStatus', '') <> 'LOW_CONFIDENCE_DROPPED'
  );
