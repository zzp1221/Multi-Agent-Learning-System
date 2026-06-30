---
title: Source Registry
tags: [llm-wiki, sources, provenance]
updated_at: 2026-06-29
---

# Source Registry

Use stable IDs when citing sources in compiled pages.

| source_id | title | type | status | notes |
|---|---|---|---|---|
| src-clrs | Introduction to Algorithms | book | existing-free-text | Existing pages cite CLRS in free text; convert gradually |
| src-dbsc | Database System Concepts | book | existing-free-text | Existing database pages cite this source in free text |
| src-cod | Computer Organization and Design | book | existing-free-text | Existing computer organization pages cite this source in free text |
| src-tap-l | Types and Programming Languages | book | existing-free-text | Existing PL pages cite Pierce in free text |

## Registry Rules

- `source_id` format: `src-` plus lowercase kebab-case identifier.
- `status` values: `registered`, `existing-free-text`, `needs-review`, `deprecated`.
- Add exact edition, URL, DOI, page range, or chapter details when available.
- Keep this file concise; put long source notes in `raw/sources/`.
