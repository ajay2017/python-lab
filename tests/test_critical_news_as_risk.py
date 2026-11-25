"""EW round 2, R2-B1: a critical_news card that is the PRIMARY of a consolidated group
carries the same-ticker `risk` cards' flags (a sector-concentration breach with its
rebalance-plan payload). Dropping it as "contradictory hold" because the ticker also has
a reduce call must not drop those flags: `decision_bucket.critical_news_as_risk` re-casts
the card as a risk card, and both drop sites (`_reconcile_act`, the Brief's risk-off fold)
keep it. A news card with NO flags is still dropped exactly as before."""
import copy
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import daily_briefing as db
from stock_analyzer import decision_bucket as dbk

pytestmark = pytest.mark.fast

_FLAG = {
    "title": "Sector concentration", "recommendation": "Trim the sector toward its ceiling.",
    "problem": "Tech 40%", "rec_type": "sector_concentration",
    "trim_candidates": [{"ticker": "AAA"}], "redeploy_sectors": ["Energy"],
    "trim_target_pp": 5.0, "trim_target_dollar": 5000.0, "trim_target_denom": 100000.0,
}


def _news(ticker="AAA", flags=None):
    d = {
        "priority": "high", "icon": "🚨", "ticker": ticker, "kind": "critical_news",
        "action": "Watch — Critical News",
        "directive": "Hold for now, but consider setting a tighter manual stop.",
        "why": "2 headlines from tier-1/2 sources", "trigger": "Price -3% intraday",
        "weight": 19.0, "pnl_pct": 5.0,
        "news_headline": "AAA recalls product", "news_compound": -0.6,
    }
    if flags is not None:
        d["risk_flags"] = flags
    return d


def _risk(ticker="AAA"):
    return {"priority": "high", "icon": "⚠️", "ticker": ticker, "kind": "risk",
            "action": "RISK — Sector concentration", "risk_flags": [dict(_FLAG)],
            "weight": None, "pnl_pct": None}


def _port():
    return pd.DataFrame([
        {"Ticker": "AAA", "Weight (%)": 19.0, "P&L (%)": 5.0, "Price": 100.0, "Shares": 10.0},
        {"Ticker": "BBB", "Weight (%)": 5.0, "P&L (%)": 0.0, "Price": 10.0, "Shares": 10.0},
    ])


def _trim_review(reason_key="earnings"):
    return {"icon": "📅", "ticker": "AAA", "priority": "medium",
            "headline": "earnings in 3d", "why": "w", "trigger": "t", "weight": 19.0,
            "action": {"type": "TRIM_TO_TARGET", "trim_shares": 5, "trim_dollars": 500.0,
                       "from_weight": 19.0, "target_weight": 10.0, "reason_key": reason_key}}


# -- the helper ------------------------------------------------------------------------

def test_helper_recasts_a_flagged_news_card_as_a_risk_card_without_mutating_it():
    src = _news(flags=[dict(_FLAG)])
    before = copy.deepcopy(src)
    out = dbk.critical_news_as_risk(src)
    assert src == before                                   # input untouched
    assert out is not src and out["risk_flags"] is not src["risk_flags"]
    assert out["kind"] == "risk" and out["ticker"] == "AAA" and out["priority"] == "high"
    assert out["action"] == "RISK — Sector concentration"  # the consolidation's own format
    assert out["risk_flags"] == [_FLAG]
    for k in ("directive", "why", "trigger", "news_headline", "news_compound"):
        assert k not in out                                # the hold read is gone
    assert out["weight"] == 19.0 and out["pnl_pct"] == 5.0
    assert out["reason"] == _FLAG["recommendation"]


def test_helper_multi_flag_action_text_matches_the_consolidation():
    src = _news(flags=[dict(_FLAG), {"title": "Beta", "recommendation": "Trim beta."}])
    out = dbk.critical_news_as_risk(src)
    assert out["action"] == "RISK — 2 flags on AAA"
    assert out["reason"] == f"{_FLAG['recommendation']} Trim beta."


@pytest.mark.parametrize("item", [
    None, "x", {}, _news(), _news(flags=[]), _news(flags=None),
    {"kind": "risk", "ticker": "AAA", "risk_flags": [dict(_FLAG)]},
    {"kind": "stop_breach", "ticker": "AAA", "risk_flags": [dict(_FLAG)]},
])
def test_helper_is_none_without_a_news_kind_or_flags(item):
    assert dbk.critical_news_as_risk(item) is None


# -- (a) consolidated news+risk, earnings trim on the same ticker ------------------------

def _consolidated():
    out = db._consolidate_act_today([_news(), _risk()], _port())
    assert len(out) == 1 and out[0]["kind"] == "critical_news" and out[0]["risk_flags"]
    return out


def test_consolidated_news_primary_keeps_its_sector_flag_beside_an_earnings_trim():
    act = _consolidated()
    held = {"AAA": {"earnings": "2026-10-29"}}
    # U6: _port() has no Score column, so AAA also reads as a weak-large holding and the
    # weak-large trim (8%) would now WIN over this earnings trim (10%), which becomes a
    # covered Watch. A score above the weak-conviction line keeps this test about what it
    # always pinned: the earnings trim beside the sector flag.
    review = db._review_list(_port().assign(Score=70.0), [], [], held, date(2026, 10, 26),
                             portfolio_value=100_000.0, act_today=act)
    trim = next(i for i in review if i["ticker"] == "AAA" and i["icon"] == "📅")
    assert trim["action"]["type"] == "TRIM_TO_TARGET"       # U5: news card doesn't skip it
    split = dbk.split_defensive(act, review)
    cards = [i for i in split["act"] if i.get("ticker") == "AAA"]
    risk = [i for i in cards if i.get("kind") == "risk"]
    assert len(risk) == 1 and risk[0]["risk_flags"][0]["rec_type"] == "sector_concentration"
    assert not [i for i in cards if i.get("kind") == "critical_news"]
    assert any(i.get("_source") == "review" for i in cards)  # the trim is still there
    # the fold note lands on the real reduce card, not on the converted risk card
    assert "negative headlines" in trim_card(cards)["why"]
    assert "negative headlines" not in risk[0].get("why", "")


def trim_card(cards):
    return next(i for i in cards if i.get("_source") == "review")


# -- (b) weak-large TRIM_TO_TARGET -------------------------------------------------------

def test_flag_survives_a_weak_large_trim_too():
    split = dbk.split_defensive(_consolidated(), [_trim_review("weak_large")])
    risk = [i for i in split["act"] if i.get("kind") == "risk"]
    assert len(risk) == 1 and risk[0]["risk_flags"][0]["trim_target_pp"] == 5.0
    assert not [i for i in split["act"] if i.get("kind") == "critical_news"]


# -- (c) the risk-off fold -----------------------------------------------------------------

_HELD = {t: {"risk_metrics": {"beta": b}} for t, b in
         [("AAA", 2.0), ("BBB", 1.5), ("CCC", 1.4), ("DDD", 1.3)]}


def _brief(monkeypatch, act):
    monkeypatch.setattr(db, "_act_today", lambda *a, **k: [dict(x) for x in act])
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: [])
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: {})
    port = pd.DataFrame([
        {"Ticker": t, "Weight (%)": w, "Price": 100.0, "Shares": 10.0, "P&L (%)": 5.0}
        for t, w in [("AAA", 20.0), ("BBB", 15.0), ("CCC", 10.0), ("DDD", 10.0)]])
    return db.build_daily_briefing(
        port, [], [], [], [], _HELD, None, 10000.0, date(2026, 10, 16),
        fragility={"severity": "fragile"},
        spy_trend_df=pd.DataFrame({"Close": [200.0 - i * 0.5 for i in range(250)]}),
        vix_level=14.0)


def test_risk_off_fold_keeps_the_sector_flag(monkeypatch):
    brief = _brief(monkeypatch, [_news(flags=[dict(_FLAG)])])
    aaa = [i for i in brief["act_today"] if i.get("ticker") == "AAA"]
    assert sorted(i["kind"] for i in aaa) == ["risk", "risk_off_derisk"]
    ro = next(i for i in aaa if i["kind"] == "risk_off_derisk")
    assert "recent negative headlines" in ro["why"]        # the headline is still folded
    risk = next(i for i in aaa if i["kind"] == "risk")
    assert risk["risk_flags"][0]["rec_type"] == "sector_concentration"
    assert "directive" not in risk and "news_headline" not in risk


# -- (d) a flag-less news card is dropped exactly as before ---------------------------------

def test_flagless_news_card_is_still_dropped_with_the_fold_note():
    split = dbk.split_defensive([_news()], [_trim_review()])
    cards = [i for i in split["act"] + split["aware"] if i.get("ticker") == "AAA"]
    assert [i.get("_source") for i in cards] == ["review"]
    assert cards[0]["why"].endswith(
        "⚠ Also flagged on recent negative headlines — already factored into this "
        "reduce; it isn't a separate 'hold'.")


def test_flagless_news_card_is_still_dropped_by_the_risk_off_fold(monkeypatch):
    brief = _brief(monkeypatch, [_news()])
    aaa = [i for i in brief["act_today"] if i.get("ticker") == "AAA"]
    assert [i["kind"] for i in aaa] == ["risk_off_derisk"]
