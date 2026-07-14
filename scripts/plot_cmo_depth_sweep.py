"""Plot the CMO depth-sweep results: presentation-ready PNGs in one command.

Reads the sweep runner's depth_sweep_summary.csv (and, when present, the
per-depth output workbooks for the entity view) and writes:

  cmo-outputs/plot_depth_curve.png     coverage / pages / runtime vs depth
                                       (the saturation-curve figure)
  cmo-outputs/plot_per_question.png    populated count per question per depth
                                       (which questions benefit from crawling)
  cmo-outputs/plot_per_entity.png      populated cells per entity at the
                                       deepest completed depth (the
                                       broken-seed story: entities whose seed
                                       is dead flatline regardless of depth)

Matplotlib only, no seaborn. Skips gracefully whatever inputs are missing so
a partial sweep still plots. Point --csv at a preserved copy to plot an older
sweep (the runner overwrites its CSV each time — copy it aside after a run
you want to keep, e.g. depth_sweep_summary_budget100.csv).

Usage:
    python scripts/plot_cmo_depth_sweep.py
    python scripts/plot_cmo_depth_sweep.py --csv cmo-outputs/depth_sweep_summary_budget100.csv
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")  # file output only; no display needed
import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = "cmo-outputs"
_EMPTY_MARKERS = {"", "no data found", "nan"}


def _populated(value) -> bool:
    return str(value).strip().lower() not in _EMPTY_MARKERS


def _budget_pinned_note(depths: list[int], out_dir: str) -> str | None:
    """Best-effort honesty check (George, 2026-07-14): at the deepest depth,
    how many entities sit at exactly the page budget? BFS spends a binding
    budget on shallow breadth, so depths beyond a pinned entity's cap are
    NOT a fresh measurement for it — flat cells past that point can be a
    budget artefact, not saturation. Returns a caption fragment, or None if
    it can't be determined (missing config/log) rather than guessing.
    """
    deepest = max(depths)
    cfg_path = os.path.join("cmo-inputs", f"cmo_input_named_depth{deepest}.xlsx")
    wb_path = os.path.join(out_dir, f"cmo_output_depth{deepest}.xlsx")
    if not (os.path.exists(cfg_path) and os.path.exists(wb_path)):
        return None
    try:
        cfg = pd.read_excel(cfg_path, sheet_name="config")
        row = cfg[cfg["setting"].astype(str).str.upper() == "CRAWL_MAX_PAGES"]
        if row.empty:
            return None
        budget = int(row.iloc[0]["value"])
        aq = pd.read_excel(wb_path, sheet_name="Acquire Log")
        per_entity = aq.groupby("Entities")["Page URL"].count()
        pinned = int((per_entity >= budget).sum())
        total = len(per_entity)
        if pinned == 0:
            return (f"Note: no entity reached the {budget}-page budget — "
                    f"flat segments are genuine saturation.")
        return (f"Note: {pinned}/{total} entities were capped at the {budget}-page "
                f"budget, so flat segments partly reflect the cap, not saturation.")
    except Exception:
        return None


def _claims_per_depth(depths: list[int], out_dir: str) -> dict[int, int]:
    """Provenance row count per depth — the enrichment signal populated-cell
    counts miss entirely (George, 2026-07-14: depth1 533 -> depth2 1,290
    claims while populated cells barely moved). Best-effort per depth."""
    out: dict[int, int] = {}
    for d in depths:
        wb = os.path.join(out_dir, f"cmo_output_depth{d}.xlsx")
        if not os.path.exists(wb):
            continue
        try:
            out[d] = len(pd.read_excel(wb, sheet_name="Provenance"))
        except Exception:
            pass
    return out


def _annotate(ax, xs, ys, labels) -> None:
    """Label points without collisions: on a flat run (repeated values) only
    the FIRST point of the run is labelled — four identical '69 (92%)' labels
    smashing into each other was the failure mode this replaces."""
    prev = object()
    for x, y, lab in zip(xs, ys, labels):
        if lab != prev:
            ax.annotate(lab, (x, y), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=9)
        prev = lab


def plot_depth_curve(df: pd.DataFrame, out_dir: str) -> str:
    ok = df[df["status"] == "ok"].sort_values("depth")
    depths = ok["depth"].astype(int).tolist()
    claims = _claims_per_depth(depths, out_dir)

    n_panels = 4 if claims else 3
    fig, axes = plt.subplots(1, n_panels, figsize=(3.5 * n_panels + 1, 4.2))

    pct = ok["total_populated"] / ok["total_cells"] * 100
    axes[0].plot(ok["depth"], pct, "o-", color="#2E7D32", linewidth=2)
    _annotate(axes[0], ok["depth"], pct,
              [f"{n:.0f} ({p:.0f}%)" for n, p in zip(ok["total_populated"], pct)])
    axes[0].set_title("Answer coverage", pad=12)
    axes[0].set_ylabel(f"populated cells (% of {int(ok['total_cells'].iloc[0])})")
    axes[0].set_ylim(0, 108)

    axes[1].plot(ok["depth"], ok["pages_fetched"], "s-", color="#1565C0", linewidth=2)
    _annotate(axes[1], ok["depth"], ok["pages_fetched"],
              [f"{n:.0f}" for n in ok["pages_fetched"]])
    axes[1].set_title("Pages fetched", pad=12)
    axes[1].set_ylabel("pages fetched")

    axes[2].plot(ok["depth"], ok["seconds"] / 60, "^-", color="#C62828", linewidth=2)
    _annotate(axes[2], ok["depth"], ok["seconds"] / 60,
              [f"{s / 60:.1f}m" for s in ok["seconds"]])
    axes[2].set_title("Runtime", pad=12)
    axes[2].set_ylabel("minutes")

    if claims:
        # The enrichment signal populated-cell counts miss entirely: cells
        # can be "done" at depth 1 yet carry far less evidence than the
        # same cells at depth 2 (2026-07-14 finding).
        cd = sorted(claims)
        cv = [claims[d] for d in cd]
        axes[3].plot(cd, cv, "D-", color="#6A1B9A", linewidth=2)
        _annotate(axes[3], cd, cv, [f"{v:,}" for v in cv])
        axes[3].set_title("Extracted claims (evidence volume)", pad=12)
        axes[3].set_ylabel("Provenance rows (claims)")

    for ax in axes:
        ax.set_xticks(ok["depth"].tolist())
        ax.set_xlabel("max crawl depth")
        ax.grid(alpha=0.3)
        # Headroom so point labels never collide with the panel title.
        if ax is not axes[0]:
            lo, hi = ax.get_ylim()
            ax.set_ylim(lo, hi * 1.15)

    note = _budget_pinned_note(depths, out_dir)
    fig.suptitle("CMO case study — depth sweep (5-entity fixed sample, 15 questions)",
                 fontsize=12)
    if note:
        # Short footnote at the bottom, out of the panels' way.
        fig.text(0.5, -0.02, note, ha="center", va="top",
                 fontsize=9, style="italic", color="#555555")
    fig.tight_layout()
    path = os.path.join(out_dir, "plot_depth_curve.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_per_question(df: pd.DataFrame, out_dir: str) -> str | None:
    ok = df[df["status"] == "ok"].sort_values("depth")
    qcols = [c for c in df.columns if c.startswith("q_")]
    if not qcols:
        return None
    mat = ok.set_index("depth")[qcols].T
    mat.index = [c[2:] for c in mat.index]  # strip "q_" prefix

    n_entities = int(ok["entities"].iloc[0]) if "entities" in ok.columns else int(mat.values.max())
    fig, ax = plt.subplots(figsize=(1.6 + 1.1 * len(mat.columns), 0.42 * len(mat) + 1.5))
    im = ax.imshow(mat.values, cmap="Greens", vmin=0, vmax=n_entities, aspect="auto")
    ax.set_xticks(range(len(mat.columns)), [f"depth {d}" for d in mat.columns])
    ax.set_yticks(range(len(mat)), mat.index, fontsize=8)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8,
                    color="white" if v > n_entities * 0.6 else "black")
    ax.set_title(f"Populated answers per question (out of {n_entities} entities)")
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    path = os.path.join(out_dir, "plot_per_question.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_per_entity(depths: list[int], out_dir: str) -> str | None:
    # Deepest completed depth whose output workbook exists.
    for depth in sorted(depths, reverse=True):
        wb = os.path.join(out_dir, f"cmo_output_depth{depth}.xlsx")
        if os.path.exists(wb):
            break
    else:
        return None
    matrix = pd.read_excel(wb, sheet_name="Matrix").set_index("Entity")
    counts = matrix.apply(lambda r: sum(_populated(v) for v in r), axis=1).sort_values()

    fig, ax = plt.subplots(figsize=(8, 0.5 * len(counts) + 1.5))
    bars = ax.barh(counts.index, counts.values, color="#2E7D32")
    ax.bar_label(bars, fontsize=9)
    ax.set_xlim(0, len(matrix.columns))
    ax.set_xlabel(f"populated cells (of {len(matrix.columns)} questions)")
    ax.set_title(f"Coverage per entity at depth {depth} — dead/stale seeds flatline")
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    path = os.path.join(out_dir, "plot_per_entity.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="plot CMO depth-sweep results")
    ap.add_argument("--csv", default=os.path.join(OUT_DIR, "depth_sweep_summary.csv"))
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"summary CSV not found: {args.csv} — run scripts/run_cmo_depth_sweep.py first")
    df = pd.read_csv(args.csv)
    ok = df[df["status"] == "ok"]
    if ok.empty:
        sys.exit("no successful depths in the summary CSV — nothing to plot")

    os.makedirs(args.out_dir, exist_ok=True)
    written = [plot_depth_curve(df, args.out_dir)]
    p = plot_per_question(df, args.out_dir)
    if p:
        written.append(p)
    p = plot_per_entity(ok["depth"].astype(int).tolist(), args.out_dir)
    if p:
        written.append(p)

    print("Plots written:")
    for w in written:
        print(f"  {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
