"""stock_analyzer.scorecard -- the account scorecard (docs/plans/account-scorecard.md, section 8).

Snapshot inputs are real DataFrames. The leverage tests monkeypatch
`period_inputs.compute_period_core` with crafted cores (the engine itself is
covered by its own tests); the real `performance_review.first_measurable_period_start`,
`period_attribution.split_moves` and `interest_posted` run on them.

The 2026 calendar used below: Thu Jun 25 (baseline), Sat Jun 27, Mon Aug 3, Thu Nov 12,
Fri Nov 13, Sat Nov 14, Sun Nov 15.
"""
import re
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import period_inputs
from stock_analyzer import scorecard as sc

D_BASE = date(2026, 6, 25)
D_END = date(2026, 11, 13)
TODAY = date(2026, 11, 13)

ST_RATE, LT_RATE = 0.30, 0.15   # test-local rates; the module never hardcodes them


# --------------------------------------------------------------------------- fixtures

def _cash_ts(d: date, hour: int = 17) -> str:
    return pd.Timestamp(f"{d.isoformat()} {hour:02d}:00", tz="America/New_York").isoformat()


def _row(d, net_equity, *, hour=17, prov=None):
    return {"snapshot_date": d.isoformat(), "net_equity": net_equity,
            "cash_as_of": _cash_ts(d, hour), "journal_max_trade_id": prov}


def _acct(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _flows(*extra, baseline=(D_BASE, 1000.0)):
    out = []
    if baseline is not None:
        out.append({"flow_date": baseline[0].isoformat(), "flow_type": "baseline",
                    "amount": baseline[1]})
    out.extend(extra)
    return out


def _flow(d, kind, amount):
    return {"flow_date": d.isoformat(), "flow_type": kind, "amount": amount}


SPY = {D_BASE: 100.0, D_END: 110.0}


NO_TRADES = pd.DataFrame({"id": [], "traded_at": []})   # a readable, empty journal


def _since(end_equity=1150.0, *, spy=SPY, flows=None, rows=None, today=TODAY, trades=NO_TRADES):
    flows = _flows() if flows is None else flows
    rows = rows if rows is not None else [_row(D_END, end_equity)]
    return sc.since_start(flows, _acct(*rows), trades, spy, today=today)


# --------------------------------------------------------------------------- since_start: ahead / behind / level

def test_ahead_of_spy():
    r = _since(1150.0)
    assert r["state"] == "ok"
    assert r["start_date"] == D_BASE and r["start_value"] == 1000.0
    assert r["end_date"] == D_END and r["end_value"] == 1150.0
    assert r["account_gain_usd"] == 150.0 and r["account_return_pct"] == 15.0
    assert r["spy_gain_usd"] == 100.0 and r["spy_return_pct"] == 10.0
    assert r["gap_usd"] == 50.0
    assert r["n_flows"] == 0 and r["net_flows"] == 0.0
    assert r["skipped_dates"] == []


def test_behind_spy():
    r = _since(1050.0)
    assert r["state"] == "ok" and r["gap_usd"] == -50.0
    assert r["account_gain_usd"] == 50.0


def test_exactly_level_with_spy():
    r = _since(1100.0)
    assert r["state"] == "ok" and r["gap_usd"] == 0.0
    card = sc.card_model(r, None, None)
    assert card["headline"] == "Level with SPY" and card["tone"] == "neutral"


def test_every_key_is_always_present():
    keys = {"state", "start_date", "start_value", "end_date", "end_value", "net_flows",
            "n_flows", "account_gain_usd", "account_return_pct", "spy_gain_usd",
            "spy_return_pct", "gap_usd", "skipped_dates", "skipped_reasons", "excluded"}
    for r in (_since(), sc.since_start(None, _acct(), NO_TRADES, SPY, today=TODAY),
              sc.since_start([], _acct(), NO_TRADES, SPY, today=TODAY)):
        assert set(r) == keys


def test_spy_keys_may_be_iso_strings():
    r = _since(1150.0, spy={D_BASE.isoformat(): 100.0, D_END.isoformat(): 110.0})
    assert r["state"] == "ok" and r["gap_usd"] == 50.0


# --------------------------------------------------------------------------- since_start: ledger states

def test_flows_none_is_offline_not_no_baseline():
    r = sc.since_start(None, _acct(_row(D_END, 1150.0)), NO_TRADES, SPY, today=TODAY)
    assert r["state"] == "offline"


def test_snapshots_none_is_offline():
    r = sc.since_start(_flows(), None, NO_TRADES, SPY, today=TODAY)
    assert r["state"] == "offline"


def test_unreadable_journal_is_offline_not_a_silent_fallback():
    # exclude_stale_rows would mask every journal-stamped row when trades_df is None
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, 1150.0, prov=7)]
    r = _since(rows=rows, trades=None)
    assert r["state"] == "offline" and r["end_date"] is None and r["gap_usd"] is None


def test_empty_ledger_is_no_baseline():
    r = sc.since_start([], _acct(_row(D_END, 1150.0)), NO_TRADES, SPY, today=TODAY)
    assert r["state"] == "no_baseline" and r["gap_usd"] is None


def test_deposit_only_ledger_is_no_baseline():
    r = _since(flows=[_flow(date(2026, 8, 3), "deposit", 500.0)])
    assert r["state"] == "no_baseline"


def test_two_baseline_rows_are_ambiguous():
    r = _since(flows=_flows({"flow_date": "2026-06-30", "flow_type": "baseline", "amount": 900.0}))
    assert r["state"] == "ambiguous" and r["gap_usd"] is None


def test_unreadable_baseline_row_is_offline_not_no_baseline():
    r = _since(flows=[{"flow_date": "2026-06-25", "flow_type": "baseline", "amount": None}])
    assert r["state"] == "offline"


def test_unreadable_flow_in_scope_is_offline():
    r = _since(flows=_flows({"flow_date": "not a date", "flow_type": "deposit", "amount": 5.0}))
    assert r["state"] == "offline"


# --------------------------------------------------------------------------- since_start: flow window

def _expected_gap(end_equity, units_spy_flows, end_close=110.0):
    units = 1000.0 / 100.0 + units_spy_flows
    shadow = units * end_close
    return round(end_equity - shadow, 2), shadow


def test_deposit_on_baseline_date_is_excluded():
    r = _since(1150.0, flows=_flows(_flow(D_BASE, "deposit", 500.0)))
    assert r["n_flows"] == 0 and r["net_flows"] == 0.0
    assert r["account_gain_usd"] == 150.0 and r["gap_usd"] == 50.0


def test_deposit_before_baseline_is_excluded():
    r = _since(1150.0, flows=_flows(_flow(date(2026, 6, 1), "deposit", 500.0)))
    assert r["n_flows"] == 0 and r["gap_usd"] == 50.0


def test_deposit_after_baseline_counts_on_both_sides():
    d = date(2026, 8, 3)
    spy = {D_BASE: 100.0, d: 105.0, D_END: 110.0}
    r = _since(1700.0, spy=spy, flows=_flows(_flow(d, "deposit", 500.0)))
    gap, shadow = _expected_gap(1700.0, 500.0 / 105.0)
    assert r["n_flows"] == 1 and r["net_flows"] == 500.0
    assert r["account_gain_usd"] == 200.0   # 1700 - 1000 - 500
    assert r["spy_gain_usd"] == round(shadow - 1000.0 - 500.0, 2)
    assert r["gap_usd"] == gap
    assert r["account_return_pct"] is not None and r["spy_return_pct"] is not None


def test_withdrawal_after_baseline_is_signed_negative():
    d = date(2026, 8, 3)
    spy = {D_BASE: 100.0, d: 105.0, D_END: 110.0}
    r = _since(800.0, spy=spy, flows=_flows(_flow(d, "withdrawal", 300.0)))
    gap, _ = _expected_gap(800.0, -300.0 / 105.0)
    assert r["net_flows"] == -300.0 and r["gap_usd"] == gap
    assert r["account_gain_usd"] == 100.0   # 800 - 1000 + 300


def test_flow_dated_after_the_end_row_is_out_of_window():
    r = _since(1150.0, today=date(2026, 11, 13),
               flows=_flows(_flow(date(2026, 11, 14), "deposit", 500.0)))
    assert r["n_flows"] == 0 and r["gap_usd"] == 50.0


def test_flow_on_a_weekend_looks_back_for_its_spy_close():
    d = date(2026, 8, 1)   # Saturday
    spy = {D_BASE: 100.0, date(2026, 7, 31): 104.0, D_END: 110.0}
    r = _since(1700.0, spy=spy, flows=_flows(_flow(d, "deposit", 500.0)))
    gap, _ = _expected_gap(1700.0, 500.0 / 104.0)
    assert r["state"] == "ok" and r["gap_usd"] == gap


# --------------------------------------------------------------------------- since_start: SPY

def test_weekend_baseline_date_looks_back_to_friday():
    sat = date(2026, 6, 27)
    spy = {date(2026, 6, 26): 100.0, D_END: 110.0}
    r = _since(1150.0, spy=spy, flows=_flows(baseline=(sat, 1000.0)))
    assert r["state"] == "ok" and r["start_date"] == sat
    assert r["spy_gain_usd"] == 100.0 and r["gap_usd"] == 50.0


def test_start_look_back_is_capped_at_five_days_like_benchmark_mirror():
    # A hole in SPY history longer than the 5-day look-back must read as
    # unavailable, never bridged with a weeks-old close (lead fix, 2026-11-15).
    sat = date(2026, 6, 27)
    spy = {date(2026, 6, 12): 100.0, D_END: 110.0}   # 15 days before the start
    r = _since(1150.0, spy=spy, flows=_flows(baseline=(sat, 1000.0)))
    assert r["state"] == "spy_unavailable"
    assert r["gap_usd"] is None and r["account_gain_usd"] == 150.0
    # exactly 5 days back is still allowed
    spy_ok = {date(2026, 6, 22): 100.0, D_END: 110.0}
    assert _since(1150.0, spy=spy_ok, flows=_flows(baseline=(sat, 1000.0)))["state"] == "ok"


def test_spy_missing_on_end_date_is_pending_and_never_reuses_an_older_close():
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0}
    r = _since(1150.0, spy=spy)
    assert r["state"] == "spy_pending"
    assert r["gap_usd"] is None and r["spy_gain_usd"] is None and r["spy_return_pct"] is None
    assert r["account_gain_usd"] == 150.0 and r["account_return_pct"] == 15.0
    assert r["end_date"] == D_END


@pytest.mark.parametrize("closes", [None, {}, {D_END: float("nan")}, {D_END: 0.0}])
def test_no_usable_spy_is_unavailable_with_account_figures(closes):
    r = _since(1150.0, spy=closes)
    assert r["state"] == "spy_unavailable"
    assert r["gap_usd"] is None and r["account_gain_usd"] == 150.0


def test_no_spy_close_on_or_before_the_start_is_unavailable():
    r = _since(1150.0, spy={date(2026, 7, 1): 100.0, D_END: 110.0})
    assert r["state"] == "spy_unavailable" and r["account_gain_usd"] == 150.0


# --------------------------------------------------------------------------- since_start: end row

def _late_trade_journal():
    """A readable journal with a trade (id 2) made before Nov 13's cash read but logged
    after that row was written (journal_max_trade_id 1) -> the row is masked as stale."""
    return pd.DataFrame([{"id": 2, "ticker": "AAA", "action": "BUY", "shares": 5.0,
                          "price": 10.0, "traded_at": "2026-11-13T20:00:00+00:00"}])


def test_masked_latest_row_falls_back_and_lists_the_skipped_date():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, 1150.0, prov=1)]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0, D_END: 110.0}
    r = _since(spy=spy, rows=rows, trades=_late_trade_journal())
    assert r["state"] == "ok"
    assert r["end_date"] == date(2026, 11, 12) and r["end_value"] == 1140.0
    assert r["skipped_dates"] == [D_END]
    assert [e["snapshot_date"] for e in r["excluded"]] == ["2026-11-13"]
    assert r["gap_usd"] == round(1140.0 - 10.0 * 108.0, 2)
    # the reason is the one the mask carries, not a guessed cash-read cause
    assert "holdings don't match the trade journal" in r["skipped_reasons"][D_END]
    info = sc.card_model(r, None, None)["info"]
    assert "holdings don't match the trade journal" in info
    assert "before the close" not in info


def test_row_failing_the_cash_check_gets_the_generic_reason_not_a_journal_claim():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, 1150.0, hour=11)]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0, D_END: 110.0}
    r = _since(spy=spy, rows=rows)
    assert r["skipped_reasons"][D_END].startswith("it has no usable end-of-day reading")
    info = sc.card_model(r, None, None)["info"]
    assert "match" not in info and "journal" not in info


def test_pre_close_cash_read_falls_back_to_the_earlier_row():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, 1150.0, hour=11)]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0, D_END: 110.0}
    r = _since(spy=spy, rows=rows)
    assert r["end_date"] == date(2026, 11, 12)
    assert r["skipped_dates"] == [D_END]


def test_nan_net_equity_is_treated_as_missing():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, float("nan"))]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0, D_END: 110.0}
    r = _since(spy=spy, rows=rows)
    assert r["end_date"] == date(2026, 11, 12) and r["end_value"] == 1140.0
    assert r["skipped_dates"] == [D_END]


def test_none_net_equity_is_treated_as_missing():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, None)]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0}
    r = _since(spy=spy, rows=rows)
    assert r["end_date"] == date(2026, 11, 12)


def test_weekend_today_uses_fridays_row_and_ignores_a_saturday_row():
    rows = [_row(D_END, 1150.0), _row(date(2026, 11, 14), 9999.0)]   # Sat row is not a session
    r = _since(rows=rows, today=date(2026, 11, 15))
    assert r["state"] == "ok" and r["end_date"] == D_END and r["end_value"] == 1150.0
    assert r["skipped_dates"] == []


def test_no_usable_row_after_the_baseline_is_no_account_row():
    r = _since(rows=[_row(D_BASE, 1000.0)])   # the baseline day itself is not an end row
    assert r["state"] == "no_account_row"
    assert r["start_value"] == 1000.0 and r["end_value"] is None and r["gap_usd"] is None


def test_row_dated_after_today_is_ignored():
    rows = [_row(date(2026, 11, 12), 1140.0), _row(D_END, 1150.0)]
    spy = {D_BASE: 100.0, date(2026, 11, 12): 108.0, D_END: 110.0}
    r = _since(spy=spy, rows=rows, today=date(2026, 11, 12))
    assert r["end_date"] == date(2026, 11, 12) and r["skipped_dates"] == []


def test_holiday_dated_row_is_never_the_end_row():
    thanksgiving = date(2026, 11, 26)
    rows = [_row(date(2026, 11, 25), 1140.0), _row(thanksgiving, 9999.0)]
    spy = {D_BASE: 100.0, date(2026, 11, 25): 108.0, thanksgiving: 109.0}
    r = _since(spy=spy, rows=rows, today=date(2026, 11, 27))
    assert r["end_date"] == date(2026, 11, 25) and r["end_value"] == 1140.0
    assert r["skipped_dates"] == []   # a holiday is not a session that was "left out"


def _row_cash_utc(d, net_equity, cash_utc):
    return {"snapshot_date": d.isoformat(), "net_equity": net_equity,
            "cash_as_of": cash_utc, "journal_max_trade_id": None}


def test_dst_change_day_cash_read_is_judged_in_et():
    # DST ended Sun Nov 1 2026, so Mon Nov 2 is on EST (UTC-5): 21:05Z is 16:05 ET (valid),
    # 20:30Z is 15:30 ET (before the close). The Oct 30 session was still EDT (UTC-4).
    nov2, oct30 = date(2026, 11, 2), date(2026, 10, 30)
    spy = {D_BASE: 100.0, oct30: 105.0, nov2: 106.0}
    prior = _row_cash_utc(oct30, 1100.0, "2026-10-30T20:05:00+00:00")   # 16:05 EDT
    ok = _since(spy=spy, today=nov2,
                rows=[prior, _row_cash_utc(nov2, 1120.0, "2026-11-02T21:05:00+00:00")])
    assert ok["end_date"] == nov2 and ok["end_value"] == 1120.0
    early = _since(spy=spy, today=nov2,
                   rows=[prior, _row_cash_utc(nov2, 1120.0, "2026-11-02T20:30:00+00:00")])
    assert early["end_date"] == oct30 and early["skipped_dates"] == [nov2]


def test_empty_snapshot_frame_is_no_account_row():
    r = sc.since_start(_flows(), pd.DataFrame(), NO_TRADES, SPY, today=TODAY)
    assert r["state"] == "no_account_row"


def test_does_not_mutate_its_inputs():
    flows = _flows()
    df = _acct(_row(D_END, 1150.0, prov=3))
    before = df.copy()
    sc.since_start(flows, df, NO_TRADES, SPY, today=TODAY)
    pd.testing.assert_frame_equal(df, before)
    assert flows == _flows()


# --------------------------------------------------------------------------- leverage

def _recon(borrowed=310.0, own=100.0, charged=228.0, *, hs_unsplit=0.0, bought=0.0, rt=0.0,
           avg=2.9, days_split=20, last="2026-10", gaps=(), earned=0.0,
           unpriced=(), inconsistent=(), early=False, status="ok"):
    return {
        "status": status,
        "unpriced": list(unpriced), "journal_inconsistent": list(inconsistent),
        "d1_short_of_period_end": early,
        "income": {"interest_charged": charged, "interest_earned": earned,
                   "last_interest_charged_month": last,
                   "interest_coverage_gap_months": list(gaps)},
        "pos_pnl": {"held_through": borrowed + own + hs_unsplit, "sold_leg": 0.0,
                    "bought_leg": bought, "round_trip": rt},
        "leverage_split": {"borrowed": borrowed, "own": own, "days_split": days_split,
                           "avg_leverage": avg},
    }


def _core(recon):
    return {"recon": recon, "leverage": None, "account_return": None,
            "account_rows_excluded": []}


def _inputs(first_valid=date(2026, 9, 10)):
    return {"account_snapshots_df": _acct(_row(first_valid, 5000.0))}


@pytest.fixture
def engine(monkeypatch):
    """Patch compute_period_core; returns the list of (start, end) calls. The recon it
    returns is set via engine.recon."""
    class _E:
        recon = _recon()
        calls = []

        def __call__(self, inputs, start, end, *, spy_prices_by_date, reference_leverage):
            self.calls.append((start, end))
            return _core(self.recon)

    e = _E()
    e.calls = []
    monkeypatch.setattr(period_inputs, "compute_period_core", e)
    return e


def _lev(engine, *, today=date(2026, 11, 15), inputs=None):
    return sc.leverage_verdict(inputs if inputs is not None else _inputs(), None,
                               today=today, reference_leverage=3.0)


def test_leverage_inputs_none_is_offline():
    assert sc.leverage_verdict(None, None, today=TODAY, reference_leverage=3.0)["state"] == "offline"


def test_leverage_account_frame_none_is_offline():
    r = sc.leverage_verdict({"account_snapshots_df": None}, None, today=TODAY,
                            reference_leverage=3.0)
    assert r["state"] == "offline"


def test_leverage_no_valid_account_row_is_no_complete_month(engine):
    r = _lev(engine, inputs={"account_snapshots_df": _acct(_row(date(2026, 9, 10), 5000.0, hour=11))})
    assert r["state"] == "no_complete_month" and engine.calls == []


def test_leverage_no_complete_month_yet(engine):
    # first measurable day is Sep 11 -> first month is October; Oct 31 hasn't ended before today
    r = _lev(engine, today=date(2026, 10, 31))
    assert r["state"] == "no_complete_month" and engine.calls == []


def test_leverage_window_is_the_calendar_months_that_ended_before_today(engine):
    r = _lev(engine, today=date(2026, 11, 30))
    assert engine.calls == [(date(2026, 10, 1), date(2026, 10, 31))]
    assert r["period_start"] == date(2026, 10, 1) and r["period_end"] == date(2026, 10, 31)
    engine.calls.clear()
    engine.recon = _recon(last="2026-11")
    r = _lev(engine, today=date(2026, 12, 1))
    assert engine.calls == [(date(2026, 10, 1), date(2026, 11, 30))]
    assert r["state"] == "ok"


def test_leverage_trims_to_the_last_month_with_posted_interest(engine):
    engine.recon = _recon(last="2026-10")
    r = _lev(engine, today=date(2026, 12, 5))   # Nov is complete but its charge hasn't posted
    assert engine.calls == [(date(2026, 10, 1), date(2026, 11, 30)),
                            (date(2026, 10, 1), date(2026, 10, 31))]
    assert r["period_end"] == date(2026, 10, 31) and r["state"] == "ok"


def test_leverage_last_interest_before_the_first_month_is_no_complete_month(engine):
    engine.recon = _recon(last="2026-09")
    assert _lev(engine)["state"] == "no_complete_month"


def test_leverage_never_charged_is_no_complete_month(engine):
    engine.recon = _recon(last=None)
    assert _lev(engine)["state"] == "no_complete_month"


def test_leverage_missing_income_is_offline(engine):
    engine.recon = {"income": None, "pos_pnl": None, "leverage_split": None}
    assert _lev(engine)["state"] == "offline"
    engine.recon = None
    assert _lev(engine)["state"] == "offline"


def test_leverage_engine_exception_is_offline(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(period_inputs, "compute_period_core", boom)
    assert sc.leverage_verdict(_inputs(), None, today=date(2026, 11, 15),
                               reference_leverage=3.0)["state"] == "offline"


def test_leverage_interest_gap_month_returns_figures_without_a_verdict(engine):
    engine.recon = _recon(gaps=["2026-10"])
    r = _lev(engine)
    assert r["state"] == "interest_gap" and r["gap_months"] == ["2026-10"]
    assert r["borrowed_usd"] == 310.0 and r["interest_usd"] == -228.0
    assert r["net_usd"] is None and r["verdict"] is None


def test_leverage_no_split_days_is_unknown_split(engine):
    engine.recon = _recon(days_split=0)
    r = _lev(engine)
    assert r["state"] == "unknown_split" and r["borrowed_usd"] is None
    assert r["net_usd"] is None and r["verdict"] is None


def test_leverage_missing_position_pnl_is_unknown_split(engine):
    rc = _recon()
    rc["pos_pnl"] = None
    engine.recon = rc
    assert _lev(engine)["state"] == "unknown_split"


def test_leverage_paid(engine):
    engine.recon = _recon(borrowed=310.0, charged=228.0, hs_unsplit=50.0)
    r = _lev(engine)
    assert r["state"] == "ok" and r["verdict"] == "paid"
    assert r["net_usd"] == pytest.approx(82.0) and r["interest_usd"] == -228.0
    assert r["unsplit_usd"] == pytest.approx(50.0) and r["avg_leverage"] == 2.9
    assert r["own_usd"] == 100.0


def test_leverage_unsplit_sums_the_three_legs(engine):
    engine.recon = _recon(hs_unsplit=10.0, bought=20.0, rt=-5.0)
    assert _lev(engine)["unsplit_usd"] == pytest.approx(25.0)


def test_leverage_cost(engine):
    engine.recon = _recon(borrowed=100.0, charged=228.0, hs_unsplit=50.0)
    r = _lev(engine)
    assert r["verdict"] == "cost" and r["net_usd"] == pytest.approx(-128.0)


def test_leverage_spreading_the_unsplit_can_flip_the_sign_to_too_close(engine):
    # net +82, unsplit -140 at avg 2.9x (borrowed share ~0.655) -> 82 - 91.7 < 0
    engine.recon = _recon(borrowed=310.0, charged=228.0, bought=-140.0)
    r = _lev(engine)
    assert r["net_usd"] == pytest.approx(82.0) and r["verdict"] == "too_close"
    # and the mirror: a cost that spreading would turn positive
    engine.recon = _recon(borrowed=100.0, charged=228.0, bought=300.0)
    assert _lev(engine)["verdict"] == "too_close"


@pytest.mark.parametrize("avg", [None, 1.0, 0.8])
def test_leverage_no_borrowed_share_is_too_close(engine, avg):
    engine.recon = _recon(avg=avg)
    r = _lev(engine)
    assert r["state"] == "ok" and r["verdict"] == "too_close"


def test_leverage_first_month_is_the_first_trading_day_case(engine):
    # earliest valid Fri Feb 27 -> first measurable Mon Mar 2 = March's first session
    engine.recon = _recon(last="2026-03")
    r = _lev(engine, today=date(2026, 4, 15), inputs=_inputs(date(2026, 2, 27)))
    assert engine.calls[0][0] == date(2026, 3, 1) and r["state"] == "ok"


def test_leverage_first_month_skips_to_next_when_start_is_mid_month(engine):
    # earliest valid Mon Mar 2 -> first measurable Tue Mar 3, not the 1st / first session
    engine.recon = _recon(last="2026-04")
    _lev(engine, today=date(2026, 5, 15), inputs=_inputs(date(2026, 3, 2)))
    assert engine.calls[0][0] == date(2026, 4, 1)


def test_leverage_first_month_when_start_is_the_first_of_the_month(engine):
    # earliest valid Wed Sep 30 -> first measurable Thu Oct 1
    _lev(engine, inputs=_inputs(date(2026, 9, 30)))
    assert engine.calls[0][0] == date(2026, 10, 1)


def test_leverage_window_across_a_year_end(engine):
    engine.recon = _recon(last="2026-12")
    r = _lev(engine, today=date(2027, 1, 10))
    assert engine.calls == [(date(2026, 10, 1), date(2026, 12, 31))]
    assert r["state"] == "ok"


def test_leverage_unpriced_position_days_make_the_figures_incomplete(engine):
    engine.recon = _recon(unpriced=[("AAA", date(2026, 10, 15))])
    r = _lev(engine)
    assert r["state"] == "incomplete" and r["verdict"] is None
    assert r["n_unpriced"] == 1 and r["n_journal_inconsistent"] == 0 and r["ended_early"] is False
    assert r["borrowed_usd"] == 310.0 and r["net_usd"] == pytest.approx(82.0)
    card = sc.card_model(_since_ok(), r, None)["lev_row"]
    assert card["chip_label"] is None and card["chip_kind"] is None
    assert card["text"].startswith("Made +$310 on borrowed money")
    assert "1 position-day couldn't be priced or matched to your journal" in card["caption"]
    assert "no verdict" in card["caption"] and "Reports" in card["caption"]


def test_leverage_journal_inconsistent_position_days_make_the_figures_incomplete(engine):
    engine.recon = _recon(inconsistent=[("AAA", date(2026, 10, 9)), ("AAA", date(2026, 10, 12))])
    r = _lev(engine)
    assert r["state"] == "incomplete" and r["verdict"] is None
    assert r["n_unpriced"] == 0 and r["n_journal_inconsistent"] == 2
    cap = sc.card_model(_since_ok(), r, None)["lev_row"]["caption"]
    assert "2 position-days couldn't be priced or matched to your journal" in cap


def test_leverage_window_that_ended_early_is_incomplete(engine):
    engine.recon = _recon(early=True)
    r = _lev(engine)
    assert r["state"] == "incomplete" and r["verdict"] is None and r["ended_early"] is True
    row = sc.card_model(_since_ok(), r, None)["lev_row"]
    assert row["chip_label"] is None
    assert "The last complete record is before the month's end" in row["caption"]
    assert "position-day" not in row["caption"]


def test_leverage_recon_without_the_completeness_fields_is_incomplete_not_ok(engine):
    rc = _recon()
    for k in ("unpriced", "journal_inconsistent", "d1_short_of_period_end"):
        del rc[k]
    engine.recon = rc
    r = _lev(engine)
    assert r["state"] == "incomplete" and r["verdict"] is None
    assert r["n_unpriced"] is None and r["ended_early"] is None


def test_leverage_interest_gap_outranks_incomplete(engine):
    engine.recon = _recon(gaps=["2026-10"], unpriced=[("AAA", date(2026, 10, 15))])
    r = _lev(engine)
    assert r["state"] == "interest_gap" and r["n_unpriced"] == 1


def test_leverage_net_that_rounds_to_zero_dollars_is_too_close(engine):
    engine.recon = _recon(borrowed=100.3, charged=100.0)   # net +$0.30 would otherwise be "paid"
    r = _lev(engine)
    assert r["state"] == "ok" and r["verdict"] == "too_close"
    row = sc.card_model(_since_ok(), r, None)["lev_row"]
    assert "The net is too close to zero to call." in row["caption"]
    assert "could flip" not in row["caption"]


def test_card_and_email_for_an_incomplete_leverage_figure():
    lev = _lev_ok(state="incomplete", verdict=None, n_unpriced=1, n_journal_inconsistent=0,
                  ended_early=False)
    assert sc.card_model(_since_ok(), lev, None)["lev_row"]["chip_label"] is None
    lines = sc.email_lines(_since_ok(), lev, _tax_ok())
    assert lines[2] == "Borrowed money, October 2026: net +$82 after interest (incomplete, no verdict)"


# leverage_window -------------------------------------------------------------

def test_leverage_window_is_the_complete_months_before_the_interest_trim():
    acct = _inputs()["account_snapshots_df"]   # earliest valid row Thu Sep 10
    assert sc.leverage_window(acct, date(2026, 11, 15)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert sc.leverage_window(acct, date(2026, 12, 1)) == (date(2026, 10, 1), date(2026, 11, 30))
    assert sc.leverage_window(acct, date(2027, 1, 10)) == (date(2026, 10, 1), date(2026, 12, 31))
    assert sc.leverage_window(acct, date(2026, 10, 31)) is None   # October hasn't ended yet


def test_leverage_window_none_without_a_valid_account_row():
    assert sc.leverage_window(_acct(_row(date(2026, 9, 10), 5000.0, hour=11)), date(2026, 11, 15)) is None
    assert sc.leverage_window(pd.DataFrame(), date(2026, 11, 15)) is None


def test_leverage_verdict_computes_over_exactly_the_leverage_window(engine):
    inputs = _inputs(date(2026, 2, 27))
    win = sc.leverage_window(inputs["account_snapshots_df"], date(2026, 12, 5))
    engine.recon = _recon(last="2026-12")
    sc.leverage_verdict(inputs, None, today=date(2026, 12, 5), reference_leverage=3.0)
    assert engine.calls[0] == win == (date(2026, 3, 1), date(2026, 11, 30))


# real engine -----------------------------------------------------------------

def _sessions(a, b):
    out, d = [], a
    while d <= b:
        if sc.performance_review.is_trading_day(d):
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


def _engine_inputs(missing_close=()):
    """One ticker (10 AAA bought Sep 29), a levered account row for every session from Sep 30
    to Oct 30, one margin-interest charge on Oct 24; `missing_close` days have no price."""
    days = _sessions(date(2026, 9, 30), date(2026, 10, 30))
    acct = pd.DataFrame([{
        "snapshot_date": d.isoformat(), "net_equity": 900.0, "gross_book": 1000.0,
        "cash_balance": -100.0, "leverage": 1000.0 / 900.0, "cash_as_of": _cash_ts(d),
        "journal_max_trade_id": None} for d in days])
    trades = pd.DataFrame([{"id": 1, "ticker": "AAA", "action": "BUY", "shares": 10.0,
                            "price": 100.0, "traded_at": "2026-09-29T15:00:00+00:00"}])
    daily = pd.DataFrame([{"snapshot_date": d.isoformat(), "ticker": "AAA",
                           "close_price": 100.0 + i, "shares": 10}
                          for i, d in enumerate(days) if d not in missing_close])
    income = [{"event_type": "interest", "amount": -50.0, "event_date": "2026-10-24"}]
    return {"trades_df": trades, "account_snapshots_df": acct, "daily_snapshots_df": daily,
            "flows": [], "income_events": income}


def test_real_engine_clean_month_gets_a_verdict():
    r = sc.leverage_verdict(_engine_inputs(), None, today=date(2026, 11, 15), reference_leverage=3.0)
    assert r["state"] == "ok" and r["verdict"] in ("paid", "cost", "too_close")
    assert r["n_unpriced"] == 0 and r["n_journal_inconsistent"] == 0 and r["ended_early"] is False
    assert r["period_start"] == date(2026, 10, 1) and r["period_end"] == date(2026, 10, 31)


def test_real_engine_missing_close_reports_incomplete_not_a_silent_verdict():
    r = sc.leverage_verdict(_engine_inputs(missing_close=(date(2026, 10, 15),)), None,
                            today=date(2026, 11, 15), reference_leverage=3.0)
    assert r["state"] == "incomplete" and r["verdict"] is None
    assert r["n_unpriced"] >= 1 and r["borrowed_usd"] is not None


def test_real_engine_account_rows_ending_early_report_incomplete():
    inputs = _engine_inputs()
    acct = inputs["account_snapshots_df"]
    inputs["account_snapshots_df"] = acct[acct["snapshot_date"] <= "2026-10-02"]   # nothing after Oct 2
    r = sc.leverage_verdict(inputs, None, today=date(2026, 11, 15), reference_leverage=3.0)
    assert r["state"] == "incomplete" and r["ended_early"] is True and r["verdict"] is None


# --------------------------------------------------------------------------- tax

def _trades(*rows):
    cols = ["id", "ticker", "action", "shares", "price", "traded_at", "realized_pnl", "cost_basis"]
    out = []
    for i, r in enumerate(rows, 1):
        base = dict(zip(cols, [i, None, None, 10, None, None, None, None]))
        base.update(r)
        out.append(base)
    return pd.DataFrame(out, columns=cols)


def _buy(t, ts, px, sh=10):
    return {"ticker": t, "action": "BUY", "shares": sh, "price": px, "traded_at": ts}


def _sell(t, ts, px, pnl, sh=10, **kw):
    return {"ticker": t, "action": "SELL", "shares": sh, "price": px, "traded_at": ts,
            "realized_pnl": pnl, **kw}


LT_BUY = "2025-01-10T15:00:00+00:00"
ST_BUY = "2026-01-05T15:00:00+00:00"
SELL_1 = "2026-03-10T15:00:00+00:00"
SELL_2 = "2026-02-05T15:00:00+00:00"


def _tax(df, year=2026, today=date(2026, 11, 15)):
    return sc.tax_triggered(df, tax_year=year, st_rate=ST_RATE, lt_rate=LT_RATE, today=today)


def test_tax_short_gain_with_long_loss_is_netted_at_the_short_rate():
    df = _trades(_buy("AAA", LT_BUY, 100), _sell("AAA", SELL_1, 90, -100.0),
                 _buy("BBB", ST_BUY, 100), _sell("BBB", SELL_2, 150, 500.0))
    r = _tax(df)
    assert r["state"] == "ok"
    assert r["st_net"] == 500.0 and r["lt_net"] == -100.0
    assert r["tax_usd"] == pytest.approx(400.0 * ST_RATE)
    assert r["net_loss"] is False and r["reconciles"] is True
    assert r["n_unknown_counted"] == 0 and r["n_unknown_left_out"] == 0


def test_tax_long_gain_with_short_loss_is_netted_at_the_long_rate():
    df = _trades(_buy("CCC", LT_BUY, 100), _sell("CCC", SELL_1, 150, 500.0),
                 _buy("DDD", ST_BUY, 100), _sell("DDD", SELL_2, 80, -200.0))
    r = _tax(df)
    assert r["st_net"] == -200.0 and r["lt_net"] == 500.0
    assert r["tax_usd"] == pytest.approx(300.0 * LT_RATE)
    assert r["net_loss"] is False


def test_tax_both_gains_use_their_own_rates():
    df = _trades(_buy("CCC", LT_BUY, 100), _sell("CCC", SELL_1, 150, 500.0),
                 _buy("BBB", ST_BUY, 100), _sell("BBB", SELL_2, 150, 500.0))
    assert _tax(df)["tax_usd"] == pytest.approx(500.0 * ST_RATE + 500.0 * LT_RATE)


def test_tax_both_negative_is_zero_and_a_net_loss():
    df = _trades(_buy("AAA", LT_BUY, 100), _sell("AAA", SELL_1, 90, -100.0),
                 _buy("DDD", ST_BUY, 100), _sell("DDD", SELL_2, 80, -200.0))
    r = _tax(df)
    assert r["tax_usd"] == 0.0 and r["net_loss"] is True


def test_tax_loss_larger_than_the_gain_is_a_net_loss_with_no_tax():
    df = _trades(_buy("BBB", ST_BUY, 100), _sell("BBB", SELL_2, 110, 100.0),
                 _buy("AAA", LT_BUY, 100), _sell("AAA", SELL_1, 70, -300.0))
    r = _tax(df)
    assert r["tax_usd"] == 0.0 and r["net_loss"] is True


def test_tax_unknown_term_is_counted_short_term_and_disclosed():
    df = _trades(_sell("EEE", SELL_2, 120, 200.0, cost_basis=100.0))   # no BUY history
    r = _tax(df)
    assert r["n_unknown_counted"] == 1 and r["n_unknown_left_out"] == 0
    assert r["unknown_gain"] == 200.0
    assert r["st_net"] == 200.0 and r["tax_usd"] == pytest.approx(200.0 * ST_RATE)


def test_tax_unknown_term_row_that_cannot_be_valued_is_left_out_and_said_so():
    # no BUY history and neither cost_basis nor realized_pnl -> no gain at all
    df = _trades(_sell("EEE", SELL_2, 120, None),
                 _buy("BBB", ST_BUY, 100), _sell("BBB", SELL_2, 150, 500.0))
    r = _tax(df)
    assert r["n_unknown_left_out"] == 1 and r["n_unknown_counted"] == 0
    assert r["st_net"] == 500.0   # the unvalued row added nothing
    cap = sc.card_model(_since_ok(), None, r)["tax_row"]["caption"]
    assert "(0 this year)" in cap
    assert "1 sale couldn't be valued (its cost is unknown), so it is left out of this figure." in cap


def test_tax_counts_wash_sale_violations():
    df = _trades(_buy("DDD", ST_BUY, 100), _sell("DDD", SELL_2, 80, -200.0),
                 _buy("DDD", "2026-02-20T15:00:00+00:00", 82))   # rebought inside 30 days
    assert _tax(df)["n_wash"] == 1
    clean = _trades(_buy("DDD", ST_BUY, 100), _sell("DDD", SELL_2, 80, -200.0))
    assert _tax(clean, today=date(2026, 11, 15))["n_wash"] == 0


def test_tax_year_boundary_is_decided_on_the_et_date():
    buy = _buy("FFF", "2026-06-01T15:00:00+00:00", 100)
    dec31_late_et = _sell("FFF", "2027-01-01T04:30:00+00:00", 150, 500.0)   # Dec 31 23:30 ET
    r = _tax(_trades(buy, dec31_late_et), year=2026, today=date(2027, 3, 1))
    assert r["st_net"] == 500.0
    jan2 = _sell("FFF", "2027-01-02T16:00:00+00:00", 150, 500.0)
    r = _tax(_trades(buy, jan2), year=2026, today=date(2027, 3, 1))
    assert r["st_net"] == 0.0 and r["tax_usd"] == 0.0


def test_tax_ledger_none_is_offline():
    r = _tax(None)
    assert r["state"] == "offline" and r["tax_usd"] is None and r["net_loss"] is None


def test_tax_empty_year_is_ok_and_zero():
    r = _tax(_trades(_buy("AAA", ST_BUY, 100)))
    assert r["state"] == "ok" and r["tax_usd"] == 0.0 and r["net_loss"] is False


def test_tax_echoes_the_rates():
    r = _tax(_trades(_buy("AAA", ST_BUY, 100)))
    assert r["st_rate"] == ST_RATE and r["lt_rate"] == LT_RATE


# --------------------------------------------------------------------------- card_model / email_lines

def _since_ok(**kw):
    d = {"state": "ok", "start_date": D_BASE, "start_value": 7152.99, "end_date": D_END,
         "end_value": 7650.0, "net_flows": 0.0, "n_flows": 0, "account_gain_usd": 497.0,
         "account_return_pct": 6.9, "spy_gain_usd": 447.0, "spy_return_pct": 6.2,
         "gap_usd": 50.0, "skipped_dates": [], "excluded": []}
    d.update(kw)
    return d


def _lev_ok(**kw):
    d = {"state": "ok", "period_start": date(2026, 10, 1), "period_end": date(2026, 10, 31),
         "borrowed_usd": 310.0, "own_usd": 100.0, "interest_usd": -228.0, "net_usd": 82.0,
         "unsplit_usd": 140.0, "avg_leverage": 2.9, "verdict": "too_close", "gap_months": []}
    d.update(kw)
    return d


def _tax_ok(**kw):
    d = {"state": "ok", "tax_year": 2026, "st_net": 1500.0, "lt_net": 0.0, "unknown_gain": 0.0,
         "n_unknown_counted": 0, "n_unknown_left_out": 0, "tax_usd": 555.0, "net_loss": False, "n_wash": 2, "reconciles": True,
         "st_rate": 0.37, "lt_rate": 0.20}
    d.update(kw)
    return d


def _strings(card):
    out = []
    for v in card.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            out.extend(x for x in v.values() if isinstance(x, str))
    return out


def test_card_ahead_matches_the_mockup_wording():
    c = sc.card_model(_since_ok(), _lev_ok(), _tax_ok())
    assert c["title"] == "\U0001f9ed Is this account earning extra?"
    assert c["asof"] == ("As of Fri Nov 13 close · starting point $7,152.99 on Jun 25, "
                         "when tracking began")
    assert c["headline"] == "$50 ahead of SPY" and c["tone"] == "pos"
    assert c["subline"] == ("Account +$497 (+6.9%) · the same money in SPY +$447 (+6.2%)")
    assert c["warning"] is None and c["info"] is None


def test_card_behind_uses_the_true_minus_sign():
    c = sc.card_model(_since_ok(account_gain_usd=-173.0, account_return_pct=-2.4, gap_usd=-620.0),
                      None, None)
    assert c["headline"] == "$620 behind SPY" and c["tone"] == "neg"
    assert c["subline"] == ("Account −$173 (−2.4%) · the same money in SPY "
                            "+$447 (+6.2%)")


def test_card_has_no_hyphen_minus_money_and_cents_only_on_the_starting_point():
    c = sc.card_model(_since_ok(account_gain_usd=-173.0, account_return_pct=-2.4, gap_usd=-620.0),
                      _lev_ok(net_usd=-82.0, interest_usd=-228.0), _tax_ok())
    for s in _strings(c):
        assert "-$" not in s and "-0." not in s
        for m in re.findall(r"\$[\d,]+\.\d+", s):
            assert m == "$7,152.99", s


def test_card_pending_spy_shows_the_account_and_a_warning_but_no_gap():
    c = sc.card_model(_since_ok(state="spy_pending", spy_gain_usd=None, spy_return_pct=None,
                                gap_usd=None), None, None)
    assert c["headline"] is None and c["tone"] == "neutral"
    assert c["subline"] == "Account +$497 (+6.9%)"
    assert c["warning"] == ("⚠️ SPY's Nov 13 close isn't available yet, so the comparison "
                            "isn't shown. An older close is never reused.")


def test_card_spy_unavailable_warns_and_keeps_the_account_figure():
    c = sc.card_model(_since_ok(state="spy_unavailable", spy_gain_usd=None, spy_return_pct=None,
                                gap_usd=None), None, None)
    assert c["headline"] is None and c["subline"] == "Account +$497 (+6.9%)"
    assert "SPY" in c["warning"]


def test_card_unreadable_no_baseline_and_ambiguous_texts():
    off = sc.card_model(sc.since_start(None, None, None, None, today=TODAY), None, None)
    assert off["warning"] == ("⚠️ Couldn't read your deposits ledger or account records "
                              "just now, so nothing is shown. This is not the same as zero; try "
                              "again shortly.")
    assert off["headline"] is None and off["subline"] is None and off["asof"] is None
    nb = sc.card_model(_since(flows=[]), None, None)
    assert nb["warning"] == "⚠️ Starting point not set — set a baseline on \U0001f4b0 Account."
    amb = sc.card_model(_since(flows=_flows({"flow_date": "2026-06-30", "flow_type": "baseline",
                                              "amount": 5.0})), None, None)
    assert amb["warning"] == ("⚠️ More than one starting point is recorded on "
                              "\U0001f4b0 Account; fix the deposits ledger before this can be computed.")


def test_card_no_account_row_keeps_the_starting_point_line():
    c = sc.card_model(_since(rows=[_row(D_BASE, 1000.0)]), None, None)
    assert c["headline"] is None and c["warning"] is not None
    assert c["asof"] == "Starting point $1,000.00 on Jun 25, when tracking began"


def test_card_info_names_the_left_out_dates_and_the_date_it_uses():
    one = sc.card_model(_since_ok(end_date=date(2026, 11, 12), skipped_dates=[D_END]),
                        None, None)
    assert one["info"] == ("ℹ️ Fri Nov 13's account record was left out (it has no usable "
                           "end-of-day reading: no net equity, or its cash wasn't read after that "
                           "day's close), so this uses Thu Nov 12.")
    carried = sc.card_model(_since_ok(end_date=date(2026, 11, 12), skipped_dates=[D_END],
                                      skipped_reasons={D_END: "its holdings changed"}),
                            None, None)["info"]
    assert carried == ("ℹ️ Fri Nov 13's account record was left out (its holdings changed), "
                       "so this uses Thu Nov 12.")
    assert one["asof"].startswith("As of Thu Nov 12 close")
    many = sc.card_model(_since_ok(end_date=date(2026, 11, 11),
                                   skipped_dates=[date(2026, 11, 12), D_END]),
                         None, None)
    assert many["info"].startswith("ℹ️ These account records were left out: Thu Nov 12 (")
    assert "; Fri Nov 13 (" in many["info"]
    assert many["info"].endswith("This uses Wed Nov 11.")


def test_card_start_date_shows_its_year_when_it_differs_from_the_end_year():
    c = sc.card_model(_since_ok(end_date=date(2027, 1, 8)), None, None)
    assert "on Jun 25, 2026," in c["asof"]


def test_lev_row_normal_matches_the_mockup():
    r = sc.card_model(_since_ok(), _lev_ok(), None)["lev_row"]
    assert r["heading"] == "Borrowed money, October 2026"
    assert r["text"] == ("Made +$310 on borrowed money (avg 2.9× leverage) · margin "
                         "interest posted −$228 → net +$82")
    assert r["chip_label"] == "Too close to call" and r["chip_kind"] == "close"
    assert "$140 of moves couldn't be split between borrowed and own money" in r["caption"]
    assert "October's own mix" in r["caption"] and "so no verdict" in r["caption"]
    assert "around the 24th" in r["caption"] and "November joins after its charge posts" in r["caption"]


@pytest.mark.parametrize("verdict,label,kind", [("paid", "Leverage paid", "pay"),
                                                ("cost", "Leverage cost you", "nopay"),
                                                ("too_close", "Too close to call", "close")])
def test_lev_row_chip(verdict, label, kind):
    r = sc.card_model(_since_ok(), _lev_ok(verdict=verdict), None)["lev_row"]
    assert (r["chip_label"], r["chip_kind"]) == (label, kind)


def test_lev_row_month_labels():
    two = _lev_ok(period_end=date(2026, 11, 30))
    assert sc.card_model(_since_ok(), two, None)["lev_row"]["heading"] == \
        "Borrowed money, October–November 2026"
    yr = _lev_ok(period_end=date(2027, 1, 31))
    assert sc.card_model(_since_ok(), yr, None)["lev_row"]["heading"] == \
        "Borrowed money, October 2026–January 2027"


def test_lev_row_no_complete_month_and_other_states():
    r = sc.card_model(_since_ok(), {"state": "no_complete_month"}, None)["lev_row"]
    assert r["heading"] == "Borrowed money" and r["text"] is None and r["chip_label"] is None
    assert r["caption"] == ("Needs a complete month with interest posted. The first one in this "
                            "window is shown once its charge posts.")
    gap = sc.card_model(_since_ok(), _lev_ok(state="interest_gap", net_usd=None, verdict=None,
                                             gap_months=["2026-10"]), None)["lev_row"]
    assert "Margin interest isn't recorded for October 2026" in gap["caption"]
    assert gap["chip_label"] is None and "net +" not in (gap["text"] or "")
    unk = sc.card_model(_since_ok(), _lev_ok(state="unknown_split", borrowed_usd=None, net_usd=None,
                                             verdict=None), None)["lev_row"]
    assert unk["text"] is None and "couldn't be split" in unk["caption"]
    off = sc.card_model(_since_ok(), {"state": "offline"}, None)["lev_row"]
    assert off["caption"] and off["chip_label"] is None


def test_tax_row_normal_matches_the_mockup():
    r = sc.card_model(_since_ok(), None, _tax_ok())["tax_row"]
    assert r["heading"] == "Tax already triggered by 2026 sales (estimate)"
    assert r["text"] == "About $555: net short-term gain of $1,500 at 37%"
    assert "An estimate at the top federal bracket, not your actual tax." in r["caption"]
    assert "(0 this year)" in r["caption"]
    assert "2 wash sales are not adjusted for." in r["caption"]
    assert "Ledger reconciles with your journal ✓." in r["caption"]
    assert "The same money left in SPY would owe nothing yet." in r["caption"]


def test_tax_row_rate_text_comes_from_the_rates_never_a_literal():
    c = sc.card_model(_since_ok(), _lev_ok(),
                      _tax_ok(tax_usd=450.0, st_rate=0.30, lt_rate=0.15))
    assert c["tax_row"]["text"].endswith("at 30%")
    assert all("37%" not in s for s in _strings(c))


def test_tax_row_prints_the_rate_the_tax_dict_carries():
    r = sc.card_model(_since_ok(), None, _tax_ok(st_rate=0.25))["tax_row"]["text"]
    assert r.endswith("at 25%")
    r = sc.card_model(_since_ok(), None, _tax_ok(st_rate=None))["tax_row"]["text"]
    assert r == "About $555: net short-term gain of $1,500"   # no rate known -> none printed


def test_tax_row_variants():
    both = sc.card_model(_since_ok(), None, _tax_ok(st_net=1000.0, lt_net=500.0, tax_usd=445.0))["tax_row"]["text"]
    assert both == ("About $445: short-term gain of $1,000 at 37% plus long-term gain of $500 at 20%")
    st_lt = sc.card_model(_since_ok(), None, _tax_ok(st_net=500.0, lt_net=-100.0, tax_usd=148.0))["tax_row"]["text"]
    assert st_lt == ("About $148: short-term gain of $500 less a long-term loss of $100, "
                     "net $400 at 37%")
    lt_st = sc.card_model(_since_ok(), None, _tax_ok(st_net=-200.0, lt_net=500.0, tax_usd=60.0))["tax_row"]["text"]
    assert lt_st == ("About $60: long-term gain of $500 less a short-term loss of $200, "
                     "net $300 at 20%")
    loss = sc.card_model(_since_ok(), None, _tax_ok(st_net=-200.0, lt_net=-100.0, tax_usd=0.0,
                                                    net_loss=True, n_wash=0))["tax_row"]
    assert loss["text"] == "About $0: net loss of $300 so far"
    assert "wash sale" not in loss["caption"]
    one = sc.card_model(_since_ok(), None, _tax_ok(n_wash=1))["tax_row"]["caption"]
    assert "1 wash sale is not adjusted for." in one
    bad = sc.card_model(_since_ok(), None, _tax_ok(reconciles=False))["tax_row"]["caption"]
    assert "does not reconcile" in bad and "reconciles with your journal" not in bad


def test_tax_row_offline():
    r = sc.card_model(_since_ok(), None, {"state": "offline", "tax_year": 2026})["tax_row"]
    assert r["heading"] == "Tax already triggered by 2026 sales (estimate)"
    assert "Couldn't read" in r["text"] and r["caption"] is None


def test_card_model_tolerates_none_for_every_part():
    c = sc.card_model(None, None, None)
    assert c["warning"] is not None and c["lev_row"]["heading"] and c["tax_row"]["text"]


def test_email_lines_full_block_is_four_lines():
    lines = sc.email_lines(_since_ok(), _lev_ok(), _tax_ok())
    assert lines == [
        "\U0001f9ed Is this account earning extra? as of Fri Nov 13 close · starting point "
        "$7,152.99 on Jun 25",
        "$50 ahead of SPY: account +$497 (+6.9%) · the same money in SPY +$447 (+6.2%)",
        "Borrowed money, October 2026: net +$82 after interest (too close to call)",
        "Tax already triggered by 2026 sales: about $555 (estimate; SPY would owe nothing yet)",
    ]


def test_email_lines_pending_spy_swaps_the_gap_for_a_warning():
    s = _since_ok(state="spy_pending", spy_gain_usd=None, spy_return_pct=None, gap_usd=None)
    lines = sc.email_lines(s, _lev_ok(), _tax_ok())
    assert len(lines) == 4
    assert lines[1].startswith("Account +$497 (+6.9%). ") and "never reused" in lines[1]
    assert not any("ahead of SPY" in x or "behind SPY" in x for x in lines)


def test_email_lines_are_fewer_when_parts_are_unknown():
    lines = sc.email_lines(_since_ok(), {"state": "no_complete_month"}, {"state": "offline"})
    assert len(lines) == 2
    off = sc.email_lines(sc.since_start(None, None, None, None, today=TODAY),
                         {"state": "offline"}, {"state": "offline", "tax_year": 2026})
    assert len(off) == 2 and off[0] == "\U0001f9ed Is this account earning extra?"
    assert "Couldn't read" in off[1]
    assert len(sc.email_lines(None, None, None)) == 2


def test_email_lines_net_loss_and_interest_gap_variants():
    lines = sc.email_lines(_since_ok(), _lev_ok(state="interest_gap", net_usd=None, verdict=None),
                           _tax_ok(net_loss=True, tax_usd=0.0))
    assert lines[2].startswith("Borrowed money, October 2026: no net result yet")
    assert lines[3] == "Tax already triggered by 2026 sales: about $0 (net loss so far; estimate)"
    paid = sc.email_lines(_since_ok(), _lev_ok(verdict="paid"), None)
    assert paid[2].endswith("(leverage paid)") and len(paid) == 3


def test_email_lines_level_and_no_hyphen_minus():
    lines = sc.email_lines(_since_ok(gap_usd=0.0), _lev_ok(net_usd=-82.0, verdict="cost"),
                           _tax_ok())
    assert lines[1].startswith("Level with SPY: ")
    assert "net −$82 after interest (leverage cost you)" in lines[2]
    assert all("-$" not in x for x in lines)


# --------------------------------------------------------------------------- end to end

def test_since_start_feeds_card_model_end_to_end():
    r = _since(1150.0)
    c = sc.card_model(r, None, None)
    assert c["headline"] == "$50 ahead of SPY" and c["tone"] == "pos"
    assert c["subline"] == "Account +$150 (+15.0%) · the same money in SPY +$100 (+10.0%)"
    assert c["asof"] == "As of Fri Nov 13 close · starting point $1,000.00 on Jun 25, when tracking began"



@pytest.mark.parametrize("status", ["pre_coverage", None])
def test_pre_coverage_window_is_incomplete_with_no_verdict(engine, status):
    # The window's opening record isn't a valid anchor -> no paid/cost verdict
    # (lead fix after review round 1).
    engine.recon = _recon(status=status)
    r = _lev(engine)
    assert r["state"] == "incomplete" and r["verdict"] is None
    assert r["start_uncovered"] is True
    row = sc.card_model(_since_ok(), r, None)["lev_row"]
    assert row["chip_kind"] is None
    assert "start of the window isn't usable" in row["caption"]


def test_partial_status_alone_does_not_make_the_window_incomplete(engine):
    # "partial" (e.g. an unknown residual) with nothing dropped keeps its verdict
    engine.recon = _recon(status="partial")
    r = _lev(engine)
    assert r["state"] == "ok" and r["start_uncovered"] is False
