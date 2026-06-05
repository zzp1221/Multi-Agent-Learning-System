"""Tutor Agent 使用的工具调用 LLM 适配器。"""

from __future__ import annotations

from typing import Any

from src.ai_modules.config import get_settings
from src.ai_modules.llms.openai_compatible import OpenAICompatibleToolCallingLLM
from src.ai_modules.llms.spark_compatible import SparkCompatibleToolCallingLLM
from src.ai_modules.runtime import AssistantTurn, ToolCall


class RuleBasedTutorLLM:
    """仍然执行 AgentCoreLoop 工具调用的确定性回退 LLM。"""

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        del system_prompt, tools
        has_tool_messages = any(message.get("role") == "tool" for message in messages)
        if not has_tool_messages:
            return AssistantTurn(
                content="先读取历史摘要、压缩上下文和检索证据。",
                tool_calls=[
                    ToolCall(id="call_memory", name="load_conversation_memory", input={}),
                    ToolCall(id="call_context", name="read_compacted_context", input={}),
                    ToolCall(id="call_evidence", name="read_retrieval_evidence", input={}),
                ],
            )

        memory = self._read_tool_content(messages, "load_conversation_memory")
        context = self._read_tool_content(messages, "read_compacted_context")
        evidence = self._read_tool_content(messages, "read_retrieval_evidence")
        final_text = self._compose_response(memory=memory, context=context, evidence=evidence)
        return AssistantTurn(content=final_text)

    def _read_tool_content(
        self,
        messages: list[dict[str, Any]],
        tool_name: str,
    ) -> dict[str, Any]:
        for message in reversed(messages):
            if message.get("role") == "tool" and message.get("name") == tool_name:
                content = message.get("content", {})
                return content if isinstance(content, dict) else {"raw": content}
        return {}

    def _compose_response(
        self,
        *,
        memory: dict[str, Any],
        context: dict[str, Any],
        evidence: dict[str, Any],
    ) -> str:
        query = str(
            evidence.get("rewrittenQuery")
            or context.get("lastUserMessage")
            or "当前主题"
        )
        if self._is_small_talk(query):
            return self._compose_small_talk_reply(query)
        documents = evidence.get("documents", []) if isinstance(evidence.get("documents"), list) else []
        external_resources = (
            evidence.get("externalResources", [])
            if isinstance(evidence.get("externalResources"), list)
            else []
        )
        if evidence.get("webSearchEnabled") is True and external_resources:
            return self._compose_external_resource_answer(query=query, resources=external_resources)
        graph_pack = evidence.get("graphEvidencePack") if isinstance(evidence, dict) else {}
        return self._compose_direct_answer(
            query=query,
            memory=memory,
            context=context,
            documents=documents,
            graph_pack=graph_pack if isinstance(graph_pack, dict) else {},
        )

    def _compose_external_resource_answer(
        self,
        *,
        query: str,
        resources: list[dict[str, Any]],
    ) -> str:
        lines = [f"我找到了几条和“{query}”相关的外部资源链接："]
        for index, resource in enumerate(resources[:5], start=1):
            if not isinstance(resource, dict):
                continue
            title = str(resource.get("title") or resource.get("sourceTitle") or "外部资源").strip()
            url = str(resource.get("url") or "").strip()
            snippet = str(resource.get("snippet") or "").strip()
            if not url:
                continue
            line = f"{index}. {title}: {url}"
            if snippet:
                line += f"\n   {snippet[:120]}"
            lines.append(line)
        if len(lines) == 1:
            return "联网搜索已开启，但当前没有检索到可验证的外部链接。"
        lines.append("建议先看标题最贴合你当前主题的一两个资源，再回来问我其中不理解的部分。")
        return "\n".join(lines)

    def _is_small_talk(self, query: str) -> bool:
        normalized = "".join(query.lower().split())
        if not normalized:
            return True
        small_talk_keywords = (
            "你好",
            "您好",
            "hi",
            "hello",
            "哈喽",
            "在吗",
            "早上好",
            "中午好",
            "晚上好",
            "谢谢",
            "感谢",
            "再见",
            "拜拜",
            "你是谁",
            "你能做什么",
            "今天天气",
            "今天怎么样",
        )
        if normalized in small_talk_keywords:
            return True
        return len(normalized) <= 12 and any(keyword in normalized for keyword in small_talk_keywords)

    def _compose_small_talk_reply(self, query: str) -> str:
        normalized = "".join(query.lower().split())
        if any(keyword in normalized for keyword in ("谢谢", "感谢")):
            return "不客气，需要我继续帮你梳理学习问题、生成资源，或者做路径规划时直接告诉我。"
        if any(keyword in normalized for keyword in ("再见", "拜拜")):
            return "好，先这样。有需要随时来找我。"
        if any(keyword in normalized for keyword in ("你是谁", "你能做什么")):
            return "我是智学引擎，可以陪你对话答疑，也可以帮你做资源生成、学习路径规划、资源推送和学习效果评估。"
        return "哈喽，来啦。今天怎么样？如果你有具体课程、知识点或题目，也可以直接发我，我帮你一起拆解。"

    def _compose_direct_answer(
        self,
        *,
        query: str,
        memory: dict[str, Any],
        context: dict[str, Any],
        documents: list[dict[str, Any]],
        graph_pack: dict[str, Any],
    ) -> str:
        normalized = "".join(query.lower().split())
        if "什么是java" in normalized or normalized == "java":
            return (
                "Java 是一种通用的面向对象编程语言，特点是语法相对稳定、生态完善，并且可以通过 JVM 实现跨平台运行。"
                "你可以先把它理解成一门既适合教学入门，也适合企业级开发的编程语言。\n\n"
                "它常见的几个特点是：\n"
                "1. 面向对象，适合用类、对象、封装这些方式组织代码。\n"
                "2. 跨平台，同一份字节码可以在不同系统上的 JVM 中运行。\n"
                "3. 生态成熟，Web、后端、并发、微服务等方向都有大量现成框架。\n\n"
                "如果你愿意，我可以继续用“定义 + 示例代码 + 典型应用场景”给你讲清楚 Java。"
            )
        if "什么是并发编程" in normalized or normalized == "并发编程":
            return (
                "并发编程是指让程序在同一时间段内同时处理多个任务的编程方式。它不一定表示多个任务在同一时刻真正同时执行，"
                "也可以是通过线程切换，让多个任务交替推进。\n\n"
                "在 Java 里，并发编程通常会涉及这几类内容：\n"
                "1. 线程与线程池，用来同时执行多个任务。\n"
                "2. 锁、synchronized、Lock，用来保证共享数据安全。\n"
                "3. volatile、原子类、并发集合，用来解决可见性和并发访问问题。\n\n"
                "简单说，并发编程的目标就是：一边提升程序处理多个任务的能力，一边避免线程安全问题。"
            )
        generic_term = self._extract_generic_concept(query)
        if generic_term:
            return (
                f"{generic_term} 可以先理解为一个需要先抓住“定义、作用、使用场景”三件事的概念。\n\n"
                f"先说定义：{generic_term}通常不是孤立记忆的名词，而是用来解决某一类编程或学习问题的方法、机制或知识点。\n"
                f"再说作用：你需要关注它是为了解决什么问题、适合在什么前提下使用。\n"
                f"最后看场景：只有放到具体代码、题目或业务场景里，才能真正理解 {generic_term}。\n\n"
                f"如果你愿意，我可以继续按“定义 + 例子 + 易错点”把 {generic_term} 详细讲清楚。"
            )

        answer_lines = [f"关于“{query}”，可以先这样理解："]
        evidence_texts = self._collect_evidence_lines(documents)
        if evidence_texts:
            answer_lines.append(evidence_texts[0])
        else:
            answer_lines.append(f"它通常需要结合概念定义、使用条件和具体场景一起理解，而不是只记一句结论。")

        graph_lines = self._collect_graph_evidence_lines(graph_pack)
        if graph_lines:
            answer_lines.append("")
            answer_lines.append("结合图谱证据，可以优先围绕这些相关概念展开：")
            answer_lines.extend(graph_lines)

        if len(evidence_texts) > 1:
            answer_lines.append("")
            answer_lines.append("你可以重点抓住这几点：")
            for index, line in enumerate(evidence_texts[1:4], start=1):
                answer_lines.append(f"{index}. {line}")

        unresolved = memory.get("unresolvedQuestions", []) or context.get("unresolvedQuestions", [])
        if unresolved:
            answer_lines.append("")
            answer_lines.append(f"结合你前面的上下文，你还可以继续追问：{unresolved[0]}")
        else:
            answer_lines.append("")
            answer_lines.append(f"如果你愿意，我可以继续把“{query}”拆成定义、例子和易错点三部分继续讲。")
        return "\n".join(answer_lines)

    def _collect_evidence_lines(self, documents: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for document in documents[:4]:
            title = str(document.get("title") or "").strip()
            evidence = str(document.get("evidence") or "").strip()
            text = evidence or title
            if not text:
                continue
            cleaned = text.replace("由关键词 `", "").replace("` 生成的回退候选。", "").strip()
            if cleaned.startswith("候选知识:"):
                continue
            if cleaned and cleaned not in lines:
                lines.append(cleaned)
        return lines

    def _collect_graph_evidence_lines(self, graph_pack: dict[str, Any]) -> list[str]:
        intent = str(graph_pack.get("intent") or "").strip().upper()
        nodes = graph_pack.get("nodes", [])
        if intent not in {
            "COMMON_MISTAKE",
            "COMMUNITY_SUMMARY",
            "COMPARISON",
            "CROSS_LAYER_RELATION",
            "MECHANISM_APPLICATION",
            "MULTI_HOP_RELATION",
            "PREREQUISITE_PATH",
        } or not isinstance(nodes, list):
            return []

        titles: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
            if len(titles) >= 4:
                break
        if not titles:
            return []

        guidance_by_intent = {
            "PREREQUISITE_PATH": "把它们当作可能的前置基础、当前概念和后续延伸来梳理，不要硬背顺序。",
            "CROSS_LAYER_RELATION": "把它们当作跨层关联证据来解释每层负责什么、如何相互影响。",
            "MECHANISM_APPLICATION": "把它们当作机制、实现位置和应用效果的线索来串起来。",
            "COMPARISON": "把它们当作对比对象来区分共同点、差异和适用边界。",
            "COMMON_MISTAKE": "把它们当作易错点证据来设计反例和纠正心智模型。",
            "COMMUNITY_SUMMARY": "把它们当作同一概念群来概括共同作用和组内差异。",
            "MULTI_HOP_RELATION": "把它们当作多跳相关证据来解释概念之间的联系。",
        }
        return [
            f"1. 相关概念集合：{'、'.join(titles)}。",
            f"2. 组织方式：{guidance_by_intent.get(intent, '围绕这些证据组织回答。')}",
        ]

    def _extract_generic_concept(self, query: str) -> str:
        normalized = query.strip().rstrip("？?。.")
        for prefix in ("什么是", "啥是", "请解释", "解释一下"):
            if normalized.startswith(prefix):
                concept = normalized[len(prefix):].strip("：:，, ")
                if concept:
                    return concept
        return ""


class OpenAICompatibleTutorLLM(OpenAICompatibleToolCallingLLM):
    """Tutor 使用的 OpenAI 兼容工具调用适配器。"""


class TutorLLMClientFactory:
    """创建 Tutor LLM 客户端，支持 OpenAI 兼容主模型和基于规则的回退。"""

    @staticmethod
    def _create_provider_client(
        *,
        provider_name: str,
        model_name: str,
    ) -> Any:
        settings = get_settings()
        endpoint = settings.provider_endpoint_config(provider_name)
        if provider_name == "spark" or endpoint.protocol == "spark_compatible":
            return SparkCompatibleToolCallingLLM(
                api_key=settings.provider_api_key(provider_name),
                base_url=endpoint.base_url,
                model_name=model_name,
            )
        return OpenAICompatibleTutorLLM(
            api_key=settings.provider_api_key(provider_name),
            base_url=endpoint.base_url,
            model_name=model_name,
        )

    @staticmethod
    def _resolve_model_for_provider(provider_name: str) -> str:
        settings = get_settings()
        return settings.resolve_component_model(
            "tutor_llm",
            default_logical_model="main_chat_model",
            provider_name=provider_name,
        )

    @staticmethod
    def create() -> Any:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("tutor_llm")
        if settings.provider_ready(provider_name):
            model_name = TutorLLMClientFactory._resolve_model_for_provider(provider_name)
            return TutorLLMClientFactory._create_provider_client(
                provider_name=provider_name,
                model_name=model_name,
            )
        return RuleBasedTutorLLM()

    @staticmethod
    def create_llm_candidates() -> list[Any]:
        """Create only real LLM clients for Tutor runtime attempts."""

        settings = get_settings()
        routing = settings.model_routing_config()
        provider_names: list[str] = []

        def add_provider(provider_name: str | None) -> None:
            normalized = settings.normalize_provider_name(provider_name)
            if normalized and normalized not in provider_names:
                provider_names.append(normalized)

        add_provider(settings.resolve_component_provider("tutor_llm"))
        add_provider(settings.selected_fallback_provider_name())
        add_provider(routing.fallback_provider)

        clients: list[Any] = []
        for provider_name in provider_names:
            if provider_name not in routing.providers:
                continue
            if not settings.provider_ready(provider_name):
                continue
            model_name = TutorLLMClientFactory._resolve_model_for_provider(provider_name)
            clients.append(
                TutorLLMClientFactory._create_provider_client(
                    provider_name=provider_name,
                    model_name=model_name,
                )
            )
        return clients
