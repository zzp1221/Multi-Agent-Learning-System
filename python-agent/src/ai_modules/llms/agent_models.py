"""支持多提供商的结构化辅助工具和模型选择工厂。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.config import get_settings
from src.ai_modules.llms.openai_compatible import OpenAICompatibleClient
from src.ai_modules.llms.spark_compatible import (
    SparkCompatibleClient,
    SparkCompatibleToolCallingLLM,
)
from src.ai_modules.llms.practice_llm import RuleBasedJudgeLLM, RuleBasedPracticeLLM
from src.ai_modules.llms.profile_llm import RuleBasedProfileLLM
from src.ai_modules.llms.tutor_llm import OpenAICompatibleTutorLLM, RuleBasedTutorLLM
from src.ai_modules.models import (
    EvaluationPayload,
    JudgeItemResult,
    LearningPlanPayload,
    LearnerProfileDimensions,
    PracticeQuestion,
    QueryRewriteResult,
    QuestionBatchPayload,
    RetrievalResponse,
)
from src.ai_modules.runtime.ttl_cache import InMemoryTTLCache, stable_cache_key


_LLM_RESULT_CACHE = InMemoryTTLCache()


def _configure_llm_result_cache():
    settings = get_settings()
    _LLM_RESULT_CACHE.max_entries = max(1, settings.runtime_cache_max_entries)
    _LLM_RESULT_CACHE.configure(
        adaptive_enabled=settings.cache_adaptive_enabled,
        adaptive_window_size=settings.cache_adaptive_window_size,
        adaptive_min_samples=settings.cache_adaptive_min_samples,
        adaptive_min_hit_rate=settings.cache_adaptive_min_hit_rate,
        adaptive_bypass_seconds=settings.cache_adaptive_bypass_seconds,
        adaptive_probe_interval=settings.cache_adaptive_probe_interval,
        max_value_bytes=settings.cache_max_value_bytes,
    )
    return settings


def _cache_ttl_seconds() -> int:
    settings = _configure_llm_result_cache()
    if not settings.enable_llm_result_cache:
        return 0
    return max(0, settings.llm_result_cache_ttl_seconds)


def _should_use_llm_cache(namespace: str) -> bool:
    if _cache_ttl_seconds() <= 0:
        return False
    return _LLM_RESULT_CACHE.should_read(namespace)


def _provider_name() -> str:
    return get_settings().runtime_provider_name()


def _primary_model_name() -> str:
    return get_settings().resolve_logical_model("main_chat_model")


def _fast_model_name() -> str:
    return get_settings().resolve_logical_model("fast_model")


def _has_provider_api_key() -> bool:
    return get_settings().provider_ready()


def _resolve_component_binding(
    component_name: str,
    *,
    default_logical_model: str,
) -> tuple[str, str]:
    settings = get_settings()
    provider_name = settings.resolve_component_provider(component_name)
    model_name = settings.resolve_component_model(
        component_name,
        default_logical_model=default_logical_model,
        provider_name=provider_name,
    )
    return provider_name, model_name


def _component_provider_ready(component_name: str) -> bool:
    settings = get_settings()
    return settings.provider_ready(settings.resolve_component_provider(component_name))


def _require_component_provider_ready(component_name: str) -> tuple[str, str]:
    if not _component_provider_ready(component_name):
        raise RuntimeError(f"{component_name} provider is not ready")
    return _resolve_component_binding(component_name, default_logical_model="main_chat_model")


def create_compatible_client(
    *,
    model_name: str | None = None,
    provider_name: str | None = None,
) -> Any:
    """构建当前配置的 OpenAI 兼容客户端。"""

    resolved_provider = (provider_name or _provider_name()).strip().lower()
    if resolved_provider == "spark":
        return SparkCompatibleClient(model_name=model_name or _primary_model_name())
    return OpenAICompatibleClient(model_name=model_name or _primary_model_name())


def create_tool_calling_llm(
    *,
    model_name: str | None = None,
    provider_name: str | None = None,
) -> Any:
    """构建当前配置的工具调用 LLM 适配器。"""

    resolved_provider = (provider_name or _provider_name()).strip().lower()
    if resolved_provider == "spark":
        return SparkCompatibleToolCallingLLM(model_name=model_name or _primary_model_name())
    return OpenAICompatibleTutorLLM(model_name=model_name or _primary_model_name())


class OpenAICompatibleJSONGenerator:
    """使用活跃的 OpenAI 兼容提供商生成结构化 JSON。"""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        temperature: float = 0.2,
        cache_namespace: str | None = None,
    ) -> None:
        self.client = create_compatible_client(model_name=model_name, provider_name=provider_name)
        self.temperature = temperature
        self.cache_namespace = cache_namespace

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        cache_key = ""
        if self.cache_namespace and _should_use_llm_cache(self.cache_namespace):
            cache_key = self._build_cache_key(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model_name=model_name,
                max_tokens=max_tokens,
            )
        if cache_key:
            cached_payload = _LLM_RESULT_CACHE.get(
                cache_key,
                namespace=self.cache_namespace,
            )
            if isinstance(cached_payload, dict):
                return cached_payload

        response = await self.client.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model_name=model_name,
            temperature=self.temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        message = self.client.extract_message(response)
        content = self.client.extract_content(message).strip()
        if not content:
            raise ValueError("empty assistant content for structured json output")
        payload = self.client.parse_json_text(content)
        if cache_key:
            _LLM_RESULT_CACHE.set(
                cache_key,
                payload,
                ttl_seconds=_cache_ttl_seconds(),
                namespace=self.cache_namespace,
            )
        return payload

    def _build_cache_key(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str | None,
        max_tokens: int | None,
    ) -> str:
        if not self.cache_namespace:
            return ""
        return stable_cache_key(
            f"llm-json:{self.cache_namespace}",
            {
                "provider": self.client.provider_name,
                "model": model_name or self.client.model_name,
                "temperature": self.temperature,
                "responseFormat": {"type": "json_object"},
                "maxTokens": max_tokens,
                "systemPrompt": system_prompt,
                "userPrompt": user_prompt,
            },
        )


class OpenAICompatibleQueryRewriteGenerator:
    """使用轻量级 OpenAI 兼容模型改写检索查询。"""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding("query_rewrite_llm", default_logical_model="fast_model")
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
            cache_namespace="query_rewrite",
        )

    async def rewrite(
        self,
        *,
        system_prompt: str,
        original_query: str,
        learning_context: dict[str, Any],
    ) -> QueryRewriteResult:
        payload = await self.generator.generate(
            system_prompt=system_prompt,
            user_prompt="\n".join(
                [
                    f"原始问题: {original_query}",
                    f"学习上下文: {json.dumps(learning_context, ensure_ascii=False)}",
                    "请返回 JSON。",
                ]
            ),
            max_tokens=400,
        )
        return QueryRewriteResult.model_validate(payload)


class OpenAICompatibleRetrievalSummaryGenerator:
    """使用轻量级 OpenAI 兼容模型总结检索证据。"""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding("retrieval_llm", default_logical_model="fast_model")
        self.client = create_compatible_client(model_name=model_name, provider_name=provider_name)

    async def summarize(
        self,
        *,
        system_prompt: str,
        retrieval_response: RetrievalResponse,
    ) -> str:
        cache_namespace = "retrieval_summary"
        cache_key = ""
        if _should_use_llm_cache(cache_namespace):
            cache_key = self._build_cache_key(
                system_prompt=system_prompt,
                retrieval_response=retrieval_response,
            )
        if cache_key:
            cached_summary = _LLM_RESULT_CACHE.get(
                cache_key,
                namespace=cache_namespace,
            )
            if isinstance(cached_summary, str):
                return cached_summary

        response = await self.client.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "\n".join(
                        [
                            f"查询: {retrieval_response.rewritten_query}",
                            f"候选文档: {json.dumps(retrieval_response.model_dump(by_alias=True), ensure_ascii=False)}",
                            "请输出 1-2 句中文摘要，不要返回 JSON。",
                        ]
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=220,
        )
        summary = self.client.extract_content(self.client.extract_message(response)).strip()
        if cache_key and summary:
            _LLM_RESULT_CACHE.set(
                cache_key,
                summary,
                ttl_seconds=_cache_ttl_seconds(),
                namespace=cache_namespace,
            )
        return summary

    def _build_cache_key(
        self,
        *,
        system_prompt: str,
        retrieval_response: RetrievalResponse,
    ) -> str:
        return stable_cache_key(
            "llm-summary:retrieval",
            {
                "provider": self.client.provider_name,
                "model": self.client.model_name,
                "temperature": 0.2,
                "maxTokens": 220,
                "systemPrompt": system_prompt,
                "retrievalResponse": retrieval_response,
            },
        )


class OpenAICompatibleConversationSummaryRefiner:
    """LLM-assisted structured extraction for long or ambiguous dialogue memory."""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding(
            "conversation_summary_llm",
            default_logical_model="fast_model",
        )
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
            cache_namespace="conversation_summary_refiner",
        )

    async def refine(
        self,
        *,
        messages: list[dict[str, Any]],
        rule_summary: dict[str, Any],
    ) -> dict[str, Any]:
        user_messages = [
            str(message.get("content", "")).strip()
            for message in messages
            if str(message.get("role", "")).lower() == "user"
        ][-12:]
        payload = await self.generator.generate(
            system_prompt=(
                "你是学习对话记忆压缩器。请从学生原话中提取结构化学习记忆，"
                "尤其识别非标准表达和指代，不要编造未出现的薄弱点。"
                "只返回 JSON，字段为 "
                '{"topicFocus":["..."],"canonicalTopicKeys":["..."],"aliases":{"canonical.key":["原始表达"]},'
                '"learnerGoal":"...","knownGaps":["..."],"unresolvedQuestions":["..."],'
                '"preferredHelpStyle":"step_by_step|example_first|concept_then_question|visual_first",'
                '"confidence":0.0,"summaryText":"..."}。'
            ),
            user_prompt=json.dumps(
                {
                    "ruleSummary": rule_summary,
                    "recentUserMessages": user_messages,
                },
                ensure_ascii=False,
            ),
            max_tokens=700,
        )
        return payload


class OpenAICompatibleEvaluationGenerator:
    """使用主提供商模型生成结构化学习者评估。"""

    def __init__(self) -> None:
        if not _component_provider_ready("evaluation_llm"):
            raise RuntimeError("evaluation_llm provider is not ready")
        provider_name, model_name = _resolve_component_binding("evaluation_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(model_name=model_name, provider_name=provider_name)

    async def evaluate(
        self,
        *,
        system_prompt: str,
        context_payload: dict[str, Any],
    ) -> EvaluationPayload:
        max_tokens = self._resolve_max_tokens(context_payload)
        payload = await self.generator.generate(
            system_prompt=system_prompt,
            user_prompt=(
                "请结合以下上下文评估学生当前水平，并只返回 JSON。\n"
                f"{json.dumps(context_payload, ensure_ascii=False)}"
            ),
            max_tokens=max_tokens,
        )
        return EvaluationPayload.model_validate(_normalize_evaluation_payload(payload))

    def _resolve_max_tokens(self, context_payload: dict[str, Any]) -> int:
        dimensions = context_payload.get("assessmentDimensions")
        primary_dimension = ""
        if isinstance(dimensions, list):
            for item in dimensions:
                text = str(item).strip()
                if text:
                    primary_dimension = text
                    break
        if primary_dimension in {"学习主动性", "复盘闭环"}:
            return 2200
        return 1200


class OpenAICompatibleLearningPathGenerator:
    """使用主提供商模型生成结构化学习路径。"""

    def __init__(self) -> None:
        if not _component_provider_ready("path_planning_llm"):
            raise RuntimeError("path_planning_llm provider is not ready")
        provider_name, model_name = _resolve_component_binding("path_planning_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(model_name=model_name, provider_name=provider_name)

    async def plan(
        self,
        *,
        system_prompt: str,
        context_payload: dict[str, Any],
    ) -> LearningPlanPayload:
        payload = await self.generator.generate(
            system_prompt=system_prompt,
            user_prompt=(
                "请根据以下上下文制定学习路径，并只返回 JSON。\n"
                f"{json.dumps(context_payload, ensure_ascii=False)}"
            ),
            max_tokens=1400,
        )
        return LearningPlanPayload.model_validate(self._normalize_learning_plan_payload(payload))

    def _normalize_learning_plan_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_payload = payload.get("learningPath") if isinstance(payload.get("learningPath"), dict) else payload
        if not isinstance(raw_payload, dict):
            return payload

        raw_steps = raw_payload.get("steps")
        normalized_steps = self._normalize_learning_plan_steps(raw_steps)
        if not normalized_steps:
            normalized_steps = self._normalize_learning_plan_steps(raw_payload.get("phases"))
        if not normalized_steps:
            normalized_steps = self._normalize_learning_plan_steps(raw_payload.get("milestones"))

        milestones_value = raw_payload.get("milestones")
        milestones: list[str] = []
        if isinstance(milestones_value, list):
            for item in milestones_value:
                if isinstance(item, dict):
                    text = item.get("title") or item.get("milestone") or item.get("objective")
                    if isinstance(text, str) and text.strip():
                        milestones.append(text.strip())
                elif str(item).strip():
                    milestones.append(str(item).strip())
        else:
            single_milestone = raw_payload.get("milestone")
            if isinstance(single_milestone, str) and single_milestone.strip():
                milestones = [single_milestone.strip()]
        if not milestones and normalized_steps:
            milestones = [step["title"] for step in normalized_steps if step.get("title")]

        duration = raw_payload.get("duration")
        if not isinstance(duration, str) or not duration.strip():
            duration = raw_payload.get("targetPeriod") or raw_payload.get("period")
        if not isinstance(duration, str) or not duration.strip():
            day_value = raw_payload.get("day") or raw_payload.get("days") or raw_payload.get("totalDays")
            if day_value is not None:
                duration = f"{day_value}天"

        summary_text = raw_payload.get("summaryText")
        if not isinstance(summary_text, str) or not summary_text.strip():
            summary_text = raw_payload.get("summary") or raw_payload.get("overview") or raw_payload.get("milestone") or ""
        if isinstance(summary_text, str) and not summary_text.strip():
            goal = str(raw_payload.get("goal") or raw_payload.get("target") or "").strip()
            duration_text = str(duration or "").strip()
            if goal and duration_text:
                summary_text = f"已生成一个 {duration_text} 的学习路径，围绕“{goal}”推进。"
            elif goal:
                summary_text = f"已生成围绕“{goal}”的学习路径。"

        return {
            "goal": raw_payload.get("goal") or raw_payload.get("target") or "",
            "duration": duration or "",
            "milestones": milestones,
            "steps": normalized_steps,
            "summaryText": summary_text,
        }

    def _normalize_learning_plan_steps(self, raw_steps: Any) -> list[dict[str, Any]]:
        normalized_steps: list[dict[str, Any]] = []
        if not isinstance(raw_steps, list):
            return normalized_steps
        for index, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("milestone") or f"阶段 {index}").strip()
            objective = str(
                item.get("objective")
                or item.get("description")
                or item.get("milestone")
                or title
            ).strip()
            activities_value = item.get("activities") or item.get("tasks")
            activities: list[str] = []
            if isinstance(activities_value, list):
                for entry in activities_value:
                    if isinstance(entry, dict):
                        text = entry.get("description") or entry.get("title") or entry.get("name")
                        if isinstance(text, str) and text.strip():
                            activities.append(text.strip())
                    elif str(entry).strip():
                        activities.append(str(entry).strip())
            elif isinstance(item.get("resources"), list):
                for entry in item["resources"]:
                    if isinstance(entry, dict):
                        text = entry.get("description") or entry.get("title") or entry.get("name")
                        if isinstance(text, str) and text.strip():
                            activities.append(text.strip())
                    elif str(entry).strip():
                        activities.append(str(entry).strip())
            success_criteria = str(
                item.get("successCriteria")
                or item.get("success_criteria")
                or item.get("assessment")
                or item.get("expectedOutcome")
                or item.get("completionCriteria")
                or objective
            ).strip()
            normalized_steps.append(
                {
                    "title": title,
                    "objective": objective,
                    "activities": activities or [objective],
                    "successCriteria": success_criteria,
                }
            )
        return normalized_steps


class OpenAICompatiblePracticeQuestionGenerator:
    """使用主提供商模型生成结构化练习批次。"""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding("practice_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(model_name=model_name, provider_name=provider_name)

    async def generate_batch(
        self,
        *,
        topic: str,
        difficulty: str,
        count: int,
        learning_context: dict[str, Any],
    ) -> QuestionBatchPayload:
        payload = await self.generator.generate(
            system_prompt=(
                "你是教学系统中的 Practice Agent。"
                "请围绕指定主题生成高质量中文练习题。"
                "输出必须是 JSON，结构为 "
                '{"title":"...","topic":"...","difficulty":"...","questions":'
                '[{"questionId":"q1","questionType":"SINGLE_CHOICE或SHORT_ANSWER","stem":"...",'
                '"options":["..."],"answer":"...","knowledgeTags":["..."],'
                '"difficultyLevel":"...","explanation":"..."}]}.'
            ),
            user_prompt="\n".join(
                [
                    f"主题: {topic}",
                    f"难度: {difficulty}",
                    f"题量: {count}",
                    f"学习上下文: {json.dumps(learning_context, ensure_ascii=False)}",
                    "要求同时覆盖概念、条件判断、易错点和自测/迁移。",
                ]
            ),
            max_tokens=1800,
        )
        batch = QuestionBatchPayload.model_validate(payload)
        normalized_questions = []
        for index, question in enumerate(batch.questions[:count], start=1):
            normalized_question = question.model_copy(update={"question_id": f"q{index}"})
            normalized_questions.append(normalized_question)
        return batch.model_copy(update={"questions": normalized_questions})


class OpenAICompatibleObjectiveJudgeGenerator:
    """使用活跃提供商判分客观题并返回结构化结果。"""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding("judge_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
        )

    async def judge(
        self,
        *,
        questions: list[PracticeQuestion],
        answers: dict[str, str],
    ) -> dict[str, Any]:
        payload = await self.generator.generate(
            system_prompt=(
                "你是教学系统中的 Judge Agent。"
                "请对客观题判题，并只返回 JSON。"
                '结构为 {"items":[{"questionId":"...","questionType":"...","learnerAnswer":"...",'
                '"correctAnswer":"...","isCorrect":true,"score":20.0,"knowledgeTags":["..."],'
                '"reason":"...","feedback":"...","profileDelta":{"confidenceLevel":"LOW或MEDIUM","weakPoints":["..."]}}],'
                '"pendingSubjective":[{PracticeQuestion原样对象}]}.'
                "SHORT_ANSWER 题不要判分，原样放入 pendingSubjective。"
            ),
            user_prompt=json.dumps(
                {
                    "questions": [question.model_dump(by_alias=True) for question in questions],
                    "answers": answers,
                },
                ensure_ascii=False,
            ),
            max_tokens=2200,
        )
        payload["items"] = [
            JudgeItemResult.model_validate(item).model_dump(by_alias=True)
            for item in payload.get("items", [])
        ]
        payload["pendingSubjective"] = [
            PracticeQuestion.model_validate(item).model_dump(by_alias=True)
            for item in payload.get("pendingSubjective", [])
        ]
        return payload


class OpenAICompatibleJudgeFeedbackGenerator:
    """使用主提供商模型生成最终判题汇总。"""

    def __init__(self) -> None:
        provider_name, model_name = _resolve_component_binding("judge_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.2,
        )

    async def summarize(
        self,
        *,
        items: list[JudgeItemResult],
        topic: str,
    ) -> dict[str, Any]:
        payload = await self.generator.generate(
            system_prompt=(
                "你是教学系统中的 Judge Agent。"
                "请基于逐题判题结果汇总整体反馈，只返回 JSON。"
                '结构为 {"summary":"...","totalScore":0.0,"accuracy":0.0,"items":[...],"weakKnowledgeTags":["..."]}。'
                "accuracy 取值 0 到 1。summary 要用中文完整表述。"
            ),
            user_prompt=json.dumps(
                {
                    "topic": topic,
                    "items": [item.model_dump(by_alias=True) for item in items],
                },
                ensure_ascii=False,
            ),
            max_tokens=1400,
        )
        payload["items"] = [
            JudgeItemResult.model_validate(item).model_dump(by_alias=True)
            for item in payload.get("items", [])
        ]
        return payload


class OpenAICompatibleProfileAnalyzer:
    """使用主提供商模型提取学习者画像维度。"""

    def __init__(self) -> None:
        if not _component_provider_ready("profile_llm"):
            raise RuntimeError("profile_llm provider is not ready")
        provider_name, model_name = _resolve_component_binding("profile_llm", default_logical_model="main_chat_model")
        self.generator = OpenAICompatibleJSONGenerator(model_name=model_name, provider_name=provider_name)

    async def analyze(
        self,
        *,
        context_payload: dict[str, Any],
    ) -> LearnerProfileDimensions:
        payload = await self.generator.generate(
            system_prompt=(
                "你是教学系统中的 Profile Agent。"
                "请根据对话、结构化摘要、练习题、判题结果和已有画像，抽取可落地的学习画像。"
                "你必须覆盖至少 7 个教育画像维度：知识基础、技能掌握、薄弱知识点、学习习惯、"
                "易错模式、认知风格与学习偏好、当前学习目标。"
                "禁止输出占位词、禁止写“待补充/未提供/未知情况较多”这类空泛描述；"
                "信息不足时请基于已有证据做保守推断，并把置信度调低。"
                "输出必须是 JSON，字段为 "
                '{"knowledgeFoundation":"BEGINNER|BASIC|INTERMEDIATE|ADVANCED",'
                '"learningGoal":"...",'
                '"professionalBackground":"...",'
                '"learningPreference":"...",'
                '"cognitiveStyle":"...",'
                '"weakPoints":["..."],'
                '"learningPace":"steady|normal|fast",'
                '"confidenceLevel":"LOW|MEDIUM|HIGH",'
                '"confidenceScore":0.0,'
                '"skillMastery":{"技能名":0.0},'
                '"weakPointDetails":[{"topic":"...","severity":0.0,"lastError":"..."}],'
                '"learningHabits":{"studyFrequency":"...","preferredTime":"...","avgSessionDuration":0,'
                '"noteTaking":false,"selfTesting":false},'
                '"errorPatterns":[{"pattern":"...","frequency":0.0,"examples":["..."]}],'
                '"currentGoal":{"shortTerm":"...","midTerm":"...","context":"...","urgency":"LOW|MEDIUM|HIGH"},'
                '"preferredResourceTypes":["DOCUMENT|READING|MINDMAP|CODE|QUIZ|VIDEO"],'
                '"explanationPreference":"先原理后例子|先例子后原理|step_by_step",'
                '"inferredRecommendations":["..."],'
                '"evidence":["..."],'
                '"source":"CONVERSATION|EVALUATION|PRACTICE",'
                '"summaryText":"..."}。'
                "所有分值范围必须在 0 到 1 之间；summaryText 需要明确说明该学生当前水平、薄弱点、偏好和下一步建议。"
            ),
            user_prompt=json.dumps(context_payload, ensure_ascii=False),
            max_tokens=1400,
        )
        return LearnerProfileDimensions.model_validate(payload)


class ResourcePushRerankItem(BaseModel):
    """LLM 返回的重排序资源候选。"""

    index: int
    score: float = 0.0
    reason: str = ""

    model_config = ConfigDict(extra="ignore")


class ResourcePushRerankPayload(BaseModel):
    """资源推送的结构化重排序载荷。"""

    ranked_items: list[ResourcePushRerankItem] = Field(default_factory=list, alias="rankedItems")
    summary_text: str = Field(default="", alias="summaryText")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class OpenAICompatibleResourcePushReranker:
    """使用 OpenAI 兼容的重排序模型对资源推送候选进行重排序。"""

    def __init__(self) -> None:
        settings = get_settings()
        if not _component_provider_ready("resource_push_llm"):
            raise RuntimeError("resource_push_llm provider is not ready")
        provider_name = settings.resolve_component_provider("resource_push_llm")
        model_name = settings.resolve_component_model(
            "resource_push_llm",
            default_logical_model="rerank_model",
            provider_name=provider_name,
        )
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.0,
        )

    async def rerank(
        self,
        *,
        query: str,
        profile_context: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> ResourcePushRerankPayload:
        payload = await self.generator.generate(
            system_prompt=(
                "你是学习资源推送系统中的重排器。"
                "请结合用户查询、学习画像和候选资源，选出最适合当前学生的资源排序。"
                "排序原则：先匹配薄弱点与学习目标，再匹配学生水平和资源类型偏好，最后考虑摘要与标题相关性。"
                "禁止凭空捏造候选资源，不允许输出未提供的索引。"
                "输出必须是 JSON，格式为 "
                '{"rankedItems":[{"index":0,"score":0.0,"reason":"..."}],"summaryText":"..."}。'
                "score 范围为 0 到 1，rankedItems 按优先级从高到低排序，最多返回 5 个。"
            ),
            user_prompt=json.dumps(
                {
                    "query": query,
                    "profileContext": profile_context,
                    "candidates": candidates,
                },
                ensure_ascii=False,
            ),
            max_tokens=1200,
        )
        return ResourcePushRerankPayload.model_validate(payload)


def _normalize_evaluation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in ("strengths", "weaknesses", "nextFocus"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = _split_text_list(value)
    dimensions = normalized.get("dimensions")
    if isinstance(dimensions, dict):
        normalized["dimensions"] = [dimensions]
    elif not isinstance(dimensions, list):
        normalized["dimensions"] = []
    return normalized


def _split_text_list(value: str) -> list[str]:
    parts = [
        item.strip(" -\t\r\n")
        for item in value.replace("；", "，").replace("、", "，").replace("\n", "，").split("，")
    ]
    filtered = [item for item in parts if item]
    return filtered or [value.strip()]


# 当前 Agent 使用的提供商无关别名。
StructuredJSONGenerator = OpenAICompatibleJSONGenerator
QueryRewriteGenerator = OpenAICompatibleQueryRewriteGenerator
RetrievalSummaryGenerator = OpenAICompatibleRetrievalSummaryGenerator
ConversationSummaryRefiner = OpenAICompatibleConversationSummaryRefiner
EvaluationGenerator = OpenAICompatibleEvaluationGenerator
LearningPathGenerator = OpenAICompatibleLearningPathGenerator
PracticeQuestionGenerator = OpenAICompatiblePracticeQuestionGenerator
ObjectiveJudgeGenerator = OpenAICompatibleObjectiveJudgeGenerator
JudgeFeedbackGenerator = OpenAICompatibleJudgeFeedbackGenerator
ProfileAnalyzer = OpenAICompatibleProfileAnalyzer
ResourcePushReranker = OpenAICompatibleResourcePushReranker

BailianJSONGenerator = OpenAICompatibleJSONGenerator
BailianQueryRewriteGenerator = OpenAICompatibleQueryRewriteGenerator
BailianRetrievalSummaryGenerator = OpenAICompatibleRetrievalSummaryGenerator
BailianConversationSummaryRefiner = OpenAICompatibleConversationSummaryRefiner
BailianEvaluationGenerator = OpenAICompatibleEvaluationGenerator
BailianLearningPathGenerator = OpenAICompatibleLearningPathGenerator
BailianPracticeQuestionGenerator = OpenAICompatiblePracticeQuestionGenerator
BailianObjectiveJudgeGenerator = OpenAICompatibleObjectiveJudgeGenerator
BailianJudgeFeedbackGenerator = OpenAICompatibleJudgeFeedbackGenerator
BailianProfileAnalyzer = OpenAICompatibleProfileAnalyzer
BailianResourcePushReranker = OpenAICompatibleResourcePushReranker


class PracticeLLMClientFactory:
    """创建练习 Agent LLM，支持提供商路由和规则回退。"""

    @staticmethod
    def create() -> Any:
        if _component_provider_ready("practice_llm"):
            provider_name, model_name = _resolve_component_binding("practice_llm", default_logical_model="main_chat_model")
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        return RuleBasedPracticeLLM()


class JudgeLLMClientFactory:
    """创建判题 Agent LLM，支持提供商路由和规则回退。"""

    @staticmethod
    def create() -> Any:
        if _component_provider_ready("judge_llm"):
            provider_name, model_name = _resolve_component_binding("judge_llm", default_logical_model="main_chat_model")
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        return RuleBasedJudgeLLM()


class ProfileLLMClientFactory:
    """创建画像 Agent LLM，支持提供商路由和规则回退。"""

    @staticmethod
    def create() -> Any:
        if _component_provider_ready("profile_llm"):
            provider_name, model_name = _resolve_component_binding("profile_llm", default_logical_model="main_chat_model")
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        return RuleBasedProfileLLM()


class TutorToolLLMClientFactory:
    """创建 Tutor Agent LLM，支持提供商路由和规则回退。"""

    @staticmethod
    def create() -> Any:
        if _component_provider_ready("tutor_llm"):
            provider_name, model_name = _resolve_component_binding("tutor_llm", default_logical_model="main_chat_model")
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        return RuleBasedTutorLLM()


class ConversationSummaryRefinerFactory:
    """Create an optional LLM refiner for structured conversation memory."""

    @staticmethod
    def create() -> Any | None:
        if _component_provider_ready("conversation_summary_llm"):
            return OpenAICompatibleConversationSummaryRefiner()
        return None
