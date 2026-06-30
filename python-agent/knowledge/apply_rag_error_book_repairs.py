"""Build reproducible graph repair links from RAG Error Book proposals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge.graph_low_evidence_repairs import canonical_slug_key
from knowledge.wiki_file_filter import iter_content_wiki_markdown
from retrieval.error_book import load_error_records

DEFAULT_ERROR_BOOK = REPO_ROOT / "docs" / "rag_error_book.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / "knowledge" / "graph_low_evidence_repairs.json"
DEFAULT_WIKI_ROOT = REPO_ROOT / "wiki"


def load_wiki_slug_index(wiki_root: Path | str = DEFAULT_WIKI_ROOT) -> dict[str, str]:
    """Return canonical-key -> original slug for local wiki markdown pages."""
    root = Path(wiki_root)
    index: dict[str, str] = {}
    for path in iter_content_wiki_markdown(root):
        slug = str(path.relative_to(root)).replace("\\", "/").removesuffix(".md")
        index[canonical_slug_key(slug)] = slug
    return index


def build_repair_payload(
    records: list[dict[str, Any]],
    *,
    wiki_slug_index: dict[str, str],
    source_report: str = "",
) -> dict[str, Any]:
    repairs: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for record in records:
        proposal = record.get("proposal") if isinstance(record.get("proposal"), dict) else {}
        if proposal.get("type") != "add_wikilink":
            continue
        links = []
        for raw_link in proposal.get("candidateLinks", []):
            normalized = _normalize_link(raw_link, wiki_slug_index)
            if not normalized:
                continue
            from_slug, to_slug, semantic_relation = normalized
            key = (from_slug, to_slug, semantic_relation)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            links.append([from_slug, to_slug, semantic_relation])
        if not links:
            continue
        repairs.append(
            {
                "id": str(record.get("question_id") or record.get("id") or "").replace("rag-", ""),
                "graphIntent": str(record.get("graph_intent") or ""),
                "links": links,
            }
        )
    return {
        "sourceReport": source_report,
        "scope": "ragErrorBook.add_wikilink",
        "repairs": repairs,
    }


def _normalize_link(raw_link: Any, wiki_slug_index: dict[str, str]) -> tuple[str, str, str] | None:
    if not isinstance(raw_link, list) or len(raw_link) < 2:
        return None
    from_slug = _resolve_slug(raw_link[0], wiki_slug_index)
    to_slug = _resolve_slug(raw_link[1], wiki_slug_index)
    if not from_slug or not to_slug or from_slug == to_slug:
        return None
    semantic_relation = str(raw_link[2] if len(raw_link) >= 3 else "WIKILINK").strip().upper() or "WIKILINK"
    return from_slug, to_slug, semantic_relation


def _resolve_slug(value: Any, wiki_slug_index: dict[str, str]) -> str:
    key = canonical_slug_key(value)
    return wiki_slug_index.get(key, "")


def write_repair_payload(path: Path | str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply validated RAG Error Book wikilink repairs.")
    parser.add_argument("--error-book", type=Path, default=DEFAULT_ERROR_BOOK)
    parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    records = load_error_records(args.error_book)
    wiki_slug_index = load_wiki_slug_index(args.wiki_root)
    payload = build_repair_payload(
        records,
        wiki_slug_index=wiki_slug_index,
        source_report=args.error_book.name,
    )
    write_repair_payload(args.output, payload)
    repair_count = len(payload["repairs"])
    edge_count = sum(len(item["links"]) for item in payload["repairs"])
    print(f"Wrote {repair_count} repair record(s), {edge_count} edge(s) to {args.output}")


if __name__ == "__main__":
    main()
