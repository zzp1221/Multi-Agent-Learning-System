"""混合检索 Agent 实现。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import RetrievalSummaryGenerator
from src.ai_modules.models import (
    ProgressPayload,
    ProgressSSEEvent,
    QueryRewriteResult,
    ReasoningChunkPayload,
    ReasoningChunkSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    RetrievalDocument,
    RetrievalResponse,
    SSEEvent,
)
from src.ai_modules.prompts import build_retrieval_summary_prompt
from src.ai_modules.retrieval import HybridRetrievalService, QueryRewriteService
from src.ai_modules.retrieval.evidence_relevance import (
    EVIDENCE_STATE_EMPTY,
    EVIDENCE_STATE_HIGH,
    EVIDENCE_STATE_LOW,
    EVIDENCE_STATE_PARTIAL,
    evidence_channel,
    evidence_title,
    select_relevant_evidence,
)
from src.ai_modules.retrieval.external_evidence import build_external_evidence_contract
from src.ai_modules.runtime import (
    RecoveryEngine,
    RecoveryFailureType,
    SystemSnapshot,
)
from src.ai_modules.runtime.skill_loader import append_user_skill_to_prompt

LOGGER = logging.getLogger(__name__)


class RetrievalAgent(PlaceholderAgent):
    """运行混合检索并将来源证据附加到参数中。"""

    def __init__(
        self,
        service: HybridRetrievalService | None = None,
        summary_generator: Any | None = None,
        query_rewrite_service: QueryRewriteService | None = None,
    ) -> None:
        super().__init__("Hybrid Retrieval Agent", "retrieving")
        self.service = service or HybridRetrievalService()
        self.summary_generator = summary_generator
        self.query_rewrite_service = query_rewrite_service or QueryRewriteService()
        self.recovery_engine = RecoveryEngine()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return append_user_skill_to_prompt(
            build_retrieval_summary_prompt(snapshot),
            component_name="retrieval_llm",
            ability_key="ability:path",
        )

    async def run(
        self,
        *,
        task_id: str,
        trace_id: str,
        seq: int,
        service_type: str,
        params: dict,
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> AsyncIterator[SSEEvent]:
        query = str(params.get("query") or params.get("rewrittenQuery") or "未提供查询")
        rewritten_query = str(params.get("rewrittenQuery") or query)
        if self._is_plain_tutoring(service_type, params):
            isolated = self.query_rewrite_service.isolate_plain_tutoring_query(
                params,
                QueryRewriteResult(
                    originalQuery=query,
                    rewrittenQuery=rewritten_query,
                    keywords=list(params.get("keywords", [])),
                ),
            )
            query = isolated.original_query
            rewritten_query = isolated.rewritten_query
            params["query"] = query
            params["rewrittenQuery"] = rewritten_query
            params["keywords"] = isolated.keywords
        web_search_query = self._web_search_query(params, query=query)
        params["webSearchQuery"] = web_search_query
        keywords = list(params.get("keywords", []))
        query_expansion = self.service.build_query_expansion(rewritten_query)
        params["queryExpansionResult"] = query_expansion.to_dict()
        retrieval_strategy = self._retrieval_strategy(params)
        graph_intent = self._graph_intent(params)
        web_search_enabled = retrieval_strategy == "WEB_AUGMENTED" or self._web_search_enabled(params)
        deep_quality_mode = self._is_deep_quality_mode(params)
        current_seq = seq

        if deep_quality_mode:
            for text in self._build_pre_retrieval_reasoning_chunks(
                query=query,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                expanded_query_count=len(query_expansion.expanded_queries),
                include_question=params.get("publicReasoningQuestionIntroduced") is not True,
            ):
                yield ReasoningChunkSSEEvent(
                    taskId=task_id,
                    traceId=trace_id,
                    seq=current_seq,
                    payload=ReasoningChunkPayload(
                        text=text,
                        stage="deep_reasoning",
                        provider="system",
                        model="public-process",
                    ),
                )
                current_seq += 1

        if retrieval_strategy in {"NONE", "CONTEXT_ONLY"}:
            retrieval_response = self._empty_retrieval_response(
                query=query,
                rewritten_query=rewritten_query,
                keywords=keywords,
                retrieval_strategy=retrieval_strategy,
            )
            summary_text = retrieval_response.sources_summary
            params["retrievalResult"] = retrieval_response.model_dump(by_alias=True)
            params["retrievalEvidence"] = self._build_retrieval_evidence(retrieval_response, summary_text)
            params.update(self._empty_external_evidence_contract())
            params["retrievalSummaryText"] = summary_text
            yield ProgressSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ProgressPayload(
                    stage=self.stage_name,
                    percent=45,
                    message="Skipped retrieval; using conversation context.",
                ),
            )
            current_seq += 1
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ResultChunkPayload(text=f"Retrieval strategy {retrieval_strategy}: {summary_text}"),
            )
            return

        retrieval_response, summary_text = await self._run_agent_core_loop(
            query=query,
            rewritten_query=rewritten_query,
            keywords=keywords,
            web_search_enabled=web_search_enabled,
            web_search_query=web_search_query,
            retrieval_strategy=retrieval_strategy,
            graph_intent=graph_intent,
            params=params,
            system_prompt=system_prompt,
            expanded_queries=query_expansion.expanded_queries,
        )
        filtered_response, evidence_selection = self._filter_retrieval_response(
            retrieval_response,
            query=query,
        )
        retrieval_response = filtered_response
        if summary_text == "无命中来源" or not summary_text.strip():
            summary_text = retrieval_response.sources_summary
        external_contract = self._build_external_evidence_contract(
            query=web_search_query,
            retrieval_response=retrieval_response,
            params=params,
        ) if web_search_enabled else self._empty_external_evidence_contract()
        if web_search_enabled:
            retrieval_response = self._promote_adopted_web_sources(
                retrieval_response,
                external_contract=external_contract,
            )
            summary_text = retrieval_response.sources_summary
        params["retrievalResult"] = retrieval_response.model_dump(by_alias=True)
        params["retrievalEvidence"] = self._build_retrieval_evidence(retrieval_response, summary_text)
        params.update(external_contract)
        diagnostics = self._build_evidence_diagnostics(
            selection=evidence_selection,
            query_expansion=query_expansion.to_dict(),
        )
        params["retrievalEvidenceDiagnostics"] = diagnostics

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=45,
                message=f"检索完成，命中 {len(retrieval_response.documents)} 个候选来源",
            ),
        )
        current_seq += 1
        if deep_quality_mode:
            yield ReasoningChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ReasoningChunkPayload(
                    text=self._build_retrieval_reasoning_summary(
                        query=query,
                        retrieval_response=retrieval_response,
                        diagnostics=diagnostics,
                    ),
                    stage="deep_reasoning",
                    provider="system",
                    model="public-process",
                ),
            )
            current_seq += 1
        if web_search_enabled:
            yield ReasoningChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ReasoningChunkPayload(
                    text=self._build_web_reasoning_summary(
                        query=web_search_query,
                        retrieval_response=retrieval_response,
                        params=params,
                    ),
                    stage="retrieval",
                    provider="web",
                    model="evidence",
                ),
            )
            current_seq += 1
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ResultChunkPayload(
                text=(
                    f"检索查询: {retrieval_response.rewritten_query}；"
                    f"来源摘要: {summary_text}"
                )
            ),
        )

    def _build_retrieval_evidence(self, retrieval_response: RetrievalResponse, summary_text: str) -> list[dict[str, Any]]:
        evidence_items: list[dict[str, Any]] = []
        for document in retrieval_response.documents[:8]:
            evidence_items.append(
                {
                    "title": document.title,
                    "slug": document.slug,
                    "channel": document.channel,
                    "score": document.score,
                    "evidence": document.evidence or document.snippet or "",
                    "url": document.url,
                    "sourceTitle": document.source_title,
                }
            )
        if not evidence_items and summary_text.strip():
            evidence_items.append(
                {
                    "title": "检索摘要",
                    "slug": "retrieval-summary",
                    "channel": "summary",
                    "score": 0.0,
                    "evidence": summary_text.strip(),
                    "url": None,
                    "sourceTitle": None,
                }
            )
        return evidence_items

    def _build_pre_retrieval_reasoning_chunks(
        self,
        *,
        query: str,
        web_search_enabled: bool,
        web_search_query: str,
        expanded_query_count: int = 1,
        include_question: bool = True,
    ) -> list[str]:
        chunks = []
        if include_question:
            chunks.append(
                f"我先识别问题意图：当前要回答的是「{self._truncate_text(query, 120)}」。"
                "我会围绕这个问题组织解释，不把历史画像或检索扩写词当成新的问题。\n"
            )
        if expanded_query_count > 1:
            chunks.append(
                f"接着规划证据检索：保留原问题，并补充 {expanded_query_count - 1} 条别名/同义词扩展查询，"
                "让多义缩写由检索排序来决定，不做单向替换。\n"
            )
        else:
            chunks.append("接着规划证据检索：先查看本地知识库是否有直接相关证据，再决定哪些证据能进入回答。\n")
        if web_search_enabled:
            chunks.append(
                f"联网计划：已开启联网搜索，搜索词使用当前问题清洗结果「{self._truncate_text(web_search_query, 120)}」。\n"
            )
        return chunks

    def _build_retrieval_reasoning_summary(
        self,
        *,
        query: str,
        retrieval_response: RetrievalResponse,
        diagnostics: dict[str, Any],
    ) -> str:
        state = str(diagnostics.get("evidenceState") or EVIDENCE_STATE_EMPTY)
        lines = [f"接着检查证据：围绕「{self._truncate_text(query, 120)}」筛选本地候选。"]
        if retrieval_response.documents:
            if state == EVIDENCE_STATE_HIGH:
                lines.append("本地知识库找到可作为高置信参考的资料：")
            elif state == EVIDENCE_STATE_PARTIAL:
                lines.append("本地知识库找到部分相关资料，可辅助回答但会降低断言强度：")
            else:
                lines.append("本地知识库只有低置信候选，会谨慎作为 fallback context：")
            for document in retrieval_response.documents[:3]:
                title = evidence_title(document)
                channel = evidence_channel(document)
                suffix = f"（{channel}）" if channel else ""
                lines.append(f"- {self._truncate_text(title, 80)}{suffix}")
        else:
            lines.append("本地证据不足，将谨慎使用通用知识回答。")
        discarded = int(diagnostics.get("discardedCount") or 0)
        if discarded > 0:
            lines.append(f"同时排除 {discarded} 条低相关候选，避免把无关概念混入回答。")
        return "\n".join(lines) + "\n"

    def _filter_retrieval_response(
        self,
        retrieval_response: RetrievalResponse,
        *,
        query: str,
    ) -> tuple[RetrievalResponse, Any]:
        selection = select_relevant_evidence(
            query=query,
            documents=list(retrieval_response.documents),
            limit=5,
        )
        documents = selection.adopted
        if selection.evidence_state == EVIDENCE_STATE_HIGH:
            summary_prefix = "高置信来源"
        elif selection.evidence_state == EVIDENCE_STATE_PARTIAL:
            summary_prefix = "部分相关来源"
        elif selection.evidence_state == EVIDENCE_STATE_LOW:
            summary_prefix = "低置信候选"
        else:
            summary_prefix = "无命中来源"
        sources_summary = "；".join(
            f"{document.title}({document.channel}:{document.score})"
            for document in documents[:3]
        )
        if sources_summary:
            sources_summary = f"{summary_prefix}: {sources_summary}"
        else:
            sources_summary = summary_prefix
        return (
            retrieval_response.model_copy(
                update={
                    "documents": documents,
                    "sources_summary": sources_summary,
                },
            ),
            selection,
        )

    def _promote_adopted_web_sources(
        self,
        retrieval_response: RetrievalResponse,
        *,
        external_contract: dict[str, list[Any]],
    ) -> RetrievalResponse:
        adopted = external_contract.get("adoptedExternalSources")
        if not isinstance(adopted, list) or not adopted:
            return retrieval_response

        web_documents = [
            self._web_source_document(item)
            for item in adopted
            if isinstance(item, dict) and str(item.get("url") or "").startswith(("http://", "https://"))
        ]
        if not web_documents:
            return retrieval_response

        supplemental_documents = [
            document
            for document in retrieval_response.documents
            if document.channel == "web" or self._document_mentions_keyword(document, retrieval_response.keywords)
        ]
        documents: list[RetrievalDocument] = []
        seen: set[str] = set()
        for document in [*web_documents, *supplemental_documents]:
            key = self._document_identity(document)
            if key in seen:
                continue
            seen.add(key)
            documents.append(document)
            if len(documents) >= 5:
                break

        sources_summary = "；".join(
            f"{document.title}({document.channel}:{document.score})"
            for document in documents[:3]
        ) or retrieval_response.sources_summary
        return retrieval_response.model_copy(
            update={
                "documents": documents,
                "sources_summary": sources_summary,
            }
        )

    def _build_evidence_diagnostics(
        self,
        *,
        selection: Any,
        query_expansion: dict[str, Any],
    ) -> dict[str, Any]:
        discarded_count = int(getattr(selection, "discarded_count", 0) or 0)
        return {
            "discardedLocalCount": discarded_count,
            "relevanceGate": "soft-confidence-layering",
            "rawCandidateCount": int(getattr(selection, "raw_candidate_count", 0) or 0),
            "expandedQueryCount": len(query_expansion.get("expandedQueries", []) or []),
            "adoptedHighCount": int(getattr(selection, "adopted_high_count", 0) or 0),
            "adoptedMediumCount": int(getattr(selection, "adopted_medium_count", 0) or 0),
            "fallbackLowCount": int(getattr(selection, "fallback_low_count", 0) or 0),
            "discardedCount": discarded_count,
            "expansionSources": list(query_expansion.get("expansionSources", []) or []),
            "evidenceState": str(getattr(selection, "evidence_state", EVIDENCE_STATE_EMPTY) or EVIDENCE_STATE_EMPTY),
        }

    def _web_source_document(self, item: dict[str, Any]) -> RetrievalDocument:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or item.get("sourceTitle") or url).strip()
        snippet = str(item.get("snippet") or "").strip()
        return RetrievalDocument(
            slug=url,
            title=title,
            score=self._safe_score(item.get("score")),
            channel="web",
            matchType="external",
            evidence=snippet or title,
            snippet=snippet,
            url=url,
            sourceTitle=str(item.get("sourceTitle") or title).strip(),
            publishedDate=str(item.get("publishedDate") or "").strip() or None,
        )

    @staticmethod
    def _document_identity(document: RetrievalDocument) -> str:
        if document.url:
            return f"url:{document.url.strip().lower()}"
        return f"slug:{document.slug.strip().lower()}"

    @staticmethod
    def _document_mentions_keyword(document: RetrievalDocument, keywords: list[str]) -> bool:
        text = " ".join(
            value
            for value in [
                document.title,
                document.snippet or "",
                document.source_title or "",
            ]
            if value
        ).lower()
        for keyword in keywords:
            normalized = str(keyword or "").strip().lower()
            if len(normalized) >= 3 and normalized in text:
                return True
            if normalized.isascii() and len(normalized) >= 2 and normalized in text:
                return True
        return False

    @staticmethod
    def _safe_score(value: Any) -> float:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return 0.0

    def _build_web_reasoning_summary(
        self,
        *,
        query: str,
        retrieval_response: RetrievalResponse,
        params: dict[str, Any],
    ) -> str:
        contract = self._build_external_evidence_contract(
            query=query,
            retrieval_response=retrieval_response,
            params=params,
        )
        adopted = contract["adoptedExternalSources"]
        ignored = contract["ignoredExternalSources"]
        web_items = self._web_channel_items(params)

        lines = [f"已开启联网搜索，搜索词：{query}"]
        if adopted:
            lines.append("采用来源：")
            for item in adopted[:5]:
                lines.append(f"- {item['title']} | {item['url']} | {item['reason']}")
        else:
            lines.append("采用来源：未采用到足够相关且带 URL 的联网证据。")

        if ignored:
            lines.append("忽略来源：")
            for item in ignored[:4]:
                lines.append(f"- {item['title']} | {item['url']} | {item['reason']}")
        elif web_items:
            lines.append("忽略来源：未发现需要忽略的联网候选。")
        else:
            lines.append("忽略来源：联网通道未返回候选结果。")

        lines.append("说明：以上只展示可审计的检索过程，不包含模型内部思维链。")
        return "\n".join(lines) + "\n"

    def _build_external_evidence_contract(
        self,
        *,
        query: str,
        retrieval_response: RetrievalResponse,
        params: dict[str, Any],
    ) -> dict[str, list[Any]]:
        return build_external_evidence_contract(
            query=query,
            documents=retrieval_response.model_dump(by_alias=True).get("documents", []),
            web_items=self._web_channel_items(params),
        )

    def _empty_external_evidence_contract(self) -> dict[str, list[Any]]:
        return {
            "adoptedExternalSources": [],
            "ignoredExternalSources": [],
            "evidenceIds": [],
            "externalUrls": [],
        }

    def _web_channel_items(self, params: dict[str, Any]) -> list[Any]:
        web_result = params.get("webRetrievalResult")
        if isinstance(web_result, dict) and isinstance(web_result.get("results"), list):
            return list(web_result["results"])
        raw_result = params.get("retrievalRawResult")
        if isinstance(raw_result, dict):
            channels = raw_result.get("channels")
            if isinstance(channels, dict) and isinstance(channels.get("web"), list):
                return list(channels["web"])
        return []

    def _web_item_title_url(self, item: Any) -> tuple[str, str]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("sourceTitle") or item.get("url") or "").strip()
            url = str(item.get("url") or item.get("slug") or "").strip()
            return title, url if url.startswith(("http://", "https://")) else ""
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return "", ""
        metadata = next((extra for extra in item[3:] if isinstance(extra, dict)), {})
        title = str(item[1] or "").strip()
        url = str(metadata.get("url") or item[0] or "").strip()
        return title, url if url.startswith(("http://", "https://")) else ""

    async def _run_agent_core_loop(
        self,
        *,
        query: str,
        rewritten_query: str,
        keywords: list[str],
        web_search_enabled: bool,
        web_search_query: str,
        retrieval_strategy: str,
        graph_intent: str | None,
        params: dict[str, Any],
        system_prompt: str,
        expanded_queries: list[str] | None = None,
    ):
        try:
            # 步骤 1: 获取原始检索结果（1 次数据库查询，带恢复机制）
            raw_result = await self._safe_get_expanded_raw_result(
                rewritten_query=rewritten_query,
                keywords=keywords,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                retrieval_strategy=retrieval_strategy,
                graph_intent=graph_intent,
                expanded_queries=expanded_queries,
            )
            params["retrievalRawResult"] = raw_result

            # 步骤 2: 分渠道结果（确定性操作，并行执行）
            grep_task = asyncio.to_thread(self.service.channel_results, raw_result, "grep")
            vector_task = asyncio.to_thread(self.service.channel_results, raw_result, "vector")
            graph_task = asyncio.to_thread(self.service.channel_results, raw_result, "graph")
            web_task = asyncio.to_thread(self.service.channel_results, raw_result, "web")
            grep_result, vector_result, graph_result, web_result = await asyncio.gather(
                grep_task, vector_task, graph_task, web_task,
            )
            params["grepRetrievalResult"] = {
                "priority": grep_result.get("priority", []) if isinstance(grep_result, dict) else [],
                "query": rewritten_query,
            }
            params["vectorRetrievalResult"] = {
                "results": list(vector_result) if not isinstance(vector_result, dict) else vector_result.get("results", []),
                "query": rewritten_query,
            }
            params["graphRetrievalResult"] = {
                "results": list(graph_result) if not isinstance(graph_result, dict) else graph_result.get("results", []),
                "query": rewritten_query,
                "graphIntent": graph_intent,
            }
            params["webRetrievalResult"] = {
                "enabled": web_search_enabled,
                "results": list(web_result) if not isinstance(web_result, dict) else web_result.get("results", []),
                "query": web_search_query,
            }

            # 步骤 3: RRF 融合（确定性操作）
            retrieval_response = self.service.build_response(
                query=query, rewritten_query=rewritten_query, keywords=keywords, raw_result=raw_result,
            )
            params["mergedRetrievalResult"] = retrieval_response

            # 步骤 4: 摘要来源（默认本地，按需 LLM）
            summary_text = await self._safe_summarize(
                retrieval_response=retrieval_response,
                system_prompt=system_prompt,
                llm_enabled=self._llm_summary_enabled(params),
            )
            params["retrievalSummaryText"] = summary_text
            return retrieval_response, summary_text

        except Exception:
            LOGGER.warning("Direct retrieval failed, falling back to service retrieval.", exc_info=True)

        retrieval_response = await asyncio.to_thread(
            self.service.retrieve,
            query=query,
            rewritten_query=rewritten_query,
            keywords=keywords,
            web_search_enabled=web_search_enabled,
            web_search_query=web_search_query,
            graph_intent=graph_intent,
        )
        summary_text = await self._safe_summarize(
            retrieval_response=retrieval_response,
            system_prompt=system_prompt,
            llm_enabled=self._llm_summary_enabled(params),
        )
        return retrieval_response, summary_text

    async def _safe_get_raw_result(
        self,
        *,
        rewritten_query: str,
        keywords: list[str],
        web_search_enabled: bool = False,
        web_search_query: str | None = None,
        retrieval_strategy: str = "LOCAL_HYBRID",
        graph_intent: str | None = None,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            if retrieval_strategy == "LOCAL_GREP_FIRST":
                retrieve_grep_first = getattr(self.service, "retrieve_grep_first", None)
                if callable(retrieve_grep_first):
                    return await asyncio.to_thread(
                        retrieve_grep_first,
                        rewritten_query,
                        web_search_enabled=web_search_enabled,
                        web_search_query=web_search_query,
                        graph_intent=graph_intent,
                    )
            return await asyncio.to_thread(
                self.service.retrieve_raw,
                rewritten_query,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                graph_intent=graph_intent,
            )

        async def fallback_operation() -> dict[str, Any]:
            fallback_payload = self.service.fallback_raw_result(
                rewritten_query=rewritten_query,
                keywords=keywords,
            )
            await self.recovery_engine.recover_retrieval_unavailable(
                query=rewritten_query,
                fallback_payload=fallback_payload,
            )
            return fallback_payload

        return await self.recovery_engine.call_with_recovery(
            failure_type=RecoveryFailureType.RETRIEVAL_UNAVAILABLE,
            operation=operation,
            fallback_operation=fallback_operation,
        )

    async def _safe_get_expanded_raw_result(
        self,
        *,
        rewritten_query: str,
        keywords: list[str],
        web_search_enabled: bool = False,
        web_search_query: str | None = None,
        retrieval_strategy: str = "LOCAL_HYBRID",
        graph_intent: str | None = None,
        expanded_queries: list[str] | None = None,
    ) -> dict[str, Any]:
        queries = self._dedupe_queries([*(expanded_queries or []), rewritten_query])
        if len(queries) <= 1:
            return await self._safe_get_raw_result(
                rewritten_query=rewritten_query,
                keywords=keywords,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                retrieval_strategy=retrieval_strategy,
                graph_intent=graph_intent,
            )

        raw_results: list[dict[str, Any]] = []
        for index, expanded_query in enumerate(queries):
            raw_results.append(
                await self._safe_get_raw_result(
                    rewritten_query=expanded_query,
                    keywords=keywords,
                    web_search_enabled=web_search_enabled if index == 0 else False,
                    web_search_query=web_search_query,
                    retrieval_strategy=retrieval_strategy,
                    graph_intent=graph_intent,
                )
            )
        return self._merge_raw_results(raw_results, rewritten_query=rewritten_query, expanded_queries=queries)

    def _merge_raw_results(
        self,
        raw_results: list[dict[str, Any]],
        *,
        rewritten_query: str,
        expanded_queries: list[str],
    ) -> dict[str, Any]:
        if not raw_results:
            return self.service.fallback_raw_result(rewritten_query=rewritten_query, keywords=[])
        primary = dict(raw_results[0])
        merged_channels: dict[str, Any] = {
            "grep": {"priority": [], "normal": [], "normal_count": 0},
            "vector": [],
            "graph": [],
            "web": [],
        }
        for raw_result in raw_results:
            channels = raw_result.get("channels", {}) if isinstance(raw_result, dict) else {}
            grep = channels.get("grep", {}) if isinstance(channels, dict) else {}
            if isinstance(grep, dict):
                merged_channels["grep"]["priority"].extend(grep.get("priority", []) or [])
                merged_channels["grep"]["normal"].extend(grep.get("normal", []) or [])
                merged_channels["grep"]["normal_count"] += int(grep.get("normal_count") or 0)
            for channel_name in ("vector", "graph", "web"):
                items = channels.get(channel_name, []) if isinstance(channels, dict) else []
                if isinstance(items, list):
                    merged_channels[channel_name].extend(items)

        merged_channels["grep"]["priority"] = self._dedupe_ranked_items(merged_channels["grep"]["priority"])
        merged_channels["grep"]["normal"] = self._dedupe_ranked_items(merged_channels["grep"]["normal"])
        for channel_name in ("vector", "graph", "web"):
            merged_channels[channel_name] = self._dedupe_ranked_items(merged_channels[channel_name])

        top_lists: list[list[Any]] = []
        for raw_result in raw_results:
            top = raw_result.get("top", []) if isinstance(raw_result, dict) else []
            if isinstance(top, list):
                top_lists.append(top)
        primary["query"] = rewritten_query
        primary["expandedQueries"] = expanded_queries
        primary["channels"] = merged_channels
        primary["top"] = self._rrf_merge_top_lists(top_lists)[:8]
        return primary

    def _dedupe_queries(self, queries: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for query in queries:
            text = " ".join(str(query or "").split())
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _dedupe_ranked_items(self, items: list[Any]) -> list[Any]:
        seen: set[str] = set()
        deduped: list[Any] = []
        for item in items:
            key = self._ranked_item_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _ranked_item_key(self, item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("url") or item.get("slug") or item.get("title") or "").strip().lower()
        if isinstance(item, (list, tuple)) and item:
            return str(item[0]).strip().lower()
        return ""

    def _rrf_merge_top_lists(self, ranked_lists: list[list[Any]]) -> list[tuple[str, str, float]]:
        scored: dict[str, dict[str, Any]] = {}
        for list_index, items in enumerate(ranked_lists):
            weight = 1.0 if list_index == 0 else 0.92
            for rank, item in enumerate(items):
                slug, title = self._ranked_item_slug_title(item)
                if not slug:
                    continue
                key = slug.strip().lower()
                entry = scored.setdefault(key, {"slug": slug, "title": title or slug, "score": 0.0, "firstRank": rank})
                entry["score"] += weight / (60 + rank + 1)
                entry["firstRank"] = min(entry["firstRank"], rank)
        ranked = sorted(scored.values(), key=lambda item: (-item["score"], item["firstRank"]))
        return [
            (str(item["slug"]), str(item["title"]), round(float(item["score"]), 4))
            for item in ranked
        ]

    def _ranked_item_slug_title(self, item: Any) -> tuple[str, str]:
        if isinstance(item, dict):
            slug = str(item.get("slug") or item.get("url") or item.get("id") or "").strip()
            title = str(item.get("title") or item.get("sourceTitle") or slug).strip()
            return slug, title
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return str(item[0]).strip(), str(item[1]).strip()
        return "", ""

    def _web_search_query(self, params: dict[str, Any], *, query: str) -> str:
        for value in (
            params.get("webSearchQuery"),
            params.get("originalQuery"),
            params.get("userInput"),
            params.get("message"),
            params.get("question"),
            query,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return query.strip()

    def _is_deep_quality_mode(self, params: dict[str, Any]) -> bool:
        reasoning_mode = params.get("reasoningMode")
        if isinstance(reasoning_mode, str) and reasoning_mode.strip().upper() == "DEEP":
            return True
        return params.get("deepReasoning") is True or params.get("deepQualityMode") is True

    def _truncate_text(self, text: str, max_length: int = 220) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1].rstrip() + "…"

    def _web_search_enabled(self, params: dict[str, Any]) -> bool:
        return bool(
            params.get("webSearchEnabled") is True
            or params.get("enableWebSearch") is True
            or params.get("tavilySearchEnabled") is True
        )

    def _is_plain_tutoring(self, service_type: str, params: dict[str, Any]) -> bool:
        if str(service_type or "").strip().upper() != "TUTORING":
            return False
        resource_types = params.get("resourceTypes")
        return (
            params.get("conversationTriggeredResourceGeneration") is not True
            and not (isinstance(resource_types, list) and bool(resource_types))
            and not params.get("resourceType")
        )

    async def _safe_summarize(
        self,
        *,
        retrieval_response,
        system_prompt: str,
        llm_enabled: bool = False,
    ) -> str:
        if not llm_enabled:
            return retrieval_response.sources_summary
        try:
            generator = self.summary_generator or RetrievalSummaryGenerator()
            summary = await generator.summarize(
                system_prompt=system_prompt,
                retrieval_response=retrieval_response,
            )
            if summary:
                return summary
        except Exception:
            LOGGER.warning(
                "Retrieval summary generation failed, falling back to source summary.",
                exc_info=True,
            )
        return retrieval_response.sources_summary

    def _retrieval_strategy(self, params: dict[str, Any]) -> str:
        strategy = str(params.get("retrievalStrategy") or "LOCAL_HYBRID").strip().upper()
        allowed = {
            "NONE",
            "CONTEXT_ONLY",
            "LOCAL_GREP_FIRST",
            "LOCAL_HYBRID",
            "WEB_AUGMENTED",
            "DEEP_EVIDENCE",
        }
        return strategy if strategy in allowed else "LOCAL_HYBRID"

    def _graph_intent(self, params: dict[str, Any]) -> str | None:
        direct = params.get("graphIntent")
        if isinstance(direct, str) and direct.strip():
            return direct.strip().upper()
        classification = params.get("queryClassification")
        if isinstance(classification, dict):
            value = classification.get("graphIntent")
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        return None

    def _llm_summary_enabled(self, params: dict[str, Any]) -> bool:
        return params.get("llmRetrievalSummaryEnabled") is True

    def _empty_retrieval_response(
        self,
        *,
        query: str,
        rewritten_query: str,
        keywords: list[str],
        retrieval_strategy: str,
    ) -> RetrievalResponse:
        return RetrievalResponse(
            query=query,
            rewrittenQuery=rewritten_query,
            keywords=keywords,
            documents=[],
            sourcesSummary=f"{retrieval_strategy} strategy skipped external retrieval",
        )
