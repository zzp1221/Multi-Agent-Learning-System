"""LLM-backed Planner for conversation-level multi-agent execution."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import OpenAICompatibleJSONGenerator
from src.ai_modules.models import ConversationPlan
from src.ai_modules.runtime.context_snapshot import SystemSnapshot


ALLOWED_PLAN_SERVICE_TYPES: set[str] = {
    "PERSONALIZED_LEARNING",
    "TUTORING",
    "RESOURCE_GENERATION",
    "RESOURCE_PUSH",
    "PRACTICE_JUDGE",
    "PATH_PLANNING",
    "EVALUATION",
    "PROFILE_BUILD",
}


PLANNER_SYSTEM_PROMPT = """你是智学引擎的多智能体 Planner。
你必须基于用户当前学习目标生成可执行的 JSON 计划，不允许输出 Markdown。
计划中的每一步必须绑定到一个已登记 Agent 或 serviceType。
serviceType 只能从白名单中选择：
PERSONALIZED_LEARNING, TUTORING, RESOURCE_GENERATION, RESOURCE_PUSH, PRACTICE_JUDGE, PATH_PLANNING, EVALUATION, PROFILE_BUILD。
不要输出未登记工具、URL、SQL、shell 命令或虚构服务。
如果需要完整个性化学习方案、资源生成、资源推送或练习判题，可以直接写成对应 serviceType，本系统会自动执行。
输出 JSON 结构：
{
  "goal": "...",
  "steps": [
    {
      "stepId": "短英文id",
      "title": "中文步骤标题",
      "intent": "中文意图说明",
      "agentName": "query_rewrite/retrieval/tutor/profile/practice/judge/path_planning/evaluation/resource_push之一",
      "serviceType": "或白名单serviceType之一",
      "dependsOn": [],
      "inputKeys": [],
      "outputKeys": [],
      "requiresApproval": false,
      "qualityGate": "critic或空"
    }
  ]
}
"""


class ConversationPlanner:
    """Create real LLM-generated plans and validate them against local capabilities."""

    def __init__(
        self,
        *,
        allowed_agent_names: set[str],
        generator: OpenAICompatibleJSONGenerator | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        settings = get_settings()
        resolved_provider = provider_name or settings.resolve_component_provider("planning_llm")
        if not settings.provider_ready(resolved_provider):
            raise RuntimeError("planning_llm provider is not ready")
        resolved_model = model_name or settings.resolve_component_model(
            "planning_llm",
            default_logical_model="reasoning_model",
            provider_name=resolved_provider,
        )
        self.provider_name = resolved_provider
        self.model_name = resolved_model
        self.allowed_agent_names = set(allowed_agent_names)
        self.generator = generator or OpenAICompatibleJSONGenerator(
            model_name=resolved_model,
            provider_name=resolved_provider,
            temperature=0.1,
            cache_namespace="conversation_planner",
        )

    async def plan(
        self,
        *,
        service_type: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        route_agent_names: list[str],
        query_type: str | None = None,
        graph_intent: str | None = None,
    ) -> ConversationPlan:
        payload = await self.generator.generate(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "serviceType": service_type,
                    "query": params.get("query") or params.get("topic") or params.get("question"),
                    "params": self._compact_params(params),
                    "snapshot": self._snapshot_payload(snapshot),
                    "routeAgentNames": route_agent_names,
                    "queryType": query_type,
                    "graphIntent": graph_intent,
                },
                ensure_ascii=False,
            ),
            max_tokens=1600,
        )
        raw_plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
        if not isinstance(raw_plan, dict):
            raise ValueError("Planner returned non-object payload")
        normalized = {
            **raw_plan,
            "planId": str(raw_plan.get("planId") or uuid4()),
            "serviceType": service_type,
            "createdBy": "llm_planner",
            "status": "PLANNED",
            "provider": self.provider_name,
            "model": self.model_name,
            "metadata": {
                **(raw_plan.get("metadata") if isinstance(raw_plan.get("metadata"), dict) else {}),
                "queryType": query_type,
                "graphIntent": graph_intent,
            },
        }
        plan = ConversationPlan.model_validate(normalized)
        self._validate_plan(plan)
        return plan

    def _validate_plan(self, plan: ConversationPlan) -> None:
        for step in plan.steps:
            if step.agent_name and step.agent_name not in self.allowed_agent_names:
                raise ValueError(f"Planner returned unregistered agentName: {step.agent_name}")
            if step.service_type:
                normalized_service = step.service_type.strip().upper()
                if normalized_service not in ALLOWED_PLAN_SERVICE_TYPES:
                    raise ValueError(f"Planner returned unsupported serviceType: {step.service_type}")
                step.service_type = normalized_service
            if step.quality_gate and step.quality_gate != "critic":
                raise ValueError(f"Planner returned unsupported qualityGate: {step.quality_gate}")

    @staticmethod
    def _compact_params(params: dict[str, Any]) -> dict[str, Any]:
        kept_keys = (
            "query",
            "topic",
            "resourceType",
            "resourceTypes",
            "learningContext",
            "profile",
            "profileSummary",
            "messages",
            "difficulty",
            "assessmentDimensions",
        )
        return {key: params[key] for key in kept_keys if key in params}

    @staticmethod
    def _snapshot_payload(snapshot: SystemSnapshot) -> dict[str, Any]:
        return {
            "currentCourse": snapshot.current_course,
            "currentChapter": snapshot.current_chapter,
            "studentLevel": snapshot.student_level,
            "knowledgeGaps": snapshot.knowledge_gaps,
            "preferredStyle": snapshot.preferred_style,
            "recentMistakes": snapshot.recent_mistakes,
            "conversationLength": snapshot.conversation_length,
        }
