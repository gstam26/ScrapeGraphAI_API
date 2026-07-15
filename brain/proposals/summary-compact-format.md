# AI Summary: compact analyst format (s4)

**Status:** PROPOSED 2026-07-14 — George reopened the 2026-07-08 "ship the
floor" format decision (his to reopen: the CMO case study makes an
analyst-facing deliverable the point, so readability is now a requirement,
not polish). Not built; awaiting George's format approval + one Nick check.

## What George asked for

"More compact and grouped — kind of what aggregate is doing but taking it a
step further. The most simple readable way that the analyst would understand
it. COMPACT."

## Design: render the themes, don't narrate them

The summarizer's input is ALREADY the grouped-theme structure (aggregate one
step further = grouping, which is built and calibrated). The s3 prompt asks
the model to weave themes into prose — that's where the comma-run sentences
and citation clutter come from. s4 instead renders **one line per theme**:

    <theme label>: <compact synthesis of that theme's claims> [cites]

**Before (s3 prose, real shape of the Hologic R&D cell):**

> Hologic conducts research and development in the United States, Germany,
> France, Costa Rica, Canada, the United Kingdom, ... (25 locations welded
> into one sentence) [C0102]. The company also maintains...

**After (s4 compact):**

> • R&D sites: US, Germany, France, Costa Rica (+21 more — see Provenance) [C0102, C0110]
> • Focus areas: cytology, molecular diagnostics [C0088, C0093]

Rules (s4 prompt): one line per theme, `label: items` shape, hard cap on
items per line with an explicit `(+N more — see Provenance)` overflow marker
(nothing hidden silently — same contract as MATRIX_MAX_DISPLAY_ITEMS),
citations clustered at end of line, no interpretive sentences, no filler.

## Tag-only cells: skip the LLM entirely

The unspent 2026-07-08 idea, now directly serving compactness: cells whose
grouped structure is a single short tag (Company type = one "own-product"
claim) render deterministically as `own-product [C0201]` — no model call.
Zero faithfulness risk, ~30% fewer summarizer calls, and maximally compact
by construction. The s3-era failure mode on these cells (gloss/filler) was
OUR floor rule forcing prose where none exists; deterministic routing
removes the possibility instead of prompting around it.

## Eval implications (the honest part)

The three passed ship bars (corruptions 0.972, self-agr 0.996, label 0.928)
were measured on s3 outputs. s4 changes sentence structure, so:

- **Judge contract unchanged** (every line must be supported by its cited
  claims) — the judge and Tier-1 gate work on lines exactly as on sentences;
  the gate's citation check applies per line.
- **Re-run required (automated, cheap):** corruptions + self-agreement legs
  on a fresh s4 workbook. Same pre-registered bars (0.90/0.90).
- **Label bar:** full ~50-label re-collection is overkill for a format
  change; proposal = George spot-labels ~20 s4 lines; if binary agreement
  holds ≥0.80 the bar stands. If it dips, full re-label before shipping.
- prompt_version bumps s3→s4 in the Summary Log; fingerprints recorded as
  before. The deterministic tag-route needs no eval (no model output).

## The one Nick check

LLM prose was Nick's original ask. Compact theme-lines are still an "AI
summary" (synthesis within each theme is model-written), but the register
changes from narrative to scan-optimized. One-line confirmation from Nick
that this serves the client deliverable before it ships client-facing.

## Decisions needed

- **George:** approve the line format + overflow cap default (proposed: 8
  items/line) + the deterministic tag-route; spot-label ~20 lines post-build.
- **Nick:** confirm compact register is what the deliverable wants.

## 2026-07-15 addendum: deterministic answer route (routing v2, prompt stays s6)

George's direction after hand-labelling the CMO s6 output (41 rows, all three
ship bars passed on CMO data): an analyst wants the ANSWER — Yes/No, a number,
a list — not prose about the answer, and Provenance already carries the
audit depth. The cells that read worst (EOL "confirms having end-of-line
testing capability"; the "8 items across 6 themes" digest fallback) were all
short-tag cells pushed through a prose prompt.

Change (src/summarize.py `deterministic_answer`): the s4 single-tag route now
covers EVERY cell whose citable values are all <= SUMMARY_TAG_MAX_CHARS:

- bare yes/y/true and no/n/false claims collapse to ONE cited verdict:
  "Yes [C0046, C0089]". A genuine split renders both sides, never a merged
  verdict: "Conflicting: Yes [C0046] / No [C0091]".
- every other short value renders verbatim: "MIL-STD 810 testing [C0037]",
  '; '-joined, capped at SUMMARY_MAX_ITEMS_PER_LINE with the standard
  "(more in Provenance)" overflow marker.
- one prose-length value anywhere -> whole cell keeps the LLM path (never
  mix verbatim and synthesized text inside one cell).

Consequences: binary/numeric/categorical/list questions never touch the LLM
(no hallucination surface, no gate failures, no digest fallbacks, fewer Azure
calls); the s6 prompt is UNCHANGED and stays reserved for prose cells
(Description, news, independence) where its 3-line synthesis passed George's
eyeball and the bars. Model column reads "deterministic-answer".

Eval implications: prompt unchanged -> no scaffolding round spent, no
re-label session owed. Next fresh workbook: re-run judge + corruptions +
self-agreement legs (automated) to confirm the population shift; judge is
already certified on two domains, so spot-checks suffice for the new
deterministic renders (faithful by construction — every token is a verified
claim or a citation).

NOT done (deliberate): no question-type metadata/config — claim-shape routing
needs none for the baseline; week-2 instructions can add per-question format
directives to the extractor AND (if still wanted) per-cell prompt directives,
measured as one before/after. Grouping/aggregate untouched (locked chain).

## 2026-07-15 addendum 2: s7 — merge route + fallback/gate fixes (George's s6c review)

The s6c check (first routed output, 5 entities) surfaced two George rejections:

1. **Verbatim rendering repeats one fact under variant spellings** —
   "Tempe, Arizona [C0056]; Tempe, AZ [C0099]; Tempe, AZ 85288 USA [C0050]".
   String metrics cannot know US=USA=United States; semantic dedup is
   exactly the LLM's job (George: "the whole point of having an LLM ... is
   to avoid having to read the same thing twice", with pooled references).
2. **Fallback cells (light orange) showed digest bookkeeping** ("6 items
   across 4 themes...") — useless to an analyst. Worse, most of those
   fallbacks were caused by two harness-side bugs, not model faults:
   3 of 6 gate failures were the coverage gate demanding a citation from a
   '"True" (2 items)' theme the model rightly ignored; 2 more were the
   model placing "(more in Provenance)" after the final period, which the
   sentence splitter turned into an "uncited sentence".

s7 changes (commit-level record in src/summarize.py):

- **Three-way routing.** deterministic (bare booleans + <=1 short value;
  verbatim, no LLM) / **merge** (2+ short values; new compact prompt: merge
  same-meaning variants keeping the clearest wording verbatim, pool their
  claim IDs into one bracket, NEVER merge different numbers/dates/places,
  verdict first, cap 8 + overflow marker) / prose (any long value; s6
  template unchanged, but bare booleans are excluded from the prompt and
  rendered as a deterministic verdict line prepended to the output).
- **Gate fixes.** Standalone "(more in Provenance)" units exempt from the
  uncited-sentence check; boolean claims can no longer form coverage-
  mandatory themes (they never enter the prompt).
- **Readable fallbacks.** Every LLM record carries fallback_text: the
  verbatim value render (merge route) or the top themes' medoid claims —
  real verified claim strings with pooled member citations (prose route).
  io_excel shows that on gate failure, marked "[fallback: verbatim claims
  — ...]"; the digest line remains only for pre-s7 workbooks. The Digest
  sheet itself is untouched.

Eval implications: PROMPT_VERSION bumped s6 -> s7 (new merge prompt; prose
template unchanged). Automated legs (judge, corruptions, self-agreement)
must re-run on the next fresh workbook; George spot-labels ~10 merge-route
lines (the new judged population — merge faithfulness = "did it pool the
right citations"). Deterministic and verdict segments stay faithful by
construction.
