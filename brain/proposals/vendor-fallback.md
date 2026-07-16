# Vendor fallback: condition-triggered Firecrawl escalation for denied pages

**Status:** PROPOSED 2026-07-13 — awaiting George's design review, then leadership's
credit-spend approval. Not built.

## Problem

The 2026-07-13 stage-2 parity attribution proved every lost Matrix cell is
fetch-side (the cache-served model-only run scored 100% on all four questions).
Per-site live probes then split the losses into causes, and exactly one class
is unfixable self-hosted: **explicit denial**. Agilent's Akamai WAF returns
HTTP 403 "Access Denied" to both the static probe and the rendered browser
(103 chars either way). No polite self-hosted fetcher gets that page; Firecrawl
does, because circumventing anti-bot infrastructure is the paid service.
Diagnostics type — pre-registered bar item 1 — fails 92% vs the 0.95 bar on
Agilent (+ Aladdin, separate diagnosis pending).

Stealth/evasion in-house was considered and rejected (again — first rejected
in the 2026-07-02 decision log): it inverts the politeness posture that
Sagentia's IP-blocking history demands, it's a maintenance arms race against
Akamai-grade detection that non-technical consultants would inherit, and the
economics are absurd (days of fragile build to save ~15 credits/run).

## Design principle (George, 2026-07-13)

**The trigger is a detected condition, never a site name. The exception list
is an OUTPUT of each run (readable from the Acquire Log), not an input.**
A hardcoded domain list would be an ADLM artifact; this mechanism must
generalise unchanged to the plant-milk benchmark and to future advisory
tasks. Same rule the codebase already enforces for scorer terms, consent
selectors, and diagnostic defaults: generic and deterministic, no
site-specific rules.

## Trigger — Tier 1 (this proposal)

A page escalates to the vendor when BOTH hold:

1. the static fetch returned an explicit protocol-level denial
   (HTTP status in {401, 403, 429, 503}), AND
2. the render escalation still produced below-gate-minimum text
   (< QUALITY_MIN_CHARS after extraction, or the render errored).

Calibrated against the 2026-07-13 probe evidence:

| Case | Static | Render | Triggers? | Right? |
|---|---|---|---|---|
| Agilent (Akamai) | 403 | 103 ch | YES | yes — genuinely denied |
| Nova (Cloudflare) | 403 | 971 ch real content | no | yes — our browser beat the challenge; no credits needed |
| Hologic link-grids | 200 | ~300 ch | no | yes — genuinely thin nav pages; vendor text buys nothing |
| Neogen SPA | 200 | kept richer static (1ab666b) | no | yes — content already recovered |
| Monobind (commerce template) | 200 | 675 ch | no | honest gap — see Tier 2 |

## Mechanism

- On trigger: one Firecrawl markdown+rawHtml fetch for that page (the
  existing `_fetch_firecrawl_doc` path). Result replaces the denied page's
  text/html; provenance `backend="firecrawl_fallback"` so every vendor call
  is visible in the Acquire Log's Backend column. The per-run "exception
  list" leadership sees is a pivot on that column.
- **Budget cap:** `FALLBACK_MAX_PAGES_PER_RUN` (proposed default 30). At the
  cap, further triggers are logged (`gate_reason="fallback_budget_exhausted"`)
  but not fetched — a pathological input cannot silently drain credits.
- **Fail-soft:** no Firecrawl key present → page stays as the hybrid left it,
  reason logged. The pipeline never crashes on a missing key.
- **Off by default:** `VENDOR_FALLBACK_ENABLED = False` until leadership approves;
  env-overridable per the FILTER_MODE / SUMMARY_ENABLED convention.

## Cost model

Bounded by construction: (pages actually denied) × 1 credit, capped. On the
25-entity replay the only Tier-1 trigger is Agilent ≈ 14 pages ≈ 14 credits
per full run. Extrapolating to 178 companies: unknown but visible — the first
capped run measures it exactly, and the Acquire Log itemises it.

## Pre-registered validation (before any bar claim)

Re-run the 25-replay with the fallback enabled; expectations stated BEFORE
the run, per house rules:

1. Agilent's denied pages trigger; its Diagnostics type cell repopulates;
   Diagnostics type parity clears 0.95.
2. NO page without a protocol-level denial triggers (Nova, Hologic, Neogen,
   Aniara stay vendor-free). Any unexpected trigger is a finding to
   investigate, not a pass.
3. Total vendor calls ≈ 14. Materially more means the signature is too loose.

## Tier 2 — explicitly NOT in this proposal

Monobind-class losses (content present in HTML but not extractable as text,
even rendered — commerce templates) do not show a protocol denial, so Tier 1
leaves them lost. An entity-level trigger ("most of an entity's pages
gate-fail after render → vendor-fetch the seed") could recover them but risks
false credit burn on Hologic-class link-grid sites, which look similar by
that signature. Build only if parity evidence says Monobind-class losses
matter to a bar that matters; that is a George quality/cost call, and
Q1/Q4 (where those losses sit) are not bar item 1.

## Decisions needed

- **George:** design review of the trigger signature + cap default.
- **Leadership:** approval to enable spend (bounded, itemised per run in the
  Acquire Log), i.e. flip VENDOR_FALLBACK_ENABLED in the client-facing env.
