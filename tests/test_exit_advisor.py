"""Regression tests for stock_analyzer/exit_advisor.py — the WATCH/TRIM/EXIT
deterioration tier logic (the "missing middle layer between Hold and a
score-collapse Sell" that a trade-log review found ~$1,465 of unflagged
realized loss behind — see docs/plans/exit-discipline.md). classify_deterioration_tier()
is the pure scalar decision core; these tests pin its tier boundaries and the
non-obvious interactions between them (ATR-scaled floors, settling grace,
relative-strength gating, the deep-drawdown EXIT shortcut) directly, since a
silent regression here is exactly the failure mode this module exists to close.
"""
import pandas as pd

from stock_analyzer.constants import (
    DETERIORATION_TRIM_DD_CEILING,
    DETERIORATION_EXIT_DD_CEILING,
    DETERIORATION_WATCH_DD_PCT,
)
from stock_analyzer.exit_advisor import (
    EXIT,
    TRIM,
    WATCH,
    _exit_floor,
    _trim_floor,
    assess_risk_off_derisk,
    classify_deterioration_tier,
    market_risk_posture,
    risk_off_regime,
)


def _classify(
    *,
    dd_from_peak_pct: float | None = 0.0,
    atr_pct: float = 0.0,
    trend_broken_now: bool = False,
    below_ma_count: int = 0,
    rel_strength: float = 0.0,
    price: float = 100.0,
    avg_cost: float = 100.0,
    dollar_pnl: float = 0.0,
    age_days: int | None = None,
):
    return classify_deterioration_tier(
        dd_from_peak_pct=dd_from_peak_pct,  # type: ignore[arg-type]  -- None is the function's own documented guard-clause input
        atr_pct=atr_pct,
        trend_broken_now=trend_broken_now,
        below_ma_count=below_ma_count,
        rel_strength=rel_strength,
        price=price,
        avg_cost=avg_cost,
        dollar_pnl=dollar_pnl,
        age_days=age_days,
    )


# ── _trim_floor / _exit_floor ────────────────────────────────────────────────

def test_trim_floor_uses_base_when_atr_is_low():
    assert _trim_floor(atr_pct=0.5) == 8.0  # DETERIORATION_TRIM_DD_PCT base


def test_trim_floor_scales_with_atr_but_caps_at_ceiling():
    assert _trim_floor(atr_pct=100.0) == DETERIORATION_TRIM_DD_CEILING


def test_exit_floor_scales_with_atr_but_caps_at_ceiling():
    assert _exit_floor(atr_pct=100.0) == DETERIORATION_EXIT_DD_CEILING


def test_exit_floor_always_at_or_above_trim_floor_across_atr_range():
    for atr in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 50.0):
        assert _exit_floor(atr) >= _trim_floor(atr)


# ── classify_deterioration_tier — guard clauses ─────────────────────────────

def test_classify_returns_none_when_dd_missing():
    assert _classify(dd_from_peak_pct=None) is None  # type: ignore[arg-type]  -- exercises the function's own None guard


def test_classify_returns_none_when_price_non_positive():
    assert _classify(price=0.0, dd_from_peak_pct=20.0, trend_broken_now=True) is None


def test_classify_returns_none_below_watch_floor():
    assert _classify(dd_from_peak_pct=DETERIORATION_WATCH_DD_PCT - 0.1, trend_broken_now=True) is None


# ── WATCH tier ────────────────────────────────────────────────────────────────

def test_watch_fires_on_drawdown_plus_trend_break():
    assert _classify(dd_from_peak_pct=7.0, trend_broken_now=True) == WATCH


def test_watch_is_rel_strength_independent():
    # WATCH must fire regardless of relative strength (docstring: RS-independent).
    assert _classify(dd_from_peak_pct=7.0, trend_broken_now=True, rel_strength=5.0) == WATCH


def test_watch_requires_trend_broken_now():
    assert _classify(dd_from_peak_pct=7.0, trend_broken_now=False) is None


def test_watch_suppressed_during_settling_grace():
    assert _classify(dd_from_peak_pct=7.0, trend_broken_now=True, age_days=5) is None


def test_watch_not_suppressed_once_past_settling_window():
    assert _classify(dd_from_peak_pct=7.0, trend_broken_now=True, age_days=10) == WATCH


# ── TRIM tier ─────────────────────────────────────────────────────────────────

def test_trim_fires_on_floor_plus_confirmation_plus_weak_rel_strength():
    assert _classify(
        dd_from_peak_pct=9.0, below_ma_count=2, rel_strength=-1.0, trend_broken_now=True,
    ) == TRIM


def test_trim_does_not_require_trend_broken_now_on_current_bar():
    # trim_active only checks dd/below_ma_count/rel_strength — a name that
    # bounced back above the MA today can still TRIM on a confirmed 2-of-3 break.
    assert _classify(
        dd_from_peak_pct=9.0, below_ma_count=2, rel_strength=-1.0, trend_broken_now=False,
    ) == TRIM


def test_trim_suppressed_when_rel_strength_non_negative():
    # Market-wide down day (not idiosyncratic) → falls back to WATCH, not TRIM.
    assert _classify(
        dd_from_peak_pct=9.0, below_ma_count=3, rel_strength=0.0, trend_broken_now=True,
    ) == WATCH


def test_trim_suppressed_without_confirmation_days():
    assert _classify(
        dd_from_peak_pct=9.0, below_ma_count=1, rel_strength=-1.0, trend_broken_now=True,
    ) == WATCH


def test_trim_suppressed_during_settling_grace():
    assert _classify(
        dd_from_peak_pct=9.0, below_ma_count=2, rel_strength=-1.0, trend_broken_now=True,
        age_days=5,
    ) is None


def test_high_atr_name_gets_more_room_before_trim():
    # Same 10% drawdown that TRIMs a quiet name only WATCHes a volatile one,
    # since the floor scales up with ATR (atr_pct=6 -> trim_floor capped at 14).
    assert _classify(
        dd_from_peak_pct=10.0, atr_pct=6.0, below_ma_count=3, rel_strength=-1.0,
        trend_broken_now=True,
    ) == WATCH


# ── EXIT tier ─────────────────────────────────────────────────────────────────

def _trim_active(
    *,
    price: float = 100.0,
    avg_cost: float = 100.0,
    dollar_pnl: float = 0.0,
    age_days: int | None = None,
):
    # Baseline that satisfies trim_active (dd past floor + confirmed + weak RS);
    # callers vary only the escalation-relevant fields.
    return _classify(
        dd_from_peak_pct=9.0, below_ma_count=2, rel_strength=-1.0, trend_broken_now=True,
        price=price, avg_cost=avg_cost, dollar_pnl=dollar_pnl, age_days=age_days,
    )


def test_exit_escalates_when_underwater_vs_cost():
    assert _trim_active(price=90.0, avg_cost=100.0) == EXIT


def test_exit_escalates_on_large_dollar_loss():
    assert _trim_active(dollar_pnl=-300.0) == EXIT


def test_exit_escalation_never_silenced_by_settling_grace():
    assert _trim_active(price=90.0, avg_cost=100.0, age_days=5) == EXIT


def test_deep_drawdown_exits_without_2of3_confirmation():
    # dd past the (uncapped) exit floor + trend broken now fires EXIT even with
    # zero below-MA confirmation and positive relative strength.
    assert _classify(
        dd_from_peak_pct=13.0, below_ma_count=0, rel_strength=5.0, trend_broken_now=True,
    ) == EXIT


def test_deep_drawdown_requires_trend_broken_now():
    # Depth alone, without today's bar being below the MA, does not shortcut EXIT.
    assert _classify(
        dd_from_peak_pct=13.0, below_ma_count=0, rel_strength=5.0, trend_broken_now=False,
    ) is None


# ── risk_off_regime ───────────────────────────────────────────────────────────

def _spy_df(closes):
    return pd.DataFrame({"Close": closes})


def test_risk_off_regime_calm_when_uptrend_and_low_vix():
    closes = [100.0 + i * 0.5 for i in range(250)]  # steady uptrend
    tripped, reasons = risk_off_regime(_spy_df(closes), vix_level=14.0, trend_ma=200, vix_threshold=25.0)
    assert tripped is False
    assert reasons == []


def test_risk_off_regime_trips_on_trend_break():
    closes = [200.0 - i * 0.5 for i in range(250)]  # steady downtrend
    tripped, reasons = risk_off_regime(_spy_df(closes), vix_level=14.0, trend_ma=200, vix_threshold=25.0)
    assert tripped is True
    assert any("average" in r for r in reasons)


def test_risk_off_regime_trips_on_high_vix_alone():
    closes = [100.0 + i * 0.5 for i in range(250)]  # uptrend, so trend leg stays clean
    tripped, reasons = risk_off_regime(_spy_df(closes), vix_level=30.0, trend_ma=200, vix_threshold=25.0)
    assert tripped is True
    assert any("VIX" in r for r in reasons)


def test_risk_off_regime_insufficient_history_skips_trend_leg_not_fabricated():
    closes = [100.0] * 10  # far short of trend_ma=200
    tripped, reasons = risk_off_regime(_spy_df(closes), vix_level=None, trend_ma=200, vix_threshold=25.0)
    assert tripped is False
    assert reasons == []


# ── market_risk_posture ───────────────────────────────────────────────────────

def test_market_risk_posture_withholds_on_missing_fragility():
    assert market_risk_posture(None, risk_off=True) is None
    assert market_risk_posture({"severity": "unknown"}, risk_off=True) is None


def test_market_risk_posture_calm_and_not_risk_off():
    result = market_risk_posture({"severity": "calm"}, risk_off=False)
    assert result is not None
    assert result["score"] == 0
    assert result["label"] == "Steady"
    assert result["armed"] is False


def test_market_risk_posture_caution_and_risk_off_is_armed():
    result = market_risk_posture({"severity": "caution"}, risk_off=True)
    assert result is not None
    assert result["score"] == 2
    assert result["armed"] is True


def test_market_risk_posture_fragile_alone_not_armed_without_regime():
    # Book fragility alone does NOT arm the de-risk action — needs regime confirmation too.
    result = market_risk_posture({"severity": "fragile"}, risk_off=False)
    assert result is not None
    assert result["armed"] is False
    assert result["score"] == 2


def test_market_risk_posture_fragile_and_risk_off_is_worst_case():
    result = market_risk_posture({"severity": "fragile"}, risk_off=True)
    assert result is not None
    assert result["score"] == 3
    assert result["label"] == "Risk-off & fragile"
    assert result["armed"] is True


# ── assess_risk_off_derisk — per-contributor "price" field (2026-08-05 bug fix) ─
# c["price"] was already computed internally (falls back to 0.0 on a missing
# "Price" row) and used for dollar_risk, but never exposed on the returned
# card dict, so downstream exit-signal persistence always wrote a NULL
# price_at_signal for RISK_OFF rows even when the row's price was known.

def _risk_off_port_df(rows):
    return pd.DataFrame([
        {
            "Ticker": r["ticker"], "Weight (%)": r.get("weight", 20.0),
            "Price": r.get("price"), "Shares": r.get("shares", 10.0),
            "P&L (%)": r.get("pnl_pct", 5.0),
        }
        for r in rows
    ])


_RISK_OFF_CLOSES = [200.0 - i * 0.5 for i in range(250)]  # trips the trend leg


def _risk_off_call(port_df, held_data):
    return assess_risk_off_derisk(
        port_df, held_data,
        fragility={"severity": "fragile"},
        spy_trend_df=_spy_df(_RISK_OFF_CLOSES),
        vix_level=14.0,
    )


def test_risk_off_card_exposes_known_price():
    port_df = _risk_off_port_df([{"ticker": "NVDA", "price": 123.456, "shares": 10}])
    held_data = {"NVDA": {"risk_metrics": {"beta": 1.8}}}
    cards = _risk_off_call(port_df, held_data)
    assert len(cards) == 1
    assert cards[0]["ticker"] == "NVDA"
    assert cards[0]["price"] == 123.46  # rounded to 2dp, matches dollar_risk's basis


def test_risk_off_card_price_none_when_missing():
    port_df = _risk_off_port_df([{"ticker": "NVDA", "price": None, "shares": 10}])
    held_data = {"NVDA": {"risk_metrics": {"beta": 1.8}}}
    cards = _risk_off_call(port_df, held_data)
    assert len(cards) == 1
    assert cards[0]["price"] is None  # never the silent-lie 0.0 fallback


def test_risk_off_card_price_none_when_non_positive():
    port_df = _risk_off_port_df([{"ticker": "NVDA", "price": 0.0, "shares": 10}])
    held_data = {"NVDA": {"risk_metrics": {"beta": 1.8}}}
    cards = _risk_off_call(port_df, held_data)
    assert len(cards) == 1
    assert cards[0]["price"] is None
