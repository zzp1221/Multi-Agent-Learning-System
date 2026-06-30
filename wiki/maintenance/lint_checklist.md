---
title: LLM-Wiki Lint Checklist
tags: [llm-wiki, lint]
updated_at: 2026-06-29
---

# Lint Checklist

Run this checklist before large imports, after bulk wiki generation, and after GraphRAG repair batches.

## Structure

- [ ] Root [[index]] lists major course folders and maintenance entry points.
- [ ] Root [[schema]] matches current import/vectorization behavior.
- [ ] Root [[log]] has an entry for the latest ingest/repair/lint batch.
- [ ] Maintenance directories are excluded from RAG import.

## Page Quality

- [ ] Every compiled page has `title`, `course`, `chapter`, `difficulty`, `tags`, `aliases`, `source`, `updated_at`.
- [ ] Pages have at least one meaningful paragraph after headings.
- [ ] Important concepts mentioned repeatedly have their own page.
- [ ] Duplicate or near-duplicate pages are merged or intentionally cross-linked.

## Link Health

- [ ] No high-value `[[wikilinks]]` point to missing pages.
- [ ] New comparison pages link both directions to compared concepts.
- [ ] Prerequisite and implementation links are explicit, not inferred only from shared tags.
- [ ] GraphRAG repair links have a reason in [[error_book]].

## Source Health

- [ ] New raw sources are registered in [[raw/source_registry]].
- [ ] High-impact claims cite stable source IDs or clear bibliographic source text.
- [ ] Contradictions and superseded claims are recorded before being resolved.

## Retrieval Health

- [ ] `wiki_search` can find pages by title, alias, and common English abbreviation.
- [ ] `wiki_read` returns relevant chunks for long pages.
- [ ] GraphRAG benchmark failures are filed in [[error_book]] or the project-level RAG error book.
