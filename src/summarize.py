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
    SUMMARY_SEED,
    SUMMARY_TIMEOUT,
)
from src.group import ALL_ITEMS_THEME
from src.io_excel import _norm_claim, build_claim_index

# Bumped whenever the prompt template changes — prose is never compared
# across prompt versions (design §3). s3 (2026-07-08, scaffolding round 2 of
# 2, George-directed): dropped the 2-4 sentence FLOOR — it contradicted the
# no-interpretation rule for one-tag cells (Company type), forcing the model
# to pad ("this means the company...") or emit filler; 13/18 judge flags on
# the 07b run were that self-inflicted pattern.
PROMPT_VERSION = "s3"

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

# Sentence split shared by the Tier-1 gate and the Tier-2 judge. Fragments
# created by splitting after a known abbreviation are merged back into the
# previous sentence — the 2026-07-07 laptop eval showed company names
# ("Aalto Scientific Ltd.", "U.S.") chopping prose into citation-less
# fragments that failed the gate and mis-fed the judge. Unknown abbreviations
# still over-split, which only ever FAILS a summary toward its deterministic
# Digest line — the safe direction.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREV_END_RE = re.compile(
    r"(?:\b(?:Inc|Ltd|Corp|Co|LLC|GmbH|No|Dr|Mr|Ms|Mrs|St|Jr|Sr|vs|approx|est)"
    r"|\be\.g|\bi\.e|\bU\.S|\bU\.K)\.$",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _ABBREV_END_RE.search(merged[-1]):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)
    return merged


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
    """
    input_ids: set[str] = set()
    top_theme_id_sets: list[tuple[str, set[str]]] = []
    blocks: list[str] = []

    for group in groups:
        pairs = []
        for value in group.get("values", []):
            hit = claim_index.get((entity, question, _norm_claim(value)))
            if hit:
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
        f"You are summarizing verified extracted claims about {entity} "
        f'for the question "{question}".\n'
        "Claims are grouped into themes. Rules (all mandatory):\n"
        "1. Cite the claim ID(s) each statement draws from in square brackets, "
        "e.g. [C0042] or [C0042, C0043]. EVERY sentence must end with its own "
        "citation — do not gather all citations into the final sentence.\n"
        "2. State only what the cited claims say. Do NOT add interpretation, "
        "inference, or a concluding sentence (e.g. no 'this indicates', 'this "
        "suggests', 'these locations show'). If a claim is a short label or "
        "category (e.g. 'own-product'), report it verbatim with its citation "
        "and stop — never explain what the label means, and never add filler "
        "such as 'no additional information is provided'.\n"
        "3. Be brief: plain prose, at most 4 sentences, and as few as cover "
        "the themes — for a cell with one or two short claims, ONE short "
        "sentence is the correct answer. No headings, no bullet lists, no "
        "padding."
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
    uncited = [s for s in sentences if not has_citation(s)]
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

    gate is "pass", "failed citation gate: ...", or "call failed: ..." —
    io_excel renders non-pass rows as their Digest line with the failure
    visible in the Faithfulness column.

    Raises only on a missing AZURE_API_KEY (before any call); run_pipeline
    wraps this call so that only skips the sheet.
    """
    client = make_client()
    # Same function the Provenance writer uses, so the IDs cited here are
    # exactly the IDs the workbook will carry.
    claim_index = build_claim_index(rows)

    cells: dict[tuple[str, str], list[dict]] = {}
    for group in claim_groups:
        key = (group.get("entity", ""), group.get("question", ""))
        cells.setdefault(key, []).append(group)

    jobs = []
    for (entity, question), groups in cells.items():
        prompt, input_ids, top_sets = _cell_prompt(entity, question, groups, claim_index)
        if not input_ids:
            # Nothing citable — no summary row, mirroring "no group, no row".
            continue
        jobs.append((entity, question, prompt, input_ids, top_sets))

    print(f"  -> Summarizing {len(jobs)} grouped cells via Azure ({AZURE_DEPLOYMENT})...")
    # max_workers doubles as the global concurrency cap — these are the only
    # Azure calls this layer makes (EXTRACT_MAX_CONCURRENT_CALLS pattern).
    responses: list[dict | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=max(1, SUMMARY_MAX_CONCURRENT_CALLS)) as pool:
        futures = {pool.submit(azure_chat, client, job[2]): i for i, job in enumerate(jobs)}
        for fut in as_completed(futures):
            responses[futures[fut]] = fut.result()

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[dict] = []
    for (entity, question, prompt, input_ids, top_sets), resp in zip(jobs, responses):
        text = resp.get("text")
        if resp.get("error") is not None or text is None:
            gate = f"call failed: {resp.get('error') or 'no response'}"
            cited, uncited = set(), []
            text = ""
        else:
            reasons, cited, uncited = mechanical_gate(text, input_ids, top_sets)
            gate = "pass" if not reasons else "failed citation gate: " + "; ".join(reasons)
        out.append({
            "entity": entity,
            "question": question,
            "summary": text,
            "cited_ids": sorted(cited),
            "uncited_sentences": uncited,
            "input_claim_ids": sorted(input_ids),
            "gate": gate,
            "model": AZURE_DEPLOYMENT,
            "prompt_version": PROMPT_VERSION,
            "generated_at": generated_at,
            "system_fingerprint": resp.get("system_fingerprint"),
            "prompt": prompt,
            "raw_response": resp.get("text") or "",
            "duration_ms": resp.get("duration_ms", 0),
            "error": resp.get("error"),
        })

    passed = sum(1 for s in out if s["gate"] == "pass")
    print(f"  -> Summaries: {passed}/{len(out)} passed the mechanical gate")
    return out
