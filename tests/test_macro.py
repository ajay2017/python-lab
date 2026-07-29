"""Tests for stock_analyzer/macro.py — the legacy ETF-proxy regime detector
(`detect_macro_regime_legacy`) and the per-holding rate-sensitivity/alignment
scorer (`portfolio_macro_exposure`). Pure pandas/dict logic, no I/O.
Constants used (from stock_analyzer/constants.py): RISK_OFF_VIX_LEVEL=25.0,
RISK_ON_VIX_LEVEL=15.0, MACRO_LEGACY_TLT_RET_PCT=3.0,
MACRO_LEGACY_SPY_RET_PCT=5.0. Previously zero test coverage.
"""
import pandas as pd

from stock_analyzer import macro as mc
from stock_analyzer.constants import (
    MACRO_LEGACY_SPY_RET_PCT,
    MACRO_LEGACY_TLT_RET_PCT,
    RISK_OFF_VIX_LEVEL,
    RISK_ON_VIX_LEVEL,
)

# A "neutral" VIX reading that trips neither risk_off nor risk_on, used to
# isolate the rate_env signal in combined/label precedence tests.
_NEUTRAL_VIX = (RISK_OFF_VIX_LEVEL + RISK_ON_VIX_LEVEL) / 2


# ─── detect_macro_regime_legacy — rate_env at the TLT ±3.0 boundary ────────

def test_detect_macro_regime_rate_env_just_below_negative_boundary_is_rising_rates():
    r = mc.detect_macro_regime_legacy(tlt_ret=-(MACRO_LEGACY_TLT_RET_PCT + 0.01), spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["rate_env"] == "rising_rates"


def test_detect_macro_regime_rate_env_at_negative_boundary_is_neutral():
    r = mc.detect_macro_regime_legacy(tlt_ret=-MACRO_LEGACY_TLT_RET_PCT, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["rate_env"] == "neutral"


def test_detect_macro_regime_rate_env_just_above_positive_boundary_is_falling_rates():
    r = mc.detect_macro_regime_legacy(tlt_ret=MACRO_LEGACY_TLT_RET_PCT + 0.01, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["rate_env"] == "falling_rates"


def test_detect_macro_regime_rate_env_at_positive_boundary_is_neutral():
    r = mc.detect_macro_regime_legacy(tlt_ret=MACRO_LEGACY_TLT_RET_PCT, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["rate_env"] == "neutral"


# ─── detect_macro_regime_legacy — risk_env at the VIX RISK_OFF/RISK_ON boundaries ──

def test_detect_macro_regime_risk_env_at_risk_off_boundary_is_risk_off():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=RISK_OFF_VIX_LEVEL)
    assert r["risk_env"] == "risk_off"


def test_detect_macro_regime_risk_env_just_below_risk_off_boundary_is_neutral():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=RISK_OFF_VIX_LEVEL - 0.01)
    assert r["risk_env"] == "neutral"


def test_detect_macro_regime_risk_env_at_risk_on_boundary_is_risk_on():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=RISK_ON_VIX_LEVEL)
    assert r["risk_env"] == "risk_on"


def test_detect_macro_regime_risk_env_just_above_risk_on_boundary_is_neutral():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=RISK_ON_VIX_LEVEL + 0.01)
    assert r["risk_env"] == "neutral"


# ─── detect_macro_regime_legacy — SPY signal text (descriptive only, doesn't feed combined) ──

def test_detect_macro_regime_spy_signal_bull_trend_at_boundary():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=MACRO_LEGACY_SPY_RET_PCT, vix=_NEUTRAL_VIX)
    assert "bull trend" in r["signals"]["Market (SPY)"]


def test_detect_macro_regime_spy_signal_bear_trend_at_boundary():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=-MACRO_LEGACY_SPY_RET_PCT, vix=_NEUTRAL_VIX)
    assert "bear trend" in r["signals"]["Market (SPY)"]


def test_detect_macro_regime_spy_signal_sideways_between_boundaries():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert "sideways" in r["signals"]["Market (SPY)"]


def test_detect_macro_regime_spy_signal_never_changes_combined():
    # A strong bull SPY signal alone (rate + vix both neutral) still yields
    # combined == "neutral" -- SPY is descriptive-only.
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=20.0, vix=_NEUTRAL_VIX)
    assert r["combined"] == "neutral"


# ─── detect_macro_regime_legacy — combined/label precedence: all 5 outcomes ──

def test_detect_macro_regime_combined_rising_rates():
    r = mc.detect_macro_regime_legacy(tlt_ret=-5.0, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["combined"] == "rising_rates"
    assert r["label"] == "Rising Rates / Tightening"


def test_detect_macro_regime_combined_falling_rates():
    r = mc.detect_macro_regime_legacy(tlt_ret=5.0, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["combined"] == "falling_rates"
    assert r["label"] == "Falling Rates / Easing"


def test_detect_macro_regime_combined_risk_off():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=30.0)
    assert r["combined"] == "risk_off"
    assert r["label"] == "Risk-Off / Defensive"


def test_detect_macro_regime_combined_risk_on():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=10.0)
    assert r["combined"] == "risk_on"
    assert r["label"] == "Risk-On / Growth"


def test_detect_macro_regime_combined_neutral():
    r = mc.detect_macro_regime_legacy(tlt_ret=0.0, spy_ret=0.0, vix=_NEUTRAL_VIX)
    assert r["combined"] == "neutral"
    assert r["label"] == "Neutral / Mixed Signals"


def test_detect_macro_regime_combined_conflict_rate_wins_over_risk():
    # Rate signal says rising (rate_env=rising_rates) while VIX says risk_on
    # (risk_env=risk_on) -- rate precedence must win.
    r = mc.detect_macro_regime_legacy(tlt_ret=-5.0, spy_ret=0.0, vix=10.0)
    assert r["rate_env"] == "rising_rates"
    assert r["risk_env"] == "risk_on"
    assert r["combined"] == "rising_rates"


# ─── portfolio_macro_exposure — empty df ────────────────────────────────────

def test_portfolio_macro_exposure_empty_port_df_returns_empty_df():
    result = mc.portfolio_macro_exposure(pd.DataFrame(), {"combined": "rising_rates"})
    assert result.empty


# ─── portfolio_macro_exposure — alignment buckets ──────────────────────────

def _port_df():
    return pd.DataFrame({
        "Ticker": ["A", "B", "C", "D"],
        "Sector": ["Financials", "Semiconductors", "Healthcare", "Energy"],
        "Weight (%)": [10.0, 20.0, 30.0, 40.0],
    })


def test_portfolio_macro_exposure_overweight_sector_is_tailwind():
    result = mc.portfolio_macro_exposure(_port_df(), {"combined": "rising_rates"})
    row = result[result["Ticker"] == "A"].iloc[0]  # Financials -- overweight in rising_rates
    assert row["Macro Alignment"] == "Tailwind ↑"
    assert row["Icon"] == "🟢"


def test_portfolio_macro_exposure_underweight_sector_is_headwind():
    result = mc.portfolio_macro_exposure(_port_df(), {"combined": "rising_rates"})
    row = result[result["Ticker"] == "B"].iloc[0]  # Semiconductors -- underweight in rising_rates
    assert row["Macro Alignment"] == "Headwind ↓"
    assert row["Icon"] == "🔴"


def test_portfolio_macro_exposure_neither_overweight_nor_underweight_is_neutral():
    result = mc.portfolio_macro_exposure(_port_df(), {"combined": "rising_rates"})
    row = result[result["Ticker"] == "C"].iloc[0]  # Healthcare -- in neither list
    assert row["Macro Alignment"] == "Neutral ↔"
    assert row["Icon"] == "⬜"


def test_portfolio_macro_exposure_unrecognized_combined_falls_back_to_neutral():
    result = mc.portfolio_macro_exposure(_port_df(), {"combined": "not_a_real_regime"})
    assert (result["Macro Alignment"] == "Neutral ↔").all()


# ─── portfolio_macro_exposure — sort order: most-headwind to most-tailwind ──

def test_portfolio_macro_exposure_sorted_ascending_by_rate_sensitivity():
    result = mc.portfolio_macro_exposure(_port_df(), {"combined": "rising_rates"})
    sensitivities = result["Rate Sensitivity"].tolist()
    assert sensitivities == sorted(sensitivities)
    # Semiconductors (-0.70) most-headwind first, Financials (+0.70) last.
    assert result.iloc[0]["Sector"] == "Semiconductors"
    assert result.iloc[-1]["Sector"] == "Financials"
