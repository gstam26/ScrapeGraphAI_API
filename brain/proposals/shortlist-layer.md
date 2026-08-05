# Shortlist layer: criteria-driven ranked shortlist from the deliverable Matrix

**Status:** PROPOSED 2026-08-05 — awaiting George's design review. Not built.

## Problem

The pipeline ends at the Matrix: 59 entities × 17 answers. The analyst's next
step — "which 5 should we actually talk to?" — is manual. A Shortlist layer
reads the deliverable Matrix (read-only, post-Aggregate, no changes to any
existing layer) and produces a ranked top-k with per-criterion traceability:
every exclusion attributable to one criterion and one source cell.

Design principle (same rule as scorer terms / consent selectors / vendor
triggers): **criteria are data, not code.** Add, remove, reweight, or
re-threshold without touching Python. First two hard gates: independence
status, and company size ("giant" via employees and/or revenue).

No LLM anywhere in v1. Deterministic parsing and scoring only.

---

## 1. Survey: what the Matrix cells actually contain

Two sources: the cell grammar from the writer (`io_excel._make_matrix_df`,
authoritative) and a census of the real 2026-08-03 single-best run (59
entities, via the eval report's Detail values). Caveat: the census sees
values post line-split; **marker frequencies (conflict/unverified/demotion)
could not be counted on this machine** — the raw workbook lives on the work
laptop. Grammar handling is designed below regardless; frequencies are a
verify-on-laptop item.

### Cell grammar (writer-defined, closed set)

```
"No data found"                                → whole-cell null
"- value" lines                                → one item each
"(sources conflict)\n" prefix                  → conflict cell (orange)
"-- Unverified --" divider                     → items below are unverified
"(unverified)" trailing line                   → whole cell unverified
"[+N other candidate(s) — see Provenance]"     → single-best demoted N rivals
"[+N more items — see Provenance]"             → display cap hid N items
```

### Independence — CLEAN (parseable: 59/59)

All 59 cells are bare `Yes` / `No` / `None (not disclosed)` (28 / 25 / 6).
The single-best discipline delivers exactly what a binary parser needs.

### Employees — MESSY (parseable: ~26/59; ND: 32; unparseable: 1)

Observed forms:
| Form | Examples |
|---|---|
| bare / comma int | `3300`, `220`, `12500`, `1,200` |
| qualified | `more than 6,000`, `over 2000`, `300+`, `around 1,530`, `6,500+` |
| year-annotated | `9,000+ (2025)`, **`15 (2005)`** ← 21-year-old figure |
| forecast/planned | `200 (planned within next 2 years)` |
| subset count | `more than 100 skilled engineers` (engineers ≠ headcount) |
| non-answer | `a staff of design experts` |

### Revenue — SPARSE + MESSY (parseable: ~11/59; ND: 48)

| Form | Examples |
|---|---|
| currency+scale | `$29.8B (Fiscal Year 2025)`, `£45M (2026)`, `CHF 883 million (2025)` |
| unit-suffix | `1,426.50 MSEK` |
| range | `$100 million to $500 million` |
| no currency | `2+ billion` |
| partial period | `CHF 180.5 million (first half of 2026)` |
| forecast | `USD 20.5 billion (2026 outlook)` |
| **parent-attributed** | **`$10.5 billion (Mitsui Chemicals, Inc.)`** (Arrk), **`exceeding $125 billion`** (Phillips Medisize — that is Koch-scale, not the CMO's) |

### Countries — VARIANT-HEAVY (329 items)

Spelling variants (`USA`/`US`/`United States`/`United States of America`;
`UK`/`United Kingdom`), region terms that are not countries (`Europe` ×15,
`Asia` ×13, `North America` ×9, `Americas` ×3, `global` ×2), 10 whole-cell
NDs. Not needed for the first two gates; a normalisation table is designed
but its build-out can trail v1.

### Size-signal coverage — the headline survey number

**Only 28/59 entities (47%) have ANY numeric size signal (employees or
revenue). 30 have neither.** Whatever the giant-gate thresholds are, the
missing-data policy decides more of the shortlist than the thresholds do.

---

## 2. Findings: what makes gating unreliable as-built

F1. **Missing data dominates.** A hard giant-gate with `missing_policy:
    exclude` shortlists from ≤28 candidates before any scoring happens.
    The policy choice is the design decision, not a footnote.

F2. **Parent-company revenue attribution.** Arrk's cell carries Mitsui's
    $10.5B; Phillips Medisize's carries ~$125B (Koch). A naive revenue gate
    excludes mid-size subsidiaries as "giants" — or, under the other
    reading, correctly excludes members of giant groups. Either semantics
    is defensible; the layer must pick one *explicitly* and flag the cells.

F3. **Subset counts and non-answers pass a naive numeric parse.**
    `more than 100 skilled engineers` would gate Oechsler as a 100-person
    company. Keyword guards (`engineers`, `staff`, `team`, `planned`,
    `outlook`, `first half`) are required and must live in the spec as
    data, like everything else.

F4. **Staleness is invisible to a bare parse.** `15 (2005)` is two decades
    old. Year annotations must be captured and a max-age policy applied
    (default: flag, don't drop).

F5. **Pipeline error propagates into gate verdicts.** Eval (final59,
    2026-08-03): independence F1 0.786 (P 0.815), employees 0.692, revenue
    0.818. Roughly 1 in 5 independence answers disagrees with GT — a hard
    gate turns each single-cell error into a shortlist membership change.
    This is exactly what evaluation level 2 (below) measures; it is not a
    reason not to build, it is the reason the eval design exists.

F6. **Marker cells (conflict / unverified / demoted rivals) have unknown
    frequency** on this machine. The parser handles the closed grammar;
    verify counts on the laptop's raw workbook before trusting flag rates.

F7. **`None (not disclosed)` is an answer, not an absence** (the pipeline's
    abstention discipline). The gate layer must distinguish it from
    `No data found` (nothing extracted at all): both are "unknown" but the
    first is a *verified* unknown. Output labels them differently.

---

## 3. Open questions (answers wanted before build)

Q1. **Giant semantics under parent attribution (F2):** does group
    membership in a giant count as giant? Recommended default: yes-but-
    flagged (`PARENT_ATTRIBUTION`), because the deterministic detector
    (non-year parenthetical / "exceeding" + implausible magnitude) is
    reliable enough to flag but not to silently discard.

Q2. **Missing-policy defaults for the two launch gates.** Recommended:
    independence `flag`, size `flag` (pass-with-flag) — given F1, `exclude`
    on size would gut the candidate pool. George may prefer `exclude` for
    a client who insists on disclosed financials; that is precisely why it
    is a spec field, and why the eval includes the policy-flip scenario.

Q3. **Independence gate direction:** exclude on explicit `No` only, with
    `Not disclosed` flagged-through? Or require explicit `Yes`? Recommended:
    exclude on `No`, flag ND (6 entities affected).

Q4. **Actual thresholds for "giant"** (e.g. employees > 10,000; revenue >
    USD 1B?). Analyst's call — it's data, but v1 ships with defaults.

Q5. **Output placement:** separate shortlist workbook (recommended — the
    pipeline artefact stays pristine, matching the read-only constraint) or
    a sheet appended to a copy of the pipeline output?

Q6. **Does an analyst's own shortlist exist** (or can one be produced from
    the GT matrix by Caitlin/JH) for evaluation level 1? Without it, level
    1 cannot run and only levels 2 + sensitivity are measurable.

Q7. **FX table:** static coarse rates in the spec (GBP/CHF/SEK/EUR→USD),
    reviewed manually? Order-of-magnitude gates don't need live FX;
    recommended yes.

Q8. **Unverified values:** usable for gating with an `UNVERIFIED` flag
    (recommended), or verified-only?

---

## 4. Design (recommendation)

### 4.1 Criteria spec — a workbook sheet, versioned like GT

Workbook `cmo-inputs/shortlist_criteria.xlsx`, sheet `Criteria`, one row per
criterion:

| field | values | notes |
|---|---|---|
| id | slug | stable key for output columns |
| question | exact Matrix column text | fuzzy-matched ≥0.70 like the evaluator |
| type | `hard_gate` \| `scored` | gates run first; all gates must pass |
| parser | `binary` \| `count` \| `money` \| `country_set` \| `text` | |
| direction | `require_yes` \| `require_no` \| `max` \| `min` \| `prefer_high` \| `prefer_low` \| `range` | |
| threshold_lo / threshold_hi | numbers | gate bound(s) / scoring knee(s) |
| weight | float | scored criteria only |
| missing_policy | `exclude` \| `pass` \| `flag` | applies to ND, No-data, AND unparseable |
| unverified_policy | `use_flagged` \| `verified_only` | default `use_flagged` |
| max_age_years | int, blank = no check | staleness flag (F4) |
| notes | free text | analyst-facing rationale |

Aux sheets, all data: `FX` (currency→USD), `Guards` (keyword lists for
subset/forecast/non-answer detection, F3), `Countries` (variant→canonical +
region terms). Launch spec ships the two hard gates:

```
independence  | Is the company still operating independently? | hard_gate | binary | require_yes* | flag
emp_giant     | How many employees does the company have?     | hard_gate | count  | max  10000   | flag
rev_giant     | What is the company's yearly revenue?         | hard_gate | money  | max  1e9 USD | flag
```
(*Q3 pending: `require_yes` with ND flagged-through, i.e. only explicit `No`
excludes. "Giant" trips if EITHER size gate fails — independent gates give
this AND-pass/OR-exclude semantics for free; no composite-gate machinery in v1.)

### 4.2 Parsers (deterministic, per type)

Shared pre-pass (grammar): strip bullets; detect and record cell flags
`CONFLICT`, `UNVERIFIED` (whole-cell or per-item section), `DEMOTED_RIVALS(N)`,
`CAPPED(N)`; `No data found` → NO_DATA; `None (not disclosed)` → ND.

- **binary** — the eval's proven typed rule (`Yes`/`No`/`true`/`false` bare
  tokens); anything else → UNPARSEABLE.
- **count** — strip qualifiers (the eval's `_NUM_QUALIFIER_RE` semantics:
  `over`, `more than`, `around`, `~`, trailing `+` → qualifier recorded,
  lower bound kept); commas removed; `(YYYY)` captured as vintage; ranges
  `a-b`/`a to b` → both bounds; Guards hit (`engineers`, `staff`, `team`,
  `planned`, …) → SUBSET_OR_NON_ANSWER → UNPARSEABLE with reason.
- **money** — symbol/code table + scale words (`M`/`B`/`million`/`bn`/`MSEK`);
  amount×scale→USD via FX sheet; `(YYYY…)` vintage; Guards (`outlook`,
  `first half`, `H1`, `planned`) → FORECAST/PARTIAL flag; non-year
  parenthetical containing letters → PARENT_ATTRIBUTION flag (F2/Q1);
  missing currency → assume USD + NO_CURRENCY flag; ranges → both bounds.
- **country_set** — split items, map variants via Countries sheet; region
  terms → REGION_ONLY flag (not a country claim). Not used by launch gates.

Gate comparison uses the bound that the qualifier guarantees (`over 20,000`
vs max-10,000 ⇒ definitive FAIL; `over 1,500` vs max-10,000 ⇒ PASS — the
guaranteed-side bound decides; when the qualifier leaves the gate genuinely
undecidable, verdict INDETERMINATE → missing_policy applies).

Cells that resist deterministic parsing (census: `a staff of design
experts`, subset counts, and any Guards hit) are **never guessed**: they take
`missing_policy` with an explicit PARSE_FAIL reason in the output.

### 4.3 Unknown handling — three distinct visible states (F7)

| state | source | gate behaviour | output label |
|---|---|---|---|
| ND | `None (not disclosed)` | per missing_policy | `NOT DISCLOSED` |
| NO_DATA | `No data found` | per missing_policy | `NO DATA` |
| PARSE_FAIL | unparseable / Guards | per missing_policy | `UNPARSEABLE: <reason>` |

`exclude` → entity excluded, reason = criterion id. `pass` → silent pass
(discouraged; exists for completeness). `flag` → passes, carries the flag
into the output row. Excluded-by-gate, passed-with-flag, and missing-data
entities are three visibly different things in the sheet.

### 4.4 Scoring and ranking

Gates first (any FAIL ⇒ excluded, criterion recorded). Survivors scored:
each `scored` criterion normalised to [0,1] piecewise-linear against its
thresholds, weighted sum **renormalised over the criteria present for that
entity**, with a `coverage` column (fraction of scored-criterion weight that
had data) shown beside the total — no hidden penalty, no hidden imputation.
Deterministic tie-break: score, then coverage, then entity name.

### 4.5 Output — one sheet, all 59 rows, nothing silently dropped

`Shortlist` sheet in a separate workbook (Q5), four visually-separated
blocks: **Top-k** (default 5) → **ranked remainder** → **excluded** (with
`excluded_by` criterion + the raw source cell text) → **no-data/flagged-out**.
Columns: rank, entity, website, total, coverage, then per criterion:
verdict/score, parsed value, raw cell excerpt (traceability to the source
cell), flags. Styled like the eval report (Run Info sheet with spec file
hash + code version, glossary of verdicts and flags).

### 4.6 Code placement — new files only

`src/shortlist.py` (spec reader, parsers, gate/score engine — pure
functions) + `scripts/shortlist_cmo.py` (CLI: pipeline workbook + criteria
workbook in, shortlist workbook out). No changes to any existing module.
Tests: the census examples above become the parser fixture set verbatim.

---

## 5. Evaluation design (in-proposal, built with the layer)

Two-level comparison, isolating *criteria fidelity* from *data-quality cost*:

- **Level 1 — criteria fidelity:** `shortlist(criteria, GT-matrix)` vs the
  analyst's own shortlist (Q6). Disagreement means the encoded criteria
  don't capture analyst judgement — a spec problem, not a pipeline problem.
  Metrics: overlap@k, plus a per-disagreement narrative (k is small; read
  the five names, don't just score them).
- **Level 2 — data-quality cost:** `shortlist(criteria, pipeline-matrix)`
  vs `shortlist(criteria, GT-matrix)`, same spec. Isolates what pipeline
  extraction errors cost the decision. Metrics: overlap@k, rank correlation
  (Kendall τ over the union), and a per-gate confusion table (entities
  whose gate verdict flips GT↔pipeline — F5 made concrete).
- **Sensitivity analysis:** perturb each threshold ±10 % and ±25 % and each
  weight ±25 % (one-at-a-time), rerun, report overlap@k with the base
  shortlist; plus the **missing-policy flip** (`flag`↔`exclude` on the size
  gates) as a named scenario. A shortlist that dissolves under small
  perturbations is a criteria-risk finding worth more than the list itself.

All three run from one command; deterministic, so re-runs are free.

## 6. Explicitly out of v1 (gated follow-ups)

- **LLM normaliser** for PARSE_FAIL cells (`a staff of design experts` →
  null; `first half of 2026` → annualise?): only after v1 measures how much
  is actually lost deterministically, and only through a label-set gate
  (the CE-promotion precedent — validated against human labels before it
  decides anything).
- Countries-based criteria (needs the normalisation table matured).
- Composite gates (OR-groups), soft gates, per-client spec profiles.

## Acceptance bars

1. Same inputs (workbook + spec) ⇒ byte-identical shortlist, any machine.
2. Every excluded entity's row shows the criterion and raw cell that did it.
3. Parser fixture suite: every census form above parses to the documented
   result (incl. every PARSE_FAIL staying a PARSE_FAIL — no guessing).
4. The three unknown states render distinguishably in the output sheet.
5. Zero diffs outside `src/shortlist.py`, `scripts/shortlist_cmo.py`,
   `cmo-inputs/shortlist_criteria.xlsx`, tests.
