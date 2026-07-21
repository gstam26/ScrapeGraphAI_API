# Tool Register — AI Extraction Pipeline

**One row per tool: layer, config flag, actual implementation, constraints. Status: Active / Baseline / Blocked / Dropped / Deferred.**
**Cross-checked against live code 2026-07-02 (brain/audit-findings.md).**

## Fetch / Acquire

| Tool | Config flag | Status | Implementation | Constraints / Notes |
|---|---|---|---|---|
| Firecrawl | `ACQUIRE_TOOL="firecrawl"` (default) | Active | `_fetch_firecrawl_doc` (`src/acquire/fetcher.py:132`) — requests `["markdown","rawHtml"]`; raw HTML feeds link discovery (markdown/cleaned html drop nav links — 2026-07-01) | ~$83/mo standard tier. `FIRECRAWL_API_KEY` in .env. Fetches from Firecrawl's IPs (shields Sagentia's). Fails on-network when corporate TLS interception breaks the api.firecrawl.dev cert (seen 2026-07-02, EUROIMMUN ×3) |
| Playwright | `ACQUIRE_TOOL="playwright"`; also auto-fallback | Active (fallback) | `_render_page_html` — domcontentloaded + 2 s wait, 15 s timeout; fresh Chromium per page | Thin-content fallback for Firecrawl (<200 chars) and local quality-gate failures. Candidate primary backend — see `proposals/firecrawl-replacement.md` (blocked on leadership: IP exposure) |
| Local (httpx + Trafilatura) | `ACQUIRE_TOOL="local"` | Active (privacy option) | `_fetch_local` — static fetch, Trafilatura extract, 3-rule quality gate, Playwright re-render on failure | Data stays on network. `include_links=True` so markdown context path fires (2026-06-16) |
| Requests | `ACQUIRE_TOOL="requests"` | Active (4th option) | Plain GET + BS4 text | No JS — fails on React/SPA sites. Fastest for static pages |
| SGAI managed API | `ACQUIRE_TOOL="sgai"` | Dropped (still selectable) | `_fetch_sgai` remains in `_FETCHERS` dispatch — not removed, just never choose it | 0/60 pages in the five-backend comparison. Retained as extraction baseline only |

## Extract

| Tool | Config flag | Status | Implementation | Constraints / Notes |
|---|---|---|---|---|
| GPT-5.5 via Power Automate (LLMAPI) | `EXTRACT_TOOL="llmapi"` — set per run via workbook config sheet or .env; **config.py default is `"azure"`** | Active (production runs) | `src/llmapi.py` `LLMAPI.call()`; retries once on 5xx (2026-07-02) | Only externally-reachable LLM permitted by Sagentia IT (Power Automate flow = the approved M365 route). No temperature/seed control — payload is `{"text": prompt}` only. `LLM_API_URL` in .env |
| Azure gpt-4.1-mini | `EXTRACT_TOOL="azure"` (config.py default) | Caution | `_extract_with_azure` via OpenAI SDK | Produces wall-of-text quotes, ignores single-sentence instruction — avoid for evaluation/production runs |
| Claude (direct API) | `EXTRACT_TOOL="claude"` | Active (off-network only) | `_extract_with_claude` (`src/extract.py:350`) — direct Anthropic Messages API via httpx; `CLAUDE_MODEL` default haiku-4.5 | Direct LLM APIs are blocked on the Sagentia network — usable only off-network for small runs/spot checks. Rate limits bite at scale |
| SGAI smartscraper | `EXTRACT_TOOL="sgai"` | Baseline | `_extract_with_sgai` | Evaluation comparison method only |
| Pydantic | — | Active | Runtime validation of LLM output; malformed responses dropped per field | |

All extractors share: chunking 8000/200, 8 chunk workers, sha256 extract cache, and a **global 16-call semaphore** (`EXTRACT_MAX_CONCURRENT_CALLS`, 2026-07-02).

## Embeddings / scoring

| Tool | Config flag | Status | Implementation | Constraints / Notes |
|---|---|---|---|---|
| nomic-embed-text (Ollama) | `OLLAMA_HOST`, `SCORER_TOOL="ollama"` | Active | `src/embed.py` batch endpoint; used by **Acquire** (link scoring), **Filter** (routing), **Verify** (diagnostic semantic_score) | Internal server `http://10.99.96.1:11434` — Science Group WiFi/VPN only. The ONLY permitted embedding source (HuggingFace blocked). Graceful degradation: BM25 in Acquire, route-all in Filter, skipped semantic_score in Verify |
| BM25 | — (automatic fallback) | Active (fallback) | `score_links` in `link_scorer.py` | Crawler-only fallback when Ollama unreachable. Per-batch relative scores (threshold `CRAWL_MIN_SCORE=0.12`) |
| sentence-transformers | — | Blocked | Never implemented | HuggingFace downloads blocked by Sagentia IT. Interim-report plan; replaced by Ollama |
| ms-marco-MiniLM-L6-v2 (cross-encoder) | `--semantic-backend cross-encoder` (generic_eval), `--cross-encoder` (filter_recalibration); `CROSS_ENCODER_MODEL`/`CROSS_ENCODER_MIN` env | Experimental | `src/eval/cross_encoder.py` — pairwise scorer, sigmoid logits, lazy sentence-transformers load | PAIR scorer, no vectors: candidate for filter routing + eval matching; structurally unusable for group.py clustering. Relevance-trained (≠ equivalence); threshold unvalidated until matcher_eval label-score passes. Model files must exist LOCALLY (HF blocked on-network); nothing routes through it by default (2026-07-21) |

## Verify

| Tool | Config flag | Status | Implementation | Constraints / Notes |
|---|---|---|---|---|
| rapidfuzz | `VERIFY_TOOL="rapidfuzz"` | Active | exact substring → normalised `partial_ratio ≥ 70` → soft ≥ 68 for quotes ≥ 100 chars with literal 20-char anchors (`src/verify.py:24-45`) | The verification gate. Checks quote EXISTS, not that it SUPPORTS the value — see `proposals/semantic-verify.md` |
| NLI entailment (HuggingFace) | — | Blocked (unconfirmed) | Not attempted | HuggingFace blocked; original "Final" verify tier |
| LLM-as-judge | — | Deferred → proposed | Not built | Rejected 2026-06-24 as keep/reject filter; re-scoped 2026-07-02 as an annotation tier gated on a human-labelled eval (`proposals/semantic-verify.md`) |

## Output

| Tool | Config flag | Status | Implementation | Constraints / Notes |
|---|---|---|---|---|
| openpyxl | `DIAGNOSTICS` (7 sheets vs 3) | Active | `src/io_excel.py` | `char_span` computed by Verify but NOT written to Provenance (known limitation, report-deltas §5.5) |
