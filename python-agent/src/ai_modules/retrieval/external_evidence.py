"""Shared external evidence normalization for web-augmented answers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.ai_modules.retrieval.evidence_relevance import (
    evidence_title,
    evidence_url,
    select_relevant_evidence,
)


def build_external_evidence_contract(
    *,
    query: str,
    documents: list[Any],
    web_items: Iterable[Any],
    limit: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    """Return the single auditable source contract used by reasoning and answers."""

    candidates = _collect_candidates(documents=documents, web_items=web_items)
    selected = select_relevant_evidence(query=query, documents=candidates, limit=limit)
    adopted_urls = {_normalize_url(item.get("url")) for item in selected.adopted}
    ignored = _build_ignored_sources(candidates=candidates, adopted_urls=adopted_urls)
    return {
        "adoptedExternalSources": [_public_source(item, index) for index, item in enumerate(selected.adopted, 1)],
        "ignoredExternalSources": ignored,
        "evidenceIds": [str(item.get("id") or "").strip() for item in selected.adopted if str(item.get("id") or "").strip()],
        "externalUrls": [str(item.get("url") or "").strip() for item in selected.adopted if str(item.get("url") or "").strip()],
    }


def _collect_candidates(*, documents: list[Any], web_items: Iterable[Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add_resource(
        *,
        title: Any,
        url: Any,
        snippet: Any = "",
        source_title: Any = "",
        published_date: Any = "",
        score: Any = None,
        channel: Any = "",
        source: str,
    ) -> None:
        normalized_url = _normalize_url(url)
        if not normalized_url:
            return
        dedupe_key = normalized_url.lower()
        if dedupe_key in seen_urls:
            return
        seen_urls.add(dedupe_key)
        clean_title = str(title or source_title or normalized_url).strip()
        resources.append(
            {
                "id": f"ext-{len(resources) + 1}",
                "title": clean_title,
                "url": normalized_url,
                "snippet": str(snippet or "").strip(),
                "sourceTitle": str(source_title or title or "").strip(),
                "publishedDate": str(published_date or "").strip(),
                "score": _safe_float(score),
                "channel": str(channel or "").strip(),
                "source": source,
            }
        )

    for document in documents:
        if not isinstance(document, dict):
            continue
        add_resource(
            title=document.get("title"),
            url=document.get("url"),
            snippet=document.get("snippet") or document.get("evidence"),
            source_title=document.get("sourceTitle") or document.get("source_title"),
            published_date=document.get("publishedDate") or document.get("published_date"),
            score=document.get("score"),
            channel=document.get("channel"),
            source="retrievalResult",
        )

    for item in web_items:
        if isinstance(item, dict):
            add_resource(
                title=item.get("title") or item.get("sourceTitle"),
                url=item.get("url") or item.get("slug"),
                snippet=item.get("snippet") or item.get("evidence") or item.get("content"),
                source_title=item.get("sourceTitle"),
                published_date=item.get("publishedDate"),
                score=item.get("score"),
                channel=item.get("channel") or "web",
                source="webRetrievalResult",
            )
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        metadata = next((extra for extra in item[3:] if isinstance(extra, dict)), {})
        add_resource(
            title=item[1],
            url=metadata.get("url") or item[0],
            snippet=metadata.get("snippet") or metadata.get("content"),
            source_title=metadata.get("sourceTitle") or item[1],
            published_date=metadata.get("publishedDate"),
            score=item[2] if len(item) > 2 else None,
            channel="web",
            source="webRetrievalResult",
        )
    return resources


def _build_ignored_sources(
    *,
    candidates: list[dict[str, Any]],
    adopted_urls: set[str],
) -> list[dict[str, Any]]:
    ignored: list[dict[str, Any]] = []
    for item in candidates:
        url = _normalize_url(item.get("url"))
        if not url or url in adopted_urls:
            continue
        ignored.append(
            {
                "title": str(item.get("title") or url).strip(),
                "url": url,
                "reason": "相关性不足或未进入融合结果",
            }
        )
    return ignored


def _public_source(item: dict[str, Any], index: int) -> dict[str, Any]:
    url = evidence_url(item)
    return {
        "citationId": f"S{index}",
        "id": str(item.get("id") or f"ext-{index}"),
        "title": evidence_title(item) or url,
        "url": url,
        "snippet": str(item.get("snippet") or item.get("evidence") or "").strip(),
        "sourceTitle": str(item.get("sourceTitle") or item.get("title") or "").strip(),
        "publishedDate": str(item.get("publishedDate") or "").strip(),
        "score": _safe_float(item.get("score")),
        "reason": "通过联网检索且与当前问题相关",
    }


def _normalize_url(value: Any) -> str:
    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
