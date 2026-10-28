"""2026-10-28: the last three round-up trim sites migrated to floored sizing
(owner rule 2026-10-23: no surface proposes a trim that sizes 0 shares, and a
sub-share excess is never rounded UP to 1).

  * Brief weak-large TRIM_TO_TARGET  (daily_briefing._review_list)
  * Brief macro PROTECTIVE_TRIM      (daily_briefing._review_list)
  * Signals "review" half_shares     (portfolio.rebalance_actions)
"""
import datetime

import pandas as pd
import pytest

import stock_analyzer.daily_briefing as db_mod
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import (
    LARGE_POSITION_WEIGHT_PCT,
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    MACRO_BROAD_EXPOSURE_PCT,
    WEAK_CONVICTION_SCORE,
    WEAK_LARGE_TRIM_TO_PCT,
)
from stock_analyzer.macro_calendar import HIGH as MC_HIGH
from stock_analyzer.portfolio import rebalance_actions
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = datetime.date(2026, 7, 27)
_PV = 100_000.0


# -- trim_sizing.shares_for_dollars ------------------------------------------

@pytest.mark.parametrize("dollars,price,expected", [
    (160.0, 100.0, 1),     # 1.6 shares -> 1, never 2
    (99.0, 100.0, 0),      # under one share -> 0, never rounded up
    (100.0, 100.0, 1),
    (0.0, 100.0, 0),
    (-5.0, 100.0, 0),
    (500.0, 0.0, 0),       # bad price
    (500.0, None, 0),
    (None, 100.0, 0),
    (float("nan"), 100.0, 0),
])
def test_shares_for_dollars(dollars, price, expected):
    assert ts.shares_for_dollars(dollars, price) == expected


# -- Site 1: weak-large TRIM_TO_TARGET ---------------------------------------

def _wl(shares, price):
    # weight is held at LARGE+2 (12%) regardless; shares/price set the grain.
    return make_port_df([{
        "ticker": "AAA", "weight": LARGE_POSITION_WEIGHT_PCT + 2,
        "score": WEAK_CONVICTION_SCORE - 10, "price": price, "shares": shares,
    }])


def test_weak_large_excess_under_one_share_is_watch_not_trim():
    w = LARGE_POSITION_WEIGHT_PCT + 2
    # 2 shares: floor(2 * (w - target) / w) = 0
    assert ts.shares_to_trim(2, w, WEAK_LARGE_TRIM_TO_PCT) == 0
    items = db_mod._review_list(_wl(2, 6000.0), [], [], {}, _TODAY, portfolio_value=_PV)
    item = find_item(items, "AAA")
    assert item is not None
    assert item["action"] == {"type": "WATCH"}
    assert "less than one share" in item["why"]
    assert f"{WEAK_LARGE_TRIM_TO_PCT:.1f}%" in item["why"]
    assert f"{LARGE_POSITION_WEIGHT_PCT:.0f}%" in item["why"]
    assert "act before close" not in item["trigger"]
    assert "No trade sized today" in item["trigger"]
    assert item["trigger"].endswith("the score falls into Sell.")
    assert item["headline"].startswith("largest weak holding")


def test_weak_large_floors_to_whole_shares_and_reconciles_dollars():
    w = LARGE_POSITION_WEIGHT_PCT + 2
    # 5 shares at 12%: 5 * 4/12 = 1.67 -> floor 1 (the old round() gave 2)
    assert ts.shares_to_trim(5, w, WEAK_LARGE_TRIM_TO_PCT) == 1
    items = db_mod._review_list(_wl(5, 2400.0), [], [], {}, _TODAY, portfolio_value=_PV)
    a = find_item(items, "AAA")["action"]
    assert a["type"] == "TRIM_TO_TARGET" and a["reason_key"] == "weak_large"
    assert a["trim_shares"] == 1
    assert a["trim_dollars"] == round(1 * 2400.0, 0)


def test_weak_large_sell_signal_min_trim_requires_a_whole_share():
    act = [{"ticker": "AAA", "kind": "sell_signal", "priority": "high",
            "directive": "Reduce or exit.", "why": "Sell zone."}]
    items = db_mod._review_list(_wl(2, 6000.0), [], [], {}, _TODAY,
                                portfolio_value=_PV, act_today=act)
    assert "min_trim" not in act[0]
    assert "At minimum" not in act[0]["directive"]
    # the reduce card exists, so no standalone weak-large item either
    assert find_item(items, "AAA") is None


@pytest.mark.parametrize("price,pv", [(0.0, _PV), (6000.0, 0.0)])
def test_weak_large_unsized_guard_fail_is_watch_not_zero_share_trim(price, pv):
    items = db_mod._review_list(_wl(2, price), [], [], {}, _TODAY, portfolio_value=pv)
    item = find_item(items, "AAA")
    assert item is not None
    assert item["action"] == {"type": "WATCH"}
    assert "share count, price or portfolio value is unavailable" in item["why"]
    assert "no trim can be sized" in item["why"]
    assert f"{LARGE_POSITION_WEIGHT_PCT:.0f}%" in item["why"]
    assert "act before close" not in item["trigger"]
    assert "No trade sized" in item["trigger"]


@pytest.mark.parametrize("shares", [0, float("nan")])
def test_weak_large_missing_or_zero_shares_is_unavailable_watch(shares):
    items = db_mod._review_list(_wl(shares, 100.0), [], [], {}, _TODAY, portfolio_value=_PV)
    item = find_item(items, "AAA")
    assert item["action"] == {"type": "WATCH"}
    assert "share count, price or portfolio value is unavailable" in item["why"]
    assert "less than one share" not in item["why"]


def test_new_weak_large_watch_items_classify_aware():
    from stock_analyzer.decision_bucket import classify_bucket
    items = db_mod._review_list(_wl(2, 6000.0), [], [], {}, _TODAY, portfolio_value=_PV)
    it = dict(find_item(items, "AAA"), _source="review")
    assert classify_bucket(it) == "aware"


# -- Site 2: macro PROTECTIVE_TRIM -------------------------------------------

def _macro(monkeypatch, price, shares=10.0, pv=10_000.0):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _cat: ["Tech"])
    ev = {"date": (_TODAY + datetime.timedelta(days=1)).isoformat(),
          "impact": MC_HIGH, "category": "Inflation", "event": "CPI"}
    df = pd.DataFrame([
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 40.0, "Score": 60.0,
         "Score Available": True, "Shares": shares, "Price": price},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0,
         "Score Available": True, "Shares": 1.0, "Price": 10.0},
    ])
    items = db_mod._review_list(df, [], [ev], {}, _TODAY, portfolio_value=pv, act_today=[])
    return [i for i in items if i.get("icon") == "🌐"]


def test_macro_fixture_sits_in_the_sized_trim_band():
    assert MACRO_AFFECTED_TRIM_THRESHOLD_PCT < 40.0 < MACRO_BROAD_EXPOSURE_PCT


def test_protective_trim_under_one_share_is_watch_without_trim_fields(monkeypatch):
    pv = 10_000.0
    target = round(pv * MACRO_AFFECTED_TRIM_REDUCTION_PP / 100, 0)
    price = target + 50.0                     # one share is worth more than the target
    (item,) = _macro(monkeypatch, price)
    a = item["action"]
    assert a["type"] == "WATCH"
    assert a["from_exposure"] == 40.0
    assert a["threshold"] == MACRO_AFFECTED_TRIM_THRESHOLD_PCT
    assert not any(k.startswith("trim_") for k in a)
    assert "under one share of AAA" in item["why"]
    assert f"{MACRO_AFFECTED_TRIM_REDUCTION_PP:.0f}pp trim" in item["why"]
    assert f"{price / pv * 100:.1f}% of the book" in item["why"]


def test_protective_trim_price_unavailable_is_watch_without_trim_fields(monkeypatch):
    (item,) = _macro(monkeypatch, 0.0)
    a = item["action"]
    assert a["type"] == "WATCH"
    assert a["from_exposure"] == 40.0
    assert a["threshold"] == MACRO_AFFECTED_TRIM_THRESHOLD_PCT
    assert not any(k.startswith("trim_") for k in a)
    assert "AAA's price is unavailable, so no trim can be sized" in item["why"]
    assert "Re-check once prices load." in item["why"]


def test_protective_trim_dollars_equal_to_price_is_one_share(monkeypatch):
    pv = 10_000.0
    price = round(pv * MACRO_AFFECTED_TRIM_REDUCTION_PP / 100, 0)   # exactly one share
    (item,) = _macro(monkeypatch, price)
    a = item["action"]
    assert a["type"] == "PROTECTIVE_TRIM" and a["trim_shares"] == 1
    assert a["trim_dollars"] == price


def test_macro_watch_classifies_aware_and_keeps_same_ticker_news_watch(monkeypatch):
    from stock_analyzer.decision_bucket import classify_bucket
    (item,) = _macro(monkeypatch, 50_000.0)
    assert item["action"]["type"] == "WATCH"
    assert classify_bucket(dict(item, _source="review")) == "aware"
    # No trim_ticker on the WATCH, so the final-pass news dedup keeps AAA's news WATCH.
    pdf = make_port_df([
        {"ticker": "AAA", "weight": 35.0, "sector": "Semiconductors", "score": 60.0, "price": 50_000.0},
        {"ticker": "BBB", "weight": 20.0, "sector": "Healthcare"},
    ])
    ev = {"date": "2026-10-20", "impact": "HIGH", "category": "Employment",
          "event": "Non-Farm Payrolls", "affected_tickers": ["AAA"]}
    from stock_analyzer import held_news_check as hnc
    today = datetime.date(2026, 10, 19)
    news = [{"ticker": "AAA", "compound": -0.10, "tier": 3,
             "ts": hnc.news_window_start(today).timestamp() + 100, "title": "AAA headline"}]
    rev = db_mod._review_list(pdf, news, [ev], {}, today, portfolio_value=100_000.0, act_today=[])
    macro = [i for i in rev if i.get("icon") == "🌐"]
    assert macro and macro[0]["action"]["type"] == "WATCH", "precondition: macro is an under-one-share WATCH"
    assert [i for i in rev if i.get("watch_kind") == "news" and i.get("ticker") == "AAA"]


def test_protective_trim_floors_one_point_six_shares_to_one(monkeypatch):
    pv = 10_000.0
    target = round(pv * MACRO_AFFECTED_TRIM_REDUCTION_PP / 100, 0)
    price = target / 1.6
    (item,) = _macro(monkeypatch, price)
    a = item["action"]
    assert a["type"] == "PROTECTIVE_TRIM"
    assert a["trim_shares"] == 1              # the old round() gave 2
    assert a["trim_dollars"] == round(price, 0)
    assert a["trim_ticker"] == "AAA"


# -- Site 3: Signals "review" half_shares ------------------------------------

def _review(shares):
    price = 100.0
    row = {
        "Ticker": "AAA", "Weight (%)": 5.0, "P&L (%)": 20.0, "Price": price,
        "Shares": shares, "Signal": "Sell", "Score": 30.0, "Stop": 90.0,
        "Gap to Stop (%)": 10.0, "Market Value": shares * price,
        "Avg Cost": 80.0, "Stop Type": "ATR Stop", "Sector": "Tech",
    }
    acts = rebalance_actions(pd.DataFrame([row]))
    return next(a for a in acts if a["type"] == "review")


@pytest.mark.parametrize("shares,half", [(1, 0), (0.5, 0), (2, 1), (5, 2), (10, 5), (float("nan"), 0)])
def test_review_half_shares_is_floored(shares, half):
    assert _review(shares)["half_shares"] == half
