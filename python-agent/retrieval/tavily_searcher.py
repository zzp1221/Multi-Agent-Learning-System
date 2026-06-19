"""Optional Tavily web search channel for explicit user-enabled retrieval."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.ai_modules.config import get_settings

LOGGER = logging.getLogger(__name__)


class TavilySearcher:
    """Search the public web only when the caller explicitly enables it."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, top_k: int = 5) -> list[tuple]:
        settings = get_settings()
        api_key = self._setting_value(settings, "tavily_api_key", env_name="TAVILY_API_KEY")
        base_url = self._setting_value(
            settings,
            "tavily_base_url",
            env_name="TAVILY_BASE_URL",
            default="https://api.tavily.com/search",
        )
        if not api_key or not query.strip():
            return []

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(top_k, 10)),
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Tavily web search failed for query %r: %s", query, exc)
            return []

        results = data.get("results", [])
        if not isinstance(results, list):
            return []

        normalized: list[tuple] = []
        for rank, item in enumerate(results[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            url = self._clean_string(item.get("url"))
            title = self._clean_string(item.get("title")) or url
            if not url or not title:
                continue
            score = self._coerce_score(item.get("score"), rank)
            snippet = self._clean_string(item.get("content"))
            normalized.append(
                (
                    url,
                    title,
                    score,
                    {
                        "url": url,
                        "snippet": snippet,
                        "sourceTitle": title,
                        "publishedDate": self._clean_string(item.get("published_date")),
                    },
                )
            )
        return normalized

    def _setting_value(self, settings: Any, name: str, *, env_name: str, default: str = "") -> str:
        value = getattr(settings, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return os.getenv(env_name, default).strip()

    def _coerce_score(self, value: Any, rank: int) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 1.0 / max(rank, 1)
        return round(max(score, 0.0), 4)

    def _clean_string(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split()).strip()
