---
title: LLM-Wiki Vault README
tags: [llm-wiki, governance]
updated_at: 2026-06-29
---

# LLM-Wiki Vault

This directory is the project LLM-Wiki vault for computer science learning knowledge.

The content course folders are compiled wiki pages used by RAG/GraphRAG. The LLM-Wiki control files below define how the vault should be maintained without polluting the RAG corpus:

- [[index]]: content-oriented catalog and navigation entry.
- [[schema]]: page conventions, source rules, and agent workflows.
- [[log]]: append-only maintenance timeline.
- [[maintenance/lint_checklist]]: recurring health checks.
- [[maintenance/error_book]]: structural and semantic repair queue.
- [[raw/source_registry]]: immutable source catalog.
- [[templates/concept_page_template]]: template for compiled concept pages.
- [[templates/source_note_template]]: template for raw source notes.

## Content Folders

Current compiled course folders:

- [[C语言深入]]
- [[Go语言]]
- [[JavaScript]]
- [[Java深入]]
- [[Python深入]]
- [[Rust语言]]
- [[信息安全]]
- [[分布式系统]]
- [[操作系统]]
- [[数据库原理]]
- [[数据结构]]
- [[离散数学]]
- [[程序设计]]
- [[程序设计语言原理]]
- [[算法设计与分析]]
- [[编译原理]]
- [[视频资源]]
- [[计算机图形学]]
- [[计算机组成原理]]
- [[计算机网络]]
- [[软件工程]]

## RAG Boundary

Only compiled course pages should be imported into `rag.wiki_page` and vectorized. The root files and these directories are maintenance metadata and must stay out of RAG imports:

- `raw/`
- `maintenance/`
- `templates/`
- `assets/`
- `index.md`
- `schema.md`
- `log.md`
- `README.md`
