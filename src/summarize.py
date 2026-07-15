"""
LLM summary layer — synthesized prose over verified, grouped claims.

Design: brain/proposals/llm-summary-layer.md (approved 2026-07-06/07). The
summarizer consumes the grouped-theme structure (diag["claim_groups"]), not
raw claims, so the verified-only guarantee is inherited from group.py's
single choke point (_display_values) and every input claim carries a
Provenance claim ID the prose must cite.

Properties honoured here:

  * WALLED OFF — output goes to diag["cell_summaries"] only; result.rows and
    every existing sheet are byte-identical whether this layer runs or not.
  * FAIL-SOFT — a missing AZURE_API_KEY raises once at entry (the pipeline
    wraps the call and skips the sheet); per-call failures are captured in
    the summary record and surface as a visible Digest-line fallback row,
    never silently.
  * CITED — every sentence must cite [C####] claim IDs from the closed input
    set. The Tier-1 mechanical gate below (no LLM, deterministic) fails a
    summary to its Digest line; gate false-positives therefore fail SAFE
    (deterministic text shown instead of prose).
  * NON-DETERMINISM REDUCED AND AUDITED — temperature=0 + fixed seed
    (honoured on this deployment, probe 2026-07-07), with system_fingerprint,
    exact prompt and raw response recorded per call for the Summary Log.

The Tier-2 LLM-judge is deliberately NOT here — it is a post-run diagnostics
pass (diagnostics/summary_judge.py), not part of the deliverable pipeline.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config import (
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    AZURE_ENDPOINT,
    SUMMARY_MAX_CLAIMS_PER_THEME,
    SUMMARY_MAX_CONCURRENT_CALLS,
    SUMMARY_MAX_ITEMS_PER_LINE,
    SUMMARY_MAX_LINES_PER_CELL,
    SUMMARY_SEED,
    SUMMARY_TAG_MAX_CHARS,
    SUMMARY_TIMEOUT,
)
from src.group import ALL_ITEMS_THEME
from src.io_excel import _norm_claim, build_claim_index

# Bumped whenever the prompt template changes — output is never compared
# across prompt versions (design §3). s3 (2026-07-08, scaffolding round 2 of
# 2, George-directed): dropped the 2-4 sentence FLOOR — it contradicted the
# no-interpretation rule for one-tag cells (Company type), forcing the model
# to pad ("this means the company...") or emit filler; 13/18 judge flags on
# the 07b run were that self-inflicted pattern.
# s4 (2026-07-14, George reopened the 07-08 format decision — compact
# analyst format, brain/proposals/summary-compact-format.md): render the
# themes instead of narrating them — one line per theme,
# "label: items [cites]", capped items with a visible overflow marker; the
# gate/judge unit becomes the line for multi-line output. The s3 ship bars
# do NOT transfer: automated eval legs must re-run on s4 output before any
# faithfulness claim.
# s5 (2026-07-14, same day — George's eyeball test on real CMO output
# failed s4 two ways): (a) "one line per theme" had no cell-level cap, so
# an 11-theme Description cell rendered as an 11-line wall; s5 caps at
# SUMMARY_MAX_LINES_PER_CELL covering the largest themes. (b) CMO theme
# labels are whole verbatim claim sentences (not ADLM-style short tags),
# and s4's "label: items" made the model ECHO the label then restate it as
# the content; s5 has the model write a 2-5 word topic itself and
# synthesize members instead of enumerating them.
# s6 (2026-07-14, from the s5 review): two observed over-reach patterns
# banned explicitly. (a) Range-blending: claims {80, 330, 3000} employees
# (different DECADES, from a /history page) became "between 80 and 3,000
# employees" — a statement no source makes. (b) Absence assertions: "No
# evidence X manufactures in China" is an inference about the corpus, not
# a claim's content. Both are cited-but-unsupported — the worst kind.
# 2026-07-15 ROUTING change (prompt unchanged, version stays s6): the
# deterministic route below now covers every all-short-values cell, not just
# single-tag cells — binary verdicts, numbers, categories and location-style
# lists render verbatim with per-value citations and no LLM call (George's
# analyst-format direction: "Yes/No, a number, a list — easy on the eye";
# Provenance carries the depth). The LLM path is reserved for cells with
# prose-length claims, where synthesis actually adds something.
# s7 (2026-07-15, George's s6c eyeball): three changes from the first CMO
# check of the routed output. (a) MERGE route: verbatim rendering showed the
# same fact under variant spellings ("Tempe, Arizona" / "Tempe, AZ" /
# "Tempe, AZ 85288 USA"), each with its own citation — string matching can't
# know US=USA=United States, so multi-value short cells now go to the LLM
# with a dedicated merge prompt (pool citations of same-meaning variants,
# never merge different numbers/places). (b) Bare boolean claims are pulled
# out BEFORE any LLM call and rendered as a deterministic verdict — on the
# s6c run 3 of 6 gate failures were the coverage gate demanding a citation
# from a '"True" (2 items)' theme the model rightly ignored. (c) The prose
# prompt template itself is UNCHANGED from s6.
PROMPT_VERSION = "s7"

# Citation parsing. The model batches IDs inside one bracket —
# "[C0183, C0184, C0185]" — and sometimes chains brackets "[C0183][C0184]".
# The 2026-07-07 laptop eval showed the old single-ID-per-bracket regex
# (r"\[(C\d{4,})\]") registered every multi-ID bracket as UNCITED, failing
# 72/89 summaries at the gate on a parser bug, not a model fault. Match any
# bracket containing >=1 claim ID, then pull all IDs from inside it.
_CITED_BRACKET_RE = re.compile(r"\[[^\[\]]*?C\d{4,}[^\[\]]*?\]")
_CLAIM_ID_RE = re.compile(r"C\d{4,}")


def cited_ids(text: str) -> list[str]:
    """All claim IDs cited anywhere in text (multi-ID brackets expanded)."""
    ids: list[str] = []
    for bracket in _CITED_BRACKET_RE.findall(text or ""):
        ids.extend(_CLAIM_ID_RE.findall(bracket))
    return ids


def has_citation(text: str) -> bool:
    """True if text carries >=1 bracketed claim-ID citation."""
    return _CITED_BRACKET_RE.search(text or "") is not None

# Unit split shared by the Tier-1 gate, the Tier-2 judge and the eval legs.
# s4 output is one line per theme, so multi-line text splits on newlines
# (defensively stripping bullet markers the prompt forbids); single-line
# text keeps the s3-era sentence split, so older workbooks re-judge
# unchanged. Sentence fragments created by splitting after a known
# abbreviation are merged back — the 2026-07-07 laptop eval showed company
# names ("Aalto Scientific Ltd.", "U.S.") chopping prose into citation-less
# fragments that failed the gate and mis-fed the judge. Unknown abbreviations
# still over-split, which only ever FAILS a summary toward its deterministic
# Digest line — the safe direction.
# A unit that is ONLY the overflow marker is our own mandated text, not a
# claim — it must not count as an uncited sentence. On the s6c CMO run the
# model placed "(more in Provenance)" after the final period of single-line
# output, and the sentence splitter turned it into a citation-less fragment
# that failed the gate (Tecan Systems Integration, Mack Medical device).
_PROVENANCE_MARKER_RE = re.compile(r"^\(?\s*more in provenance\s*\)?\s*[.!]?$", re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREV_END_RE = re.compile(
    r"(?:\b(?:Inc|Ltd|Corp|Co|LLC|GmbH|No|Dr|Mr|Ms|Mrs|St|Jr|Sr|vs|approx|est)"
    r"|\be\.g|\bi\.e|\bU\.S|\bU\.K)\.$",
    re.IGNORECASE,
)
_BULLET_PREFIX_RE = re.compile(r"^[•\-\*]\s+")


def _split_sentences(text: str) -> list[str]:
    if "\n" in (text or "").strip():
        return [
            _BULLET_PREFIX_RE.sub("", line.strip())
            for line in text.splitlines()
            if line.strip()
        ]
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _ABBREV_END_RE.search(merged[-1]):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)
    return merged


def _join_units(units: list[str], like: str) -> str:
    """Rejoin units split by _split_sentences, preserving the original shape:
    newline-joined when the source was multi-line (s4), space-joined
    otherwise (s3 prose). Used by the eval corruption legs so a corrupted
    multi-line summary stays multi-line and unit indices stay aligned."""
    return ("\n" if "\n" in (like or "").strip() else " ").join(units)


# Boolean claim vocabulary for the deterministic answer route. Deliberately
# tight: only bare yes/no/true/false (any case, trailing punctuation ignored)
# count as verdicts. Anything else — "Yes, via subcontractors", "No details
# disclosed" — is NOT a verdict and renders verbatim like any other claim.
_BOOL_TRUE = {"yes", "y", "true"}
_BOOL_FALSE = {"no", "n", "false"}
_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!]+$")


def _bool_class(value: str) -> str | None:
    v = _TRAILING_PUNCT_RE.sub("", str(value)).strip().lower()
    if v in _BOOL_TRUE:
        return "Yes"
    if v in _BOOL_FALSE:
        return "No"
    return None


def _verdict_segment(pairs: list[tuple[str, str]]) -> str | None:
    """Collapse the bare boolean claims among pairs into one cited verdict
    segment, or None when there are none. A genuine yes/no split renders both
    sides — never a merged verdict no source states."""
    yes_ids = [cid for cid, v in pairs if _bool_class(v) == "Yes"]
    no_ids = [cid for cid, v in pairs if _bool_class(v) == "No"]
    if yes_ids and no_ids:
        return f"Conflicting: Yes [{', '.join(yes_ids)}] / No [{', '.join(no_ids)}]"
    if yes_ids:
        return f"Yes [{', '.join(yes_ids)}]"
    if no_ids:
        return f"No [{', '.join(no_ids)}]"
    return None


def deterministic_answer(pairs: list[tuple[str, str]]) -> str | None:
    """Render a cell's citable (claim_id, value) pairs as a compact verbatim
    answer line: verdict first (see _verdict_segment), every other value
    verbatim as "value [Cid]", '; '-joined, capped at
    SUMMARY_MAX_ITEMS_PER_LINE with the visible "(more in Provenance)"
    overflow marker. Returns None when any value is longer than
    SUMMARY_TAG_MAX_CHARS (prose — the LLM path's job).

    Faithful by construction — every rendered token is a verified claim or a
    citation. Since s7 this render is used two ways: directly for cells with
    <=1 non-boolean value (nothing to merge), and as the visible FALLBACK for
    the LLM merge route (an analyst-readable degradation, unlike the old
    "N items across M themes" digest line George rejected on the s6c check).
    """
    if not pairs or any(len(v) > SUMMARY_TAG_MAX_CHARS for _, v in pairs):
        return None

    others = [(cid, v) for cid, v in pairs if _bool_class(v) is None]
    parts: list[str] = []
    verdict = _verdict_segment(pairs)
    if verdict:
        parts.append(verdict)

    shown = others[: SUMMARY_MAX_ITEMS_PER_LINE]
    parts.extend(f"{v} [{cid}]" for cid, v in shown)

    line = "; ".join(parts)
    if len(others) > len(shown):
        line += " (more in Provenance)"
    return line


def _merge_prompt(entity: str, question: str, pairs: list[tuple[str, str]]) -> str:
    """Prompt for the s7 merge route: multi-value short cells where verbatim
    rendering repeats the same fact under variant spellings. The one job the
    LLM adds over the deterministic render is SEMANTIC deduplication —
    knowing "U.S." = "USA" = "United States" — which no string metric does.
    Bare booleans are handled by rule (verdict first) so a pure-verdict cell
    never reaches here; mixed cells keep one uniform output line."""
    values = "\n".join(f"[{cid}] {v}" for cid, v in pairs)
    return (
        f"You are compiling the verified extracted answers about {entity} "
        f'for the question "{question}" into one compact line an analyst can '
        "scan instantly.\n"
        "Each value below carries its claim ID. Rules (all mandatory):\n"
        '1. Output exactly ONE line: the distinct answers joined by "; ", '
        "each as <answer> [claim IDs].\n"
        "2. Merge values that say the same thing (abbreviation, spelling, "
        'phrasing or subset variants, e.g. "USA" / "U.S." / "United States") '
        "into ONE entry: keep the clearest wording among them VERBATIM and "
        "pool all their claim IDs into its bracket.\n"
        "3. NEVER merge values with different meanings: different numbers, "
        "dates, places or scopes each keep their own entry and citations. "
        "Never invent a range, total or combined figure no value states.\n"
        '4. Bare yes/true values become the single entry "Yes" (pool their '
        'IDs); bare no/false become "No". A verdict goes first. If both '
        'appear, start with: Conflicting: Yes [ids] / No [ids].\n'
        "5. Use only words that appear in the values — no interpretation, "
        "no explanation, no extra text.\n"
        f"6. At most {SUMMARY_MAX_ITEMS_PER_LINE} entries; if more remain "
        'after merging, keep the first ones and end the line with '
        '"(more in Provenance)".\n'
        "Values:\n" + values
    )


def _theme_fallback(
    entity: str,
    question: str,
    groups: list[dict],
    claim_index: dict,
) -> str:
    """Analyst-readable fallback for a failed prose-cell LLM call: the top
    themes' MEDOID labels — real verified claim strings, never synthesized —
    each cited with its resolvable members' pooled IDs. Replaces the
    "N items across M themes" digest bookkeeping in the AI Summary sheet
    (the Digest sheet itself is unchanged). Capped at
    SUMMARY_MAX_LINES_PER_CELL lines with the standard overflow marker."""
    lines: list[str] = []
    skipped = 0
    for group in groups:
        if len(lines) == SUMMARY_MAX_LINES_PER_CELL:
            skipped += 1
            continue
        ids = []
        first_value = None
        for value in group.get("values", []):
            hit = claim_index.get((entity, question, _norm_claim(value)))
            if hit and _bool_class(str(value)) is None:
                ids.append(hit[0])
                if first_value is None:
                    first_value = str(value).strip()
        if not ids:
            skipped += 1  # nothing resolvable, or a pure-boolean theme
            continue
        theme = group.get("theme", "")
        label = theme
        if theme == ALL_ITEMS_THEME or _bool_class(theme) is not None:
            label = first_value
        lines.append(f"{label} [{', '.join(ids)}]")
    if skipped and lines:
        lines[-1] += " (more in Provenance)"
    return "\n".join(lines)


def make_client():
    """OpenAI SDK client on the Azure /openai/v1 compat endpoint — the
    _extract_with_azure pattern. Raises on a missing key so callers fail
    once, up front, instead of per cell."""
    if not AZURE_API_KEY:
        raise RuntimeError("Missing AZURE_API_KEY in .env")
    from openai import OpenAI

    return OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)


def azure_chat(
    client,
    prompt: str,
    *,
    timeout: float = SUMMARY_TIMEOUT,
    seed: int = SUMMARY_SEED,
) -> dict:
    """One temperature-0, seeded chat call. Never raises — errors come back
    in the dict so one bad cell can't take down the batch. Shared by the
    summarizer and the post-run judge."""
    t0 = time.time()
    out: dict = {"text": None, "system_fingerprint": None, "error": None}
    try:
        completion = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            seed=seed,
            timeout=timeout,
        )
        out["text"] = completion.choices[0].message.content or ""
        out["system_fingerprint"] = getattr(completion, "system_fingerprint", None)
    except Exception as e:
        out["error"] = str(e)
    out["duration_ms"] = int((time.time() - t0) * 1000)
    return out


def _cell_prompt(
    entity: str,
    question: str,
    groups: list[dict],
    claim_index: dict,
    exclude_ids: set[str] | None = None,
) -> tuple[str, set[str], list[tuple[str, set[str]]]]:
    """Build one cell's prompt from its themes.

    Returns (prompt, input_ids, top_theme_id_sets):
      input_ids         — claim IDs actually SHOWN in the prompt (the closed
                          set citations are checked against; hidden overflow
                          members are excluded because the model never saw
                          their IDs).
      top_theme_id_sets — [(label, shown_ids)] for the top-3 real themes by
                          size, the same top-3 the Digest line cites (groups
                          arrive size-desc from group_rows); the coverage
                          gate requires >=1 citation from each.

    Members whose value doesn't resolve to a claim ID are omitted — an
    uncitable claim must not be paraphrasable. Truncation is principled:
    members are capped per theme (marked "+N more"), whole themes never drop.

    exclude_ids (s7): claims routed elsewhere — bare booleans rendered as a
    deterministic verdict — are kept out of the prompt AND the coverage
    sets, so a '"True" (2 items)' theme can no longer fail the gate by
    being a top theme the model rightly never cites.
    """
    exclude_ids = exclude_ids or set()
    input_ids: set[str] = set()
    top_theme_id_sets: list[tuple[str, set[str]]] = []
    blocks: list[str] = []

    for group in groups:
        pairs = []
        for value in group.get("values", []):
            hit = claim_index.get((entity, question, _norm_claim(value)))
            if hit and hit[0] not in exclude_ids:
                pairs.append((hit[0], str(value).strip()))
        if not pairs:
            continue

        shown = pairs[:SUMMARY_MAX_CLAIMS_PER_THEME]
        hidden = len(pairs) - len(shown)
        shown_ids = {cid for cid, _ in shown}
        input_ids |= shown_ids

        theme = group.get("theme", "")
        if theme == ALL_ITEMS_THEME:
            header = f"Claims ({len(pairs)} total, not grouped into themes):"
        else:
            header = f'Theme "{theme}" ({group.get("n_items", len(pairs))} claims):'
            if len(top_theme_id_sets) < 3:
                top_theme_id_sets.append((theme, shown_ids))

        lines = [header] + [f"[{cid}] {value}" for cid, value in shown]
        if hidden:
            lines.append(f"(+{hidden} more claims in this theme, not shown)")
        blocks.append("\n".join(lines))

    instructions = (
        f"You are compiling verified extracted claims about {entity} "
        f'for the question "{question}" into a summary an analyst can scan '
        "in five seconds.\n"
        "Claims are grouped into themes, LARGEST FIRST. Rules (all mandatory):\n"
        f"1. Output AT MOST {SUMMARY_MAX_LINES_PER_CELL} lines in total, "
        "covering the largest themes (the first listed). If any theme is "
        "left out, end the final line with '(more in Provenance)' before "
        "its citations.\n"
        "2. Each line has the form: <topic, 2-5 words>: <one compact "
        "statement of what the theme's claims say> [claim IDs]. Write the "
        "topic yourself — NEVER copy a whole claim as the topic, and NEVER "
        "repeat the theme's header text as the content.\n"
        "3. Synthesize, don't enumerate: merge near-duplicate claims into "
        "one statement instead of listing each variant. Only genuinely "
        "list-like answers (e.g. locations, certifications) are listed, at "
        f"most {SUMMARY_MAX_ITEMS_PER_LINE} distinct items.\n"
        "4. EVERY line must end with the claim ID(s) it draws from in square "
        "brackets, e.g. [C0042] or [C0042, C0043].\n"
        "5. State only what the cited claims say. No interpretation, no "
        "inference, no concluding line, no filler. A short label or category "
        "claim (e.g. 'own-product') is reported verbatim — never explain "
        "what it means.\n"
        "6. When cited values conflict (different numbers, yes vs no), "
        "report each value with its own citation — NEVER merge them into a "
        "range, average, or single verdict no source states.\n"
        "7. Never state that evidence is absent, lacking, or not found — "
        "simply omit what the claims do not say.\n"
        "8. Plain lines only: no headings, no bullet markers, no blank "
        "lines, no prose paragraphs."
    )
    prompt = instructions + "\n\n" + "\n\n".join(blocks)
    return prompt, input_ids, top_theme_id_sets


def mechanical_gate(
    text: str,
    input_ids: set[str],
    top_theme_id_sets: list[tuple[str, set[str]]],
) -> tuple[list[str], set[str], list[str]]:
    """Tier-1 gate (design §4): deterministic, free, runs inline.

    Returns (failure_reasons, cited_ids, uncited_sentences); empty reasons
    means pass. Checks: no invented citations (set membership against the
    shown input IDs), every sentence cites >=1 claim, and each top-3 theme
    is represented by >=1 citation from its member set.
    """
    reasons: list[str] = []
    cited = set(cited_ids(text))

    invented = cited - input_ids
    if invented:
        reasons.append("invented citation(s): " + ", ".join(sorted(invented)))

    sentences = _split_sentences(text or "")
    if not sentences:
        reasons.append("empty summary")
    uncited = [
        s for s in sentences
        if not has_citation(s) and not _PROVENANCE_MARKER_RE.match(s)
    ]
    if uncited:
        reasons.append(f"{len(uncited)} uncited sentence(s)")

    for label, ids in top_theme_id_sets:
        if ids and not (cited & ids):
            reasons.append(f'top theme not cited: "{label}"')

    return reasons, cited, uncited


def summarize_groups(claim_groups: list[dict], rows: list) -> list[dict]:
    """Summarize each grouped cell (one Azure call per cell) and gate the
    result. Returns diag["cell_summaries"] records (design §3):

      {entity, question, summary, cited_ids, uncited_sentences,
       input_claim_ids, gate, model, prompt_version, generated_at,
       system_fingerprint, prompt, raw_response, duration_ms, error}
    plus, on LLM-routed records since s7, fallback_text — the
    analyst-readable degradation io_excel shows when gate != pass.

    gate is "pass", "failed citation gate: ...", or "call failed: ..." —
    io_excel renders non-pass rows as their Digest line with the failure
    visible in the Faithfulness column.

    Raises only on a missing AZURE_API_KEY (before any LLM call, and only
    when at least one cell actually needs the LLM); run_pipeline wraps this
    call so that only skips the sheet.
    """
    # Same function the Provenance writer uses, so the IDs cited here are
    # exactly the IDs the workbook will carry.
    claim_index = build_claim_index(rows)

    cells: dict[tuple[str, str], list[dict]] = {}
    for group in claim_groups:
        key = (group.get("entity", ""), group.get("question", ""))
        cells.setdefault(key, []).append(group)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    deterministic: list[dict] = []
    jobs = []
    for (entity, question), groups in cells.items():
        # Collect ALL citable pairs across groups, uncapped — the per-theme
        # prompt cap exists for the LLM's context, not for routing.
        pairs: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for g in groups:
            for v in g.get("values", []):
                hit = claim_index.get((entity, question, _norm_claim(v)))
                if hit and hit[0] not in seen_ids:
                    seen_ids.add(hit[0])
                    pairs.append((hit[0], str(v).strip()))
        if not pairs:
            # Nothing citable — no summary row, mirroring "no group, no row".
            continue

        # s7 three-way routing (George's s6c review, 2026-07-15):
        #   deterministic — bare booleans and <=1 non-boolean short value;
        #                   nothing to merge, render verbatim, no LLM.
        #   merge         — 2+ short values; the LLM's one job is SEMANTIC
        #                   dedup ("Tempe, AZ" = "Tempe, Arizona"), pooling
        #                   citations of same-meaning variants. Fallback =
        #                   the verbatim render (readable, never digest
        #                   bookkeeping).
        #   prose         — any long value; the s6 synthesis prompt over the
        #                   NON-boolean claims, with the boolean verdict
        #                   prepended deterministically (a '"True" (2 items)'
        #                   top theme can no longer fail the coverage gate).
        bool_ids = {cid for cid, v in pairs if _bool_class(v) is not None}
        content = [(cid, v) for cid, v in pairs if cid not in bool_ids]
        verdict = _verdict_segment(pairs)
        all_short = all(len(v) <= SUMMARY_TAG_MAX_CHARS for _, v in content)

        if all_short and len(content) <= 1:
            rendered = deterministic_answer(pairs)
            deterministic.append({
                "entity": entity,
                "question": question,
                "summary": rendered,
                "cited_ids": sorted(set(cited_ids(rendered))),
                "uncited_sentences": [],
                "input_claim_ids": sorted(cid for cid, _ in pairs),
                "gate": "pass",
                "model": "deterministic-answer",
                "prompt_version": PROMPT_VERSION,
                "generated_at": generated_at,
                "system_fingerprint": None,
                "prompt": "",
                # The judge and the eval legs read the Summary Log's Raw
                # Response column (never the possibly-annotated sheet
                # cell). An empty string here made every tag cell
                # unjudgeable — 13 "no sentences" failures and 5 auto-miss
                # corruptions on the 2026-07-14 CMO s6 run. The rendered
                # line IS this deterministic path's raw response.
                "raw_response": rendered,
                "duration_ms": 0,
                "error": None,
            })
            continue

        if all_short:
            jobs.append({
                "entity": entity,
                "question": question,
                "prompt": _merge_prompt(entity, question, pairs),
                "input_ids": {cid for cid, _ in pairs},
                "all_ids": sorted(cid for cid, _ in pairs),
                "top_sets": [],  # flat values — no theme coverage to demand
                "prefix": None,  # merge rule 4 has the model place the verdict
                "fallback": deterministic_answer(pairs),
            })
            continue

        prompt, input_ids, top_sets = _cell_prompt(
            entity, question, groups, claim_index, exclude_ids=bool_ids)
        if not input_ids:
            continue
        jobs.append({
            "entity": entity,
            "question": question,
            "prompt": prompt,
            "input_ids": input_ids,
            "all_ids": sorted(cid for cid, _ in pairs),
            "top_sets": top_sets,
            "prefix": verdict,
            "fallback": _theme_fallback(entity, question, groups, claim_index),
        })

    if deterministic:
        print(f"  -> {len(deterministic)} short-value cell(s) rendered deterministically (no LLM call)")
    if not jobs:
        return deterministic

    client = make_client()
    print(f"  -> Summarizing {len(jobs)} grouped cells via Azure ({AZURE_DEPLOYMENT})...")
    # max_workers doubles as the global concurrency cap — these are the only
    # Azure calls this layer makes (EXTRACT_MAX_CONCURRENT_CALLS pattern).
    responses: list[dict | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=max(1, SUMMARY_MAX_CONCURRENT_CALLS)) as pool:
        futures = {pool.submit(azure_chat, client, job["prompt"]): i for i, job in enumerate(jobs)}
        for fut in as_completed(futures):
            responses[futures[fut]] = fut.result()

    out: list[dict] = list(deterministic)
    for job, resp in zip(jobs, responses):
        text = resp.get("text")
        if resp.get("error") is not None or text is None:
            gate = f"call failed: {resp.get('error') or 'no response'}"
            cited, uncited = set(), []
            text = ""
        else:
            reasons, cited, uncited = mechanical_gate(text, job["input_ids"], job["top_sets"])
            gate = "pass" if not reasons else "failed citation gate: " + "; ".join(reasons)
        # The deterministic verdict line (prose route) is part of the cell's
        # output: prepend it so the sheet, the judge and the eval legs all
        # see the same text. It is assembled AFTER the gate — the gate's
        # closed set is the prompt's ids, the verdict cites boolean ids the
        # model never saw.
        summary = text
        if gate == "pass" and job["prefix"]:
            summary = job["prefix"] + "\n" + text
            cited = set(cited) | set(cited_ids(job["prefix"]))
        out.append({
            "entity": job["entity"],
            "question": job["question"],
            "summary": summary,
            "cited_ids": sorted(cited),
            "uncited_sentences": uncited,
            "input_claim_ids": job["all_ids"],
            "gate": gate,
            "model": AZURE_DEPLOYMENT,
            "prompt_version": PROMPT_VERSION,
            "generated_at": generated_at,
            "system_fingerprint": resp.get("system_fingerprint"),
            "prompt": job["prompt"],
            "raw_response": summary if gate == "pass" else (resp.get("text") or ""),
            # Analyst-readable degradation for the AI Summary sheet: the
            # verbatim value render (merge route) or the top themes' medoid
            # claims (prose route) — never the "N items across M themes"
            # digest bookkeeping George rejected on the s6c check.
            "fallback_text": job["fallback"],
            "duration_ms": resp.get("duration_ms", 0),
            "error": resp.get("error"),
        })

    passed = sum(1 for s in out if s["gate"] == "pass")
    print(f"  -> Summaries: {passed}/{len(out)} passed the mechanical gate")
    return out
