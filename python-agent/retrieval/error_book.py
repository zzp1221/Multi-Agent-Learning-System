"""Lightweight RAG error book stored as append-only YAML."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_FAILURE_TYPES = {
    "missing_chunk",
    "bad_chunk_boundary",
    "missing_alias",
    "dangling_link",
    "low_value_topk",
    "insufficient_hop_evidence",
}

DEFAULT_ERROR_BOOK_PATH = Path(__file__).resolve().parents[2] / "docs" / "rag_error_book.yaml"


def build_error_record(
    *,
    query: str,
    expected: Any,
    top_results: list[Any],
    failure_type: str,
    root_cause: str,
    constraint_rule: str,
    status: str = "open",
    created_at: str | None = None,
) -> dict[str, Any]:
    normalized_failure_type = str(failure_type or "").strip()
    if normalized_failure_type not in ALLOWED_FAILURE_TYPES:
        raise ValueError(f"Unsupported RAG failure type: {failure_type}")
    return {
        "query": str(query or "").strip(),
        "expected": expected,
        "top_results": top_results,
        "failure_type": normalized_failure_type,
        "root_cause": str(root_cause or "").strip(),
        "constraint_rule": str(constraint_rule or "").strip(),
        "status": str(status or "open").strip(),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def append_error_record(path: Path | str, record: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else "errors: []\n"
    if existing.strip() in {"", "errors: []"}:
        prefix = "errors:\n"
    else:
        prefix = existing.rstrip() + "\n"
    target.write_text(prefix + _record_to_yaml_item(record), encoding="utf-8")


def record_error(
    *,
    query: str,
    expected: Any,
    top_results: list[Any],
    failure_type: str,
    root_cause: str,
    constraint_rule: str,
    status: str = "open",
    path: Path | str = DEFAULT_ERROR_BOOK_PATH,
) -> dict[str, Any]:
    record = build_error_record(
        query=query,
        expected=expected,
        top_results=top_results,
        failure_type=failure_type,
        root_cause=root_cause,
        constraint_rule=constraint_rule,
        status=status,
    )
    append_error_record(path, record)
    return record


def _record_to_yaml_item(record: dict[str, Any]) -> str:
    lines = ["- query: " + _yaml_scalar(record.get("query", ""))]
    lines.append("  expected: " + _yaml_value(record.get("expected")))
    lines.append("  top_results: " + _yaml_value(record.get("top_results", [])))
    lines.append("  failure_type: " + _yaml_scalar(record.get("failure_type", "")))
    lines.append("  root_cause: " + _yaml_scalar(record.get("root_cause", "")))
    lines.append("  constraint_rule: " + _yaml_scalar(record.get("constraint_rule", "")))
    lines.append("  status: " + _yaml_scalar(record.get("status", "")))
    lines.append("  created_at: " + _yaml_scalar(record.get("created_at", "")))
    return "\n".join(lines) + "\n"


def _yaml_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "[]"
        rendered = []
        for item in value:
            if isinstance(item, dict):
                rendered.append("{" + ", ".join(f"{key}: {_yaml_scalar(val)}" for key, val in item.items()) + "}")
            else:
                rendered.append(_yaml_scalar(item))
        return "[" + ", ".join(rendered) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key}: {_yaml_scalar(val)}" for key, val in value.items()) + "}"
    return _yaml_scalar(value)


def _yaml_scalar(value: Any) -> str:
    text = str(value if value is not None else "")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
