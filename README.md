# Entity Extraction Pipeline

A pipeline that extracts structured, **source-verified** answers about named entities from their websites, and writes an Excel workbook with full provenance. Built as an MSc dissertation project with Sagentia Innovation; designed to be run by non-technical consultants on real engagements. Five core layers (Acquire → Filter → Extract → Verify → Aggregate) do the extraction and grounding; an optional grouping + LLM-summary layer turns the verified claims into a consultant-facing summary.

The core reliability claim: every extracted answer carries a verbatim quote, and the Verify layer independently checks that quote against the cached source page — the LLM is never trusted on its own word. The same principle extends to the optional AI Summary: every sentence must cite a claim ID from the verified set, and a deterministic gate falls back to verbatim claims when a sentence doesn't.

## Architecture

```text
Input workbook (entities + urls + questions + optional config)
        |
        v
+----------------+   fetch seed URLs; guided same-domain crawl scores links
|    ACQUIRE     |   against the questions (Ollama embeddings, BM25 fallback);
|                |   pages cached to cache/ by sha256(url)
+----------------+
        |
        v
+----------------+   routes each page to the questions it can answer
|     FILTER     |   (max-chunk cosine >= threshold OR keyword gate);
|                |   NEVER excludes a page — worst case routes all questions
+----------------+
        |
        v
+----------------+   LLM extracts {value, verbatim quote} per entity x question;
|    EXTRACT     |   8000-char chunking with overlap; per-chunk cache;
|                |   global concurrency cap on LLM calls
+----------------+
        |
        v
+----------------+   deterministic rapidfuzz check that each quote actually
|     VERIFY     |   appears in the cached page (exact -> fuzzy -> soft anchors);
|                |   + diagnostic semantic score (claim vs quote cosine)
+----------------+
        |
        v
+----------------+   dedupe & rank evidence per entity x question; conflicts
|   AGGREGATE    |   flagged on single-answer questions only; null sentinels
|                |   never compete with real answers
+----------------+
        |
        v
+----------------+   cluster each cell's verified claims into themes and assign
|     GROUP      |   stable [C####] claim IDs (deterministic, on by default);
|                |   feeds the Digest + Grouped Themes sheets
+----------------+
        |
        v
+----------------+   OPTIONAL (SUMMARY_ENABLED): LLM synthesizes the verified
|   SUMMARIZE    |   claims into a scannable line per cell, each sentence cited;
|                |   a mechanical gate falls back to verbatim claims on failure
+----------------+
        |
        v
Excel output (always): Summary | Matrix | Provenance
  + grouping (default): Digest | Grouped Themes
  + summary (opt-in):   AI Summary [| Summary Log]
  + diagnostics (default): Acquire | Crawl | Filter | Extract | Verify logs
```

### The stages in plain terms

| Stage | What it does | Why it's there |
|---|---|---|
| **Acquire** | Fetches each entity's website and crawls the relevant pages | You can't answer questions about a company without its content |
| **Filter** | Routes each page only to the questions it could plausibly answer | Don't ask "what's the revenue?" on a careers page — saves cost and noise |
| **Extract** | An LLM pulls out each answer **plus a verbatim quote** proving it | Structured answers, with the receipt attached |
| **Verify** | Checks that quote actually appears on the page | Catches hallucination — the LLM is never taken at its word |
| **Aggregate** | Merges duplicate answers across pages, flags genuine conflicts | One clean answer per cell, without hiding disagreement |
| **Group** | Clusters the verified claims and gives each a citable `[C####]` ID | The audit trail: every claim is traceable back to its source |
| **Summarize** *(optional)* | An LLM writes a scannable summary, every sentence cited; a gate falls back to plain claims if it can't cite | A consultant-ready view that stays honest by construction |

### Tools & methods per stage

Where each stage lives, what it uses, and how it works:

| Stage | Code | Tool(s) | Method |
|---|---|---|---|
| **Acquire — fetch** | `src/acquire/fetcher.py` | httpx · Trafilatura · Playwright → Chromium | Static-first hybrid: httpx GET → Trafilatura extracts the real content → a quality gate judges it; only on failure does it launch a headless Chromium render (via Playwright), then keeps whichever text is richer |
| **Acquire — crawl** | `src/acquire/crawler.py`, `link_scorer.py` | Ollama embeddings (BM25 fallback) · BeautifulSoup | Same-domain crawl; scores links against the questions and follows the most relevant; pages cached by `sha256(url)` |
| **Filter** | `src/filter.py` | Ollama embeddings + cosine | Chunk the page, embed chunks + questions, take the max cosine per question; keep a question if cosine ≥ threshold **or** a keyword matches. Fail-safe: route all questions if unsure |
| **Extract** | `src/extract.py` | Azure OpenAI GPT-4.1-mini (Claude / SGAI pluggable) | Prompt for strict JSON `{value, verbatim quote}` per entity × question; overlapped chunking, concurrency-capped calls, per-chunk cache, results merged |
| **Verify** | `src/verify.py` | rapidfuzz + Ollama embeddings | Quote grounding: exact substring → fuzzy (`partial_ratio`) → soft-anchor for long quotes; plus a semantic cosine (claim vs quote). Unverified evidence is kept and flagged, never dropped |
| **Aggregate** | `src/aggregate.py` | rapidfuzz (`token_sort_ratio`) | Group by entity × question; fuzzy-dedup near-identical answers; flag conflicts on single-answer questions; rank evidence by verification quality |
| **Group** | `src/group.py` | Ollama embeddings (mean-centered) + cosine | Cluster verified claims into themes and assign stable `[C####]` IDs — deterministic, so the audit trail is reproducible |
| **Summarize** *(opt-in)* | `src/summarize.py` | Azure OpenAI (temp 0, seeded) + deterministic gate | 3-way route — deterministic (verbatim) / merge (semantic dedup) / prose (synthesis); every sentence must cite a `[C####]` ID; a mechanical gate falls back to verbatim claims on failure |

Layers are separable by design: tools are swapped via `config.py` / workbook config, and Filter/Verify/Aggregate never know which fetcher or LLM ran upstream. Deep-dive notes per layer: `brain/layers/`.

## Sagentia network constraints (read first)

These are IT-policy constraints, not preferences — the pipeline is built around them:

- **No HuggingFace** (model downloads blocked). Embeddings come **only** from the internal Ollama server (`nomic-embed-text` at `OLLAMA_HOST`, reachable on Science Group WiFi/VPN only). Off VPN, the pipeline degrades gracefully: BM25 crawl scoring, route-all filtering, no semantic scores.
- **Production extraction is Azure-direct GPT-4.1-mini** (`EXTRACT_TOOL=azure`, leadership-sanctioned 2026-07). The Power Automate proxy (`llmapi`) remains as a legacy path — it now serves the same model, so it buys nothing but an extra dependency. Claude direct is off-network spot checks only.
- **Polite crawling.** The default fetcher (`playwright_pooled_hybrid`) runs from THIS machine's IP, so politeness is enforced by construction: robots.txt respected, ≥2 s per-domain delay, honest User-Agent. Sagentia has had IPs blocked before — do not weaken these.
- Corporate TLS interception can break vendor API calls (e.g. Firecrawl `SSL: CERTIFICATE_VERIFY_FAILED`) — a known on-network failure mode.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for the playwright/local backends
```

`.env` in the project root (only the keys for the backends you use):

```env
AZURE_API_KEY=...       # production extraction (EXTRACT_TOOL=azure)
FIRECRAWL_API_KEY=...   # optional: ACQUIRE_TOOL=firecrawl (vendor fetch, credits)
LLM_API_URL=...         # legacy: Power Automate flow (EXTRACT_TOOL=llmapi)
CLAUDE_API_KEY=...      # optional: EXTRACT_TOOL=claude (off-network only)
```

## Running

```bash
python main.py
```

Prompts for an input workbook path and an output filename; everything else comes from the workbook. Output lands in `outputs/`.

### Input workbook (4 sheets)

| Sheet | Columns | Notes |
|---|---|---|
| `entities` | `entity` | Row labels of the output Matrix |
| `urls` | `url`, `depth` (any int ≥ 0), `entities` | Blank `entities` = applies to all; comma-separated otherwise |
| `questions` | `question`, `instructions` | Instruction text is appended to the extraction prompt |
| `config` (optional) | `setting`, `value` | Per-run overrides, see below |

Workbook-overridable settings: `ACQUIRE_TOOL`, `EXTRACT_TOOL`, `CRAWL_MIN_SCORE`, `CRAWL_MIN_SCORE_EMBED`, `CRAWL_SCORER`, `CRAWL_MAX_PAGES`, `DEFAULT_DEPTH`. `FILTER_MODE` is not workbook-overridable but IS env-overridable (`FILTER_MODE=passthrough` in `.env`), as is `SUMMARY_ENABLED`.

Sample: `samples/test_smoke.xlsx`. Workbook builders (ADLM, CMO): `scripts/build_*_workbook.py`.

### Key config (`config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `ACQUIRE_TOOL` | `playwright_pooled_hybrid` | Fetcher: hybrid / `playwright_pooled` / `firecrawl` / `local` / `playwright` / `requests` |
| `EXTRACT_TOOL` | `azure` (env-overridable) | Extractor: Azure GPT-4.1-mini is production; `llmapi` legacy |
| `FILTER_MODE` | `threshold` | `passthrough` routes everything (scores still logged) |
| `FILTER_THRESHOLD` | `0.55` | Cosine gate for question routing |
| `CRAWL_MAX_PAGES` | `15` | Page budget per entity |
| `CRAWL_LOCALE_DEDUP` | `True` | Drop translated copies of already-fetched pages |
| `PIPELINE_ENTITY_WORKERS` | `4` | Entities crawled concurrently (one domain each) |
| `EXTRACT_MAX_CONCURRENT_CALLS` | `16` | Global LLM-call cap across all workers |
| `VERIFY_THRESHOLD` | `70` | Fuzzy match gate for quote verification |
| `GROUPING_ENABLED` | `True` | Adds Digest + Grouped Themes (claim-ID traceability chain) |
| `SUMMARY_ENABLED` | `False` (env-only) | Opt-in AI Summary layer (LLM synthesis + citation gate) |
| `DIAGNOSTICS` | `True` | Adds the per-layer log sheets (Acquire/Crawl/Filter/Extract/Verify + Summary Log) |

### The two fetch backends that matter

- **`playwright_pooled_hybrid`** (default): static-first (httpx + Trafilatura + quality gate), escalating to a pooled headless Chromium render only on gate failure — and keeping whichever extraction is richer. Free, polite by construction (robots.txt, per-domain delay, honest UA), full-DOM link discovery. Measured parity vs the Firecrawl baseline: 100% on Company type, 92% on Diagnostics type (the losses are WAF-denied sites — see `brain/proposals/vendor-fallback.md`).
- **`firecrawl`** (vendor, credits): fetches from Firecrawl's anti-bot infrastructure, so it reaches sites that deny us (e.g. Akamai 403s). Kept for per-workbook use and as the proposed automatic fallback for protocol-level denials.

## Output workbook

**Always:** `Summary`, `Matrix` (one row per entity, one column per question — deduplicated, ranked, conflict-flagged), `Provenance` (every evidence item: claim, verbatim quote, verified flag, match type, scores, source URL).

**Grouping layer (on by default):** `Digest` and `Grouped Themes` — deterministic claim clusters with stable `[C####]` IDs, hyperlinked back to Provenance (chain: Digest → Grouped Themes → Provenance).

**Summary layer (`SUMMARY_ENABLED`, opt-in):** `AI Summary` — the matrix-shaped consultant view; gate-failed cells fall back to the deterministic Digest line, visibly marked. `Summary Log` (under `DIAGNOSTICS`) is the per-call audit trail.

**Diagnostics (`DIAGNOSTICS=True`, default):** `Acquire Log`, `Crawl Candidates`, `Filter Log`, `Extract Log`, `Verify Log`.

Unverified claims are marked and highlighted in the Matrix; Provenance is the audit trail back to source.

## Tests

```bash
python -m pytest tests/ --ignore=tests/test_acquire_smoke.py   # offline suite (~196 tests)
python -m pytest tests/test_acquire_smoke.py                   # live-network smoke
```

## Project structure

```text
main.py, pipeline.py, config.py, models.py     entry points + shared config/schema
src/                                           the pipeline layers
  acquire/  (fetcher, crawler, link_scorer, cache)
  filter.py, extract.py, verify.py, aggregate.py
  group.py, summarize.py                       grouping + optional LLM summary
  io_excel.py, embed.py, llmapi.py
  resolve/  (company-name -> URL resolver; fallback to the directory scrape)
tests/                                         offline + live smoke tests
diagnostics/                                   standalone reports + eval_lib (Stage 10 evaluation)
brain/                                         decision log, tool register, layer notes, proposals
adlm-inputs/, adlm-outputs/                    ADLM engagement workbooks (tracked)
cache/, outputs/                               generated (gitignored)
```

`brain/README.md` indexes the project's working memory — start at `brain/decision-log.md` for why anything is the way it is.
