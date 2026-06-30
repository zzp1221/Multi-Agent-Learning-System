---
title: LLM-Wiki Schema
tags: [llm-wiki, schema, governance]
updated_at: 2026-06-29
---

# LLM-Wiki Schema

This file defines how agents maintain the wiki. It follows the LLM-Wiki pattern: immutable raw sources, LLM-maintained compiled wiki pages, and explicit maintenance operations.

## Layers

| Layer | Path | Ownership | Rule |
|---|---|---|---|
| Raw sources | `wiki/raw/` | Human curated, immutable | Do not rewrite source content; register sources in [[raw/source_registry]] |
| Compiled wiki | course folders under `wiki/` | LLM maintained | Create, update, link, and repair pages using this schema |
| Maintenance schema | root + `maintenance/` + `templates/` | Human + LLM co-maintained | Tracks conventions, logs, lint, and repair queue |

## Page Types

| Type | Location | Required frontmatter |
|---|---|---|
| Concept page | course folders | `title`, `course`, `chapter`, `difficulty`, `tags`, `aliases`, `source`, `updated_at` |
| Source note | `wiki/raw/sources/` | `source_id`, `title`, `source_type`, `status`, `added_at` |
| Maintenance page | `wiki/maintenance/` or root | `title`, `tags`, `updated_at` |
| Template | `wiki/templates/` | `title`, `tags`, `updated_at` |

## Concept Page Contract

Compiled concept pages should use this shape unless the topic clearly requires otherwise:

1. YAML frontmatter.
2. `## 核心定义`
3. `## 关键结论`
4. `## 易错点`
5. Domain-specific sections, examples, code, formulas, or diagrams.
6. `## 相关链接` with explicit `[[wikilinks]]`.
7. `## 来源` when claim-level source IDs are available.

## Source Rules

- Prefer stable `source_id` values from [[raw/source_registry]] over free-text citations.
- Raw source files are append-only or replace-by-new-version. Do not silently edit old source notes.
- If a new source contradicts a compiled page, update the page and add a note to [[maintenance/error_book]] or [[log]].
- Claims without a source are allowed only for low-risk textbook knowledge and should be marked for future provenance improvement when they affect retrieval answers.

## Link Rules

- Use `[[Page Title]]` or `[[Folder/Page Title]]` for durable internal links.
- Add links for prerequisites, comparisons, implementations, common confusions, and cross-layer relations.
- Avoid low-value links based only on shared generic tags such as "概述", "基础", or "原理".
- When adding graph repair links from benchmark failures, record the reason in [[maintenance/error_book]].

## Ingest Workflow

1. Add or register the raw source in [[raw/source_registry]].
2. Read the source and identify new concepts, updates, contradictions, and missing links.
3. Create or update compiled concept pages using [[templates/concept_page_template]].
4. Add or repair `[[wikilinks]]` on affected pages.
5. Update [[index]] if a new major area or synthesis page is created.
6. Append one entry to [[log]].
7. Run relevant RAG/wiki tests before importing or vectorizing.

## Query Workflow

1. Start from [[index]] or tool search.
2. Read candidate pages and follow explicit links for multi-hop evidence.
3. Prefer compiled pages over raw sources for normal answers.
4. Use raw sources only to verify provenance, resolve contradiction, or ingest new knowledge.
5. File reusable answers back into the compiled wiki when they add lasting value.

## Lint Workflow

Run the checklist in [[maintenance/lint_checklist]] periodically. Record findings in [[maintenance/error_book]] and append a summary to [[log]].

## RAG Import Boundary

The following are metadata and must not be imported as compiled knowledge pages:

- `index.md`
- `schema.md`
- `log.md`
- `README.md`
- `raw/**`
- `maintenance/**`
- `templates/**`
- `assets/**`
