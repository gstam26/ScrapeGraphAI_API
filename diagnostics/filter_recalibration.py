"""Filter recalibration: instruction-aware routing queries, before/after AUC.

Measures the 2026-07-03 filter fix (embed column name + instruction instead
of the 2-3 word name alone — see brain/proposals/filter-and-synthesis.md)
on the completed validation run, with zero Firecrawl credits and no LLM:

BEFORE (offline, any machine): reads the baseline workbook's Filter Log
(name-only scores logged under passthrough) and Provenance, labels each
(page, question) pair answered=True iff it produced >=1 non-null Claim, and
computes per-question + overall AUC (Mann-Whitney pairwise-wins) of the
logged embedding score vs that label. Expected overall ~0.64.

AFTER (work laptop only — needs Ollama + the cached validation pages):
re-scores the SAME cached pages with OLD (name-only) and NEW
(name + instruction) queries via local embeddings, recomputes both AUC
tables against the same answered labels, and sweeps thresholds 0.40-0.70
(step 0.01) reporting per-question precision/recall/F1 and the best-F1
threshold. If Ollama is unreachable the script prints the BEFORE table and
a boxed instruction to run the AFTER half on the work laptop — it never
crashes on a missing endpoint or missing cache files.

Questions/instructions are read from adlm-inputs/validation_sample_input.xlsx
when present; otherwise a QUESTIONS constant copied verbatim from
build_182_workbook.py is used (the script prints which source was used).

Usage:
    python diagnostics/filter_recalibration.py
    python diagnostics/filter_recalibration.py --baseline path.xlsx --cache-dir cache --out out.xlsx
"""
import argparse
import json
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import OLLAMA_DOC_PREFIX, OLLAMA_HOST, OLLAMA_QUERY_PREFIX
from src.acquire.cache import cache_path_any
from src.embed import embed_batch
from src.filter import _chunk_text, _cosine

DEFAULT_BASELINE = "adlm-outputs/validation_sample_run_2026-07-02_v2.xlsx"
DEFAULT_CACHE_DIR = "cache"
DEFAULT_OUT = "adlm-outputs/filter_recalibration.xlsx"
QUESTIONS_XLSX = "adlm-inputs/validation_sample_input.xlsx"

# Fallback copied verbatim from build_182_workbook.py questions_df — used only
# when QUESTIONS_XLSX is absent (same 4 ADLM questions + instructions).
QUESTIONS = {
    "R&D location":
        "In which country or countries does the company conduct its R&D? List each "
        "location separately; include city or region if stated. Check headquarters, "
        "locations, laboratories, or about pages.",
    "Company type":
        "Does the company develop and market its own branded diagnostic products, or does "
        "it make products for other companies (OEM / contract manufacturing / white-label)? "
        "Answer own-product, OEM/contract, or both, based on how the company describes itself.",
    "Diagnostics type":
        "Which types of clinical diagnostics does the company provide? List each distinct "
        "diagnostic area, technology, or assay type separately.",
    "Recent news":
        "What recent news or announcements has the company published — product launches, "
        "regulatory clearances, funding, partnerships, and similar? List each item "
        "separately, with its date if given.",
}

THRESHOLDS = [round(0.40 + 0.01 * i, 2) for i in range(31)]  # 0.40 .. 0.70

# Cross-encoder scores are sigmoid(logit), distributed nothing like cosine —
# sweep the whole range rather than the cosine band.
CE_THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05 .. 0.95


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_url(url) -> str:
    return str(url).strip().rstrip("/")


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """Mann-Whitney AUC via pairwise wins: P(score_pos > score_neg) + 0.5 ties."""
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def _auc_table(rows: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """Per-question + Overall AUC of rows[score_col] vs rows['answered']."""
    out = []
    for q, sub in rows.groupby("question"):
        pos = sub.loc[sub["answered"], score_col].tolist()
        neg = sub.loc[~sub["answered"], score_col].tolist()
        out.append({"Question": q, "N": len(sub), "Answered": len(pos),
                    "AUC": _auc(pos, neg)})
    pos = rows.loc[rows["answered"], score_col].tolist()
    neg = rows.loc[~rows["answered"], score_col].tolist()
    out.append({"Question": "Overall", "N": len(rows), "Answered": len(pos),
                "AUC": _auc(pos, neg)})
    df = pd.DataFrame(out)
    df["AUC"] = df["AUC"].map(lambda a: round(a, 3) if a is not None else None)
    return df


def _print_table(title: str, df: pd.DataFrame) -> None:
    print(f"\n================ {title} ================")
    print(df.to_string(index=False))


def _answered_pairs(prov: pd.DataFrame) -> set[tuple[str, str]]:
    """(normalised Source URL, Question) pairs with >=1 non-null Claim."""
    has_claim = prov["Claim"].notna() & (prov["Claim"].astype(str).str.strip() != "")
    sub = prov.loc[has_claim, ["Source URL", "Question"]]
    return {(_norm_url(u), str(q)) for u, q in zip(sub["Source URL"], sub["Question"])}


def _ollama_reachable(timeout_s: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST.rstrip('/')}/api/tags", timeout=timeout_s) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except Exception:
        return False


def _load_questions() -> tuple[dict[str, str], str]:
    """Return {name: instruction} and a note on where they came from."""
    if os.path.exists(QUESTIONS_XLSX):
        qdf = pd.read_excel(QUESTIONS_XLSX, "questions")
        qs = {str(r["question"]): str(r["instructions"]).strip()
              for _, r in qdf.iterrows()}
        return qs, f"questions read from {QUESTIONS_XLSX}"
    return dict(QUESTIONS), "questions from embedded QUESTIONS constant (copied from build_182_workbook.py)"


# ── BEFORE: baseline Filter Log vs Provenance ────────────────────────────────

def before_table(baseline: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (AUC table, labelled row-level frame) from the baseline workbook."""
    xl = pd.ExcelFile(baseline)
    flog = pd.read_excel(xl, "Filter Log")
    prov = pd.read_excel(xl, "Provenance")

    answered = _answered_pairs(prov)

    rows = flog[["URL", "Column", "Embedding Score"]].copy()
    rows.columns = ["url", "question", "score"]
    rows = rows.dropna(subset=["score"])
    rows["url"] = rows["url"].map(_norm_url)
    rows["question"] = rows["question"].astype(str)
    rows["answered"] = [
        (u, q) in answered for u, q in zip(rows["url"], rows["question"])
    ]
    return _auc_table(rows, "score"), rows


# ── AFTER: re-score cached pages with old vs new queries ────────────────────

def _page_scores(chunks: list[str], query_embs: list[list[float]]) -> list[float]:
    """Max cosine per query across chunk embeddings (chunks already embedded)."""
    return [max((_cosine(c, q) for c in chunks), default=0.0) for q in query_embs]


def after_tables(
    baseline: str,
    cache_dir: str,
    questions: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, int, int]:
    """Re-score all baseline pages; return AUC tables, scores, sweep, bests."""
    xl = pd.ExcelFile(baseline)
    acq = pd.read_excel(xl, "Acquire Log")
    prov = pd.read_excel(xl, "Provenance")
    answered = _answered_pairs(prov)

    names = list(questions.keys())
    old_queries = [OLLAMA_QUERY_PREFIX + n for n in names]
    new_queries = [OLLAMA_QUERY_PREFIX + f"{n}. {questions[n]}" for n in names]
    q_embs = embed_batch(old_queries + new_queries)
    old_embs, new_embs = q_embs[:len(names)], q_embs[len(names):]

    urls = sorted({_norm_url(u) for u in acq["Page URL"].dropna()})
    score_rows: list[dict] = []
    scored_pages = 0
    cache_misses = 0

    for i, url in enumerate(urls, 1):
        path = cache_path_any(url, cache_dir)
        if path is None:
            cache_misses += 1
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = _chunk_text(text)
        if not chunks:
            cache_misses += 1
            continue
        try:
            chunk_embs = embed_batch([OLLAMA_DOC_PREFIX + c for c in chunks])
        except Exception as e:
            print(f"  ! embed failed for {url[:70]} ({e}); skipping")
            cache_misses += 1
            continue
        old_s = _page_scores(chunk_embs, old_embs)
        new_s = _page_scores(chunk_embs, new_embs)
        for name, os_, ns_ in zip(names, old_s, new_s):
            score_rows.append({
                "url": url, "question": name,
                "old_score": round(os_, 4), "new_score": round(ns_, 4),
                "answered": (url, name) in answered,
            })
        scored_pages += 1
        if i % 25 == 0 or i == len(urls):
            print(f"  scored {scored_pages}/{i} pages (of {len(urls)} total, {cache_misses} skipped)")

    scores = pd.DataFrame(score_rows)
    if scores.empty:
        raise RuntimeError("no cached pages could be scored — is --cache-dir right?")

    auc_old = _auc_table(scores, "old_score")
    auc_new = _auc_table(scores, "new_score")

    # Threshold sweep: precision/recall/F1 of "score >= t" predicting answered.
    sweep_rows: list[dict] = []
    for variant, col in (("old (name only)", "old_score"), ("new (name+instruction)", "new_score")):
        for q, sub in scores.groupby("question"):
            y = sub["answered"].to_numpy()
            s = sub[col].to_numpy()
            for t in THRESHOLDS:
                pred = s >= t
                tp = int((pred & y).sum())
                fp = int((pred & ~y).sum())
                fn = int((~pred & y).sum())
                prec = tp / (tp + fp) if tp + fp else 0.0
                rec = tp / (tp + fn) if tp + fn else 0.0
                f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
                sweep_rows.append({
                    "Variant": variant, "Question": q, "Threshold": t,
                    "Precision": round(prec, 3), "Recall": round(rec, 3),
                    "F1": round(f1, 3),
                })
    sweep = pd.DataFrame(sweep_rows)
    best = (
        sweep.sort_values(["Variant", "Question", "F1", "Threshold"],
                          ascending=[True, True, False, True])
        .groupby(["Variant", "Question"], as_index=False).first()
    )
    return auc_old, auc_new, scores, sweep, best, scored_pages, cache_misses


# ── CROSS-ENCODER leg: ms-marco pairwise relevance vs embedding cosine ───────

def cross_encoder_tables(
    baseline: str,
    cache_dir: str,
    questions: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, int, int]:
    """Score the SAME cached pages with the local cross-encoder (query =
    name + instruction, the production query form; page score per question =
    max over chunks) and compute the same AUC table + threshold sweep, so the
    CE column is directly comparable with the embedding AUC on this page set.
    Needs no Ollama — only the cached pages and the local model files
    (src/eval/cross_encoder.py; HuggingFace is blocked on-network, so the
    model must already exist locally)."""
    from src.eval.cross_encoder import CrossEncoderScorer

    scorer = CrossEncoderScorer()
    # Load the model BEFORE the page loop: a load failure must abort the CE
    # leg once (caught by main's fail-soft wrapper), not be retried per page
    # (2026-07-21 work-laptop run: SSL-broken HF HEAD checks re-attempted
    # for every URL because only score_pairs was in the try block).
    scorer.ensure_ready()
    xl = pd.ExcelFile(baseline)
    acq = pd.read_excel(xl, "Acquire Log")
    prov = pd.read_excel(xl, "Provenance")
    answered = _answered_pairs(prov)

    names = list(questions.keys())
    queries = [f"{n}. {questions[n]}" for n in names]

    urls = sorted({_norm_url(u) for u in acq["Page URL"].dropna()})
    score_rows: list[dict] = []
    scored_pages = 0
    cache_misses = 0

    for i, url in enumerate(urls, 1):
        path = cache_path_any(url, cache_dir)
        if path is None:
            cache_misses += 1
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = _chunk_text(text)
        if not chunks:
            cache_misses += 1
            continue
        pairs = [(q, c) for q in queries for c in chunks]
        try:
            flat = scorer.score_pairs(pairs)
        except Exception as e:
            print(f"  ! cross-encoder failed for {url[:70]} ({e}); skipping")
            cache_misses += 1
            continue
        n_chunks = len(chunks)
        for qi, name in enumerate(names):
            page_score = max(flat[qi * n_chunks:(qi + 1) * n_chunks])
            score_rows.append({
                "url": url, "question": name,
                "ce_score": round(page_score, 4),
                "answered": (url, name) in answered,
            })
        scored_pages += 1
        if i % 25 == 0 or i == len(urls):
            print(f"  CE-scored {scored_pages}/{i} pages (of {len(urls)} total, "
                  f"{cache_misses} skipped)")

    scores = pd.DataFrame(score_rows)
    if scores.empty:
        raise RuntimeError("no cached pages could be CE-scored — is --cache-dir right?")

    auc_ce = _auc_table(scores, "ce_score")

    sweep_rows: list[dict] = []
    for q, sub in scores.groupby("question"):
        y = sub["answered"].to_numpy()
        s = sub["ce_score"].to_numpy()
        for t in CE_THRESHOLDS:
            pred = s >= t
            tp = int((pred & y).sum())
            fp = int((pred & ~y).sum())
            fn = int((~pred & y).sum())
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            sweep_rows.append({
                "Variant": "cross-encoder (name+instruction)", "Question": q,
                "Threshold": t, "Precision": round(prec, 3),
                "Recall": round(rec, 3), "F1": round(f1, 3),
            })
    sweep = pd.DataFrame(sweep_rows)
    return auc_ce, scores, sweep, scored_pages, cache_misses


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Filter recalibration: name-only vs name+instruction routing queries"
    )
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--cross-encoder", action="store_true",
                    help="ALSO score the cached pages with the local "
                         "cross-encoder (src/eval/cross_encoder.py) and add "
                         "its AUC + sweep — the A/B against the embedding "
                         "scorer. Needs the model files locally; no Ollama.")
    args = ap.parse_args()

    # BEFORE — pure workbook analysis, no network needed.
    auc_before, before_rows = before_table(args.baseline)
    _print_table("BEFORE -- baseline Filter Log (name-only queries) AUC", auc_before)

    sheets: dict[str, pd.DataFrame] = {
        "BEFORE AUC": auc_before,
        "BEFORE Rows": before_rows,
    }

    # AFTER — needs Ollama + the cached validation pages.
    if not _ollama_reachable():
        cmd = (f"python diagnostics/filter_recalibration.py "
               f"--baseline {args.baseline} --cache-dir {args.cache_dir} --out {args.out}")
        width = max(len(cmd) + 6, 66)
        print()
        print("+" + "-" * width + "+")
        for line in (
            "Ollama is not reachable from this machine "
            f"({OLLAMA_HOST}).",
            "The AFTER measurement (re-scoring cached pages with old vs new",
            "queries) must run on the WORK LAPTOP, where Ollama and the",
            "validation page cache live. Run there:",
            "",
            f"   {cmd}",
        ):
            print("| " + line.ljust(width - 2) + " |")
        print("+" + "-" * width + "+")
    else:
        questions, q_source = _load_questions()
        print(f"\nAFTER -- re-scoring cached pages ({q_source})")
        auc_old, auc_new, scores, sweep, best, n_ok, n_miss = after_tables(
            args.baseline, args.cache_dir, questions
        )
        print(f"\nScored {n_ok} pages; {n_miss} skipped (cache miss / empty / embed error)")
        _print_table("AFTER -- re-scored, OLD queries (name only) AUC", auc_old)
        _print_table("AFTER -- re-scored, NEW queries (name + instruction) AUC", auc_new)
        _print_table("Best-F1 threshold per question", best)
        sheets.update({
            "AFTER AUC old": auc_old,
            "AFTER AUC new": auc_new,
            "Page Scores": scores,
            "Threshold Sweep": sweep,
            "Best Thresholds": best,
        })

    # Cross-encoder leg — independent of Ollama; needs local model files.
    if args.cross_encoder:
        questions, q_source = _load_questions()
        print(f"\nCROSS-ENCODER -- scoring cached pages ({q_source})")
        try:
            auc_ce, ce_scores, ce_sweep, n_ok, n_miss = cross_encoder_tables(
                args.baseline, args.cache_dir, questions
            )
            print(f"\nCE-scored {n_ok} pages; {n_miss} skipped")
            _print_table("CROSS-ENCODER (name + instruction) AUC "
                         "[compare vs embedding AUC on same pages]", auc_ce)
            sheets.update({
                "CE AUC": auc_ce,
                "CE Page Scores": ce_scores,
                "CE Threshold Sweep": ce_sweep,
            })
        except Exception as e:  # noqa: BLE001 — CE leg must not kill the report
            print(f"\n  ! cross-encoder leg failed ({type(e).__name__}: {e})")
            print("    (model files present locally? sentence-transformers installed?)")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with pd.ExcelWriter(args.out, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
