"""C5b: 'who made the call' derived from the app's stored calls (no user
fields except STOP_HIT to avoid mislabelling an unlogged stop sale)."""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import self_track_record as stv
from stock_analyzer.constants import (
    SELF_TRACK_MATCH_LOOKBACK_DAYS as LB,
    SELF_TRACK_RELIABLE_LOG_START,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS as SW,
)

pytestmark = pytest.mark.fast

D = date(2026, 10, 12)


def _t(action="BUY", tk="AAA", d=D, **kw):
    r = {"id": 1, "ticker": tk, "traded_at": d.isoformat(), "action": action,
         "shares": 10.0, "price": 100.0, "user_thesis": ""}
    r.update(kw)
    return r


def _buys(recs=None, ev=None, trade=None):
    return stv.classify_buys(
        pd.DataFrame([trade or _t()]), pd.DataFrame(recs or [], columns=["ticker", "rec_date", "rec_type", "already_held"]) if not recs else pd.DataFrame(recs),
        {"AAA"}, set(), SELF_TRACK_RELIABLE_LOG_START, LB, rec_events_df=ev)[0]


def _sells(sigs=None, ev=None, trade=None):
    return stv.classify_sells(
        pd.DataFrame([trade or _t("SELL")]),
        pd.DataFrame(sigs) if sigs else pd.DataFrame(columns=["ticker", "signal_date", "signal_type"]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SW, rec_events_df=ev)[0]


def _rec(rt, d=D, **kw):
    r = {"ticker": "AAA", "rec_date": d.isoformat(), "rec_type": rt}
    r.update(kw)
    return r


@pytest.mark.parametrize("rt", ["add_winner", "enter_now", "new_pick", "overflow_pick"])
def test_buy_rec_types_match_with_name(rt):
    r = _buys([_rec(rt)])
    assert r["bucket"] == "app_aligned" and r["matched_call"] == rt


@pytest.mark.parametrize("rt", ["add_winner", "enter_now"])
def test_buy_window_edges(rt):
    assert _buys([_rec(rt, D - timedelta(days=LB))])["bucket"] == "app_aligned"
    assert _buys([_rec(rt, D - timedelta(days=LB + 1))])["bucket"] != "app_aligned"
    assert _buys([_rec(rt, D + timedelta(days=1))])["bucket"] != "app_aligned"


def test_enter_now_already_held_does_not_match():
    assert _buys([_rec("enter_now", already_held=True)])["bucket"] != "app_aligned"
    assert _buys([_rec("enter_now", already_held=False)])["bucket"] == "app_aligned"
    assert _buys([_rec("enter_now", already_held=None)])["bucket"] == "app_aligned"


def test_diversify_add_matches_and_edges():
    ev = lambda d: pd.DataFrame([{"rec_type": "diversify_add", "ticker": "aaa",
                                  "fired_date": d.isoformat()}])
    r = _buys(ev=ev(D - timedelta(days=LB)))
    assert r["bucket"] == "app_aligned" and r["matched_call"] == "diversify_add"
    assert _buys(ev=ev(D - timedelta(days=LB + 1)))["bucket"] != "app_aligned"


def test_unmatched_buy_has_none_matched_call():
    assert _buys()["matched_call"] is None


@pytest.mark.parametrize("st,name", [("EXIT", "exit_EXIT"), ("TRIM", "exit_TRIM"),
                                     ("RISK_OFF", "exit_RISK_OFF")])
def test_sell_signal_types_match(st, name):
    sg = lambda d: [{"ticker": "AAA", "signal_date": d.isoformat(), "signal_type": st}]
    r = _sells(sg(D - timedelta(days=SW)))
    assert r["bucket"] == "engine_aligned" and r["matched_call"] == name
    assert _sells(sg(D - timedelta(days=SW + 1)))["bucket"] != "engine_aligned"


def test_watch_never_matches():
    r = _sells([{"ticker": "AAA", "signal_date": D.isoformat(), "signal_type": "WATCH"}])
    assert r["bucket"] == "self_initiated" and r["matched_call"] is None


@pytest.mark.parametrize("rt", ["rebal_trim", "beta_trim"])
def test_sell_rec_events_match(rt):
    ev = lambda d: pd.DataFrame([{"rec_type": rt, "ticker": "AAA", "fired_date": d.isoformat()}])
    r = _sells(ev=ev(D - timedelta(days=SW)))
    assert r["bucket"] == "engine_aligned" and r["matched_call"] == rt
    assert _sells(ev=ev(D - timedelta(days=SW + 1)))["bucket"] != "engine_aligned"


def test_stop_hit_unmatched_is_stop_unlogged():
    r = _sells(trade=_t("SELL", trigger_type="STOP_HIT"))
    assert r["bucket"] == "stop_unlogged" and r["matched_call"] is None


def test_stop_hit_with_matching_call_is_engine_aligned():
    r = _sells([{"ticker": "AAA", "signal_date": D.isoformat(), "signal_type": "EXIT"}],
               trade=_t("SELL", trigger_type="STOP_HIT"))
    assert r["bucket"] == "engine_aligned"


@pytest.mark.parametrize("tt", [None, "MANUAL", "", float("nan")])
def test_non_stop_trigger_is_not_stop(tt):
    assert _sells(trade=_t("SELL", trigger_type=tt))["bucket"] == "self_initiated"
    assert _sells(trade=_t("SELL"))["bucket"] == "self_initiated"  # column absent


def test_sell_summary_excludes_stop_unlogged_from_grading():
    cl = [_sells(trade=_t("SELL", trigger_type="STOP_HIT")),
          _sells(trade=_t("SELL"))]
    s = stv.self_vs_engine_sell_summary(cl, {"AAA": 110.0}, {}, D + timedelta(days=60), 1)
    assert s["n_stop_unlogged"] == 1 and s["n_self_initiated"] == 1
    assert s["n_coverage_limited"] == 0


def test_coverage_helper():
    recs = pd.DataFrame([_rec("enter_now", date(2026, 9, 5)), _rec("enter_now", date(2026, 9, 1)),
                         _rec("add_winner", date(2026, 9, 9))])
    sig = pd.DataFrame([{"ticker": "A", "signal_date": "2026-08-01", "signal_type": "RISK_OFF"},
                        {"ticker": "A", "signal_date": "2026-08-02", "signal_type": "WATCH"}])
    ev = pd.DataFrame([{"rec_type": "beta_trim", "ticker": "A", "fired_date": "2026-09-17"}])
    c = stv.matched_call_coverage(recs, sig, ev)
    assert c["enter_now"] == date(2026, 9, 1) and c["add_winner"] == date(2026, 9, 9)
    assert c["exit_RISK_OFF"] == date(2026, 8, 1) and c["beta_trim"] == date(2026, 9, 17)
    assert c["diversify_add"] is None and c["exit_EXIT"] is None
    c2 = stv.matched_call_coverage(None, None)
    assert all(v is None for v in c2.values())


def test_backward_compat_golden():
    """Old rec/signal types only, no rec_events -> same buckets as before."""
    trades = pd.DataFrame([_t("BUY", d=D), _t("BUY", tk="ZZZ", d=D),
                           _t("SELL", d=D), _t("SELL", tk="ZZZ", d=date(2026, 1, 5))])
    recs = pd.DataFrame([_rec("new_pick", D), _rec("buy_candidate", D, composite_score=10)])
    sigs = pd.DataFrame([{"ticker": "AAA", "signal_date": D.isoformat(), "signal_type": "TRIM"}])
    b = stv.classify_buys(trades, recs, {"AAA"}, set(), SELF_TRACK_RELIABLE_LOG_START, LB)
    s = stv.classify_sells(trades, sigs, SELF_TRACK_SELL_RELIABLE_LOG_START, SW)
    assert [r["bucket"] for r in b] == ["app_aligned", "self_out_of_scope"]
    assert [r["bucket"] for r in s] == ["engine_aligned", "coverage_limited"]


# ── review fixes (2026-10-20) ────────────────────────────────────────────────
import ast as _ast
from pathlib import Path as _Path
import pandas as _pd


def test_coverage_caption_distinguishes_unreadable_from_no_rows():
    from stock_analyzer import self_track_record as stv
    empty = _pd.DataFrame()
    c = stv.matched_call_coverage_caption(empty, empty, None)
    assert "diversify adds couldn't be read" in c
    assert "rebalance trims couldn't be read" in c and "beta trims couldn't be read" in c
    assert "Grow Today picks no rows yet" in c and "EXIT signals no rows yet" in c
    c2 = stv.matched_call_coverage_caption(None, None, empty)
    assert "Grow Today picks couldn't be read" in c2
    assert "risk-off signals couldn't be read" in c2
    assert "diversify adds no rows yet" in c2


def test_rule_reclassified_caption_lists_every_matched_buy_call():
    from stock_analyzer import self_track_record as stv
    c = stv.rule_reclassified_caption(2, "2026-10-14")
    for needle in ("Grow Today pick", "add-to-winner", "Watchlist ready-to-enter",
                   "diversify-add"):
        assert needle in c


def test_investigator_classify_sells_summary_names_new_calls():
    from stock_analyzer import investigator as inv
    s = inv.TOOLBOX["classify_sells"]["summary"]
    assert "risk-off" in s and "stop_unlogged" in s


def _sell_fixture():
    trades = _pd.DataFrame([{
        "id": 1, "ticker": "AAA", "action": "SELL", "shares": 1, "price": 10.0,
        "traded_at": "2026-10-10T10:00:00-04:00", "trigger_type": "MANUAL"}])
    ev = _pd.DataFrame([{"rec_type": "beta_trim", "ticker": "AAA",
                         "fired_date": "2026-10-09"}])
    return trades, ev


def test_investigator_adapter_passes_rec_events_to_classify_sells():
    from stock_analyzer import investigator as inv
    trades, ev = _sell_fixture()
    exits = _pd.DataFrame(columns=["ticker", "signal_type", "signal_date"])
    base = {"trades_df": trades, "exit_signals_df": exits}
    without = inv._adapt_classify_sells(base)
    with_ev = inv._adapt_classify_sells({**base, "rec_events_df": ev})
    assert without != with_ev, "rec_events_df is not reaching classify_sells"


def test_investigator_adapters_source_pass_rec_events_kwarg():
    src = (_Path(__file__).resolve().parent.parent / "stock_analyzer" / "investigator.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    for fn in ("_adapt_classify_buys", "_adapt_classify_sells"):
        node = next(n for n in _ast.walk(tree)
                    if isinstance(n, _ast.FunctionDef) and n.name == fn)
        kws = [k.arg for c in _ast.walk(node) if isinstance(c, _ast.Call)
               for k in c.keywords]
        assert "rec_events_df" in kws, fn


def test_analysis_scripts_keep_exit_trim_only_signals():
    root = _Path(__file__).resolve().parent.parent / "scripts"
    s1 = (root / "exit_early_cost_analysis.py").read_text(encoding="utf-8")
    assert "trades_df, scoped," in s1 and "trades_df, signals_df," not in s1
    s2 = (root / "holding_period_analysis.py").read_text(encoding="utf-8")
    i = s2.index("_stv.classify_sells(")
    assert '.isin(["EXIT", "TRIM"])' in s2[:i]


def test_risk_off_row_would_flip_unscoped_but_not_scoped_frame():
    # Premise of the scripts' fix: the full frame changes the bucket, the
    # EXIT/TRIM-scoped frame does not.
    from stock_analyzer import self_track_record as stv
    from datetime import date
    trades, _ = _sell_fixture()
    sig = _pd.DataFrame([{"ticker": "AAA", "signal_type": "RISK_OFF",
                          "signal_date": "2026-10-09"}])
    scoped = sig[sig["signal_type"].isin(["EXIT", "TRIM"])]
    full = stv.classify_sells(trades, sig, date(2026, 8, 1), 5)
    sc = stv.classify_sells(trades, scoped, date(2026, 8, 1), 5)
    assert full[0]["bucket"] == "engine_aligned"
    assert sc[0]["bucket"] == "self_initiated"
