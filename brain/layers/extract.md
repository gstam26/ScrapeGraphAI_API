# Extract

**Responsibility:** LLM extraction of `{value, quote}` answers per entity × question from cached page text. Quotes must be verbatim (Verify depends on it).

## Interface
- In: `extract_cells(page: PageDoc, columns, entities, cfg, diag, use_cache)` — `src/extract.py`
- Out: `list[ExtractedCell]` — one per entity × column; `evidence: [SourceQuote(value, quote)]`, multi-answer values as lists
- Chunking: `EXTRACT_CHUNK_SIZE=8000`, overlap 200; per-chunk results merged (`_merge_chunk_data`, value-keyed)
- Cache: `cache/extract/<sha256(chunk+columns+entities+tool)>.json`

## Current implementation
- Tool dispatch (if/elif in `extract_cells`): `llmapi` (GPT-5.5 via Power Automate — production), `azure` (config.py default — wall-of-text prone, avoid for real runs), `claude` (direct API, off-network only), `sgai` (baseline).
- Concurrency: 8 chunk workers × 4 page workers × 4 entity workers, bounded by a **global 16-call semaphore** (`_LLM_CALL_SEMAPHORE`, 2026-07-02). Cache hits bypass the semaphore.
- `LLMAPI.call` retries once on 5xx (2026-07-02); timeouts surface as TimeoutError, not retried.
- Prompt: one verbatim sentence per claim, list-quotes forbidden, distinct claims split (2026-06-21). Case-insensitive entity/column key matching; single-entity flat-shape tolerance.

## Known issues
- Power Automate proxy 502s under load (retry mitigates, doesn't eliminate).
- No temperature/seed control through the proxy (`{"text": ...}` payload only) — extraction is not bit-reproducible.
- Chunk-boundary near-duplicate claims on very long pages (Oatly carryover; aggregate dedup catches most).

## Open questions
- None blocking. Judge-style reuse of the proxy is Verify's question (`proposals/semantic-verify.md`).
