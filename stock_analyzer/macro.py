"""
Macro regime detection and rate / sector-rotation sensitivity analysis.

All computation logic lives here; data fetching (yfinance) happens in app.py
so this module stays import-free and easily testable.

Proxies used:
  TLT  — iShares 20-Year Treasury ETF  (falls when long rates rise)
  SPY  — S&P 500 ETF                   (broad market trend)
  ^VIX — CBOE Volatility Index          (fear gauge)
"""

import pandas as pd

# ── Rate sensitivity per sector ───────────────────────────────────────────────
# +1.0 = big rate beneficiary, -1.0 = big rate victim
# Based on duration / earnings-growth dependency / credit exposure
RATE_SENSITIVITY = {
    "Semiconductors":  -0.70,  # high-growth, long-duration multiples
    "AI & Cloud":      -0.65,
    "AI & Data":       -0.65,
    "Consumer Tech":   -0.60,
    "Clean Energy":    -0.60,  # capital-intensive, subsidy-dependent
    "Cybersecurity":   -0.55,
    "EV & Auto":       -0.50,
    "Enterprise Tech": -0.45,
    "Healthcare":      -0.15,  # defensive, moderate sensitivity
    "Other":            0.00,
    "Defense":         +0.05,  # budget-driven, near rate-neutral
    "Energy":          +0.25,  # oil/inflation driven, mild positive
    "Financials":      +0.70,  # banks earn more on net interest margin
}

# ── Sector rotation playbook per macro regime ─────────────────────────────────
REGIME_FAVORED = {
    "rising_rates": {
        "overweight":  ["Financials", "Energy", "Defense"],
        "underweight": ["AI & Cloud", "Semiconductors", "Clean Energy", "Consumer Tech"],
        "reason": (
            "Rising rates compress growth-stock multiples (long-duration cash flows worth less today) "
            "and benefit banks via higher net interest margins and commodity producers via inflation pass-through."
        ),
    },
    "falling_rates": {
        "overweight":  ["Semiconductors", "AI & Cloud", "Clean Energy", "Consumer Tech"],
        "underweight": ["Financials", "Energy"],
        "reason": (
            "Falling rates expand growth-stock multiples, reduce borrowing costs for capex-heavy sectors, "
            "and compress bank margins."
        ),
    },
    "risk_off": {
        "overweight":  ["Healthcare", "Defense", "Energy"],
        "underweight": ["Semiconductors", "AI & Cloud", "Consumer Tech", "AI & Data"],
        "reason": (
            "Flight-to-safety bid lifts defensive sectors. High-beta growth sells off first "
            "as investors reduce risk exposure."
        ),
    },
    "risk_on": {
        "overweight":  ["Semiconductors", "AI & Cloud", "Consumer Tech", "AI & Data"],
        "underweight": ["Defense", "Healthcare"],
        "reason": (
            "Risk appetite supports high-beta growth names. "
            "Defensives lag as capital rotates to cyclicals and growth."
        ),
    },
    "neutral": {
        "overweight":  [],
        "underweight": [],
        "reason": "Mixed macro signals — no strong directional bias. Stock-picking over macro tilts.",
    },
}


def detect_macro_regime_legacy(
    tlt_ret: float,
    spy_ret: float,
    vix: float,
) -> dict:
    """
    Infer the current macro regime from three ETF proxies.

    Renamed from `detect_macro_regime` (2026-07-29 audit H1) to avoid import-name
    collision with `macro_calendar.detect_macro_regime` (the FRED-based, 7-signal
    successor used by Home/Risk Analysis/Regime Fit). This ETF-proxy read is kept as
    a deliberate independent secondary read on the manual "Macro Signals" panel — see
    the on-screen disclaimer at that panel's render site.

    tlt_ret : TLT 3-month price return (%)  — negative = rates rising
    spy_ret : SPY 3-month price return (%)  — direction of equities
    vix     : latest VIX close               — fear gauge

    Returns a regime dict:
      rate_env  : "rising_rates" | "falling_rates" | "neutral"
      risk_env  : "risk_on" | "risk_off" | "neutral"
      combined  : dominant regime key (matches REGIME_FAVORED keys)
      label     : human-readable label
      signals   : {name: description} for display
    """
    signals = {}

    # Rate direction: TLT price falls when rates rise
    if tlt_ret < -3:
        rate_env = "rising_rates"
        signals["Rates (TLT)"] = f"{tlt_ret:+.1f}% (3mo) — bond prices falling · rates rising"
    elif tlt_ret > 3:
        rate_env = "falling_rates"
        signals["Rates (TLT)"] = f"{tlt_ret:+.1f}% (3mo) — bond prices rising · rates falling"
    else:
        rate_env = "neutral"
        signals["Rates (TLT)"] = f"{tlt_ret:+.1f}% (3mo) — rates range-bound"

    # Risk appetite: VIX level
    if vix >= 25:
        risk_env = "risk_off"
        signals["Volatility (VIX)"] = f"{vix:.0f} — elevated fear · risk-off"
    elif vix <= 15:
        risk_env = "risk_on"
        signals["Volatility (VIX)"] = f"{vix:.0f} — low volatility · risk-on"
    else:
        risk_env = "neutral"
        signals["Volatility (VIX)"] = f"{vix:.0f} — moderate volatility"

    # Market trend signal
    if spy_ret >= 5:
        signals["Market (SPY)"] = f"{spy_ret:+.1f}% (3mo) — bull trend"
    elif spy_ret <= -5:
        signals["Market (SPY)"] = f"{spy_ret:+.1f}% (3mo) — bear trend"
    else:
        signals["Market (SPY)"] = f"{spy_ret:+.1f}% (3mo) — sideways"

    # Combine: rate signal takes precedence if strong; risk signal as tiebreaker
    if rate_env == "rising_rates":
        combined = "rising_rates"
        label = "Rising Rates / Tightening"
    elif rate_env == "falling_rates":
        combined = "falling_rates"
        label = "Falling Rates / Easing"
    elif risk_env == "risk_off":
        combined = "risk_off"
        label = "Risk-Off / Defensive"
    elif risk_env == "risk_on":
        combined = "risk_on"
        label = "Risk-On / Growth"
    else:
        combined = "neutral"
        label = "Neutral / Mixed Signals"

    return {
        "rate_env":  rate_env,
        "risk_env":  risk_env,
        "combined":  combined,
        "label":     label,
        "signals":   signals,
        "tlt_ret":   round(tlt_ret, 1),
        "spy_ret":   round(spy_ret, 1),
        "vix":       round(vix, 1),
    }


def portfolio_macro_exposure(port_df: pd.DataFrame, regime: dict) -> pd.DataFrame:
    """
    Score each holding's alignment with the detected macro regime.

    Returns a DataFrame with columns:
      Ticker, Sector, Weight (%), Rate Sensitivity, Macro Alignment, Icon
    sorted from most-headwind to most-tailwind.
    """
    combined = regime["combined"]
    favored  = REGIME_FAVORED.get(combined, REGIME_FAVORED["neutral"])
    over     = favored["overweight"]
    under    = favored["underweight"]

    rows = []
    for _, row in port_df.iterrows():
        sector   = row["Sector"]
        rate_s   = RATE_SENSITIVITY.get(sector, 0.0)

        if sector in over:
            alignment = "Tailwind ↑"
            icon = "🟢"
        elif sector in under:
            alignment = "Headwind ↓"
            icon = "🔴"
        else:
            alignment = "Neutral ↔"
            icon = "⬜"

        rows.append({
            "Ticker":           row["Ticker"],
            "Sector":           sector,
            "Weight (%)":       row["Weight (%)"],
            "Rate Sensitivity": rate_s,
            "Macro Alignment":  alignment,
            "Icon":             icon,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Rate Sensitivity")
    return df
