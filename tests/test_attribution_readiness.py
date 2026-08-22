"""Tests for stock_analyzer/attribution_readiness.py — the E2 alpha-attribution
data-readiness audit.

The load-bearing behaviour is that a GAPPED history cannot present as a
continuous one. The panel this replaces measured coverage as
`(latest - earliest).days + 1`, so snapshots for 5 sessions in March plus 1 in
August reported 168 days of "coverage" backed by 6 real dates —
and `daily_snapshots` is cron-written, so gaps are demonstrated in this app
rather than hypothetical. These pin that the distinct-date count, the
NYSE-session denominator and the largest-gap figure all tell the truth on
exactly that input.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import attribution_readiness as ar
from stock_analyzer.data import is_trading_day


def _snaps(rows):
    """rows = [(date, ticker, shares, close_price), ...]"""
    return pd.DataFrame(
        [{"snapshot_date": d, "ticker": t, "shares": s, "close_price": p}
         for d, t, s, p in rows]
    )


def _sessions_from(start: date, n: int) -> list[date]:
    """The next `n` NYSE sessions starting at/after `start`."""
    out, cur = [], start
    while len(out) < n:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


# ── snapshot_coverage ─────────────────────────────────────────────────────────

def test_snapshot_coverage_is_none_on_no_usable_dates():
    for bad in (None, pd.DataFrame(), pd.DataFrame({"ticker": ["A"]})):
        assert ar.snapshot_coverage(bad) is None


def test_snapshot_coverage_counts_distinct_dates_not_rows():
    """Per-ticker rows must not inflate the observation count.

    daily_snapshots' PK is (snapshot_date, ticker), so an 18-name book writes 18
    rows per day. Counting rows would report 18x the real history.
    """
    days = _sessions_from(date(2026, 3, 2), 5)
    rows = [(d, t, 10.0, 100.0) for d in days for t in ("AAA", "BBB", "CCC")]
    cov = ar.snapshot_coverage(_snaps(rows))
    assert cov["n_dates"] == 5          # not 15
    assert cov["earliest"] == days[0]
    assert cov["latest"] == days[-1]


def test_a_gapped_history_cannot_present_as_continuous():
    """THE regression this module exists for.

    One week of snapshots, a long silence, then one more day. The old
    calendar-span metric would report the whole span as coverage; completeness
    must instead expose that almost nothing is there.
    """
    early = _sessions_from(date(2026, 3, 2), 5)
    late = _sessions_from(date(2026, 8, 17), 1)
    cov = ar.snapshot_coverage(_snaps([(d, "AAA", 1.0, 10.0) for d in early + late]))

    assert cov["n_dates"] == 6
    # The calendar span is enormous — and that is exactly the misleading figure.
    assert cov["span_days"] > 150
    # The honest one is not.
    assert cov["completeness_pct"] < 10
    assert cov["missing_sessions"] > 100
    # And the single long outage is surfaced as such.
    assert cov["largest_gap_sessions"] > 100


def test_snapshot_coverage_complete_run_reads_100_pct():
    days = _sessions_from(date(2026, 3, 2), 20)
    cov = ar.snapshot_coverage(_snaps([(d, "AAA", 1.0, 10.0) for d in days]))
    assert cov["n_dates"] == 20
    assert cov["expected_sessions"] == 20
    assert cov["missing_sessions"] == 0
    assert cov["largest_gap_sessions"] == 0
    assert cov["completeness_pct"] == 100.0


def test_weekends_and_holidays_are_not_counted_as_gaps():
    """A naive weekday/calendar denominator would invent missing sessions.

    Span deliberately brackets July 4th 2026 observed + weekends; a run of
    consecutive sessions must still read 100% complete.
    """
    days = _sessions_from(date(2026, 6, 29), 12)   # spans the Jul-4 holiday week
    cov = ar.snapshot_coverage(_snaps([(d, "AAA", 1.0, 10.0) for d in days]))
    assert cov["completeness_pct"] == 100.0
    assert cov["missing_sessions"] == 0
    # The calendar span is strictly longer than the session count it contains.
    assert cov["span_days"] > cov["expected_sessions"]


def test_largest_gap_isolates_one_outage_from_scattered_misses():
    """Same total misses, very different meaning — the figure must distinguish."""
    days = _sessions_from(date(2026, 3, 2), 20)
    scattered = [d for i, d in enumerate(days) if i not in (3, 8, 14)]
    contiguous = [d for i, d in enumerate(days) if i not in (7, 8, 9)]

    cov_s = ar.snapshot_coverage(_snaps([(d, "AAA", 1.0, 10.0) for d in scattered]))
    cov_c = ar.snapshot_coverage(_snaps([(d, "AAA", 1.0, 10.0) for d in contiguous]))
    assert cov_s["missing_sessions"] == cov_c["missing_sessions"] == 3
    assert cov_s["largest_gap_sessions"] == 1
    assert cov_c["largest_gap_sessions"] == 3


def test_snapshot_coverage_counts_non_session_writes_without_treating_them_as_gaps():
    days = _sessions_from(date(2026, 3, 2), 3)
    saturday = date(2026, 3, 7)
    assert not is_trading_day(saturday)
    cov = ar.snapshot_coverage(
        _snaps([(d, "AAA", 1.0, 10.0) for d in days + [saturday]])
    )
    assert cov["non_session_dates"] == 1
    assert cov["n_dates"] == 4


# ── concentration ─────────────────────────────────────────────────────────────

def test_concentration_effective_positions_on_equal_weights():
    days = _sessions_from(date(2026, 3, 2), 2)
    rows = [(days[-1], t, 10.0, 100.0) for t in ("A", "B", "C", "D")]
    rows += [(days[0], "A", 1.0, 1.0)]          # earlier date must be ignored
    con = ar.concentration(_snaps(rows))
    assert con["as_of"] == days[-1]
    assert con["n_positions"] == 4
    assert con["effective_positions"] == 4.0    # equal weights → 1/H == n
    assert con["top_weight_pct"] == 25.0


def test_concentration_effective_positions_falls_below_n_when_concentrated():
    d = _sessions_from(date(2026, 3, 2), 1)[0]
    rows = [(d, "BIG", 100.0, 100.0)] + [(d, t, 1.0, 100.0) for t in ("B", "C", "D")]
    con = ar.concentration(_snaps(rows))
    assert con["n_positions"] == 4
    assert con["effective_positions"] < 2.0, "a dominant name must collapse effective N"
    assert con["top_weight_pct"] > 90


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"snapshot_date": []})])
def test_concentration_is_none_on_unusable_input(bad):
    assert ar.concentration(bad) is None


def test_concentration_is_none_not_zero_on_a_worthless_book():
    d = _sessions_from(date(2026, 3, 2), 1)[0]
    assert ar.concentration(_snaps([(d, "AAA", 1.0, 0.0)])) is None


# ── turnover ──────────────────────────────────────────────────────────────────

def _trades(rows):
    """rows = [(traded_at, ticker, action, shares, price), ...]"""
    return pd.DataFrame(
        [{"traded_at": t, "ticker": tk, "action": a, "shares": s, "price": p}
         for t, tk, a, s, p in rows]
    )


def test_turnover_measures_the_window_and_splits_the_legs():
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])   # book = $10,000 flat
    # One $2,000 round trip inside the window.
    trades = _trades([
        (f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0),
        (f"{days[5]}T14:00:00Z", "AAA", "SELL", 10.0, 100.0),
    ])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["n_trades"] == 2
    assert out["traded_notional"] == 2000.0
    assert out["buy_notional"] == 1000.0
    assert out["sell_notional"] == 1000.0
    assert out["mean_book_value"] == 10000.0
    assert out["n_snapshot_dates_in_window"] == 10
    assert out["window_turnover_pct"] == 20.0
    # Legs as percentages of the same book, for a caller that must not handle
    # dollar figures.
    assert out["buy_turnover_pct"] == 10.0
    assert out["sell_turnover_pct"] == 10.0


def test_turnover_legs_as_pct_sum_to_the_total_pct():
    """buy_pct + sell_pct == window_turnover_pct, the same identity the notional
    fields hold. Pinned because the two are rounded independently."""
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([
        (f"{days[1]}T14:00:00Z", "AAA", "BUY",  3.0, 100.0),
        (f"{days[3]}T14:00:00Z", "AAA", "SELL", 7.0, 100.0),
    ])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["buy_turnover_pct"] + out["sell_turnover_pct"] == pytest.approx(
        out["window_turnover_pct"], abs=0.2
    )


def test_turnover_accumulation_and_churn_differ_in_the_legs_not_the_total():
    """THE reason the legs are reported at all.

    A book BUILT inside its own measurement window and a book CHURNED inside it
    produce the SAME total turnover figure and mean completely different things.
    The summed number cannot tell them apart; the split does immediately. The
    shipped panel rendered only the sum, which is what made a live 1564% reading
    uninterpretable.
    """
    days  = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])   # book = $10,000

    churn = ar.turnover(_trades([
        (f"{days[2]}T14:00:00Z", "AAA", "BUY",  10.0, 100.0),
        (f"{days[5]}T14:00:00Z", "AAA", "SELL", 10.0, 100.0),
    ]), snaps, lookback_days=180)

    accumulation = ar.turnover(_trades([
        (f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0),
        (f"{days[5]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0),
    ]), snaps, lookback_days=180)

    # Indistinguishable on the headline figure...
    assert churn["window_turnover_pct"] == accumulation["window_turnover_pct"] == 20.0
    # ...and unambiguous on the legs.
    assert (churn["buy_turnover_pct"], churn["sell_turnover_pct"]) == (10.0, 10.0)
    assert (accumulation["buy_turnover_pct"], accumulation["sell_turnover_pct"]) == (20.0, 0.0)


def test_turnover_window_days_is_inclusive_and_agrees_with_span_days():
    """Both figures render on the same panel describing the same interval.

    Before 2026-08-21 they read 74 vs 73 days for one interval — an
    inclusive/exclusive artifact, but on a panel whose entire purpose is
    trustworthy counting it reads as a bug.
    """
    days  = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([(f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0)])
    turn = ar.turnover(trades, snaps, lookback_days=180)
    cov  = ar.snapshot_coverage(snaps)
    # The snapshot history is shorter than the lookback, so the turnover window
    # IS the snapshot span — the two must agree exactly.
    assert turn["window_days"] == cov["span_days"]
    assert turn["window_days"] == (days[-1] - days[0]).days + 1


def test_turnover_withholds_the_annualised_figure_on_a_short_window():
    """Annualising a 10-day history multiplies by ~36 and means nothing.

    The earlier version of this test asserted only `annualised > 20.0`, which
    passed on a ~664% figure — the assertion documented the blow-up instead of
    catching it. The annualised number is now withheld until the window is
    genuinely as long as the lookback.
    """
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([(f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0)])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["window_days"] < 180
    assert out["annualised_turnover_pct"] is None
    assert out["window_turnover_pct"] is not None, "the honest figure must still be there"


def test_turnover_annualises_once_the_window_is_long_enough():
    days = _sessions_from(date(2026, 1, 2), 130)     # spans ~188 calendar days
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    # MID-window deliberately: 130 sessions span ~188 calendar days, so the
    # 180-day lookback starts AFTER days[0] and an early trade falls outside it.
    trades = _trades([(f"{days[60]}T14:00:00Z", "AAA", "BUY", 100.0, 100.0)])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["window_days"] >= 180
    assert out["n_trades"] == 1
    assert out["annualised_turnover_pct"] is not None
    # 10,000 traded on a 10,000 book over ~180 days ≈ 200%/yr.
    assert 150 < out["annualised_turnover_pct"] < 250


def test_turnover_excludes_a_trade_older_than_the_lookback():
    """The window boundary is real — an early trade in a long span is dropped."""
    days = _sessions_from(date(2026, 1, 2), 130)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([(f"{days[2]}T14:00:00Z", "AAA", "BUY", 100.0, 100.0)])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["n_trades"] == 0
    assert out["window_turnover_pct"] == 0.0


def test_turnover_ignores_synthetic_split_rows():
    """A SPLIT row's shares x price is the whole cost basis, not a trade.

    The Apply-Split handler (app.py) writes a synthetic db.save_trade row with
    action='SPLIT', shares = adjusted TOTAL shares and price = adjusted avg
    cost, so counting one injects a full-position-sized fake notional.
    trades.py / portfolio_qa.py / evening_debrief.py all filter this; memory
    project_split_recalc_deferred records it as a recurring class.
    """
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    real = _trades([(f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0)])
    with_split = _trades([
        (f"{days[2]}T14:00:00Z", "AAA", "BUY", 10.0, 100.0),
        # 1000 shares x $100 basis = $100,000 of fake notional on a $10k book.
        (f"{days[4]}T14:00:00Z", "AAA", "SPLIT", 1000.0, 100.0),
    ])
    base = ar.turnover(real, snaps, lookback_days=180)
    withs = ar.turnover(with_split, snaps, lookback_days=180)
    assert withs["n_trades"] == base["n_trades"] == 1
    assert withs["traded_notional"] == base["traded_notional"] == 1000.0
    assert withs["window_turnover_pct"] == base["window_turnover_pct"]


def test_turnover_requires_an_action_column_rather_than_assuming_trades():
    """No `action` means splits are indistinguishable from trades → unknown."""
    days = _sessions_from(date(2026, 3, 2), 5)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    no_action = pd.DataFrame([
        {"traded_at": f"{days[1]}T14:00:00Z", "shares": 10.0, "price": 100.0}
    ])
    assert ar.turnover(no_action, snaps) is None


def test_turnover_excludes_trades_outside_the_lookback():
    days = _sessions_from(date(2026, 6, 1), 5)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([
        ("2025-01-05T14:00:00Z", "AAA", "BUY", 10.0, 100.0),   # long before
        (f"{days[1]}T14:00:00Z", "AAA", "BUY", 5.0, 100.0),    # inside
    ])
    out = ar.turnover(trades, snaps, lookback_days=30)
    assert out["n_trades"] == 1
    assert out["traded_notional"] == 500.0


def test_turnover_is_none_not_zero_when_a_leg_is_missing():
    """A missing leg must read 'unknown', never 'no churn'."""
    days = _sessions_from(date(2026, 3, 2), 3)
    snaps = _snaps([(d, "AAA", 10.0, 100.0) for d in days])
    trades = _trades([(f"{days[0]}T14:00:00Z", "AAA", "BUY", 1.0, 10.0)])
    assert ar.turnover(None, snaps) is None
    assert ar.turnover(trades, None) is None
    assert ar.turnover(pd.DataFrame(), snaps) is None
    # traded_at absent → cannot place trades in time → unknown, not zero.
    assert ar.turnover(pd.DataFrame({"shares": [1.0], "price": [2.0]}), snaps) is None


def test_turnover_handles_mixed_timezone_offsets():
    """traded_at is written by several paths; mixed ISO offsets must not become NaT."""
    days = _sessions_from(date(2026, 3, 2), 5)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([
        (f"{days[1]}T14:00:00+00:00", "AAA", "BUY", 5.0, 100.0),
        (f"{days[2]}T09:30:00-05:00", "AAA", "SELL", 5.0, 100.0),
    ])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["n_trades"] == 2, "a mixed-offset row must not be dropped as NaT"
    assert out["traded_notional"] == 1000.0


def test_turnover_total_equals_the_sum_of_its_legs():
    """The identity must hold by construction, not by convention.

    `action` is {BUY, SELL, SPLIT} across all five write paths today, but a
    legacy row whose action was backfilled None stringifies to "NONE" — it must
    land in neither the legs nor the total, rather than inflating the total.
    """
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([
        (f"{days[1]}T14:00:00Z", "AAA", "BUY", 5.0, 100.0),
        (f"{days[3]}T14:00:00Z", "AAA", "SELL", 2.0, 100.0),
        (f"{days[4]}T14:00:00Z", "AAA", None, 99.0, 100.0),    # legacy null action
        (f"{days[5]}T14:00:00Z", "AAA", "SPLIT", 1000.0, 100.0),
    ])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["traded_notional"] == out["buy_notional"] + out["sell_notional"]
    assert out["traded_notional"] == 700.0      # 500 buy + 200 sell only
    assert out["n_trades"] == 2                  # null-action and SPLIT excluded


def test_turnover_ignores_unpriced_rows_in_the_trade_count():
    """An unparseable shares/price row must not inflate n_trades."""
    days = _sessions_from(date(2026, 3, 2), 10)
    snaps = _snaps([(d, "AAA", 100.0, 100.0) for d in days])
    trades = _trades([
        (f"{days[1]}T14:00:00Z", "AAA", "BUY", 5.0, 100.0),
        (f"{days[2]}T14:00:00Z", "AAA", "BUY", "junk", 100.0),
    ])
    out = ar.turnover(trades, snaps, lookback_days=180)
    assert out["n_trades"] == 1
    assert out["traded_notional"] == 500.0


def test_is_session_uses_late_binding_not_a_cached_global():
    """A monkeypatched is_trading_day must not latch permanently.

    Caching the resolved function in a module global would bind whichever object
    was present on the FIRST call, so a patched weekday-only lambda could
    survive teardown and silently count untabled holidays as sessions for every
    later caller in the process.
    """
    import stock_analyzer.data as sdata
    assert not hasattr(ar, "_IS_SESSION"), "no module-level cache should exist"
    real = sdata.is_trading_day
    try:
        sdata.is_trading_day = lambda d: False      # nothing is a session
        assert ar._is_session(date(2026, 3, 2)) is False
    finally:
        sdata.is_trading_day = real
    # Reverting the patch must take effect immediately — no stale binding.
    assert ar._is_session(date(2026, 3, 2)) is True
