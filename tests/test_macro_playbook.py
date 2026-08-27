"""
Tests for stock_analyzer/macro_playbook.py — the Pre-Event Macro Playbook's
PROTECT/WATCH/HOLD/OPPORTUNITY position-level action classifier and its
post-event scenario classification. Previously zero test coverage despite
containing real decision thresholds (_pre_event_action) reconciled against
constants.py (SINGLE_NAME_CEILING, COMPOSITE_HOLD, COMPOSITE_BUY).

Correction to an earlier audit: this file does NOT contain
compute_protective_alerts/_assess_pullback/PULLBACK_ALERT_INDEX_PCT (those
live in headless_alert_engine.py) -- verified against source before writing
these tests, not assumed from the prior description.
"""
from datetime import date, timedelta

from stock_analyzer.market_time import today_et

import pandas as pd

from stock_analyzer.macro_playbook import (
    _pre_event_action,
    _build_rationale,
    _action_detail,
    _post_event_rules,
    build_event_playbooks,
    classify_scenario,
    _parse_number,
    build_post_event_analysis,
    get_scenario_conditions,
)


def _row(**overrides):
    row = {
        "Ticker": "XYZ", "Sector": "Financials", "Weight (%)": 5.0,
        "Score": 60.0, "Signal": "Hold", "P&L (%)": 0.0,
        "Shares": 100, "Price": 50.0, "Market Value": 5000.0,
    }
    row.update(overrides)
    return row


# ─── _pre_event_action ────────────────────────────────────────────────────────
# Using "Non-Farm Payrolls": Financials bear=-2.0 (>= PROTECT_BEAR 1.5),
# bull=+2.5 (>= OPP_BULL 1.5); Healthcare bear=-0.5, bull=+0.5 (both weak).

def test_sell_signal_always_protects_high():
    action, priority = _pre_event_action(
        _row(Signal="Sell", Score=90.0, **{"Weight (%)": 1.0}), "Non-Farm Payrolls", days_until=20
    )
    assert action == "PROTECT" and priority == "HIGH"


def test_strong_sell_signal_protects_high():
    action, priority = _pre_event_action(_row(Signal="Strong Sell"), "Non-Farm Payrolls", days_until=20)
    assert action == "PROTECT" and priority == "HIGH"


def test_low_score_with_bear_exposure_protects_high():
    action, priority = _pre_event_action(
        _row(Score=40.0, Sector="Financials"), "Non-Farm Payrolls", days_until=20
    )
    assert action == "PROTECT" and priority == "HIGH"


def test_low_score_without_bear_exposure_does_not_protect():
    # Healthcare's bear move (-0.5) is below _PROTECT_BEAR (1.5)
    action, _ = _pre_event_action(_row(Score=40.0, Sector="Healthcare"), "Non-Farm Payrolls", days_until=20)
    assert action != "PROTECT"


def test_oversized_position_with_bear_exposure_protects_high():
    action, priority = _pre_event_action(
        _row(**{"Weight (%)": 20.0}, Sector="Financials"), "Non-Farm Payrolls", days_until=20
    )
    assert action == "PROTECT" and priority == "HIGH"


def test_deep_loss_near_event_protects_medium():
    action, priority = _pre_event_action(
        _row(**{"P&L (%)": -20.0}, Sector="Financials"), "Non-Farm Payrolls", days_until=5
    )
    assert action == "PROTECT" and priority == "MEDIUM"


def test_deep_loss_far_from_event_does_not_protect_via_pnl_path():
    # Same deep loss, but days_until=10 (> 7) -- pnl_pct PROTECT path requires <= 7 days
    action, _ = _pre_event_action(
        _row(**{"P&L (%)": -20.0}, Sector="Financials"), "Non-Farm Payrolls", days_until=10
    )
    assert action != "PROTECT"


def test_opportunity_high_score_buy_signal_and_bull_exposure():
    action, priority = _pre_event_action(
        _row(Score=75.0, Signal="Buy", Sector="Financials"), "Non-Farm Payrolls", days_until=10
    )
    assert action == "OPPORTUNITY" and priority == "OK"


def test_opportunity_requires_event_within_14_days():
    action, _ = _pre_event_action(
        _row(Score=75.0, Signal="Buy", Sector="Financials"), "Non-Farm Payrolls", days_until=20
    )
    assert action != "OPPORTUNITY"


def test_opportunity_requires_buy_signal_not_just_high_score():
    action, _ = _pre_event_action(
        _row(Score=75.0, Signal="Hold", Sector="Financials"), "Non-Farm Payrolls", days_until=10
    )
    assert action != "OPPORTUNITY"


def test_watch_medium_on_bear_exposure_and_sufficient_weight():
    action, priority = _pre_event_action(
        _row(Score=60.0, **{"Weight (%)": 10.0}, Sector="Financials"), "Non-Farm Payrolls", days_until=20
    )
    assert action == "WATCH" and priority == "MEDIUM"


def test_watch_low_on_weak_score():
    # NFP "AI & Data" bear = -1.2 -- in [WATCH_BEAR 1.0, PROTECT_BEAR 1.5),
    # the band where only the WATCH-LOW check (not WATCH-MEDIUM) can fire.
    action, priority = _pre_event_action(
        _row(Score=50.0, Sector="AI & Data"), "Non-Farm Payrolls", days_until=20
    )
    assert action == "WATCH" and priority == "LOW"


def test_watch_low_on_high_weight_even_with_ok_score():
    action, priority = _pre_event_action(
        _row(Score=60.0, **{"Weight (%)": 13.0}, Sector="AI & Data"), "Non-Farm Payrolls", days_until=20
    )
    assert action == "WATCH" and priority == "LOW"


def test_hold_when_no_conditions_trigger():
    action, priority = _pre_event_action(_row(Sector="Healthcare"), "Non-Farm Payrolls", days_until=20)
    assert action == "HOLD" and priority == "OK"


def test_unknown_event_name_defaults_to_hold():
    action, priority = _pre_event_action(_row(Score=90.0, Signal="Buy"), "Not A Real Event", days_until=1)
    assert action == "HOLD" and priority == "OK"


def test_unknown_sector_in_known_event_defaults_zero_moves():
    action, _ = _pre_event_action(_row(Sector="Not A Real Sector"), "Non-Farm Payrolls", days_until=5)
    assert action == "HOLD"


def test_sell_signal_takes_priority_over_opportunity_conditions():
    # High score + Buy-adjacent text wouldn't matter; a real Sell signal
    # must win regardless of how good the other numbers look.
    action, priority = _pre_event_action(
        _row(Signal="Sell", Score=95.0, **{"Weight (%)": 1.0}), "Non-Farm Payrolls", days_until=3
    )
    assert action == "PROTECT" and priority == "HIGH"


# ─── _build_rationale / _action_detail / _post_event_rules ───────────────────

def test_rationale_protect_sell_mentions_ticker_and_sector_move():
    text = _build_rationale(_row(Signal="Sell", Ticker="OXY", Sector="Financials"), "Non-Farm Payrolls", "PROTECT")
    assert "OXY" in text
    assert "Sell signal" in text


def test_rationale_protect_weight_mentions_ceiling():
    text = _build_rationale(_row(**{"Weight (%)": 20.0}, Ticker="OXY"), "Non-Farm Payrolls", "PROTECT")
    assert "20%" in text or "20" in text


def test_rationale_opportunity_mentions_bull_label():
    text = _build_rationale(_row(Score=75.0, Signal="Buy", Ticker="NVDA", Sector="Financials"), "Non-Farm Payrolls", "OPPORTUNITY")
    assert "NVDA" in text
    assert "Strong Beat" in text  # NFP's bull label


def test_rationale_watch_includes_score_and_weight():
    text = _build_rationale(_row(Ticker="AAPL", **{"Weight (%)": 9.0}, Score=58.0), "Non-Farm Payrolls", "WATCH")
    assert "58" in text
    assert "9.0" in text


def test_rationale_hold_limited_exposure_when_bear_move_small():
    # NFP Defense bear = -0.3, strictly < the 0.5 threshold for this branch
    text = _build_rationale(_row(Ticker="ABC", Sector="Defense"), "Non-Farm Payrolls", "HOLD")
    assert "limited direct exposure" in text.lower()


def test_action_detail_protect_trims_to_ceiling_when_oversized():
    detail = _action_detail(_row(**{"Weight (%)": 20.0}, Shares=1000, Price=10.0, **{"Market Value": 10000.0}), "Non-Farm Payrolls", "PROTECT")
    assert "sell" in detail.lower()
    # trim_frac = (20-15)/20 = 0.25 -> 250 shares
    assert "250" in detail


def test_action_detail_protect_50pct_reduction_when_other_trigger():
    detail = _action_detail(_row(Shares=100, Price=50.0), "Non-Farm Payrolls", "PROTECT")
    assert "50%" in detail
    assert "sell 50 shares" in detail.lower()


def test_action_detail_opportunity_adds_10pct():
    detail = _action_detail(_row(Shares=100, Price=50.0), "Non-Farm Payrolls", "OPPORTUNITY")
    assert "10 shares" in detail  # 10% of 100


def test_action_detail_hold_is_generic():
    detail = _action_detail(_row(), "Non-Farm Payrolls", "HOLD")
    assert "Hold current position" in detail


def test_post_event_rules_bear_only():
    text = _post_event_rules(_row(Ticker="XYZ", Sector="Financials"), "Non-Farm Payrolls")
    assert "Miss + Downward Revisions" in text or "If" in text
    assert "XYZ" in text


def test_post_event_rules_bull_only():
    text = _post_event_rules(_row(Ticker="XYZ", Sector="AI & Cloud"), "CPI Inflation")
    # AI & Cloud CPI bull = +2.0 (> 1.0), bear = -2.0 (< -1.0) -- both rules present
    assert "XYZ" in text


def test_post_event_rules_fallback_when_no_material_moves():
    text = _post_event_rules(_row(Ticker="XYZ", Sector="Not A Real Sector"), "Non-Farm Payrolls")
    assert "spillover" in text.lower()


# ─── classify_scenario / _parse_number ────────────────────────────────────────

def test_parse_number_none_returns_none():
    assert _parse_number(None) is None


def test_parse_number_passthrough_numeric():
    assert _parse_number(42.5) == 42.5


def test_parse_number_extracts_from_fred_string():
    assert _parse_number("NFP Chg: +215K") == 215.0


def test_parse_number_handles_negative_and_decimal():
    assert _parse_number("CPI YoY: -2.45%") == -2.45


def test_parse_number_unparseable_returns_none():
    assert _parse_number("no digits here") is None


def test_classify_scenario_none_actual_returns_none():
    assert classify_scenario("Non-Farm Payrolls", None) is None


def test_classify_scenario_unknown_event_returns_none():
    assert classify_scenario("Not A Real Event", 200) is None


def test_classify_scenario_higher_is_bull_beat():
    # NFP: beat=20, miss=20; estimate=165 -> bull if actual > 185
    assert classify_scenario("Non-Farm Payrolls", 250, estimate=165) == "bull"


def test_classify_scenario_higher_is_bull_miss():
    assert classify_scenario("Non-Farm Payrolls", 100, estimate=165) == "bear"


def test_classify_scenario_higher_is_bull_inline():
    assert classify_scenario("Non-Farm Payrolls", 170, estimate=165) == "base"


def test_classify_scenario_lower_is_bull_for_cpi():
    # CPI: higher_is_bull=False, beat=0.05, miss=0.05
    assert classify_scenario("CPI Inflation", 2.9, estimate=3.0) == "bull"   # cooler than estimate
    assert classify_scenario("CPI Inflation", 3.2, estimate=3.0) == "bear"   # hotter than estimate
    assert classify_scenario("CPI Inflation", 3.0, estimate=3.0) == "base"


def test_classify_scenario_falls_back_to_implied_base_when_no_estimate():
    # NFP has implied_base=165
    assert classify_scenario("Non-Farm Payrolls", 250, estimate=None) == "bull"


def test_classify_scenario_none_when_no_estimate_and_no_implied_base():
    # CPI has implied_base=None
    assert classify_scenario("CPI Inflation", 3.0, estimate=None) is None


def test_classify_scenario_accepts_fred_formatted_actual():
    assert classify_scenario("Non-Farm Payrolls", "NFP Chg: +250K", estimate=165) == "bull"


def test_get_scenario_conditions_known_event():
    conditions = get_scenario_conditions("Non-Farm Payrolls")
    assert conditions["bull"] and conditions["base"] and conditions["bear"]


def test_get_scenario_conditions_unknown_event_returns_empty_strings():
    conditions = get_scenario_conditions("Not A Real Event")
    assert conditions == {"bull": "", "base": "", "bear": ""}


# ─── build_event_playbooks ────────────────────────────────────────────────────

def _port_df_row(ticker="XYZ", sector="Financials", weight=5.0, score=60.0,
                  signal="Hold", pnl=0.0, shares=100, price=50.0, mval=5000.0):
    return {
        "Ticker": ticker, "Sector": sector, "Weight (%)": weight, "Score": score,
        "Signal": signal, "P&L (%)": pnl, "Shares": shares, "Price": price,
        "Market Value": mval,
    }


def _event(name="Non-Farm Payrolls", days_ahead=5, impact="HIGH"):
    # today_et(), NOT date.today() -- build_event_playbooks compares against
    # _today_et() (ET), and date.today() is the system/UTC date. They disagree
    # for ~4 hours a day (UTC has rolled to a new date, ET has not), which made
    # this fixture flip a genuinely-past event into a same-day one and fail
    # test_build_event_playbooks_skips_past_events on CI runs landing in that
    # window (observed 2026-08-26/27, 3 occurrences).
    return {
        "event": name, "date": today_et() + timedelta(days=days_ahead),
        "impact": impact, "days_label": f"{days_ahead}d", "category": "Labor",
        "description": "", "estimate": None, "previous": None, "context": "",
        "watch_for": [],
    }


def test_build_event_playbooks_empty_port_df_returns_empty_list():
    assert build_event_playbooks([_event()], pd.DataFrame(), 100_000.0) == []
    assert build_event_playbooks([_event()], None, 100_000.0) == []


def test_build_event_playbooks_skips_non_high_impact_events():
    port_df = pd.DataFrame([_port_df_row()])
    result = build_event_playbooks([_event(impact="MEDIUM")], port_df, 100_000.0)
    assert result == []


def test_build_event_playbooks_skips_past_events():
    port_df = pd.DataFrame([_port_df_row()])
    result = build_event_playbooks([_event(days_ahead=-1)], port_df, 100_000.0)
    assert result == []


def test_build_event_playbooks_skips_unknown_event_names():
    port_df = pd.DataFrame([_port_df_row()])
    result = build_event_playbooks([_event(name="Not A Real Event")], port_df, 100_000.0)
    assert result == []


def test_build_event_playbooks_excludes_positions_with_negligible_sector_moves():
    port_df = pd.DataFrame([_port_df_row(sector="Not A Real Sector")])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    assert result[0]["positions"] == []


def test_build_event_playbooks_includes_positions_with_real_sector_exposure():
    port_df = pd.DataFrame([_port_df_row(sector="Financials")])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    assert len(result[0]["positions"]) == 1
    assert result[0]["positions"][0]["ticker"] == "XYZ"


def test_build_event_playbooks_exposure_level_buckets():
    # A single 100%-weight position with a bear-exposed sector -> exposure_pct=100 -> CRITICAL
    port_df = pd.DataFrame([_port_df_row(sector="Financials", mval=100_000.0)])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    assert result[0]["exposure_level"] == "CRITICAL"
    assert result[0]["exposure_pct"] == 100.0


def test_build_event_playbooks_exposure_level_low_when_no_bear_exposure():
    port_df = pd.DataFrame([_port_df_row(sector="Healthcare", mval=100_000.0)])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    assert result[0]["exposure_level"] == "LOW"


def test_build_event_playbooks_action_counts_tally_correctly():
    port_df = pd.DataFrame([
        _port_df_row(ticker="A", signal="Sell", sector="Financials"),       # PROTECT
        _port_df_row(ticker="B", score=75.0, signal="Buy", sector="Financials"),  # OPPORTUNITY
        _port_df_row(ticker="C", weight=10.0, sector="Financials"),          # WATCH
    ])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    pb = result[0]
    assert pb["protect_count"] == 1
    assert pb["opp_count"] == 1
    assert pb["watch_count"] == 1


def test_build_event_playbooks_positions_sorted_protect_first():
    port_df = pd.DataFrame([
        _port_df_row(ticker="HOLD_ME", weight=1.0, sector="Financials", score=60.0),
        _port_df_row(ticker="PROTECT_ME", signal="Sell", sector="Financials"),
    ])
    result = build_event_playbooks([_event()], port_df, 100_000.0)
    assert result[0]["positions"][0]["ticker"] == "PROTECT_ME"


def test_build_event_playbooks_multiple_events_sorted_by_date():
    port_df = pd.DataFrame([_port_df_row(sector="Financials")])
    events = [_event(name="CPI Inflation", days_ahead=10), _event(name="Non-Farm Payrolls", days_ahead=2)]
    result = build_event_playbooks(events, port_df, 100_000.0)
    assert [r["event"] for r in result] == ["Non-Farm Payrolls", "CPI Inflation"]


def test_build_event_playbooks_zero_total_val_guards_division():
    port_df = pd.DataFrame([_port_df_row(sector="Financials")])
    result = build_event_playbooks([_event()], port_df, 0.0)
    assert result[0]["exposure_pct"] == 0.0


# ─── build_post_event_analysis ────────────────────────────────────────────────

def test_build_post_event_analysis_unknown_event_returns_empty_dict():
    assert build_post_event_analysis(_event(name="Not A Real Event"), pd.DataFrame(), 100_000.0, "bull") == {}


def test_build_post_event_analysis_invalid_scenario_key_returns_empty_dict():
    assert build_post_event_analysis(_event(), pd.DataFrame(), 100_000.0, "not_a_scenario") == {}


def test_build_post_event_analysis_none_port_df_handled_gracefully():
    result = build_post_event_analysis(_event(), None, 100_000.0, "bull")
    assert result["positions"] == []


def test_build_post_event_analysis_bull_add_when_strong_setup():
    port_df = pd.DataFrame([_port_df_row(sector="Financials", score=75.0, signal="Buy")])
    result = build_post_event_analysis(_event(), port_df, 100_000.0, "bull")
    assert result["positions"][0]["action"] == "ADD"


def test_build_post_event_analysis_bull_hold_when_modest_tailwind():
    port_df = pd.DataFrame([_port_df_row(sector="Healthcare", score=75.0, signal="Buy")])
    # Healthcare NFP bull = +0.5, below the 1.0 HOLD-tailwind threshold but
    # still >= 0.1 so it's not excluded outright
    result = build_post_event_analysis(_event(), port_df, 100_000.0, "bull")
    assert result["positions"][0]["action"] == "HOLD"


def test_build_post_event_analysis_bear_protect_when_large_headwind_and_weight():
    port_df = pd.DataFrame([_port_df_row(sector="Financials", weight=10.0)])
    result = build_post_event_analysis(_event(), port_df, 100_000.0, "bear")
    assert result["positions"][0]["action"] == "PROTECT"


def test_build_post_event_analysis_bear_watch_when_moderate_headwind():
    port_df = pd.DataFrame([_port_df_row(sector="AI & Data", weight=2.0)])
    # NFP AI & Data bear = -1.2 (between -1.0 and -2.0)
    result = build_post_event_analysis(_event(), port_df, 100_000.0, "bear")
    assert result["positions"][0]["action"] == "WATCH"


def test_build_post_event_analysis_excludes_negligible_moves():
    port_df = pd.DataFrame([_port_df_row(sector="Not A Real Sector")])
    result = build_post_event_analysis(_event(), port_df, 100_000.0, "bull")
    assert result["positions"] == []


def test_build_post_event_analysis_sorted_by_absolute_dollar_impact_descending():
    port_df = pd.DataFrame([
        _port_df_row(ticker="SMALL", sector="Healthcare", mval=1000.0),
        _port_df_row(ticker="BIG", sector="Financials", mval=100_000.0),
    ])
    result = build_post_event_analysis(_event(), port_df, 200_000.0, "bull")
    assert result["positions"][0]["ticker"] == "BIG"


def test_build_post_event_analysis_includes_scenario_metadata():
    result = build_post_event_analysis(_event(), pd.DataFrame([_port_df_row(sector="Financials")]), 100_000.0, "bull")
    assert result["scenario_label"] == "Strong Beat"
    assert result["market_pct"] == 1.2
