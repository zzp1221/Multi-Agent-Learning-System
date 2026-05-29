"""混合检索 Agent 实现。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import RetrievalSummaryGenerator
from src.ai_modules.models import ProgressPayload, ProgressSSEEvent, ResultChunkPayload, ResultChunkSSEEvent, RetrievalResponse, SSEEvent
from src.ai_modules.prompts import build_retrieval_summary_prompt
from src.ai_modules.retrieval import HybridRetrievalService
from src.ai_modules.runtime import (
    RecoveryEngine,
    RecoveryFailureType,
    SystemSnapshot,
)

LOGGER = logging.getLogger(__name__)


class RetrievalAgent(PlaceholderAgent):
    """运行混合检索并将来源证据附加到参数中。"""

    def __init__(
        self,
        service: HybridRetrievalService | None = None,
        summary_generator: Any | None = None,
    ) -> None:
        super().__init__("Hybrid Retrieval Agent", "retrieving")
        self.service = service or HybridRetrievalService()
        self.summary_generator = summary_generator
        self.recovery_engine = RecoveryEngine()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return build_retrieval_summary_prompt(snapshot)

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
        del service_type
        query = str(params.get("query") or params.get("rewrittenQuery") or "未提供查询")
        rewritten_query = str(params.get("rewrittenQuery") or query)
        keywords = list(params.get("keywords", []))
        retrieval_strategy = self._retrieval_strategy(params)
        graph_intent = self._graph_intent(params)
        web_search_enabled = retrieval_strategy == "WEB_AUGMENTED" or self._web_search_enabled(params)

        if retrieval_strategy in {"NONE", "CONTEXT_ONLY"}:
            retrieval_response = self._empty_retrieval_response(
                query=query,
                rewritten_query=rewritten_query,
                keywords=keywords,
                retrieval_strategy=retrieval_strategy,
            )
            summary_text = retrieval_response.sources_summary
            params["retrievalResult"] = retrieval_response.model_dump(by_alias=True)
            params["retrievalSummaryText"] = summary_text
            yield ProgressSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq,
                payload=ProgressPayload(
                    stage=self.stage_name,
                    percent=45,
                    message="Skipped retrieval; using conversation context.",
                ),
            )
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 1,
                payload=ResultChunkPayload(text=f"Retrieval strategy {retrieval_strategy}: {summary_text}"),
            )
            return

        retrieval_response, summary_text = await self._run_agent_core_loop(
            query=query,
            rewritten_query=rewritten_query,
            keywords=keywords,
            web_search_enabled=web_search_enabled,
            retrieval_strategy=retrieval_strategy,
            graph_intent=graph_intent,
            params=params,
            system_prompt=system_prompt,
        )
        params["retrievalResult"] = retrieval_response.model_dump(by_alias=True)

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=45,
                message=f"检索完成，命中 {len(retrieval_response.documents)} 个候选来源",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(
                text=(
                    f"检索查询: {retrieval_response.rewritten_query}；"
                    f"来源摘要: {summary_text}"
                )
            ),
        )

    async def _run_agent_core_loop(
        self,
        *,
        query: str,
        rewritten_query: str,
        keywords: list[str],
        web_search_enabled: bool,
        retrieval_strategy: str,
        graph_intent: str | None,
        params: dict[str, Any],
        system_prompt: str,
    ):
        try:
            # 步骤 1: 获取原始检索结果（1 次数据库查询，带恢复机制）
            raw_result = await self._safe_get_raw_result(
                rewritten_query=rewritten_query,
                keywords=keywords,
                web_search_enabled=web_search_enabled,
                retrieval_strategy=retrieval_strategy,
                graph_intent=graph_intent,
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
                "query": rewritten_query,
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
                        graph_intent=graph_intent,
                    )
            return await asyncio.to_thread(
                self.service.retrieve_raw,
                rewritten_query,
                web_search_enabled=web_search_enabled,
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

    def _web_search_enabled(self, params: dict[str, Any]) -> bool:
        return bool(
            params.get("webSearchEnabled") is True
            or params.get("enableWebSearch") is True
            or params.get("tavilySearchEnabled") is True
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
