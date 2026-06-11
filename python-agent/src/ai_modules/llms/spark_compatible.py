"""异步 Spark OpenAI 兼容客户端辅助工具。"""

from __future__ import annotations

from src.ai_modules.config import get_settings
from src.ai_modules.llms.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleToolCallingLLM,
)


class SparkCompatibleClient(OpenAICompatibleClient):
    """用于 Spark OpenAI 兼容聊天补全的小型异步客户端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        settings = get_settings()
        super().__init__(
            api_key=api_key or settings.spark_api_key,
            base_url=base_url or settings.spark_base_url,
            model_name=model_name or settings.spark_model_name,
            provider_name="spark",
            timeout_seconds=timeout_seconds,
        )


class SparkCompatibleToolCallingLLM(OpenAICompatibleToolCallingLLM):
    """基于 Spark OpenAI 兼容聊天补全的工具调用适配器。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        self.client = SparkCompatibleClient(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
        )
        self.temperature = temperature
