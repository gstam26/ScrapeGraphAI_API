# Audit: Digest → Grouped Themes → Provenance traceability chain

**Status:** Audit complete 2026-07-05, against `adlm-outputs/validation_sample_run_2026-07-03.xlsx` (built by commit 766d542). One bug fixed this session (below). No design change implemented — findings only, per brief.

## Blunt verdict

**Showable to leadership with caveats, not as a finished claim.** The chain's *mechanics* are genuinely solid: every hyperlink and every Claim ID reference I checked — a full-population scan, not a sample, across all 89 Digest rows, all 322 Grouped Themes rows, and all 2766 Provenance rows — resolves to the row it claims to, with correct Entity/Question alignment and zero dangling references. FUJIFILM's small chain (2 cells, 6 Provenance rows) traces perfectly end to end. That's real and worth demoing.

But the one sentence the whole chain exists to let George say — **"every citation traces to a verified claim"** — is not true of the artifact. It is asserted three times in the codebase (`src/group.py:13-14`, `src/group.py:152`, `brain/decision-log.md:56`: theme labels are "always a real verified claim, never synthesized") and contradicted by the workbook: two theme anchors and ten further citations point to Provenance rows with `Verified=False`, including one inside HORIBA itself (the pathological case) and one that is quoted verbatim in the Digest sheet's top-3 line — the first thing a consultant reads. Do not tell leadership "100% verified-only" until gap #1 below is closed. Everything else is real but secondary.

## Ranked gaps

### 1. STRUCTURAL — "traces to verified claims" is not enforced anywhere in the code that builds the chain

> **RESOLVED 2026-07-06.** George's standing decision: exclusion (change-plan option a, strengthened). Implemented: `src/group.py:_display_values` filters grouping input to values with ≥1 verified evidence item (before `GROUP_MIN_ITEMS`); `src/io_excel.py` `claim_index` anchors on the first *verified* Provenance occurrence; the orange Verified=False review flag in `_style_sheet` was dead code (compared `"FALSE"` against bool `str()` `"False"`) and now renders. Unverified claims stay in Provenance, visibly flagged. 5 new tests; suite 125 passed. The validation criterion below should still be re-run on the next full workbook as confirmation.

**Evidence (this session, full-population check, script discarded but reproducible from description below):**
- Provenance has 16 rows with `Verified=False` out of 2766 (confirmed via column J, "Verified").
- Of those, **12 are cited as Claim IDs inside the Grouped Themes "Claim IDs" column**, and **2 are the actual Theme-cell anchor** (the hyperlink target, i.e. the representative claim for the whole theme):
  - `Grouped Themes!C6` (Agilent Technologies, Diagnostics type, theme "Biomarker research") → hyperlink `#Provenance!A31` → `Provenance!A31` = Claim ID `C0030`, Claim="Biomarker research", **Verified=False, Match Type='none', Verification Score=64**.
  - `Grouped Themes!C183` (Sartorius, Diagnostics type, theme "Biolayer Interferometry") → hyperlink `#Provenance!A1595` → `Provenance!A1595` = Claim ID `C1594`, **Verified=False, Match Type='none', Verification Score=56.5**. This exact claim is quoted in **`Digest!E48`**: *"20 items across 5 themes. Top: … "Biolayer Interferometry" (7 items) [C1594]…"* — a client-facing consultant reading Digest first, as instructed, hits this on their very first pass.
  - HORIBA-specific instance (the task's pathological case): `Grouped Themes!row 88` (theme "Anticoagulant Monitoring", 19 items, anchor `C0915`/verified, fine) lists member `[C1009]` in its bullet text → `Provenance!A1010` = Claim="NIVD", **Verified=False, Match Type='none'**. Bullets don't visually distinguish verified from unverified members — `- NIVD [C1009]` reads identically to a verified line.
- **Root cause, read from code:** `src/aggregate.py:aggregate_cells` builds `cell.value`/`cell.evidence` (`real_deduped`, `unique_values`) from *all* evidence regardless of `.verified` (only a null-sentinel check and fuzzy-dedup at line 121-165; no verified filter). `_rank_evidence` (`src/aggregate.py:91-99`) sorts by `match_type` then `semantic_score` — verified status is never a sort key. `src/group.py:group_rows`/`_medoid` cluster and label themes from `cell.value` with no verified filter either. So an evidence item that failed verification (weak/no fuzzy-match to its quote) can still become a display value, a cluster member, and even the medoid (theme label / anchor) if it happens to have the highest average intra-cluster cosine.
- **Why this is structural, not tuning:** there is no threshold to move — the `verified` boolean is simply never read by the grouping/labelling code path. Closing it requires a design decision (see change plan) about what "unverified but clustered" should mean, not a constant tweak.

**Validation criteria:** re-run the bulk check on a future workbook — for every Grouped Themes row, resolve the Theme-cell hyperlink to its Provenance row and assert `Verified == True`; for every ID in the "Claim IDs" column, same assertion (or, if unverified members are intentionally kept visible under a revised design, assert each carries a visible unverified marker and none is ever the medoid/anchor). Zero unmarked unverified citations = fixed.

### 2. STRUCTURAL (mitigated this session, residual is tuning) — overflow escape hatch didn't actually preserve traceability for oversized cells

**Evidence (before fix):** `src/io_excel.py:_make_grouped_themes_df` truncated the "Claim IDs" column to `MATRIX_MAX_DISPLAY_ITEMS` (50, `config.py:256`) in lockstep with the bullet-display cap, then appended a `" (+N more)"` suffix with **no IDs for the hidden items**. On the real workbook, HORIBA's largest theme (`Grouped Themes!row 92`, "Launching Ammonia･Nitrate Nitrogen Meter…", 115 items) showed 50 Claim IDs plus a bare `(+65 more)` — for exactly the 65 items the overflow text tells the consultant to "see Provenance" for, there was no ID to search Provenance by, and Provenance has no "Theme" column to filter on instead. This is the pathological-case failure the task asked me to hunt for, and it hit on the first large cell I checked.

**Fixed this session** (see "Bugs fixed" below) — Claim IDs column now lists every member regardless of the bullet-display cap. **Residual, tuning-level:** a consultant still can't click straight from a hidden item to its Provenance row (Excel allows one hyperlink per cell, and 65 targets can't share one cell's link) — they must Ctrl+F the now-present ID in Provenance's Claim ID column. Workable, not one-click. Also Provenance still has no "Theme" column, so filtering Provenance directly by theme (rather than ID-by-ID) isn't possible — see change plan step 2.

**Validation criteria:** for HORIBA's Recent news cell (328 items, 15 themes) in a future run, confirm every one of the 328 Claim IDs appears somewhere in the Grouped Themes "Claim IDs" columns for that entity/question (not just the first 50 per theme).

### 3. STRUCTURAL constraint, tuning-level mitigation available — inline `[C####]` citations aren't clickable

**Evidence:** Excel allows exactly one hyperlink per cell. The Theme cell (col C) and the Digest Question cell (col B) each get one real hyperlink to their anchor row — confirmed correct on every row checked. But the bracket citations *inside* the Values/Digest cell text (`- Anticoagulant Monitoring [C0915]`, or the Digest line's `[C0532]`, `[C0606]` for items #2/#3 of the top-3) are plain text, never hyperlinks — there structurally cannot be more than one live link per cell with the current one-row-per-theme layout. A first-time reader trained by the *other* blue-underlined cells to expect click-to-navigate will click a bracket citation and nothing happens.

**Verdict:** the *constraint* (can't multi-link a cell) is structural; a real fix (one row per claim, or a helper column per top-N slot) is a layout redesign, not attempted here. The **cheap mitigation** — a one-line legend ("[C0032]-style tags are Claim IDs — press Ctrl+F on Provenance's Claim ID column to jump to the source; only the underlined Theme/Question links are clickable") — is tuning and not yet present anywhere in the workbook (Summary sheet has no legend/notes at all, confirmed by dumping every non-empty cell in Summary — it's a pure metrics table, columns A–K).

**Validation criteria:** hand the workbook to someone who hasn't seen it; they should be able to explain, unaided, what a bracket citation is for within under a minute of reading a Digest row.

### 4. TUNING — Digest silently omits "no data" cells with no legend

**Evidence:** Digest has 89 data rows for 25 companies × 4 questions = 100 possible cells; the 11 missing pairs (`Agilent/R&D location`, `FUJIFILM/R&D location`, `FUJIFILM/Recent news`, `QuidelOrtho/R&D location`, `Sysmex America/R&D location`, `Thermo Fisher/R&D location`, `McKesson/R&D location`, `Aladdin Scientific/R&D location`, `Aladdin Scientific/Recent news`, `Calbiotech/Recent news`, `Catachem/Recent news`) all cross-checked exactly against Matrix cells reading `"No data found"` — confirmed correct, not a silent data-loss bug. But nothing on the Digest sheet says "absent = no data, see Matrix" — a reader could mistake it for an incomplete build.

**Validation criteria:** either add placeholder rows for no-data cells, or a one-line note; either way a reader should be able to confirm completeness from the Digest sheet alone.

### 5. TUNING — physical tab order doesn't match the chain's read order

**Evidence:** `wb.sheetnames` = `['Summary', 'Matrix', 'Digest', 'Provenance', 'Grouped Themes', 'Acquire Log', …]` (confirmed by direct load). The documented chain is Digest → Grouped Themes → Provenance, but a reader tabbing left-to-right hits the 2766-row raw Provenance dump *before* the readable Grouped Themes layer. `src/io_excel.py`'s `sheets.insert(2, ("Digest", digest_df))` puts Digest right after Matrix (correct) but `sheets.append(("Grouped Themes", themes_df))` puts it after Provenance, which was already appended earlier in the list (`("Provenance", provenance_df)` is added to `sheets` before the `claim_groups` block runs).

**Validation criteria:** tab order Summary, Matrix, Digest, Grouped Themes, Provenance matches the stated chain.

### 6. Ruled out — the "Marlborough mojibake" the brief flagged is not a real bug

**Checked directly:** scanned every cell in every sheet for `�` (replacement character) and classic double-encoding patterns (`â€`, `Ã©`, `Â`) — **zero hits**. Full non-ASCII histogram across all 10 sheets (159 distinct codepoints) shows only legitimate Unicode: `Saint-Mandé` (é = U+00E9), `São Paulo` (ã = U+00E3), `Göttingen` (ö = U+00F6), correctly encoded, confirmed byte-for-byte via `repr()` written to a UTF-8 file and read back. The `�` that appears when this data is piped through a cp1252-default Python `print`/terminal is an artifact of *this session's own tooling* (Windows console codepage truncating multi-byte UTF-8), not a defect in the xlsx. No code change made or needed; noting explicitly so the hypothesis is closed, not just dropped.

### 7. Latent, unobserved — silent no-anchor case is untested

**Evidence (code-reading only, not observed in this dataset):** `_group_claim_refs` (`src/io_excel.py`) can return `anchor=None` if no member's normalised text matches any `claim_index` entry (e.g. would happen for union-list columns like the plant-milk `"Plant milk types"` column, which joins multiple values into one synthesized comma-string that matches no single evidence value verbatim — not present in ADLM's 4 questions, so not triggered here: confirmed 0/322 Grouped Themes rows lack a Theme hyperlink in this workbook). When it happens, `theme_links` simply has no entry for that row, so the Theme cell renders with no hyperlink and no visible indication that traceability failed for that specific row — same look as a cell nobody bothered to link. `tests/test_traceability.py` has no fixture that exercises this path.

**Validation criteria:** add a test that builds a group whose theme/values never match `claim_index`, and decide/assert what should render (e.g. plain text with a warning marker) instead of a silently un-hyperlinked cell indistinguishable from correct output.

## Change plan for structural gaps (planned only, not implemented)

1. **Decide the verified-only policy (gap #1).** Options, needing a George call, not a coding call:
   a. Filter grouping input to `verified==True` evidence only (in `aggregate.py` or `group.py`) — simplest, but silently drops real (if unverified) claims from the consultant-facing view even though Matrix might still show them, changing recall.
   b. Keep unverified evidence in the cluster but exclude it from ever being the `_medoid`/anchor, and mark it inline (e.g. `[C1009 ⚠]`) with a legend.
   c. Split "Claim IDs" into two columns (verified / unverified) so nothing is hidden but nothing is unlabelled either.
   Validation: re-run this session's bulk anchor/citation check (Theme-cell hyperlink target and every "Claim IDs" entry resolved against Provenance's Verified column) on the next full run; require zero unmarked unverified citations.
2. **Give Provenance a "Theme" column** (denormalize group membership back in), so a consultant can filter/sort Provenance directly by theme for oversized cells instead of Ctrl+F-ing IDs one at a time — closes the residual from gap #2. Needs threading theme assignment from `group.py`'s output back into `_make_provenance_df`, which currently has no knowledge of grouping (grouping runs after Provenance is built, on the same `result.rows`, and is diagnostics/DIAGNOSTICS-gated separately). Validation: for HORIBA's 328-item Recent news cell, confirm a plain Excel filter on the new column isolates exactly the ~65 "hidden" items of one theme.
3. **Reorder sheets + add a legend** (gaps #3, #4, #5): move Grouped Themes before Provenance in `write_output_excel`'s sheet list; add a short note block (Summary sheet or a new one) explaining bracket citations, Ctrl+F, and the "absent from Digest = no data" convention. Validation: an unfamiliar reader can navigate one full hop chain and explain both conventions unaided within 2 minutes.
4. **Add the missing no-anchor test** (gap #7) once the verified-only policy (step 1) is decided, since the intended rendering for "no anchor resolves" and "anchor resolves but unverified" are likely the same code path.

## Bugs fixed this session

- **`src/io_excel.py`, `_make_grouped_themes_df` (~line 549-566):** the "Claim IDs" column was sliced to `MATRIX_MAX_DISPLAY_ITEMS` (50) in lockstep with the bullet-display truncation, so any theme with more than 50 members lost the Claim IDs for every member past #50 — exactly the members the "+N more — see Provenance" overflow text tells a reader to go look up, leaving them with no ID to search by. Fixed by computing the full `all_ids` list from the untruncated `pairs` before slicing for display; the `" (+N more)"` suffix on `ids_text` was removed since the column is now complete.
  **Test:** `tests/test_traceability.py::test_grouped_themes_claim_ids_not_truncated_by_display_cap` (new) — builds a 55-item theme (`MATRIX_MAX_DISPLAY_ITEMS + 5`), asserts the Values bullets stay capped at 50 with the overflow marker, but the Claim IDs column lists all 55 sequential IDs (`C0001`…`C0055`). Full suite: **119 passed** (was 118 before this session's one addition), run via `python -m pytest tests/ -q`, fully offline.

No other code changes made. The verified-only gap (#1) was diagnosed but deliberately left unfixed — closing it changes what data appears in the consultant-facing view, which is a design call reserved for George per the brief.
