"""Plot the CMO GT-scoring results: presentation-ready PNGs in one command.

Reads a generic_eval output workbook (Summary + Detail sheets) and writes:

  outputs/plot_eval_per_question.png   precision / recall / F1 per question,
                                       sorted by F1 (which question types the
                                       pipeline handles well vs badly)
  outputs/plot_eval_per_entity.png     TP / FN / FP composition per entity;
                                       with --baseline, each bar is annotated
                                       with the entity's fetched page count
                                       (the acquire-starvation story: Arrk at
                                       2 pages cannot answer 15 questions)

Matplotlib only, no seaborn. Captions carry the honesty caveats: GT covers a
subset of entities, and list-question precision is a lower bound (GT lists are
non-exhaustive by design).

Usage:
    python scripts/plot_cmo_eval.py
    python scripts/plot_cmo_eval.py --eval outputs/cmo_eval_provenance.xlsx
    python scripts/plot_cmo_eval.py --baseline path/to/cmo_output_v2_FULL_baseline.xlsx
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")  # file output only; no display needed
import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = "outputs"

# Detail-sheet verdicts -> scoring class. redundant / suppressed_null are
# neutral by construction (dropped from the precision denominator) and are
# excluded from the composition bars rather than miscounted. null_match is
# plotted separately from substantive TP: a starved entity that answers
# nothing "agrees" with every not-disclosed GT cell, and folding that into
# TP would hide exactly the starvation the plot exists to show.
_TP_VERDICTS = {"auto_match", "semantic_review"}
_NULL_VERDICTS = {"null_match"}
_FN_VERDICTS = {"auto_miss", "no_ai_data"}
_FP_VERDICTS = {"ai_only"}


def _short(question: str, limit: int = 48) -> str:
    q = str(question).strip().rstrip("?")
    return q if len(q) <= limit else q[: limit - 1] + "…"


def _list_questions(detail: pd.DataFrame) -> set[str]:
    if "is_list" not in detail.columns:
        return set()
    flagged = detail[detail["is_list"].astype(str).str.lower().isin({"true", "1"})]
    return set(flagged["question"].astype(str))


def plot_per_question(summary: pd.DataFrame, detail: pd.DataFrame,
                      out_dir: str, source_name: str) -> str:
    rows = summary[summary["question"] != "OVERALL"].copy()
    rows = rows.sort_values("F1")
    lists = _list_questions(detail)
    labels = [
        _short(q) + (" (list)" if q in lists else "")
        for q in rows["question"]
    ]

    y = range(len(rows))
    height = 0.26
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(rows) + 2))
    ax.barh([i + height for i in y], rows["precision"], height,
            label="precision", color="#1565C0")
    ax.barh(list(y), rows["recall"], height, label="recall", color="#2E7D32")
    ax.barh([i - height for i in y], rows["F1"], height,
            label="F1", color="#6A1B9A")
    ax.set_yticks(list(y), labels, fontsize=8)
    ax.set_xlim(0, 1.02)
    ax.set_xlabel("score")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3, axis="x")

    overall = summary[summary["question"] == "OVERALL"]
    headline = ""
    if not overall.empty:
        headline = f" — overall F1 {float(overall.iloc[0]['F1']):.3f}"
    n_entities = detail["entity"].nunique()
    ax.set_title(f"CMO v2 baseline vs analyst GT ({source_name}){headline}", pad=12)
    fig.text(0.5, -0.01,
             f"GT covers {n_entities} of 69 entities (partial handoff); "
             "(list) precision = lower bound, GT lists non-exhaustive.",
             ha="center", va="top", fontsize=9, style="italic", color="#555555")
    fig.tight_layout()
    path = os.path.join(out_dir, "plot_eval_per_question.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _pages_per_entity(baseline_path: str, entities: list[str]) -> dict[str, int]:
    """Fetched-page count per GT entity from the run workbook's Acquire Log.
    Best-effort: returns {} rather than guessing when the workbook or sheet
    is missing, and the caller skips the annotation."""
    try:
        aq = pd.read_excel(baseline_path, sheet_name="Acquire Log")
        ok = aq[aq["Status"].astype(str).isin(["ok", "gate_failed"])]
        counts = ok.groupby("Entities")["Page URL"].count()
    except Exception:
        return {}
    return {e: int(counts[e]) for e in entities if e in counts.index}


def plot_per_entity(detail: pd.DataFrame, out_dir: str,
                    baseline_path: str | None) -> str:
    verdicts = detail["verdict"].astype(str)
    comp = pd.DataFrame({
        "TP": verdicts.isin(_TP_VERDICTS),
        "TP (null agreement)": verdicts.isin(_NULL_VERDICTS),
        "FN": verdicts.isin(_FN_VERDICTS),
        "FP": verdicts.isin(_FP_VERDICTS),
    })
    comp["entity"] = detail["entity"].astype(str)
    comp = comp.groupby("entity").sum().sort_values("TP")

    pages = _pages_per_entity(baseline_path, list(comp.index)) if baseline_path else {}

    fig, ax = plt.subplots(figsize=(9, 0.6 * len(comp) + 1.8))
    left = pd.Series(0, index=comp.index)
    for col, colour in (("TP", "#2E7D32"), ("TP (null agreement)", "#A5D6A7"),
                        ("FN", "#C62828"), ("FP", "#F9A825")):
        ax.barh(comp.index, comp[col], left=left, label=col, color=colour)
        left = left + comp[col]
    if pages:
        for i, entity in enumerate(comp.index):
            if entity in pages:
                ax.text(left[entity] + 0.5, i, f"{pages[entity]} pages fetched",
                        va="center", fontsize=8, color="#555555")
        ax.set_xlim(0, left.max() * 1.35)
    ax.set_xlabel("scored items")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3, axis="x")
    title = "Score composition per entity"
    if pages:
        title += " — starved entities (few pages) miss across every question"
    ax.set_title(title, pad=12)
    fig.tight_layout()
    path = os.path.join(out_dir, "plot_eval_per_entity.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="plot CMO GT-scoring results")
    ap.add_argument("--eval", default=os.path.join(OUT_DIR, "cmo_eval_matrix.xlsx"),
                    help="generic_eval output workbook (Summary + Detail sheets)")
    ap.add_argument("--baseline", default=None,
                    help="pipeline run workbook; adds fetched-page annotation "
                         "to the per-entity plot (acquire-starvation view)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    if not os.path.exists(args.eval):
        sys.exit(f"eval workbook not found: {args.eval} — run generic_eval first")
    summary = pd.read_excel(args.eval, sheet_name="Summary")
    detail = pd.read_excel(args.eval, sheet_name="Detail")
    source_name = os.path.splitext(os.path.basename(args.eval))[0]

    os.makedirs(args.out_dir, exist_ok=True)
    written = [
        plot_per_question(summary, detail, args.out_dir, source_name),
        plot_per_entity(detail, args.out_dir, args.baseline),
    ]
    print("Plots written:")
    for w in written:
        print(f"  {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
