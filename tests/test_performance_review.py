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
    for key in ("return_vs_spy", "trade_behavior", "recs", "gates", "leverage_drift", "risk_drift"):
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
    assert "not included" in rvs["caption"]
    assert rvs["realized_pnl_total"] == pytest.approx(50.0)
    assert rvs["n_realized_trades"] == 1
    assert rvs["spy_period_return_pct"] is not None
    # cost basis of the closed lot = 5 shares * $10 = $50; realized P&L $50
    # -> realized_return_pct = 100.0%, directly comparable to SPY's own %.
    assert rvs["total_cost_basis"] == pytest.approx(50.0)
    assert rvs["realized_return_pct"] == pytest.approx(100.0)
    assert rvs["delta_vs_spy_pp"] == pytest.approx(
        rvs["realized_return_pct"] - rvs["spy_period_return_pct"]
    )


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
    assert rvs["delta_vs_spy_pp"] is None


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
    assert set(csv_df["section"].unique()) <= {"rec", "gate"}
