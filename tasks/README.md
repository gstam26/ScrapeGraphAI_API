# Evaluation tasks

Each directory here is one **worked example of the per-task builder pattern**: a
small, self-contained benchmark that the evaluation suite can run the whole
pipeline against and score.

| Task | What it exercises |
|---|---|
| `task1_digital_foundations` | Depth-0 baseline — hand-picked about pages, no crawl noise |
| `task2_medtech_companies` | Multi-entity crawl over company sites |
| `task3_standards_bodies` | Standards/organisation lookups |

> These three tasks are **synthetic examples**, fabricated to demonstrate the
> pattern and to give the eval suite something to run. They are not client
> deliverables and their ground truth is illustrative — treat the numbers they
> produce as a smoke signal, not as a validated benchmark.

## The pattern

A task directory holds exactly two committed scripts:

```
tasks/<task_name>/
    build_input.py    -> writes input.xlsx        (what the pipeline reads)
    build_gt.py       -> writes ground_truth.xlsx (what the eval scores against)
```

The two `.xlsx` files are **generated, not committed** — `*.xlsx` is gitignored
repo-wide. The builders are the source of truth: the data lives in Python
literals at the top of each script, so a task is reviewable in a diff and
reproducible on any machine.

`build_input.py` writes the standard four-sheet input workbook — `entities`,
`urls`, `questions`, `config` (see the README at the repo root for the column
contract). `build_gt.py` writes a two-sheet workbook: `GroundTruth`
(`entity | question | value | is_list | verbatim_quote | source_url | notes`)
and `Metadata` (`key | value`), the format `src/eval/generic_eval.py` reads.

## Running the suite

`src/eval/run_eval_suite.py` **discovers** tasks — nothing is hardcoded. Its
`discover_tasks()` walks `tasks/`, and includes a directory only when **both**
`input.xlsx` and `ground_truth.xlsx` exist inside it:

```python
inp = os.path.join(tdir, "input.xlsx")
gt  = os.path.join(tdir, "ground_truth.xlsx")
if os.path.isfile(inp) and os.path.isfile(gt):
    found[name] = (name, inp, gt)
```

A task dir with only the builders in it is silently skipped, and if no task has
both files the suite exits with
`No runnable tasks (need tasks/<name>/input.xlsx + ground_truth.xlsx)`. So on a
fresh clone you must generate the workbooks first:

```bash
# from the repo root — run both builders for every task you want scored
python tasks/task1_digital_foundations/build_input.py
python tasks/task1_digital_foundations/build_gt.py
python tasks/task2_medtech_companies/build_input.py
python tasks/task2_medtech_companies/build_gt.py
python tasks/task3_standards_bodies/build_input.py
python tasks/task3_standards_bodies/build_gt.py

python src/eval/run_eval_suite.py
```

Useful flags:

```bash
python src/eval/run_eval_suite.py --tasks task1_digital_foundations,task3_standards_bodies
python src/eval/run_eval_suite.py --backend local --outdir outputs/eval_local
python src/eval/run_eval_suite.py --verbose
```

`--tasks` filters *and orders* by directory name, and a name that does not match
a discovered task is a hard error (so a typo never silently drops a task).
Results land in `outputs/eval_suite_<timestamp>/`: a `<task>_output.xlsx` and
`<task>_eval.xlsx` per task, plus one `suite_summary.xlsx`. Each task is
fail-soft — a network or Azure failure on one is recorded as `ERROR` and the
rest still run — and the suite exits non-zero if any task errored.

## Adding a new task

1. Create `tasks/<your_task_name>/`. The directory name is the task name used
   in reports and in `--tasks`, so make it descriptive and keep it
   filesystem-safe.
2. Copy `build_input.py` and `build_gt.py` from the closest existing task and
   replace the data literals. Keep both writing to
   `os.path.join(os.path.dirname(__file__), "input.xlsx")` and
   `".../ground_truth.xlsx"` — the discovery contract is those exact filenames
   inside the task directory.
3. Keep the module docstring convention: state the difficulty levers (crawl
   depth, number of entities, number and type of questions) and, in
   `build_gt.py`, how each ground-truth value was verified. That docstring is
   the only record of why the GT says what it says.
4. Run both builders, then `python src/eval/run_eval_suite.py --tasks <your_task_name>`
   to check it is discovered and scores end-to-end.

No registration step is needed anywhere — creating the directory and generating
the two workbooks is the whole contract.
