"""
GROUP_SIMILARITY calibration for the deterministic Grouped Themes layer.

Reads a run workbook's Provenance sheet, picks the N biggest (entity,
question) cells by claim count, embeds their claims via Ollama (one batch),
and prints cluster count + sizes at thresholds 0.50-0.75 (step 0.05) using
the exact production clustering (src.group.cluster_values) so the diagnostic
can never drift from the shipped logic.

Run this on the work laptop (Ollama reachable) to pick GROUP_SIMILARITY;
config.py's 0.62 is a starting default pending this calibration.

Usage:
    python diagnostics/group_calibration.py
    python diagnostics/group_calibration.py --baseline adlm-outputs/validation_sample_run_2026-07-02_v2.xlsx --top-n 5

Requires:
    Reachable Ollama host (internal network / VPN). Off-network the script
    probes with a short timeout, prints a clear message and exits 0.
"""

import argparse
import json
import os
import sys
import urllib.request

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from config import OLLAMA_DOC_PREFIX, OLLAMA_HOST
from src.embed import embed_batch
from src.group import _normalise_value, cluster_values

_PROBE_TIMEOUT_S = 4
_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
_DEFAULT_BASELINE = "adlm-outputs/validation_sample_run_2026-07-02_v2.xlsx"


def _ollama_reachable() -> bool:
    """Cheap probe with a short timeout so an off-network run fails fast."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST.rstrip('/')}/api/version")
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            json.loads(resp.read().decode("utf-8"))
        return True
    except Exception:
        return False


def _load_biggest_cells(baseline: str, top_n: int) -> list[tuple[str, str, list[str]]]:
    """Return the top_n biggest (entity, question) cells as distinct claim lists."""
    df = pd.read_excel(baseline, sheet_name="Provenance")
    cells: list[tuple[str, str, list[str]]] = []
    for (entity, question), group in df.groupby(["Entity", "Question"], sort=False):
        seen: set[str] = set()
        claims: list[str] = []
        for value in group["Claim"].tolist():
            text = str(value).strip()
            norm = _normalise_value(text)
            if not text or text.lower() == "nan" or norm in seen:
                continue
            seen.add(norm)
            claims.append(text)
        if claims:
            cells.append((str(entity), str(question), claims))
    cells.sort(key=lambda c: (-len(c[2]), c[0], c[1]))
    return cells[:top_n]


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate GROUP_SIMILARITY on a run workbook")
    parser.add_argument("--baseline", default=_DEFAULT_BASELINE,
                        help="Run workbook whose Provenance sheet supplies the claims")
    parser.add_argument("--top-n", type=int, default=5,
                        help="Number of biggest (entity, question) cells to calibrate on")
    args = parser.parse_args()

    if not os.path.exists(args.baseline):
        print(f"Baseline workbook not found: {args.baseline}")
        return 1

    cells = _load_biggest_cells(args.baseline, args.top_n)
    if not cells:
        print(f"No claims found in the Provenance sheet of {args.baseline}")
        return 1

    print(f"Baseline: {args.baseline}")
    print(f"Biggest {len(cells)} cells:")
    for entity, question, claims in cells:
        print(f"  {entity} / {question}: {len(claims)} distinct claims")

    if not _ollama_reachable():
        print(f"\nOllama unreachable at {OLLAMA_HOST} (probe timeout {_PROBE_TIMEOUT_S}s).")
        print("This calibration needs local embeddings — run it on the work laptop")
        print("(Sagentia network / VPN). Nothing was computed; exiting cleanly.")
        return 0

    # One batch for all cells, sliced per cell — mirrors group_rows.
    all_claims = [claim for _, _, claims in cells for claim in claims]
    print(f"\nEmbedding {len(all_claims)} claims in one batch...")
    vectors_flat = embed_batch([OLLAMA_DOC_PREFIX + c for c in all_claims])

    offset = 0
    for entity, question, claims in cells:
        vectors = {c: vectors_flat[offset + i] for i, c in enumerate(claims)}
        offset += len(claims)

        print(f"\n=== {entity} / {question} ({len(claims)} claims) ===")
        print(f"{'threshold':>9} | {'clusters':>8} | sizes (desc)")
        for threshold in _THRESHOLDS:
            clusters = cluster_values(claims, vectors, threshold=threshold)
            sizes = sorted((len(c) for c in clusters), reverse=True)
            shown = ", ".join(str(s) for s in sizes[:15])
            if len(sizes) > 15:
                shown += f", ... (+{len(sizes) - 15} more)"
            print(f"{threshold:>9.2f} | {len(clusters):>8} | {shown}")

    print("\nPick the threshold where big cells split into a handful of coherent")
    print("themes (not 1 giant cluster, not dozens of singletons) and set")
    print("GROUP_SIMILARITY in config.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
