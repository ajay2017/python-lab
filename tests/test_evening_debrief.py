"""Tests for stock_analyzer/evening_debrief.py — the PM companion to Today's
Brief (Plan vs. Reality, Today's Trades summary, Tomorrow's Setup). Pure
logic, no Streamlit/API calls. Constants used (from
stock_analyzer/constants.py): MEANINGFUL_INTRADAY_PCT=1.0,
EARNINGS_IMMINENT_DAYS=7. Previously zero test coverage.
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import evening_debrief as ed


# ─── _trades_today — builders ────────────────────────────────────────────────

def _trade_row(ticker="AAA", action="BUY", shares=10.0, price=100.0,
                traded_at="2024-01-01", **kw):
    return {
        "id": kw.get("id", 1),
        "ticker": ticker,
        "action": action,
        "shares": shares,
        "price": price,
        "cost_basis": kw.get("cost_basis"),
        "realized_pnl": kw.get("realized_pnl"),
        "trigger_type": kw.get("trigger_type", ""),
        "signal_seen": kw.get("signal_seen", ""),
        "followed_signal": kw.get("followed_signal"),
        "notes": kw.get("notes", ""),
        "traded_at": traded_at,
    }


def _df(rows):
    return pd.DataFrame(rows)


# ─── _trades_today ────────────────────────────────────────────────────────────

def test_trades_today_none_df_returns_empty_list():
    assert ed._trades_today(None, date(2024, 1, 1)) == []


def test_trades_today_empty_df_returns_empty_list():
    assert ed._trades_today(pd.DataFrame(), date(2024, 1, 1)) == []


def test_trades_today_string_traded_at_matches_today():
    df = _df([_trade_row(traded_at="2024-01-01")])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert len(out) == 1
    assert out[0]["ticker"] == "AAA"


def test_trades_today_datetime_like_traded_at_matches_today():
    df = _df([_trade_row(traded_at=pd.Timestamp("2024-01-01 14:30:00"))])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert len(out) == 1


def test_trades_today_unparseable_traded_at_skipped_not_crashed():
    df = _df([_trade_row(traded_at="not-a-real-date")])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert out == []


def test_trades_today_date_mismatch_excluded():
    df = _df([_trade_row(traded_at="2024-01-02")])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert out == []


def test_trades_today_split_action_alone_excluded():
    df = _df([_trade_row(action="SPLIT", traded_at="2024-01-01")])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert out == []


def test_trades_today_split_within_compound_action_excluded():
    df = _df([_trade_row(action="BUY_SPLIT_ADJUST", traded_at="2024-01-01")])
    out = ed._trades_today(df, date(2024, 1, 1))
    assert out == []


# ─── _plan_vs_reality — builders ─────────────────────────────────────────────

def _brief(new_picks=None, adds=None, skipped=None, buys=None):
    return {
        "grow_today": {
            "new_picks": new_picks or [],
            "add_positions": adds or [],
            "composite_skipped": skipped or [],
        },
        "buy_candidates": buys or [],
    }


def test_plan_vs_reality_none_brief_returns_empty_shape():
    result = ed._plan_vs_reality(None, [], {})
    assert result == {"go_picks": [], "skip_picks": []}


def test_plan_vs_reality_falsy_empty_dict_brief_returns_empty_shape():
    result = ed._plan_vs_reality({}, [], {})
    assert result == {"go_picks": [], "skip_picks": []}


# ─── _plan_vs_reality — go-pick dedup precedence ─────────────────────────────

def test_plan_vs_reality_new_pick_wins_dedup_over_buy_candidate_same_ticker():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75, "sector": "Tech", "thesis": "t1"}]
    buys = [{"ticker": "AAA", "score": 50, "xref": {"verdict": "confirmed"}}]
    brief = _brief(new_picks=new_picks, buys=buys)
    result = ed._plan_vs_reality(brief, [], {})
    assert len(result["go_picks"]) == 1
    assert result["go_picks"][0]["source"] == "new_pick"


def test_plan_vs_reality_buy_candidate_unconfirmed_verdict_excluded():
    buys = [{"ticker": "BBB", "xref": {"verdict": "pending"}}]
    brief = _brief(buys=buys)
    result = ed._plan_vs_reality(brief, [], {})
    assert result["go_picks"] == []


def test_plan_vs_reality_buy_candidate_confirmed_verdict_included():
    buys = [{"ticker": "BBB", "score": 60, "xref": {"verdict": "confirmed"}}]
    brief = _brief(buys=buys)
    result = ed._plan_vs_reality(brief, [], {})
    assert len(result["go_picks"]) == 1
    assert result["go_picks"][0]["source"] == "buy_candidate"


# ─── _plan_vs_reality — go_picks 4-branch outcome + MEANINGFUL_INTRADAY_PCT ──

def test_plan_vs_reality_go_pick_acted_takes_priority_over_intraday_outcome():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75}]
    brief = _brief(new_picks=new_picks)
    trades_today = [{"ticker": "AAA", "action": "BUY"}]
    result = ed._plan_vs_reality(brief, trades_today, {"AAA": 5.0})  # meaningful gain, but acted wins
    assert result["go_picks"][0]["action_taken"] is True
    assert "Acted" in result["go_picks"][0]["outcome"]


def test_plan_vs_reality_go_pick_no_intraday_data():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75}]
    brief = _brief(new_picks=new_picks)
    result = ed._plan_vs_reality(brief, [], {})
    assert "unavailable" in result["go_picks"][0]["outcome"]


def test_plan_vs_reality_go_pick_missed_at_meaningful_gain_boundary():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75}]
    brief = _brief(new_picks=new_picks)
    result = ed._plan_vs_reality(brief, [], {"AAA": 1.0})  # == MEANINGFUL_INTRADAY_PCT
    assert "Missed" in result["go_picks"][0]["outcome"]


def test_plan_vs_reality_go_pick_dodged_at_meaningful_loss_boundary():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75}]
    brief = _brief(new_picks=new_picks)
    result = ed._plan_vs_reality(brief, [], {"AAA": -1.0})
    assert "Dodged" in result["go_picks"][0]["outcome"]


def test_plan_vs_reality_go_pick_flat_between_boundaries():
    new_picks = [{"ticker": "AAA", "score": 80, "composite_score": 75}]
    brief = _brief(new_picks=new_picks)
    result = ed._plan_vs_reality(brief, [], {"AAA": 0.5})
    assert "Flat" in result["go_picks"][0]["outcome"]


# ─── _plan_vs_reality — skip_picks 3-branch outcome + verdict field ─────────

def test_plan_vs_reality_skip_pick_unavailable_verdict():
    skipped = [{"ticker": "CCC", "sector": "Tech", "momentum_score": 50, "composite_score": 40}]
    brief = _brief(skipped=skipped)
    result = ed._plan_vs_reality(brief, [], {})
    assert result["skip_picks"][0]["verdict"] == "unknown"


def test_plan_vs_reality_skip_pick_missed_verdict():
    skipped = [{"ticker": "CCC", "sector": "Tech", "momentum_score": 50, "composite_score": 40}]
    brief = _brief(skipped=skipped)
    result = ed._plan_vs_reality(brief, [], {"CCC": 2.0})
    assert result["skip_picks"][0]["verdict"] == "missed"


def test_plan_vs_reality_skip_pick_validated_verdict():
    skipped = [{"ticker": "CCC", "sector": "Tech", "momentum_score": 50, "composite_score": 40}]
    brief = _brief(skipped=skipped)
    result = ed._plan_vs_reality(brief, [], {"CCC": -2.0})
    assert result["skip_picks"][0]["verdict"] == "validated"


def test_plan_vs_reality_skip_pick_flat_verdict():
    skipped = [{"ticker": "CCC", "sector": "Tech", "momentum_score": 50, "composite_score": 40}]
    brief = _brief(skipped=skipped)
    result = ed._plan_vs_reality(brief, [], {"CCC": 0.2})
    assert result["skip_picks"][0]["verdict"] == "flat"


# ─── _plan_vs_reality — sort order ────────────────────────────────────────────

def test_plan_vs_reality_go_picks_sort_acted_first_then_abs_pct_desc():
    new_picks = [
        {"ticker": "AAA", "score": 80, "composite_score": 75},
        {"ticker": "BBB", "score": 70, "composite_score": 65},
        {"ticker": "CCC", "score": 60, "composite_score": 55},
    ]
    brief = _brief(new_picks=new_picks)
    trades_today = [{"ticker": "CCC", "action": "BUY"}]  # only CCC acted
    intraday = {"AAA": 2.0, "BBB": 10.0, "CCC": 0.1}
    result = ed._plan_vs_reality(brief, trades_today, intraday)
    tickers_order = [g["ticker"] for g in result["go_picks"]]
    # CCC acted -> first, regardless of its small |pct|; then BBB (10.0) > AAA (2.0)
    assert tickers_order == ["CCC", "BBB", "AAA"]


def test_plan_vs_reality_skip_picks_sort_by_abs_pct_desc_none_as_zero():
    skipped = [
        {"ticker": "X", "momentum_score": 1, "composite_score": 1},
        {"ticker": "Y", "momentum_score": 1, "composite_score": 1},
        {"ticker": "Z", "momentum_score": 1, "composite_score": 1},
    ]
    brief = _brief(skipped=skipped)
    intraday = {"X": -5.0, "Z": 2.0}  # Y has no intraday data -> today_pct None -> treated as 0
    result = ed._plan_vs_reality(brief, [], intraday)
    tickers_order = [s["ticker"] for s in result["skip_picks"]]
    assert tickers_order == ["X", "Z", "Y"]


# ─── _today_summary ───────────────────────────────────────────────────────────

def test_today_summary_counts_and_deployed_and_realized():
    trades = [
        {"ticker": "AAA", "action": "BUY", "shares": 10.0, "price": 20.0, "realized_pnl": 0.0,
         "followed_signal": True},
        {"ticker": "BBB", "action": "SELL", "shares": 5.0, "price": 30.0, "realized_pnl": 50.0,
         "followed_signal": False},
    ]
    summary = ed._today_summary(trades)
    assert summary["n_buys"] == 1
    assert summary["n_sells"] == 1
    assert summary["deployed"] == pytest.approx(200.0)
    assert summary["realized_pnl"] == pytest.approx(50.0)
    assert summary["n_followed"] == 1
    assert summary["n_deviated"] == 1


def test_today_summary_buy_row_with_nonzero_realized_pnl_not_included():
    trades = [
        {"ticker": "AAA", "action": "BUY", "shares": 10.0, "price": 20.0, "realized_pnl": 999.0,
         "followed_signal": None},
    ]
    summary = ed._today_summary(trades)
    assert summary["realized_pnl"] == 0.0


def test_today_summary_followed_signal_none_counts_toward_neither_bucket():
    trades = [
        {"ticker": "AAA", "action": "BUY", "shares": 10.0, "price": 20.0, "realized_pnl": 0.0,
         "followed_signal": None},
    ]
    summary = ed._today_summary(trades)
    assert summary["n_followed"] == 0
    assert summary["n_deviated"] == 0


def test_today_summary_empty_list_gives_zeros():
    summary = ed._today_summary([])
    assert summary["n_trades"] == 0
    assert summary["deployed"] == 0.0
    assert summary["realized_pnl"] == 0.0


# ─── _next_trading_day ───────────────────────────────────────────────────────

def test_next_trading_day_friday_is_monday():
    fri = date(2024, 1, 5)  # Friday
    assert ed._next_trading_day(fri) == date(2024, 1, 8)  # Monday


def test_next_trading_day_saturday_is_monday():
    sat = date(2024, 1, 6)
    assert ed._next_trading_day(sat) == date(2024, 1, 8)


def test_next_trading_day_sunday_is_monday():
    sun = date(2024, 1, 7)
    assert ed._next_trading_day(sun) == date(2024, 1, 8)


def test_next_trading_day_plain_tuesday_is_wednesday():
    tue = date(2024, 1, 2)
    assert ed._next_trading_day(tue) == date(2024, 1, 3)


# ─── _tomorrow_setup ──────────────────────────────────────────────────────────

def test_tomorrow_setup_friday_picks_up_monday_events_not_saturday():
    friday = date(2024, 1, 5)
    macro_events = [
        {"date": "2024-01-06", "event": "Sat event"},   # Saturday -- not tomorrow
        {"date": "2024-01-08", "event": "Mon event"},   # Monday -- next trading day
    ]
    result = ed._tomorrow_setup(macro_events, {}, friday)
    assert result["tomorrow_date"] == "2024-01-08"
    events = [e["event"] for e in result["macro_tomorrow"]]
    assert events == ["Mon event"]


def test_tomorrow_setup_event_date_as_real_date_object_parses():
    today = date(2024, 1, 1)
    macro_events = [{"date": date(2024, 1, 2), "event": "Real date obj"}]
    result = ed._tomorrow_setup(macro_events, {}, today)
    assert len(result["macro_tomorrow"]) == 1


def test_tomorrow_setup_earnings_boundary_1_day_included():
    today = date(2024, 1, 1)
    held = {"AAA": {"earnings": "2024-01-02"}}  # 1 day out
    result = ed._tomorrow_setup([], held, today)
    assert len(result["earnings_imminent"]) == 1
    assert result["earnings_imminent"][0]["days"] == 1


def test_tomorrow_setup_earnings_boundary_7_days_included():
    today = date(2024, 1, 1)
    held = {"AAA": {"earnings": "2024-01-08"}}  # 7 days out == EARNINGS_IMMINENT_DAYS
    result = ed._tomorrow_setup([], held, today)
    assert len(result["earnings_imminent"]) == 1


def test_tomorrow_setup_earnings_boundary_8_days_excluded():
    today = date(2024, 1, 1)
    held = {"AAA": {"earnings": "2024-01-09"}}  # 8 days out
    result = ed._tomorrow_setup([], held, today)
    assert result["earnings_imminent"] == []


def test_tomorrow_setup_earnings_already_happened_excluded():
    today = date(2024, 1, 10)
    held = {"AAA": {"earnings": "2024-01-05"}}  # negative days
    result = ed._tomorrow_setup([], held, today)
    assert result["earnings_imminent"] == []


def test_tomorrow_setup_earnings_sorted_ascending_by_days():
    today = date(2024, 1, 1)
    held = {
        "AAA": {"earnings": "2024-01-07"},  # 6 days
        "BBB": {"earnings": "2024-01-02"},  # 1 day
        "CCC": {"earnings": "2024-01-04"},  # 3 days
    }
    result = ed._tomorrow_setup([], held, today)
    tickers = [e["ticker"] for e in result["earnings_imminent"]]
    assert tickers == ["BBB", "CCC", "AAA"]


# ─── build_evening_debrief — integration smoke test ──────────────────────────

def test_build_evening_debrief_shape_and_passthrough():
    trades_df = _df([_trade_row(traded_at="2024-01-01")])
    result = ed.build_evening_debrief(
        brief=None,
        trades_df=trades_df,
        port_df=None,
        held_data={},
        macro_events=[],
        today=date(2024, 1, 1),
        intraday_pct=None,
        am_baseline_source="locked",
        am_baseline_at="2024-01-01T09:00:00",
    )
    assert result["am_baseline_source"] == "locked"
    assert result["am_baseline_at"] == "2024-01-01T09:00:00"
    assert result["plan_vs_reality"] == {"go_picks": [], "skip_picks": []}
    assert len(result["today_trades"]) == 1
    assert "today_summary" in result
    assert "tomorrow_setup" in result
