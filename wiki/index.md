---
title: LLM-Wiki Index
tags: [llm-wiki, index]
updated_at: 2026-06-29
---

# LLM-Wiki Index

This index is the first page an agent should read before maintaining or querying the wiki. It lists the compiled knowledge areas and the maintenance files that govern them.

## Maintenance Entry Points

| Page | Purpose |
|---|---|
| [[schema]] | Rules for page structure, source handling, ingest/query/lint workflows |
| [[log]] | Append-only timeline of ingests, queries, lints, and repairs |
| [[raw/source_registry]] | Immutable source IDs and bibliographic metadata |
| [[maintenance/lint_checklist]] | Repeatable wiki health checklist |
| [[maintenance/error_book]] | Known retrieval, link, source, and contradiction issues |
| [[templates/concept_page_template]] | Template for new compiled concept pages |
| [[templates/source_note_template]] | Template for source notes |

## Course Catalog

| Area | Current Role |
|---|---|
| [[数据结构]] | Core data structures, trees, graphs, hashing, sorting |
| [[算法设计与分析]] | Algorithm paradigms, complexity, graph algorithms, DP |
| [[操作系统]] | Processes, memory, scheduling, IO, filesystems |
| [[计算机网络]] | TCP/IP, routing, application protocols, network security |
| [[数据库原理]] | Relational model, SQL, transactions, indexing, distributed databases |
| [[计算机组成原理]] | CPU, memory hierarchy, pipeline, ISA, IO |
| [[软件工程]] | Requirements, architecture, testing, process, DevOps |
| [[编译原理]] | Lexing, parsing, syntax-directed translation, optimization |
| [[离散数学]] | Logic, sets, relations, graph theory, combinatorics |
| [[程序设计]] | Programming paradigms, language features, debugging, performance |
| [[程序设计语言原理]] | Type systems, closures, evaluation, concurrency models |
| [[Python深入]] | Python runtime, metaprogramming, typing, async |
| [[Java深入]] | JVM, concurrency, generics, tooling |
| [[JavaScript]] | JavaScript and TypeScript runtime, language model, web APIs |
| [[Go语言]] | Go runtime, interfaces, concurrency, tooling |
| [[Rust语言]] | Ownership, traits, async, safety, tooling |
| [[C语言深入]] | C memory model, ABI, preprocessor, portability |
| [[分布式系统]] | Consensus, transactions, storage, stream processing |
| [[信息安全]] | Cryptography, web security, identity, security engineering |
| [[计算机图形学]] | Rendering, geometry, shaders, ray tracing |
| [[视频资源]] | Video-oriented learning resources |

## Query Workflow

1. Read this index and identify candidate areas.
2. Search/read compiled pages in the relevant course folders.
3. Follow explicit `[[wikilinks]]` before falling back to broad vector retrieval.
4. Cite source IDs or page titles used in the answer.
5. If the answer creates a reusable comparison or synthesis, file it as a new compiled wiki page and append [[log]].

## Maintenance Notes

- Keep this file content-oriented, not chronological.
- Append timeline entries only to [[log]].
- Add unresolved structural problems to [[maintenance/error_book]].
