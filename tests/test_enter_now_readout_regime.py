"""Tests for the entry regime additions to the ENTER_NOW readout (C2 of
docs/plans/entry-regime-check.md): rules computed through entry_regime, the
stock's own return beside alpha, hold rate and flips, the pre-registered go-live
check, and the --rec-types dedupe. Read-only diagnostics; synthetic data only.
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import enter_now_readout as eno
from stock_analyzer import entry_regime as er
from stock_analyzer import market_trend as mt
from stock_analyzer.constants import (
    ENTRY_REGIME_GO_LIVE_EARLY_HORIZON,
    ENTRY_REGIME_GO_LIVE_MARGIN_PP,
    GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_HORIZON_TRADING_DAYS,
    GATE_LEDGER_MIN_TICKERS,
)
from stock_analyzer.data import is_trading_day
from stock_analyzer.predictive_analytics import forward_alpha_at_horizon
from stock_analyzer.recommendations_history import _spy_return_pct

pytestmark = pytest.mark.fast

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "enter_now_regime_readout.py"

PRIOR = date(2026, 11, 20)        # Friday
CALL = date(2026, 11, 23)         # Monday


# -- builders -----------------------------------------------------------------

def _trend(as_of=PRIOR, label="weakening", ma200="above", ndx="steady"):
    def row(sym, lab, side200):
        r = {k: None for k in mt._ROW_KEYS}
        r.update({"symbol": sym, "name": sym, "app_sectors": [], "label": lab, "reason": "r",
                  "confirm_k": 1, "ma50_side": "below", "ma200_side": side200,
                  "from_high_pct": -5.0, "drawdown_word": None, "ma50_run_capped": False})
        return r
    return {"as_of": as_of,
            "indices": [row("^GSPC", label, ma200), row("^IXIC", ndx, "above")],
            "sectors": [{"symbol": "SOXX", "app_sectors": ["Semiconductors"], "label": "rising"}],
            "not_checked": []}


def _vix(day, value):
    return pd.Series([value], index=pd.DatetimeIndex([pd.Timestamp(day)]))


def _classify(trend, vix):
    call = {"ticker": "NVDA", "rec_date": CALL, "sector": "Semiconductors"}
    return eno.classify_call(call, spy_close=pd.Series(dtype=float), spx_close=pd.Series(dtype=float),
                             vix_close=vix, trend_fn=lambda as_of: trend)


# -- classify_call goes through entry_regime ----------------------------------

@pytest.mark.parametrize("label", ["falling", "weakening", "steady", "rising"])
@pytest.mark.parametrize("ma200", ["below", "above"])
@pytest.mark.parametrize("vix", [None, 24.99, 25.0])
def test_classify_call_rules_equal_entry_regime_decide_on_the_same_read(label, ma200, vix):
    trend = _trend(label=label, ma200=ma200)
    vix_s = None if vix is None else _vix(PRIOR, vix)
    out = _classify(trend, vix_s)
    rd = er.read(trend, er.vix_close_asof(vix_s, PRIOR), PRIOR)
    for rule, field in (("A", "rule_a"), ("B", "rule_b"), ("C", "rule_c")):
        assert out[field] == er.decide(rd, rule)["state"]
    assert out["entry_regime_read_state"] == "ok"


def test_classify_call_unreadable_trend_is_unknown_never_clear():
    out = _classify(None, _vix(PRIOR, 15.0))
    assert (out["rule_a"], out["rule_b"], out["rule_c"]) == ("unknown", "unknown", "unknown")
    assert out["entry_regime_read_state"] == "trend_unread"
    assert out["ndx_label"] == "not checked"


def test_classify_call_trend_for_another_session_is_unknown():
    out = _classify(_trend(as_of=date(2026, 11, 19), label="falling"), _vix(PRIOR, 30.0))
    assert (out["rule_a"], out["rule_b"]) == ("unknown", "unknown")
    assert out["rule_c"] == "hold"        # the VIX leg alone still trips


def test_classify_call_uses_the_vix_dated_the_prior_session_only():
    trend = _trend(label="rising", ma200="above")
    # a VIX spike on the call day and a calm close two sessions back are both ignored
    vix = pd.Series([14.0, 60.0], index=pd.DatetimeIndex([date(2026, 11, 19), CALL]))
    out = _classify(trend, vix)
    assert out["vix_prior_close"] is None and out["rule_c"] == "unknown"
    assert _classify(trend, _vix(PRIOR, 14.0))["rule_c"] == "clear"


def test_rule_fields_do_not_come_from_the_old_regime_of_path():
    # SPY broken below its 200-day mean arms the OLD regime read, while the S&P
    # trend row reads Rising: rule A / B clear, regime_armed yes.
    days = [d for d in (PRIOR - timedelta(days=i) for i in range(400)) if is_trading_day(d)][::-1][-260:]
    spy = pd.Series([100.0] * (len(days) - 1) + [50.0], index=pd.DatetimeIndex(days))
    call = {"ticker": "NVDA", "rec_date": CALL}
    out = eno.classify_call(call, spy_close=spy, spx_close=spy, vix_close=_vix(PRIOR, 14.0),
                            trend_fn=lambda as_of: _trend(label="rising", ma200="above"))
    assert out["regime_armed"] == "yes"
    assert (out["rule_a"], out["rule_b"], out["rule_c"]) == ("clear", "clear", "clear")


def test_classify_call_records_the_nasdaq_label_and_keeps_the_old_fields():
    out = _classify(_trend(ndx="falling"), _vix(PRIOR, 14.0))
    assert out["ndx_label"] == "falling"
    for k in ("regime_armed", "spx_label", "sector_label", "day_tone_approx", "already_held"):
        assert k in out
    assert out["spx_label"] == "weakening" and out["sector_label"] == "rising"


def test_rules_use_real_compute_trend_output():
    n = 300
    sessions = [d for d in (PRIOR - timedelta(days=i) for i in range(500)) if is_trading_day(d)][::-1][-n:]
    frame = pd.DataFrame({s: np.full(n, 100.0) for s in mt.trend_symbols()},
                         index=pd.DatetimeIndex(sessions))
    frame["^GSPC"] = np.linspace(80.0, 120.0, n)       # steadily rising
    out = eno.classify_call({"ticker": "NVDA", "rec_date": CALL}, spy_close=frame["^GSPC"],
                            spx_close=frame["^GSPC"], vix_close=_vix(PRIOR, 15.0),
                            trend_fn=lambda as_of: mt.compute_trend(frame, as_of=as_of))
    assert out["spx_label"] == "rising"
    assert (out["rule_a"], out["rule_b"], out["rule_c"]) == ("clear", "clear", "clear")


# -- SPLITS -------------------------------------------------------------------

def test_splits_carry_the_rule_splits_in_hold_clear_unknown_order_and_label_the_exploratory_one():
    by_field = {f: (t, o) for t, f, o in eno.SPLITS}
    for f, tag in (("rule_a", "PRIMARY, pre-registered"), ("rule_b", "secondary"),
                   ("rule_c", "secondary")):
        title, order = by_field[f]
        assert tag in title and order == ("hold", "clear", "unknown")
    assert "EXPLORATORY" in by_field["ndx_label"][0] and "not pre-registered" in by_field["ndx_label"][0]


# -- the stock's own return ---------------------------------------------------

def test_forward_return_pct_uses_the_alpha_window_and_returns_the_own_return():
    calls = []

    def close_fn(tk, start, end):
        calls.append((tk, start, end))
        return 110.0
    rec = date(2026, 9, 14)
    target = eno.advance_trading_days(rec, 30)
    assert eno.forward_return_pct("NVDA", rec, 100.0, 30, close_fn) == pytest.approx(10.0)
    assert calls == [("NVDA", target, target + timedelta(days=7))]


@pytest.mark.parametrize("price", [None, 0, -5, float("nan"), "x"])
def test_forward_return_pct_needs_a_positive_entry_price(price):
    assert eno.forward_return_pct("NVDA", date(2026, 9, 14), price, 10, lambda *a: 110.0) is None


@pytest.mark.parametrize("fwd", [None, 0, -1, float("nan"), "x"])
def test_forward_return_pct_needs_a_usable_forward_close(fwd):
    assert eno.forward_return_pct("NVDA", date(2026, 9, 14), 100.0, 10, lambda *a: fwd) is None


def test_forward_return_pct_never_raises():
    def boom(*a):
        raise RuntimeError("provider down")
    assert eno.forward_return_pct("NVDA", date(2026, 9, 14), 100.0, 10, boom) is None
    assert eno.forward_return_pct("", date(2026, 9, 14), 100.0, 10, boom) is None
    assert eno.forward_return_pct("NVDA", None, 100.0, 10, boom) is None


@pytest.mark.parametrize("fwd,spy_end", [(112.5, 410.0), (95.0, 405.0), (100.0, 380.0)])
def test_stock_return_minus_spy_return_equals_forward_alpha_on_the_same_inputs(fwd, spy_end):
    rec = date(2025, 3, 3)
    h = 10
    target = eno.advance_trading_days(rec, h)
    spy = {rec: 400.0, target: spy_end}
    close_fn = lambda tk, start, end: fwd  # noqa: E731
    alpha = forward_alpha_at_horizon("NVDA", rec, 100.0, h, spy, close_fn)
    ret = eno.forward_return_pct("NVDA", rec, 100.0, h, close_fn)
    assert alpha is not None and ret is not None
    assert round(ret - _spy_return_pct(spy, rec, target), 2) == alpha


def test_grade_return_never_prices_an_unmatured_call():
    def must_not_run(*a):
        raise AssertionError("priced an unmatured call")
    call = {"ticker": "NVDA", "rec_date": date(2026, 11, 20), "price_at_surface": 100.0}
    assert eno.grade_return(call, 30, date(2026, 11, 22), must_not_run) is None


def test_grade_return_matured_passes_the_recorded_price_and_cleans_the_answer():
    seen = []

    def fn(tk, d, price, h):
        seen.append((tk, d, price, h))
        return 7.5
    call = {"ticker": "NVDA", "rec_date": date(2026, 9, 14), "price_at_surface": 100.0}
    assert eno.grade_return(call, 10, date(2026, 11, 22), fn) == 7.5
    assert seen == [("NVDA", date(2026, 9, 14), 100.0, 10)]
    assert eno.grade_return(call, 10, date(2026, 11, 22), lambda *a: float("nan")) is None


def test_returns_by_counts_only_rows_with_both_alpha_and_return():
    rows = [
        {"f": "a", "alpha_10": 1.0, "ret_10": 5.0},
        {"f": "a", "alpha_10": None, "ret_10": 9.0},       # unmatured / unpriced: out
        {"f": "a", "alpha_10": 2.0, "ret_10": None},       # no return: out
        {"f": "b", "alpha_10": -1.0, "ret_10": -3.0},
    ]
    cells = eno.returns_by(rows, "f", "alpha_10", "ret_10", ("a", "b", "c"))
    assert cells == {"a": [5.0], "b": [-3.0], "c": []}


def test_attach_mean_return_withholds_a_building_cell_and_means_a_measured_one():
    summ = eno.summarise({"x": [1.0, 2.0, 3.0], "y": [1.0]}, min_calls=3)
    out = eno.attach_mean_return(summ, {"x": [10.0, -4.0, 6.0], "y": [50.0]})
    assert out[0]["mean_return"] == pytest.approx(4.0) and out[0]["n_returns"] == 3
    assert out[1]["mean_return"] is None and out[1]["verdict"] == "building"
    assert out[0]["mean_alpha"] == pytest.approx(2.0)      # alpha figures untouched


# -- hold rate and flips ------------------------------------------------------

def _call(d, verdict, field="rule_a"):
    return {"rec_date": d, field: verdict}


def test_hold_rate_and_flips_counts_dates_calls_and_skips_unknown_dates():
    mon, tue, wed, thu, fri = (date(2026, 11, 16) + timedelta(days=i) for i in range(5))
    calls = [_call(mon, "hold"), _call(mon, "hold"), _call(tue, "hold"), _call(wed, "clear"),
             _call(thu, "unknown"), _call(fri, "hold")]
    st = eno.hold_rate_and_flips(calls, "rule_a")
    assert st["n_calls"] == 6 and st["hold_calls"] == 4
    assert st["n_dates"] == 5 and st["hold_dates"] == 3 and st["clear_dates"] == 1
    assert st["unknown_dates"] == 1
    assert st["hold_share_dates"] == pytest.approx(3 / 5)
    assert st["hold_share_calls"] == pytest.approx(4 / 6)
    assert st["flips"] == 2                      # hold -> clear -> (unknown skipped) -> hold
    assert st["session_span"] == 4
    assert st["flips_per_20_sessions"] == pytest.approx(2 / 4 * 20)


def test_hold_rate_and_flips_empty_and_single_date():
    st = eno.hold_rate_and_flips([], "rule_a")
    assert st["n_dates"] == 0 and st["hold_share_dates"] is None and st["flips"] == 0
    one = eno.hold_rate_and_flips([_call(date(2026, 11, 16), "hold")], "rule_a")
    assert one["flips"] == 0 and one["flips_per_20_sessions"] is None


def test_hold_rate_and_flips_disagreeing_calls_on_one_date_count_unknown():
    d = date(2026, 11, 16)
    st = eno.hold_rate_and_flips([_call(d, "hold"), _call(d, "clear")], "rule_a")
    assert st["unknown_dates"] == 1 and st["hold_dates"] == 0


def test_format_hold_lines_names_each_rule_and_keeps_the_per_call_line():
    st = {"rule A": eno.hold_rate_and_flips([_call(date(2026, 11, 16), "hold")], "rule_a")}
    line, calls_line = eno.format_hold_lines(st)
    assert "rule A" in line and "1/1 call dates" in line and "flips" in line
    assert "of the calls" in calls_line and "1/1" in calls_line


def _trend_fn_by_date(labels):
    """trend_fn returning a full trend for each prior session (label by date)."""
    def fn(as_of):
        lab = labels.get(as_of)
        return None if lab is None else _trend(as_of=as_of, label=lab, ma200="above")
    return fn


def test_session_rule_verdicts_cover_every_nyse_session_and_skip_weekends_and_holidays():
    first, last = date(2026, 11, 23), date(2026, 12, 1)       # Mon .. Tue, Thanksgiving inside
    out = eno.session_rule_verdicts(first, last, trend_fn=lambda a: None, vix_close=None)
    expected = [d for d in (first + timedelta(days=i) for i in range((last - first).days + 1))
                if is_trading_day(d)]
    assert sorted(out) == expected
    assert date(2026, 11, 26) not in out and date(2026, 11, 28) not in out
    assert all(v == {"rule_a": "unknown", "rule_b": "unknown", "rule_c": "unknown"} for v in out.values())


def test_session_verdicts_equal_what_classify_call_reads_for_a_call_that_day():
    labels = {date(2026, 11, 20): "falling", date(2026, 11, 23): "rising", date(2026, 11, 24): "steady"}
    fn = _trend_fn_by_date(labels)
    vix = pd.Series([30.0, 14.0, 20.0], index=pd.DatetimeIndex(
        [date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)]))
    out = eno.session_rule_verdicts(date(2026, 11, 23), date(2026, 11, 25), trend_fn=fn, vix_close=vix)
    assert len(out) == 3
    for d, verdicts in out.items():
        c = eno.classify_call({"ticker": "NVDA", "rec_date": d}, spy_close=pd.Series(dtype=float),
                              spx_close=pd.Series(dtype=float), vix_close=vix, trend_fn=fn)
        assert verdicts == {"rule_a": c["rule_a"], "rule_b": c["rule_b"], "rule_c": c["rule_c"]}


def test_hold_rate_over_sessions_counts_sessions_without_a_call_and_exact_flips():
    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]          # Mon..Fri
    sess = {d: {"rule_a": v} for d, v in zip(days, ["hold", "hold", "clear", "unknown", "hold"])}
    calls = [_call(days[0], "hold")]                       # only ONE call date
    st = eno.hold_rate_and_flips(calls, "rule_a", sess)
    assert st["basis"] == "sessions"
    assert st["n_dates"] == 5 and st["hold_dates"] == 3 and st["unknown_dates"] == 1
    assert st["hold_share_dates"] == pytest.approx(3 / 5)
    assert st["flips"] == 2 and st["session_span"] == 4
    assert st["flips_per_20_sessions"] == pytest.approx(10.0)
    assert st["hold_share_calls"] == 1.0                   # the per-call share is still reported
    assert "NYSE sessions" in eno.format_hold_lines({"rule A": st})[0]


# -- go_live_check ------------------------------------------------------------

def _eps(prefix, n, beats, verdict, *, win=5.0, lose=-5.0, same_ticker=None, field="alpha_30"):
    """n first-call rows; `beats` of them have a positive alpha."""
    rows = []
    for i in range(n):
        rows.append({"ticker": same_ticker or f"{prefix}{i}", "rule_a": verdict,
                     field: win if i < beats else lose})
    return rows


def _merge(rows30, rows10):
    """Episode rows carrying both alpha fields (the same list feeds both args)."""
    out = []
    for a, b in zip(rows30, rows10):
        r = dict(a)
        r["alpha_10"] = b["alpha_10"]
        out.append(r)
    return out


def _all_met_rows():
    h30 = _eps("H", 20, 5, "hold")                       # hold beat 25%, median < 0
    p30 = _eps("P", 20, 13, "clear")                     # pass beat 65%
    h10 = _eps("H", 20, 6, "hold", field="alpha_10")     # 10-session: hold below pass
    p10 = _eps("P", 20, 12, "clear", field="alpha_10")
    return _merge(h30 + p30, h10 + p10)


def test_go_live_all_met():
    rows = _all_met_rows()
    res = eno.go_live_check(rows, rows)
    assert [c["status"] for c in res["criteria"]] == ["MET"] * 4
    assert res["overall"] == "ALL MET" and res["data_sufficient"]
    assert res["criteria"][1]["gap_pp"] == pytest.approx(40.0)


def test_go_live_not_met_on_the_margin():
    h30 = _eps("H", 20, 9, "hold")        # 45%
    p30 = _eps("P", 20, 12, "clear")      # 60%: gap 15 pp
    p30b = _eps("P", 20, 11, "clear")     # 55%: gap only 10 pp
    h10 = _eps("H", 20, 6, "hold", field="alpha_10")
    p10 = _eps("P", 20, 12, "clear", field="alpha_10")
    ok = eno.go_live_check(_merge(h30 + p30, h10 + p10), _merge(h30 + p30, h10 + p10))
    assert ok["criteria"][1]["status"] == "MET"           # exactly 15 pp counts
    rows = _merge(h30 + p30b, h10 + p10)
    res = eno.go_live_check(rows, rows)
    assert res["criteria"][1]["status"] == "NOT MET"
    assert res["criteria"][0]["status"] == "MET" and res["overall"] == "NOT MET"
    assert res["data_sufficient"]


def test_go_live_margin_and_early_horizon_are_the_pre_registered_constants():
    # plan section 4 (owner U5): 15 pp, 10 sessions, at the 30-session headline
    assert ENTRY_REGIME_GO_LIVE_MARGIN_PP == 15.0
    assert ENTRY_REGIME_GO_LIVE_EARLY_HORIZON == 10
    assert GATE_LEDGER_HORIZON_TRADING_DAYS == 30
    assert not hasattr(eno, "GO_LIVE_MARGIN_PP")       # no second, bare copy of the 15
    rows = _all_met_rows()
    assert eno.go_live_check(rows, rows, margin_pp=50.0)["criteria"][1]["status"] == "NOT MET"


def test_go_live_not_met_when_the_hold_median_is_not_below_zero():
    # hold beats half the time with winners larger than the losses: median > 0
    h30 = _eps("H", 20, 10, "hold", win=5.0, lose=-1.0)
    p30 = _eps("P", 20, 18, "clear")                      # 90%: margin met (40 pp)
    h10 = _eps("H", 20, 6, "hold", field="alpha_10")
    p10 = _eps("P", 20, 12, "clear", field="alpha_10")
    rows = _merge(h30 + p30, h10 + p10)
    res = eno.go_live_check(rows, rows)
    assert res["criteria"][1]["status"] == "MET"
    assert res["criteria"][2]["status"] == "NOT MET"
    assert res["overall"] == "NOT MET"


def test_go_live_thin_cell_is_not_enough_data_and_never_all_met():
    h30 = _eps("H", GATE_LEDGER_FIRM_CALLS - 1, 2, "hold")
    p30 = _eps("P", 20, 13, "clear")
    h10 = _eps("H", GATE_LEDGER_FIRM_CALLS - 1, 3, "hold", field="alpha_10")
    p10 = _eps("P", 20, 12, "clear", field="alpha_10")
    pad = [{"alpha_10": None}] * (len(h30) + len(p30) - len(h10) - len(p10))
    rows = _merge(h30 + p30, h10 + p10 + pad)
    res = eno.go_live_check(rows, rows)
    st = [c["status"] for c in res["criteria"]]
    assert st[0] == "NOT ENOUGH DATA" and st[1] == "NOT ENOUGH DATA" and st[2] == "NOT ENOUGH DATA"
    assert res["overall"] == "NOT MET" and not res["data_sufficient"]
    assert res["hold"]["figures_shown"] is False


def test_go_live_too_few_distinct_tickers_is_not_enough_data():
    h30 = _eps("H", 20, 5, "hold", same_ticker="AAA")
    p30 = _eps("P", 20, 13, "clear")
    assert GATE_LEDGER_MIN_TICKERS > 1
    h10 = _eps("H", 20, 6, "hold", same_ticker="AAA", field="alpha_10")
    p10 = _eps("P", 20, 12, "clear", field="alpha_10")
    rows = _merge(h30 + p30, h10 + p10)
    res = eno.go_live_check(rows, rows)
    assert res["hold"]["tickers"] == 1
    assert res["criteria"][0]["status"] == "NOT ENOUGH DATA"
    assert res["overall"] == "NOT MET"


def test_go_live_ten_session_sign_disagreement_is_not_met():
    h30 = _eps("H", 20, 5, "hold")
    p30 = _eps("P", 20, 13, "clear")
    h10 = _eps("H", 20, 14, "hold", field="alpha_10")     # 10-session: hold ABOVE pass
    p10 = _eps("P", 20, 10, "clear", field="alpha_10")
    rows = _merge(h30 + p30, h10 + p10)
    res = eno.go_live_check(rows, rows)
    assert [c["status"] for c in res["criteria"]] == ["MET", "MET", "MET", "NOT MET"]
    assert res["criteria"][3]["diff10_pp"] > 0
    assert res["overall"] == "NOT MET"


def test_go_live_leaves_unknown_and_unmatured_episodes_out_of_both_cells():
    rows = _all_met_rows()
    rows += [{"ticker": f"U{i}", "rule_a": "unknown", "alpha_30": -9.0, "alpha_10": -9.0}
             for i in range(10)]
    rows += [{"ticker": f"M{i}", "rule_a": "hold", "alpha_30": None, "alpha_10": None}
             for i in range(10)]
    res = eno.go_live_check(rows, rows)
    assert res["hold"]["n"] == 20 and res["pass"]["n"] == 20
    assert res["unknown_30"] == 10
    assert res["overall"] == "ALL MET"


def test_format_go_live_lines_states_criterion_4_reading_and_prints_the_10_session_cells():
    rows = _all_met_rows()
    lines = eno.format_go_live_lines(eno.go_live_check(rows, rows))
    text = "\n".join(lines)
    assert ("(4) reads: 10-session hold beat-SPY share below the pass share; same floors; "
            "see plan") in text and "clarification 2026-11-22" in text
    i4 = next(i for i, ln in enumerate(lines) if ln.strip().startswith("(4) 10-session read"))
    below = "\n".join(lines[i4:])
    assert "A would hold (10)" in below and "A would pass (10)" in below
    assert below.count("median alpha") == 2                # both cells' figures shown (above floors)
    thin = eno.go_live_check(_eps("H", 3, 1, "hold"), _eps("H", 3, 1, "hold", field="alpha_10"))
    thin_text = "\n".join(eno.format_go_live_lines(thin))
    assert "A would hold (10)" in thin_text and "figures withheld" in thin_text


def test_go_live_ten_session_cells_include_episodes_not_matured_at_30():
    rows = _all_met_rows()
    extra = [{"ticker": f"X{i}", "rule_a": "hold", "alpha_30": None, "alpha_10": -3.0}
             for i in range(4)]
    res = eno.go_live_check(rows + extra, rows + extra)
    assert res["hold"]["n"] == 20 and res["hold_10"]["n"] == 24


def test_go_live_population_is_default_gap_and_no_since_only():
    assert eno.go_live_population_ok(eno.EPISODE_GAP_SESSIONS, "") is True
    assert eno.go_live_population_ok(eno.EPISODE_GAP_SESSIONS, None) is True
    assert eno.go_live_population_ok(eno.EPISODE_GAP_SESSIONS + 1, "") is False
    assert eno.go_live_population_ok(eno.EPISODE_GAP_SESSIONS, "2026-10-01") is False
    assert "go-live block skipped" in eno.GO_LIVE_SKIPPED_LINE
    assert "not the pre-registered population" in eno.GO_LIVE_SKIPPED_LINE
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "go_live_population_ok(args.episode_gap, args.since)" in src


def test_format_go_live_lines_withholds_thin_figures_and_prints_overall():
    thin = eno.go_live_check(_eps("H", 3, 1, "hold"), _eps("H", 3, 1, "hold", field="alpha_10"))
    text = "\n".join(eno.format_go_live_lines(thin))
    assert "figures withheld" in text and "OVERALL: NOT MET" in text
    assert "NOT ENOUGH DATA" in text and "%" not in text.split("A would hold")[1].split("\n")[0]
    rows = _all_met_rows()
    full = "\n".join(eno.format_go_live_lines(eno.go_live_check(rows, rows)))
    assert "OVERALL: ALL MET" in full and "MET" in full and "median alpha" in full
    assert "pre-registered" in full


# -- --rec-types dedupe -------------------------------------------------------

def test_dedupe_by_rec_type_keeps_one_call_per_type_but_default_merges():
    rows = [
        {"ticker": "NVDA", "rec_date": "2026-11-16", "rec_type": "enter_now", "id": 1},
        {"ticker": "nvda", "rec_date": "2026-11-16", "rec_type": "new_pick", "id": 2},
        {"ticker": "NVDA", "rec_date": "2026-11-16", "rec_type": "enter_now", "id": 3},
    ]
    typed = eno.dedupe_calls(rows, by_rec_type=True)
    assert [(c["ticker"], c["rec_type"], c["id"]) for c in typed] == [
        ("NVDA", "enter_now", 1), ("NVDA", "new_pick", 2)]
    assert len(eno.dedupe_calls(rows)) == 1          # old behaviour unchanged


# -- the script ---------------------------------------------------------------

def _load_script():
    spec = importlib.util.spec_from_file_location("enter_now_regime_readout_t", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_format_line_prints_mean_abs_return_after_the_alpha_figures_and_withholds_thin():
    sc = _load_script()
    summ = eno.attach_mean_return(
        eno.summarise({"hold": [1.0, -2.0, 3.0], "thin": [1.0]}, 3),
        {"hold": [4.0, -1.0, 9.0], "thin": [50.0]})
    measured = sc.format_line(summ[0], 3)
    assert "median" in measured and "mean stock return +4.00%" in measured
    assert measured.index("median") < measured.index("mean stock return")
    thin = sc.format_line(summ[1], 3)
    assert "building" in thin and "return" not in thin


def test_script_csv_columns_and_rec_types_option_exist():
    sc = _load_script()
    for col in ("rec_type", "rule_a", "rule_b", "rule_c", "ndx_label", "ret_10", "ret_30"):
        assert col in sc.CSV_COLUMNS
    src = _SCRIPT.read_text(encoding="utf-8")
    assert '"--rec-types"' in src and 'default="enter_now"' in src
    assert "by_rec_type=True" in src and "go_live_check" in src


def _synthetic_rows(n=12):
    rows = []
    for i in range(n):
        r = {"ticker": f"T{i}", "rule_a": "hold" if i % 2 else "clear", "rule_b": "clear",
             "rule_c": "clear", "ndx_label": "steady", "spx_label": "rising",
             "regime_armed": "no", "sector_label": "rising", "day_tone_approx": "flat",
             "already_held": "no"}
        for h in (10, 30):
            r[f"alpha_{h}"] = float(i - 5)
            r[f"ret_{h}"] = float(i - 4)
        rows.append(r)
    return rows


def test_script_rule_a_title_drops_primary_for_a_non_enter_now_type_and_points_to_the_go_live_block(capsys):
    sc = _load_script()
    rows = _synthetic_rows()
    st = {"graded": 12, "not_matured": 0, "unpriced": 0}
    sc.print_report(rows, rows, 30, "H", st, rec_label="NEW_PICK", compact=True, primary=False)
    out = capsys.readouterr().out
    assert "Would hold under rule A" in out and "PRIMARY" not in out and "go-live is decided" not in out
    sc.print_report(rows, rows, 30, "H", st, compact=True, primary=True, go_live_where="below")
    out = capsys.readouterr().out
    assert "(PRIMARY, pre-registered)" in out
    lines = out.splitlines()
    ep = next(i for i, ln in enumerate(lines) if ln.startswith("BY EPISODE: Would hold under rule A"))
    pointer = [ln for ln in lines[ep:ep + 6] if "go-live is decided only in the go-live block below" in ln]
    assert pointer and f"{GATE_LEDGER_FIRM_CALLS}-episode / {GATE_LEDGER_MIN_TICKERS}-ticker floors" in pointer[0]
    # the per-call block carries no pointer
    assert out.index("go-live is decided") > out.index("BY EPISODE: Would hold under rule A")


def test_script_docstring_says_one_call_is_ticker_date_rec_type():
    assert "One call = one (TICKER, rec_date, rec_type)" in _SCRIPT.read_text(encoding="utf-8")
