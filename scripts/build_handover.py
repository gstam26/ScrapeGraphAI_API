"""Rebuild the `handover` branch from main — one command, manifest-driven.

    python scripts/build_handover.py             # rebuild from local main
    python scripts/build_handover.py --push      # ...and push (force-with-lease)
    python scripts/build_handover.py --from <rev>

The handover branch is main's tree minus the exclusions in
scripts/handover_manifest.txt, with .gitignore replaced by the manifest's
trimmed version, squashed into a single orphan commit ("Entity extraction
pipeline"). This script IS the sanitisation process:

  1. read main's tree, drop every [exclude] path;
  2. leak scan every kept text file: a [fail-terms] hit ABORTS, a
     [warn-terms] hit prints for review;
  3. refuse any .xlsx/.csv outside samples/;
  4. build the tree with git plumbing (no working-tree churn) and compare to
     the current handover tip — IDENTICAL TREE = NO-OP, so re-running is
     always safe;
  5. otherwise write one fresh orphan commit (authored by the local git
     user) and move refs/heads/handover to it. History on handover is
     rewritten by design — it is a generated artifact, not a line of work.

The manifest was derived from the reviewed 2026-08-06 handover tree; the
no-op rebuild of that tree is the correctness proof. When main gains new
engagement files, add them to [exclude] — the term scan is the safety net
for anything forgotten.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "scripts", "handover_manifest.txt")
COMMIT_MSG = "Entity extraction pipeline"
BRANCH = "handover"

_TEXT_EXT = {".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini",
             ".json", ".html", ".js", ".css", ".bat", ".ps1", ".example", ""}


def _git(*args: str, input_text: str | None = None, env: dict | None = None) -> str:
    """Run git in BINARY mode: Python's text-mode pipes translate \\n to
    \\r\\n on Windows, which silently corrupts `update-index --index-info`
    input (every line rejected -> empty index -> empty tree)."""
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                       input=input_text.encode("utf-8") if input_text is not None else None,
                       env=e)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:3])}... failed:\n"
                         f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")


def read_manifest() -> dict:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in open(MANIFEST, encoding="utf-8").read().splitlines():
        line = raw.rstrip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = []
            continue
        if current == "gitignore":
            sections[current].append(line)      # verbatim, comments included
            continue
        if not line or line.lstrip().startswith("#"):
            continue
        if current:
            sections[current].append(line.strip())
    if not sections.get("exclude") or "gitignore" not in sections:
        raise SystemExit("manifest missing [exclude] or [gitignore] section")
    return sections


def excluded(path: str, rules: list[str]) -> bool:
    return any(path == r or (r.endswith("/") and path.startswith(r))
               for r in rules)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild the handover branch from main")
    ap.add_argument("--from", dest="base", default="main",
                    help="revision to build from (default: main)")
    ap.add_argument("--push", action="store_true",
                    help="push the result (force-with-lease) after building")
    args = ap.parse_args()

    man = read_manifest()
    base = _git("rev-parse", args.base).strip()
    print(f"Building handover from {args.base} ({base[:7]})")

    # 1. filter main's tree
    entries = []      # (mode, sha, path)
    for line in _git("ls-tree", "-r", base).splitlines():
        meta, path = line.split("\t", 1)
        mode, _type, sha = meta.split()
        if not excluded(path, man["exclude"]):
            entries.append((mode, sha, path))
    n_dropped = len(_git("ls-tree", "-r", base).splitlines()) - len(entries)
    print(f"  kept {len(entries)} files, excluded {n_dropped}")

    # 2. leak scan + 3. data-file check on the KEPT set
    fails, warns = [], []
    for mode, sha, path in entries:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".csv") and not path.startswith("samples/"):
            fails.append(f"{path}: data workbook outside samples/")
            continue
        if ext not in _TEXT_EXT:
            continue
        blob = _git("cat-file", "blob", sha).lower()
        for term in man.get("fail-terms", []):
            if term in blob:
                fails.append(f"{path}: FAIL term {term!r}")
        for term in man.get("warn-terms", []):
            if term in blob:
                warns.append(f"{path}: {term!r}")
    for w in warns:
        print(f"  [warn] accepted residual: {w}")
    if fails:
        print("\nBUILD ABORTED — confidential content in kept files:")
        for f in fails:
            print(f"  [FAIL] {f}")
        return 1

    # replace .gitignore with the manifest's trimmed version
    gi_content = "\n".join(man["gitignore"]).strip() + "\n"
    gi_sha = _git("hash-object", "-w", "--stdin", input_text=gi_content).strip()
    entries = [(m, gi_sha if p == ".gitignore" else s, p) for m, s, p in entries]

    # 4. build the tree in a temporary index
    tmp_index = os.path.join(REPO, ".git", "handover-index")
    env = {"GIT_INDEX_FILE": tmp_index}
    try:
        _git("read-tree", "--empty", env=env)
        info = "".join(f"{m} {s} 0\t{p}\n" for m, s, p in entries)
        _git("update-index", "--index-info", input_text=info, env=env)
        tree = _git("write-tree", env=env).strip()
    finally:
        if os.path.exists(tmp_index):
            os.remove(tmp_index)

    # no-op guard: identical tree -> nothing to do
    try:
        old_tree = _git("rev-parse", f"{BRANCH}^{{tree}}").strip()
    except SystemExit:
        old_tree = ""
    if tree == old_tree:
        print(f"handover is already up to date (tree {tree[:7]}) — no-op.")
        return 0

    # 5. single orphan commit, branch moved
    commit = _git("commit-tree", tree, "-m", COMMIT_MSG).strip()
    _git("update-ref", f"refs/heads/{BRANCH}", commit)
    print(f"handover rebuilt: commit {commit[:7]} (tree {tree[:7]})")
    if args.push:
        out = _git("push", "--force-with-lease", "origin", f"{BRANCH}:{BRANCH}")
        print(out or f"pushed {BRANCH}")
    else:
        print(f"review with:  git diff {BRANCH}@{{1}} {BRANCH}\n"
              f"push with:    git push --force-with-lease origin {BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
