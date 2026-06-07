#!/bin/sh
# Restore vectorized knowledge base data into PostgreSQL
# This script is called automatically by docker-compose on first startup,
# or can be run manually: docker exec zhixue-postgres sh /docker-entrypoint-initdb.d/restore_vector_data.sh
set -e

DB_USER="${POSTGRES_USER:-postgres}"
DB_NAME="${POSTGRES_DB:-zhixue}"
DUMP_FILE="${VECTOR_DUMP_FILE:-/docker-entrypoint-initdb.d/vector_data.dump}"
RESTORE_SQL="/tmp/vector_restore.sql"
LEARNING_RESOURCE_STAGE_SQL="/tmp/learning_resource_stage.sql"
VECTOR_DATA_SQL="/tmp/vector_data_without_learning_resource.sql"
RESOURCE_STAGE_SQL="/tmp/resource_document_stage.sql"

cleanup() {
  rm -f "$RESTORE_SQL" "$LEARNING_RESOURCE_STAGE_SQL" "$VECTOR_DATA_SQL" "$RESOURCE_STAGE_SQL"
}

trap cleanup EXIT

echo "Checking if vector data needs to be restored..."

EXISTS=$(psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM rag.knowledge_chunk;" 2>/dev/null || echo "0")

if [ "$EXISTS" -gt 0 ]; then
  echo "Vector data already exists ($EXISTS chunks), skipping restore."
  exit 0
fi

echo "Ensuring vector extension exists..."
psql -U "$DB_USER" -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "Dumping vector archive to SQL..."
pg_restore -a -f "$RESTORE_SQL" "$DUMP_FILE"

echo "Preparing preloaded learning resources from dump..."
awk '
  /^COPY (app|preload_export)\.learning_resource / {
    sub(/^COPY preload_export\.learning_resource /, "COPY app.learning_resource ")
    print
    flag = 1
    next
  }
  flag {
    print
    if ($0 == "\\.") {
      exit
    }
  }
' "$RESTORE_SQL" > "$LEARNING_RESOURCE_STAGE_SQL"

if grep -q '^COPY app\.learning_resource ' "$LEARNING_RESOURCE_STAGE_SQL"; then
  echo "Restoring preloaded learning resources..."
  psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
\i $LEARNING_RESOURCE_STAGE_SQL
SQL
else
  echo "Preparing placeholder learning resources for imported resource documents..."
  awk '
  /COPY rag\.resource_document / {
    print "CREATE TEMP TABLE pg_temp.resource_document_stage ("
    print "  id UUID,"
    print "  resource_id UUID,"
    print "  title TEXT,"
    print "  domain TEXT,"
    print "  resource_type app.resource_type,"
    print "  difficulty_level app.difficulty_level,"
    print "  source_kind app.source_kind,"
    print "  source_ref TEXT,"
    print "  summary_text TEXT,"
    print "  transcript_text TEXT,"
    print "  access_scope app.access_scope,"
    print "  owner_user_id UUID,"
    print "  course_id UUID,"
    print "  metadata_json JSONB,"
    print "  created_at TIMESTAMPTZ,"
    print "  updated_at TIMESTAMPTZ"
    print ");"
    print "COPY pg_temp.resource_document_stage (id, resource_id, title, domain, resource_type, difficulty_level, source_kind, source_ref, summary_text, transcript_text, access_scope, owner_user_id, course_id, metadata_json, created_at, updated_at) FROM stdin;"
    flag = 1
    next
  }
  flag {
    print
    if ($0 == "\\.") {
      exit
    }
  }
' "$RESTORE_SQL" > "$RESOURCE_STAGE_SQL"

  psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
\i $RESOURCE_STAGE_SQL
INSERT INTO app.learning_resource (
  id,
  title,
  domain,
  resource_type,
  difficulty_level,
  source_kind,
  access_scope,
  owner_user_id,
  course_id,
  summary_text,
  metadata_json,
  status,
  created_at,
  updated_at
)
SELECT
  s.resource_id,
  s.title,
  s.domain,
  s.resource_type,
  s.difficulty_level,
  s.source_kind,
  CASE
    WHEN s.access_scope = 'USER' AND u.id IS NULL THEN 'GLOBAL'::app.access_scope
    WHEN s.access_scope = 'COURSE' AND c.id IS NULL THEN 'GLOBAL'::app.access_scope
    ELSE s.access_scope
  END AS access_scope,
  CASE WHEN u.id IS NOT NULL THEN s.owner_user_id ELSE NULL END AS owner_user_id,
  CASE WHEN c.id IS NOT NULL THEN s.course_id ELSE NULL END AS course_id,
  s.summary_text,
  COALESCE(s.metadata_json, '{}'::jsonb) || jsonb_build_object(
    'sourceRef', s.source_ref,
    'transcriptText', s.transcript_text,
    'restoredFrom', 'vector_data.dump'
  ),
  'ACTIVE',
  s.created_at,
  s.updated_at
FROM pg_temp.resource_document_stage s
LEFT JOIN app.users u ON u.id = s.owner_user_id
LEFT JOIN app.courses c ON c.id = s.course_id
WHERE NOT EXISTS (
  SELECT 1
  FROM app.learning_resource lr
  WHERE lr.id = s.resource_id
);
SQL
fi

awk '
  /^COPY (app|preload_export)\.learning_resource / {
    skip = 1
    next
  }
  skip {
    if ($0 == "\\.") {
      skip = 0
    }
    next
  }
  {
    sub(/^COPY preload_export\.knowledge_document /, "COPY rag.knowledge_document ")
    sub(/^COPY preload_export\.knowledge_chunk /, "COPY rag.knowledge_chunk ")
    sub(/^COPY preload_export\.resource_document /, "COPY rag.resource_document ")
    sub(/^COPY preload_export\.resource_chunk /, "COPY rag.resource_chunk ")
    sub(/^COPY preload_export\.wiki_page /, "COPY rag.wiki_page ")
    sub(/^COPY preload_export\.wiki_link /, "COPY rag.wiki_link ")
    sub(/^COPY preload_export\.term_lexicon /, "COPY rag.term_lexicon ")
    sub(/^COPY preload_export\.synonym_group /, "COPY rag.synonym_group ")
    print
  }
' "$RESTORE_SQL" > "$VECTOR_DATA_SQL"

echo "Restoring vectorized knowledge base from dump..."
psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 --single-transaction <<SQL
SET session_replication_role = replica;
\i $VECTOR_DATA_SQL
SET session_replication_role = DEFAULT;
SELECT setval('rag.knowledge_chunk_id_seq', COALESCE((SELECT MAX(id) FROM rag.knowledge_chunk), 0) + 1, false);
SELECT setval('rag.resource_chunk_id_seq', COALESCE((SELECT MAX(id) FROM rag.resource_chunk), 0) + 1, false);
SELECT setval('rag.wiki_link_id_seq', COALESCE((SELECT MAX(id) FROM rag.wiki_link), 0) + 1, false);
SELECT setval('rag.term_lexicon_id_seq', COALESCE((SELECT MAX(id) FROM rag.term_lexicon), 0) + 1, false);
SELECT setval('rag.synonym_group_id_seq', COALESCE((SELECT MAX(id) FROM rag.synonym_group), 0) + 1, false);
SQL

CHUNKS=$(psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM rag.knowledge_chunk;")
RESOURCES=$(psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM rag.resource_chunk;")
echo "Vector data restore complete: $CHUNKS knowledge chunks, $RESOURCES resource chunks loaded."
