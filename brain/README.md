# brain/ — project working memory

Decision-focused notes for the extraction-pipeline dissertation project. Terse by design; the code is the truth where they disagree (last full cross-check: 2026-07-02, `audit-findings.md`).

| File / dir | What lives there |
|---|---|
| `decision-log.md` | Append-only architectural decision record, newest first. One entry per decision: context → options → decision → why → status. The canonical "why is it like this" reference — start here when questioning any design choice. |
| `tool-register.md` | One row per tool: layer, config flag, actual implementation, status, constraints (Sagentia network policy, Ollama-only embeddings, no HuggingFace). Cross-checked against live code 2026-07-02. |
| `report-deltas.md` | Where reality diverges from the interim report, keyed to report sections — the dissertation correction list (extractor is GPT-5.5/Power Automate not Claude, embeddings are Ollama not sentence-transformers, etc.). |
| `layers/` | One file per pipeline layer (acquire, filter, extract, verify, aggregate): responsibility, interface, current implementation, known issues, open questions. The fastest way to load context on one layer. |
| `tasks/adlm-2026.md` | The active engagement: four questions, 182-company scope, validation status, gate before the production run, open decisions. |
| `proposals/` | Investigations awaiting a decision (runtime, Firecrawl replacement, semantic verify, code restructure). Findings separated from recommendations; each names who decides. |
| `audit-findings.md` | 2026-07-02 audit: every brain/docs claim cross-checked against code — divergences (fixed), stale code comments (recorded), dead/orphaned file list. |
