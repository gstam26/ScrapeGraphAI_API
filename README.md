# Entity Extraction Pipeline

A five-layer pipeline that extracts structured, **source-verified** answers about named entities from their websites, and writes an Excel workbook with full provenance. Built as an MSc dissertation project with Sagentia Innovation; designed to be run by non-technical consultants on real engagements.

The core reliability claim: every extracted answer carries a verbatim quote, and the Verify layer independently checks that quote against the cached source page — the LLM is never trusted on its own word.

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
Excel output: Summary | Matrix | Provenance (+ 4 diagnostic sheets)
```

Layers are separable by design: tools are swapped via `config.py` / workbook config, and Filter/Verify/Aggregate never know which fetcher or LLM ran upstream. Deep-dive notes per layer: `brain/layers/`.

## Sagentia network constraints (read first)

These are IT-policy constraints, not preferences — the pipeline is built around them:

- **No HuggingFace** (model downloads blocked). Embeddings come **only** from the internal Ollama server (`nomic-embed-text` at `OLLAMA_HOST`, reachable on Science Group WiFi/VPN only). Off VPN, the pipeline degrades gracefully: BM25 crawl scoring, route-all filtering, no semantic scores.
- **No direct external LLM APIs on-network.** Production extraction uses GPT-5.5 via an approved **Power Automate proxy** (`EXTRACT_TOOL=llmapi`, `LLM_API_URL` in `.env`). The direct Claude/Azure paths exist for off-network spot checks only.
- **Polite crawling.** Firecrawl (the default fetcher) proxies requests through its own infrastructure. Do not point a self-hosted fetcher at external sites without rate limiting — see `brain/proposals/firecrawl-replacement.md`.
- Corporate TLS interception can break the Firecrawl API call itself (`SSL: CERTIFICATE_VERIFY_FAILED`) — a known on-network failure mode.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for the playwright/local backends
```

`.env` in the project root (only the keys for the backends you use):

```env
FIRECRAWL_API_KEY=...   # default fetch backend
LLM_API_URL=...         # Power Automate flow (EXTRACT_TOOL=llmapi)
AZURE_API_KEY=...       # optional: EXTRACT_TOOL=azure
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
| `urls` | `url`, `depth` (0–2), `entities` | Blank `entities` = applies to all; comma-separated otherwise |
| `questions` | `question`, `instructions` | Instruction text is appended to the extraction prompt |
| `config` (optional) | `setting`, `value` | Per-run overrides, see below |

Workbook-overridable settings: `ACQUIRE_TOOL`, `EXTRACT_TOOL`, `CRAWL_MIN_SCORE`, `CRAWL_MIN_SCORE_EMBED`, `CRAWL_SCORER`, `CRAWL_MAX_PAGES`, `DEFAULT_DEPTH`. **`FILTER_MODE` is NOT workbook-overridable** — passthrough runs require editing `config.py`.

Sample: `samples/test_smoke.xlsx`. Builders for the ADLM engagement: `build_*_workbook.py`.

### Key config (`config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `ACQUIRE_TOOL` | `firecrawl` | Fetcher: `firecrawl` / `local` / `playwright` / `requests` (`sgai` exists but is dropped) |
| `EXTRACT_TOOL` | `azure` (env-overridable) | Extractor: use `llmapi` for production runs |
| `FILTER_MODE` | `threshold` | `passthrough` routes everything (scores still logged) |
| `FILTER_THRESHOLD` | `0.55` | Cosine gate for question routing |
| `CRAWL_MAX_PAGES` | `15` | Page budget per entity |
| `CRAWL_LOCALE_DEDUP` | `True` | Drop translated copies of already-fetched pages |
| `PIPELINE_ENTITY_WORKERS` | `4` | Entities crawled concurrently (one domain each) |
| `EXTRACT_MAX_CONCURRENT_CALLS` | `16` | Global LLM-call cap across all workers |
| `VERIFY_THRESHOLD` | `70` | Fuzzy match gate for quote verification |
| `DIAGNOSTICS` | `True` | 7 output sheets vs 3 |

### The two fetch backends that matter

- **`firecrawl`** (default): best content quality and discovery in the five-backend comparison; requests raw HTML so nav/footer links survive into link discovery; requires the paid API key; fetches from Firecrawl's IPs.
- **`local`** (privacy option): httpx + Trafilatura with a three-rule quality gate and Playwright re-render fallback; keeps all traffic on the local network; weaker on JS-heavy sites.

## Output workbook

`Summary`, `Matrix` (one row per entity, one column per question — deduplicated, ranked, conflict-flagged), `Provenance` (every evidence item: claim, verbatim quote, verified flag, match type, scores, source URL). With `DIAGNOSTICS=True` also: `Acquire Log`, `Crawl Candidates`, `Filter Log`, `Extract Log`, `Verify Log`.

Unverified claims are marked and highlighted in the Matrix; Provenance is the audit trail back to source.

## Tests

```bash
python -m pytest tests/ --ignore=tests/test_acquire_smoke.py   # offline suite (~72 tests)
python -m pytest tests/test_acquire_smoke.py                   # live-network smoke
```

## Project structure

```text
main.py, pipeline.py, config.py, models.py     entry points + shared config/schema
src/                                           the five layers
  acquire/  (fetcher, crawler, link_scorer, cache)
  filter.py, extract.py, verify.py, aggregate.py
  io_excel.py, embed.py, llmapi.py
  resolve/  (company-name -> URL resolver; fallback to the directory scrape)
tests/                                         offline + live smoke tests
diagnostics/                                   standalone reports + eval_lib (Stage 10 evaluation)
brain/                                         decision log, tool register, layer notes, proposals
adlm-inputs/, adlm-outputs/                    ADLM engagement workbooks (tracked)
cache/, outputs/                               generated (gitignored)
```

`brain/README.md` indexes the project's working memory — start at `brain/decision-log.md` for why anything is the way it is.
