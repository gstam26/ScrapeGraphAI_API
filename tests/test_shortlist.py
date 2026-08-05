"""Shortlist layer tests — parser fixtures are the REAL census values from
the 2026-08-03 run (brain/proposals/shortlist-layer.md §1); gate semantics
pin George's approved decisions (2026-08-05)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.shortlist import (Cell, CellItem, Criterion, Spec, _DEFAULT_FX,
                           _DEFAULT_GUARDS, eval_gate, parse_binary,
                           parse_count, parse_money, run_shortlist)

GUARDS = _DEFAULT_GUARDS
FX = _DEFAULT_FX
REF = 2026


def _spec(criteria):
    return Spec(criteria=criteria, fx=FX, guards=GUARDS)


def _cell(*raws, state="data", verified=True, flags=()):
    return Cell(items=[CellItem(raw=r, verified=verified) for r in raws],
                state=state, flags=set(flags))


# ---------------------------------------------------------------------------
# parse_count — census fixtures
# ---------------------------------------------------------------------------
def test_count_bare_and_comma():
    assert parse_count("3300", GUARDS, REF, None).value == 3300
    assert parse_count("1,200", GUARDS, REF, None).value == 1200


def test_count_qualified_lower_bound():
    p = parse_count("more than 6,000", GUARDS, REF, None)
    assert p.ok and p.lo == 6000 and p.hi is None and "QUALIFIED" in p.flags
    p = parse_count("6,500+", GUARDS, REF, None)
    assert p.ok and p.lo == 6500 and p.hi is None
    p = parse_count("over 20,000", GUARDS, REF, None)
    assert p.ok and p.lo == 20000 and p.hi is None


def test_count_approx_is_point():
    p = parse_count("around 1,530", GUARDS, REF, None)
    assert p.ok and p.lo == p.hi == 1530 and "QUALIFIED" in p.flags


def test_count_year_and_staleness():
    p = parse_count("9,000+ (2025)", GUARDS, REF, 10)
    assert p.ok and p.vintage == 2025 and "STALE" not in p.flags
    p = parse_count("15 (2005)", GUARDS, REF, 10)
    assert p.ok and p.value == 15 and "STALE" in p.flags


def test_count_never_guesses():
    # census: subset counts and non-answers must PARSE_FAIL, not parse
    assert not parse_count("more than 100 skilled engineers", GUARDS, REF, None).ok
    assert not parse_count("a staff of design experts", GUARDS, REF, None).ok
    assert not parse_count("200 (planned within next 2 years)", GUARDS, REF, None).ok


# ---------------------------------------------------------------------------
# parse_money — census fixtures
# ---------------------------------------------------------------------------
def test_money_currencies_and_scales():
    assert abs(parse_money("$29.8B (Fiscal Year 2025)", GUARDS, FX, REF, None).value
               - 29.8e9) < 1
    assert abs(parse_money("£45M (2026)", GUARDS, FX, REF, None).value
               - 45e6 * 1.27) < 1
    assert abs(parse_money("CHF 883 million (2025)", GUARDS, FX, REF, None).value
               - 883e6 * 1.10) < 1
    assert abs(parse_money("1,426.50 MSEK", GUARDS, FX, REF, None).value
               - 1426.5e6 * 0.095) < 1


def test_money_no_currency_flagged_usd():
    p = parse_money("2+ billion", GUARDS, FX, REF, None)
    assert p.ok and "NO_CURRENCY" in p.flags and p.lo == 2e9 and p.hi is None


def test_money_range():
    p = parse_money("$100 million to $500 million", GUARDS, FX, REF, None)
    assert p.ok and p.lo == 100e6 and p.hi == 500e6 and "RANGE" in p.flags


def test_money_forecast_and_partial_flags():
    assert "FORECAST" in parse_money("USD 20.5 billion (2026 outlook)",
                                     GUARDS, FX, REF, None).flags
    assert "PARTIAL" in parse_money("CHF 180.5 million (first half of 2026)",
                                    GUARDS, FX, REF, None).flags


def test_money_parent_attribution_detected():
    p = parse_money("$10.5 billion (Mitsui Chemicals, Inc.)", GUARDS, FX, REF, None)
    assert p.ok and "PARENT_ATTRIBUTED" in p.flags


def test_money_unparseable():
    assert not parse_money("substantial", GUARDS, FX, REF, None).ok


# ---------------------------------------------------------------------------
# Gate semantics — George's approved decisions
# ---------------------------------------------------------------------------
IND = Criterion("independence", "q", "hard_gate", "binary", "require_yes",
                None, None, 0, "flag", "verified_only", None)
EMP = Criterion("emp_giant", "q", "hard_gate", "count", "max",
                None, 10000, 0, "flag", "use_flagged", 10)
REV = Criterion("rev_giant", "q", "hard_gate", "money", "max",
                None, 1e9, 0, "flag", "use_flagged", 10)


def test_independence_excludes_explicit_no_only():
    spec = _spec([IND])
    assert eval_gate(IND, _cell("No"), spec, REF).outcome == "FAIL"
    assert eval_gate(IND, _cell("Yes"), spec, REF).outcome == "PASS"
    # ND / no-data / unparseable all flag through, visibly distinct
    v = eval_gate(IND, _cell(state="ND"), spec, REF)
    assert v.outcome == "PASS" and "not disclosed" in v.label
    v = eval_gate(IND, None, spec, REF)
    assert v.outcome == "PASS" and "no data" in v.label
    v = eval_gate(IND, _cell("Yes, since 1998"), spec, REF)
    assert v.outcome == "PASS" and "unparseable" in v.label


def test_independence_unverified_no_cannot_exclude():
    # Q3: exclude on explicit VERIFIED value only
    spec = _spec([IND])
    v = eval_gate(IND, _cell("No", verified=False), spec, REF)
    assert v.outcome == "PASS" and "unverified only" in v.label


def test_giant_gate_bounds():
    spec = _spec([EMP])
    assert eval_gate(EMP, _cell("over 20,000"), spec, REF).outcome == "FAIL"   # lo>T
    assert eval_gate(EMP, _cell("150,000"), spec, REF).outcome == "FAIL"
    assert eval_gate(EMP, _cell("220"), spec, REF).outcome == "PASS"
    v = eval_gate(EMP, _cell("over 1,500"), spec, REF)                          # bound open
    assert v.outcome == "PASS" and "BOUND_OPEN" in v.flags


def test_parent_attributed_revenue_never_trips_giant_gate():
    # Q1: giant gate measures the entity's OWN size only
    spec = _spec([REV])
    v = eval_gate(REV, _cell("$10.5 billion (Mitsui Chemicals, Inc.)"), spec, REF)
    assert v.outcome == "PASS" and "parent-attributed" in v.label


def test_missing_policy_exclude_variant():
    emp_x = Criterion("emp_giant", "q", "hard_gate", "count", "max",
                      None, 10000, 0, "exclude", "use_flagged", 10)
    spec = _spec([emp_x])
    v = eval_gate(emp_x, _cell(state="ND"), spec, REF)
    assert v.outcome == "FAIL" and "not disclosed" in v.label


def test_three_unknown_states_distinct():
    spec = _spec([EMP])
    labels = {
        eval_gate(EMP, _cell(state="ND"), spec, REF).label,
        eval_gate(EMP, None, spec, REF).label,
        eval_gate(EMP, _cell("a staff of design experts"), spec, REF).label,
    }
    assert len(labels) == 3   # NOT DISCLOSED / NO DATA / PARSE_FAIL never collapse


# ---------------------------------------------------------------------------
# Engine: ranking, determinism
# ---------------------------------------------------------------------------
def _mini_cells():
    q_ind, q_med = "independent?", "medical?"
    return {
        ("alpha", q_ind): _cell("Yes"), ("alpha", q_med): _cell("Yes"),
        ("beta", q_ind): _cell("No"), ("beta", q_med): _cell("Yes"),
        ("gamma", q_ind): _cell("Yes"), ("gamma", q_med): _cell("No"),
        ("delta", q_ind): _cell(state="ND"), ("delta", q_med): _cell("Yes"),
    }


def _mini_spec():
    return _spec([
        Criterion("independence", "independent?", "hard_gate", "binary",
                  "require_yes", None, None, 0, "flag", "use_flagged", None),
        Criterion("medical", "medical?", "scored", "binary", "prefer_yes",
                  None, None, 2, "flag", "use_flagged", None),
    ])


def test_engine_gates_rank_and_flag_through():
    res = run_shortlist(_mini_cells(), _mini_spec(), k=2, ref_year=REF)
    by = {r.entity: r for r in res.entities}
    assert by["beta"].excluded_by == "independence"        # explicit No -> out
    assert by["delta"].excluded_by == ""                   # ND flags through
    ranked = [r.entity for r in res.entities if r.rank is not None]
    assert ranked[0] in ("alpha", "delta") and by["gamma"].rank is not None
    assert by["alpha"].total == 1.0 and by["gamma"].total == 0.0


def test_engine_deterministic():
    a = run_shortlist(_mini_cells(), _mini_spec(), k=2, ref_year=REF)
    b = run_shortlist(_mini_cells(), _mini_spec(), k=2, ref_year=REF)
    assert [(r.entity, r.rank, r.total, r.excluded_by,
             [(v.criterion_id, v.label) for v in r.gates]) for r in a.entities] \
        == [(r.entity, r.rank, r.total, r.excluded_by,
             [(v.criterion_id, v.label) for v in r.gates]) for r in b.entities]
