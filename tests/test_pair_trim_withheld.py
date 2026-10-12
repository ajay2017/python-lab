"""Audit A-H2 -- a danger-tier pair on a thin/unknown correlation sample must
CONVERT from a PAIR_RISK "trim the weaker" SELL call to a PAIR_RISK_WITHHELD
info rec; G-26's add-pause (keyed on risk_pairs) is untouched.

Owner decisions: D2 reuse the floor (CORR_MIN_OBS_TRUSTED), D4 withhold on
SAMPLE SIZE ONLY -- never on a missing holding (corr_unchecked).
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import home_risk_synthesis as hrs
from stock_analyzer import portfolio
from stock_analyzer.constants import CORR_MIN_OBS_TRUSTED, SECTOR_REDUCE_TRIGGER
from stock_analyzer.pair_add_gate import add_block_map, describe_reason
from stock_analyzer.util import pair_trim_withheld_note
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.xdist_group(name="pair_trim_withheld_ast")

_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_PY = _ROOT / "app.py"


# ── fixtures ────────────────────────────────────────────────────────────────

def _t11_port_df():
    names = ["AAA", "BBB", "EEE", "FFF", "GGG", "HHH"]
    return pd.DataFrame({
        "Ticker": names,
        "Sector": ["Technology"] * 6,
        "Score": [70.0, 60.0, 65.0, float("nan"), 50.0, 55.0],
        "Score Available": [True, True, True, False, True, True],
        "Signal": ["Hold"] * 6,
        "P&L (%)": [5.0] * 6,
        "Weight (%)": [100.0 / 6] * 6,
        "Market Value": [10_000.0] * 6,
    })


def _t11_div():
    return {"risk_pairs": [
        {"t1": "AAA", "t2": "BBB", "corr": 0.90, "level": "danger"},   # both scored
        {"t1": "EEE", "t2": "FFF", "corr": 0.88, "level": "danger"},   # FFF unscored
        {"t1": "GGG", "t2": "HHH", "corr": 0.70, "level": "warning"},  # warning
    ]}


def _run(cov):
    return portfolio.diversification_recommendations(
        _t11_port_df(), pd.DataFrame(), _t11_div(),
        sector_candidates={"Healthcare": ["JNJ"]}, discovery_universe={},
        corr_coverage=cov,
    )


def _types(recs, t):
    return [r for r in recs if r["type"] == t]


def _pairs_of(recs, t):
    return {(r["t1"], r["t2"]) for r in recs if r["type"] == t}


# ── T11 ─────────────────────────────────────────────────────────────────────

def test_t11_pair_risk_converts_to_withheld_on_thin_or_unknown():
    trusted = _run({"n_obs": CORR_MIN_OBS_TRUSTED})
    thin = _run({"n_obs": 2})
    unknown = _run(None)

    # trusted: the existing PAIR_RISK, for the both-scored pair ONLY
    assert _pairs_of(trusted, "PAIR_RISK") == {("AAA", "BBB")}
    assert _types(trusted, "PAIR_RISK_WITHHELD") == []

    # thin: WITHHELD with n_obs / min_obs, and ZERO PAIR_RISK
    assert _types(thin, "PAIR_RISK") == []
    w = _types(thin, "PAIR_RISK_WITHHELD")
    assert len(w) == 1
    assert w[0] == {
        "type": "PAIR_RISK_WITHHELD", "urgency": "info",
        "t1": "AAA", "t2": "BBB", "corr": 0.90,
        "withheld_reason": "thin_sample", "n_obs": 2,
        "min_obs": CORR_MIN_OBS_TRUSTED,
    }

    # unknown: WITHHELD "sample_unknown", n_obs None
    assert _types(unknown, "PAIR_RISK") == []
    u = _types(unknown, "PAIR_RISK_WITHHELD")
    assert len(u) == 1 and u[0]["withheld_reason"] == "sample_unknown"
    assert u[0]["n_obs"] is None and u[0]["min_obs"] == CORR_MIN_OBS_TRUSTED

    # the one-unscored pair and the warning pair are absent in EVERY run
    # (kills putting the thin check before the Score-Available skip)
    for recs in (trusted, thin, unknown):
        for typ in ("PAIR_RISK", "PAIR_RISK_WITHHELD"):
            assert ("EEE", "FFF") not in _pairs_of(recs, typ)
            assert ("GGG", "HHH") not in _pairs_of(recs, typ)

    # REDUCE / ADD identical across runs AND non-empty (kills early-return leakage)
    def core(recs):
        return [r for r in recs if r["type"] in ("REDUCE", "ADD")]
    assert _types(trusted, "REDUCE") and _types(trusted, "ADD")
    assert core(trusted) == core(thin) == core(unknown)


def test_t11_floor_boundary_is_inclusive():
    just_below = _run({"n_obs": CORR_MIN_OBS_TRUSTED - 1})
    at_floor = _run({"n_obs": CORR_MIN_OBS_TRUSTED})
    assert _pairs_of(just_below, "PAIR_RISK_WITHHELD") == {("AAA", "BBB")}
    assert _pairs_of(at_floor, "PAIR_RISK") == {("AAA", "BBB")}


# ── T12 ─────────────────────────────────────────────────────────────────────

def test_t12_corr_coverage_is_keyword_only_with_no_default():
    p = inspect.signature(portfolio.diversification_recommendations).parameters["corr_coverage"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is inspect.Parameter.empty
    # contrast: the sibling kwargs keep their shape
    assert inspect.signature(portfolio.diversification_recommendations).parameters[
        "portfolio_value"].default == 50_000.0
    with pytest.raises(TypeError):
        portfolio.diversification_recommendations(
            _t11_port_df(), pd.DataFrame(), _t11_div(),
            sector_candidates={}, discovery_universe={},
        )


# ── T13 / T16: the real chain ───────────────────────────────────────────────

def _walk(n, seed, drift=0.0):
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1 + rng.normal(drift, 0.02, n))


def _held(n):
    a = _walk(n, 1)
    held = {
        "AAA": {"df": pd.DataFrame({"Close": a})},
        "BBB": {"df": pd.DataFrame({"Close": a * 1.5})},        # lockstep with AAA
        "CCC": {"df": pd.DataFrame({"Close": _walk(n, 99)})},   # unrelated
    }
    return held


def _bundle(n, held=None, tickers=("AAA", "BBB", "CCC")):
    k = len(tickers)
    port = pd.DataFrame({
        "Ticker": list(tickers), "Sector": ["Technology"] * k,
        "Market Value": [10_000.0] * k, "Weight (%)": [100.0 / k] * k,
        "Score": [70.0, 60.0, 65.0][:k], "Signal": ["BUY"] * k, "P&L (%)": [5.0] * k,
    })
    return hrs.build_correlation_bundle(
        port, held if held is not None else _held(n), 50_000.0,
        sector_candidates={}, discovery_universe={},
        held_tickers=list(tickers),
    )


def test_t13_real_chain_thin_withholds_and_long_sample_trims():
    thin = _bundle(12)
    long_ = _bundle(61)
    assert thin["corr_coverage"]["n_obs"] < CORR_MIN_OBS_TRUSTED
    assert long_["corr_coverage"]["n_obs"] >= CORR_MIN_OBS_TRUSTED

    # both fixtures genuinely hold the danger pair (the fixture is not vacuous)
    for b in (thin, long_):
        assert any(rp["level"] == "danger" and {rp["t1"], rp["t2"]} == {"AAA", "BBB"}
                   for rp in b["risk_pairs"])

    assert _types(thin["div_recs"], "PAIR_RISK") == []
    assert _pairs_of(thin["div_recs"], "PAIR_RISK_WITHHELD") == {("AAA", "BBB")}
    assert _pairs_of(long_["div_recs"], "PAIR_RISK") == {("AAA", "BBB")}
    assert _types(long_["div_recs"], "PAIR_RISK_WITHHELD") == []


def test_t16_g26_map_stays_non_empty_and_names_the_withheld_state():
    thin = _bundle(12)
    long_ = _bundle(61)
    held = ["AAA", "BBB", "CCC"]
    m_thin = add_block_map(thin["corr_df"], thin["risk_pairs"], thin["div_recs"], held,
                           corr_coverage=thin["corr_coverage"])
    m_long = add_block_map(long_["corr_df"], long_["risk_pairs"], long_["div_recs"], held,
                           corr_coverage=long_["corr_coverage"])
    # F-294 regression guard: a FIRING map is never downgraded by a thin sample
    assert m_thin and set(m_thin) == {"AAA", "BBB"}
    assert m_long and set(m_long) == {"AAA", "BBB"}
    assert {p["trim_call"] for e in m_thin.values() for p in e["partners"]} == {"withheld"}
    assert {p["trim_call"] for e in m_long.values() for p in e["partners"]} == {"named"}
    assert all(p["weaker"] is None for e in m_thin.values() for p in e["partners"])


def test_d4_withholds_on_sample_size_only_not_on_a_missing_holding():
    """A trusted sample with an UNRELATED holding missing from the matrix
    (corr_unchecked non-empty) must still produce the PAIR_RISK trim --
    correlation_claim_verified would have withheld it."""
    held = _held(61)
    # DDD is held but has no price history -> missing from the matrix
    port4 = pd.DataFrame({
        "Ticker": ["AAA", "BBB", "CCC", "DDD"], "Sector": ["Technology"] * 4,
        "Market Value": [10_000.0] * 4, "Weight (%)": [25.0] * 4,
        "Score": [70.0, 60.0, 65.0, 50.0], "Signal": ["BUY"] * 4, "P&L (%)": [5.0] * 4,
    })
    b = hrs.build_correlation_bundle(
        port4, held, 50_000.0, sector_candidates={}, discovery_universe={},
        held_tickers=["AAA", "BBB", "CCC", "DDD"],
    )
    assert b["corr_unchecked"] == ["DDD"]                      # a holding IS missing
    assert b["corr_coverage"]["n_obs"] >= CORR_MIN_OBS_TRUSTED  # sample is sound
    assert not portfolio.correlation_claim_verified(b["corr_unchecked"], b["corr_coverage"])
    assert _pairs_of(b["div_recs"], "PAIR_RISK") == {("AAA", "BBB")}
    assert _types(b["div_recs"], "PAIR_RISK_WITHHELD") == []


# ── T14 ─────────────────────────────────────────────────────────────────────

def _gate_inputs():
    corr = pd.DataFrame(0.0, index=["AAA", "BBB"], columns=["AAA", "BBB"])
    corr.loc["AAA", "AAA"] = corr.loc["BBB", "BBB"] = 1.0
    corr.loc["AAA", "BBB"] = corr.loc["BBB", "AAA"] = 0.9
    return corr, [{"t1": "AAA", "t2": "BBB", "corr": 0.9, "level": "danger"}]


@pytest.mark.parametrize("div_recs,expected,weaker", [
    ([{"type": "PAIR_RISK", "t1": "AAA", "t2": "BBB", "weaker": "BBB"}], "named", "BBB"),
    ([{"type": "PAIR_RISK_WITHHELD", "t1": "BBB", "t2": "AAA"}], "withheld", None),
    ([], "not_named", None),
    (None, "unknown", None),
])
def test_t14_trim_call_four_states(div_recs, expected, weaker):
    corr, pairs = _gate_inputs()
    m = add_block_map(corr, pairs, div_recs, ["AAA", "BBB"])
    assert m, "the pair itself must fire in every state"
    for t in ("AAA", "BBB"):
        (partner,) = m[t]["partners"]
        assert partner["trim_call"] == expected
        assert partner["weaker"] == weaker


# ── T15 ─────────────────────────────────────────────────────────────────────

def test_t15_describe_reason_withheld_vs_not_named():
    wh = describe_reason({"partners": [{"partner": "BBB", "trim_call": "withheld"}]})
    nn = describe_reason({"partners": [{"partner": "BBB", "trim_call": "not_named"}]})
    assert "withheld" in wh and "a fund, or a data outage" not in wh
    assert "a fund, or a data outage" in nn and "withheld" not in nn
    three = describe_reason({"partners": [
        {"partner": p, "trim_call": "withheld"} for p in ("BBB", "CCC", "DDD")]})
    assert three.count("withheld") == 3
    assert len(three) < 300, len(three)


# ── T18 ─────────────────────────────────────────────────────────────────────

def _wh(**kw):
    rec = {"type": "PAIR_RISK_WITHHELD", "urgency": "info", "t1": "AAA", "t2": "BBB",
           "corr": 0.9, "withheld_reason": "thin_sample", "n_obs": 7, "min_obs": 20}
    rec.update(kw)
    return rec


def test_t18_note_thin_names_tickers_and_counts():
    note = pair_trim_withheld_note(_wh())
    assert "AAA" in note and "BBB" in note and "7" in note and "20" in note
    assert "only 7 shared daily returns, needs 20" in note
    assert "No trim is recommended" in note and "paused" in note
    assert "$" not in note


def test_t18_note_unknown_says_unconfirmed():
    note = pair_trim_withheld_note(_wh(withheld_reason="sample_unknown", n_obs=None))
    assert "couldn't be confirmed" in note and "only" not in note
    assert "$" not in note


@pytest.mark.parametrize("bad", [
    {"type": "PAIR_RISK", "t1": "AAA", "t2": "BBB", "weaker": "AAA"},
    "PAIR_RISK_WITHHELD", None, 7, [],
    {"type": "PAIR_RISK_WITHHELD", "t1": "AAA"},
    {"type": "PAIR_RISK_WITHHELD", "t1": 1, "t2": 2},
    {"type": "PAIR_RISK_WITHHELD", "t1": "", "t2": "BBB"},
])
def test_t18_note_none_for_anything_else(bad):
    assert pair_trim_withheld_note(bad) is None
    assert pair_trim_withheld_note(_wh()) is not None  # contrast


def test_t18_note_thin_with_bool_or_missing_counts_degrades_not_crashes():
    note = pair_trim_withheld_note(_wh(n_obs=True))
    assert note is not None and "couldn't be confirmed" in note


# ── T17: AST pins on app.py ─────────────────────────────────────────────────

_TREE: dict = {}


def _app():
    if "t" not in _TREE:
        _TREE["t"] = parse_file_cached(APP_PY)
    return _TREE["t"]


def _consts(node):
    return [c for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]


def _is_type_eq(test, literal):
    """test is `<x>["type"] == literal` or `<x>.get("type") == literal`."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == literal):
        return False
    left = test.left
    if isinstance(left, ast.Subscript):
        return isinstance(left.slice, ast.Constant) and left.slice.value == "type"
    if isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and left.func.attr == "get":
        return bool(left.args) and isinstance(left.args[0], ast.Constant) and left.args[0].value == "type"
    return False


def _listcomp_assign(tree, name):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.ListComp)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)):
            out.append(n)
    return out


def test_t17_reduce_recs_tuple_is_exactly_reduce_and_pair_risk():
    (assign,) = _listcomp_assign(_app(), "reduce_recs")
    (gen,) = assign.value.generators
    (cond,) = gen.ifs
    assert isinstance(cond, ast.Compare) and isinstance(cond.ops[0], ast.In)
    assert ast.literal_eval(cond.comparators[0]) == ("REDUCE", "PAIR_RISK")


def _consider_trimming_fstrings(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr) and any("Consider trimming" in c.value for c in _consts(n)):
            if any(isinstance(v, ast.FormattedValue) and "weaker" in ast.dump(v) for v in n.values):
                out.append(n)
    return out


def _enclosed_by_type_if(tree, targets, literal):
    ok = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and _is_type_eq(n.test, literal):
            for sub in n.body:
                for m in ast.walk(sub):
                    ok.add(id(m))
    return all(id(t) in ok for t in targets)


def test_t17_consider_trimming_only_inside_a_pair_risk_branch():
    targets = _consider_trimming_fstrings(_app())
    assert len(targets) == 1, "the PAIR_RISK trim sentence must exist exactly once"
    assert _enclosed_by_type_if(_app(), targets, "PAIR_RISK")


def test_t17_detector_rejects_an_unguarded_trim_sentence():
    good = ast.parse("if rec['type'] == 'PAIR_RISK':\n    f(f\"Consider trimming {rec['weaker']}\")\n")
    bad = ast.parse("if rec['type'] == 'REDUCE':\n    f(f\"Consider trimming {rec['weaker']}\")\n")
    (g,) = _consider_trimming_fstrings(good)
    (b,) = _consider_trimming_fstrings(bad)
    assert _enclosed_by_type_if(good, [g], "PAIR_RISK")
    assert not _enclosed_by_type_if(bad, [b], "PAIR_RISK")


def _calls_name(node, name):
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == name
               for c in ast.walk(node))


def _withheld_for_loops(tree):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "withheld_recs"
                and _calls_name(n, "pair_trim_withheld_note")):
            out.append(n)
    return out


def test_t17_withheld_render_filters_and_calls_the_note_helper():
    (assign,) = _listcomp_assign(_app(), "withheld_recs")
    (gen,) = assign.value.generators
    (cond,) = gen.ifs
    assert _is_type_eq(cond, "PAIR_RISK_WITHHELD")
    assert len(_withheld_for_loops(_app())) == 1


def test_t17_well_balanced_success_unreachable_when_only_withheld_recs_exist():
    """The green 'well balanced' success sits under `elif not div_recs`; a
    div_recs holding only PAIR_RISK_WITHHELD recs is non-empty, so it reaches
    the ELSE chain, where the withheld render lives."""
    hits = []
    for n in ast.walk(_app()):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
                and any("well balanced" in c.value for sub in n.body for c in _consts(sub))):
            hits.append(n)
    # innermost If(s) directly guarding the success text (outer page-level
    # Ifs have a Compare test and are excluded by the UnaryOp filter)
    assert len(hits) == 1
    t = hits[0].test
    assert (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
            and isinstance(t.operand, ast.Name) and t.operand.id == "div_recs"), ast.dump(t)
    in_else = [loop for sub in hits[0].orelse for loop in _withheld_for_loops(sub)]
    assert len(in_else) == 1, "withheld render must live in the non-success branch"
    # and the success body itself never renders withheld recs
    assert not any(_calls_name(sub, "pair_trim_withheld_note") for sub in hits[0].body)


def test_t17_brief_nav_button_still_requires_a_named_trim_only():
    (assign,) = [n for n in ast.walk(_app())
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "_pb_has_named" for t in n.targets)]
    cmp_consts = {c.value for cmp_ in ast.walk(assign.value) if isinstance(cmp_, ast.Compare)
                  for comp in cmp_.comparators
                  for c in ast.walk(comp) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert cmp_consts == {"named"}


def test_t17_synth_schema_version_bumped_for_the_new_div_recs_shape():
    vals = [n.value.value for n in ast.walk(_app())
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            and any(isinstance(t, ast.Name) and t.id == "_SYNTH_SCHEMA_VER" for t in n.targets)]
    assert vals and all(v >= 15 for v in vals), vals


# -- C2 review NB-3: the card that delivers "convert, never drop" -----------
def _withheld_card_if(tree):
    """The If whose body holds the single withheld-render loop."""
    (loop,) = _withheld_for_loops(tree)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.If) and any(b is loop for b in n.body)]
    assert len(hits) == 1, len(hits)
    return hits[0], loop


def test_withheld_card_guard_is_the_bare_list_and_skips_only_a_none_note():
    """Kills M14 (`if withheld_recs and False:` -- the card goes dead) and
    M15 (the `_wh_note is None` skip inverted -- every real note skipped)."""
    card_if, loop = _withheld_card_if(_app())
    assert isinstance(card_if.test, ast.Name) and card_if.test.id == "withheld_recs"
    skips = [n for n in loop.body if isinstance(n, ast.If)]
    assert len(skips) == 1
    t = skips[0].test
    assert (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "_wh_note"
            and len(t.ops) == 1 and isinstance(t.ops[0], ast.Is)
            and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None)
    assert len(skips[0].body) == 1 and isinstance(skips[0].body[0], ast.Continue)


def test_withheld_card_detector_rejects_the_dead_and_inverted_forms():
    good = ("if withheld_recs:\n    for rec in withheld_recs:\n"
            "        _wh_note = pair_trim_withheld_note(rec)\n"
            "        if _wh_note is None:\n            continue\n")
    card_if, _ = _withheld_card_if(ast.parse(good))
    assert isinstance(card_if.test, ast.Name)
    dead = good.replace("if withheld_recs:", "if withheld_recs and False:")
    assert not isinstance(_withheld_card_if(ast.parse(dead))[0].test, ast.Name)
    inverted = good.replace("is None", "is not None")
    (_, loop) = _withheld_card_if(ast.parse(inverted))
    (skip,) = [n for n in loop.body if isinstance(n, ast.If)]
    assert not isinstance(skip.test.ops[0], ast.Is)


# -- C2 review NB-1 / NB-2: the wording claims only what is true ------------
def test_note_claims_only_the_add_to_winner_pause():
    """G-26 pauses add-to-winner suggestions only (Rebalancer drift-ADD and
    Analysis add-sizing are still uncovered, D5), so the note must say exactly
    that -- the same phrase as the PAIR_RISK card's caption."""
    rec = {"type": "PAIR_RISK_WITHHELD", "t1": "AAA", "t2": "BBB", "corr": 0.9,
           "withheld_reason": "thin_sample", "n_obs": 7, "min_obs": 20}
    note = pair_trim_withheld_note(rec)
    assert "add-to-winner suggestions on both names paused" in note
    assert "adds to both names" not in note


def test_withheld_reason_clause_is_true_for_thin_and_unknown_alike():
    """The clause is persisted into gate-ledger rows and does not know which
    state produced it, so it must not assert "too few" (false for unknown)."""
    entry = {"partners": [{"partner": "BBB", "corr": 0.9, "trim_call": "withheld", "weaker": None}],
             "max_corr": 0.9}
    text = describe_reason(entry)
    assert "withheld" in text and "too thin or unconfirmed" in text
    assert "too few shared trading days" not in text
