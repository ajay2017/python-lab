"""Technical-pillar withhold (C2, 2026-10-15): `pillar_availability` and every
decision consumer that now asks it "can this bundle back a verdict?".

Every BUY-side invariant here is paired: the SAME fixture is first asserted to
FIRE with ``t_available=True`` (so the withhold run cannot pass vacuously),
then flipped to ``t_available=False``.
"""
from __future__ import annotations

import inspect
import math

import pandas as pd
import pytest

from tests._held_stops import hs_for
from stock_analyzer import analyst_intel, premortem_advisor
from stock_analyzer.constants import (
    COMPOSITE_BUY, COMPOSITE_STRONG_BUY, ETF_AUM_THIN_FLOOR_USD, TECHNICAL_MIN_BARS,
)
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.etf_candidates import etf_newpick_eligible
from stock_analyzer.exit_advisor import classify_deterioration_tier
from stock_analyzer.pillar_availability import (
    technical_clause, technical_history_short, unmeasured_pillars, verdict_basis,
)
from stock_analyzer.portfolio import rebalance_actions
from stock_analyzer.portfolio import alerts, build_portfolio_df
from stock_analyzer.util import score_withheld_banner_text
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation
from stock_analyzer import quick_research as qr
from stock_analyzer.portfolio import WITHHELD_SIGNAL
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = __import__("datetime").date(2026, 10, 15)


# ── truth table ──────────────────────────────────────────────────────────────

def _etf(**o):
    b = {"asset_type": "etf", "fundamentals_available": False, "val_available": False,
         "etf_available": True, "etf_total": 80.0}
    b.update(o)
    return b


def test_legacy_bundle_no_keys_is_equity_and_nothing_missing():
    assert verdict_basis({"total": 70}) == "equity"
    assert unmeasured_pillars({"total": 70}) == []


def test_none_and_empty_bundle():
    assert verdict_basis(None) == "withheld"
    assert unmeasured_pillars(None) == []
    assert unmeasured_pillars({}) == []


@pytest.mark.parametrize("bq,val,t,expected,missing", [
    (True, True, True, "equity", []),
    (True, True, False, "withheld", ["Technical"]),
    (False, True, True, "withheld", ["Business Quality"]),
    (True, False, True, "withheld", ["Valuation"]),
    (False, False, False, "withheld", ["Technical", "Business Quality", "Valuation"]),
])
def test_stock_truth_table(bq, val, t, expected, missing):
    b = {"fundamentals_available": bq, "val_available": val, "t_available": t}
    assert verdict_basis(b) == expected
    assert unmeasured_pillars(b) == missing


def test_etf_ok_is_etf_basis():
    assert verdict_basis(_etf()) == "etf"
    assert unmeasured_pillars(_etf()) == []


def test_etf_with_technical_unmeasured_is_withheld_naming_only_technical():
    b = _etf(t_available=False)
    assert verdict_basis(b) == "withheld"
    assert unmeasured_pillars(b) == ["Technical"]


def test_etf_without_expense_ratio_withheld():
    b = _etf(etf_available=False)
    assert verdict_basis(b) == "withheld"
    assert unmeasured_pillars(b) == ["Expense ratio"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "abc", "-inf"])
def test_etf_non_finite_total_is_not_etf_basis(bad):
    assert verdict_basis(_etf(etf_total=bad)) == "withheld"


def test_stock_never_takes_etf_basis_even_with_etf_flags():
    b = {"asset_type": "stock", "fundamentals_available": False,
         "etf_available": True, "etf_total": 80.0}
    assert verdict_basis(b) == "withheld"


# ── portfolio.build_portfolio_df ─────────────────────────────────────────────

def _loaded_row(price=100.0, stop=90.0, **o):
    r = {"current_price": price, "stop": stop, "sector": "Technology",
         "rec": {"icon": "G", "label": "Buy"}, "total": 70}
    r.update(o)
    return r


_H = [{"Ticker": "AAA", "Shares": 10, "Avg Cost ($)": 50.0}]


def test_portfolio_score_available_only_when_technical_measured():
    ok = build_portfolio_df(_H, {"AAA": _loaded_row(t_available=True, t_bars=120)}, held_stops=hs_for({"AAA": _loaded_row(t_available=True, t_bars=120)}))
    assert ok.iloc[0]["Score Available"] == True  # noqa: E712 - fires first
    assert ok.iloc[0]["Score"] == 70
    assert ok.attrs["score_withheld"] == []
    bad = build_portfolio_df(_H, {"AAA": _loaded_row(t_available=False, t_bars=30)}, held_stops=hs_for({"AAA": _loaded_row(t_available=False, t_bars=30)}))
    row = bad.iloc[0]
    assert row["Score Available"] == False  # noqa: E712
    assert pd.isna(row["Score"])
    assert row["Signal"] == WITHHELD_SIGNAL
    assert bad.attrs["score_withheld"] == [
        {"ticker": "AAA", "missing": ["Technical"], "t_bars": 30}]


def test_portfolio_legacy_bundle_equals_t_available_true():
    legacy = build_portfolio_df(_H, {"AAA": _loaded_row()}, held_stops=hs_for({"AAA": _loaded_row()}))
    explicit = build_portfolio_df(_H, {"AAA": _loaded_row(t_available=True)}, held_stops=hs_for({"AAA": _loaded_row(t_available=True)}))
    pd.testing.assert_frame_equal(legacy, explicit)
    assert legacy.attrs["score_withheld"] == explicit.attrs["score_withheld"] == []


def test_portfolio_etf_t_unavailable_withheld_no_crash_on_missing_etf_rec():
    etf_rec = {"icon": "G", "label": "Buy"}
    good = _loaded_row(asset_type="etf", fundamentals_available=False, val_available=False,
                       etf_available=True, etf_total=80.0, etf_rec=etf_rec, t_available=True)
    assert build_portfolio_df(_H, {"AAA": good}, held_stops=hs_for({"AAA": good})).iloc[0]["Score"] == 80.0   # fires first
    bad = dict(good, t_available=False, etf_rec=None)    # etf_rec must never be touched
    df = build_portfolio_df(_H, {"AAA": bad}, held_stops=hs_for({"AAA": bad}))
    assert pd.isna(df.iloc[0]["Score"])
    assert df.attrs["score_withheld"][0]["missing"] == ["Technical"]


def test_protective_outputs_identical_regardless_of_technical_flag():
    """Stops, gap-to-stop, weight and the stop/concentration alerts are
    price-based: a withheld technical pillar must not touch them."""
    def run(t):
        df = build_portfolio_df(_H, {"AAA": _loaded_row(price=100.0, stop=99.0, t_available=t)}, held_stops=hs_for({"AAA": _loaded_row(price=100.0, stop=99.0, t_available=t)}))
        al = [a for a in alerts(df) if a["category"] in ("stop", "concentration")]
        return df, al
    df_t, al_t = run(True)
    df_f, al_f = run(False)
    assert al_t, "fixture must actually fire a stop/concentration alert"
    assert al_t == al_f
    for col in ("Stop", "Stop Type", "Gap to Stop (%)", "Weight (%)", "Price", "Market Value"):
        assert list(df_t[col]) == list(df_f[col])


def test_deterioration_ladder_takes_no_bundle_and_still_exits():
    params = inspect.signature(classify_deterioration_tier).parameters
    assert not any("bundle" in p or "available" in p for p in params)
    assert classify_deterioration_tier(
        dd_from_peak_pct=40.0, atr_pct=0.0, trend_broken_now=True, below_ma_count=3,
        rel_strength=-5.0, price=60.0, avg_cost=100.0, dollar_pnl=-400.0, age_days=None,
    ) == "EXIT"


# ── daily_briefing new-pick lane ─────────────────────────────────────────────

def _scanner(ticker="NEW"):
    return pd.DataFrame([{
        "Ticker": ticker, "Score": COMPOSITE_BUY + 10, "Price": 50.0, "Signal": "Buy",
        "Sector": "Healthcare", "RSI": 60.0, "1M Momentum": 5.0, "Trend": "Up",
    }])


def _grow(**bundle_over):
    comp = {"NEW": {"total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
                    "fundamentals_available": True, **bundle_over}}
    port = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    return _grow_today(port, _scanner(), [], {}, _TODAY, 100_000.0,
                       {"tone": "bull"}, composites=comp)


def test_new_pick_lane_withholds_when_technical_unmeasured():
    ok = _grow(t_available=True)
    assert find_item(ok["new_picks"], "NEW") is not None          # fires first
    bad = _grow(t_available=False, t_bars=30)
    assert find_item(bad["new_picks"], "NEW") is None
    held_out = find_item(bad["composite_unavailable"], "NEW")
    assert held_out is not None
    assert held_out["missing"] == ["Technical"]


def test_new_pick_lane_legacy_bundle_same_as_true():
    legacy = _grow()
    explicit = _grow(t_available=True)
    assert find_item(legacy["new_picks"], "NEW") is not None
    assert [p["ticker"] for p in legacy["new_picks"]] == [p["ticker"] for p in explicit["new_picks"]]


# ── watchlist ────────────────────────────────────────────────────────────────

def _wl(**o):
    d = {"total": 80.0, "rec": {"label": "Buy"}, "current_price": 100.0,
         "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
         "targets": {"base": 130.0}, "earnings": None}
    d.update(o)
    return d


def test_watchlist_enter_now_withheld_when_technical_unmeasured():
    ok = build_watchlist_recommendation("XYZ", _wl(t_available=True))
    assert ok["action"] == "ENTER_NOW"                              # fires first
    bad = build_watchlist_recommendation("XYZ", _wl(t_available=False, t_bars=30))
    assert bad["action"] == "DATA_UNAVAILABLE"
    assert "price history too short" in bad["title"]
    assert "30 sessions" in bad["summary"] and str(TECHNICAL_MIN_BARS) in bad["summary"]
    assert "ENTER_NOW" in bad["summary"]
    assert "None" not in bad["summary"] + bad["detail"]


def test_watchlist_legacy_bundle_same_as_true():
    a = build_watchlist_recommendation("XYZ", _wl())
    b = build_watchlist_recommendation("XYZ", _wl(t_available=True))
    assert a["action"] == b["action"] == "ENTER_NOW"


def test_watchlist_fund_only_keeps_original_wording():
    r = build_watchlist_recommendation("XYZ", _wl(fundamentals_available=False))
    assert r["title"] == "XYZ — Can't Assess Right Now (data unavailable)"
    assert r["summary"].startswith("Fundamentals and/or valuation data couldn't be sourced")


def test_watchlist_both_missing_names_both():
    r = build_watchlist_recommendation(
        "XYZ", _wl(fundamentals_available=False, t_available=False, t_bars=12))
    assert r["action"] == "DATA_UNAVAILABLE"
    assert "Fundamentals and/or valuation" in r["summary"]
    assert "12 sessions" in r["summary"]


def test_watchlist_unknown_bars_prints_no_none():
    r = build_watchlist_recommendation("XYZ", _wl(t_available=False))
    assert "None" not in r["summary"]
    assert f"{TECHNICAL_MIN_BARS} sessions needed" in r["summary"]


# ── etf_candidates ───────────────────────────────────────────────────────────

def _good_etf(**o):
    b = {"asset_type": "etf", "stale_as_of": None, "etf_available": True,
         "etf_total": COMPOSITE_STRONG_BUY + 5.0,
         "etf_facts": {"total_assets": ETF_AUM_THIN_FLOOR_USD + 1.0}}
    b.update(o)
    return b


def test_etf_eligibility_requires_technical_measured():
    ok, why = etf_newpick_eligible(_good_etf(t_available=True), "bull")
    assert (ok, why) == (True, None)                                # fires first
    ok2, why2 = etf_newpick_eligible(_good_etf(t_available=False, t_bars=30), "bull")
    assert ok2 is False
    assert why2 == "technical trend unmeasured (30 bars)"
    ok3, why3 = etf_newpick_eligible(_good_etf(t_available=False), "bull")
    assert ok3 is False and "None" not in why3


def test_etf_eligibility_legacy_same_as_true():
    assert etf_newpick_eligible(_good_etf(), "bull") == (True, None)


# ── quick_research ───────────────────────────────────────────────────────────

def _qr_data(**o):
    d = {
        "df": pd.DataFrame({"Close": [100, 101, 102]}),
        "t_signals": {"MA Trend": "Price above rising 50-day MA — bullish"},
        "rec": {"icon": "G", "label": "Buy", "rationale": "Solid", "color": "#0f0"},
        "total": 72.0, "current_price": 102,
        "financials": {"analyst_target": None, "short_pct_float": None},
        "revisions": {"net": 0}, "earnings": None, "name": "T", "sector": "Tech",
        "headlines": [],
    }
    d.update(o)
    return d


def test_quick_research_withholds_on_technical_and_names_it():
    ok = qr.research_ticker("AAA", _qr_data(t_available=True))
    assert ok["signal"] == "Buy"                                    # fires first
    bad = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=30))
    assert bad["signal"] == "Verdict withheld" and bad["score"] is None
    assert "30 sessions" in bad["bullets"][0]
    assert "fundamentals couldn't be sourced" not in bad["bullets"][0]
    assert bad["fundamentals_available"] is True      # bq/val meaning unchanged
    assert bad["verdict_basis"] == "withheld" and bad["missing"] == ["Technical"]


def test_quick_research_legacy_same_as_true():
    a = qr.research_ticker("AAA", _qr_data())
    b = qr.research_ticker("AAA", _qr_data(t_available=True))
    assert a["signal"] == b["signal"] == "Buy"


# ── banner ───────────────────────────────────────────────────────────────────

_LADDER = "trend-break exit ladder can't assess it"


def test_banner_technical_only():
    t = score_withheld_banner_text([{"ticker": "AAA", "missing": ["Technical"], "t_bars": 30}])
    assert "30 sessions" in t and str(TECHNICAL_MIN_BARS) in t
    assert _LADDER in t and "only its stop and concentration limits protect it" in t
    assert "stops and risk metrics still apply" not in t.lower()
    assert "**" not in t


def test_banner_differing_bars_listed_per_ticker():
    t = score_withheld_banner_text([
        {"ticker": "AAA", "missing": ["Technical"], "t_bars": 30},
        {"ticker": "BBB", "missing": ["Technical"], "t_bars": 12}])
    assert "AAA 30 sessions" in t and "BBB 12 sessions" in t


def test_banner_bq_only_and_legacy_keep_original_sentence_exactly():
    orig = (
        "ℹ️ 1 holding shown without a composite score: AAA. Company fundamentals "
        "aren't available for these from any provider (e.g. funds/ETFs), so a "
        "composite score would be a guess rather than a measurement — it's "
        "withheld here too, matching the Analysis page. Price, weight, stops and "
        "risk metrics still apply."
    )
    assert score_withheld_banner_text([{"ticker": "AAA", "missing": ["Business Quality"]}]) == orig
    assert score_withheld_banner_text([{"ticker": "AAA"}]) == orig
    assert _LADDER not in orig


def test_banner_mixed_list_ladder_caveat_only_for_technical_tickers():
    t = score_withheld_banner_text([
        {"ticker": "AAA", "missing": ["Technical"], "t_bars": 30},
        {"ticker": "BBB", "missing": ["Valuation"]},
        {"ticker": "CCC"}])
    assert t.startswith("ℹ️ 3 holdings shown without a composite score.")
    assert "BBB, CCC: company fundamentals" in t
    assert t.count(_LADDER) == 1
    assert t.index("BBB") < t.index(_LADDER)
    assert "AAA: price history too short" in t
    assert "None" not in t and "nan" not in t.lower()


def test_banner_unknown_bars_no_none():
    t = score_withheld_banner_text([{"ticker": "AAA", "missing": ["Technical"], "t_bars": None}])
    assert "None" not in t and f"{TECHNICAL_MIN_BARS} sessions needed" in t


# ── analyst_intel / premortem ────────────────────────────────────────────────

def test_trustworthy_composite_rejects_unmeasured_technical():
    assert analyst_intel.trustworthy_composite({"total": 70.0, "t_available": True}) == 70.0
    assert analyst_intel.trustworthy_composite({"total": 70.0}) == 70.0     # legacy
    assert analyst_intel.trustworthy_composite({"total": 70.0, "t_available": False}) is None


def test_driving_pillar_skips_unmeasured_technical():
    b = {"t_score": 99.0, "bq_score": 60.0, "val_score": 50.0, "s_score": 40.0,
         "t_signals": {}, "bq_signals": {}, "val_signals": {}}
    assert premortem_advisor.driving_pillar_from_bundle(b)["driving_pillar"] == "technical"
    b["t_available"] = False
    assert premortem_advisor.driving_pillar_from_bundle(b)["driving_pillar"] == "fundamentals"
    assert math.isfinite(b["t_score"])


# ── review round 2: wrong-reason wording when bars >= floor ─────────────────

_LONG = TECHNICAL_MIN_BARS + 10          # >= floor: history is NOT the problem


def test_technical_history_short_boundaries():
    assert technical_history_short(None) is True
    assert technical_history_short("abc") is True
    assert technical_history_short(TECHNICAL_MIN_BARS - 1) is True
    assert technical_history_short(TECHNICAL_MIN_BARS) is False
    assert technical_history_short(_LONG) is False


def test_technical_clause_bars_at_or_above_floor_never_says_too_short():
    c = technical_clause(_LONG)
    assert "too short" not in c and str(_LONG) not in c
    assert "couldn't be computed" in c
    assert "None" not in technical_clause(None)
    assert "too short" in technical_clause(30) and "30 sessions" in technical_clause(30)


def test_banner_bars_above_floor_has_no_ladder_caveat_or_too_short():
    t = score_withheld_banner_text([{"ticker": "AAA", "missing": ["Technical"], "t_bars": _LONG}])
    assert "couldn't be computed from recent prices" in t
    assert "too short" not in t and "ladder" not in t and "stop and concentration" not in t
    assert "None" not in t


def test_banner_groups_by_cause_and_grammar():
    t = score_withheld_banner_text([
        {"ticker": "AAA", "missing": ["Technical"], "t_bars": 30},
        {"ticker": "BBB", "missing": ["Technical"], "t_bars": 12},
        {"ticker": "CCC", "missing": ["Technical"], "t_bars": _LONG}])
    short_part, flat_part = t.split("CCC:")
    assert "AAA, BBB: price history too short" in short_part
    assert "composites are withheld" in short_part
    assert "can't assess them either" in short_part and "only their stop" in short_part
    assert "protect them." in short_part
    assert "ladder" not in flat_part and "too short" not in flat_part
    one = score_withheld_banner_text([{"ticker": "AAA", "missing": ["Technical"], "t_bars": 30}])
    assert "can't assess it either" in one and "only its stop" in one and "protect it." in one


def test_banner_unknown_bars_is_treated_as_short():
    t = score_withheld_banner_text([{"ticker": "AAA", "missing": ["Technical"]}])
    assert "too short" in t and "ladder" in t and "None" not in t


def test_watchlist_bars_above_floor_no_too_short_no_sessions_advice():
    ok = build_watchlist_recommendation("XYZ", _wl(t_available=True))
    assert ok["action"] == "ENTER_NOW"                                # fires first
    r = build_watchlist_recommendation("XYZ", _wl(t_available=False, t_bars=_LONG))
    assert r["action"] == "DATA_UNAVAILABLE"
    blob = " ".join([r["title"], r["summary"], r["detail"]] + list(r["conditions_missing"])).lower()
    assert "too short" not in blob and "sessions have accrued" not in blob
    assert "couldn't" in blob and "flat or halted" in blob
    assert "re-check later" in blob


def test_watchlist_both_missing_above_floor_wording():
    r = build_watchlist_recommendation(
        "XYZ", _wl(fundamentals_available=False, t_available=False, t_bars=_LONG))
    blob = (r["summary"] + r["detail"]).lower()
    assert "too short" not in blob and "flat or halted" in blob


def test_watchlist_etf_never_gets_technical_only_recheck_card():
    r = build_watchlist_recommendation("SPY", _wl(
        asset_type="etf", fundamentals_available=False, val_available=False,
        t_available=False, t_bars=30))
    assert r["action"] == "DATA_UNAVAILABLE"
    blob = (r["title"] + r["summary"] + r["detail"]).lower()
    assert "sessions have accrued" not in blob and "too short" not in blob
    assert r["title"] == "SPY — Can't Assess Right Now (data unavailable)"
    # even an ETF with fundamentals-style flags True but technical unmeasured
    # must not promise a re-check once sessions accrue
    r2 = build_watchlist_recommendation("SPY", _wl(asset_type="etf", t_available=False, t_bars=30))
    assert "sessions have accrued" not in r2["detail"].lower()


# ── quick_research entry-timing coherence ───────────────────────────────────

def test_quick_research_entry_timing_buy_now_replaced_when_technical_unmeasured():
    # 3 bars -> RSI None -> _entry_timing defaults it to 50 -> buy_now
    ok = qr.research_ticker("AAA", _qr_data(t_available=True))
    assert ok["entry"]["verdict"] == "buy_now"                         # fires first
    bad = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=3))
    assert bad["entry"]["verdict"] == "not_assessed"
    assert "Normal Entry Conditions" not in bad["bullets"][2]
    assert "Technical setup supports" not in bad["bullets"][2]
    for k in ("verdict", "label", "color", "bg", "icon", "explanation"):
        assert k in bad["entry"] and bad["entry"][k]
    long_bad = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=_LONG))
    assert long_bad["entry"]["verdict"] == "not_assessed"


def test_quick_research_protective_entry_outcome_kept_when_technical_unmeasured():
    closes = [100, 101, 102, 103, 104, 140]    # +35% move: high_risk_avoid
    df = pd.DataFrame({"Close": closes})
    ok = qr.research_ticker("AAA", _qr_data(df=df, current_price=140, t_available=True))
    assert ok["entry"]["verdict"] == "high_risk_avoid"
    bad = qr.research_ticker("AAA", _qr_data(df=df, current_price=140, t_available=False, t_bars=6))
    assert bad["entry"]["verdict"] == "high_risk_avoid"


def test_quick_research_legacy_entry_timing_unchanged():
    assert qr.research_ticker("AAA", _qr_data())["entry"]["verdict"] == "buy_now"


# ── held add-to-winner / undersized-add lanes (via port_df Score Available) ──

def _two_holdings(t_flag):
    holdings = [
        {"Ticker": "AAA", "Shares": 1, "Avg Cost ($)": 50.0},
        {"Ticker": "BBB", "Shares": 100, "Avg Cost ($)": 50.0},
    ]
    strong = _loaded_row(price=100.0, stop=70.0, total=85,
                         rec={"icon": "G", "label": "Strong Buy"})
    if t_flag is not None:
        strong["t_available"] = t_flag
    other = _loaded_row(price=100.0, stop=70.0, total=50, sector="Healthcare",
                        rec={"icon": "H", "label": "Hold"})
    return build_portfolio_df(holdings, {"AAA": strong, "BBB": other}, held_stops=hs_for({"AAA": strong, "BBB": other}))


def _grow_adds(df):
    """Grow Today's add-to-winner lane on a bull day (the only add lane since
    the scanner-only buy-candidates lane was retired 2026-10-15)."""
    return _grow_today(df, None, [], {}, _TODAY, 100_000.0,
                       {"tone": "bull"})["add_positions"]


def test_held_add_lanes_fire_only_when_technical_measured():
    ok = _two_holdings(True)
    items = _grow_adds(ok)
    assert find_item(items, "AAA") is not None                           # fires first
    assert any(a["type"] == "add" and a["ticker"] == "AAA" for a in rebalance_actions(ok))

    bad = _two_holdings(False)
    assert find_item(_grow_adds(bad), "AAA") is None
    assert not any(a["type"] == "add" and a["ticker"] == "AAA" for a in rebalance_actions(bad))


def test_held_add_lanes_legacy_same_as_true():
    legacy = _two_holdings(None)
    assert find_item(_grow_adds(legacy), "AAA") is not None


# ── review round 3 ──────────────────────────────────────────────────────────

def _oversold_df():
    from stock_analyzer.constants import QUICK_RESEARCH_RSI_OVERSOLD
    return pd.DataFrame({"Close": [100.0, 100.0, 100.0],
                         "RSI": [QUICK_RESEARCH_RSI_OVERSOLD] * 3})


def test_quick_research_oversold_replaced_when_technical_unmeasured():
    ok = qr.research_ticker("AAA", _qr_data(df=_oversold_df(), t_available=True))
    assert ok["entry"]["verdict"] == "oversold"                        # fires first
    bad = qr.research_ticker("AAA", _qr_data(df=_oversold_df(), t_available=False, t_bars=3))
    assert bad["entry"]["verdict"] == "not_assessed"


def test_quick_research_moves_note_true_per_cause():
    short = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=30))["bullets"][0]
    assert "cover only the history available" in short
    flat = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=_LONG))["bullets"][0]
    assert "cover only the history" not in flat and "too short" not in flat
    assert "verify the quote is actually trading" in flat
    both = qr.research_ticker("AAA", _qr_data(
        t_available=False, t_bars=_LONG, fundamentals_available=False))["bullets"][0]
    assert "verify the quote is actually trading" in both and "too short" not in both


def test_quick_research_bullet3_has_no_doubled_entry_timing_phrase():
    bad = qr.research_ticker("AAA", _qr_data(t_available=False, t_bars=3))
    assert bad["bullets"][2].count("Entry Timing") == 1
    ok = qr.research_ticker("AAA", _qr_data(t_available=True))
    assert ok["bullets"][2].startswith("**Entry Timing: ")


# ── withheld_heading / withheld_lead: name exactly the missing pillar(s) ────

def _pa():
    from stock_analyzer import pillar_availability as pa
    return pa


def test_withheld_heading_per_case():
    pa = _pa()
    assert pa.withheld_heading({"fundamentals_available": False}) == "fundamentals unavailable"
    assert pa.withheld_heading(
        {"fundamentals_available": False, "val_available": False}) == "fundamentals unavailable"
    assert pa.withheld_heading({"val_available": False}) == "valuation unavailable"
    assert pa.withheld_heading(
        {"val_available": False, "t_available": False}) == "valuation and technical trend unavailable"
    assert pa.withheld_heading({"t_available": False}) == "technical trend not measured"
    assert pa.withheld_heading(
        {"fundamentals_available": False, "t_available": False}) == "fundamentals and technical trend unavailable"
    # ETF whose expense ratio is missing
    assert pa.withheld_heading(
        {"asset_type": "etf", "etf_available": False}) == "expense ratio unavailable"
    assert pa.withheld_heading(
        {"asset_type": "etf", "etf_available": False, "t_available": False}
    ) == "expense ratio and technical trend unavailable"
    # no usable label -> today's wording
    assert pa.withheld_heading(None) == "fundamentals unavailable"
    assert pa.withheld_heading({}) == "fundamentals unavailable"


def test_withheld_lead_valuation_only_does_not_claim_fundamentals_unfetched():
    pa = _pa()
    lead = pa.withheld_lead({"val_available": False}, "ACCV")
    assert "No objective valuation metric (Forward P/E or FCF Yield)" in lead
    assert "ACCV" in lead
    assert "fundamental data" not in lead
    # still true with Technical also missing (tech is appended by the caller)
    assert "fundamental data" not in pa.withheld_lead(
        {"val_available": False, "t_available": False}, "ACCV")


def test_withheld_lead_business_quality_missing_keeps_fetch_failure_text():
    pa = _pa()
    for b in ({"fundamentals_available": False},
              {"fundamentals_available": False, "val_available": False},
              {"fundamentals_available": False, "t_available": False}):
        lead = pa.withheld_lead(b, "XYZ")
        assert "couldn't get XYZ's fundamental data from any source" in lead
        assert "Business Quality leg" in lead
