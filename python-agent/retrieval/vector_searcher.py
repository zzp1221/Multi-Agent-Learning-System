"""
Vector (semantic) search channel using pgvector cosine similarity.
"""
import os
import time

import dashscope
from dashscope import MultiModalEmbedding

from retrieval.source_quality import low_value_source_filter_sql
from src.ai_modules.config import get_settings

KNOWLEDGE_LOW_VALUE_SQL = low_value_source_filter_sql("kd")
RESOURCE_LOW_VALUE_SQL = low_value_source_filter_sql("rd")


class VectorSearcher:
    """Semantic search via pgvector <=> cosine distance on knowledge_chunk embeddings."""

    def __init__(
        self,
        dimension: int | None = None,
        model: str | None = None,
        max_embedding_retries: int | None = None,
        embedding_retry_backoff_seconds: float | None = None,
    ):
        settings = get_settings()
        self.dimension = dimension or settings.knowledge_embedding_dimension
        self.model = model or settings.knowledge_embedding_model_name
        self.api_key = settings.effective_embedding_api_key
        self.request_timeout = max(1.0, settings.knowledge_embedding_timeout_seconds)
        self.max_embedding_retries = max(1, max_embedding_retries or settings.knowledge_embedding_max_retries)
        self.embedding_retry_backoff_seconds = max(
            0.0,
            embedding_retry_backoff_seconds
            if embedding_retry_backoff_seconds is not None
            else settings.knowledge_embedding_retry_backoff_seconds,
        )

    def _embed(self, text: str) -> list[float]:
        if self.api_key:
            os.environ["DASHSCOPE_API_KEY"] = self.api_key
            dashscope.api_key = self.api_key
        last_exc: Exception | None = None
        for attempt in range(self.max_embedding_retries):
            try:
                resp = MultiModalEmbedding.call(
                    model=self.model,
                    input=[{"text": text}],
                    dimension=self.dimension,
                    output_type="dense",
                    request_timeout=self.request_timeout,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 >= self.max_embedding_retries:
                    raise
                time.sleep(self.embedding_retry_backoff_seconds * (2**attempt))
        else:
            raise RuntimeError("Embedding API request failed") from last_exc
        if resp.status_code != 200:
            raise RuntimeError(f"Embedding API error: {resp.code} {resp.message}")
        return resp.output["embeddings"][0]["embedding"]

    def _embed_vec_str(self, vec: list[float]) -> str:
        return "[" + ",".join(str(v) for v in vec) + "]"

    def search(self, cur, query: str, top_k: int = 10,
               domain: str = "COMPUTER_SCIENCE") -> list[tuple]:
        """
        Returns top_k results as [(slug, title, similarity_score), ...].
        Searches knowledge_chunk only.
        """
        embedding = self._embed(query)
        vec_str = self._embed_vec_str(embedding)

        cur.execute("""
            SELECT kd.source_ref AS slug, kd.title,
                   ROUND((1 - (kc.embedding <=> %s::vector))::numeric, 4) AS similarity
            FROM rag.knowledge_chunk kc
            JOIN rag.knowledge_document kd ON kd.id = kc.document_id
            WHERE kd.domain = %s
              {knowledge_low_value_filter}
            ORDER BY kc.embedding <=> %s::vector
            LIMIT %s
        """.format(knowledge_low_value_filter=KNOWLEDGE_LOW_VALUE_SQL), (vec_str, domain, vec_str, top_k))
        return [(row[0], row[1], float(row[2])) for row in cur.fetchall()]

    def search_all(self, cur, query: str, top_k: int = 10,
                   domain: str = "COMPUTER_SCIENCE") -> list[tuple]:
        """
        Returns top_k results from both knowledge_chunk and resource_chunk.
        Results tagged with source: [(slug, title, similarity, source), ...]
        """
        embedding = self._embed(query)
        vec_str = self._embed_vec_str(embedding)

        cur.execute("""
            SELECT slug, title, similarity, source FROM (
                SELECT kd.source_ref AS slug, kd.title,
                       ROUND((1 - (kc.embedding <=> %s::vector))::numeric, 4) AS similarity,
                       'knowledge' AS source
                FROM rag.knowledge_chunk kc
                JOIN rag.knowledge_document kd ON kd.id = kc.document_id
                WHERE kd.domain = %s
                  {knowledge_low_value_filter}
                UNION ALL
                SELECT rd.source_ref AS slug, rd.title,
                       ROUND((1 - (rc.embedding <=> %s::vector))::numeric, 4) AS similarity,
                       'resource' AS source
                FROM rag.resource_chunk rc
                JOIN rag.resource_document rd ON rd.id = rc.document_id
                JOIN app.learning_resource lr ON lr.id = rc.resource_id
                WHERE rc.domain = %s
                  AND lr.status = 'ACTIVE'
                  AND COALESCE(lr.metadata_json ->> 'wikiBindingStatus', '') <> 'LOW_CONFIDENCE_DROPPED'
                  AND rc.access_scope::text = 'GLOBAL'
                  {resource_low_value_filter}
            ) combined
            ORDER BY similarity DESC
            LIMIT %s
        """.format(
            knowledge_low_value_filter=KNOWLEDGE_LOW_VALUE_SQL,
            resource_low_value_filter=RESOURCE_LOW_VALUE_SQL,
        ), (vec_str, domain, vec_str, domain, top_k))
        return [(row[0], row[1], float(row[2]), row[3]) for row in cur.fetchall()]
