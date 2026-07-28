"""Regression tests for the Entry Timing tab's pure functions in
stock_analyzer/predictive_analytics.py — dedupe_repeated_tickers,
divergence_at_entry, forward_alpha_at_horizon, by_divergence_band.
See docs/plans/entry-timing-tab.md for the design this implements.
"""
from datetime import date

from stock_analyzer.predictive_analytics import (
    by_divergence_band,
    dedupe_repeated_tickers,
    divergence_at_entry,
    forward_alpha_at_horizon,
)


def _rec(ticker, d, rec_type="new_pick", composite=70.0, momentum=95.0, **extra):
    row = {
        "ticker": ticker, "rec_date": d, "rec_type": rec_type,
        "composite_score": composite, "momentum_score": momentum,
    }
    row.update(extra)
    return row


# ── divergence_at_entry ──────────────────────────────────────────────────────

def test_divergence_at_entry_positive():
    assert divergence_at_entry(_rec("AMD", date(2026, 7, 9))) == 25.0


def test_divergence_at_entry_missing_scores_returns_none():
    assert divergence_at_entry({"ticker": "AMD"}) is None
    assert divergence_at_entry({"composite_score": 70}) is None


def test_divergence_at_entry_negative_is_still_computed():
    # Negative divergence is a valid computation here — filtering it out of
    # scope happens downstream in by_divergence_band, not in this function.
    assert divergence_at_entry(_rec("XYZ", date(2026, 7, 9), composite=90, momentum=60)) == -30.0


# ── dedupe_repeated_tickers ──────────────────────────────────────────────────

def test_dedupe_collapses_amd_style_repeat_firings():
    rows = [
        _rec("AMD", date(2026, 7, 9)),
        _rec("AMD", date(2026, 7, 10)),
        _rec("AMD", date(2026, 7, 14)),
        _rec("AMD", date(2026, 7, 15)),
        _rec("AMD", date(2026, 7, 22)),
    ]
    out = dedupe_repeated_tickers(rows, window_days=5)
    # 07-09 anchor absorbs 07-10/07-14 (both within 5 days of 07-09); 07-15 is
    # 6 days out -> new anchor; 07-22 is 7 days after 07-15 -> new anchor.
    assert [r["rec_date"] for r in out] == [date(2026, 7, 9), date(2026, 7, 15), date(2026, 7, 22)]


def test_dedupe_leaves_distinct_tickers_alone():
    rows = [_rec("AMD", date(2026, 7, 9)), _rec("NVDA", date(2026, 7, 9))]
    out = dedupe_repeated_tickers(rows, window_days=5)
    assert {r["ticker"] for r in out} == {"AMD", "NVDA"}


def test_dedupe_scopes_to_rec_types_and_passes_others_through():
    rows = [
        _rec("AMD", date(2026, 7, 9), rec_type="new_pick"),
        _rec("AMD", date(2026, 7, 10), rec_type="new_pick"),
        _rec("AMD", date(2026, 7, 9), rec_type="add_winner"),
        _rec("AMD", date(2026, 7, 10), rec_type="add_winner"),
    ]
    out = dedupe_repeated_tickers(rows, window_days=5, rec_types=("new_pick",))
    new_picks = [r for r in out if r["rec_type"] == "new_pick"]
    add_winners = [r for r in out if r["rec_type"] == "add_winner"]
    assert len(new_picks) == 1
    assert len(add_winners) == 2   # untouched, not deduped


def test_dedupe_keeps_undated_rows():
    rows = [_rec("AMD", None), _rec("AMD", date(2026, 7, 9))]
    out = dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 2


# ── forward_alpha_at_horizon ─────────────────────────────────────────────────

def test_forward_alpha_at_horizon_basic():
    spy_by_date = {date(2026, 7, 9): 500.0, date(2026, 7, 16): 505.0}

    def fake_fetch(ticker, start, end):
        assert ticker == "AMD"
        return 110.0   # forward close

    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, spy_by_date, historical_close_fn=fake_fetch,
    )
    # stock: +10%, SPY: +1% -> alpha ~ +9pp
    assert alpha == 9.0


def test_forward_alpha_at_horizon_missing_entry_price():
    assert forward_alpha_at_horizon("AMD", date(2026, 7, 9), None, 5, {}) is None
    assert forward_alpha_at_horizon("AMD", date(2026, 7, 9), 0.0, 5, {}) is None


def test_forward_alpha_at_horizon_fetch_returns_none():
    alpha = forward_alpha_at_horizon(
        "DELISTED", date(2026, 7, 9), 100.0, 5, {"x": 1},
        historical_close_fn=lambda t, s, e: None,
    )
    assert alpha is None


def test_forward_alpha_at_horizon_fetch_raises_is_caught():
    def boom(t, s, e):
        raise RuntimeError("network down")
    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, {}, historical_close_fn=boom,
    )
    assert alpha is None


def test_forward_alpha_at_horizon_missing_spy_series_returns_none():
    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, None,
        historical_close_fn=lambda t, s, e: 110.0,
    )
    assert alpha is None


# ── by_divergence_band ───────────────────────────────────────────────────────

def _band_row(divergence, day1_alpha=None, day5_alpha=None, alpha_pct=None, maturing=False):
    return {
        "divergence": divergence, "day1_alpha": day1_alpha, "day5_alpha": day5_alpha,
        "alpha_pct": alpha_pct, "outcome_maturing": maturing,
    }


def test_by_divergence_band_excludes_non_positive_divergence():
    rows = [
        _band_row(divergence=-5, day1_alpha=-10),
        _band_row(divergence=0, day1_alpha=-10),
        _band_row(divergence=None, day1_alpha=-10),
    ]
    assert by_divergence_band(rows, aligned_max=15, diverging_max=25) == []


def test_by_divergence_band_buckets_by_threshold():
    rows = [
        _band_row(divergence=10, day1_alpha=-2),    # Aligned
        _band_row(divergence=20, day1_alpha=-8),    # Diverging
        _band_row(divergence=30, day1_alpha=-15),   # Extreme
    ]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    labels = {b["band_label"]: b for b in bands}
    assert set(labels) == {"Aligned", "Diverging", "Extreme"}
    assert labels["Aligned"]["day1_alpha"] == -2
    assert labels["Extreme"]["day1_alpha"] == -15


def test_by_divergence_band_pct_red_and_day20_reuses_mature_alpha():
    rows = [
        _band_row(divergence=30, day1_alpha=-5, alpha_pct=-3, maturing=False),
        _band_row(divergence=35, day1_alpha=5,  alpha_pct=8,  maturing=False),
        _band_row(divergence=40, day1_alpha=-2, alpha_pct=None, maturing=True),  # excluded from day20
    ]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    extreme = next(b for b in bands if b["band_label"] == "Extreme")
    assert extreme["day1_n"] == 3
    assert extreme["day1_pct_red"] == round(2 / 3, 3)
    assert extreme["day20_n"] == 2   # the maturing row is excluded
    assert extreme["day20_alpha"] == round((-3 + 8) / 2, 2)
    assert extreme["p_positive_alpha"] == 0.5   # 1 of 2 day20 outcomes positive


def test_by_divergence_band_missing_horizon_data_is_none_not_zero():
    rows = [_band_row(divergence=10, day1_alpha=None, day5_alpha=None, alpha_pct=None, maturing=True)]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    aligned = bands[0]
    assert aligned["n"] == 1
    assert aligned["day1_alpha"] is None
    assert aligned["day1_n"] == 0
    assert aligned["day20_alpha"] is None
