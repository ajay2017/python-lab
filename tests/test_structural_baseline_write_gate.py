"""C3 / audit A-H1 -- a Structural Scan may only be SAVED (becoming G-25's
baseline and lifting live add-pauses) on a fully-measured correlation matrix."""
from __future__ import annotations

import ast
import itertools
import pathlib

import numpy as np
import pandas as pd
import pytest
from tests._ast_cache import parse_file_cached

from stock_analyzer import portfolio, portfolio_intelligence, structural_scanner
from stock_analyzer.cluster_add_gate import baseline_write_allowed
from stock_analyzer.util import correlation_record_withheld_note

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UNCHECKED = [None, [], ["X"]]
_COVERAGE = [None, {}, {"n_obs": 5}, {"n_obs": 125}, {"n_obs": True}]


# -- T6 -----------------------------------------------------------------------
def test_baseline_write_allowed_contrast_and_sweep():
    assert baseline_write_allowed([], {"n_obs": 125}) is True
    assert baseline_write_allowed([], {"n_obs": 5}) is False
    for u, c in itertools.product(_UNCHECKED, _COVERAGE):
        expected = (u == [] and c == {"n_obs": 125})
        assert baseline_write_allowed(u, c) is expected, (u, c)


def test_baseline_write_allowed_never_raises():
    assert baseline_write_allowed(object(), object()) is False
    assert baseline_write_allowed([], {"n_obs": 125}) is True


# -- T9 -----------------------------------------------------------------------
def test_note_and_gate_agree_across_sweep():
    for u, c in itertools.product(_UNCHECKED, _COVERAGE):
        note = correlation_record_withheld_note(u, c, subject="This scan")
        assert (note is None) == portfolio.correlation_claim_verified(u, c), (u, c)
        assert (note is None) == baseline_write_allowed(u, c), (u, c)
        if note is not None:
            assert "$" not in note
            assert note.startswith("This scan")


def test_note_names_both_gaps_when_both_hold():
    both = correlation_record_withheld_note(["AAA", "BBB"], {"n_obs": 5}, subject="This scan")
    assert "AAA" in both and "BBB" in both
    assert f"only 5 shared daily returns (needs {portfolio.CORR_MIN_OBS_TRUSTED})" in both
    thin = correlation_record_withheld_note([], {"n_obs": 5}, subject="This scan")
    assert "only 5 shared daily returns" in thin and "missing" not in thin
    miss = correlation_record_withheld_note(["AAA"], {"n_obs": 125}, subject="This scan")
    assert "AAA" in miss and "shared daily returns" not in miss
    assert correlation_record_withheld_note([], {"n_obs": 125}, subject="This scan") is None


def test_note_unknown_states_and_truncation():
    unk = correlation_record_withheld_note(None, None, subject="This scan")
    assert "couldn't confirm every holding was included" in unk
    assert "couldn't confirm the sample size" in unk
    many = correlation_record_withheld_note(list("ABCDEFGH"), {"n_obs": 125}, subject="This scan")
    assert "A, B, C, D, E +3 more" in many
    assert "+" not in correlation_record_withheld_note(["A"], {"n_obs": 125}, subject="S")


def test_note_wording_grammar_and_remedies():
    one = correlation_record_withheld_note(["A"], {"n_obs": 125}, subject="S")
    two = correlation_record_withheld_note(["A", "B"], {"n_obs": 125}, subject="S")
    assert "1 holding is missing" in one and "holding(s)" not in one
    assert "2 holdings are missing" in two
    # Thin: names a ticker as THE cap only when provable (shortest_len == n_obs+1) ...
    capped = correlation_record_withheld_note(
        [], {"n_obs": 5, "shortest_ticker": "newco", "shortest_len": 6}, subject="S")
    assert "capped by NEWCO's short price history" in capped
    # ... never when the shortest history is LONGER than the overlap (contrast) ...
    misaligned = correlation_record_withheld_note(
        [], {"n_obs": 5, "shortest_ticker": "AAA", "shortest_len": 125}, subject="S")
    assert "capped by" not in misaligned
    assert "only overlap on those days" in misaligned
    # ... nor when the length is missing or a bool.
    for bad in ({"n_obs": 5, "shortest_ticker": "AAA"},
                {"n_obs": 5, "shortest_ticker": "AAA", "shortest_len": True}):
        assert "capped by" not in correlation_record_withheld_note([], bad, subject="S")
    plain = correlation_record_withheld_note([], {"n_obs": 5}, subject="S")
    assert "capped by" not in plain and "only 5 shared daily returns" in plain
    # Remedy for an UNCONFIRMED reading is Refresh Signals (a Home revisit just
    # re-serves the memoized failure); a measured-thin sample gets none.
    _fix = "Open 🏠 Home and click 🔄 Refresh Signals to recompute it."
    assert _fix in correlation_record_withheld_note(None, {"n_obs": 125}, subject="S")
    assert _fix in correlation_record_withheld_note([], None, subject="S")
    assert "Refresh Signals" not in plain, "a measured-thin sample has a different remedy"
    assert "Revisit" not in correlation_record_withheld_note(None, None, subject="S")


def test_misaligned_histories_from_the_real_coverage_do_not_name_a_cap():
    """C3 review blocking #1, reproduced end to end: equal-length histories that
    overlap on only a few days give a thin n_obs, and the real
    correlation_coverage still reports a `shortest_ticker` -- which is NOT the
    cap. Contrast: a genuinely short history IS named."""
    long_dates = pd.bdate_range("2026-01-01", periods=125)
    rng = np.random.default_rng(3)
    a = np.cumprod(1 + rng.normal(0, 0.01, 125)) * 100
    b = np.cumprod(1 + rng.normal(0, 0.01, 125)) * 80
    shifted = pd.bdate_range(long_dates[-6], periods=125)  # overlaps on 6 days
    hd = {"AAA": _df(a, long_dates), "BBB": _df(b, shifted), "CCC": _df(a * 1.1, long_dates)}
    cov = portfolio.correlation_coverage(hd)
    assert cov["n_obs"] < portfolio.CORR_MIN_OBS_TRUSTED
    assert cov["shortest_len"] != cov["n_obs"] + 1  # precondition: no single cap
    note = correlation_record_withheld_note([], cov, subject="S")
    assert "capped by" not in note and "only overlap on those days" in note

    short = dict(hd)
    short["NEWCO"] = _df([10.0, 10.5, 10.2, 10.8], long_dates[-4:])
    cov2 = portfolio.correlation_coverage(short)
    assert cov2["shortest_ticker"] == "NEWCO" and cov2["shortest_len"] == cov2["n_obs"] + 1
    assert "capped by NEWCO's short price history" in correlation_record_withheld_note([], cov2, subject="S")
    # The subject is the lead-in; reasons follow the colon.
    assert correlation_record_withheld_note([], {"n_obs": 5}, subject="X can't run").startswith("X can't run: ")


# -- T7: the real hazard chain --------------------------------------------------
def _df(vals, dates):
    return {"df": pd.DataFrame({"Close": vals}, index=dates)}


def _bundle(thin):
    dates = pd.bdate_range("2026-01-01", periods=130)
    rng = np.random.default_rng(7)
    a = np.cumprod(1 + rng.normal(0, 0.01, 130)) * 100
    b = a * (1 + rng.normal(0, 0.004, 130))  # genuine strong co-mover
    c = np.cumprod(1 + rng.normal(0, 0.01, 130)) * 50
    hd = {"AAA": _df(a, dates), "BBB": _df(b, dates), "CCC": _df(c, dates)}
    if thin:
        # 3-bar histories -> a 2-return shared window; same direction -> corr 1.0
        hd["AAA"] = _df([100.0, 110.0, 115.5], dates[-3:])
        hd["BBB"] = _df([50.0, 60.0, 61.0], dates[-3:])
    return hd


def _chain(thin):
    hd = _bundle(thin)
    corr = portfolio.correlation_matrix(hd)
    cov = portfolio.correlation_coverage(hd)
    unchecked = portfolio.correlation_unchecked(corr, list(hd))
    clusters = portfolio_intelligence.correlation_clusters(
        corr, {"AAA": 10, "BBB": 10, "CCC": 10})
    return corr, cov, unchecked, clusters


def test_thin_day_spurious_pair_blocks_save_and_would_silence_real_cluster():
    t_corr, t_cov, t_unchecked, t_clusters = _chain(thin=True)
    assert t_cov["n_obs"] < portfolio.CORR_MIN_OBS_TRUSTED
    assert float(t_corr.loc["AAA", "BBB"]) == pytest.approx(1.0)  # spurious
    assert any({"AAA", "BBB"} <= set(c["tickers"]) for c in t_clusters)
    assert baseline_write_allowed(t_unchecked, t_cov) is False

    f_corr, f_cov, f_unchecked, f_clusters = _chain(thin=False)
    assert f_cov["n_obs"] >= portfolio.CORR_MIN_OBS_TRUSTED
    assert baseline_write_allowed(f_unchecked, f_cov) is True  # contrast
    assert 0.65 <= float(f_corr.loc["AAA", "BBB"]) < 0.99  # genuine pair
    assert f_clusters

    # Against an EMPTY prior snapshot the genuine cluster is flagged ...
    fresh = structural_scanner.detect_new_clusters_strict(f_clusters, [], f_corr)
    assert any({"AAA", "BBB"} <= set(x["tickers"]) for x in fresh)
    # ... but against the thin-day snapshot it reads as already acknowledged.
    silenced = structural_scanner.detect_new_clusters_strict(f_clusters, t_clusters, f_corr)
    assert silenced == []


# -- T8: AST pins on app.py's Structural tab ------------------------------------
# One shared parse of the ~44K-line app.py (37.7s each, measured 2026-10-12;
# per-test re-parsing pushed the pre-commit suite past its 450s timeout).
# Module cache + xdist_group, the test_check_antipatterns.py pattern. The
# tests only read the tree.
pytestmark = pytest.mark.xdist_group(name="structural_baseline_write_gate_ast")
_APP_TREE: dict = {}


def _app():
    if "tree" not in _APP_TREE:
        _APP_TREE["tree"] = parse_file_cached(_ROOT / "app.py")
    return _APP_TREE["tree"]


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_get_of(value, key):
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get" and value.args
            and isinstance(value.args[0], ast.Constant) and value.args[0].value == key)


def _assigns(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


def _one(seq):
    seq = list(seq)
    assert len(seq) == 1, len(seq)
    return seq[0]


def test_producer_record_ok_fed_by_plain_gets():
    tree = _app()
    call = _one(_assigns(tree, "_ss_record_ok")).value
    assert isinstance(call, ast.Call) and call.func.attr == "baseline_write_allowed"
    assert [a.id for a in call.args] == ["_ss_unchecked", "_ss_cov"]
    for var, key in (("_ss_unchecked", "_corr_unchecked_cache"),
                     ("_ss_cov", "_corr_coverage_cache")):
        a = _one(_assigns(tree, var))
        assert not isinstance(a.value, ast.BoolOp)
        assert _is_get_of(a.value, key)


def _ss_button_if(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.If):
            for c in ast.walk(n.test):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "button"
                        and any(k.arg == "key" and isinstance(k.value, ast.Constant)
                                and k.value.value == "_ss_gen_narrative_btn"
                                for k in c.keywords)):
                    out.append((n, c))
    return _one(out)


def _is_not_record_ok(node):
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name) and node.operand.id == "_ss_record_ok")


def _and_with_bare_record_ok(test):
    return (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)
            and any(isinstance(v, ast.Name) and v.id == "_ss_record_ok" for v in test.values))


def test_button_disabled_and_click_guard_reference_record_ok():
    """POLARITY, not just presence (C3 review NB-1): a flipped
    `disabled=... or _ss_record_ok` would silently disable the scan on every
    VERIFIED day while a name-only pin stayed green."""
    if_node, btn = _ss_button_if(_app())
    disabled = _one(k for k in btn.keywords if k.arg == "disabled").value
    assert isinstance(disabled, ast.BoolOp) and isinstance(disabled.op, ast.Or)
    assert any(_is_not_record_ok(v) for v in disabled.values), "disabled must use `not _ss_record_ok`"
    assert not any(isinstance(v, ast.Name) and v.id == "_ss_record_ok" for v in disabled.values), \
        "a bare (un-negated) _ss_record_ok in disabled= inverts the gate"
    assert _and_with_bare_record_ok(if_node.test)


def test_withheld_caption_explains_the_disabled_button():
    """NB-2: a disabled button with no explanation breaks the "suppressions
    render a visible banner" rule. Pin: an If testing exactly
    `not _ss_record_ok` renders correlation_record_withheld_note."""
    hits = [n for n in ast.walk(_app())
            if isinstance(n, ast.If) and _is_not_record_ok(n.test)
            and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id == "correlation_record_withheld_note"
                    for sub in n.body for c in ast.walk(sub))]
    assert len(hits) == 1


def test_detector_helpers_reject_the_inverted_forms():
    assert _is_not_record_ok(ast.parse("not _ss_record_ok", mode="eval").body)
    assert not _is_not_record_ok(ast.parse("_ss_record_ok", mode="eval").body)
    assert not _is_not_record_ok(ast.parse("not other", mode="eval").body)
    assert _and_with_bare_record_ok(ast.parse("a and _ss_record_ok", mode="eval").body)
    assert not _and_with_bare_record_ok(ast.parse("a and not _ss_record_ok", mode="eval").body)
    assert not _and_with_bare_record_ok(ast.parse("a or _ss_record_ok", mode="eval").body)


def test_save_and_memo_pop_inside_record_ok_if():
    tree = _app()
    saves = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "save_structural_scan_cache"]
    assert len(saves) == 1

    parents = {}
    for n in ast.walk(tree):
        for ch in ast.iter_child_nodes(n):
            parents[ch] = n

    def inside_guard(target):
        # The INNERMOST enclosing If must itself test _ss_record_ok -- an outer
        # click-guard If alone must not satisfy this pin.
        n = parents.get(target)
        while n is not None and not isinstance(n, ast.If):
            n = parents.get(n)
        # NB-1: polarity -- `_ narrative and not _ss_record_ok` must not pass.
        return n is not None and _and_with_bare_record_ok(n.test)

    assert inside_guard(saves[0])
    pops = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "pop" and n.args
                and isinstance(n.args[0], ast.JoinedStr)):
            consts = "".join(v.value for v in n.args[0].values if isinstance(v, ast.Constant))
            fvals = [v for v in n.args[0].values if isinstance(v, ast.FormattedValue)]
            if "_struct_alert_baseline_" in consts and any(
                    "_ss_scan_date" in _names(v.value) for v in fvals):
                pops.append(n)
    assert len(pops) == 1
    assert inside_guard(pops[0])
