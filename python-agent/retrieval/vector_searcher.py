"""
Vector (semantic) search channel using pgvector cosine similarity.
"""
import os

import dashscope
from dashscope import MultiModalEmbedding

from src.ai_modules.config import get_settings


class VectorSearcher:
    """Semantic search via pgvector <=> cosine distance on knowledge_chunk embeddings."""

    def __init__(self, dimension: int | None = None, model: str | None = None):
        settings = get_settings()
        self.dimension = dimension or settings.knowledge_embedding_dimension
        self.model = model or settings.knowledge_embedding_model_name
        self.api_key = settings.effective_embedding_api_key

    def _embed(self, text: str) -> list[float]:
        if self.api_key:
            os.environ["DASHSCOPE_API_KEY"] = self.api_key
            dashscope.api_key = self.api_key
        resp = MultiModalEmbedding.call(
            model=self.model,
            input=[{"text": text}],
            dimension=self.dimension,
            output_type="dense",
        )
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
            ORDER BY kc.embedding <=> %s::vector
            LIMIT %s
        """, (vec_str, domain, vec_str, top_k))
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
                UNION ALL
                SELECT rd.source_ref AS slug, rd.title,
                       ROUND((1 - (rc.embedding <=> %s::vector))::numeric, 4) AS similarity,
                       'resource' AS source
                FROM rag.resource_chunk rc
                JOIN rag.resource_document rd ON rd.id = rc.document_id
                JOIN app.learning_resource lr ON lr.id = rc.resource_id
                WHERE rc.domain = %s
                  AND lr.status = 'ACTIVE'
                  AND rc.access_scope::text = 'GLOBAL'
            ) combined
            ORDER BY similarity DESC
            LIMIT %s
        """, (vec_str, domain, vec_str, domain, top_k))
        return [(row[0], row[1], float(row[2]), row[3]) for row in cur.fetchall()]
