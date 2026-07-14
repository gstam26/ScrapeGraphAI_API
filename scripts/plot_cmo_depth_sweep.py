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


def plot_depth_curve(df: pd.DataFrame, out_dir: str) -> str:
    ok = df[df["status"] == "ok"].sort_values("depth")
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))

    pct = ok["total_populated"] / ok["total_cells"] * 100
    axes[0].plot(ok["depth"], pct, "o-", color="#2E7D32", linewidth=2)
    for d, p, n in zip(ok["depth"], pct, ok["total_populated"]):
        axes[0].annotate(f"{n:.0f} ({p:.0f}%)", (d, p), textcoords="offset points",
                         xytext=(0, 9), ha="center", fontsize=9)
    axes[0].set_title("Answer coverage vs crawl depth")
    axes[0].set_xlabel("max crawl depth")
    axes[0].set_ylabel(f"populated cells (% of {int(ok['total_cells'].iloc[0])})")
    axes[0].set_ylim(0, 100)

    axes[1].plot(ok["depth"], ok["pages_fetched"], "s-", color="#1565C0", linewidth=2)
    for d, n in zip(ok["depth"], ok["pages_fetched"]):
        axes[1].annotate(f"{n:.0f}", (d, n), textcoords="offset points",
                         xytext=(0, 9), ha="center", fontsize=9)
    axes[1].set_title("Pages fetched vs crawl depth")
    axes[1].set_xlabel("max crawl depth")
    axes[1].set_ylabel("pages fetched")

    axes[2].plot(ok["depth"], ok["seconds"] / 60, "^-", color="#C62828", linewidth=2)
    for d, s in zip(ok["depth"], ok["seconds"]):
        axes[2].annotate(f"{s / 60:.1f}m", (d, s / 60), textcoords="offset points",
                         xytext=(0, 9), ha="center", fontsize=9)
    axes[2].set_title("Runtime vs crawl depth")
    axes[2].set_xlabel("max crawl depth")
    axes[2].set_ylabel("minutes")

    for ax in axes:
        ax.set_xticks(ok["depth"].tolist())
        ax.grid(alpha=0.3)
    fig.suptitle("CMO case study — depth sweep (5-entity fixed sample, 15 questions)",
                 fontsize=12, y=1.02)
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
