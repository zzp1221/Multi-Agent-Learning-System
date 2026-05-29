"""基于 AgentCoreLoop、结构化压缩和持久化记忆的辅导 Agent。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import ConversationSummaryRefinerFactory, TutorLLMClientFactory
from src.ai_modules.memory import MongoConversationSummaryStore
from src.ai_modules.models import (
    DialogState,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_tutor_system_prompt
from src.ai_modules.runtime import (
    AgentCoreLoop,
    ConversationCompactor,
    PermissionLevel,
    RecoveryEngine,
    StructuredConversationSummary,
    SystemSnapshot,
    ToolRegistry,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)

GRAPH_AWARE_INTENTS = {
    "COMMON_MISTAKE",
    "COMMUNITY_SUMMARY",
    "COMPARISON",
    "CROSS_LAYER_RELATION",
    "MECHANISM_APPLICATION",
    "MULTI_HOP_RELATION",
    "PREREQUISITE_PATH",
}

GRAPH_INTENT_GUIDANCE = {
    "PREREQUISITE_PATH": "围绕可能的前置基础、当前概念和后续延伸组织答案；不要把候选排序说成已验证的严格先修顺序。",
    "CROSS_LAYER_RELATION": "围绕跨层相关证据组织答案；只在证据明确时才表达确定的层间因果或调用链。",
    "MECHANISM_APPLICATION": "围绕机制、触发条件、实现位置和应用效果组织答案；不要把候选排序说成真实执行顺序。",
    "COMPARISON": "先给共同点，再给关键差异、适用边界和容易混淆的判断标准。",
    "COMMON_MISTAKE": "先指出常见误解，再用反例或边界条件瓦解，最后给正确心智模型。",
    "COMMUNITY_SUMMARY": "按概念群组总结主题、共同作用和组内差异。",
    "MULTI_HOP_RELATION": "围绕多跳相关证据组织答案，避免只解释孤立概念；不把候选列表当作真实路径。",
}

GRAPH_INTENT_LABELS = {
    "PREREQUISITE_PATH": "学习路径/前置依赖",
    "CROSS_LAYER_RELATION": "跨层关系",
    "MECHANISM_APPLICATION": "机制应用",
    "COMPARISON": "对比辨析",
    "COMMON_MISTAKE": "常见误区",
    "COMMUNITY_SUMMARY": "概念群总结",
    "MULTI_HOP_RELATION": "多跳关系",
}

GRAPH_SOURCE_LABELS = {
    "direct_evidence": "直接证据",
    "seed_protected": "种子概念",
    "graph_1hop": "一跳图谱相关概念",
    "graph_2hop": "二跳图谱补充概念",
    "graph": "图谱相关概念",
}


class TutorAgent(PlaceholderAgent):
    """使用近期对话和检索证据指导学习者。"""

    def __init__(
        self,
        compactor: ConversationCompactor | None = None,
        summary_store: Any | None = None,
        llm_client: Any | None = None,
        llm_fallback_clients: list[Any] | None = None,
        summary_refiner: Any | None = None,
    ) -> None:
        super().__init__("Tutor Agent", "tutoring")
        self.summary_refiner = summary_refiner or ConversationSummaryRefinerFactory.create()
        self.compactor = compactor or ConversationCompactor(summary_refiner=self.summary_refiner)
        if compactor is not None and summary_refiner is not None:
            self.compactor.summary_refiner = summary_refiner
        self.summary_store = summary_store or MongoConversationSummaryStore()
        self.llm_clients = (
            [llm_client]
            if llm_client is not None
            else TutorLLMClientFactory.create_llm_candidates()
        )
        if llm_fallback_clients:
            self.llm_clients.extend(llm_fallback_clients)
        self.llm_client = self.llm_clients[0] if self.llm_clients else None
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="tutor",
            snapshot=snapshot,
            fallback_prompt=build_tutor_system_prompt(snapshot),
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
        del service_type
        conversation = self._extract_conversation(params)
        user_query = self._resolve_user_query(params)
        persisted_summary = await self._load_persisted_summary(
            conversation_id=self._conversation_id(params, task_id),
            user_id=params.get("userId"),
        )
        compaction_result = await self._compact_conversation(
            conversation,
            previous_summary=self._build_previous_summary(persisted_summary),
        )
        params["compactedConversation"] = compaction_result.compacted_messages
        params["structuredConversationSummary"] = compaction_result.structured_summary.model_dump(
            by_alias=True
        )
        params["conversationSummary"] = compaction_result.summary
        recent_dialogue = self._build_recent_dialogue_context(
            conversation=conversation,
            user_query=user_query,
        )
        input_mode = self._classify_input_mode(
            user_query=user_query,
            recent_dialogue=recent_dialogue,
            params=params,
        )
        recent_dialogue["inputMode"] = input_mode
        params["recentDialogueContext"] = recent_dialogue
        params["inputMode"] = input_mode

        if compaction_result.was_compacted:
            await self._upsert_summary(
                params=params,
                task_id=task_id,
                structured_summary=compaction_result.structured_summary.model_dump(by_alias=True),
            )

        strategy = self._select_strategy(snapshot=snapshot, params=params)
        dialog_state = DialogState(
            conversationId=self._conversation_id(params, task_id),
            turnId=f"{task_id}-turn",
            pedagogyStrategy=strategy,
            nextAction=self._resolve_next_action(input_mode),
        )

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=30 if compaction_result.was_compacted else 20,
                message=(
                    "已压缩历史对话，开始流式生成辅导回答"
                    if compaction_result.was_compacted
                    else "开始流式生成辅导回答"
                ),
            ),
            dialogState=dialog_state,
        )

        current_seq = seq + 1
        if not self.llm_clients:
            raise RuntimeError("tutor_llm provider is not ready")

        last_error: Exception | None = None
        for llm_client in self.llm_clients:
            streamed = False
            try:
                async for token in self._try_direct_chat_stream(
                    llm_client=llm_client,
                    system_prompt=system_prompt,
                    params=params,
                    persisted_summary=persisted_summary,
                ):
                    streamed = True
                    yield ResultChunkSSEEvent(
                        taskId=task_id,
                        traceId=trace_id,
                        seq=current_seq,
                        payload=ResultChunkPayload(text=token, stage="tutoring"),
                        dialogState=dialog_state,
                    )
                    current_seq += 1
                if streamed:
                    self._log_llm_success("direct_stream", llm_client)
                    return
                details = self._describe_llm_client(llm_client)
                LOGGER.warning(
                    "Direct tutor stream produced no tokens; trying non-stream LLM "
                    "provider=%s model=%s baseUrl=%s",
                    details["provider"],
                    details["model"],
                    details["baseUrl"],
                )
            except Exception as exc:
                last_error = exc
                self._log_llm_failure("direct_stream", exc, llm_client)
                if streamed:
                    raise

            try:
                response_text = await self._run_agent_core_loop(
                    llm_client=llm_client,
                    system_prompt=system_prompt,
                    params=params,
                    snapshot=snapshot,
                    persisted_summary=persisted_summary,
                )
                yield ResultChunkSSEEvent(
                    taskId=task_id,
                    traceId=trace_id,
                    seq=current_seq,
                    payload=ResultChunkPayload(text=response_text, stage="tutoring"),
                    dialogState=dialog_state,
                )
                self._log_llm_success("agent_core_loop", llm_client)
                return
            except Exception as exc:
                last_error = exc
                self._log_llm_failure("agent_core_loop", exc, llm_client)

        if last_error is not None:
            raise last_error
        raise RuntimeError("tutor_llm provider is not ready")

    def _describe_llm_client(self, llm_client: Any) -> dict[str, str]:
        client = getattr(llm_client, "client", llm_client)
        return {
            "provider": str(getattr(client, "provider_name", type(llm_client).__name__)),
            "model": str(getattr(client, "model_name", "")),
            "baseUrl": str(getattr(client, "base_url", "")),
        }

    def _log_llm_failure(self, stage: str, exc: Exception, llm_client: Any) -> None:
        details = self._describe_llm_client(llm_client)
        LOGGER.warning(
            "Tutor LLM attempt failed stage=%s provider=%s model=%s baseUrl=%s errorType=%s",
            stage,
            details["provider"],
            details["model"],
            details["baseUrl"],
            type(exc).__name__,
            exc_info=True,
        )

    def _log_llm_success(self, stage: str, llm_client: Any) -> None:
        if llm_client is self.llm_client:
            return
        details = self._describe_llm_client(llm_client)
        LOGGER.info(
            "Tutor LLM fallback succeeded stage=%s provider=%s model=%s baseUrl=%s",
            stage,
            details["provider"],
            details["model"],
            details["baseUrl"],
        )

    def _extract_conversation(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = params.get("messages") or params.get("conversation") or []
        if not isinstance(candidates, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                }
            )
        return normalized

    def _select_strategy(self, *, snapshot: SystemSnapshot, params: dict[str, Any]) -> str:
        """选择教学策略，默认采用 Sigma 风格的苏格拉底式提问法。

        - mastery_socratic: 苏格拉底式提问 + 掌握程度评分标准 + 误解追踪
        - retrieval_grounded_scaffold: 基于检索证据的苏格拉底式辅导
        - diagnostic_scaffold: 聚焦薄弱点的诊断式分解
        """
        retrieval_result = params.get("retrievalResult", {})
        documents = retrieval_result.get("documents", [])
        profile = params.get("profile", {})
        has_misconceptions = bool(profile.get("misconceptions") or [])
        if snapshot.knowledge_gaps and documents:
            return "retrieval_grounded_scaffold"
        if snapshot.knowledge_gaps or has_misconceptions:
            return "diagnostic_scaffold"
        return "mastery_socratic"

    async def _run_agent_core_loop(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        del snapshot
        user_query = self._resolve_user_query(params)
        try:
            return await self._try_direct_chat(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_query=user_query,
                params=params,
                persisted_summary=persisted_summary,
            )
        except Exception as exc:
            self._log_llm_failure("direct_chat", exc, llm_client)
        return await self._run_with_agent_core_loop(
            llm_client=llm_client,
            system_prompt=system_prompt,
            user_query=user_query,
            params=params,
            persisted_summary=persisted_summary,
        )

    async def _try_direct_chat(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        client = llm_client.client
        memory_data = self._tool_load_conversation_memory(
            tool_input={}, persisted_summary=persisted_summary,
        )
        context_data = self._tool_read_compacted_context(tool_input={}, params=params)
        evidence_data = self._tool_read_retrieval_evidence(tool_input={}, params=params)
        profile_data = self._tool_read_profile_context(tool_input={}, params=params)
        image_analysis_data = self._tool_read_image_analysis_context(tool_input={}, params=params)
        recent_dialogue_data = self._tool_read_recent_dialogue_context(
            tool_input={}, params=params,
        )
        input_mode = self._resolve_input_mode(params=params, recent_dialogue=recent_dialogue_data)
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=memory_data,
            context=context_data,
            evidence=evidence_data,
            profile=profile_data,
            image_analysis=image_analysis_data,
            recent_dialogue=recent_dialogue_data,
            input_mode=input_mode,
        )
        llm_messages = self._build_llm_messages(
            system_prompt=system_prompt,
            runtime_context=enriched_message,
            recent_dialogue=recent_dialogue_data,
            user_query=user_query,
        )
        response = await client.chat_completion(
            messages=llm_messages,
        )
        message = client.extract_message(response)
        return client.extract_content(message)

    async def _try_direct_chat_stream(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ):
        """从 LLM 流式输出 token 以实现实时辅导展示。"""
        client = llm_client.client
        user_query = self._resolve_user_query(params)
        memory_data = self._tool_load_conversation_memory(
            tool_input={}, persisted_summary=persisted_summary,
        )
        context_data = self._tool_read_compacted_context(tool_input={}, params=params)
        evidence_data = self._tool_read_retrieval_evidence(tool_input={}, params=params)
        profile_data = self._tool_read_profile_context(tool_input={}, params=params)
        image_analysis_data = self._tool_read_image_analysis_context(tool_input={}, params=params)
        recent_dialogue_data = self._tool_read_recent_dialogue_context(
            tool_input={}, params=params,
        )
        input_mode = self._resolve_input_mode(params=params, recent_dialogue=recent_dialogue_data)
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=memory_data,
            context=context_data,
            evidence=evidence_data,
            profile=profile_data,
            image_analysis=image_analysis_data,
            recent_dialogue=recent_dialogue_data,
            input_mode=input_mode,
        )
        llm_messages = self._build_llm_messages(
            system_prompt=system_prompt,
            runtime_context=enriched_message,
            recent_dialogue=recent_dialogue_data,
            user_query=user_query,
        )

        # 每累积 3 个 token 批量输出，使 UI 更新更平滑
        batch: list[str] = []
        async for token in client.chat_completion_stream(
            messages=llm_messages,
        ):
            batch.append(token)
            if len(batch) >= 3:
                yield "".join(batch)
                batch.clear()
        if batch:
            yield "".join(batch)

    def _build_enriched_message(
        self,
        *,
        user_query: str,
        memory: dict[str, Any],
        context: dict[str, Any],
        evidence: dict[str, Any],
        profile: dict[str, Any],
        image_analysis: dict[str, Any],
        recent_dialogue: dict[str, Any],
        input_mode: str,
    ) -> str:
        parts: list[str] = []
        topic_focus = memory.get("topicFocus") or context.get("topicFocus") or []
        learner_goal = memory.get("learnerGoal") or context.get("learnerGoal") or ""
        known_gaps = memory.get("knownGaps") or context.get("knownGaps") or []
        unresolved = memory.get("unresolvedQuestions") or context.get("unresolvedQuestions") or []
        teaching_state = recent_dialogue.get("teachingState", {})
        recent_messages = recent_dialogue.get("recentMessages", [])
        now = datetime.now().astimezone()
        parts.append(
            "Runtime date/time (server local): "
            f"{now.isoformat(timespec='seconds')}; weekday: {now.strftime('%A')}. "
            "Use this for questions about today, current date, current weekday, or current time."
        )
        parts.append(f"当前输入模式：{input_mode}")
        # Sigma: 展示已记录的误解，用于针对性反例设计
        recorded_misconceptions = (
            profile.get("misconceptions")
            or memory.get("misconceptions")
            or []
        )
        mastered_concepts = profile.get("masteredConcepts") or memory.get("masteredConcepts") or []
        if profile:
            if profile.get("studentLevel"):
                parts.append(f"学生水平：{profile['studentLevel']}")
            if profile.get("learningPreference"):
                parts.append(f"讲解偏好：{profile['learningPreference']}")
            if profile.get("cognitiveStyle"):
                parts.append(f"认知风格：{profile['cognitiveStyle']}")
            preferred_resource_types = profile.get("preferredResourceTypes") or []
            if preferred_resource_types:
                parts.append(f"偏好资源类型：{', '.join(preferred_resource_types[:3])}")
        if topic_focus:
            parts.append(f"对话主题：{', '.join(topic_focus) if isinstance(topic_focus, list) else topic_focus}")
        if learner_goal:
            parts.append(f"学习目标：{learner_goal}")
        if known_gaps:
            parts.append(f"已知薄弱点：{', '.join(known_gaps)}")
        if unresolved:
            parts.append(f"未解决问题：{', '.join(unresolved)}")
        if teaching_state:
            last_assistant_question = str(teaching_state.get("lastAssistantQuestion") or "").strip()
            current_user_intent = str(teaching_state.get("currentUserIntent") or "").strip()
            if last_assistant_question:
                parts.append(f"上一轮导师追问：{last_assistant_question}")
            if teaching_state.get("awaitingUserAnswer"):
                parts.append("当前教学状态：导师上一轮刚提出问题，当前更可能在等待学生作答。")
            if current_user_intent == "answer_previous_question":
                parts.append("当前轮意图：用户更像是在回答上一轮问题，不要把它当成新的话题开场。")
        if recent_messages:
            parts.append("最近对话片段（优先用于承接上下文）：")
            for item in recent_messages:
                role = "导师" if item.get("role") == "assistant" else "学生"
                content = str(item.get("content") or "").strip()
                if content:
                    parts.append(f"  - {role}：{content}")
        if recorded_misconceptions:
            parts.append("‼️ 已记录的错误概念（必须用反例瓦解，勿直接纠正）：")
            for mc in recorded_misconceptions[:5]:
                concept = mc.get("concept", "") if isinstance(mc, dict) else ""
                belief = mc.get("wrongBelief", "") if isinstance(mc, dict) else ""
                status = mc.get("status", "") if isinstance(mc, dict) else ""
                if concept and belief:
                    parts.append(f"  - [{concept}] {belief} (状态: {status or 'active'})")
        if mastered_concepts:
            concepts_str = ", ".join(
                mc.get("concept", "") if isinstance(mc, dict) else str(mc)
                for mc in mastered_concepts[:8]
            )
            if concepts_str:
                parts.append(f"已掌握概念（可用于交叉练习混入）：{concepts_str}")
        documents = evidence.get("documents", []) if isinstance(evidence.get("documents"), list) else []
        if documents:
            parts.append("检索到的知识来源：")
            for i, doc in enumerate(documents[:5], 1):
                title = str(doc.get("title") or "")
                snippet = str(doc.get("evidence") or doc.get("snippet") or "")[:200]
                url = str(doc.get("url") or "").strip()
                channel = str(doc.get("channel") or "").strip()
                source_hint = f" [{url}]" if channel == "web" and url else ""
                parts.append(f"  {i}. {title}{source_hint}: {snippet}")
        graph_pack = evidence.get("graphEvidencePack")
        if isinstance(graph_pack, dict) and graph_pack.get("intent"):
            intent = str(graph_pack.get("intent") or "")
            guidance = str(graph_pack.get("guidance") or "")
            parts.append("图谱证据包（用于辅助组织回答，不要复述内部诊断标签）：")
            parts.append(f"  - 图谱题型：{GRAPH_INTENT_LABELS.get(intent, intent)}")
            if guidance:
                parts.append(f"  - 回答组织建议：{guidance}")
            for i, node in enumerate(graph_pack.get("nodes", [])[:6], 1):
                if not isinstance(node, dict):
                    continue
                title = str(node.get("title") or "").strip()
                source_label = self._graph_source_label(str(node.get("source") or ""))
                if title:
                    parts.append(f"  {i}. {title}（{source_label}）")
            relation_hints = graph_pack.get("relationHints", [])
            if relation_hints:
                parts.append("  - 关系/路径线索：")
                for hint in relation_hints[:4]:
                    parts.append(f"    * {hint}")
        image_summary = str(image_analysis.get("summary") or "").strip()
        if image_summary:
            parts.append("图片识别结果：")
            parts.append(image_summary)
        if input_mode == "small_talk":
            parts.append("处理要求：这是寒暄、感谢或结束信号。自然简短回复，不进入教学诊断。")
        elif input_mode == "answer_previous_question":
            parts.append("处理要求：用户正在回答上一轮问题。先承接这句回答，指出其中合理部分，再继续推进，不要重新开题。")
        elif input_mode == "clear_question":
            parts.append("处理要求：用户提出了明确问题。先给一个简洁直接的回答，再按需要补充一个追问或例子。")
        else:
            parts.append("处理要求：只有在当前信息不足以作答时，才做一次简短澄清；禁止机械地追问“这是什么意思/什么场景”。")
        return "\n\n".join(parts)

    async def _run_with_agent_core_loop(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        tool_registry = ToolRegistry()
        tool_registry.register(
            name="load_conversation_memory",
            fn=lambda tool_input: self._tool_load_conversation_memory(
                tool_input=tool_input,
                persisted_summary=persisted_summary,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="从持久化记忆中加载最新的结构化对话摘要。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_compacted_context",
            fn=lambda tool_input: self._tool_read_compacted_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取最新的结构化压缩对话上下文。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_retrieval_evidence",
            fn=lambda tool_input: self._tool_read_retrieval_evidence(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取支持辅导回答的检索证据。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_recent_dialogue_context",
            fn=lambda tool_input: self._tool_read_recent_dialogue_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取近期对话轮次和教学状态，用于多轮连续性。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_image_analysis_context",
            fn=lambda tool_input: self._tool_read_image_analysis_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取从上传题目图片中提取的多模态图片分析结果。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        core_loop = AgentCoreLoop(
            llm_client=llm_client,
            tool_registry=tool_registry,
            recovery_engine=RecoveryEngine(),
            max_iterations=4,
            agent_level=PermissionLevel.READ_ONLY,
        )
        result = await core_loop.run(
            system_prompt=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": self._build_agent_core_request(
                        user_query=user_query,
                        params=params,
                        persisted_summary=persisted_summary,
                    ),
                }
            ],
        )
        return result.final_text

    async def _load_persisted_summary(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        try:
            document = await self.summary_store.get_latest_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception:
            return None
        if document is None:
            return None
        return document.model_dump(by_alias=True)

    async def _compact_conversation(
        self,
        conversation: list[dict[str, Any]],
        *,
        previous_summary: StructuredConversationSummary | None,
    ):
        compact_async = getattr(self.compactor, "compact_async", None)
        if callable(compact_async):
            return await compact_async(conversation, previous_summary=previous_summary)
        return self.compactor.compact(conversation, previous_summary=previous_summary)

    def _build_previous_summary(
        self,
        persisted_summary: dict[str, Any] | None,
    ) -> StructuredConversationSummary | None:
        if not persisted_summary:
            return None
        try:
            return StructuredConversationSummary.model_validate(persisted_summary)
        except Exception:
            return None

    async def _upsert_summary(
        self,
        *,
        params: dict[str, Any],
        task_id: str,
        structured_summary: dict[str, Any],
    ) -> None:
        from src.ai_modules.memory import ConversationSummaryDocument

        document = ConversationSummaryDocument(
            conversationId=self._conversation_id(params, task_id),
            userId=params.get("userId"),
            taskId=task_id,
            topicFocus=structured_summary.get("topicFocus", []),
            canonicalTopicKeys=structured_summary.get("canonicalTopicKeys", []),
            aliases=structured_summary.get("aliases", {}),
            learnerGoal=structured_summary.get("learnerGoal"),
            knownGaps=structured_summary.get("knownGaps", []),
            unresolvedQuestions=structured_summary.get("unresolvedQuestions", []),
            preferredHelpStyle=structured_summary.get("preferredHelpStyle"),
            lastUserMessage=structured_summary.get("lastUserMessage"),
            recentProgress=structured_summary.get("recentProgress", []),
            confidence=float(structured_summary.get("confidence", 0.55) or 0.55),
            summaryText=structured_summary.get("summaryText", ""),
        )
        try:
            upsert = getattr(self.summary_store, "upsert_summary", None)
            if callable(upsert):
                await upsert(document)
                return
            await self.summary_store.save_summary(document)
        except Exception:
            return

    def _conversation_id(self, params: dict[str, Any], task_id: str) -> str:
        return str(params.get("conversationId") or task_id)

    def _tool_load_conversation_memory(
        self,
        *,
        tool_input: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del tool_input
        return persisted_summary or {}

    def _tool_read_compacted_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        summary = params.get("structuredConversationSummary", {})
        return {
            **summary,
            "recentMessages": params.get("compactedConversation", [])[-2:],
        }

    def _tool_read_retrieval_evidence(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        retrieval_result = params.get("retrievalResult", {})
        if not isinstance(retrieval_result, dict):
            retrieval_result = {}
        return {
            "query": params.get("query"),
            "rewrittenQuery": params.get("rewrittenQuery"),
            "documents": retrieval_result.get("documents", []),
            "sourcesSummary": retrieval_result.get("sourcesSummary", ""),
            "graphEvidencePack": self._build_graph_evidence_pack(
                params=params,
                documents=retrieval_result.get("documents", []),
            ),
        }

    def _build_graph_evidence_pack(
        self,
        *,
        params: dict[str, Any],
        documents: Any = None,
    ) -> dict[str, Any]:
        intent = self._resolve_graph_intent_from_params(params)
        if intent not in GRAPH_AWARE_INTENTS:
            return {}

        raw_result = params.get("retrievalRawResult", {})
        raw_result = raw_result if isinstance(raw_result, dict) else {}
        graph_result = params.get("graphRetrievalResult", {})
        graph_result = graph_result if isinstance(graph_result, dict) else {}

        nodes = self._collect_graph_evidence_nodes(
            raw_result=raw_result,
            graph_result=graph_result,
            documents=documents if isinstance(documents, list) else [],
        )
        if not nodes:
            return {}
        return {
            "intent": intent,
            "guidance": GRAPH_INTENT_GUIDANCE.get(intent, "优先按图谱关系组织回答。"),
            "nodes": nodes[:8],
            "relationHints": self._build_graph_relation_hints(
                intent=intent,
                nodes=nodes,
                raw_result=raw_result,
            ),
        }

    def _resolve_graph_intent_from_params(self, params: dict[str, Any]) -> str:
        for value in (
            params.get("graphIntent"),
            params.get("retrievalRawResult", {}).get("graphIntent")
            if isinstance(params.get("retrievalRawResult"), dict)
            else None,
            params.get("graphRetrievalResult", {}).get("graphIntent")
            if isinstance(params.get("graphRetrievalResult"), dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        classification = params.get("queryClassification")
        if isinstance(classification, dict):
            value = classification.get("graphIntent")
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        return ""

    def _collect_graph_evidence_nodes(
        self,
        *,
        raw_result: dict[str, Any],
        graph_result: dict[str, Any],
        documents: list[Any],
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[Any, str]] = []
        candidates.extend((item, "graph") for item in self._graph_result_items(graph_result))
        candidates.extend(
            (item, "graph")
            for item in documents
            if isinstance(item, dict) and str(item.get("channel") or "").strip().lower() == "graph"
        )

        diagnostics = raw_result.get("graphDiagnostics", {})
        if isinstance(diagnostics, dict):
            prerequisite = diagnostics.get("prerequisiteEvidence", {})
            if isinstance(prerequisite, dict):
                candidates.extend(
                    (item, "direct_evidence")
                    for item in prerequisite.get("directEvidenceCandidatesTopN", [])
                )
                candidates.extend(
                    (item, "seed_protected")
                    for item in prerequisite.get("protectedSeeds", [])
                )

        channels = raw_result.get("channels", {})
        if isinstance(channels, dict):
            candidates.extend((item, "graph") for item in channels.get("graph", []))

        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, (item, fallback_source) in enumerate(candidates, start=1):
            node = self._graph_node_from_item(item, fallback_source=fallback_source, fallback_rank=index)
            if not node:
                continue
            dedupe_keys = [
                str(value)
                for value in (node.get("slug"), node.get("title"))
                if str(value or "").strip()
            ]
            if not dedupe_keys or any(key in seen for key in dedupe_keys):
                continue
            seen.update(dedupe_keys)
            nodes.append(node)
        return nodes

    def _graph_result_items(self, graph_result: dict[str, Any]) -> list[Any]:
        results = graph_result.get("results", [])
        return results if isinstance(results, list) else []

    def _graph_node_from_item(
        self,
        item: Any,
        *,
        fallback_source: str,
        fallback_rank: int,
    ) -> dict[str, Any]:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            slug = str(item.get("slug") or "").strip()
            source = str(item.get("source") or fallback_source).strip()
            score = item.get("score")
            rank = item.get("rank") or fallback_rank
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            slug = str(item[0] or "").strip()
            title = str(item[1] or "").strip()
            source = str(item[3] if len(item) > 3 else fallback_source).strip()
            score = item[2] if len(item) > 2 else None
            rank = fallback_rank
        else:
            return {}

        if not title and not slug:
            return {}
        return {
            "rank": rank,
            "slug": slug,
            "title": title or slug,
            "source": source or fallback_source,
            "hop": self._graph_hop_from_source(source or fallback_source),
            "score": self._safe_float(score),
        }

    def _graph_source_label(self, source: str) -> str:
        normalized = str(source or "").strip().lower()
        return GRAPH_SOURCE_LABELS.get(normalized, "图谱相关概念")

    def _graph_hop_from_source(self, source: str) -> int | None:
        normalized = str(source or "").strip().lower()
        if "2hop" in normalized or "2_hop" in normalized:
            return 2
        if "1hop" in normalized or "1_hop" in normalized:
            return 1
        return None

    def _safe_float(self, value: Any) -> float | None:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    def _build_graph_relation_hints(
        self,
        *,
        intent: str,
        nodes: list[dict[str, Any]],
        raw_result: dict[str, Any],
    ) -> list[str]:
        titles = [str(node.get("title") or "").strip() for node in nodes if node.get("title")]
        hints: list[str] = []
        if len(titles) >= 2:
            joined_titles = "、".join(titles[:5])
            if intent == "PREREQUISITE_PATH":
                hints.append(f"学习路径相关候选集合（非严格顺序）：{joined_titles}")
            elif intent in {"CROSS_LAYER_RELATION", "MULTI_HOP_RELATION"}:
                hints.append(f"关系链相关候选集合（非严格顺序）：{joined_titles}")
            elif intent == "MECHANISM_APPLICATION":
                hints.append(f"机制应用相关候选集合（非严格顺序）：{'、'.join(titles[:4])}")
            elif intent == "COMPARISON":
                hints.append("对比对象候选：" + " / ".join(titles[:4]))
            elif intent == "COMMON_MISTAKE":
                hints.append("易错点证据候选：" + " / ".join(titles[:4]))
            elif intent == "COMMUNITY_SUMMARY":
                hints.append("同一概念群候选：" + " / ".join(titles[:5]))

        diagnostics = raw_result.get("graphDiagnostics", {})
        if isinstance(diagnostics, dict):
            top5 = diagnostics.get("top5Stabilization", {})
            if isinstance(top5, dict):
                protected = top5.get("seedProtectedTop5", [])
                if protected:
                    hints.append("Top5 中保护的种子节点：" + ", ".join(map(str, protected[:4])))
        return hints

    def _tool_read_profile_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        profile = params.get("profile", {})
        if not isinstance(profile, dict):
            return {}
        return {
            "studentLevel": profile.get("studentLevel") or profile.get("knowledgeFoundation"),
            "learningPreference": profile.get("learningPreference") or profile.get("preferredStyle"),
            "cognitiveStyle": profile.get("cognitiveStyle"),
            "preferredResourceTypes": profile.get("preferredResourceTypes", []),
        }

    def _tool_read_recent_dialogue_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        recent_dialogue = params.get("recentDialogueContext", {})
        return recent_dialogue if isinstance(recent_dialogue, dict) else {}

    def _tool_read_image_analysis_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        image_analysis = params.get("imageAnalysisResult", {})
        return image_analysis if isinstance(image_analysis, dict) else {}

    def _resolve_user_query(self, params: dict[str, Any]) -> str:
        return str(
            params.get("query")
            or params.get("message")
            or params.get("rewrittenQuery")
            or params.get("structuredConversationSummary", {}).get("lastUserMessage")
            or "当前主题"
        )

    def _resolve_input_mode(
        self,
        *,
        params: dict[str, Any],
        recent_dialogue: dict[str, Any],
    ) -> str:
        mode = params.get("inputMode")
        if isinstance(mode, str) and mode.strip():
            return mode
        return str(recent_dialogue.get("inputMode") or "clear_question")

    def _build_recent_dialogue_context(
        self,
        *,
        conversation: list[dict[str, Any]],
        user_query: str,
    ) -> dict[str, Any]:
        recent_messages = self._select_recent_turns(conversation=conversation, user_query=user_query)
        teaching_state = self._infer_teaching_state(
            recent_messages=recent_messages,
            user_query=user_query,
        )
        return {
            "recentMessages": recent_messages,
            "teachingState": teaching_state,
        }

    def _classify_input_mode(
        self,
        *,
        user_query: str,
        recent_dialogue: dict[str, Any],
        params: dict[str, Any],
    ) -> str:
        query_type = str(params.get("queryType") or "").strip().upper()
        if query_type == "SMALL_TALK":
            return "small_talk"
        if query_type == "ANSWER_PREVIOUS":
            return "answer_previous_question"
        if query_type == "FOLLOW_UP":
            return "ambiguous_topic"
        normalized = "".join(str(user_query).lower().split())
        if not normalized:
            return "small_talk"
        teaching_state = recent_dialogue.get("teachingState", {})
        if teaching_state.get("awaitingUserAnswer"):
            return "answer_previous_question"
        if self._looks_like_question(user_query):
            return "clear_question"
        if len(normalized) <= 12:
            return "ambiguous_topic"
        return "clear_question"

    def _select_recent_turns(
        self,
        *,
        conversation: list[dict[str, Any]],
        user_query: str,
    ) -> list[dict[str, str]]:
        normalized_query = "".join(str(user_query).split())
        trimmed = list(conversation)
        if trimmed:
            last_item = trimmed[-1]
            last_content = "".join(str(last_item.get("content") or "").split())
            if last_item.get("role") == "user" and last_content == normalized_query:
                trimmed = trimmed[:-1]
        recent_turns = trimmed[-4:]
        selected: list[dict[str, str]] = []
        for item in recent_turns:
            role = str(item.get("role") or "user")
            content = self._truncate_dialogue_text(str(item.get("content") or ""))
            if role not in {"user", "assistant"} or not content:
                continue
            selected.append({"role": role, "content": content})
        return selected

    def _infer_teaching_state(
        self,
        *,
        recent_messages: list[dict[str, str]],
        user_query: str,
    ) -> dict[str, Any]:
        last_assistant_question = ""
        for item in reversed(recent_messages):
            if item.get("role") == "assistant":
                content = str(item.get("content") or "").strip()
                if self._looks_like_question(content):
                    last_assistant_question = content
                    break
        normalized_query = str(user_query).strip()
        likely_answer = bool(normalized_query) and not self._looks_like_question(normalized_query)
        awaiting_user_answer = bool(last_assistant_question) and likely_answer
        return {
            "lastAssistantQuestion": last_assistant_question,
            "awaitingUserAnswer": awaiting_user_answer,
            "currentUserIntent": "answer_previous_question" if awaiting_user_answer else "ask_or_shift_topic",
        }

    def _build_llm_messages(
        self,
        *,
        system_prompt: str,
        runtime_context: str,
        recent_dialogue: dict[str, Any],
        user_query: str,
    ) -> list[dict[str, str]]:
        system_content = system_prompt
        if runtime_context.strip():
            system_content = f"{system_prompt}\n\n# 运行时上下文\n{runtime_context}"
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        for item in recent_dialogue.get("recentMessages", []):
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_query})
        return messages

    def _build_agent_core_request(
        self,
        *,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=self._tool_load_conversation_memory(
                tool_input={},
                persisted_summary=persisted_summary,
            ),
            context=self._tool_read_compacted_context(tool_input={}, params=params),
            evidence=self._tool_read_retrieval_evidence(tool_input={}, params=params),
            profile=self._tool_read_profile_context(tool_input={}, params=params),
            image_analysis=self._tool_read_image_analysis_context(tool_input={}, params=params),
            recent_dialogue=self._tool_read_recent_dialogue_context(tool_input={}, params=params),
            input_mode=self._resolve_input_mode(
                params=params,
                recent_dialogue=self._tool_read_recent_dialogue_context(tool_input={}, params=params),
            ),
        )
        return (
            "请基于以下结构化上下文给出自然、贴合输入类型的回答。"
            "如果用户是在回答上一轮问题，要先承接；如果是问候或感谢，就自然回复；"
            "只有真的不清楚时才澄清。除非适合继续教学，否则不要强行追问。\n\n"
            f"{enriched_message}"
        )

    def _build_short_circuit_reply(
        self,
        *,
        user_query: str,
        input_mode: str,
        recent_dialogue: dict[str, Any],
    ) -> str:
        if input_mode != "small_talk":
            return ""
        if recent_dialogue.get("recentMessages"):
            return "我在，继续说就行。也可以直接接着上一轮的问题回答。"
        return "我在。你可以直接发问题、概念或题目，我来一起拆解。"

    def _resolve_next_action(self, input_mode: str) -> str:
        if input_mode == "small_talk":
            return "wait_user"
        if input_mode == "answer_previous_question":
            return "continue_guidance"
        return "ask_follow_up"

    def _looks_like_question(self, text: str) -> bool:
        normalized = str(text).strip()
        if not normalized:
            return False
        if "？" in normalized or "?" in normalized:
            return True
        question_markers = ("什么", "怎么", "如何", "为什么", "哪些", "哪个", "能否", "可否", "吗")
        return any(marker in normalized for marker in question_markers)

    def _truncate_dialogue_text(self, text: str, max_length: int = 220) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1].rstrip() + "…"
