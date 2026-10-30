"""trim_sizing: the single source of trim share counts and the earnings-week
overweight trim rule (owner decisions 2026-10-23 no sub-share trim calls, and
2026-10-25 Option A one earnings-week rule). The cross-surface tests prove the
Brief, the Catalyst Watch playbook and Signals size the SAME holding the same."""
import re
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import earnings_advisor as ea
from stock_analyzer import macro_playbook as mp
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import (
    COMPOSITE_HOLD,
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_OVERWEIGHT_TOLERANCE_PP,
    EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT,
    EARNINGS_OVERWEIGHT_TRIM_PCT,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    REBALANCE_TRIM_PNL_PCT,
    SINGLE_NAME_CEILING,
    STOP_PROFIT_LOCK_PNL_PCT,
)
from stock_analyzer.daily_briefing import _act_today, _review_list
from stock_analyzer.portfolio import rebalance_actions
from stock_analyzer.rec_events_capture import _build_rebal_trim_rows
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 26)


# -- shares_to_trim / fraction_trim_shares ------------------------------------

def test_shares_to_trim_equal_weight_and_below_target_is_zero():
    assert ts.shares_to_trim(100, 15.0, 15.0) == 0
    assert ts.shares_to_trim(100, 10.0, 15.0) == 0


def test_shares_to_trim_floors_never_rounds_up():
    # 100 * (19-10)/19 = 47.37 -> 47
    assert ts.shares_to_trim(100, 19.0, 10.0) == 47
    # sub-share excess -> 0, not 1
    assert ts.shares_to_trim(1, 19.0, 10.0) == 0
    assert ts.shares_to_trim(10, 15.5, 15.0) == 0


def test_shares_to_trim_float_shares_and_matches_signals_formula():
    assert ts.shares_to_trim(12.5, 20.0, 10.0) == 6
    price, shares, w, t = 50.0, 100, 19.0, 10.0
    mval = shares * price
    assert ts.shares_to_trim(shares, w, t) == int(mval * (w - t) / w / price)


@pytest.mark.parametrize("bad", [None, float("nan"), "x"])
def test_shares_to_trim_bad_input_is_zero(bad):
    assert ts.shares_to_trim(bad, 19.0, 10.0) == 0
    assert ts.shares_to_trim(100, bad, 10.0) == 0
    assert ts.shares_to_trim(100, 19.0, bad) == 0


def test_shares_to_trim_nonpositive_shares_is_zero():
    assert ts.shares_to_trim(0, 19.0, 10.0) == 0
    assert ts.shares_to_trim(-5, 19.0, 10.0) == 0


def test_fraction_trim_shares_floors_and_guards():
    assert ts.fraction_trim_shares(10, 25.0) == 2
    assert ts.fraction_trim_shares(3, 25.0) == 0
    assert ts.fraction_trim_shares(1, 50.0) == 0
    assert ts.fraction_trim_shares(7.0, 50.0) == 3
    assert ts.fraction_trim_shares(None, 50.0) == 0
    assert ts.fraction_trim_shares(float("nan"), 50.0) == 0
    assert ts.fraction_trim_shares(10, None) == 0
    assert ts.fraction_trim_shares(0, 50.0) == 0


# -- floor --------------------------------------------------------------------

def _old_floor(n):
    if n <= 0:
        return EARNINGS_OVERWEIGHT_TRIM_PCT
    return max(EARNINGS_OVERWEIGHT_TRIM_PCT,
               min(100.0 / n + EARNINGS_OVERWEIGHT_TOLERANCE_PP,
                   EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT))


@pytest.mark.parametrize("n", [0, 4, 15, 30])
def test_earnings_overweight_floor_values_unchanged(n):
    assert ts.earnings_overweight_floor(n) == _old_floor(n)


def test_daily_briefing_keeps_old_name_alias():
    from stock_analyzer.daily_briefing import _dynamic_overweight_floor
    assert _dynamic_overweight_floor(15) == ts.earnings_overweight_floor(15)


# -- dates / window -----------------------------------------------------------

def test_days_to_earnings_parse_and_bad_values():
    assert ts.days_to_earnings("2026-10-29", _TODAY) == 3
    assert ts.days_to_earnings("2026-10-25", _TODAY) == -1
    assert ts.days_to_earnings(None, _TODAY) is None
    assert ts.days_to_earnings("", _TODAY) is None
    assert ts.days_to_earnings("not-a-date", _TODAY) is None


def test_earnings_days_by_ticker_uppercases_and_skips_missing():
    held = {"aaa": {"earnings": "2026-10-29"}, "BBB": {"earnings": None},
            "CCC": {}, "DDD": None, "EEE": {"earnings": "junk"}}
    assert ts.earnings_days_by_ticker(held, _TODAY) == {"AAA": 3}
    assert ts.earnings_days_by_ticker(None, _TODAY) == {}


@pytest.mark.parametrize("days,expected", [
    (-1, False), (0, True), (EARNINGS_IMMINENT_DAYS, True),
    (EARNINGS_IMMINENT_DAYS + 1, False), (None, False),
])
def test_in_earnings_window_edges(days, expected):
    assert ts.in_earnings_window(days) is expected


# -- earnings_week_trim -------------------------------------------------------

def test_earnings_week_trim_none_at_or_below_floor():
    floor = ts.earnings_overweight_floor(15)
    assert ts.earnings_week_trim(floor, 15, 100) is None
    assert ts.earnings_week_trim(floor - 1, 15, 100) is None
    assert ts.earnings_week_trim(None, 15, 100) is None


def test_earnings_week_trim_result_and_zero_share_case():
    r = ts.earnings_week_trim(19.0, 15, 100)
    assert r == {"floor_pct": ts.earnings_overweight_floor(15),
                 "target_pct": EARNINGS_OVERWEIGHT_TRIM_TO_PCT, "trim_shares": 47}
    r0 = ts.earnings_week_trim(19.0, 15, 1)
    assert r0 is not None and r0["trim_shares"] == 0


# -- the whole point: one holding, one size, three surfaces -------------------

def _book(weight=19.0, shares=100, price=50.0, pnl=REBALANCE_TRIM_PNL_PCT + 20, n=15):
    rows = [{
        "Ticker": "AAA", "Weight (%)": weight, "P&L (%)": pnl, "Price": price,
        "Shares": shares, "Signal": "Hold", "Score": 70.0, "Stop": price * 0.9,
        "Gap to Stop (%)": 10.0, "Market Value": shares * price,
        "Avg Cost": price / 1.5, "Stop Type": "ATR Stop", "Sector": "Tech",
    }]
    for i in range(n - 1):
        rows.append({
            "Ticker": f"F{i}", "Weight (%)": 5.0, "P&L (%)": 0.0, "Price": 10.0,
            "Shares": 10, "Signal": "Hold", "Score": 60.0, "Stop": 9.0,
            "Gap to Stop (%)": 10.0, "Market Value": 100.0, "Avg Cost": 10.0,
            "Stop Type": "ATR Stop", "Sector": "Tech",
        })
    return pd.DataFrame(rows)


def _brief_item(df, days):
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=days)).isoformat()}}
    items = _review_list(df, [], [], held, _TODAY, portfolio_value=100_000.0)
    return next(i for i in items if i["ticker"] == "AAA" and i["icon"] == "📅")


def test_same_holding_same_trim_on_brief_playbook_and_signals():
    df = _book()
    expected = ts.shares_to_trim(100, 19.0, EARNINGS_OVERWEIGHT_TRIM_TO_PCT)
    assert expected == 47

    item = _brief_item(df, 3)
    assert item["action"]["type"] == "TRIM_TO_TARGET"
    assert item["action"]["trim_shares"] == expected
    assert item["action"]["target_weight"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT

    action, prio, detail, _lens = ea._recommend(
        3, 70.0, 19.0, 70.0, 10.0, 0, "Buy", 100, 5000.0, 8.0, n_positions=15)
    assert action == "REDUCE" and prio == "HIGH"
    assert f"sell {expected:,} shares" in detail
    assert f"~{EARNINGS_OVERWEIGHT_TRIM_TO_PCT:.0f}%" in detail

    acts = rebalance_actions(df, earnings_days={"AAA": 3})
    trim = next(a for a in acts if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["trim_shares"] == expected
    assert trim["target_weight"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT
    assert trim["basis"] == "earnings_week" and trim["earnings_days"] == 3
    assert "earnings in 3d" in trim["trigger"]


def test_fractional_holding_same_trim_on_playbook_and_signals():
    # 21.9 shares at 19% -> floor(21.9 * 9/19) = 10; the int-truncated count
    # (21) would give 9. The playbook sizes from the unrounded count, as Signals does.
    expected = ts.shares_to_trim(21.9, 19.0, EARNINGS_OVERWEIGHT_TRIM_TO_PCT)
    assert expected == 10
    assert ts.shares_to_trim(21, 19.0, EARNINGS_OVERWEIGHT_TRIM_TO_PCT) == 9

    _a, _p, detail, _l = ea._recommend(
        3, 70.0, 19.0, 70.0, 10.0, 0, "Buy", 21, 21.9 * 50.0, 8.0,
        n_positions=15, sizing_shares=21.9)
    assert f"sell {expected:,} shares" in detail

    df = _book(shares=21.9)
    trim = next(a for a in rebalance_actions(df, earnings_days={"AAA": 3})
                if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["trim_shares"] == expected


def test_outside_window_keeps_single_name_ceiling_target():
    df = _book()
    acts = rebalance_actions(df, earnings_days={"AAA": 10})
    trim = next(a for a in acts if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["target_weight"] == SINGLE_NAME_CEILING
    assert trim["basis"] == "ceiling"
    assert trim["trim_shares"] == ts.shares_to_trim(100, 19.0, SINGLE_NAME_CEILING)
    # no earnings info at all behaves the same
    trim2 = next(a for a in rebalance_actions(df) if a["type"] == "trim")
    assert trim2["target_weight"] == SINGLE_NAME_CEILING and trim2["earnings_days"] is None

    action, _p, detail, _l = ea._recommend(
        10, 70.0, 19.0, 70.0, 10.0, 0, "Buy", 100, 5000.0, 8.0, n_positions=15)
    assert action == "REDUCE"
    assert f"~{SINGLE_NAME_CEILING:.0f}%" in detail
    n = ts.shares_to_trim(100, 19.0, SINGLE_NAME_CEILING)
    assert f"sell {n:,} shares" in detail


def test_recommend_13pct_in_window_reduces_to_10_but_not_outside():
    # N=15 -> floor 12; 13% > floor. Outside the window 13% < SINGLE_NAME_TRIM_TRIGGER.
    action, _p, detail, _l = ea._recommend(
        3, 70.0, 13.0, 10.0, 10.0, 0, "Buy", 100, 5000.0, 8.0, n_positions=15)
    assert action == "REDUCE"
    assert f"~{EARNINGS_OVERWEIGHT_TRIM_TO_PCT:.0f}%" in detail
    assert f"sell {ts.shares_to_trim(100, 13.0, EARNINGS_OVERWEIGHT_TRIM_TO_PCT)} shares" in detail
    action2, _p2, detail2, _l2 = ea._recommend(
        10, 70.0, 13.0, 10.0, 10.0, 0, "Buy", 100, 5000.0, 8.0, n_positions=15)
    assert "too concentrated" not in detail2 and "earnings-week line" not in detail2
    assert action2 != "REDUCE"


def test_build_earnings_playbook_passes_position_count():
    df = _book(weight=13.0)
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    pb = ea.build_earnings_playbook(df, held, today=_TODAY)
    aaa = next(p for p in pb if p["ticker"] == "AAA")
    assert aaa["action"] == "REDUCE"  # 13% > floor(15) = 12


# -- sub-share cases: never "sell 1" / "sell 0" -------------------------------

def _no_sell_one(action, detail):
    return not (action == "REDUCE" and re.search(r"sell 1 share|sell 0", detail))


def test_every_reduce_branch_with_one_share_does_not_return_reduce():
    # oversized, in window and outside
    for days in (3, 10):
        a, _p, d, _l = ea._recommend(days, 70.0, 25.0, 10.0, 10.0, 0, "Buy", 1, 100.0, 8.0,
                                     n_positions=15)
        assert a != "REDUCE" and _no_sell_one(a, d)
    # weak composite
    a, _p, d, _l = ea._recommend(10, COMPOSITE_HOLD - 5, 6.0, 0.0, 10.0, 0, "Hold", 1, 100.0, 8.0)
    assert a != "REDUCE" and _no_sell_one(a, d)
    # negative revisions
    a, _p, d, _l = ea._recommend(10, 70.0, 6.0, 0.0, 10.0, -5, "Hold", 1, 100.0, 8.0)
    assert a != "REDUCE" and _no_sell_one(a, d)
    # poor beat history
    a, _p, d, _l = ea._recommend(10, 55.0, 6.0, 0.0, 10.0, 0, "Hold", 1, 100.0, 8.0,
                                 beat_rate=10.0)
    assert a != "REDUCE" and _no_sell_one(a, d)
    # bearish reaction
    a, _p, d, _l = ea._recommend(10, 55.0, 6.0, 0.0, 10.0, 0, "Hold", 1, 100.0, 8.0,
                                 reaction="bearish")
    assert a != "REDUCE" and _no_sell_one(a, d)


def test_rating_change_flag_never_trims_and_weak_reduce_still_floors():
    # Finding #22 (2026-10-30): net rating changes <= EARNINGS_REVISION_WATCH_NET are
    # a Watch flag; they no longer size the old floor(20 * 35%) = 7-share REDUCE.
    a, _p, d, _l = ea._recommend(10, 70.0, 6.0, 0.0, 10.0, -5, "Hold", 20, 1000.0, 8.0)
    assert a != "REDUCE" and "sell" not in d.lower()
    a, _p, d, _l = ea._recommend(10, COMPOSITE_HOLD - 5, 6.0, 0.0, 10.0, 0, "Hold", 21, 1000.0, 8.0)
    assert a == "REDUCE" and "**Sell 10 shares**" in d  # floor(21 * 50%)


def test_brief_earnings_item_under_one_share_excess_is_watch():
    df = _book(weight=19.0, shares=1, price=50.0)
    item = _brief_item(df, 3)
    assert item["action"] == {"type": "WATCH"}
    assert "less than one share" in item["why"]


def _stop_row(shares):
    return make_port_df([{
        "ticker": "AAA", "shares": shares, "price": 100.0, "gap_to_stop": 2.0,
        "pnl_pct": STOP_PROFIT_LOCK_PNL_PCT + 5, "weight": 5.0,
    }])


def test_profit_lock_with_tiny_shares_is_tighten_only():
    items = _review_list(_stop_row(3), [], [], {}, _TODAY, portfolio_value=100_000.0)
    item = next(i for i in items if i["ticker"] == "AAA" and i["icon"] == "📍")
    assert item["action"]["type"] == "TIGHTEN_ONLY"
    assert "under one share" in item["why"]


def test_profit_lock_with_enough_shares_still_trims_floored():
    items = _review_list(_stop_row(41), [], [], {}, _TODAY, portfolio_value=100_000.0)
    item = next(i for i in items if i["ticker"] == "AAA" and i["icon"] == "📍")
    assert item["action"]["type"] == "TRIM_AND_TIGHTEN"
    assert item["action"]["trim_shares"] == 10  # floor(41 * 25%)


def _det(shares):
    return {
        "ticker": "AAA", "tier": "TRIM", "shares": shares, "dd_from_peak_pct": 12.0,
        "peak": 100.0, "trend_ma": 50, "trim_floor": 8.0, "exit_floor": 15.0,
        "below_ma_count": 3, "rel_strength": -5.0, "pnl_pct": 5.0, "weight_pct": 8.0,
    }


def test_deterioration_trim_clause_never_says_one_of_n_for_subshare_trim():
    df = make_port_df([{"ticker": "AAA", "shares": 3, "price": 100.0}])
    items = _act_today(df, [], [], [], [], _TODAY, deterioration=[_det(3)])
    item = find_item(items, "AAA")
    assert "under one share" in item["directive"]
    assert "1 of 3" not in item["directive"]
    items = _act_today(df, [], [], [], [], _TODAY, deterioration=[_det(40)])
    assert "(10 of 40 shares)" in find_item(items, "AAA")["directive"]


# -- macro text ---------------------------------------------------------------

def _mrow(**o):
    r = {"Ticker": "XYZ", "Sector": "Financials", "Weight (%)": 5.0, "Score": 60.0,
         "Signal": "Hold", "P&L (%)": 0.0, "Shares": 100, "Price": 50.0,
         "Market Value": 5000.0}
    r.update(o)
    return r


def test_macro_text_never_sells_zero_or_one_share_at_zero_size():
    t = mp._action_detail(_mrow(Shares=1, Price=50.0), "Non-Farm Payrolls", "PROTECT")
    assert "no trim is sized" in t and "sell 0" not in t and "sell 1 shares" not in t
    t = mp._action_detail(
        _mrow(**{"Weight (%)": SINGLE_NAME_CEILING + 0.5, "Market Value": 50.0},
              Shares=1, Price=50.0), "Non-Farm Payrolls", "PROTECT")
    assert "less than one share" in t and "sell 0" not in t
    t = mp._action_detail(_mrow(Shares=100), "Non-Farm Payrolls", "PROTECT")
    assert "sell 50 shares" in t


# -- rec_events_capture -------------------------------------------------------

def test_rebal_trim_row_target_follows_basis():
    df = _book()
    rows = _build_rebal_trim_rows(df, "2026-10-26", None, earnings_days={"AAA": 3})
    assert [r["ticker"] for r in rows] == ["AAA"]
    assert rows[0]["metric_predicted_after"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT
    rows = _build_rebal_trim_rows(df, "2026-10-26", None, earnings_days={"AAA": 10})
    assert rows[0]["metric_predicted_after"] == SINGLE_NAME_CEILING
    rows = _build_rebal_trim_rows(df, "2026-10-26")
    assert rows[0]["metric_predicted_after"] == SINGLE_NAME_CEILING


# -- review round 1 (Opus FIX-FIRST, 2026-10-25) ------------------------------

def test_signals_trim_card_renders_the_sized_target_not_the_ceiling():
    # app.py has no unit tests: pin the Signals trim card's render branch to the
    # action's target_weight, so an earnings-week trim never reads "-> ~15%".
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = src.index('elif act["type"] == "trim":')
    branch = src[start:src.index('elif act["type"] == "add":', start)]
    assert 'act.get("target_weight", SINGLE_NAME_CEILING)' in branch
    assert "{SINGLE_NAME_CEILING:.0f}%" not in branch
    assert 'act.get("basis") == "earnings_week"' in branch


@pytest.mark.parametrize("days,basis", [
    (-1, "ceiling"), (0, "earnings_week"),
    (EARNINGS_IMMINENT_DAYS, "earnings_week"), (EARNINGS_IMMINENT_DAYS + 1, "ceiling"),
])
def test_rebalance_actions_window_edges(days, basis):
    trim = next(a for a in rebalance_actions(_book(), earnings_days={"AAA": days})
                if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["basis"] == basis
    if days == 0:
        assert "earnings today" in trim["trigger"]


def test_signals_earnings_basis_zero_share_trim_is_unsized():
    df = _book(shares=1)  # floor(1 * 9/19) = 0
    trim = next(a for a in rebalance_actions(df, earnings_days={"AAA": 3})
                if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["basis"] == "earnings_week"
    assert trim["trim_shares"] == 0 and trim["sized"] is False and trim["urgency"] == "low"


def test_concentrated_book_band_pins_todays_behaviour():
    # KNOWN GAP (owner call queued 2026-10-25): with N <= 7 the earnings-week line
    # (19.29% at N=7) is above SINGLE_NAME_TRIM_TRIGGER (18), so a 19% name in
    # earnings week gets WATCH on the Brief, no oversized REDUCE on the playbook,
    # and a ceiling trim on Signals. Pinned so a fix is a deliberate change.
    df = _book(weight=19.0, n=7)
    assert ts.earnings_overweight_floor(7) > 19.0
    assert _brief_item(df, 3)["action"]["type"] == "WATCH"
    action, *_ = ea._recommend(3, 70.0, 19.0, 70.0, 10.0, 0, "Buy", 100, 5000.0, 8.0,
                               n_positions=7)
    assert action != "REDUCE"
    trim = next(a for a in rebalance_actions(df, earnings_days={"AAA": 3})
                if a["type"] == "trim" and a["ticker"] == "AAA")
    assert trim["basis"] == "ceiling" and trim["target_weight"] == SINGLE_NAME_CEILING


def test_build_earnings_playbook_sizes_from_fractional_shares():
    df = _book(shares=21.9)
    df.loc[df["Ticker"] == "AAA", "Market Value"] = 21.9 * 50.0
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    pb = ea.build_earnings_playbook(df, held, today=_TODAY)
    row = next(p for p in pb if p["ticker"] == "AAA")
    assert row["action"] == "REDUCE"
    assert "sell 10 shares" in row["detail"]


def test_days_to_earnings_uses_the_briefs_parser():
    assert ts.days_to_earnings("2026-10-29T00:00:00", _TODAY) == 3
    assert ts.days_to_earnings("2026-10-29 16:00", _TODAY) == 3


def test_build_rec_event_rows_derives_earnings_days_from_held_data():
    from stock_analyzer.rec_events_capture import build_rec_event_rows
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    rows = build_rec_event_rows(_TODAY, _book(), None, held, None, None, None, None, None)
    trim = next(r for r in rows if r["rec_type"] == "rebal_trim" and r["ticker"] == "AAA")
    assert trim["metric_predicted_after"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT
    rows2 = build_rec_event_rows(_TODAY, _book(), None, {}, None, None, None, None, None)
    trim2 = next(r for r in rows2 if r["rec_type"] == "rebal_trim" and r["ticker"] == "AAA")
    assert trim2["metric_predicted_after"] == SINGLE_NAME_CEILING
