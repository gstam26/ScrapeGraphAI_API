"""CE answerability A/B: score every (page, question) pair of a pipeline run
with the ms-marco cross-encoder (two query forms) and compare against the
run's logged embedding scores on two targets derived from the run itself:
  pair-yield: did this (page, question) produce >=1 extracted claim?
  page-yield: did this page produce any claim at all (the skip-a-call target)?
Reports AUC per scorer/form/target + recall-first operating points, and dumps
per-pair scores to <input_workbook_dir>/ce_ab_pair_scores.csv.

Needs page texts fetched first (diagnostics/fetch_run_pages.py). Caveat: a
refetch on another machine can differ slightly from the texts the run saw
(site drift); the yield targets come from the run's Provenance.

First result (2026-07-23, CMO rescue run, 2,023 pairs): pair-yield oracle
would remove 78% of question-slots; CE name-only AUC 0.772 vs embedding
0.671, but at zero claim-loss CE excludes only 5% (embedding 1%) — the safe
capture of the oracle is marginal for both. Page-yield: CE 0.620 ~= embedding
0.625 (max-over-questions destroys specificity; zero-yield is often
entity-attribution, invisible to text relevance) — page-skip refuted.

Usage:
    python diagnostics/ce_answerability_ab.py <run_workbook.xlsx> <pages_dir> <input_workbook.xlsx>
"""
import os
import sys
from itertools import product

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import numpy as np
import pandas as pd

from src.acquire.cache import read_cache
from src.filter import _chunk_text

if len(sys.argv) != 4:
    sys.exit("usage: ce_answerability_ab.py <run_workbook.xlsx> <pages_dir> <input_workbook.xlsx>")
WB, PAGES_DIR, INPUT_WB = sys.argv[1], sys.argv[2], sys.argv[3]

fl = pd.read_excel(WB, "Filter Log")
prov = pd.read_excel(WB, "Provenance")
qs = pd.read_excel(INPUT_WB, "questions")
q_instr = dict(zip(qs["question"], qs["instructions"]))

pair_yield = set(zip(prov["Source URL"], prov["Question"]))
urls = list(dict.fromkeys(fl["URL"]))
questions = list(dict.fromkeys(fl["Column"]))

texts = {}
missing = 0
for u in urls:
    t = read_cache(u, PAGES_DIR)
    if t:
        texts[u] = t
    else:
        missing += 1
print(f"pages with text: {len(texts)}/{len(urls)} (missing/errored {missing})")

from sentence_transformers import CrossEncoder
model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2")

FORMS = {
    "name_only": lambda q: q,
    "name_plus_instruction": lambda q: f"{q} {q_instr.get(q, '')}".strip(),
}

# Build all (query, chunk) jobs per form, then batch-predict once per form.
results = {}  # form -> {(url, q): max_logit}
for form, make_query in FORMS.items():
    jobs, keys = [], []
    for u in texts:
        chunks = _chunk_text(texts[u])[:12]  # cap: answerability shows early
        for q in questions:
            query = make_query(q)
            for c in chunks:
                jobs.append((query, c[:1500]))
                keys.append((u, q))
    print(f"[{form}] scoring {len(jobs)} pairs...")
    logits = model.predict(jobs, batch_size=64, show_progress_bar=False)
    best: dict = {}
    for k, s in zip(keys, logits):
        if k not in best or s > best[k]:
            best[k] = float(s)
    results[form] = best

def auc(pos, neg, cap=2000):
    import random
    random.seed(0)
    if len(neg) > cap:
        neg = random.sample(list(neg), cap)
    if not pos or not neg:
        return float("nan")
    return sum(1 for a, b in product(pos, neg) if a > b) / (len(pos) * len(neg))

emb = {(u, q): s for u, q, s in zip(fl["URL"], fl["Column"], fl["Embedding Score"])}

print("\n===== PAIR-YIELD (per-question routing) =====")
scored_pairs = [(u, q) for u in texts for q in questions]
y = [(u, q) in pair_yield for (u, q) in scored_pairs]
e_scores = [emb.get((u, q), 0) for (u, q) in scored_pairs]
print(f"positives: {sum(y)}/{len(y)}")
print(f"embedding AUC: {auc([s for s, t in zip(e_scores, y) if t], [s for s, t in zip(e_scores, y) if not t]):.3f}")
for form in FORMS:
    c = [results[form][(u, q)] for (u, q) in scored_pairs]
    print(f"CE {form} AUC: {auc([s for s, t in zip(c, y) if t], [s for s, t in zip(c, y) if not t]):.3f}")


def pair_operating_points(label, scores):
    pos_scores = sorted(s for s, t in zip(scores, y) if t)
    for recall, idx in (("100%", 0), ("98%", max(0, int(0.02 * len(pos_scores)) - 1)),
                        ("95%", max(0, int(0.05 * len(pos_scores)) - 1))):
        thr = pos_scores[idx]
        excl = sum(1 for s in scores if s < thr)
        lost = sum(1 for s, t in zip(scores, y) if t and s < thr)
        print(f"   [{label}] keep-recall {recall}: exclude {excl}/{len(scores)} "
              f"pairs ({excl/len(scores):.0%}), claim-pairs lost {lost}")


pair_operating_points("embedding", e_scores)
pair_operating_points("CE name_only", [results["name_only"][(u, q)] for (u, q) in scored_pairs])

# Dump all scores for later analysis without re-running the model.
dump = pd.DataFrame({
    "url": [u for (u, q) in scored_pairs],
    "question": [q for (u, q) in scored_pairs],
    "yield": y,
    "emb": e_scores,
    "ce_name_only": [results["name_only"][(u, q)] for (u, q) in scored_pairs],
    "ce_name_instr": [results["name_plus_instruction"][(u, q)] for (u, q) in scored_pairs],
})
dump_path = os.path.join(os.path.dirname(os.path.abspath(INPUT_WB)), "ce_ab_pair_scores.csv")
dump.to_csv(dump_path, index=False)
print(f"\npair scores dumped: {dump_path}")

print("\n===== PAGE-YIELD (skip the whole extract call) =====")
page_pos = {u for (u, q) in pair_yield}
py = [u in page_pos for u in texts]
print(f"pages with any claim: {sum(py)}/{len(py)}")
e_page = [max(emb.get((u, q), 0) for q in questions) for u in texts]
print(f"embedding max AUC: {auc([s for s, t in zip(e_page, py) if t], [s for s, t in zip(e_page, py) if not t]):.3f}")
for form in FORMS:
    c_page = [max(results[form][(u, q)] for q in questions) for u in texts]
    a = auc([s for s, t in zip(c_page, py) if t], [s for s, t in zip(c_page, py) if not t])
    print(f"CE {form} max AUC: {a:.3f}")
    # Recall-first operating points: skip rate when keeping N% of claim pages
    pos_scores = sorted(s for s, t in zip(c_page, py) if t)
    for recall, idx in (("100%", 0), ("98%", max(0, int(0.02 * len(pos_scores)) - 1)),
                        ("95%", max(0, int(0.05 * len(pos_scores)) - 1))):
        thr = pos_scores[idx]
        skipped = sum(1 for s in c_page if s < thr)
        lost = sum(1 for s, t in zip(c_page, py) if t and s < thr)
        print(f"   keep-recall {recall}: threshold {thr:.2f} -> skip "
              f"{skipped}/{len(c_page)} pages ({skipped/len(c_page):.0%}), claim-pages lost {lost}")
