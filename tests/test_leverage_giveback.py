"""Tests for stock_analyzer.leverage_giveback — the Summary/Book Safety
leverage-vs-equity giveback disclosure (2026-10-02).

Fixtures for the 2026-09-23 -> 2026-09-30 "giveback" case and the 2026-09-16
"elevated, first day past the mark" near-miss use the REAL owner-verified
numbers from docs/mockups/2026-10-02-leverage-giveback-mockup.html — the
intermediate days between the named anchor dates are constructed (not
disclosed by the mockup), but every anchor day's own net_equity/gross_book/
cash_balance match the mockup exactly, and the resulting headline/caption
strings were verified byte-for-byte against the mockup's own approved copy
before being pinned here.
"""
import ast
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import leverage_giveback as lgb
from stock_analyzer import margin
from stock_analyzer.constants import (
    LEVERAGE_REFERENCE_TARGET,
    LEVERAGE_GIVEBACK_DRAWDOWN_PCT,
    LEVERAGE_GIVEBACK_LOOKBACK_DAYS,
    LEVERAGE_GIVEBACK_MIN_HISTORY_DAYS,
    LEVERAGE_GIVEBACK_CONFIRM_DAYS,
    ACCOUNT_CASH_STALE_DAYS,
    MARGIN_MAINTENANCE_RATE,
)

pytestmark = pytest.mark.fast

TARGET = LEVERAGE_REFERENCE_TARGET          # 2.0
DD_PCT = LEVERAGE_GIVEBACK_DRAWDOWN_PCT     # -10.0
LOOKBACK = LEVERAGE_GIVEBACK_LOOKBACK_DAYS  # 63
MIN_HIST = LEVERAGE_GIVEBACK_MIN_HISTORY_DAYS  # 5
CONFIRM = LEVERAGE_GIVEBACK_CONFIRM_DAYS    # 2
STALE = ACCOUNT_CASH_STALE_DAYS
RATE = MARGIN_MAINTENANCE_RATE


def _row(date, net_equity, gross_book, call_distance_pct=None, leverage=None):
    """Build one daily_snapshots-shaped row. cash_balance/leverage are
    derived unless overridden."""
    cash = net_equity - gross_book
    if leverage is None:
        leverage = gross_book / net_equity if net_equity else None
    return {
        "snapshot_date": date,
        "gross_book": gross_book,
        "cash_balance": cash,
        "net_equity": net_equity,
        "leverage": leverage,
        "call_distance_pct": call_distance_pct,
        "maintenance_rate": RATE,
    }


def _assess(rows, flows, today, **overrides):
    kwargs = dict(
        target=TARGET, drawdown_pct=DD_PCT, lookback=LOOKBACK,
        min_history=MIN_HIST, confirm_days=CONFIRM, stale_days=STALE,
        rate=RATE, today=today,
    )
    kwargs.update(overrides)
    return lgb.assess(rows, flows, **kwargs)


# ── offline / insufficient_history ────────────────────────────────────────

def test_offline_when_snapshots_df_is_none():
    r = _assess(None, [], today="2026-10-01")
    assert r["state"] == "offline"


def test_empty_but_non_none_df_is_insufficient_history_not_offline():
    r = _assess([], [], today="2026-10-01")
    assert r["state"] == "insufficient_history"
    assert r["state"] != "offline"


def test_fewer_than_min_history_valid_rows_is_insufficient():
    rows = [_row(f"2026-09-{10+i:02d}", 7000 + i, 21000) for i in range(MIN_HIST - 1)]
    r = _assess(rows, [], today="2026-09-14")
    assert r["state"] == "insufficient_history"
    assert r["settled_days_available"] == MIN_HIST - 1


def test_exactly_min_history_evaluates_normally():
    rows = [_row(f"2026-09-{10+i:02d}", 5000, 15000) for i in range(MIN_HIST)]
    r = _assess(rows, [], today="2026-09-" + str(10 + MIN_HIST - 1))
    assert r["state"] != "insufficient_history"


def test_peak_older_than_lookback_window_excluded():
    # One very old, very high peak OUTSIDE the lookback window; a lower,
    # recent window should not see it.
    old_rows = [_row("2020-01-01", 50000.0, 55000.0)]
    recent = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST)]
    rows = old_rows + recent
    r = _assess(rows, [], today=f"2026-09-{10+MIN_HIST-1:02d}", lookback=MIN_HIST)
    # leverage = 15000/5000 = 3.0 > target, so this reaches the elevated
    # branch; the ancient 50000 peak must never appear.
    assert r["state"] in ("elevated", "giveback")
    assert r.get("peak_raw_equity") != 50000.0


# ── leverage vs target boundary ───────────────────────────────────────────

def test_leverage_exactly_at_target_is_not_elevated_with_no_reference_figures():
    net_equity = 5000.0
    gross_book = net_equity * TARGET
    rows = [_row(f"2026-09-{10+i:02d}", net_equity, gross_book) for i in range(MIN_HIST)]
    r = _assess(rows, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert r["state"] == "not_elevated"
    assert r.get("book_reduction") is None
    assert r.get("cash_to_target") is None


def test_leverage_just_above_target_is_elevated():
    net_equity = 5000.0
    gross_book = net_equity * (TARGET + 0.01)
    rows = [_row(f"2026-09-{10+i:02d}", net_equity, gross_book) for i in range(MIN_HIST)]
    r = _assess(rows, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert r["state"] == "elevated"


# ── unmeasured / equity_nonpositive / unlevered ───────────────────────────

def test_latest_net_equity_null_is_unmeasured_no_fallback_to_older_row():
    # MIN_HIST + 2 rows so that nulling the latest still leaves >= MIN_HIST
    # valid rows — otherwise insufficient_history (checked first) would mask
    # the thing this test actually wants to exercise.
    n = MIN_HIST + 2
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(n)]
    rows[-1] = dict(rows[-1])
    rows[-1]["net_equity"] = None
    r = _assess(rows, [], today=f"2026-09-{10+n-1:02d}")
    assert r["state"] == "unmeasured"


def test_latest_row_older_than_stale_days_is_unmeasured():
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST)]
    r = _assess(rows, [], today=f"2026-10-{10+STALE:02d}" if 10 + STALE <= 28 else "2026-10-28")
    assert r["state"] == "unmeasured"


def test_equity_nonpositive_never_collapses_to_not_elevated_or_unlevered():
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST - 1)]
    # Latest: margin loan >= stock book -> net_equity <= 0, with real (negative) cash present.
    rows.append(_row(f"2026-09-{10+MIN_HIST-1:02d}", -200.0, 15000.0))
    r = _assess(rows, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert r["state"] == "equity_nonpositive"
    assert r["state"] not in ("not_elevated", "unlevered")


def test_unlevered_when_cash_balance_non_negative():
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 4000.0) for i in range(MIN_HIST)]
    # cash = net_equity - gross_book = 1000 > 0 -> unlevered
    r = _assess(rows, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert r["state"] == "unlevered"
    assert lgb.disclosure_lines(r, lambda v: str(v)) == []


# ── null rows inside the window ───────────────────────────────────────────

def test_null_net_equity_row_inside_window_skipped_at_peak_never_zero():
    # 6 rows total (one null) so >= MIN_HIST (5) valid rows remain.
    rows = [
        _row("2026-09-09", 8900.0, 26700.0),
        _row("2026-09-10", 9000.0, 27000.0),
        {**_row("2026-09-11", 1.0, 3.0), "net_equity": None},  # would look like a crash to 0 if mishandled
        _row("2026-09-12", 8500.0, 25500.0),
        _row("2026-09-13", 6000.0, 18000.0),
        _row("2026-09-15", 5800.0, 17400.0),
    ]
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] in ("elevated", "giveback")
    # The peak must be 9000 (Sep 10), never anywhere near 0/1.
    assert r["peak_raw_equity"] == 9000.0


def test_null_second_latest_row_caps_result_at_elevated():
    # Build a window whose last two rows would otherwise confirm a giveback,
    # but the second-to-latest is null -> the chain cannot complete.
    # 6 rows total (one null) so >= MIN_HIST (5) valid rows remain.
    rows = [
        _row("2026-09-09", 10200.0, 30600.0),
        _row("2026-09-10", 10000.0, 30000.0),  # peak
        _row("2026-09-11", 9800.0, 29400.0),
        _row("2026-09-12", 9600.0, 28800.0),
        {**_row("2026-09-13", 1.0, 3.0), "net_equity": None},  # second-to-latest, null
        _row("2026-09-15", 8000.0, 24000.0),  # latest, qualifies alone (-20%)
    ]
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] == "elevated"
    assert r["state"] != "giveback"


# ── NaN vs None (reviewer BLOCKING #1) ─────────────────────────────────────
# `db.load_account_daily_snapshots()` returns a pandas DataFrame, and a
# DataFrame stores a NULL numeric column as `float('nan')`, not `None`.
# `nan is not None` is True, so a bare `.get(...) is not None` check (the
# bug this module shipped with) silently treats a NaN as a real number. The
# two dict-based tests above do NOT reproduce this — they pass a plain
# Python `None`, which the buggy code already handled correctly. Only a
# REAL DataFrame round-trip (`pd.DataFrame(rows).to_dict("records")`, which
# is exactly what `assess()` itself does internally) turns a missing value
# into NaN and reproduces the bug.

def test_nan_latest_net_equity_via_real_dataframe_is_unmeasured():
    n = MIN_HIST + 2
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(n)]
    rows[-1] = dict(rows[-1])
    rows[-1]["net_equity"] = None  # becomes NaN once it round-trips through a DataFrame
    df = pd.DataFrame(rows)
    assert pd.isna(df.iloc[-1]["net_equity"])  # confirm the DataFrame really did coerce it
    r = _assess(df, [], today=f"2026-09-{10+n-1:02d}")
    assert r["state"] == "unmeasured"
    # Confirm the buggy "nan is not None" path is gone — no stray "nan" in
    # any rendered figure (the reviewer's reproduction showed literal "nan"
    # strings like "Settled leverage nan×").
    lines = lgb.disclosure_lines(r, lambda v: f"${v:,.0f}" if v is not None else "—")
    assert "nan" not in " ".join(t for _k, t in lines).lower()


def test_nan_second_latest_row_via_real_dataframe_cannot_falsely_qualify():
    # Mirrors test_null_second_latest_row_caps_result_at_elevated, but via a
    # REAL pandas DataFrame (None -> NaN) so it actually reproduces the
    # reviewer's exact bug: pre-fix, `adj > nan` is always False (so the NaN
    # row never becomes a false PEAK — that part was already safe), but
    # `dd = nan` then `nan > drawdown_pct` is ALSO False, so the
    # confirm-days walk treated the NaN day as a QUALIFYING day (never
    # breaking the chain) rather than correctly refusing to confirm a
    # 2nd confirming day through it. The dict-based (plain `None`) sibling
    # test does NOT reproduce this — `None is not None` is already False
    # even before the fix, so it only exercises the already-correct path.
    rows = [
        _row("2026-09-08", 10200.0, 30600.0),  # peak
        _row("2026-09-09", 10000.0, 30000.0),
        _row("2026-09-10", 9800.0, 29400.0),
        _row("2026-09-11", 9600.0, 28800.0),
        dict(_row("2026-09-12", 1.0, 1.0), net_equity=None, gross_book=None, cash_balance=None, leverage=None),
        _row("2026-09-13", 8000.0, 24000.0),  # latest, qualifies alone (-20%)
    ]
    df = pd.DataFrame(rows)
    assert pd.isna(df.iloc[4]["net_equity"])
    r = _assess(df, [], today="2026-09-13")
    assert r["state"] == "elevated"
    assert r["state"] != "giveback"
    assert r["peak_raw_equity"] == 10200.0  # never poisoned by the NaN row


def test_nan_cash_balance_and_leverage_via_real_dataframe_do_not_crash_or_fabricate():
    """A NaN cash_balance/leverage on the latest row must degrade exactly
    like a missing one (recomputed from gross_book/net_equity), never
    silently pass a NaN through to a rendered ratio or an `>= 0` compare."""
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST)]
    rows[-1] = dict(rows[-1])
    rows[-1]["cash_balance"] = None
    rows[-1]["leverage"] = None
    df = pd.DataFrame(rows)
    assert pd.isna(df.iloc[-1]["cash_balance"])
    assert pd.isna(df.iloc[-1]["leverage"])
    r = _assess(df, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    # cash_balance NaN -> not >= 0 -> does not short-circuit to "unlevered";
    # leverage NaN -> recomputed from gross_book/net_equity = 15000/5000 = 3.0.
    assert r["state"] in ("elevated", "giveback")
    assert r["leverage"] == pytest.approx(3.0)


# ── drawdown inclusivity + confirm-day state machine ──────────────────────

def _confirm_fixture(dd_day_minus1_pct, dd_latest_pct, peak=10000.0):
    """2 confirm-day fixture: a flat peak day, then two days each at the
    given drawdown % vs that peak (by construction of adjusted==raw, no
    flows). The margin DEBT is held FIXED at its peak-day level (2026-10-02
    fix: as a real loan would be, absent a new trade) rather than scaled
    proportionally to equity — a fixed debt means leverage naturally RISES
    as equity falls, which is what lets 'giveback' (now requiring leverage
    to have actually risen versus the peak, not just equity to have fallen)
    fire on these fixtures at all. The old proportional-debt construction
    held leverage flat at 2.5x on every row by construction, which is a
    scenario the fix correctly refuses to call a 'giveback'."""
    debt = peak * 1.5  # peak-day leverage 2.5x, held fixed thereafter
    d_minus1 = peak * (1 + dd_day_minus1_pct / 100.0)
    d_latest = peak * (1 + dd_latest_pct / 100.0)
    rows = [
        _row("2026-09-10", peak, peak + debt),
        _row("2026-09-11", peak * 0.99, peak * 0.99 + debt),
        _row("2026-09-12", peak * 0.98, peak * 0.98 + debt),
        _row("2026-09-13", d_minus1, d_minus1 + debt),
        _row("2026-09-15", d_latest, d_latest + debt),
    ]
    return rows


def test_drawdown_exactly_at_threshold_qualifies_inclusive():
    rows = _confirm_fixture(dd_day_minus1_pct=DD_PCT, dd_latest_pct=DD_PCT)
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] == "giveback"


def test_drawdown_just_short_of_threshold_does_not_qualify():
    rows = _confirm_fixture(dd_day_minus1_pct=DD_PCT + 0.01, dd_latest_pct=DD_PCT + 0.01)
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] == "elevated"
    assert r["state"] != "giveback"


def test_single_qualifying_latest_row_after_nonqualifying_prior_is_elevated_not_giveback():
    rows = _confirm_fixture(dd_day_minus1_pct=-2.0, dd_latest_pct=-20.0)
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] == "elevated"
    assert r["first_day_past_mark"] is True


def test_two_consecutive_qualifying_rows_is_giveback():
    rows = _confirm_fixture(dd_day_minus1_pct=-15.0, dd_latest_pct=-20.0)
    r = _assess(rows, [], today="2026-09-15")
    assert r["state"] == "giveback"


def test_row_that_fails_after_confirmed_giveback_exits_immediately():
    # Day N-1, N: confirmed giveback. Day N+1: fully recovers above target
    # back toward not_elevated-adjacent territory (still elevated, plain).
    rows = _confirm_fixture(dd_day_minus1_pct=-15.0, dd_latest_pct=-20.0)
    rows.append(_row("2026-09-16", 10000.0, 10000.0 * 2.2))  # new near-peak, shallow leverage still > target
    r = _assess(rows, [], today="2026-09-16")
    assert r["state"] in ("elevated", "not_elevated")
    assert r["state"] != "giveback"


def test_each_confirm_day_measured_against_its_own_rolling_peak():
    """Mirrors the real 2026-09-16 -> 2026-09-17 near-miss: the peak itself
    resets mid-window, so the SAME equity value reads very differently
    depending on which day evaluates it."""
    def mk(date, net_equity, gross_book):
        return _row(date, net_equity, gross_book)

    # Sep 11's own peak is deliberately LOWER than Sep 17's real recovered
    # value (7909.41, given) so Sep 17 is a genuine NEW high, not a tie.
    PEAK = 7700.0
    base_rows = [
        mk("2026-09-11", PEAK, PEAK * 3.0),          # 5-day high
        mk("2026-09-12", 7600.0, 7600.0 * 3.0),
        mk("2026-09-13", 7500.0, 7500.0 * 3.0),
        mk("2026-09-15", PEAK * 0.98, PEAK * 0.98 * 3.0),  # ~-2%, non-qualifying
        # Real 2026-09-16 row (owner-verified):
        {
            "snapshot_date": "2026-09-16", "gross_book": 18662.64,
            "cash_balance": -13579.54, "net_equity": 5083.10,
            "leverage": 18662.64 / 5083.10, "call_distance_pct": None,
            "maintenance_rate": RATE,
        },
    ]
    r16 = _assess(base_rows, [], today="2026-09-16")
    assert r16["state"] == "elevated"
    assert r16["first_day_past_mark"] is True
    assert r16["n_day"] == 5
    assert r16["peak_date"] == "Sep 11"
    assert r16["drawdown_pct_now"] <= DD_PCT

    # Real 2026-09-17 row: fully recovers to a NEW peak -> drawdown resets to 0.
    rows17 = base_rows + [mk("2026-09-17", 7909.41, 7909.41 * 2.5)]
    r17 = _assess(rows17, [], today="2026-09-17")
    assert r17["state"] == "elevated"
    assert r17["first_day_past_mark"] is False
    assert r17["drawdown_pct_now"] == pytest.approx(0.0, abs=1e-9)
    assert r17["peak_date"] == "Sep 17"  # peak reset to itself


def test_confirm_day_requires_leverage_elevated_not_drawdown_alone():
    """Reviewer BLOCKING #4 repro: a prior day at 1.8x leverage (NOT
    elevated — below the 2.0x target) that happens to carry a qualifying
    drawdown must NOT count toward confirming today's giveback. Only today
    (2.2x, elevated) actually meets BOTH conditions — before the fix, the
    walk checked drawdown alone and let the 1.8x day count anyway, firing
    'giveback' on the very first elevated day."""
    rows = [
        _row("2026-09-10", 10000.0, 25000.0),  # peak, leverage 2.5
        _row("2026-09-11", 9800.0, 24500.0),   # leverage 2.5, dd ~-2%
        _row("2026-09-12", 9700.0, 24250.0),   # leverage 2.5, dd ~-3%
        _row("2026-09-13", 8000.0, 14400.0),   # leverage 1.8 (NOT elevated), dd -20%
        _row("2026-09-14", 7900.0, 17380.0),   # leverage 2.2 (elevated), dd -21%
    ]
    r = _assess(rows, [], today="2026-09-14")
    assert r["state"] == "elevated"
    assert r["state"] != "giveback"
    assert r["first_day_past_mark"] is True  # only ONE row (today) actually qualified


# ── flow adjustment ────────────────────────────────────────────────────────

def test_flows_none_sets_flows_checked_false_but_still_evaluates_unadjusted():
    rows = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST)]
    r = _assess(rows, None, today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert r["flows_checked"] is False
    assert r["state"] != "insufficient_history"
    assert r["state"] != "offline"


def test_withdrawal_after_peak_does_not_manufacture_artificial_drawdown():
    rows = [
        _row("2026-09-10", 10000.0, 25000.0),  # peak
        _row("2026-09-11", 9600.0, 24000.0),
        _row("2026-09-12", 9200.0, 23000.0),
        _row("2026-09-13", 8800.0, 22000.0),
        # Latest: a withdrawal of 2000 happened on Sep14 — raw equity drops
        # by exactly that withdrawal, no real market move.
        _row("2026-09-15", 8000.0, 20000.0),
    ]
    flows = [{"flow_date": "2026-09-14", "flow_type": "withdrawal", "amount": 2000.0}]
    r_adj = _assess(rows, flows, today="2026-09-15")
    r_noflow = _assess(rows, [], today="2026-09-15")
    # Adjusted drawdown must be SHALLOWER (less negative) than the unadjusted
    # read, since the withdrawal's effect is added back.
    assert r_adj["drawdown_pct_now"] > r_noflow["drawdown_pct_now"]


def test_deposit_after_peak_does_not_mask_a_real_loss():
    rows = [
        _row("2026-09-10", 10000.0, 25000.0),  # peak
        _row("2026-09-11", 9600.0, 24000.0),
        _row("2026-09-12", 9200.0, 23000.0),
        _row("2026-09-13", 8800.0, 22000.0),
        # A deposit of 2000 on Sep14 is masking what would otherwise be a
        # deeper real loss.
        _row("2026-09-15", 8000.0, 20000.0),
    ]
    flows = [{"flow_date": "2026-09-14", "flow_type": "deposit", "amount": 2000.0}]
    r_adj = _assess(rows, flows, today="2026-09-15")
    r_noflow = _assess(rows, [], today="2026-09-15")
    # Adjusted drawdown must be DEEPER (more negative) than the unadjusted
    # read, since the deposit's boost is removed.
    assert r_adj["drawdown_pct_now"] < r_noflow["drawdown_pct_now"]


def test_flow_adjusted_equity_series_handles_none_flows():
    rows = [{"snapshot_date": "2026-09-10", "net_equity": 1000.0},
            {"snapshot_date": "2026-09-11", "net_equity": 900.0}]
    adjusted, flows_checked = lgb.flow_adjusted_equity_series(rows, None)
    assert flows_checked is False
    assert adjusted == [1000.0, 900.0]  # unadjusted — raw passthrough


# ── leverage_change_split identity + real fixture ─────────────────────────

def test_leverage_change_split_identity_holds():
    E_pk, D_pk, E_now, D_now = 7964.06, 15787.12, 6516.68, 17818.64
    split = lgb.leverage_change_split(E_pk, D_pk, E_now, D_now)
    actual_delta = (1 + D_now / E_now) - (1 + D_pk / E_pk)
    assert split["from_equity"] + split["from_debt"] == pytest.approx(actual_delta, abs=1e-9)


def test_leverage_change_split_real_2026_09_23_to_09_30_numbers():
    """Real, owner-verified split from the approved mockup — equity-shrinkage
    (0.44x) is the LARGER contributor, loan-growth (0.31x) the smaller one."""
    E_pk, D_pk, E_now, D_now = 7964.06, 15787.12, 6516.68, 17818.64
    split = lgb.leverage_change_split(E_pk, D_pk, E_now, D_now)
    assert round(split["from_equity"], 4) == 0.4403
    assert round(split["from_debt"], 4) == 0.3117


def _giveback_fixture_sep23_to_sep30():
    E_pk, D_pk = 7964.06, 15787.12
    E_now, D_now = 6516.68, 17818.64

    def mk(date, net_equity, margin_debit):
        gross = net_equity + margin_debit
        return _row(date, net_equity, gross)

    rows = []
    for i, d in enumerate(range(16, 23)):
        eq = E_pk - (7 - i) * 50
        dr = D_pk - (7 - i) * 20
        rows.append(mk(f"2026-09-{d:02d}", eq, dr))
    rows.append(mk("2026-09-23", E_pk, D_pk))  # peak
    for i, d in enumerate(range(24, 29)):
        frac = (i + 1) / 6.0
        eq = E_pk - frac * (E_pk - E_now) * 0.6
        dr = D_pk + frac * (D_now - D_pk) * 0.6
        rows.append(mk(f"2026-09-{d:02d}", eq, dr))
    eq29 = E_pk - 0.85 * (E_pk - E_now)
    dr29 = D_pk + 0.85 * (D_now - D_pk)
    rows.append(mk("2026-09-29", eq29, dr29))
    rows.append(mk("2026-09-30", E_now, D_now))  # real latest
    return rows


def test_real_giveback_fixture_fires_on_sep30_with_real_split():
    rows = _giveback_fixture_sep23_to_sep30()
    r = _assess(rows, [], today="2026-09-30")
    assert r["state"] == "giveback"
    assert r["peak_date"] == "Sep 23"
    assert r["n_day"] == 15
    assert round(r["from_equity"], 4) == 0.4403
    assert round(r["from_debt"], 4) == 0.3117
    assert r["leverage"] == pytest.approx(3.7343, abs=1e-3)
    assert r["leverage_pk"] == pytest.approx(2.9823, abs=1e-3)


def test_giveback_requires_leverage_to_have_risen_not_just_drawdown():
    """Reviewer BLOCKING #2 repro (1st half): the owner REDUCED the book
    while equity still fell (leverage went DOWN, 3.5x -> 2.5x, not up) --
    'giveback' must never fire here, since its own headline unconditionally
    asserts 'Leverage rose... up from X', which would be backwards and
    false for a book that was actually de-levered."""
    def mk(date, net_equity, debit):
        return _row(date, net_equity, net_equity + debit)

    rows = [
        mk("2026-09-10", 8000.0, 20000.0),  # peak, leverage 3.5
        mk("2026-09-11", 7800.0, 15000.0),  # leverage 2.92, dd ~-2.5% (non-qualifying)
        mk("2026-09-12", 7600.0, 14000.0),  # leverage 2.84, dd -5% (non-qualifying)
        mk("2026-09-13", 6500.0, 13000.0),  # leverage 3.0, dd -18.75% (qualifies)
        mk("2026-09-14", 6000.0, 9000.0),   # leverage 2.5, dd -25% (qualifies) -- DE-levered
    ]
    r = _assess(rows, [], today="2026-09-14")
    assert r["state"] == "elevated"
    assert r["state"] != "giveback"


def test_giveback_in_call_disclosure_never_shows_false_room_remaining():
    """Reviewer BLOCKING #3 repro: an account already PAST the maintenance
    floor must never have its signed call_distance_pct abs()-ed into what
    reads as 'room remaining' -- margin.call_distance() returns a POSITIVE
    call_distance_pct exactly when already in_call, and abs()-ing that away
    silently hid an active call (the single highest-stakes bug here, given
    the owner's own real position sits close to a call)."""
    def mk(date, net_equity, debit):
        return _row(date, net_equity, net_equity + debit)

    rows = [
        mk("2026-09-01", 8000.0, 16000.0),  # peak, leverage 3.0
        mk("2026-09-02", 7000.0, 19000.0),  # leverage 3.71, dd -12.5% (qualifies)
        mk("2026-09-03", 6000.0, 24000.0),  # leverage 5.0, dd -25% (qualifies) -- past the floor
    ]
    r = _assess(rows, [], today="2026-09-03", min_history=3)
    assert r["state"] == "giveback"
    assert r["in_call_now"] is True

    lines = lgb.disclosure_lines(r, lambda v: f"${v:,.0f}" if v is not None else "—")
    full_text = " ".join(t for _k, t in lines)
    assert "already at or past the estimated maintenance floor" in full_text
    # Must NOT render a plain "X% at this close" figure implying room remains.
    assert "% at this close" not in full_text


def test_call_distance_now_prefers_live_recompute_over_stale_stored_value():
    """Reviewer non-blocking confirmation-pass finding: `in_call_now` already
    preferred the LIVE recompute (`cd_raw`), but `call_distance_now` itself
    was still sourced from the row's possibly-stale stored `call_distance_pct`
    first. If MARGIN_MAINTENANCE_RATE ever changed since the row was frozen,
    the two could disagree -- the exact #3 failure shape by another route.
    Construct a row whose STORED call_distance_pct is deliberately wrong
    (inconsistent with what the real gross_book/equity/debit + today's rate
    produce) and confirm the displayed figure is the live one, not the stale
    stored one."""
    def mk(date, net_equity, debit, stored_cd):
        return _row(date, net_equity, net_equity + debit, call_distance_pct=stored_cd)

    rows = [
        mk("2026-09-01", 8000.0, 16000.0, stored_cd=None),   # peak, leverage 3.0
        mk("2026-09-02", 7000.0, 19000.0, stored_cd=None),   # leverage 3.71, dd -12.5%
        # Latest row: real numbers put this in a call at today's RATE (0.25),
        # but the STORED call_distance_pct is a stale negative value implying
        # comfortable room remaining -- simulating a row frozen before a rate
        # change or a late same-day cash update.
        mk("2026-09-03", 6000.0, 24000.0, stored_cd=-15.0),  # leverage 5.0, dd -25%
    ]
    r = _assess(rows, [], today="2026-09-03", min_history=3)
    assert r["state"] == "giveback"

    live = lgb._margin.call_distance(30000.0, 6000.0, 24000.0, RATE)
    assert live["in_call"] is True
    # The displayed figure must match the LIVE recompute, not the stale -15.0
    # stored value.
    assert r["call_distance_now"] == pytest.approx(live["call_distance_pct"])
    assert r["call_distance_now"] != pytest.approx(-15.0)
    assert r["in_call_now"] is True

    lines = lgb.disclosure_lines(r, lambda v: f"${v:,.0f}" if v is not None else "—")
    full_text = " ".join(t for _k, t in lines)
    # Must take the in-call branch (per #3's fix), never show the stale -15.0.
    assert "already at or past the estimated maintenance floor" in full_text
    assert "-15.0" not in full_text and "15.0%" not in full_text


def test_giveback_deposit_after_peak_disclosure_flags_flow_adjustment():
    """Reviewer BLOCKING #2 repro (2nd half): a deposit landing after the
    peak can make the flow-adjusted drawdown % disagree with what the RAW
    dollar figures shown beside it would otherwise imply (the reviewer's
    own repro showed an identical $10,000-vs-$10,000 raw pair next to a 25%
    drawdown). The giveback headline must explicitly say the % is
    flow-adjusted whenever a net flow occurred since the peak, rather than
    silently presenting the raw dollars and the adjusted % as if they
    already agreed."""
    def mk(date, net_equity, debit):
        return _row(date, net_equity, net_equity + debit)

    rows = [
        mk("2026-09-20", 10000.0, 15000.0),  # peak, leverage 2.5
        mk("2026-09-21", 8700.0, 16200.0),   # leverage 2.86, raw dd -13% (qualifies)
        mk("2026-09-22", 7900.0, 17000.0),   # leverage 3.15, raw dd -21% (qualifies)
    ]
    flows = [{"flow_date": "2026-09-22", "flow_type": "deposit", "amount": 1000.0}]
    r = _assess(rows, flows, today="2026-09-22", min_history=3)
    assert r["state"] == "giveback"
    assert r["net_flow_since_peak"] == pytest.approx(1000.0)

    lines = lgb.disclosure_lines(r, lambda v: f"${v:,.0f}" if v is not None else "—")
    full_text = " ".join(t for _k, t in lines)
    assert "after adjusting for deposits/withdrawals" in full_text
    assert "raw, unadjusted figures" in full_text


# ── book_reduction_to_target / cash_to_target / call-distance-at-target ──

def test_book_reduction_and_cash_to_target_never_negative_and_none_guards():
    assert lgb.book_reduction_to_target(20000.0, 10000.0, 2.0) is None  # leverage == target
    assert lgb.book_reduction_to_target(15000.0, 10000.0, 2.0) is None  # leverage below target
    assert lgb.book_reduction_to_target(30000.0, 10000.0, 2.0) == pytest.approx(10000.0)
    assert lgb.book_reduction_to_target(30000.0, -500.0, 2.0) is None  # equity <= 0

    assert lgb.cash_to_target(20000.0, 10000.0, 2.0) is None
    assert lgb.cash_to_target(30000.0, 10000.0, 2.0) == pytest.approx(5000.0)
    assert lgb.cash_to_target(30000.0, 0.0, 2.0) is None


def test_call_distance_at_target_matches_direct_margin_call_distance():
    net_equity = 6516.68
    target = 2.0
    stock_value = target * net_equity
    margin_debit = net_equity * (target - 1.0)
    expected = margin.call_distance(stock_value, net_equity, margin_debit, RATE)["call_distance_pct"]

    got = lgb._call_distance_at_leverage(net_equity, target, RATE)
    assert got == pytest.approx(expected)

    rows = _giveback_fixture_sep23_to_sep30()
    r = _assess(rows, [], today="2026-09-30")
    assert r["call_distance_at_target"] == pytest.approx(expected)


# ── disclosure_lines: banned words + money_fmt plumbing ───────────────────

_BANNED = ("sell", "reduce", "trim", "should", "consider")


def _all_example_results():
    out = {}
    out["offline"] = _assess(None, [], today="2026-10-01")
    out["insufficient_history"] = _assess([], [], today="2026-10-01")
    rows_unmeasured = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST)]
    out["unmeasured"] = _assess(rows_unmeasured, [], today="2026-10-28")
    rows_nonpos = [_row(f"2026-09-{10+i:02d}", 5000.0, 15000.0) for i in range(MIN_HIST - 1)]
    rows_nonpos.append(_row(f"2026-09-{10+MIN_HIST-1:02d}", -200.0, 15000.0))
    out["equity_nonpositive"] = _assess(rows_nonpos, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    rows_unlev = [_row(f"2026-09-{10+i:02d}", 5000.0, 4000.0) for i in range(MIN_HIST)]
    out["unlevered"] = _assess(rows_unlev, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    rows_calm = [_row(f"2026-09-{10+i:02d}", 5000.0, 5000.0 * TARGET) for i in range(MIN_HIST)]
    out["not_elevated"] = _assess(rows_calm, [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    out["elevated_plain"] = _assess(
        _confirm_fixture(dd_day_minus1_pct=-2.0, dd_latest_pct=-2.0), [], today="2026-09-15"
    )
    out["elevated_first_day"] = _assess(
        _confirm_fixture(dd_day_minus1_pct=-2.0, dd_latest_pct=-20.0), [], today="2026-09-15"
    )
    out["giveback"] = _assess(_giveback_fixture_sep23_to_sep30(), [], today="2026-09-30")
    # Same states, with flows unavailable -> extra caveat line.
    out["elevated_no_flows"] = _assess(
        _confirm_fixture(dd_day_minus1_pct=-2.0, dd_latest_pct=-20.0), None, today="2026-09-15"
    )
    out["giveback_no_flows"] = _assess(_giveback_fixture_sep23_to_sep30(), None, today="2026-09-30")
    return out


_ALL_STATE_NAMES = [
    "offline", "insufficient_history", "unmeasured", "equity_nonpositive",
    "unlevered", "not_elevated", "elevated_plain", "elevated_first_day",
    "giveback", "elevated_no_flows", "giveback_no_flows",
]


@pytest.mark.parametrize("state_name", _ALL_STATE_NAMES)
def test_disclosure_lines_never_contain_banned_words(state_name):
    result = _all_example_results()[state_name]
    lines = lgb.disclosure_lines(result, lambda v: f"${v:,.0f}" if v is not None else "—")
    full_text = " ".join(text for _kind, text in lines).lower()
    for word in _BANNED:
        assert word not in full_text, f"state={state_name!r} line contains banned word {word!r}: {full_text}"


def test_disclosure_lines_dollar_amounts_go_through_injected_money_fmt():
    calls = []

    def fake_fmt(v):
        calls.append(v)
        return f"<<{v}>>"

    result = _assess(_giveback_fixture_sep23_to_sep30(), [], today="2026-09-30")
    lines = lgb.disclosure_lines(result, fake_fmt)
    full_text = " ".join(text for _kind, text in lines)
    assert calls, "money_fmt was never invoked"
    assert "<<" in full_text and ">>" in full_text


def test_flows_checked_false_caveat_appended_for_elevated_and_giveback_only():
    r_elev = _assess(_confirm_fixture(-2.0, -20.0), None, today="2026-09-15")
    lines_elev = lgb.disclosure_lines(r_elev, lambda v: str(v))
    assert any(k == "caveat" and "Deposits/withdrawals" in t for k, t in lines_elev)

    r_calm = _assess(
        [_row(f"2026-09-{10+i:02d}", 5000.0, 5000.0 * TARGET) for i in range(MIN_HIST)],
        None, today=f"2026-09-{10+MIN_HIST-1:02d}",
    )
    lines_calm = lgb.disclosure_lines(r_calm, lambda v: str(v))
    assert not any("Deposits/withdrawals" in t for _k, t in lines_calm)


def test_unlevered_renders_nothing():
    r = _assess([_row(f"2026-09-{10+i:02d}", 5000.0, 4000.0) for i in range(MIN_HIST)],
                [], today=f"2026-09-{10+MIN_HIST-1:02d}")
    assert lgb.disclosure_lines(r, lambda v: str(v)) == []


def test_elevated_new_high_wording_avoids_zero_percent_double_negative():
    """Non-blocking item #2: a day that IS the new high (drawdown rounds to
    0%) must say 'at its N-day high', never the confusing double-negative
    '0% below its N-day high'."""
    def mk(date, net_equity, gross_book):
        return _row(date, net_equity, gross_book)

    rows = [
        mk("2026-09-11", 7700.0, 7700.0 * 3.0),
        mk("2026-09-12", 7600.0, 7600.0 * 3.0),
        mk("2026-09-13", 7500.0, 7500.0 * 3.0),
        mk("2026-09-15", 7700.0 * 0.98, 7700.0 * 0.98 * 3.0),
        mk("2026-09-16", 7909.41, 7909.41 * 2.5),  # new high -> drawdown 0%
    ]
    r = _assess(rows, [], today="2026-09-16")
    assert r["drawdown_pct_now"] == pytest.approx(0.0, abs=1e-9)
    lines = lgb.disclosure_lines(r, lambda v: str(v))
    full_text = " ".join(t for _k, t in lines)
    assert "at its" in full_text and "day high" in full_text
    assert "0% below" not in full_text


# ── gate isolation: no _GATE_FILES module may import this module ─────────

_GATE_MODULES = [
    "stock_analyzer.risk_advisor",
    "stock_analyzer.exit_advisor",
    "stock_analyzer.daily_briefing",
    "stock_analyzer.portfolio",
    "stock_analyzer.risk",
    "stock_analyzer.scoring",
    "stock_analyzer.valuation",
    "stock_analyzer.technicals",
    "stock_analyzer.fundamentals",
    "stock_analyzer.ranking",
    "stock_analyzer.targets",
    "stock_analyzer.bundle_loader",
    "stock_analyzer.watchlist_advisor",
    "stock_analyzer.home_risk_synthesis",
    "stock_analyzer.beta_repair",
    "stock_analyzer.db",
    "stock_analyzer.cron_runner",
    "stock_analyzer.system_health",
    "stock_analyzer.broker_sync",
]


def _module_source_path(mod_name: str):
    """Resolve `mod_name` (a dotted "stock_analyzer.X" name) to its source
    file, with a fallback for the two modules in `_GATE_MODULES` that live
    at the REPO ROOT rather than inside a real `stock_analyzer` package
    submodule (`cron_runner.py`, and `app.py` if it's ever added here):
    `importlib.util.find_spec("stock_analyzer.cron_runner")` returns None
    for these (no such import path exists), which previously made the check
    silently return False — a false PASS, not a verified absence of the
    import. Confirmed via a direct grep that nothing currently imports this
    module except app.py and this test file, so this is a test-hygiene fix,
    not evidence of a live gate-isolation breach."""
    spec = importlib.util.find_spec(mod_name)
    if spec is not None and spec.origin is not None:
        return Path(spec.origin)
    if mod_name.startswith("stock_analyzer."):
        root_candidate = Path(__file__).resolve().parent.parent / f"{mod_name.rsplit('.', 1)[-1]}.py"
        if root_candidate.exists():
            return root_candidate
    return None


def _module_imports_leverage_giveback(mod_name: str) -> bool:
    """AST-level import-graph check (mirrors tests/test_margin.py's
    _module_imports_margin) — catches `from stock_analyzer import
    leverage_giveback` AND `import stock_analyzer.leverage_giveback` alias
    forms, not just a hasattr probe on the already-imported module object.
    Also catches the RELATIVE-import forms (`from . import
    leverage_giveback`, `from .leverage_giveback import X`) used by the
    normal in-package style inside stock_analyzer/ itself — an absolute-only
    check would miss exactly the import style a module living in
    stock_analyzer/ would actually use."""
    path = _module_source_path(mod_name)
    if path is None:
        return False
    src = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "stock_analyzer.leverage_giveback":
                return True
            if node.module == "stock_analyzer" and any(
                alias.name == "leverage_giveback" for alias in node.names
            ):
                return True
            if node.level and node.level > 0:
                # Relative forms, used from WITHIN stock_analyzer/ itself:
                #   `from .leverage_giveback import X`  -> module == "leverage_giveback"
                #   `from . import leverage_giveback`   -> module is None
                if node.module == "leverage_giveback":
                    return True
                if node.module is None and any(
                    alias.name == "leverage_giveback" for alias in node.names
                ):
                    return True
        if isinstance(node, ast.Import) and any(
            alias.name == "stock_analyzer.leverage_giveback" for alias in node.names
        ):
            return True
    return False


@pytest.mark.parametrize("mod_name", _GATE_MODULES)
def test_leverage_giveback_not_imported_by_any_gate_module(mod_name):
    assert not _module_imports_leverage_giveback(mod_name), (
        f"{mod_name} imports stock_analyzer.leverage_giveback — this module's "
        "awareness-only invariant (never a gate, never a score, never a "
        "suppression) would be broken by this import existing at all, "
        "regardless of which name or alias it's bound to"
    )
