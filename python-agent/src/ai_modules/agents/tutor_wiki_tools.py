"""Wiki tool helpers used by TutorAgent."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from retrieval.wiki_tools import graph_intent_allows_wiki_tools

LOGGER = logging.getLogger(__name__)

MAX_WIKI_TOOL_STEPS = 3


def build_wiki_tool_protocol(
    params: dict[str, Any],
    *,
    resolve_graph_intent: Callable[[dict[str, Any]], str],
) -> str:
    intent = resolve_graph_intent(params)
    if intent == "PREREQUISITE_PATH":
        return (
            "Wiki graph tool protocol: first read the seed page, then use wiki_neighbors "
            "for prerequisite/follow-up evidence. Use at most 3 wiki tool steps. Treat wiki "
            "results as evidence enhancement, not as a replacement for retrieval evidence.\n\n"
        )
    if intent == "MULTI_HOP_RELATION":
        return (
            "Wiki graph tool protocol: explain the relation chain around seed pages and "
            "neighbors. Use at most 3 wiki tool steps. Do not present candidate edges as a "
            "strict verified path unless the evidence explicitly supports it.\n\n"
        )
    if intent == "COMPARISON":
        return (
            "Wiki graph tool protocol: read the comparison object pages and key chunks, then "
            "summarize common points, differences, and boundaries. Use at most 3 wiki tool "
            "steps and keep retrieval evidence as the primary grounding.\n\n"
        )
    return ""


def wiki_tools_enabled(
    params: dict[str, Any],
    *,
    resolve_graph_intent: Callable[[dict[str, Any]], str],
) -> bool:
    return graph_intent_allows_wiki_tools(resolve_graph_intent(params))


def tool_wiki_search(
    *,
    tool_input: dict[str, Any],
    params: dict[str, Any],
    wiki_toolset_cls: type,
    wiki_db_config: Callable[[], dict[str, Any]],
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    started = perf_counter()
    allowed, reason = claim_wiki_tool_step(params, tools_enabled=tools_enabled)
    if not allowed:
        result = {"enabled": False, "reason": reason}
        record_wiki_tool_call(
            params=params,
            tool_name="wiki_search",
            tool_input=tool_input,
            result=result,
            elapsed_ms=elapsed_ms(started),
            tools_enabled=tools_enabled,
        )
        return result
    query = str(tool_input.get("query") or params.get("rewrittenQuery") or params.get("query") or "").strip()
    limit = bounded_int(tool_input.get("limit"), default=5, minimum=1, maximum=8)
    try:
        result = {"enabled": True, **wiki_toolset_cls(wiki_db_config()).wiki_search(query, limit=limit)}
    except Exception as exc:
        LOGGER.warning("wiki_search failed: %s", exc)
        result = {"enabled": True, "error": f"{type(exc).__name__}: {exc}", "results": []}
    record_wiki_tool_call(
        params=params,
        tool_name="wiki_search",
        tool_input={"query": query, "limit": limit},
        result=result,
        elapsed_ms=elapsed_ms(started),
        tools_enabled=tools_enabled,
    )
    return result


def tool_wiki_read(
    *,
    tool_input: dict[str, Any],
    params: dict[str, Any],
    wiki_toolset_cls: type,
    wiki_db_config: Callable[[], dict[str, Any]],
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    started = perf_counter()
    allowed, reason = claim_wiki_tool_step(params, tools_enabled=tools_enabled)
    if not allowed:
        result = {"enabled": False, "reason": reason}
        record_wiki_tool_call(
            params=params,
            tool_name="wiki_read",
            tool_input=tool_input,
            result=result,
            elapsed_ms=elapsed_ms(started),
            tools_enabled=tools_enabled,
        )
        return result
    slug = str(tool_input.get("slug") or "").strip()
    chunk_limit = bounded_int(tool_input.get("chunkLimit"), default=3, minimum=1, maximum=5)
    try:
        result = {"enabled": True, **wiki_toolset_cls(wiki_db_config()).wiki_read(slug, chunk_limit=chunk_limit)}
    except Exception as exc:
        LOGGER.warning("wiki_read failed: %s", exc)
        result = {"enabled": True, "error": f"{type(exc).__name__}: {exc}", "found": False}
    record_wiki_tool_call(
        params=params,
        tool_name="wiki_read",
        tool_input={"slug": slug, "chunkLimit": chunk_limit},
        result=result,
        elapsed_ms=elapsed_ms(started),
        tools_enabled=tools_enabled,
    )
    return result


def tool_wiki_neighbors(
    *,
    tool_input: dict[str, Any],
    params: dict[str, Any],
    wiki_toolset_cls: type,
    wiki_db_config: Callable[[], dict[str, Any]],
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    started = perf_counter()
    allowed, reason = claim_wiki_tool_step(params, tools_enabled=tools_enabled)
    if not allowed:
        result = {"enabled": False, "reason": reason}
        record_wiki_tool_call(
            params=params,
            tool_name="wiki_neighbors",
            tool_input=tool_input,
            result=result,
            elapsed_ms=elapsed_ms(started),
            tools_enabled=tools_enabled,
        )
        return result
    slug = str(tool_input.get("slug") or "").strip()
    relation_type = str(tool_input.get("relationType") or "").strip() or None
    limit = bounded_int(tool_input.get("limit"), default=8, minimum=1, maximum=12)
    try:
        result = {
            "enabled": True,
            **wiki_toolset_cls(wiki_db_config()).wiki_neighbors(
                slug,
                relation_type=relation_type,
                limit=limit,
            ),
        }
    except Exception as exc:
        LOGGER.warning("wiki_neighbors failed: %s", exc)
        result = {"enabled": True, "error": f"{type(exc).__name__}: {exc}", "outgoing": [], "incoming": []}
    record_wiki_tool_call(
        params=params,
        tool_name="wiki_neighbors",
        tool_input={"slug": slug, "relationType": relation_type, "limit": limit},
        result=result,
        elapsed_ms=elapsed_ms(started),
        tools_enabled=tools_enabled,
    )
    return result


def claim_wiki_tool_step(
    params: dict[str, Any],
    *,
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> tuple[bool, str]:
    if not tools_enabled(params):
        return False, "wiki tools are limited to graph-aware intents"
    current_steps = bounded_int(params.get("wikiToolStepCount"), default=0, minimum=0, maximum=MAX_WIKI_TOOL_STEPS)
    if current_steps >= MAX_WIKI_TOOL_STEPS:
        return False, "wiki tool traversal is limited to 3 steps"
    params["wikiToolStepCount"] = current_steps + 1
    return True, ""


def record_wiki_tool_call(
    *,
    params: dict[str, Any],
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
    elapsed_ms: float,
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> None:
    calls = params.setdefault("wikiToolCalls", [])
    if not isinstance(calls, list):
        calls = []
        params["wikiToolCalls"] = calls
    summary = {
        "tool": tool_name,
        "query": tool_input.get("query"),
        "slug": tool_input.get("slug"),
        "relationType": tool_input.get("relationType"),
        "elapsedMs": round(elapsed_ms, 2),
        "enabled": result.get("enabled") is not False,
        "hitCount": wiki_result_hit_count(tool_name=tool_name, result=result),
    }
    if result.get("reason"):
        summary["disabled"] = str(result["reason"])
    if result.get("error"):
        summary["error"] = str(result["error"])
    calls.append(summary)
    sync_wiki_traversal_diagnostics(params, tools_enabled=tools_enabled)


def wiki_result_hit_count(*, tool_name: str, result: dict[str, Any]) -> int:
    if tool_name == "wiki_search":
        values = result.get("results", [])
        return len(values) if isinstance(values, list) else 0
    if tool_name == "wiki_read":
        chunks = result.get("chunks", [])
        incoming = result.get("incoming", [])
        outgoing = result.get("outgoing", [])
        return sum(len(value) for value in (chunks, incoming, outgoing) if isinstance(value, list))
    if tool_name == "wiki_neighbors":
        incoming = result.get("incoming", [])
        outgoing = result.get("outgoing", [])
        return sum(len(value) for value in (incoming, outgoing) if isinstance(value, list))
    return 0


def build_wiki_traversal_diagnostics(
    params: dict[str, Any],
    *,
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    calls = params.get("wikiToolCalls", [])
    calls = calls if isinstance(calls, list) else []
    diagnostics = {
        "enabled": tools_enabled(params),
        "stepCount": bounded_int(params.get("wikiToolStepCount"), default=0, minimum=0, maximum=MAX_WIKI_TOOL_STEPS),
        "wiki_search_ms": 0.0,
        "wiki_read_ms": 0.0,
        "wiki_neighbors_ms": 0.0,
        "errors": [],
        "calls": [],
    }
    for call in calls:
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool") or "")
        key = f"{tool_name}_ms"
        if key in diagnostics:
            diagnostics[key] = round(float(diagnostics[key]) + safe_elapsed_ms(call.get("elapsedMs")), 2)
        if call.get("error"):
            diagnostics["errors"].append(str(call["error"]))
        elif call.get("disabled"):
            diagnostics["errors"].append(str(call["disabled"]))
        diagnostics["calls"].append(
            {
                "tool": tool_name,
                "query": call.get("query"),
                "slug": call.get("slug"),
                "relationType": call.get("relationType"),
                "enabled": call.get("enabled") is not False,
                "hitCount": int(call.get("hitCount") or 0),
            }
        )
    return diagnostics


def sync_wiki_traversal_diagnostics(
    params: dict[str, Any],
    *,
    tools_enabled: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    diagnostics = build_wiki_traversal_diagnostics(params, tools_enabled=tools_enabled)
    params["wikiTraversal"] = diagnostics
    raw_result = params.get("retrievalRawResult")
    if isinstance(raw_result, dict):
        graph_diagnostics = raw_result.setdefault("graphDiagnostics", {})
        if isinstance(graph_diagnostics, dict):
            graph_diagnostics["wikiTraversal"] = diagnostics
    return diagnostics


def perf_counter() -> float:
    return time.perf_counter()


def elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


def safe_elapsed_ms(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
