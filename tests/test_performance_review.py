"""Regression tests for stock_analyzer/performance_review.py — Phase 2 of the
📄 Reports feature (docs/plans/reports.md). Period-scoping/assembly only: ET
boundary handling, inclusive-both-ends filtering, six-section offline/empty/
ok independence, the below-floor invariant (never a building/early/firm band
as the headline), delegation equivalence with rec_events_readout/
gate_ledger_readout (no independent alpha/band math), hold_days preservation
via output-filtering, the realized_only return-vs-SPY label, invalid-range
handling, and formatter crash-safety.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import gate_ledger_readout as gtr
from stock_analyzer import performance_review as pr
from stock_analyzer import rec_events_readout as rer

pytestmark = pytest.mark.fast

_TRADE_COLS = ["id", "ticker", "action", "shares", "price", "cost_basis",
               "realized_pnl", "trigger_type", "traded_at"]


def _trade_row(id_, ticker, action, shares, price, when: date, cost_basis=None,
               realized_pnl=None, trigger_type=None, hhmmss_utc="15:00:00"):
    """A single trade row, America/New_York-safe by default (15:00 UTC sits
    mid-day ET regardless of DST) unless the caller overrides `hhmmss_utc` to
    probe an ET boundary explicitly — mirrors test_tax_report.py's `_row`."""
    return {
        "id": id_, "ticker": ticker, "action": action, "shares": shares,
        "price": price, "cost_basis": cost_basis, "realized_pnl": realized_pnl,
        "trigger_type": trigger_type,
        "traded_at": f"{when.isoformat()}T{hhmmss_utc}Z",
    }


def _trades_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_TRADE_COLS)


def _base_kwargs(**overrides) -> dict:
    """Neutral, fully-offline-by-default kwargs for build_review — every
    test overrides only what it needs, so an unrelated signature change
    can't silently make every test pass for the wrong reason."""
    kw = dict(
        today=date(2026, 3, 1),
        trades=None,
        rec_events_rows=None,
        gate_rows=None,
        account_snapshots_df=None,
        account_return_snapshots_df=None,
        account_flows_rows=None,
        risk_snapshots_df=None,
        spy_prices_by_date=None,
        historical_close_fn=None,
        rec_risk_snapshot_by_date=None,
        protective_call_tickers=None,
        rec_min_calls=3, rec_firm_calls=5, rec_min_tickers=3,
        rec_horizon_days=5, rec_action_window_days=3,
        gate_min_calls=3, gate_firm_calls=5, gate_min_tickers=3,
        gate_horizon_days=5, composite_buy=65,
        gate_ids=("G-01", "G-23"),
        ohlc_by_ticker=None,
        risk_min_calls=3,
    )
    kw.update(overrides)
    return kw


def _flat_forward_close(price: float):
    def _fn(ticker, start, end):
        return price
    return _fn


def _spy_series(start: date, days: int, base: float = 400.0, step: float = 0.1) -> dict:
    return {start + timedelta(days=i): base + i * step for i in range(days)}


# ── invalid range ────────────────────────────────────────────────────────────

def test_invalid_range_returns_none():
    kw = _base_kwargs()
    assert pr.build_review(period_start=date(2026, 1, 2), period_end=date(2026, 1, 1), **kw) is None


def test_valid_single_day_range_is_not_none():
    kw = _base_kwargs()
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 1), **kw)
    assert review is not None


# ── six-section presence + offline independence ─────────────────────────────

def test_all_six_sections_present_and_offline_when_every_loader_is_none():
    kw = _base_kwargs()
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    for key in ("return_vs_spy", "trade_behavior", "recs", "gates", "leverage_drift",
                "risk_drift", "risk_discipline"):
        assert key in review
        assert review[key]["status"] == "offline"


def test_one_offline_section_does_not_force_others_offline():
    # trades=None -> trade_behavior/return_vs_spy offline; everything else
    # gets real (non-None) inputs and must NOT read "offline" as a result.
    kw = _base_kwargs(
        trades=None,
        rec_events_rows=[],
        gate_rows=[],
        account_snapshots_df=pd.DataFrame(columns=["snapshot_date", "leverage"]),
        risk_snapshots_df=pd.DataFrame(columns=["snapshot_date", "portfolio_beta"]),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["trade_behavior"]["status"] == "offline"
    assert review["return_vs_spy"]["status"] == "offline"
    assert review["recs"]["status"] == "empty"       # loaded, zero rows -> empty, not offline
    assert review["gates"]["status"] == "empty"
    assert review["leverage_drift"]["status"] == "empty"
    assert review["risk_drift"]["status"] == "empty"
    assert review["risk_discipline"]["status"] == "offline"   # also reads `trades`


# ── ET period boundary (trades.traded_at) ───────────────────────────────────

def test_et_boundary_utc_jan1_early_morning_is_et_dec31():
    rows = [
        _trade_row(1, "XYZ", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        # 2026-01-01 04:30 UTC == 2025-12-31 23:30 ET (EST, UTC-5).
        _trade_row(2, "XYZ", "SELL", 5, 12.0, cost_basis=10.0, realized_pnl=10.0,
                   when=date(2026, 1, 1), hhmmss_utc="04:30:00"),
    ]
    kw = _base_kwargs(trades=_trades_df(rows), today=date(2026, 2, 1))

    review_dec31 = pr.build_review(period_start=date(2025, 12, 31), period_end=date(2025, 12, 31), **kw)
    assert review_dec31["trade_behavior"]["status"] == "ok"
    assert review_dec31["trade_behavior"]["n_trades"] == 1

    review_jan1 = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 1), **kw)
    assert review_jan1["trade_behavior"]["status"] == "empty"


def test_et_boundary_utc_dec31_evening_stays_dec31_not_jan1():
    rows = [
        _trade_row(1, "QRS", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        _trade_row(2, "QRS", "SELL", 5, 12.0, cost_basis=10.0, realized_pnl=10.0,
                   when=date(2025, 12, 31), hhmmss_utc="23:00:00"),
    ]
    kw = _base_kwargs(trades=_trades_df(rows), today=date(2026, 2, 1))
    review = pr.build_review(period_start=date(2025, 12, 31), period_end=date(2025, 12, 31), **kw)
    assert review["trade_behavior"]["status"] == "ok"
    assert review["trade_behavior"]["n_trades"] == 1


# ── inclusive both ends ──────────────────────────────────────────────────────

def test_recs_inclusive_both_ends():
    rec_rows = [
        {"rec_type": "diversify_add", "ticker": "AAA", "fired_date": "2026-01-01",
         "price_at_rec": 100.0, "candidates": ["AAA"]},
        {"rec_type": "diversify_add", "ticker": "BBB", "fired_date": "2026-01-31",
         "price_at_rec": 100.0, "candidates": ["BBB"]},
        {"rec_type": "diversify_add", "ticker": "CCC", "fired_date": "2025-12-31",
         "price_at_rec": 100.0, "candidates": ["CCC"]},  # just before start
        {"rec_type": "diversify_add", "ticker": "DDD", "fired_date": "2026-02-01",
         "price_at_rec": 100.0, "candidates": ["DDD"]},  # just after end
    ]
    kw = _base_kwargs(rec_events_rows=rec_rows, today=date(2026, 3, 1))
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    tickers_in = {r["ticker"] for r in review["recs"]["enriched_rows"]}
    assert tickers_in == {"AAA", "BBB"}


def test_gates_inclusive_both_ends():
    gate_rows = [
        {"gate_id": "G-01", "ticker": "FFF", "rec_date": "2026-01-01",
         "counterfactual": True, "source": "app", "lane": "add_winner"},
        {"gate_id": "G-01", "ticker": "GGG", "rec_date": "2026-01-31",
         "counterfactual": True, "source": "app", "lane": "add_winner"},
        {"gate_id": "G-01", "ticker": "HHH", "rec_date": "2025-12-31",
         "counterfactual": True, "source": "app", "lane": "add_winner"},
        {"gate_id": "G-01", "ticker": "III", "rec_date": "2026-02-01",
         "counterfactual": True, "source": "app", "lane": "add_winner"},
    ]
    kw = _base_kwargs(gate_rows=gate_rows, today=date(2026, 3, 1))
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    tickers_in = {r["ticker"] for r in review["gates"]["enriched_rows"]}
    assert tickers_in == {"FFF", "GGG"}


# ── below-floor invariant ────────────────────────────────────────────────────

def test_recs_below_floor_flag_and_never_hides_raw_counts():
    rec_rows = [
        {"rec_type": "rebal_trim", "ticker": "AAA", "fired_date": "2026-01-05",
         "metric_predicted_after": 10.0},
        {"rec_type": "rebal_trim", "ticker": "BBB", "fired_date": "2026-01-06",
         "metric_predicted_after": 11.0},
    ]
    # min_calls=3 -> 2 matured, 2 tickers is below floor.
    kw = _base_kwargs(rec_events_rows=rec_rows, today=date(2026, 3, 1),
                       rec_min_calls=3, rec_min_tickers=3)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    skipped = [g for g in review["recs"]["by_type"]
               if g["rec_type"] == "rebal_trim" and g["arm"] == "skipped"][0]
    assert skipped["below_floor"] is True
    assert skipped["n_calls"] == 2            # raw counts never hidden
    assert skipped["n_distinct_tickers"] == 2
    assert skipped["band"] == "building"      # internal field only


def test_gates_below_floor_flag_carries_descriptive_mean_alpha_regardless():
    gate_rows = [
        {"gate_id": "G-01", "ticker": "FFF", "rec_date": "2026-01-05",
         "counterfactual": True, "source": "app", "lane": "new_pick",
         "composite_score": 70, "price_at_suppress": 100.0},
        {"gate_id": "G-01", "ticker": "GGG", "rec_date": "2026-01-06",
         "counterfactual": True, "source": "app", "lane": "new_pick",
         "composite_score": 70, "price_at_suppress": 100.0},
    ]
    kw = _base_kwargs(
        gate_rows=gate_rows, today=date(2026, 3, 1),
        spy_prices_by_date=_spy_series(date(2026, 1, 1), 90),
        historical_close_fn=_flat_forward_close(110.0),
        gate_min_calls=3, gate_min_tickers=3,
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    g01 = [g for g in review["gates"]["by_gate"] if g["gate_id"] == "G-01"][0]
    assert g01["below_floor"] is True
    assert g01["band"] == "building"
    # Owner decision 2026-09-18: below-floor still gets the descriptive
    # matured-subset mean alpha, never withheld pending a band.
    assert g01["mean_alpha_pct"] is not None


# ── delegation equivalence (no independent alpha/band math) ─────────────────

def test_recs_delegation_equals_direct_readout_call():
    rec_rows = [
        {"rec_type": "diversify_add", "ticker": "CCC", "fired_date": "2026-01-07",
         "price_at_rec": 100.0, "candidates": ["CCC"], "metric_before": 0.3,
         "corr_coverage_n": 50},
        {"rec_type": "diversify_add", "ticker": "DDD", "fired_date": "2026-01-08",
         "price_at_rec": 100.0, "candidates": ["DDD"], "metric_before": 0.3,
         "corr_coverage_n": 50},
    ]
    spy = _spy_series(date(2026, 1, 1), 90)
    hist = _flat_forward_close(110.0)
    kw = _base_kwargs(
        rec_events_rows=rec_rows, today=date(2026, 3, 1),
        spy_prices_by_date=spy, historical_close_fn=hist,
        rec_risk_snapshot_by_date={},
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)

    collapsed = rer.collapse_by_rec_ticker(rec_rows)
    windowed = [r for r in collapsed
                if pr._to_date(r["fired_date"]) is not None
                and date(2026, 1, 1) <= pr._to_date(r["fired_date"]) <= date(2026, 1, 31)]
    enriched_direct = rer.enrich_and_grade(
        windowed, today=date(2026, 3, 1), trades=[],
        horizon_trading_days=5, action_window_trading_days=3,
        protective_call_tickers=set(), risk_snapshot_by_date={},
        spy_close_by_date=spy, historical_close_fn=hist,
    )
    for rt in ("rebal_trim", "beta_trim", "diversify_add"):
        for arm in ("acted", "skipped"):
            direct = rer.grade_by_rec_type(enriched_direct, rec_type=rt, arm=arm,
                                            min_calls=3, firm_calls=5, min_tickers=3)
            built = [g for g in review["recs"]["by_type"]
                     if g["rec_type"] == rt and g["arm"] == arm][0]
            assert direct["band"] == built["band"]
            assert direct["n_calls"] == built["n_calls"]
            assert direct["n_distinct_tickers"] == built["n_distinct_tickers"]


def test_gates_delegation_equals_direct_readout_call():
    gate_rows = [
        {"gate_id": "G-01", "ticker": "FFF", "rec_date": "2026-01-05",
         "counterfactual": True, "source": "app", "lane": "new_pick",
         "composite_score": 70, "price_at_suppress": 100.0},
        {"gate_id": "G-01", "ticker": "GGG", "rec_date": "2026-01-06",
         "counterfactual": True, "source": "app", "lane": "new_pick",
         "composite_score": 70, "price_at_suppress": 100.0},
    ]
    spy = _spy_series(date(2026, 1, 1), 90)
    hist = _flat_forward_close(110.0)
    kw = _base_kwargs(gate_rows=gate_rows, today=date(2026, 3, 1),
                       spy_prices_by_date=spy, historical_close_fn=hist)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)

    windowed = [r for r in gate_rows
                if date(2026, 1, 1) <= pr._to_date(r["rec_date"]) <= date(2026, 1, 31)]
    enriched_direct = gtr.enrich_and_grade(
        windowed, today=date(2026, 3, 1), spy_close_by_date=spy,
        historical_close_fn=hist, horizon_trading_days=5, composite_buy=65,
    )
    graded_direct = gtr.grade_by_gate(enriched_direct, gate_ids=("G-01", "G-23"),
                                       min_calls=3, firm_calls=5, min_tickers=3)
    for direct, built in zip(graded_direct, review["gates"]["by_gate"]):
        assert direct["gate_id"] == built["gate_id"]
        assert direct.get("band") == built.get("band")
        assert direct.get("n_matured_evaluable") == built.get("n_matured_evaluable")
        assert direct.get("n_distinct_tickers_evaluable") == built.get("n_distinct_tickers_evaluable")
        assert direct.get("mean_alpha_pct") == built.get("mean_alpha_pct")


# ── hold_days preservation via output-filtering ─────────────────────────────

def test_hold_days_preserved_when_matching_buy_is_before_the_window():
    rows = [
        _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2025, 6, 1)),
    ]
    kw = _base_kwargs(trades=_trades_df(rows), today=date(2026, 1, 1))
    # Window starts AFTER the BUY — proves the module filtered the OUTPUT
    # ext_df, not the input trades_df (which would have dropped the BUY and
    # broken the hold_days match to None).
    review = pr.build_review(period_start=date(2025, 5, 1), period_end=date(2025, 6, 30), **kw)
    assert review["trade_behavior"]["status"] == "ok"
    trade_rows = review["trade_behavior"]["trades"]
    assert len(trade_rows) == 1
    assert trade_rows[0]["hold_days"] == (date(2025, 6, 1) - date(2025, 1, 1)).days


def test_hold_days_would_be_none_if_input_were_filtered_first_sanity_check():
    # Sanity check on the fixture itself: compute_extended_stats on the
    # ALREADY-window-filtered input (the wrong approach) really does lose the
    # BUY match, confirming the test above is exercising a real distinction.
    from stock_analyzer.trade_analytics import compute_extended_stats
    rows = [
        _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2025, 6, 1)),
    ]
    wrongly_prefiltered = _trades_df(rows)  # BUY dropped, as an input-filter would do
    ext = compute_extended_stats(wrongly_prefiltered)
    assert ext.iloc[0]["hold_days"] is None


# ── return_vs_spy realized-only basis ────────────────────────────────────────

def test_return_vs_spy_carries_realized_only_basis_and_caption():
    rows = [
        _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2026, 1, 1)),
        _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2026, 1, 10)),
    ]
    spy = _spy_series(date(2026, 1, 1), 40, base=400.0, step=1.0)
    kw = _base_kwargs(trades=_trades_df(rows), spy_prices_by_date=spy, today=date(2026, 2, 1))
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rvs = review["return_vs_spy"]
    assert rvs["status"] == "ok"
    assert rvs["basis"] == "realized_only"
    assert "excluded" in rvs["caption"]
    assert rvs["realized_pnl_total"] == pytest.approx(50.0)
    assert rvs["n_realized_trades"] == 1
    assert rvs["spy_period_return_pct"] is not None
    # cost basis of the closed lot = 5 shares * $10 = $50; realized P&L $50
    # -> realized_return_pct = 100.0%, a cycled-capital return — NOT
    # comparable to SPY's own % (that comparison is the removed delta).
    assert rvs["total_cost_basis"] == pytest.approx(50.0)
    assert rvs["realized_return_pct"] == pytest.approx(100.0)
    # 2026-10-01 reframe: no vs-SPY delta is computed on this figure anymore —
    # it's a per-trade/turnover return, not directly comparable to SPY's own
    # period return (the real defect this reframe fixes).
    assert "delta_vs_spy_pp" not in rvs


def test_realized_return_pct_is_none_without_cost_basis_data():
    # A SELL with no recorded cost_basis contributes $0 to the denominator —
    # the % must degrade to None (unavailable), never a fabricated 0% or a
    # divide-by-zero crash. The dollar P&L (from realized_pnl) still shows.
    rows = [
        _trade_row(1, "ABC", "SELL", 5, 20.0, cost_basis=None, realized_pnl=50.0,
                   when=date(2026, 1, 10)),
    ]
    kw = _base_kwargs(
        trades=_trades_df(rows), spy_prices_by_date=_spy_series(date(2026, 1, 1), 40),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rvs = review["return_vs_spy"]
    assert rvs["status"] == "ok"
    assert rvs["realized_pnl_total"] == pytest.approx(50.0)
    assert rvs["total_cost_basis"] == pytest.approx(0.0)
    assert rvs["realized_return_pct"] is None
    assert "delta_vs_spy_pp" not in rvs


def test_realized_return_pct_uses_total_cost_basis_across_multiple_lots():
    # Two closed lots at different cost bases in the same window — the %
    # must be pooled ($ gain / $ total deployed), not averaged per-trade.
    rows = [
        _trade_row(1, "ABC", "SELL", 10, 15.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2026, 1, 10)),
        _trade_row(2, "XYZ", "SELL", 4, 30.0, cost_basis=20.0, realized_pnl=40.0,
                   when=date(2026, 1, 15)),
    ]
    kw = _base_kwargs(
        trades=_trades_df(rows), spy_prices_by_date=_spy_series(date(2026, 1, 1), 40),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rvs = review["return_vs_spy"]
    # total cost basis = 10*10 + 4*20 = 180; total gain = 90 -> 50.0%
    assert rvs["total_cost_basis"] == pytest.approx(180.0)
    assert rvs["realized_pnl_total"] == pytest.approx(90.0)
    assert rvs["realized_return_pct"] == pytest.approx(50.0)


def test_caption_no_longer_implies_a_direct_spy_comparison():
    """2026-10-01 regression: the real Q3 2026 case this reframe fixes — the
    caption/markdown must say this is NOT the account's return and NOT
    directly comparable to SPY's own period return, so it can't be mistaken
    for a verdict the way the removed delta badge was."""
    rows = [
        _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2026, 1, 1)),
        _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2026, 1, 10)),
    ]
    kw = _base_kwargs(
        trades=_trades_df(rows), spy_prices_by_date=_spy_series(date(2026, 1, 1), 40),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    caption = review["return_vs_spy"]["caption"]
    assert "not your account" in caption.lower()
    assert "not directly comparable" in caption.lower()
    md = pr.format_review_markdown(review)
    assert "Vs. SPY" not in md
    assert "delta" not in md.lower()


def test_return_vs_spy_offline_when_either_loader_missing():
    kw_no_trades = _base_kwargs(trades=None, spy_prices_by_date=_spy_series(date(2026, 1, 1), 10))
    r1 = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 10), **kw_no_trades)
    assert r1["return_vs_spy"]["status"] == "offline"

    kw_no_spy = _base_kwargs(trades=pd.DataFrame(columns=_TRADE_COLS), spy_prices_by_date=None)
    r2 = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 10), **kw_no_spy)
    assert r2["return_vs_spy"]["status"] == "offline"


def test_return_vs_spy_empty_when_no_trades_closed_in_window():
    kw = _base_kwargs(
        trades=pd.DataFrame(columns=_TRADE_COLS),
        spy_prices_by_date=_spy_series(date(2026, 1, 1), 40),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["return_vs_spy"]["status"] == "empty"
    assert review["return_vs_spy"]["n_realized_trades"] == 0


def test_spy_period_return_uses_nearest_prior_close_on_non_trading_days():
    # Both boundaries fall on a Saturday/Sunday-style gap in the series —
    # price_on_or_before must fall back to the nearest earlier date.
    spy = {date(2026, 1, 2): 400.0, date(2026, 1, 9): 410.0}  # gaps in between
    ret = pr._spy_period_return(spy, date(2026, 1, 5), date(2026, 1, 11))
    assert ret == pytest.approx((410.0 / 400.0 - 1) * 100, rel=1e-6)


def test_realized_pnl_excludes_sell_just_outside_either_boundary():
    rows = [
        _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        _trade_row(2, "ABC", "SELL", 1, 20.0, cost_basis=10.0, realized_pnl=10.0,
                   when=date(2025, 12, 31)),   # just before start
        _trade_row(3, "ABC", "SELL", 1, 20.0, cost_basis=10.0, realized_pnl=20.0,
                   when=date(2026, 1, 1)),     # on start -> included
        _trade_row(4, "ABC", "SELL", 1, 20.0, cost_basis=10.0, realized_pnl=30.0,
                   when=date(2026, 1, 31)),    # on end -> included
        _trade_row(5, "ABC", "SELL", 1, 20.0, cost_basis=10.0, realized_pnl=40.0,
                   when=date(2026, 2, 1)),     # just after end
    ]
    kw = _base_kwargs(trades=_trades_df(rows), today=date(2026, 3, 1))
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["trade_behavior"]["n_trades"] == 2
    assert review["trade_behavior"]["total_realized_pnl"] == pytest.approx(50.0)


# ── formatter crash-safety ───────────────────────────────────────────────────

def test_format_markdown_and_csv_handle_offline_review_without_crashing():
    kw = _base_kwargs()
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    md = pr.format_review_markdown(review)
    assert isinstance(md, str) and md
    assert md.count("**") % 2 == 0   # never an unbalanced/literal bold marker
    csv_df = pr.format_review_csv(review)
    assert csv_df.empty


def test_format_markdown_and_csv_handle_empty_review_without_crashing():
    kw = _base_kwargs(
        trades=pd.DataFrame(columns=_TRADE_COLS),
        rec_events_rows=[], gate_rows=[],
        account_snapshots_df=pd.DataFrame(columns=["snapshot_date"]),
        risk_snapshots_df=pd.DataFrame(columns=["snapshot_date"]),
        spy_prices_by_date={},
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    md = pr.format_review_markdown(review)
    assert isinstance(md, str) and md
    assert md.count("**") % 2 == 0
    csv_df = pr.format_review_csv(review)
    assert csv_df.empty


def test_format_markdown_and_csv_handle_populated_review_without_crashing():
    rows = [
        _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2026, 1, 1)),
        _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0,
                   when=date(2026, 1, 15)),
    ]
    rec_rows = [
        {"rec_type": "diversify_add", "ticker": "CCC", "fired_date": "2026-01-07",
         "price_at_rec": 100.0, "candidates": ["CCC"], "metric_before": 0.3,
         "corr_coverage_n": 50},
    ]
    gate_rows = [
        {"gate_id": "G-01", "ticker": "FFF", "rec_date": "2026-01-05",
         "counterfactual": True, "source": "app", "lane": "new_pick",
         "composite_score": 70, "price_at_suppress": 100.0},
        {"gate_id": "G-23", "ticker": "__MARKET__", "rec_date": "2026-01-05",
         "counterfactual": True, "source": "app", "lane": "new_pick"},
    ]
    acct_df = pd.DataFrame([
        {"snapshot_date": "2026-01-01", "leverage": 2.5, "cushion": 0.2, "call_distance_pct": 15.0},
        {"snapshot_date": "2026-01-31", "leverage": 2.8, "cushion": 0.15, "call_distance_pct": 10.0},
    ])
    risk_df = pd.DataFrame([
        {"snapshot_date": "2026-01-01", "portfolio_beta": 1.1, "top_sector_pct": 30.0,
         "max_single_name_pct": 10.0, "avg_pairwise_corr": 0.2, "corr_coverage_n": 100},
        {"snapshot_date": "2026-01-31", "portfolio_beta": 1.3, "top_sector_pct": 35.0,
         "max_single_name_pct": 12.0, "avg_pairwise_corr": 0.25, "corr_coverage_n": 40},
    ])
    kw = _base_kwargs(
        trades=_trades_df(rows), rec_events_rows=rec_rows, gate_rows=gate_rows,
        account_snapshots_df=acct_df, risk_snapshots_df=risk_df,
        spy_prices_by_date=_spy_series(date(2026, 1, 1), 60),
        historical_close_fn=_flat_forward_close(110.0),
        rec_risk_snapshot_by_date={}, today=date(2026, 3, 1),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    md = pr.format_review_markdown(review)
    assert isinstance(md, str) and md
    assert md.count("**") % 2 == 0
    csv_df = pr.format_review_csv(review)
    assert not csv_df.empty
    # risk_discipline joined the export (chunks 4/5) -- the fixture's own
    # ABC round trip closes inside the window, so it contributes a row too
    # (unresolvable -- no ohlc_by_ticker passed -- but still a row).
    assert set(csv_df["section"].unique()) <= {"rec", "gate", "risk_discipline"}


# ─── account_return (2026-10-01) ─────────────────────────────────────────
#
# A first-vs-last account_daily_snapshots lookup + account.
# money_weighted_return — never the capital_vs_margin backward-
# reconstruction engine. See memory project_performance_review_return_tile_
# redesign for the full design and the live-data audit that justified it.

def _snap(d, net_equity, leverage=None, cash_as_of=None):
    """One account_daily_snapshots row — VALID by default (cash_as_of is
    17:00 ET the same day, matching the real pattern a live 2026-10-01 audit
    found in production: 21:0x UTC, after close, correctly reflecting that
    day's trades)."""
    return {
        "snapshot_date": d, "net_equity": net_equity, "leverage": leverage,
        "cash_as_of": cash_as_of if cash_as_of is not None else f"{d}T21:00:00+00:00",
    }


def _flow(d, flow_type, amount):
    return {"flow_date": d, "flow_type": flow_type, "amount": amount}


def _snap_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


# Real production dates (2026-09 account_daily_snapshots), reused verbatim
# from the live audit rather than invented.
_D0 = "2026-09-29"   # prior trading day before 2026-09-30
_D1 = "2026-09-30"


# ── calendar helpers ──────────────────────────────────────────────────────

def test_prior_trading_day_monday_is_friday():
    assert pr._prior_trading_day(date(2026, 9, 14)) == date(2026, 9, 11)


def test_prior_trading_day_skips_a_real_nyse_holiday():
    # 2026-09-07 is Labor Day (constants.NYSE_HOLIDAYS) -- a Monday -- so the
    # session before Tuesday 9/8 is Friday 9/4, not Monday 9/7.
    assert pr._prior_trading_day(date(2026, 9, 8)) == date(2026, 9, 4)


def test_last_trading_day_on_or_before_a_trading_day_is_itself():
    assert pr._last_trading_day_on_or_before(date(2026, 9, 30)) == date(2026, 9, 30)


def test_last_trading_day_on_or_before_a_weekend_steps_back():
    # 2026-09-26 is a Saturday.
    assert pr._last_trading_day_on_or_before(date(2026, 9, 26)) == date(2026, 9, 25)


# ── _valid_snapshot_row ────────────────────────────────────────────────────

def test_valid_row_requires_net_equity():
    row = pd.Series(_snap("2026-09-30", None))
    assert pr._valid_snapshot_row(row) is False


def test_valid_row_rejects_stale_cash_as_of_date():
    # cash_as_of dated a day earlier than snapshot_date -- a stale carried-
    # forward read, not the "no trade that day" case this module no longer
    # rejects.
    row = pd.Series(_snap("2026-09-30", 6516.68, cash_as_of="2026-09-29T21:00:00+00:00"))
    assert pr._valid_snapshot_row(row) is False


def test_valid_row_accepts_same_day_cash_as_of_regardless_of_trades():
    row = pd.Series(_snap("2026-09-30", 6516.68))
    assert pr._valid_snapshot_row(row) is True


# ── _account_return_section: offline / pre_coverage / ok ──────────────────

def test_account_return_offline_when_a_required_loader_missing():
    assert pr._account_return_section(None, [], date(2026, 9, 30), date(2026, 9, 30), {})["status"] == "offline"
    acct = _snap_df([_snap(_D0, 7000.0), _snap(_D1, 7200.0)])
    assert pr._account_return_section(acct, None, date(2026, 9, 30), date(2026, 9, 30), {})["status"] == "offline"


def test_account_return_spy_none_does_not_force_offline():
    """A temporary SPY outage must not withhold the account's own real
    return -- only spy_return_pct itself goes missing (found in Opus
    review, 2026-10-01: SPY-only outage used to force the whole section
    offline even though the account figure is independently computable)."""
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), None)
    assert out["status"] == "ok"
    assert out["spy_return_pct"] is None


def test_account_return_pre_coverage_when_snapshots_empty():
    out = pr._account_return_section(_snap_df([]), [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"
    assert out["earliest_valid_date"] is None
    assert out["secondary"] is None


def test_account_return_ok_zero_flows_equals_net_equity_ratio():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    spy = _spy_series(date(2026, 9, 1), 40, base=600.0, step=0.5)
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), spy)
    assert out["status"] == "ok"
    assert out["d0"] == date(2026, 9, 29) and out["d1"] == date(2026, 9, 30)
    expected = round((6516.68 / 7339.32 - 1) * 100, 2)
    assert out["return_pct"] == pytest.approx(expected)
    assert out["gain"] == pytest.approx(6516.68 - 7339.32)
    assert out["n_flows"] == 0


def test_account_return_delegates_to_money_weighted_return_directly():
    """The figure must equal a direct account.money_weighted_return call on
    the same inputs -- never a reimplemented Dietz formula."""
    from stock_analyzer.account import money_weighted_return as mwr
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    flows = [_flow("2026-09-30", "withdrawal", 100.0)]
    out = pr._account_return_section(acct, flows, date(2026, 9, 30), date(2026, 9, 30), {})
    direct = mwr(7339.32, date(2026, 9, 29), 6516.68, date(2026, 9, 30), flows)
    assert out["return_pct"] == direct["period_return_pct"]
    assert out["gain"] == direct["gain"]


def test_account_return_pre_coverage_when_prior_session_row_missing():
    # d0 (9/29) has no row at all -- never substitutes an earlier date.
    acct = _snap_df([_snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"


def test_account_return_pre_coverage_when_prior_session_row_invalid():
    # d0 exists but fails the validity check (stale cash_as_of).
    acct = _snap_df([
        _snap(_D0, 7339.32, cash_as_of="2026-09-28T21:00:00+00:00"),
        _snap(_D1, 6516.68),
    ])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"


def test_account_return_d1_searches_backward_past_a_missing_todays_row():
    # period_end is "today" (10/1) but today's EOD row isn't written yet --
    # d1 falls back to the last written, valid session (9/30).
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 10, 1), {})
    assert out["status"] == "ok"
    assert out["d1"] == date(2026, 9, 30)


def test_account_return_withholds_but_surfaces_a_dated_secondary_subrange():
    # Period starts long before coverage begins (real Q3 case) -- headline
    # withheld, but the covered sub-range is still shown, labeled with its
    # own dates, never the period's name.
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 7, 1), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"
    assert out["earliest_valid_date"] == date(2026, 9, 29)
    sec = out["secondary"]
    assert sec is not None
    assert sec["d0"] == date(2026, 9, 29) and sec["d1"] == date(2026, 9, 30)


def test_account_return_no_secondary_when_only_one_valid_day_exists():
    acct = _snap_df([_snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 7, 1), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"
    assert out["secondary"] is None


# ── the flow-boundary invariant (the most important edge case) ────────────

def test_flow_dated_exactly_d0_is_excluded_not_double_counted():
    """A deposit dated d0 itself must NOT be counted again -- the EOD
    snapshot for d0 already reflects it. Zero-flow result vs a d0-dated
    flow of the same size must be IDENTICAL."""
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    no_flow = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    with_d0_flow = pr._account_return_section(
        acct, [_flow(_D0, "deposit", 500.0)], date(2026, 9, 30), date(2026, 9, 30), {},
    )
    assert with_d0_flow["return_pct"] == no_flow["return_pct"]
    assert with_d0_flow["n_flows"] == 0


def test_flow_dated_d1_is_counted_at_weight_zero():
    """A flow dated d1 IS counted (changes net_flow/gain accounting) but at
    weight 0 in the Dietz denominator -- pin both the inclusion and the
    zero-weight numerically against a direct money_weighted_return call."""
    from stock_analyzer.account import money_weighted_return as mwr
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    flows = [_flow(_D1, "deposit", 500.0)]
    out = pr._account_return_section(acct, flows, date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["n_flows"] == 1
    direct = mwr(7339.32, date(2026, 9, 29), 6516.68, date(2026, 9, 30), flows)
    assert out["return_pct"] == direct["period_return_pct"]
    # weight-0 means the deposit contributes to net_flow/gain but not to the
    # weighted denominator -- i.e. it moves gain, not neutralizes itself.
    assert direct["net_flow"] == pytest.approx(500.0)


def test_flow_one_day_after_d0_is_counted():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(
        acct, [_flow("2026-09-30", "withdrawal", 100.0)], date(2026, 9, 30), date(2026, 9, 30), {},
    )
    assert out["n_flows"] == 1


# ── SPY anchored to the SAME dates the account block used ─────────────────

def test_account_return_spy_uses_the_same_d0_d1_as_the_account():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    spy = {date(2026, 9, 29): 600.0, date(2026, 9, 30): 606.0}
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), spy)
    assert out["spy_return_pct"] == pytest.approx(1.0)


# ── SPY d1-price-pending disclosure (real 2026-10-01 incident) ─────────────
#
# A live "This Quarter" render on 2026-10-01 (the quarter's first day) showed
# "SPY, same dates: +0.00%" -- confirmed against real yfinance data that
# 2026-10-01's SPY close was NaN (not yet posted), so price_on_or_before
# silently reused 9/30's close for BOTH sides. The account figure (+19.00%,
# built from recorded snapshots) was correct; only the SPY side was a false
# "measured" reading dressed up as a real comparison.

def test_spy_d1_pending_flagged_and_the_stale_figure_is_withheld():
    """Opus review, 2026-10-01: the first version of this fix disclosed the
    STALE number under a caption instead of withholding it -- a caption next
    to a still-visible wrong figure isn't this app's "recommend nothing
    rather than wrongly" posture. spy_return_pct must be None, exactly like
    every other "can't measure this" state."""
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    spy = {date(2026, 9, 29): 600.0}  # no 9/30 entry -- "today hasn't closed"
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), spy)
    assert out["spy_d1_price_pending"] is True
    assert out["spy_return_pct"] is None


def test_spy_d1_pending_false_when_d1_close_is_present():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    spy = {date(2026, 9, 29): 600.0, date(2026, 9, 30): 606.0}
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), spy)
    assert out["spy_d1_price_pending"] is False


def test_spy_d1_pending_false_when_spy_data_is_entirely_unavailable():
    """Empty/None SPY data is a totally different, already-disclosed state
    (spy_return_pct is None) -- it must not ALSO claim a close is "pending\""""
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["spy_d1_price_pending"] is False
    assert out["spy_return_pct"] is None


def test_spy_d1_pending_flows_into_the_secondary_subrange_too():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    spy = {date(2026, 9, 29): 600.0}  # 9/30 missing
    out = pr._account_return_section(acct, [], date(2026, 7, 1), date(2026, 9, 30), spy)
    assert out["status"] == "pre_coverage"
    assert out["secondary"]["spy_d1_price_pending"] is True
    assert out["secondary"]["spy_return_pct"] is None


def test_markdown_discloses_spy_d1_pending_without_crash():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    kw = _base_kwargs(
        account_return_snapshots_df=acct, account_flows_rows=[],
        spy_prices_by_date={date(2026, 9, 29): 600.0},  # 9/30 missing
    )
    review = pr.build_review(period_start=date(2026, 9, 30), period_end=date(2026, 9, 30), **kw)
    md = pr.format_review_markdown(review)
    assert "isn't posted yet" in md
    assert md.count("**") % 2 == 0


# ── leverage caption data ──────────────────────────────────────────────────

def test_account_return_max_leverage_is_the_higher_of_start_or_end():
    acct = _snap_df([
        _snap(_D0, 7339.32, leverage=2.69), _snap(_D1, 6516.68, leverage=3.73),
    ])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["max_leverage"] == pytest.approx(3.73)


def test_account_return_max_leverage_none_when_both_missing():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["max_leverage"] is None


# ── monthly_account_returns ────────────────────────────────────────────────

def test_monthly_returns_only_includes_fully_contained_months():
    acct = _snap_df([
        _snap("2026-08-31", 5000.0), _snap("2026-09-30", 7000.0),
        _snap("2026-10-01", 7100.0), _snap("2026-10-02", 7150.0),
    ])
    # Range spans a partial Aug (starts mid-month) through a partial Oct.
    rows = pr.monthly_account_returns(acct, [], date(2026, 8, 15), date(2026, 10, 2), {})
    months = [r["month"] for r in rows]
    assert months == ["2026-09"]  # only the fully-contained month


def test_monthly_returns_equal_a_standalone_call_over_that_month():
    acct = _snap_df([_snap("2026-08-31", 5000.0), _snap("2026-09-30", 7000.0)])
    rows = pr.monthly_account_returns(acct, [], date(2026, 9, 1), date(2026, 9, 30), {})
    assert len(rows) == 1
    direct = pr._account_return_section(acct, [], date(2026, 9, 1), date(2026, 9, 30), {})
    assert rows[0]["return_pct"] == direct["return_pct"]
    assert rows[0]["d0"] == direct["d0"] and rows[0]["d1"] == direct["d1"]


def test_monthly_returns_a_month_with_no_coverage_carries_its_own_status():
    # 8/31 (Monday, a real trading day -- not an NYSE holiday) is the prior
    # session before 9/1, so it's needed for September's OWN computation to
    # succeed, even though no row exists before THAT for August's.
    acct = _snap_df([
        _snap("2026-08-31", 7000.0), _snap("2026-09-29", 7339.32), _snap("2026-09-30", 6516.68),
    ])
    rows = pr.monthly_account_returns(acct, [], date(2026, 8, 1), date(2026, 9, 30), {})
    assert {r["month"] for r in rows} == {"2026-08", "2026-09"}
    aug = next(r for r in rows if r["month"] == "2026-08")
    sep = next(r for r in rows if r["month"] == "2026-09")
    assert aug["status"] == "pre_coverage"
    assert sep["status"] == "ok"


def test_monthly_returns_empty_on_offline_loader():
    assert pr.monthly_account_returns(None, [], date(2026, 9, 1), date(2026, 9, 30), {}) == []


# ── earliest_valid_account_date ────────────────────────────────────────────

def test_earliest_valid_account_date_ignores_invalid_rows():
    acct = _snap_df([
        _snap("2026-09-10", 100.0, cash_as_of="2026-09-09T21:00:00+00:00"),  # invalid: stale
        _snap("2026-09-11", 200.0),
    ])
    assert pr.earliest_valid_account_date(acct) == date(2026, 9, 11)


def test_earliest_valid_account_date_none_when_offline_or_empty():
    assert pr.earliest_valid_account_date(None) is None
    assert pr.earliest_valid_account_date(_snap_df([])) is None


# ── Opus review fixes, 2026-10-01 ───────────────────────────────────────────

def test_first_measurable_period_start_is_the_day_after_earliest_valid():
    # 2026-09-10 is a Thursday -- the next trading day is Friday 9/11.
    acct = _snap_df([_snap("2026-09-10", 7000.0)])
    assert pr.first_measurable_period_start(acct) == date(2026, 9, 11)


def test_first_measurable_period_start_none_when_no_coverage():
    assert pr.first_measurable_period_start(None) is None
    assert pr.first_measurable_period_start(_snap_df([])) is None


def test_since_tracking_began_preset_actually_produces_ok():
    """The bug this closes: using earliest_valid_account_date directly as
    period_start always resolved to pre_coverage, because its own prior
    trading day has no valid row by definition. The preset must use
    first_measurable_period_start instead."""
    acct = _snap_df([_snap("2026-09-10", 7000.0), _snap("2026-09-11", 7100.0)])
    start = pr.first_measurable_period_start(acct)
    out = pr._account_return_section(acct, [], start, date(2026, 9, 11), {})
    assert out["status"] == "ok"


def test_zero_length_window_is_pre_coverage_not_a_fabricated_zero_percent():
    """d1 == d0_target (e.g. the first day of a period, before today's own
    EOD row is written, falls back to d1 == yesterday) must NOT report a
    fabricated 0.00% indistinguishable from a real flat measurement."""
    acct = _snap_df([_snap(_D0, 7339.32)])  # only d0 exists -- no row for 9/30
    out = pr._account_return_section(acct, [], date(2026, 9, 30), date(2026, 9, 30), {})
    assert out["status"] == "pre_coverage"


def test_validity_rule_rejects_a_pre_close_same_day_cash_read():
    """The audit proved a POST-close read reflects that day's trades -- it
    did not prove every same-ET-date read does. A cash_as_of of 16:00 UTC
    (12:00 ET, before ALERT_EOD_HOUR_ET=16) on the correct calendar date
    must still fail validity (the evening-broker-sync-failure case)."""
    row = pd.Series(_snap("2026-09-30", 6516.68, cash_as_of="2026-09-30T16:00:00+00:00"))
    assert pr._valid_snapshot_row(row) is False


def test_validity_rule_accepts_a_post_close_same_day_cash_read():
    # 21:00 UTC = 17:00 ET (EDT) -- at/after the 16:00 ET close hour.
    row = pd.Series(_snap("2026-09-30", 6516.68, cash_as_of="2026-09-30T21:00:00+00:00"))
    assert pr._valid_snapshot_row(row) is True


def test_monthly_row_does_not_silently_narrow_to_a_sub_range():
    """A month row shows no dates on screen, so 'ok' must mean the WHOLE
    month. If the only valid snapshot at/before month-end is mid-month
    (e.g. the last week's rows are missing or invalid), the row must NOT
    silently claim the full month at a narrower range's return."""
    acct = _snap_df([
        _snap("2026-08-31", 5000.0),   # d0 anchor for September
        _snap("2026-09-15", 5500.0),   # last VALID row in September is mid-month
        # No valid row from 9/16 through 9/30 -- e.g. every later row fails
        # the post-close check, or is simply missing.
    ])
    rows = pr.monthly_account_returns(acct, [], date(2026, 9, 1), date(2026, 9, 30), {})
    assert len(rows) == 1
    assert rows[0]["status"] != "ok"


def test_monthly_row_ok_requires_d1_to_be_the_exact_month_closing_session():
    acct = _snap_df([_snap("2026-08-31", 5000.0), _snap("2026-09-30", 7000.0)])
    rows = pr.monthly_account_returns(acct, [], date(2026, 9, 1), date(2026, 9, 30), {})
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["d1"] == date(2026, 9, 30)


# ── build_review wiring: account_return/account_return_monthly present ────

def test_build_review_carries_account_return_and_monthly():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    kw = _base_kwargs(
        account_return_snapshots_df=acct, account_flows_rows=[],
        spy_prices_by_date=_spy_series(date(2026, 9, 1), 40, base=600.0, step=0.1),
    )
    review = pr.build_review(period_start=date(2026, 9, 30), period_end=date(2026, 9, 30), **kw)
    assert review["account_return"]["status"] == "ok"
    assert isinstance(review["account_return_monthly"], list)


def test_build_review_account_return_offline_independent_of_other_sections():
    """One section's loader failing never forces another offline — the
    module's own stated invariant, now including the new section."""
    kw = _base_kwargs(
        account_return_snapshots_df=None, account_flows_rows=None,
        trades=_trades_df([
            _trade_row(1, "ABC", "BUY", 5, 10.0, when=date(2026, 1, 1)),
            _trade_row(2, "ABC", "SELL", 5, 20.0, cost_basis=10.0, realized_pnl=50.0, when=date(2026, 1, 10)),
        ]),
        spy_prices_by_date=_spy_series(date(2026, 1, 1), 40),
    )
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["account_return"]["status"] == "offline"
    assert review["return_vs_spy"]["status"] == "ok"


# ── formatter crash-safety ─────────────────────────────────────────────────

def test_markdown_renders_account_return_ok_state_without_crash():
    acct = _snap_df([_snap(_D0, 7339.32, leverage=3.73), _snap(_D1, 6516.68, leverage=3.73)])
    kw = _base_kwargs(
        account_return_snapshots_df=acct, account_flows_rows=[],
        spy_prices_by_date=_spy_series(date(2026, 9, 1), 40, base=600.0, step=0.1),
    )
    review = pr.build_review(period_start=date(2026, 9, 30), period_end=date(2026, 9, 30), **kw)
    md = pr.format_review_markdown(review)
    assert "Account Return vs SPY" in md
    assert md.count("**") % 2 == 0


def test_markdown_renders_account_return_pre_coverage_with_secondary_without_crash():
    acct = _snap_df([_snap(_D0, 7339.32), _snap(_D1, 6516.68)])
    kw = _base_kwargs(
        account_return_snapshots_df=acct, account_flows_rows=[],
        spy_prices_by_date=_spy_series(date(2026, 9, 1), 40, base=600.0, step=0.1),
    )
    review = pr.build_review(period_start=date(2026, 7, 1), period_end=date(2026, 9, 30), **kw)
    md = pr.format_review_markdown(review)
    assert "doesn't cover the start" in md
    assert "Tracked history covers" in md
    assert md.count("**") % 2 == 0


def test_markdown_renders_account_return_offline_without_crash():
    kw = _base_kwargs(account_return_snapshots_df=None, account_flows_rows=None)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    md = pr.format_review_markdown(review)
    assert "could not be loaded" in md


# ── risk_discipline (R-multiple, chunks 4/5, 2026-10-02) ────────────────────
#
# `_flat_ohlc` mirrors test_r_multiple.py's own fixture (deliberately NOT
# cross-imported — each test module keeps its own trivial copy, matching the
# established convention the readout modules themselves use for `_to_date`).
# half_range=2.5 => True Range = 5.0 every bar => ATR(14) = 5.0 exactly (a
# constant series), so risk_per_share = ATR_STOP_MULT(2.0) * 5.0 = 10.0 and,
# at 10 shares, risk_dollars = 100.0 -- deterministic, no approximation.

def _flat_ohlc(start, n_days, close=50.0, half_range=2.5, freq="D"):
    idx = pd.date_range(start=start, periods=n_days, freq=freq)
    return pd.DataFrame({
        "High":  [close + half_range] * n_days,
        "Low":   [close - half_range] * n_days,
        "Close": [close] * n_days,
    }, index=idx)


def test_risk_discipline_offline_when_trades_is_none():
    kw = _base_kwargs(trades=None)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["risk_discipline"]["status"] == "offline"
    assert review["risk_discipline"]["episodes"] == []


def test_risk_discipline_empty_when_no_closed_episode_exits_in_window():
    # A BUY with no matching SELL at all -- an open position, never closed.
    rows = [_trade_row(1, "RDA", "BUY", 10, 100.0, when=date(2026, 1, 10))]
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker={})
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["risk_discipline"]["status"] == "empty"
    assert review["risk_discipline"]["episodes"] == []


def test_risk_discipline_empty_on_a_genuinely_empty_trades_frame():
    kw = _base_kwargs(trades=_trades_df([]))
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["risk_discipline"]["status"] == "empty"


def test_risk_discipline_below_floor_shows_episodes_but_no_aggregate():
    # One resolvable closed episode, risk_min_calls=2 -> below the floor.
    rows = [
        _trade_row(1, "RDA", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(2, "RDA", "SELL", 10, 120.0, when=date(2026, 1, 20), realized_pnl=200.0),
    ]
    ohlc_map = {"RDA": _flat_ohlc(date(2025, 12, 1), 60)}
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map, risk_min_calls=2)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rk = review["risk_discipline"]
    assert rk["status"] == "ok"
    assert rk["n_total"] == 1
    assert rk["n_resolvable"] == 1
    assert rk["below_floor"] is True
    assert rk["mean_r"] is None
    assert rk["median_r"] is None
    assert rk["n_losers_worse_than_1r"] is None
    assert rk["pct_losers_worse_than_1r"] is None
    assert len(rk["episodes"]) == 1
    assert rk["episodes"][0]["r_multiple"] == pytest.approx(2.0)   # 200 / 100


def test_risk_discipline_at_floor_includes_aggregate_stats():
    rows = [
        _trade_row(1, "RDA", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(2, "RDA", "SELL", 10, 120.0, when=date(2026, 1, 20), realized_pnl=200.0),
        _trade_row(3, "RDB", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(4, "RDB", "SELL", 10, 80.0,  when=date(2026, 1, 22), realized_pnl=-200.0),
    ]
    ohlc_map = {
        "RDA": _flat_ohlc(date(2025, 12, 1), 60),
        "RDB": _flat_ohlc(date(2025, 12, 1), 60),
    }
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map, risk_min_calls=2)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rk = review["risk_discipline"]
    assert rk["status"] == "ok"
    assert rk["below_floor"] is False
    assert rk["n_resolvable"] == 2
    # R values: +2.0 (RDA), -2.0 (RDB) -> mean 0.0, median 0.0
    assert rk["mean_r"] == pytest.approx(0.0)
    assert rk["median_r"] == pytest.approx(0.0)
    assert rk["n_losers_worse_than_1r"] == 1   # -2.0 < -1.0
    assert rk["pct_losers_worse_than_1r"] == pytest.approx(50.0)


def test_risk_discipline_exit_date_outside_window_is_excluded():
    rows = [
        _trade_row(1, "RDA", "BUY",  10, 100.0, when=date(2025, 11, 1)),
        _trade_row(2, "RDA", "SELL", 10, 120.0, when=date(2025, 12, 15), realized_pnl=200.0),
    ]
    ohlc_map = {"RDA": _flat_ohlc(date(2025, 10, 1), 60)}
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    assert review["risk_discipline"]["status"] == "empty"


def test_risk_discipline_missing_ohlc_resolves_to_none_not_an_exception():
    rows = [
        _trade_row(1, "RDC", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(2, "RDC", "SELL", 10, 120.0, when=date(2026, 1, 20), realized_pnl=200.0),
    ]
    # No OHLC entry at all for RDC -- episode_r_multiple's own None-safe
    # contract must resolve this to None, with a reason, never raise.
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker={})
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    rk = review["risk_discipline"]
    assert rk["status"] == "ok"
    ep = rk["episodes"][0]
    assert ep["r_multiple"] is None          # None, never NaN
    assert ep["r_multiple"] != ep["r_multiple"] or ep["r_multiple"] is None
    assert isinstance(ep["reason"], str) and ep["reason"]
    assert rk["n_resolvable"] == 0
    assert rk["below_floor"] is True


def test_risk_discipline_added_while_losing_flag_propagates():
    rows = [
        _trade_row(1, "RDD", "BUY",  10, 100.0, when=date(2026, 1, 5)),
        # Added at 80, below the running avg cost of 100 -> averaging down.
        _trade_row(2, "RDD", "BUY",  10, 80.0,  when=date(2026, 1, 10)),
        _trade_row(3, "RDD", "SELL", 20, 110.0, when=date(2026, 1, 20), realized_pnl=300.0),
    ]
    ohlc_map = {"RDD": _flat_ohlc(date(2025, 11, 1), 90)}
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    ep = review["risk_discipline"]["episodes"][0]
    assert ep["added_while_losing"] is True


def test_risk_discipline_csv_has_r_multiple_and_added_while_losing_columns_no_nan_text():
    rows = [
        _trade_row(1, "RDA", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(2, "RDA", "SELL", 10, 120.0, when=date(2026, 1, 20), realized_pnl=200.0),
        # Unresolvable leg -- no OHLC for RDE at all.
        _trade_row(3, "RDE", "BUY",  5, 50.0, when=date(2026, 1, 11)),
        _trade_row(4, "RDE", "SELL", 5, 55.0, when=date(2026, 1, 21), realized_pnl=25.0),
    ]
    ohlc_map = {"RDA": _flat_ohlc(date(2025, 12, 1), 60)}
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map, risk_min_calls=5)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    csv_df = pr.format_review_csv(review)
    assert "r_multiple" in csv_df.columns
    assert "added_while_losing" in csv_df.columns
    rd_rows = csv_df[csv_df["section"] == "risk_discipline"]
    assert len(rd_rows) == 2
    csv_text = csv_df.to_csv(index=False)
    assert "nan" not in csv_text.lower()


def test_risk_discipline_markdown_section_renders_without_crash_and_no_nan_text():
    rows = [
        _trade_row(1, "RDA", "BUY",  10, 100.0, when=date(2026, 1, 10)),
        _trade_row(2, "RDA", "SELL", 10, 120.0, when=date(2026, 1, 20), realized_pnl=200.0),
        _trade_row(3, "RDE", "BUY",  5, 50.0, when=date(2026, 1, 11)),
        _trade_row(4, "RDE", "SELL", 5, 55.0, when=date(2026, 1, 21), realized_pnl=25.0),
    ]
    ohlc_map = {"RDA": _flat_ohlc(date(2025, 12, 1), 60)}
    kw = _base_kwargs(trades=_trades_df(rows), ohlc_by_ticker=ohlc_map, risk_min_calls=5)
    review = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw)
    md = pr.format_review_markdown(review)
    assert "Risk Discipline" in md
    assert "no R (" in md
    assert "nan" not in md.lower()
    assert "below the standalone evaluable floor" in md.lower()


def test_risk_discipline_markdown_offline_and_empty_without_crash():
    kw_off = _base_kwargs(trades=None)
    review_off = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw_off)
    assert "could not be loaded" in pr.format_review_markdown(review_off)

    kw_empty = _base_kwargs(trades=_trades_df([]))
    review_empty = pr.build_review(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), **kw_empty)
    assert "No closed round trips" in pr.format_review_markdown(review_empty)
