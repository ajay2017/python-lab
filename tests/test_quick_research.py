"""Tests for stock_analyzer/quick_research.py — entry-timing verdicts and the
portfolio-context bullet builder feeding the Daily Briefing "Research a Stock"
feature. Pure computation, no I/O. Constants used (from
stock_analyzer/constants.py): PORTFOLIO_BETA_ELEVATED=1.3,
TICKER_BETA_HIGH=1.5, TICKER_BETA_CRITICAL=1.8, SECTOR_CEILING=35.0,
SECTOR_ELEVATED=25.0. Previously zero test coverage.
"""
import pandas as pd
import pytest

from stock_analyzer import quick_research as qr


# ─── _entry_timing — high_risk_avoid tier, each trigger independently ────────

def test_entry_timing_rsi_80_alone_triggers_high_risk_avoid():
    r = qr._entry_timing(rsi_val=80.0, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] == "high_risk_avoid"


def test_entry_timing_rsi_just_below_80_does_not_trigger_tier1():
    r = qr._entry_timing(rsi_val=79.9, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] != "high_risk_avoid"


def test_entry_timing_move1d_15_alone_triggers_high_risk_avoid():
    r = qr._entry_timing(rsi_val=50.0, move_1d=15.0, move_5d=0.0)
    assert r["verdict"] == "high_risk_avoid"


def test_entry_timing_move5d_25_alone_triggers_high_risk_avoid():
    r = qr._entry_timing(rsi_val=50.0, move_1d=0.0, move_5d=25.0)
    assert r["verdict"] == "high_risk_avoid"


# ─── _entry_timing — wait_pullback tier, each trigger independently ──────────

def test_entry_timing_rsi_68_alone_triggers_wait_pullback():
    r = qr._entry_timing(rsi_val=68.0, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] == "wait_pullback"


def test_entry_timing_move1d_5_alone_triggers_wait_pullback():
    r = qr._entry_timing(rsi_val=50.0, move_1d=5.0, move_5d=0.0)
    assert r["verdict"] == "wait_pullback"


def test_entry_timing_move5d_12_alone_triggers_wait_pullback():
    r = qr._entry_timing(rsi_val=50.0, move_1d=0.0, move_5d=12.0)
    assert r["verdict"] == "wait_pullback"


def test_entry_timing_just_below_wait_pullback_thresholds_falls_through():
    r = qr._entry_timing(rsi_val=67.9, move_1d=4.9, move_5d=11.9)
    assert r["verdict"] == "buy_now"


# ─── _entry_timing — oversold tier, gated on rsi_val is not None ─────────────

def test_entry_timing_rsi_35_triggers_oversold():
    r = qr._entry_timing(rsi_val=35.0, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] == "oversold"


def test_entry_timing_rsi_just_above_35_is_buy_now():
    r = qr._entry_timing(rsi_val=35.1, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] == "buy_now"


def test_entry_timing_rsi_none_never_hits_oversold_even_though_default_is_50():
    # rsi_val=None defaults internally to 50.0 for tier1/2 checks, but tier3
    # ("oversold") is explicitly gated on `rsi_val is not None` -- a None
    # input can never land in oversold no matter the default.
    r = qr._entry_timing(rsi_val=None, move_1d=0.0, move_5d=0.0)
    assert r["verdict"] == "buy_now"


# ─── _entry_timing — first-matching-tier-wins precedence ────────────────────

def test_entry_timing_rsi80_makes_lower_tier_conditions_moot():
    # rsi=80 alone satisfies tier 1; move_1d/move_5d values that would also
    # satisfy tier 2 are irrelevant since tier 1 is checked (and wins) first.
    r = qr._entry_timing(rsi_val=80.0, move_1d=6.0, move_5d=13.0)
    assert r["verdict"] == "high_risk_avoid"


# ─── _entry_timing — "why" reason list construction (high_risk_avoid) ───────

def test_entry_timing_why_includes_surge_clause_when_move1d_high():
    r = qr._entry_timing(rsi_val=50.0, move_1d=20.0, move_5d=0.0)
    assert "surged" in r["explanation"]
    assert "over 5 days" not in r["explanation"]


def test_entry_timing_why_includes_rsi_clause_when_rsi_high():
    r = qr._entry_timing(rsi_val=85.0, move_1d=0.0, move_5d=0.0)
    assert "severely overbought" in r["explanation"]


def test_entry_timing_why_includes_5day_clause_only_when_move5d_high_and_move1d_low():
    r = qr._entry_timing(rsi_val=50.0, move_1d=0.0, move_5d=30.0)
    assert "over 5 days" in r["explanation"]


def test_entry_timing_why_excludes_5day_clause_when_move1d_also_high():
    # move_5d>=25 AND move_1d>=15 -- the 5-day clause is suppressed per the
    # `if move_5d>=25 and move_1d<15` guard (move_1d already told the story).
    r = qr._entry_timing(rsi_val=50.0, move_1d=16.0, move_5d=30.0)
    assert "over 5 days" not in r["explanation"]
    assert "surged" in r["explanation"]


def test_entry_timing_why_all_three_clauses_present():
    r = qr._entry_timing(rsi_val=85.0, move_1d=0.0, move_5d=30.0)
    assert "RSI 85" in r["explanation"]
    assert "over 5 days" in r["explanation"]


# ─── _portfolio_bullet — priority tier order ─────────────────────────────────

def _full_ctx():
    """Ctx satisfying tiers 1+2+3+4 simultaneously -- used to prove tier 1
    wins outright."""
    return {
        "act_today_flags": [{"reason": "Stop breached", "action": "Sell"}],
        "sector_of_ticker": "Technology",
        "sector_act_today": [{"ticker": "OTHER"}],
        "held": True,
        "held_shares": 10.0,
        "held_avg_cost": 50.0,
        "held_pnl_pct": 5.0,
        "held_signal": "Hold",
        "sector_weight_pct": 40.0,
        "portfolio_beta": 1.5,
        "ticker_beta": 2.0,
    }


def test_portfolio_bullet_tier1_act_today_wins_over_all_others():
    bullet = qr._portfolio_bullet("XYZ", _full_ctx())
    assert "Act Today" in bullet
    assert "Sell" in bullet


def test_portfolio_bullet_tier2_sector_stress_wins_when_no_tier1():
    ctx = _full_ctx()
    ctx["act_today_flags"] = []
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "Sector Under Stress" in bullet


def test_portfolio_bullet_tier3_held_wins_when_no_tier1_or_2():
    ctx = _full_ctx()
    ctx["act_today_flags"] = []
    ctx["sector_act_today"] = []
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "Your Position" in bullet


def test_portfolio_bullet_tier4_new_position_wins_when_none_above():
    ctx = _full_ctx()
    ctx["act_today_flags"] = []
    ctx["sector_act_today"] = []
    ctx["held"] = False
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "Portfolio Fit" in bullet


# ─── _portfolio_bullet — tier2 excludes THIS ticker from the "other" list ────

def test_portfolio_bullet_tier2_excludes_this_ticker_from_sector_acts():
    ctx = {
        "act_today_flags": [],
        "sector_of_ticker": "Technology",
        "sector_act_today": [{"ticker": "XYZ"}],  # only itself -- no "others"
        "held": False,
    }
    bullet = qr._portfolio_bullet("XYZ", ctx)
    # No other tickers under stress -> falls through to tier 3/4, not tier 2.
    assert "Sector Under Stress" not in bullet


def test_portfolio_bullet_tier2_more_than_3_others_shows_plus_count():
    ctx = {
        "act_today_flags": [],
        "sector_of_ticker": "Technology",
        "sector_act_today": [{"ticker": t} for t in ["A", "B", "C", "D", "E"]],
        "held": False,
    }
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "+2 more" in bullet


# ─── _portfolio_bullet — reason truncation at 110 chars ──────────────────────

def test_portfolio_bullet_reason_untruncated_when_short():
    reason = "Stop breached at $45."
    ctx = {"act_today_flags": [{"reason": reason, "action": "Sell"}]}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert reason in bullet


def test_portfolio_bullet_reason_truncated_when_over_110_chars():
    reason = "A" * 150 + "."
    ctx = {"act_today_flags": [{"reason": reason, "action": "Sell"}]}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "…" in bullet
    assert "A" * 150 not in bullet


# ─── _portfolio_bullet — held branch note logic ──────────────────────────────

def test_portfolio_bullet_held_sell_signal_gives_reduce_note():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": True,
           "held_shares": 10.0, "held_avg_cost": 50.0, "held_signal": "Sell"}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "reducing" in bullet


def test_portfolio_bullet_held_avoid_signal_gives_reduce_note():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": True,
           "held_shares": 10.0, "held_avg_cost": 50.0, "held_signal": "Avoid"}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "reducing" in bullet


def test_portfolio_bullet_held_hold_signal_gives_monitor_note():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": True,
           "held_shares": 10.0, "held_avg_cost": 50.0, "held_signal": "Hold"}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "monitor" in bullet.lower()


def test_portfolio_bullet_held_neither_signal_gives_no_note():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": True,
           "held_shares": 10.0, "held_avg_cost": 50.0, "held_signal": "Buy"}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert bullet.rstrip().endswith("**Buy**.")


def test_portfolio_bullet_held_shares_avg_cost_fallback_when_missing():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": True,
           "held_shares": None, "held_avg_cost": None, "held_signal": "Hold"}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "Already held" in bullet


# ─── _portfolio_bullet — new-position sector-ceiling 3-way branch ────────────

def test_portfolio_bullet_new_position_sector_at_ceiling_boundary():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "sector_of_ticker": "Tech", "sector_weight_pct": 35.0}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "over-concentrate" in bullet


def test_portfolio_bullet_new_position_sector_just_below_ceiling_is_elevated():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "sector_of_ticker": "Tech", "sector_weight_pct": 34.9}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "moderate concentration" in bullet


def test_portfolio_bullet_new_position_sector_at_elevated_boundary():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "sector_of_ticker": "Tech", "sector_weight_pct": 25.0}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "moderate concentration" in bullet


def test_portfolio_bullet_new_position_sector_below_elevated_has_room():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "sector_of_ticker": "Tech", "sector_weight_pct": 10.0}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "room to add" in bullet


def test_portfolio_bullet_new_position_sector_zero_weight_not_currently_held():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "sector_of_ticker": "Tech", "sector_weight_pct": 0.0}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "not currently held" in bullet


# ─── _portfolio_bullet — new-position beta-fit 2-tier branch ────────────────

def test_portfolio_bullet_new_position_beta_critical_and_port_elevated():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "portfolio_beta": 1.31, "ticker_beta": 1.81}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "already elevated portfolio beta" in bullet


def test_portfolio_bullet_new_position_beta_high_alone():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "portfolio_beta": 1.0, "ticker_beta": 1.51}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "High beta" in bullet


def test_portfolio_bullet_new_position_no_concerns_fallback():
    ctx = {"act_today_flags": [], "sector_act_today": [], "held": False,
           "portfolio_beta": 1.0, "ticker_beta": 1.0}
    bullet = qr._portfolio_bullet("XYZ", ctx)
    assert "No concentration or beta concerns" in bullet


# ─── research_ticker — fundamentals-gated bullet 1 + score/signal withheld ───

def _base_data(close_values, rsi=None, fundamentals_available=True):
    df = pd.DataFrame({"Close": close_values})
    if rsi is not None:
        df["RSI"] = rsi
    return {
        "df": df,
        "t_signals": {"MA Trend": "Price above rising 50-day MA — bullish"},
        "rec": {"icon": "✅", "label": "Buy", "rationale": "Solid setup", "color": "#22c55e"},
        "total": 72.0,
        "current_price": close_values[-1],
        "financials": {"analyst_target": None, "short_pct_float": None},
        "revisions": {"net": 0},
        "earnings": None,
        "fundamentals_available": fundamentals_available,
        "name": "Test Co",
        "sector": "Technology",
        "headlines": ["h1", "h2", "h3", "h4"],
    }


def test_research_ticker_fundamentals_unavailable_withholds_score_and_signal():
    data = _base_data([100, 101, 102], fundamentals_available=False)
    result = qr.research_ticker("XYZ", data)
    assert result["score"] is None
    assert result["signal"] == "Verdict withheld"
    assert result["signal_color"] == "#dc2626"
    assert "withheld" in result["bullets"][0]


def test_research_ticker_fundamentals_available_shows_real_score():
    data = _base_data([100, 101, 102], fundamentals_available=True)
    result = qr.research_ticker("XYZ", data)
    assert result["score"] == 72.0
    assert result["signal"] == "Buy"


# ─── research_ticker — momentum move calc len(close) guards ─────────────────

def test_research_ticker_exactly_2_rows_gives_move1d_but_not_move5d():
    data = _base_data([100.0, 110.0])
    result = qr.research_ticker("XYZ", data)
    assert result["move_1d"] == pytest.approx(10.0)
    assert result["move_5d"] == 0.0


def test_research_ticker_1_row_gives_zero_move1d():
    data = _base_data([100.0])
    result = qr.research_ticker("XYZ", data)
    assert result["move_1d"] == 0.0


def test_research_ticker_6_rows_gives_real_move5d_not_move1m():
    closes = [100.0, 101, 102, 103, 104, 110.0]
    data = _base_data(closes)
    result = qr.research_ticker("XYZ", data)
    assert result["move_5d"] != 0.0
    assert result["move_1m"] == 0.0


def test_research_ticker_22_rows_gives_real_move1m():
    closes = [100.0 + i for i in range(22)]
    data = _base_data(closes)
    result = qr.research_ticker("XYZ", data)
    assert result["move_1m"] != 0.0


# ─── research_ticker — trend_short bucket mapping ────────────────────────────

def test_research_ticker_trend_strong_uptrend():
    data = _base_data([100, 101, 102])
    data["t_signals"] = {"MA Trend": "Strong Uptrend — accelerating"}
    result = qr.research_ticker("XYZ", data)
    assert result["trend"] == "Strong Uptrend"


def test_research_ticker_trend_uptrend_bullish():
    data = _base_data([100, 101, 102])
    data["t_signals"] = {"MA Trend": "bullish crossover"}
    result = qr.research_ticker("XYZ", data)
    assert result["trend"] == "Uptrend"


def test_research_ticker_trend_downtrend_below():
    data = _base_data([100, 101, 102])
    data["t_signals"] = {"MA Trend": "Price below falling MA"}
    result = qr.research_ticker("XYZ", data)
    assert result["trend"] == "Downtrend"


def test_research_ticker_trend_mixed_fallback():
    data = _base_data([100, 101, 102])
    data["t_signals"] = {"MA Trend": "Choppy sideways action"}
    result = qr.research_ticker("XYZ", data)
    assert result["trend"] == "Mixed"


# ─── research_ticker — key-context bullet (bullet 4) optional clauses ───────

def test_research_ticker_key_context_no_signals_fallback():
    data = _base_data([100, 101, 102])
    result = qr.research_ticker("XYZ", data)
    assert "No additional signals" in result["bullets"][3]


def test_research_ticker_key_context_earnings_imminent_clause():
    data = _base_data([100, 101, 102])
    data["earnings"] = "2026-08-01"
    result = qr.research_ticker("XYZ", data)
    assert "Earnings" in result["bullets"][3]


def test_research_ticker_key_context_upgrades_net_2_clause():
    data = _base_data([100, 101, 102])
    data["revisions"] = {"net": 2, "upgrades_90d": 3, "downgrades_90d": 1}
    result = qr.research_ticker("XYZ", data)
    assert "upgrades" in result["bullets"][3]


def test_research_ticker_key_context_downgrades_net_neg2_clause():
    data = _base_data([100, 101, 102])
    data["revisions"] = {"net": -2, "upgrades_90d": 0, "downgrades_90d": 2}
    result = qr.research_ticker("XYZ", data)
    assert "downgrades" in result["bullets"][3]


def test_research_ticker_key_context_upside_pct_clause():
    data = _base_data([100.0, 100.0, 100.0])
    data["financials"] = {"analyst_target": 120.0, "short_pct_float": None}
    result = qr.research_ticker("XYZ", data)
    assert "target" in result["bullets"][3]
    assert result["upside_pct"] == pytest.approx(20.0)


def test_research_ticker_key_context_short_interest_clause_above_15():
    data = _base_data([100, 101, 102])
    data["financials"] = {"analyst_target": None, "short_pct_float": 20.0}
    result = qr.research_ticker("XYZ", data)
    assert "Short interest" in result["bullets"][3]


def test_research_ticker_key_context_short_interest_at_15_not_included():
    data = _base_data([100, 101, 102])
    data["financials"] = {"analyst_target": None, "short_pct_float": 15.0}
    result = qr.research_ticker("XYZ", data)
    assert "Short interest" not in result["bullets"][3]


# ─── research_ticker — portfolio_ctx None vs {} vs dict ──────────────────────

def test_research_ticker_portfolio_ctx_none_omits_5th_bullet():
    data = _base_data([100, 101, 102])
    result = qr.research_ticker("XYZ", data, portfolio_ctx=None)
    assert len(result["bullets"]) == 4


def test_research_ticker_portfolio_ctx_empty_dict_adds_5th_bullet():
    data = _base_data([100, 101, 102])
    result = qr.research_ticker("XYZ", data, portfolio_ctx={})
    assert len(result["bullets"]) == 5


# ─── research_ticker — headlines truncated to 3 ──────────────────────────────

def test_research_ticker_headlines_truncated_to_3():
    data = _base_data([100, 101, 102])
    result = qr.research_ticker("XYZ", data)
    assert len(result["headlines"]) == 3


# ─── research_ticker — entry-timing bullet delegates to _entry_timing ───────

def test_research_ticker_entry_bullet_matches_entry_timing_output():
    data = _base_data([100, 101, 102], rsi=80.0)
    result = qr.research_ticker("XYZ", data)
    assert result["entry"]["verdict"] == "high_risk_avoid"
    assert "High Risk" in result["bullets"][2]
