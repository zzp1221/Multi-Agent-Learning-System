"""Generate offline RAG repair proposals from benchmark low-evidence records."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from retrieval.error_book import build_error_record, write_error_records


FAILURE_TYPE_BY_REASON = {
    "missingAlias": "missing_alias",
    "missingGraphEdge": "dangling_link",
    "resourceSlugCompeting": "low_value_topk",
}

ROOT_CAUSE_BY_FAILURE = {
    "missing_alias": "expected evidence has a likely alias or canonicalization mismatch",
    "dangling_link": "expected evidence is absent from graph seed/candidate coverage",
    "low_value_topk": "low-value resource slug competes with canonical wiki evidence",
    "insufficient_hop_evidence": "retrieval did not collect all graph evidence nodes in top-k",
}

CONSTRAINT_BY_FAILURE = {
    "missing_alias": "prefer adding aliases or canonical rules before widening top-k",
    "dangling_link": "add reproducible wiki graph relations only after slug validation",
    "low_value_topk": "tighten low-value source handling without suppressing canonical wiki pages",
    "insufficient_hop_evidence": "change graph expansion only when benchmark latency remains within threshold",
}


def load_benchmark_report(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_error_book(path: Path | str, records: list[dict[str, Any]]) -> None:
    write_error_records(path, records)


def propose_error_records_from_report(report: dict[str, Any], *, source_report: str = "") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    full_records = _records_by_id(report)
    for raw_item in _iter_low_evidence_items(report):
        item = _enrich_low_evidence_item(raw_item, full_records.get(str(raw_item.get("id") or "")))
        proposal = build_repair_proposal(item)
        failure_type = proposal["failureType"]
        records.append(
            build_error_record(
                record_id=f"rag-{item.get('id')}-{failure_type}",
                source_report=source_report,
                question_id=str(item.get("id") or ""),
                graph_intent=str(item.get("graphIntent") or ""),
                query=str(item.get("question") or ""),
                expected={"slugs": item.get("missingEvidenceSlugsTop5", [])},
                expected_slugs=list(item.get("missingEvidenceSlugsTop5", [])),
                top_results=[{"slug": slug, "rank": rank + 1} for rank, slug in enumerate(item.get("topSlugs", []))],
                failure_type=failure_type,
                reason_candidates=item.get("reasonCandidates", {}),
                root_cause=ROOT_CAUSE_BY_FAILURE[failure_type],
                constraint_rule=CONSTRAINT_BY_FAILURE[failure_type],
                proposal=proposal,
                verification={
                    "commands": [
                        "python-agent/.venv/Scripts/python.exe -m pytest python-agent/tests/test_wiki_chunking_tools_error_book.py python-agent/tests/test_graph_intent_plumbing.py -q",
                        "python-agent/.venv/Scripts/python.exe python-agent/knowledge/benchmark_graph_rag_100.py --output python-agent/reports/graph_rag_100_current.json --judge-cache python-agent/reports/graph_rag_100_judge_cache.json",
                    ],
                    "qualityGate": "overallPass=true and no latency regression against graph thresholds",
                },
                status="proposed",
            )
        )
    return dedupe_records(records)


def _records_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = report.get("records")
    if not isinstance(records, list):
        return {}
    return {
        str(item.get("id") or ""): item
        for item in records
        if isinstance(item, dict) and item.get("id")
    }


def _enrich_low_evidence_item(item: dict[str, Any], full_record: dict[str, Any] | None) -> dict[str, Any]:
    if not full_record:
        return dict(item)
    enriched = dict(item)
    diagnostics = full_record.get("diagnostics") if isinstance(full_record.get("diagnostics"), dict) else {}
    if not enriched.get("question"):
        enriched["question"] = full_record.get("question") or ""
    if not enriched.get("graphSeedSlugs"):
        enriched["graphSeedSlugs"] = list(diagnostics.get("graphSeedSlugs") or [])
    if not enriched.get("topSlugs") and isinstance(full_record.get("top"), list):
        enriched["topSlugs"] = [
            str(candidate.get("slug") or "")
            for candidate in full_record["top"]
            if isinstance(candidate, dict) and candidate.get("slug")
        ]
    return enriched


def build_repair_proposal(item: dict[str, Any]) -> dict[str, Any]:
    reasons = item.get("reasonCandidates", {})
    reasons = reasons if isinstance(reasons, dict) else {}
    failure_type = _failure_type_for_reasons(reasons)
    missing_slugs = list(item.get("missingEvidenceSlugsTop5", []))
    seed_slugs = list(item.get("graphSeedSlugs", []))
    proposal_type = {
        "missing_alias": "add_alias_or_canonical_rule",
        "dangling_link": "add_wikilink",
        "low_value_topk": "adjust_low_value_filter",
        "insufficient_hop_evidence": "adjust_graph_expansion",
    }[failure_type]
    proposal: dict[str, Any] = {
        "type": proposal_type,
        "failureType": failure_type,
        "missingSlugs": missing_slugs,
        "reasonCandidates": reasons,
    }
    if proposal_type == "add_wikilink":
        proposal["candidateLinks"] = candidate_wikilinks(seed_slugs, missing_slugs)
    return proposal


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("id") or "")
        if not key:
            key = "|".join(
                [
                    str(record.get("question_id") or ""),
                    str(record.get("failure_type") or ""),
                    ",".join(record.get("expected_slugs", [])),
                ]
            )
        deduped.setdefault(key, record)
    return list(deduped.values())


def candidate_wikilinks(seed_slugs: list[str], missing_slugs: list[str]) -> list[list[str]]:
    links = []
    for seed in seed_slugs:
        for missing in missing_slugs:
            if seed and missing and seed != missing:
                links.append([seed, missing])
    return links


def _iter_low_evidence_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    grouped = report.get("lowEvidenceRecordsByIntent")
    if isinstance(grouped, dict):
        for records in grouped.values():
            if isinstance(records, list):
                items.extend(item for item in records if isinstance(item, dict))
    fallback = report.get("lowEvidenceRecords")
    if not items and isinstance(fallback, list):
        items.extend(item for item in fallback if isinstance(item, dict))
    return items


def _failure_type_for_reasons(reasons: dict[str, Any]) -> str:
    for reason, failure_type in FAILURE_TYPE_BY_REASON.items():
        if reasons.get(reason) is True:
            return failure_type
    return "insufficient_hop_evidence"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate RAG Error Book proposals from a graph benchmark report.")
    parser.add_argument("--report", required=True, help="Path to graph_rag_100_*.json")
    parser.add_argument("--output", required=True, help="Path to write rag_error_book YAML")
    args = parser.parse_args()

    report_path = Path(args.report)
    report = load_benchmark_report(report_path)
    records = propose_error_records_from_report(report, source_report=report_path.name)
    write_error_book(args.output, records)
    print(f"Wrote {len(records)} proposal(s) to {args.output}")


if __name__ == "__main__":
    main()
