# Project Structure Audit

> **STALE (2026-07-02):** this snapshot predates the restructure — the layer
> modules now live under `src/`, tests under `tests/`, and `LLMAPI` in
> `src/llmapi.py`. Its §2/§5 recommendations were largely executed. Kept as
> the historical record; for current structure see `README.md`, and for the
> next round of restructuring see `brain/proposals/code-restructure.md`.

## 1. Full directory tree

```
project root/
├── main.py
├── pipeline.py
├── config.py
├── models.py
├── io_excel.py
├── filter.py
├── extract.py
├── verify.py
├── aggregate.py
├── test_smoke.py
├── test_crawl_relevance.py
├── test_acquire_smoke.py
├── README.md
├── requirements.txt
│
├── src/
│   ├── __init__.py
│   ├── embed.py
│   └── acquire/
│       ├── __init__.py
│       ├── cache.py
│       ├── crawler.py
│       ├── fetcher.py
│       ├── link_scorer.py
│       └── models.py
│
├── diagnostics/
│   ├── acquire_report.py
│   ├── crawl_collect.py
│   ├── crawl_debug.py
│   ├── crawl_trace.py
│   ├── crawl_trace_embed.py
│   ├── fetch_eval.py
│   ├── fetch_test.py
│   ├── link_extractor.py
│   ├── llmapi.py
│   ├── scratch_ollama.py
│   └── fetch_eval/          ← timestamped run output folders
│       ├── 20260605_155702Z/
│       ├── 20260605_160431Z/
│       ├── 20260605_160616Z/
│       └── 20260605_160636Z/
│
├── samples/
│   └── test_smoke.xlsx
│
├── cache/                   ← runtime cache, mix of .txt and .md files
├── outputs/                 ← pipeline run outputs
└── .claude/
    └── settings.local.json
```

---

## 2. Root-level files that should be under `src/`

These five are pipeline layers that logically belong in `src/` alongside acquire:

| File | Should be |
|---|---|
| `filter.py` | `src/filter.py` |
| `extract.py` | `src/extract.py` |
| `verify.py` | `src/verify.py` |
| `aggregate.py` | `src/aggregate.py` |
| `io_excel.py` | `src/io_excel.py` |

`models.py` and `config.py` are shared configuration/schema files — reasonable at root, though `models.py` could arguably live in `src/`. `main.py` and `pipeline.py` are entry points, root is fine for those.

---

## 3. Duplicated or diagnostic equivalents

| Root/src file | Diagnostic equivalent | Relationship |
|---|---|---|
| `src/acquire/link_scorer.py` | `diagnostics/crawl_trace.py`, `diagnostics/crawl_trace_embed.py`, `diagnostics/crawl_debug.py` | All three re-implement link scoring independently |
| `src/acquire/crawler.py` | `diagnostics/crawl_collect.py`, `diagnostics/crawl_trace_embed.py` | Full crawl loop reimplemented in diagnostics |
| `src/acquire/fetcher.py` | `diagnostics/fetch_eval.py`, `diagnostics/fetch_test.py` | Fetch logic reimplemented for benchmarking |
| `src/acquire/link_scorer.py` | `diagnostics/link_extractor.py` | Link extraction reimplemented |
| `src/embed.py` | `diagnostics/crawl_collect.py`, `diagnostics/crawl_trace_embed.py` | Both have private `_embed_batch()` copies — do not import from `src.embed` |
| `models.py` | `src/acquire/models.py` | Two separate models files — different scopes but same name |
| `diagnostics/llmapi.py` | `extract.py` | `LLMAPI` is only in diagnostics but used by the production extract layer via import |

---

## 4. Key file locations

| File | Location | Notes |
|---|---|---|
| `filter.py` | root | should be `src/filter.py` |
| `extract.py` | root | should be `src/extract.py` |
| `verify.py` | root | should be `src/verify.py` |
| `aggregate.py` | root | should be `src/aggregate.py` |
| `pipeline.py` | root | entry-point orchestrator, root is fine |
| `main.py` | root | CLI entry point, root is fine |
| `models.py` | root | shared schema, root is acceptable |
| `config.py` | root | shared config, root is fine |

---

## 5. Naming inconsistencies

- **Two `models.py` files** — `models.py` at root (pipeline-wide models) and `src/acquire/models.py` (acquire-specific models). Same filename, different scopes, potential confusion.
- **`io_excel.py`** — the `io_` prefix is used nowhere else. Other layers are named by function (`filter`, `extract`, `verify`). It would be `excel.py` or `src/io/excel.py` by the same convention.
- **`diagnostics/llmapi.py`** — production code (`LLMAPI` class) living in `diagnostics/`. It's imported by `extract.py` as if it's a src module.
- **`diagnostics/scratch_ollama.py`** — `scratch_` prefix signals throwaway work, inconsistent with the rest of the `diagnostics/` naming which uses descriptive verb-noun names.
- **Test files** — three test files at root (`test_smoke.py`, `test_acquire_smoke.py`, `test_crawl_relevance.py`) with no `tests/` folder and inconsistent naming (`test_smoke` vs `test_acquire_smoke`).
