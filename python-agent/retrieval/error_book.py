"""Lightweight RAG error book stored as append-only YAML."""

from __future__ import annotations

import json
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
    record_id: str | None = None,
    source_report: str | None = None,
    question_id: str | None = None,
    graph_intent: str | None = None,
    reason_candidates: dict[str, Any] | None = None,
    expected_slugs: list[str] | None = None,
    proposal: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_failure_type = str(failure_type or "").strip()
    if normalized_failure_type not in ALLOWED_FAILURE_TYPES:
        raise ValueError(f"Unsupported RAG failure type: {failure_type}")
    record = {
        "query": str(query or "").strip(),
        "expected": expected,
        "top_results": top_results,
        "failure_type": normalized_failure_type,
        "root_cause": str(root_cause or "").strip(),
        "constraint_rule": str(constraint_rule or "").strip(),
        "status": str(status or "open").strip(),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    optional_fields = {
        "id": str(record_id or "").strip(),
        "source_report": str(source_report or "").strip(),
        "question_id": str(question_id or "").strip(),
        "graph_intent": str(graph_intent or "").strip(),
        "reason_candidates": reason_candidates or {},
        "expected_slugs": expected_slugs or [],
        "proposal": proposal or {},
        "verification": verification or {},
    }
    for key, value in optional_fields.items():
        if value:
            record[key] = value
    return record


def append_error_record(path: Path | str, record: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else "errors: []\n"
    if existing.strip() in {"", "errors: []"}:
        prefix = "errors:\n"
    else:
        prefix = existing.rstrip() + "\n"
    target.write_text(prefix + _record_to_yaml_item(record), encoding="utf-8")


def write_error_records(path: Path | str, records: list[dict[str, Any]]) -> None:
    """Rewrite the error book with records using this module's YAML subset."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        target.write_text("errors: []\n", encoding="utf-8")
        return
    target.write_text(
        "errors:\n" + "".join(_record_to_yaml_item(record) for record in records),
        encoding="utf-8",
    )


def load_error_records(path: Path | str = DEFAULT_ERROR_BOOK_PATH) -> list[dict[str, Any]]:
    """Load records written by this module's compact YAML subset."""
    target = Path(path)
    if not target.exists():
        return []
    text = target.read_text(encoding="utf-8")
    if text.strip() in {"", "errors: []"}:
        return []

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line == "errors:":
            continue
        if line.startswith("- "):
            if current is not None:
                records.append(current)
            current = {}
            key, value = _split_yaml_pair(line[2:])
            current[key] = _parse_yaml_value(value)
            continue
        if current is not None and line.startswith("  "):
            key, value = _split_yaml_pair(line.strip())
            current[key] = _parse_yaml_value(value)
    if current is not None:
        records.append(current)
    return records


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
    record_id: str | None = None,
    source_report: str | None = None,
    question_id: str | None = None,
    graph_intent: str | None = None,
    reason_candidates: dict[str, Any] | None = None,
    expected_slugs: list[str] | None = None,
    proposal: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = build_error_record(
        query=query,
        expected=expected,
        top_results=top_results,
        failure_type=failure_type,
        root_cause=root_cause,
        constraint_rule=constraint_rule,
        status=status,
        record_id=record_id,
        source_report=source_report,
        question_id=question_id,
        graph_intent=graph_intent,
        reason_candidates=reason_candidates,
        expected_slugs=expected_slugs,
        proposal=proposal,
        verification=verification,
    )
    append_error_record(path, record)
    return record


def _record_to_yaml_item(record: dict[str, Any]) -> str:
    preferred_order = [
        "id",
        "source_report",
        "question_id",
        "graph_intent",
        "query",
        "expected",
        "expected_slugs",
        "top_results",
        "failure_type",
        "reason_candidates",
        "root_cause",
        "constraint_rule",
        "proposal",
        "verification",
        "status",
        "created_at",
    ]
    ordered_keys = [key for key in preferred_order if key in record]
    ordered_keys.extend(key for key in record if key not in ordered_keys)
    first_key = ordered_keys[0] if ordered_keys else "query"
    lines = [f"- {first_key}: {_yaml_value(record.get(first_key))}"]
    for key in ordered_keys[1:]:
        lines.append(f"  {key}: {_yaml_value(record.get(key))}")
    return "\n".join(lines) + "\n"


def _yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _split_yaml_pair(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line.strip(), '""'
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_yaml_value(value: str) -> Any:
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip().strip('"')
