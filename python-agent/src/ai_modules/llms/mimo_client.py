"""异步 + 同步 MiMo 平台客户端，用于 TTS 和 Omni 多模态请求。"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, ClassVar

import httpx
from opentelemetry import trace

from src.ai_modules.config import get_settings
from src.ai_modules.llms.errors import (
    llm_timeout_error,
    llm_transport_error,
    missing_llm_config_error,
    raise_for_llm_status,
)

LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

class MiMoClient:
    """小米 MiMo 平台的异步 HTTP 客户端。"""

    _shared: ClassVar[dict[str, httpx.AsyncClient]] = {}
    _sync_client: ClassVar[httpx.Client | None] = None

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = 60.0) -> None:
        settings = get_settings()
        resolved_api_key = api_key or settings.mimo_api_key or settings.openai_compatible_api_key
        self.api_key = resolved_api_key
        self.base_url = self._resolve_base_url(settings, resolved_api_key)
        self.timeout_seconds = timeout_seconds

    async def _get_client(self) -> httpx.AsyncClient:
        key = f"mimo:{self.timeout_seconds}"
        client = self._shared.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            self._shared[key] = client
        return client

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None or self._sync_client.is_closed:
            self._sync_client = httpx.Client(timeout=self.timeout_seconds)
        return self._sync_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _resolve_base_url(settings, api_key: str) -> str:
        # Token Plan 密钥必须保持在其区域集群上，而不是公共 API 主机。
        if api_key.startswith("tp-") and settings.openai_compatible_base_url:
            return settings.openai_compatible_base_url.rstrip("/")
        if settings.mimo_base_url:
            return settings.mimo_base_url.rstrip("/")
        if settings.openai_compatible_base_url:
            return settings.openai_compatible_base_url.rstrip("/")
        return "https://api.xiaomimimo.com/v1"

    # ── TTS 语音合成 ──────────────────────────────────────────────

    async def synthesize_speech(
        self,
        *,
        text: str,
        style_description: str = "用清晰自然的语速播报，声音沉稳专业",
        voice: str = "mimo_default",
        audio_format: str = "mp3",
    ) -> bytes:
        """调用 MiMo-V2.5-TTS 并返回原始音频字节。"""
        if not self.api_key:
            raise missing_llm_config_error(provider="mimo", model="mimo-v2.5-tts")

        payload: dict[str, Any] = {
            "model": "mimo-v2.5-tts",
            "messages": [
                {"role": "user", "content": style_description},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": audio_format, "voice": voice},
        }

        with TRACER.start_as_current_span("mimo.tts.synthesize"):
            client = await self._get_client()
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise llm_timeout_error(provider="mimo", model="mimo-v2.5-tts") from exc
            except httpx.TransportError as exc:
                raise llm_transport_error(provider="mimo", model="mimo-v2.5-tts") from exc
            raise_for_llm_status(response, provider="mimo", model="mimo-v2.5-tts")
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("mimo tts response missing choices")
        audio_b64 = choices[0].get("message", {}).get("audio", {}).get("data", "")
        if not audio_b64:
            raise RuntimeError("mimo tts response missing audio.data")
        return base64.b64decode(audio_b64)

    # ── Omni 聊天（异步） ───────────────────────────────────────

    async def omni_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 8192,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用 MiMo-V2-Omni 进行多模态生成。"""
        if not self.api_key:
            raise missing_llm_config_error(provider="mimo", model="mimo-v2-omni")

        payload: dict[str, Any] = {
            "model": "mimo-v2-omni",
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format

        with TRACER.start_as_current_span("mimo.omni.chat"):
            client = await self._get_client()
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise llm_timeout_error(provider="mimo", model="mimo-v2-omni") from exc
            except httpx.TransportError as exc:
                raise llm_transport_error(provider="mimo", model="mimo-v2-omni") from exc
            raise_for_llm_status(response, provider="mimo", model="mimo-v2-omni")
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("mimo omni response missing choices")
        return data

    # ── Omni 聊天（同步） ────────────────────────────────────────

    def omni_chat_sync(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> dict[str, Any]:
        """使用 httpx.Client 的 omni_chat 同步封装。"""
        if not self.api_key:
            raise missing_llm_config_error(provider="mimo", model="mimo-v2-omni")

        payload: dict[str, Any] = {
            "model": "mimo-v2-omni",
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        with TRACER.start_as_current_span("mimo.omni.chat_sync"):
            client = self._get_sync_client()
            try:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise llm_timeout_error(provider="mimo", model="mimo-v2-omni") from exc
            except httpx.TransportError as exc:
                raise llm_transport_error(provider="mimo", model="mimo-v2-omni") from exc
            raise_for_llm_status(response, provider="mimo", model="mimo-v2-omni")
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("mimo omni response missing choices")
        return data

    def extract_content(self, response_json: dict[str, Any]) -> str:
        choices = response_json.get("choices", [])
        if not choices:
            raise RuntimeError("missing choices")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content)

    def extract_json(self, response_json: dict[str, Any]) -> dict[str, Any]:
        content = self.extract_content(response_json)
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if match:
            return json.loads(match.group(1))
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            return json.loads(content[start : end + 1])
        raise ValueError(f"no json found in omni response: {content[:200]}")

    # ── Omni 聊天（流式） ────────────────────────────────────────

    async def omni_chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ):
        """流式调用 MiMo-V2-Omni，yield (chunk_type, chunk_data)。

        chunk_type 可能是:
        - "reasoning": 思考过程文本
        - "content": 最终内容文本
        - "done": 流结束，chunk_data 为完整响应 dict
        """
        if not self.api_key:
            raise missing_llm_config_error(provider="mimo", model="mimo-v2-omni")

        payload: dict[str, Any] = {
            "model": "mimo-v2-omni",
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        with TRACER.start_as_current_span("mimo.omni.chat_stream"):
            client = await self._get_client()
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout_seconds,
                ) as response:
                    raise_for_llm_status(response, provider="mimo", model="mimo-v2-omni")
                    accumulated_content = ""
                    async for line in response.aiter_lines():
                        if not line.strip() or line.strip() == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        try:
                            chunk = json.loads(line)
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})

                            # 检查是否有 reasoning 字段（深度思考）
                            if "reasoning" in delta:
                                reasoning_text = delta.get("reasoning", "")
                                if reasoning_text:
                                    yield ("reasoning", reasoning_text)

                            # 检查是否有 content 字段（最终内容）
                            if "content" in delta:
                                content_text = delta.get("content", "")
                                if content_text:
                                    accumulated_content += content_text
                                    yield ("content", content_text)

                            # 检查是否流结束
                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason:
                                # 构造完整响应格式
                                full_response = {
                                    "choices": [{
                                        "message": {"content": accumulated_content},
                                        "finish_reason": finish_reason,
                                    }]
                                }
                                yield ("done", full_response)
                                return
                        except json.JSONDecodeError:
                            LOGGER.warning(f"failed to parse SSE chunk: {line[:100]}")
                            continue
            except httpx.TimeoutException as exc:
                raise llm_timeout_error(provider="mimo", model="mimo-v2-omni") from exc
            except httpx.TransportError as exc:
                raise llm_transport_error(provider="mimo", model="mimo-v2-omni") from exc
