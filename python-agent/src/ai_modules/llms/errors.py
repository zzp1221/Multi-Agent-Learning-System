"""User-facing LLM provider error classification."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


class LLMServiceError(RuntimeError):
    """Classified LLM provider failure safe to show to end users."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.provider = provider
        self.model = model
        self.retryable = retryable

    def payload_kwargs(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            payload["httpStatus"] = self.http_status
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        return payload

    @classmethod
    def from_exception(cls, exc: BaseException | None) -> "LLMServiceError | None":
        seen: set[int] = set()
        current = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, cls):
                return current
            current = current.__cause__ or current.__context__
        return None


def missing_llm_config_error(*, provider: str, model: str | None = None) -> LLMServiceError:
    return LLMServiceError(
        code="LLM_CONFIG_MISSING",
        message="请先在设置页保存模型 API Key 后再使用智能功能。",
        provider=provider,
        model=model,
        retryable=False,
    )


def llm_timeout_error(*, provider: str, model: str | None = None) -> LLMServiceError:
    return LLMServiceError(
        code="LLM_TIMEOUT",
        message="模型响应超时，请稍后重试或切换更快的模型。",
        provider=provider,
        model=model,
        retryable=True,
    )


def llm_transport_error(*, provider: str, model: str | None = None) -> LLMServiceError:
    return LLMServiceError(
        code="LLM_UPSTREAM_UNAVAILABLE",
        message="模型厂商服务暂时不可用，请稍后重试。",
        provider=provider,
        model=model,
        retryable=True,
    )


def raise_for_llm_status(response: httpx.Response, *, provider: str, model: str | None = None) -> None:
    if response.status_code < 400:
        return
    raise classify_llm_http_error(
        status_code=response.status_code,
        response_text=_response_text(response),
        provider=provider,
        model=model,
    )


def classify_llm_http_error(
    *,
    status_code: int,
    response_text: str,
    provider: str,
    model: str | None = None,
) -> LLMServiceError:
    text = _compact_text(response_text)
    lowered = text.lower()
    if _looks_like_quota_error(lowered):
        return _error("LLM_QUOTA_EXHAUSTED", status_code, provider, model)
    if _looks_like_rate_limit_error(lowered) or status_code == 429:
        return _error("LLM_RATE_LIMITED", status_code, provider, model)
    if status_code == 401 or _looks_like_auth_error(lowered):
        return _error("LLM_AUTH_INVALID", status_code, provider, model)
    if status_code == 403 or _looks_like_permission_error(lowered):
        return _error("LLM_PERMISSION_DENIED", status_code, provider, model)
    if _looks_like_context_too_long_error(lowered):
        return _error("LLM_CONTEXT_TOO_LONG", status_code, provider, model)
    if status_code == 404 or _looks_like_model_error(lowered):
        return _error("LLM_MODEL_INVALID", status_code, provider, model)
    if status_code in {408, 504}:
        return _error("LLM_TIMEOUT", status_code, provider, model)
    if status_code >= 500:
        return _error("LLM_UPSTREAM_UNAVAILABLE", status_code, provider, model)
    return _error("LLM_REQUEST_INVALID", status_code, provider, model)


def _error(code: str, status_code: int, provider: str, model: str | None) -> LLMServiceError:
    messages = {
        "LLM_RATE_LIMITED": "模型服务当前限流，请稍后再试，或切换到其他模型。",
        "LLM_QUOTA_EXHAUSTED": "当前模型额度不足，请检查 API Key 额度或更换模型配置。",
        "LLM_AUTH_INVALID": "API Key 无效或已过期，请在设置页重新保存模型配置。",
        "LLM_PERMISSION_DENIED": "当前 API Key 没有调用该模型的权限，请切换模型或检查厂商权限。",
        "LLM_MODEL_INVALID": "当前模型名称不可用，请在设置页选择可用模型后重试。",
        "LLM_CONTEXT_TOO_LONG": "本次输入或上下文过长，请缩短问题或开启新对话后重试。",
        "LLM_UPSTREAM_UNAVAILABLE": "模型厂商服务暂时不可用，请稍后重试。",
        "LLM_TIMEOUT": "模型响应超时，请稍后重试或切换更快的模型。",
        "LLM_REQUEST_INVALID": "模型请求参数不被厂商接受，请检查模型配置后重试。",
    }
    return LLMServiceError(
        code=code,
        message=messages[code],
        http_status=status_code,
        provider=provider,
        model=model,
        retryable=code in {"LLM_RATE_LIMITED", "LLM_UPSTREAM_UNAVAILABLE", "LLM_TIMEOUT"},
    )


def _response_text(response: httpx.Response) -> str:
    try:
        return response.text
    except httpx.ResponseNotRead:
        return ""


def _compact_text(value: str, limit: int = 2000) -> str:
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        text = value
    else:
        text = json.dumps(parsed, ensure_ascii=False)
    return text[:limit]


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _looks_like_quota_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"insufficient[_ -]?quota",
            r"quota",
            r"billing",
            r"credit",
            r"balance",
            r"not enough",
            r"额度",
            r"余额",
            r"欠费",
        ),
    )


def _looks_like_rate_limit_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"rate[_ -]?limit",
            r"too many requests",
            r"\b429\b",
            r"限流",
            r"请求过于频繁",
        ),
    )


def _looks_like_auth_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"invalid api key",
            r"invalid token",
            r"unauthorized",
            r"authentication",
            r"鉴权",
            r"认证",
            r"密钥",
        ),
    )


def _looks_like_permission_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"permission",
            r"forbidden",
            r"access denied",
            r"no access",
            r"not authorized",
            r"权限",
            r"无权",
        ),
    )


def _looks_like_context_too_long_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"context[_ -]?length",
            r"context window",
            r"maximum context",
            r"too many tokens",
            r"token limit",
            r"上下文",
            r"输入.*过长",
            r"超过.*长度",
        ),
    )


def _looks_like_model_error(text: str) -> bool:
    return _matches(
        text,
        (
            r"model .*not found",
            r"model .*does not exist",
            r"unknown model",
            r"unsupported model",
            r"invalid model",
            r"模型.*不存在",
            r"模型.*不可用",
            r"模型.*不支持",
        ),
    )
