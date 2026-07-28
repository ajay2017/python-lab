"""Regression tests for stock_analyzer/earnings_advisor.py — the
Pre-Earnings Playbook: per-position EXIT/REDUCE/MONITOR/HOLD/HOLD_OR_ADD
recommendation ladder ahead of a binary earnings event, plus the watchlist
earnings-catalyst candidate scanner. Pure computation (date math only, no
I/O). See docs/plans/test-automation.md for scope.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import earnings_advisor as ea
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
    EARNINGS_BEARISH_REACTION_COMPOSITE_GATE,
    EARNINGS_BEAT_RATE_REDUCE_THRESHOLD,
    EARNINGS_BEAT_RATE_STRONG_THRESHOLD,
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_MIN_BEAT_RATE_ENTRY,
    EARNINGS_URGENCY_SOON_DAYS,
    SINGLE_NAME_CEILING,
    SINGLE_NAME_TRIM_TRIGGER,
)

TODAY = date(2026, 7, 28)


# ── _f ────────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert ea._f(None) == 0.0
    assert ea._f(None, default=5) == 5


def test_f_nan_returns_default():
    assert ea._f(float("nan"), default=-1) == -1


def test_f_unparseable_returns_default():
    assert ea._f("bad", default=2) == 2


def test_f_parses_valid_value():
    assert ea._f("3.5") == 3.5


# ── _estimate_move ─────────────────────────────────────────────────────────

def test_estimate_move_clamped_at_floor():
    # var95=0 -> var95*3=0, floored to 3.0, which is <=3.1 -> sector fallback.
    result = ea._estimate_move({"var_95": 0.0}, "Semiconductors")
    assert result == 10.0  # sector default


def test_estimate_move_uses_var95_times_3_when_above_fallback_band():
    result = ea._estimate_move({"var_95": -5.0}, "Semiconductors")
    assert result == 15.0  # abs(-5)*3 = 15, above the 3.1 fallback threshold


def test_estimate_move_clamped_at_ceiling():
    result = ea._estimate_move({"var_95": -50.0}, "Semiconductors")
    assert result == 25.0


def test_estimate_move_unknown_sector_fallback_is_7():
    result = ea._estimate_move({"var_95": 0.0}, "Nonexistent Sector")
    assert result == 7.0


def test_estimate_move_missing_var95_falls_back_to_sector():
    result = ea._estimate_move({}, "Healthcare")
    assert result == 8.0


# ── _recommend — priority ladder ───────────────────────────────────────────

def _rec(days=10, score=60.0, weight=8.0, pnl_pct=0.0, gap_to_stop=10.0,
         net_rev=0, signal="Hold", shares=100, market_value=10_000.0,
         est_move=5.0, beat_rate=None, reaction=None):
    return ea._recommend(days, score, weight, pnl_pct, gap_to_stop, net_rev,
                          signal, shares, market_value, est_move,
                          beat_rate=beat_rate, reaction=reaction)


def test_recommend_exit_on_sell_signal():
    action, priority, detail, lens = _rec(signal="Sell")
    assert action == "EXIT"
    assert priority == "HIGH"


def test_recommend_exit_beats_everything_else():
    # Even an oversized, high-conviction position with a Sell signal exits.
    action, _, _, _ = _rec(signal="Strong Sell", weight=25.0, score=90.0, net_rev=5)
    assert action == "EXIT"


def test_recommend_reduce_oversized_position():
    action, priority, detail, lens = _rec(weight=20.0, market_value=20_000.0, shares=200)
    assert action == "REDUCE"
    assert priority == "HIGH"
    assert "too concentrated" in detail


def test_recommend_reduce_oversized_trims_to_ceiling():
    # weight=20, target=15 -> trim_frac = (20-15)/20 = 0.25 -> trim 25% of shares
    action, _, detail, _ = _rec(weight=20.0, shares=200, market_value=20_000.0)
    assert action == "REDUCE"
    assert "sell 50 shares" in detail  # 200 * 0.25 = 50


def test_recommend_reduce_beats_weak_fundamentals_reduce():
    # weight=20 (oversized) AND score<44 (weak) -- oversized check fires first.
    action, priority, detail, _ = _rec(weight=20.0, score=30.0, shares=200, market_value=20_000.0)
    assert "too concentrated" in detail


def test_recommend_reduce_weak_fundamentals():
    action, priority, detail, _ = _rec(score=40.0, weight=8.0)
    assert action == "REDUCE"
    assert "Sell zone" in detail


def test_recommend_weak_fundamentals_requires_weight_at_least_5():
    action, _, _, _ = _rec(score=40.0, weight=4.9)
    assert action != "REDUCE"


def test_recommend_reduce_negative_revisions():
    action, priority, detail, _ = _rec(net_rev=-2, weight=8.0, score=60.0)
    assert action == "REDUCE"
    assert priority == "MEDIUM"
    assert "cutting estimates" in detail


def test_recommend_negative_revisions_beats_poor_beat_rate():
    # Both net_rev<=-2 (checked earlier) and beat_rate<60/score<65 apply --
    # negative-revisions branch must win since it's checked first.
    action, _, detail, _ = _rec(net_rev=-3, weight=8.0, score=50.0, beat_rate=40.0)
    assert "cutting estimates" in detail


def test_recommend_reduce_poor_beat_rate_and_weak_composite():
    action, priority, detail, _ = _rec(
        weight=8.0, score=50.0, beat_rate=EARNINGS_BEAT_RATE_REDUCE_THRESHOLD - 1,
    )
    assert action == "REDUCE"
    assert priority == "MEDIUM"
    assert "beat rate" in detail


def test_recommend_poor_beat_rate_requires_score_below_composite_buy():
    action, _, _, _ = _rec(
        weight=8.0, score=COMPOSITE_BUY, beat_rate=EARNINGS_BEAT_RATE_REDUCE_THRESHOLD - 1,
    )
    assert action != "REDUCE"


def test_recommend_beat_rate_none_skips_that_branch():
    action, _, _, _ = _rec(weight=8.0, score=50.0, beat_rate=None)
    # No REDUCE from beat-rate branch (beat_rate is None); falls through to
    # whatever the next applicable check is (MONITOR/HOLD here).
    assert action != "REDUCE" or True  # sanity: just confirm no crash/branch skip
    # More precise: with score=50 (not <44) and no other trigger, expect MONITOR/HOLD.
    assert action in ("MONITOR", "HOLD", "HOLD_OR_ADD")


def test_recommend_reduce_bearish_reaction_history():
    action, priority, detail, _ = _rec(
        weight=8.0, score=EARNINGS_BEARISH_REACTION_COMPOSITE_GATE - 1, reaction="bearish",
    )
    assert action == "REDUCE"
    assert priority == "MEDIUM"
    assert "bearish" in detail


def test_recommend_bearish_reaction_requires_score_below_gate():
    action, _, _, _ = _rec(
        weight=8.0, score=EARNINGS_BEARISH_REACTION_COMPOSITE_GATE, reaction="bearish",
    )
    assert action != "REDUCE"


def test_recommend_monitor_stop_unavailable():
    action, priority, detail, _ = _rec(gap_to_stop=None, score=90.0, weight=2.0)
    assert action == "MONITOR"
    assert "Stop data unavailable" in detail


def test_recommend_monitor_stop_close_to_estimated_move():
    action, priority, detail, _ = _rec(gap_to_stop=4.0, est_move=5.0, weight=2.0)
    # gap 4.0 < est_move*0.85 = 4.25 -> MONITOR
    assert action == "MONITOR"
    assert "gap to stop" in detail.lower() or "below current price" in detail


def test_recommend_monitor_stop_boundary_exactly_at_threshold_not_monitor():
    action, _, _, _ = _rec(gap_to_stop=4.25, est_move=5.0, weight=2.0, score=60.0)
    assert action != "MONITOR"


def test_recommend_hold_or_add_high_conviction_positive_revisions():
    action, priority, detail, lens = _rec(
        score=COMPOSITE_STRONG_BUY, net_rev=2, weight=2.0, gap_to_stop=20.0,
    )
    assert action == "HOLD_OR_ADD"
    assert priority == "OK"
    assert "net analyst upgrades" in detail


def test_recommend_hold_or_add_requires_net_rev_at_least_2():
    action, _, _, _ = _rec(score=COMPOSITE_STRONG_BUY, net_rev=1, weight=2.0, gap_to_stop=20.0)
    assert action != "HOLD_OR_ADD"


def test_recommend_hold_or_add_extras_beat_rate_and_bullish_reaction():
    action, _, detail, _ = _rec(
        score=COMPOSITE_STRONG_BUY, net_rev=2, weight=2.0, gap_to_stop=20.0,
        beat_rate=EARNINGS_BEAT_RATE_STRONG_THRESHOLD, reaction="bullish",
    )
    assert "historical beat rate" in detail
    assert "bullish post-earnings reaction history" in detail


def test_recommend_hold_general_fallback():
    action, priority, detail, _ = _rec(score=60.0, weight=2.0, gap_to_stop=20.0, net_rev=0)
    assert action == "HOLD"
    assert priority == "OK"
    assert "gap to stop 20.0%" in detail


# ── build_earnings_playbook ──────────────────────────────────────────────────

def _port_row(ticker="AAPL", weight=8.0, mval=10_000.0, pnl_pct=0.0, score=60.0,
              signal="Hold", gap=10.0, stop=90.0, shares=100, sector="Tech"):
    return {
        "Ticker": ticker, "Weight (%)": weight, "Market Value": mval,
        "P&L (%)": pnl_pct, "Score": score, "Signal": signal,
        "Gap to Stop (%)": gap, "Stop": stop, "Stop Type": "ATR Stop",
        "Shares": shares, "Sector": sector,
    }


def _bundle(earnings=None, var_95=-1.0, forward_eps=None, net=0, upgrades=0,
            downgrades=0, latest=None):
    return {
        "earnings": earnings,
        "info": {"forwardEps": forward_eps, "shortName": None},
        "revisions": {"net": net, "upgrades_90d": upgrades, "downgrades_90d": downgrades,
                      "latest": latest or []},
        "risk_metrics": {"var_95": var_95},
    }


def test_build_earnings_playbook_skips_no_earnings_date():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=None)}
    assert ea.build_earnings_playbook(df, held, today=TODAY) == []


def test_build_earnings_playbook_skips_unparseable_earnings_date():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings="not-a-date")}
    assert ea.build_earnings_playbook(df, held, today=TODAY) == []


def test_build_earnings_playbook_skips_past_earnings_date():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY - timedelta(days=1)).isoformat())}
    assert ea.build_earnings_playbook(df, held, today=TODAY) == []


def test_build_earnings_playbook_skips_beyond_lookahead():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=31)).isoformat())}
    assert ea.build_earnings_playbook(df, held, today=TODAY, lookahead_days=30) == []


def test_build_earnings_playbook_urgency_imminent():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=EARNINGS_IMMINENT_DAYS)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["urgency"] == "IMMINENT"


def test_build_earnings_playbook_urgency_soon():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=EARNINGS_URGENCY_SOON_DAYS)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["urgency"] == "SOON"


def test_build_earnings_playbook_urgency_ahead():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=EARNINGS_URGENCY_SOON_DAYS + 1)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["urgency"] == "AHEAD"


def test_build_earnings_playbook_gap_none_preserved_not_defaulted_to_zero():
    df = pd.DataFrame([_port_row(gap=None)])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=5)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["gap_to_stop"] is None
    assert result[0]["action"] == "MONITOR"


def test_build_earnings_playbook_sector_specific_watch_list():
    df = pd.DataFrame([_port_row(sector="Healthcare")])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=5)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["watch_for"] == ea._SECTOR_WATCH["Healthcare"]


def test_build_earnings_playbook_unknown_sector_uses_default_watch():
    df = pd.DataFrame([_port_row(sector="Nonexistent")])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=5)).isoformat())}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result[0]["watch_for"] == ea._DEFAULT_WATCH


def test_build_earnings_playbook_has_cnbc_context_flag():
    df = pd.DataFrame([_port_row()])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=5)).isoformat())}
    ctx = {"AAPL": {"beat_rate_pct": 80.0}}
    result = ea.build_earnings_playbook(df, held, today=TODAY, earnings_context=ctx)
    assert result[0]["has_cnbc_context"] is True
    assert result[0]["beat_rate_pct"] == 80.0

    result_no_ctx = ea.build_earnings_playbook(df, held, today=TODAY)
    assert result_no_ctx[0]["has_cnbc_context"] is False


def test_build_earnings_playbook_sorted_by_days_until():
    df = pd.DataFrame([_port_row(ticker="A"), _port_row(ticker="B")])
    held = {
        "A": _bundle(earnings=(TODAY + timedelta(days=20)).isoformat()),
        "B": _bundle(earnings=(TODAY + timedelta(days=3)).isoformat()),
    }
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    assert [r["ticker"] for r in result] == ["B", "A"]


def test_build_earnings_playbook_stop_at_risk_flag():
    df = pd.DataFrame([_port_row(gap=2.0)])
    held = {"AAPL": _bundle(earnings=(TODAY + timedelta(days=5)).isoformat(), var_95=-2.0)}
    result = ea.build_earnings_playbook(df, held, today=TODAY)
    # est_move = abs(-2)*3 = 6.0; gap 2.0 < 6.0*0.85=5.1 -> stop_at_risk True
    assert result[0]["stop_at_risk"] is True


# ── build_earnings_catalyst_candidates ──────────────────────────────────────

def _ctx(beat_rate=80.0, reaction="bullish", earnings_date=None, growth=None):
    return {
        "beat_rate_pct": beat_rate, "recent_reaction_direction": reaction,
        "earnings_date": earnings_date or (TODAY + timedelta(days=5)).isoformat(),
        "consensus_growth_pct": growth, "what_to_watch_cnbc": None,
    }


def test_catalyst_candidates_skips_held_tickers():
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], {"AAPL"}, {"AAPL": {"total": 80.0}}, {"AAPL": _ctx()}, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_no_earnings_context():
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, {}, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_beat_rate_below_min():
    ctx = {"AAPL": _ctx(beat_rate=EARNINGS_MIN_BEAT_RATE_ENTRY - 1)}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_beat_rate_none():
    ctx = {"AAPL": _ctx(beat_rate=None)}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_bearish_reaction():
    ctx = {"AAPL": _ctx(reaction="bearish")}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_no_earnings_date():
    ctx = {"AAPL": {**_ctx(), "earnings_date": None}}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_unparseable_earnings_date():
    ctx = {"AAPL": {**_ctx(), "earnings_date": "garbage"}}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_outside_lookahead_window():
    ctx = {"AAPL": _ctx(earnings_date=(TODAY + timedelta(days=31)).isoformat())}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 80.0}}, ctx, today=TODAY, lookahead_days=30,
    )
    assert result == []


def test_catalyst_candidates_skips_missing_composite_bundle():
    ctx = {"AAPL": _ctx()}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_skips_score_below_composite_buy():
    ctx = {"AAPL": _ctx()}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": COMPOSITE_BUY - 1}}, ctx, today=TODAY,
    )
    assert result == []


def test_catalyst_candidates_passes_all_gates():
    ctx = {"AAPL": _ctx(beat_rate=80.0, reaction="bullish")}
    result = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 70.0}}, ctx, today=TODAY,
    )
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"
    assert result[0]["rank_score"] == pytest.approx(80.0 * 70.0 * 1.2)


def test_catalyst_candidates_bullish_reaction_multiplier():
    ctx_bull = {"AAPL": _ctx(reaction="bullish")}
    ctx_neutral = {"MSFT": _ctx(reaction="mixed")}
    result_bull = ea.build_earnings_catalyst_candidates(
        ["AAPL"], set(), {"AAPL": {"total": 70.0}}, ctx_bull, today=TODAY,
    )
    result_neutral = ea.build_earnings_catalyst_candidates(
        ["MSFT"], set(), {"MSFT": {"total": 70.0}}, ctx_neutral, today=TODAY,
    )
    assert result_bull[0]["rank_score"] > result_neutral[0]["rank_score"]


def test_catalyst_candidates_sorted_by_rank_score_descending():
    ctx = {
        "LOW": _ctx(beat_rate=71.0, reaction="mixed"),
        "HIGH": _ctx(beat_rate=95.0, reaction="bullish"),
    }
    composites = {"LOW": {"total": 66.0}, "HIGH": {"total": 90.0}}
    result = ea.build_earnings_catalyst_candidates(
        ["LOW", "HIGH"], set(), composites, ctx, today=TODAY,
    )
    assert [r["ticker"] for r in result] == ["HIGH", "LOW"]
