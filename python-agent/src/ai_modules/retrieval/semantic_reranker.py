"""语义重排序器 - 使用交叉编码器提升检索质量"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SemanticReranker:
    """两阶段语义重排序器

    Stage 1: 快速粗排（启发式规则，保留top-20）
    Stage 2: 交叉编码器精排（语义相似度，输出top-k）
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_api: bool = False):
        """
        初始化重排序器

        Args:
            model_name: 交叉编码器模型名称
            use_api: 是否使用API（Cohere Rerank）而非本地模型
        """
        self.model_name = model_name
        self.use_api = use_api
        self.reranker = None
        self._initialized = False

    def _lazy_init(self):
        """延迟初始化模型（避免启动时加载）"""
        if self._initialized:
            return

        try:
            if self.use_api:
                # 使用Cohere Rerank API
                import cohere
                from src.ai_modules.config import get_settings

                settings = get_settings()
                api_key = settings.cohere_api_key if hasattr(settings, "cohere_api_key") else None

                if not api_key:
                    logger.warning("Cohere API key not found, semantic reranking disabled")
                    self._initialized = True
                    return

                self.reranker = cohere.Client(api_key)
                logger.info("Initialized Cohere Rerank API")
            else:
                # 使用本地BGE-Reranker模型
                from sentence_transformers import CrossEncoder

                self.reranker = CrossEncoder(self.model_name)
                logger.info(f"Loaded local reranker model: {self.model_name}")

            self._initialized = True

        except ImportError as e:
            logger.warning(f"Failed to load reranker dependencies: {e}. Semantic reranking disabled.")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize reranker: {e}")
            self._initialized = True

    def rerank(
        self,
        *,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
        coarse_top_n: int = 20,
        blend_weight: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        两阶段重排序

        Args:
            query: 用户查询
            documents: 候选文档列表（已包含初始score）
            top_k: 最终返回的文档数量
            coarse_top_n: 粗排保留的文档数量
            blend_weight: 语义得分权重（0-1），剩余为启发式得分权重

        Returns:
            重排序后的top-k文档
        """
        if not documents:
            return []

        # 如果文档数量小于等于top_k，直接返回
        if len(documents) <= top_k:
            return documents

        # Stage 1: 粗排（使用现有的启发式score）
        coarse_results = sorted(documents, key=lambda x: x.get("score", 0.0), reverse=True)[:coarse_top_n]

        # 如果粗排结果小于等于top_k，直接返回
        if len(coarse_results) <= top_k:
            return coarse_results

        # 尝试初始化模型
        self._lazy_init()

        # 如果模型未成功加载，回退到粗排结果
        if not self.reranker:
            logger.debug("Reranker not available, returning coarse ranking results")
            return coarse_results[:top_k]

        try:
            # Stage 2: 语义精排
            if self.use_api:
                reranked = self._rerank_with_api(query, coarse_results, top_k)
            else:
                reranked = self._rerank_with_model(query, coarse_results, top_k, blend_weight)

            return reranked

        except Exception as e:
            logger.error(f"Reranking failed: {e}, falling back to coarse results")
            return coarse_results[:top_k]

    def _rerank_with_model(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int,
        blend_weight: float,
    ) -> list[dict[str, Any]]:
        """使用本地交叉编码器模型重排序"""
        # 构建查询-文档对
        pairs = []
        for doc in documents:
            title = doc.get("title", "")
            snippet = doc.get("snippet", "")
            text = f"{title} {snippet}".strip()
            pairs.append([query, text])

        # 批量计算语义相似度得分
        rerank_scores = self.reranker.predict(pairs)

        # 混合评分: 启发式 + 语义
        for doc, rerank_score in zip(documents, rerank_scores):
            original_score = doc.get("score", 0.0)
            doc["rerank_score"] = float(rerank_score)
            doc["final_score"] = (
                original_score * (1 - blend_weight) +
                rerank_score * blend_weight
            )

        # 按最终得分排序
        sorted_docs = sorted(documents, key=lambda x: x["final_score"], reverse=True)
        return sorted_docs[:top_k]

    def _rerank_with_api(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """使用Cohere Rerank API重排序"""
        # 构建文档文本列表
        doc_texts = []
        for doc in documents:
            title = doc.get("title", "")
            snippet = doc.get("snippet", "")
            text = f"{title} {snippet}".strip()
            doc_texts.append(text)

        # 调用Cohere Rerank API
        response = self.reranker.rerank(
            query=query,
            documents=doc_texts,
            top_n=top_k,
            model="rerank-multilingual-v3.0",
        )

        # 构建重排序结果
        reranked = []
        for result in response.results:
            doc = documents[result.index]
            doc["rerank_score"] = result.relevance_score
            doc["final_score"] = result.relevance_score
            reranked.append(doc)

        return reranked
