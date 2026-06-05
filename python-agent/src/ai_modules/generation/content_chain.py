"""基于 LLM 的内容生成链。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar, Protocol, TypeVar

import httpx
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.ai_modules.config import get_settings
from src.ai_modules.generation.prompts import (
    build_code_system_prompt,
    build_code_user_prompt,
    build_document_system_prompt,
    build_document_user_prompt,
    build_mindmap_system_prompt,
    build_mindmap_user_prompt,
    build_reading_system_prompt,
    build_reading_user_prompt,
    build_slides_system_prompt,
    build_slides_user_prompt,
    build_video_script_system_prompt,
    build_video_script_user_prompt,
)
from src.ai_modules.models.video import VideoScriptPayload
from src.ai_modules.llms.openai_compatible import extract_json_object_from_text

LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)
StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


class GenerationOutputInvalidError(RuntimeError):
    """Raised when LLM structured output cannot satisfy the required schema."""


class GeneratedSection(BaseModel):
    """单个已生成章节的结构化输出。"""

    title: str
    body: str
    tips: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class GeneratedSectionBundle(BaseModel):
    """结构化教学文档的已生成内容包。"""

    sections: list[GeneratedSection]

    async def _await_self(self) -> "GeneratedSectionBundle":
        return self

    def __await__(self):
        return self._await_self().__await__()


class GeneratedTextAsset(BaseModel):
    """阅读或代码类资产的结构化输出。"""

    title: str
    summary: str
    body: str


class GeneratedSlide(BaseModel):
    """单个已生成的幻灯片。"""

    title: str
    bullets: list[str] = Field(default_factory=list)
    speaker_notes: str = Field(alias="speakerNotes")

    model_config = ConfigDict(populate_by_name=True)


class GeneratedSlideDeck(BaseModel):
    """幻灯片的结构化输出。"""

    title: str
    summary: str
    slides: list[GeneratedSlide]

    model_config = ConfigDict(populate_by_name=True)


class GeneratedMindMapNode(BaseModel):
    """已生成思维导图中的节点。"""

    name: str
    children: list["GeneratedMindMapNode"] = Field(default_factory=list)


class GeneratedMindMap(BaseModel):
    """思维导图的结构化输出。"""

    title: str
    summary: str
    root: str
    children: list[GeneratedMindMapNode]
    mermaid: str = ""


GeneratedMindMapNode.model_rebuild()


class GeneratedCodeAsset(BaseModel):
    """代码资产的结构化输出。"""

    title: str
    summary: str
    language: str = "python"
    code: str
    explanation: str


class SupportsStructuredGenerator(Protocol):
    """主结构化内容生成器的协议。"""

    async def generate_document_sections(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        section_plans: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> GeneratedSectionBundle: ...

    async def generate_reading_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedTextAsset: ...

    async def generate_slides_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedSlideDeck: ...

    async def generate_mindmap_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedMindMap: ...

    async def generate_code_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedCodeAsset: ...

    async def generate_video_script(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        duration_seconds: int,
        style: str,
    ) -> VideoScriptPayload: ...


class OpenAICompatibleStructuredGenerator:
    """用于已配置提供商的 OpenAI 兼容结构化生成器。"""

    _shared_clients: ClassVar[dict[str, httpx.Client]] = {}
    _shared_async_clients: ClassVar[dict[str, httpx.AsyncClient]] = {}

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
        provider_name: str | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        settings = get_settings()
        self.provider_name = settings.normalize_provider_name(
            provider_name or settings.resolve_component_provider("generation_llm")
        )
        provider_config = settings.provider_endpoint_config(self.provider_name)
        self.api_key = api_key or settings.provider_api_key(self.provider_name)
        self.base_url = provider_config.base_url.rstrip("/")
        self.model_name = model_name or settings.resolve_component_model(
            "generation_llm",
            default_logical_model="main_chat_model",
            provider_name=self.provider_name,
        )
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def _get_client(self) -> httpx.Client:
        client_key = f"{self.provider_name}:{self.base_url}"
        client = self._shared_clients.get(client_key)
        if client is None or client.is_closed:
            client = httpx.Client(
                timeout=60.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            self._shared_clients[client_key] = client
        return client

    def _get_async_client(self) -> httpx.AsyncClient:
        client_key = f"{self.provider_name}:{self.base_url}"
        client = self._shared_async_clients.get(client_key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=60.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            self._shared_async_clients[client_key] = client
        return client

    @classmethod
    async def close_async_clients(cls) -> None:
        """在应用关闭时关闭共享的异步 HTTP 客户端。"""

        clients = list(cls._shared_async_clients.values())
        cls._shared_async_clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
                LOGGER.debug("Skipping async structured client close after closed event loop")

    def _post_chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(f"missing {self.provider_name} api key")
        client = self._get_client()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        response = client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        self._record_usage(data)
        return data

    async def _post_chat_completion_async(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(f"missing {self.provider_name} api key")
        client = self._get_async_client()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        response = await client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        self._record_usage(data)
        return data

    def _record_usage(self, data: dict[str, Any]) -> None:
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            return
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cached_tokens = 0
        prompt_details = usage.get("prompt_tokens_details", {})
        if isinstance(prompt_details, dict):
            cached_tokens = int(prompt_details.get("cached_tokens", 0) or 0)
        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.set_attribute("llm.provider", self.provider_name)
            current_span.set_attribute("llm.model_name", self.model_name)
            current_span.set_attribute("llm.prompt_tokens", prompt_tokens)
            current_span.set_attribute("llm.completion_tokens", completion_tokens)
            current_span.set_attribute("llm.cached_prompt_tokens", cached_tokens)
        if cached_tokens > 0:
            LOGGER.info(
                "%s structured generation cache hit: %d/%d prompt tokens reused",
                self.provider_name,
                cached_tokens,
                prompt_tokens,
            )

    def generate_document_sections(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        section_plans: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return self.generate_document_sections_async(
                title=title,
                topic=topic,
                snapshot=snapshot,
                section_plans=section_plans,
                sources=sources,
            )
        return self._call_and_validate_json_sync(
            span_name=f"{self.provider_name}.generate_document_sections",
            system_prompt=build_document_system_prompt(),
            user_prompt=build_document_user_prompt(
                title=title,
                topic=topic,
                snapshot=snapshot,
                section_plans=section_plans,
                sources=sources,
            ),
            max_tokens=4200,
            model_type=GeneratedSectionBundle,
            schema_hint='{"sections":[{"title":"...","body":"...","tips":["..."],"citations":["..."]}]}',
        )

    async def generate_document_sections_async(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        section_plans: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> GeneratedSectionBundle:
        return await self._call_and_validate_json(
            span_name=f"{self.provider_name}.generate_document_sections",
            system_prompt=build_document_system_prompt(),
            user_prompt=build_document_user_prompt(
                title=title,
                topic=topic,
                snapshot=snapshot,
                section_plans=section_plans,
                sources=sources,
            ),
            max_tokens=4200,
            model_type=GeneratedSectionBundle,
            schema_hint='{"sections":[{"title":"...","body":"...","tips":["..."],"citations":["..."]}]}',
        )

    async def generate_reading_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedTextAsset:
        return await self._call_and_validate_json(
            span_name=f"{self.provider_name}.generate_reading_asset",
            system_prompt=build_reading_system_prompt(),
            user_prompt=build_reading_user_prompt(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            ),
            max_tokens=1600,
            model_type=GeneratedTextAsset,
            schema_hint='{"title":"...","summary":"...","body":"..."}',
        )

    async def generate_slides_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedSlideDeck:
        return GeneratedSlideDeck.model_validate(
            await self._call_and_parse_json(
                span_name=f"{self.provider_name}.generate_slides_asset",
                system_prompt=build_slides_system_prompt(),
                user_prompt=build_slides_user_prompt(
                    title=title,
                    topic=topic,
                    snapshot=snapshot,
                    sources=sources,
                ),
                max_tokens=1800,
            )
        )

    async def generate_mindmap_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedMindMap:
        mermaid = await self._call_and_extract_text(
            span_name=f"{self.provider_name}.generate_mindmap_asset",
            system_prompt=build_mindmap_system_prompt(),
            user_prompt=build_mindmap_user_prompt(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            ),
            max_tokens=1600,
        )
        return self._parse_mindmap_mermaid(title=title, topic=topic, mermaid=mermaid)

    async def generate_code_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedCodeAsset:
        return GeneratedCodeAsset.model_validate(
            await self._call_and_parse_json(
                span_name=f"{self.provider_name}.generate_code_asset",
                system_prompt=build_code_system_prompt(),
                user_prompt=build_code_user_prompt(
                    title=title,
                    topic=topic,
                    snapshot=snapshot,
                    sources=sources,
                ),
                max_tokens=2200,
            )
        )

    async def generate_video_script(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        duration_seconds: int,
        style: str,
    ) -> VideoScriptPayload:
        return VideoScriptPayload.model_validate(
            await self._call_and_parse_json(
                span_name=f"{self.provider_name}.generate_video_script",
                system_prompt=build_video_script_system_prompt(),
                user_prompt=build_video_script_user_prompt(
                    title=title,
                    topic=topic,
                    snapshot=snapshot,
                    sources=sources,
                    duration_seconds=duration_seconds,
                    style=style,
                ),
                max_tokens=2200,
            )
        )

    async def generate_video_script_async(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        duration_seconds: int,
        style: str,
    ) -> VideoScriptPayload:
        return VideoScriptPayload.model_validate(
            await self._call_and_parse_json_async(
                span_name=f"{self.provider_name}.generate_video_script",
                system_prompt=build_video_script_system_prompt(),
                user_prompt=build_video_script_user_prompt(
                    title=title,
                    topic=topic,
                    snapshot=snapshot,
                    sources=sources,
                    duration_seconds=duration_seconds,
                    style=style,
                ),
                max_tokens=2200,
            )
        )

    async def _call_and_validate_json(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None,
        model_type: type[StructuredModelT],
        schema_hint: str,
    ) -> StructuredModelT:
        last_output_error: Exception | None = None
        last_payload: dict[str, Any] | None = None
        attempt_user_prompt = user_prompt
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            payload: dict[str, Any] | None = None
            try:
                with TRACER.start_as_current_span(span_name):
                    response = await self._post_chat_completion_async(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": attempt_user_prompt},
                        ],
                        temperature=0.0,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                    )
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                LOGGER.warning(
                    "%s structured generation request attempt %s/%s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    attempts,
                    exc,
                )
                if attempt >= self.max_retries:
                    raise RuntimeError(f"{self.provider_name} structured generation failed: {exc}") from exc
                await asyncio.sleep(self.backoff_seconds * (2**attempt))
                continue

            try:
                payload_text = self._extract_message_content(response)
                payload = self._extract_json(payload_text)
                return model_type.model_validate(payload)
            except (RuntimeError, ValueError, KeyError, TypeError, ValidationError) as exc:
                last_output_error = exc
                if payload is not None:
                    last_payload = payload
                LOGGER.warning(
                    "%s structured output invalid attempt %s/%s model=%s base_url=%s schema=%s error_type=%s error=%s payload=%s",
                    self.provider_name,
                    attempt + 1,
                    attempts,
                    self.model_name,
                    self.base_url,
                    model_type.__name__,
                    type(exc).__name__,
                    exc,
                    self._compact_payload(last_payload or {}),
                )
                if attempt >= self.max_retries:
                    break
                attempt_user_prompt = self._build_schema_repair_prompt(
                    original_user_prompt=user_prompt,
                    schema_hint=schema_hint,
                    last_error=exc,
                    last_payload=last_payload or {},
                )
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise GenerationOutputInvalidError(
            f"{self.provider_name} structured generation produced invalid "
            f"{model_type.__name__} after {attempts} attempts: {last_output_error}"
        )

    def _call_and_validate_json_sync(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None,
        model_type: type[StructuredModelT],
        schema_hint: str,
    ) -> StructuredModelT:
        last_output_error: Exception | None = None
        last_payload: dict[str, Any] | None = None
        attempt_user_prompt = user_prompt
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            payload: dict[str, Any] | None = None
            try:
                with TRACER.start_as_current_span(span_name):
                    response = self._post_chat_completion(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": attempt_user_prompt},
                        ],
                        temperature=0.0,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                    )
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                LOGGER.warning(
                    "%s structured generation request attempt %s/%s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    attempts,
                    exc,
                )
                if attempt >= self.max_retries:
                    raise RuntimeError(f"{self.provider_name} structured generation failed: {exc}") from exc
                time.sleep(self.backoff_seconds * (2**attempt))
                continue

            try:
                payload_text = self._extract_message_content(response)
                payload = self._extract_json(payload_text)
                return model_type.model_validate(payload)
            except (RuntimeError, ValueError, KeyError, TypeError, ValidationError) as exc:
                last_output_error = exc
                if payload is not None:
                    last_payload = payload
                LOGGER.warning(
                    "%s structured output invalid attempt %s/%s model=%s base_url=%s schema=%s error_type=%s error=%s payload=%s",
                    self.provider_name,
                    attempt + 1,
                    attempts,
                    self.model_name,
                    self.base_url,
                    model_type.__name__,
                    type(exc).__name__,
                    exc,
                    self._compact_payload(last_payload or {}),
                )
                if attempt >= self.max_retries:
                    break
                attempt_user_prompt = self._build_schema_repair_prompt(
                    original_user_prompt=user_prompt,
                    schema_hint=schema_hint,
                    last_error=exc,
                    last_payload=last_payload or {},
                )
                time.sleep(self.backoff_seconds * (2**attempt))

        raise GenerationOutputInvalidError(
            f"{self.provider_name} structured generation produced invalid "
            f"{model_type.__name__} after {attempts} attempts: {last_output_error}"
        )

    async def _call_and_parse_json_once(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        with TRACER.start_as_current_span(span_name):
            response = await self._post_chat_completion_async(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        payload = self._extract_message_content(response)
        return self._extract_json(payload)

    def _build_schema_repair_prompt(
        self,
        *,
        original_user_prompt: str,
        schema_hint: str,
        last_error: Exception,
        last_payload: dict[str, Any],
    ) -> str:
        return "\n\n".join(
            [
                original_user_prompt,
                "The previous LLM output was invalid and failed schema validation.",
                f"Required JSON schema: {schema_hint}",
                f"Validation error: {self._compact_text(type(last_error).__name__ + ': ' + str(last_error))}",
                f"Previous JSON object: {self._compact_payload(last_payload)}",
                "Regenerate the complete asset. Return only one valid JSON object with all required fields.",
            ]
        )

    def _compact_payload(self, payload: dict[str, Any]) -> str:
        return self._compact_text(repr(payload))

    def _compact_text(self, value: str, limit: int = 1200) -> str:
        if len(value) <= limit:
            return value
        return value[:limit] + "...<truncated>"

    async def _call_and_parse_json(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._call_and_parse_json_once(
                    span_name=span_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )
            except (ValidationError, ValueError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as exc:
                last_error = exc
                LOGGER.warning(
                    "%s structured generation attempt %s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise RuntimeError(f"{self.provider_name} structured generation failed: {last_error}")

    async def _call_and_parse_json_async(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with TRACER.start_as_current_span(span_name):
                    response = await self._post_chat_completion_async(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.0,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                    )
                payload = self._extract_message_content(response)
                return self._extract_json(payload)
            except (ValidationError, ValueError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as exc:
                last_error = exc
                LOGGER.warning(
                    "%s structured generation attempt %s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise RuntimeError(f"{self.provider_name} structured generation failed: {last_error}")

    async def _call_and_extract_text(
        self,
        *,
        span_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with TRACER.start_as_current_span(span_name):
                    response = await self._post_chat_completion_async(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.3,
                        max_tokens=max_tokens,
                    )
                return self._extract_message_content(response)
            except (RuntimeError, KeyError, TypeError, httpx.HTTPError) as exc:
                last_error = exc
                LOGGER.warning(
                    "%s text generation attempt %s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise RuntimeError(f"{self.provider_name} text generation failed: {last_error}")

    def _extract_message_content(self, response: Any) -> str:
        if not isinstance(response, dict):
            raise RuntimeError(f"unsupported {self.provider_name} response format")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"missing choices from {self.provider_name} response")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(f"missing choice payload from {self.provider_name} response")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(f"missing message in {self.provider_name} response")
        content = message.get("content")
        if isinstance(content, list):
            joined = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
            if joined.strip():
                return joined
        if isinstance(content, str):
            if content.strip():
                return content
        raise RuntimeError(f"unsupported {self.provider_name} content format")

    def _extract_json(self, content: str) -> dict[str, Any]:
        return extract_json_object_from_text(content)

    def _parse_mindmap_mermaid(
        self,
        *,
        title: str,
        topic: str,
        mermaid: str,
    ) -> GeneratedMindMap:
        normalized_mermaid = self._normalize_mermaid_text(mermaid)
        lines = [line for line in normalized_mermaid.splitlines() if line.strip()]
        if not lines or lines[0].strip().lower() != "mindmap":
            raise ValueError(f"invalid mermaid mindmap output from {self.provider_name}")

        root: str | None = None
        root_depth: int | None = None
        children: list[GeneratedMindMapNode] = []
        stack: list[tuple[int, GeneratedMindMapNode]] = []

        for raw_line in lines[1:]:
            depth = len(raw_line) - len(raw_line.lstrip(" "))
            node_name = self._normalize_mermaid_node_label(raw_line.strip())
            if not node_name:
                continue
            if root is None:
                root = node_name
                root_depth = depth
                continue
            node = GeneratedMindMapNode(name=node_name)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].children.append(node)
            else:
                children.append(node)
            stack.append((depth, node))

        if root is None or root_depth is None:
            raise ValueError(f"missing root node in {self.provider_name} mermaid output")

        return GeneratedMindMap(
            title=title,
            summary=f"{topic} 思维导图已生成",
            root=root,
            children=children,
            mermaid=normalized_mermaid,
        )

    def _normalize_mermaid_text(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].strip().lower().startswith("```mermaid"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        if "mindmap" not in stripped.lower():
            raise ValueError(f"no mermaid mindmap found in {self.provider_name} output")
        start = stripped.lower().find("mindmap")
        return stripped[start:].strip()

    def _normalize_mermaid_node_label(self, label: str) -> str:
        text = label.strip()
        if text.startswith("root((") and text.endswith("))"):
            text = text[6:-2]
        elif "((" in text and text.endswith("))"):
            text = text[text.find("((") + 2 : -2]
        elif "[" in text and text.endswith("]"):
            text = text[text.find("[") + 1 : -1]
        elif "(" in text and text.endswith(")"):
            text = text[text.find("(") + 1 : -1]
        return text.strip().strip('"').strip("'")


BailianStructuredGenerator = OpenAICompatibleStructuredGenerator


class ContentGenerationChain:
    """仅由已配置 LLM 驱动的结构化文档内容链。"""

    def __init__(
        self,
        primary_generator: SupportsStructuredGenerator | None = None,
    ) -> None:
        self.primary_generator = primary_generator or OpenAICompatibleStructuredGenerator()

    async def generate_document_sections(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        section_plans: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> GeneratedSectionBundle:
        with TRACER.start_as_current_span("content_chain.generate_document_sections"):
            generator = self.primary_generator
            if hasattr(generator, "generate_document_sections_async"):
                return await generator.generate_document_sections_async(
                    title=title,
                    topic=topic,
                    snapshot=snapshot,
                    section_plans=section_plans,
                    sources=sources,
                )
            return await generator.generate_document_sections(
                title=title,
                topic=topic,
                snapshot=snapshot,
                section_plans=section_plans,
                sources=sources,
            )

    async def generate_reading_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedTextAsset:
        with TRACER.start_as_current_span("content_chain.generate_reading_asset"):
            return await self.primary_generator.generate_reading_asset(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            )

    async def generate_slides_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedSlideDeck:
        with TRACER.start_as_current_span("content_chain.generate_slides_asset"):
            return await self.primary_generator.generate_slides_asset(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            )

    async def generate_mindmap_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedMindMap:
        with TRACER.start_as_current_span("content_chain.generate_mindmap_asset"):
            return await self.primary_generator.generate_mindmap_asset(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            )

    async def generate_code_asset(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> GeneratedCodeAsset:
        with TRACER.start_as_current_span("content_chain.generate_code_asset"):
            return await self.primary_generator.generate_code_asset(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
            )

    async def generate_video_script(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        duration_seconds: int,
        style: str,
    ) -> VideoScriptPayload:
        with TRACER.start_as_current_span("content_chain.generate_video_script"):
            return await self.primary_generator.generate_video_script(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
                duration_seconds=duration_seconds,
                style=style,
            )

    async def generate_video_script_async(
        self,
        *,
        title: str,
        topic: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        duration_seconds: int,
        style: str,
    ) -> VideoScriptPayload:
        with TRACER.start_as_current_span("content_chain.generate_video_script"):
            return await self.primary_generator.generate_video_script_async(
                title=title,
                topic=topic,
                snapshot=snapshot,
                sources=sources,
                duration_seconds=duration_seconds,
                style=style,
            )
