"""S-D C3 -- `budget_view.risk_footer`: the Grow Today risk footer totalled from
the FINAL (post-cap, post-budget) sizes. Pure function; app.py only renders it."""
import datetime
import re

import pandas as pd
import pytest

from stock_analyzer import budget_view as bv

pytestmark = pytest.mark.fast

TODAY = datetime.date(2026, 10, 16)
NOW = datetime.datetime(2026, 10, 16, 13, 50, tzinfo=datetime.timezone.utc)
INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
          "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
NO_TRADES = pd.DataFrame(columns=["traded_at", "action", "ticker", "shares", "price"])


def _sized(shares, total_cost, stop, **extra):
    d = {"shares": shares, "total_cost": total_cost, "stop": stop}
    d.update(extra)
    return d


def _item(tk, sz):
    return {"ticker": tk, "sizing": sz}


def _grow(picks=(), adds=(), **extra):
    g = {"new_picks": list(picks), "add_positions": list(adds)}
    g.update(extra)
    return g


# 1. partial -----------------------------------------------------------------
def test_partial_four_setups_two_sized():
    grow = _grow(
        picks=[_item("AAA", _sized(10, 1000.0, 94.0)),       # risk 1000-940 = 60
               _item("BBB", _sized(5, 500.0, 90.0)),         # risk 500-450 = 50
               _item("CCC", {"ceiling_infeasible": True})],
        adds=[_item("DDD", {"budget_exhausted": True})])
    r = bv.risk_footer(grow)
    assert r["state"] == "partial" and r["n_sized"] == 2 and r["n_setups"] == 4
    assert r["risk_dollars"] == pytest.approx(110.0)
    assert "2 of 4 sized" in r["text"]
    assert "~$110 in total" in r["text"]
    assert "CCC (one share exceeds the single-name cap)" in r["text"]
    assert "DDD (today's new-position budget is used up)" in r["text"]
    assert [u["ticker"] for u in r["unsized"]] == ["CCC", "DDD"]


# 2. all sized -----------------------------------------------------------------
def test_all_sized():
    r = bv.risk_footer(_grow(picks=[_item("AAA", _sized(10, 1000.0, 94.0))]))
    assert r["state"] == "sized" and r["risk_dollars"] == pytest.approx(60.0)
    assert "No size:" not in r["text"]
    assert r["text"] == ("If every stop hits, the 1 sized setup above loses ~$60 "
                         "in total. That's the risk, not the cash to deploy.")


def test_plural_wording_two_sized():
    r = bv.risk_footer(_grow(picks=[_item("A", _sized(10, 1000.0, 94.0)),
                                    _item("B", _sized(10, 1000.0, 94.0))]))
    assert r["text"].startswith("If every stop hits, the 2 sized setups above lose ~$120")


# 3. none sized ------------------------------------------------------------------
def test_none_sized():
    r = bv.risk_footer(_grow(picks=[_item("AAA", {}), _item("BBB", None)]))
    assert r["state"] == "none_sized" and r["risk_dollars"] is None
    assert "$" not in r["text"]
    assert r["text"].startswith("None of the 2 setups above has a size today")
    assert "AAA (no size), BBB (no size)" in r["text"]


def test_none_sized_single_setup_wording():
    r = bv.risk_footer(_grow(picks=[_item("AAA", {})]))
    assert r["text"] == ("The 1 setup above has no size today, so there's no "
                         "risk total. No size: AAA (no size).")


# 4. act-risk -----------------------------------------------------------------------
@pytest.mark.parametrize("banner,prefixed", [(["Stop breach"], True), ([], False),
                                             (None, False)])
def test_act_risk_prefix(banner, prefixed):
    r = bv.risk_footer(_grow(picks=[_item("A", _sized(10, 1000.0, 94.0))],
                             risk_banner=banner))
    assert r["act_risk"] is prefixed
    assert r["text"].startswith("⚠️ Resolve Act Today risk alerts before acting.") is prefixed


# 5. partial budget uses SCALED shares ----------------------------------------------
def _full_sizing(shares, price=100.0):
    return {"shares": shares, "total_cost": round(shares * price, 2),
            "stop": 94.0, "stop_pct": 6.0, "port_pct": 17.1, "risk_budget": 100.0,
            "entry_lo": 99.0, "entry_hi": 101.0, "ceiling_capped": False,
            "uncapped_shares": shares, "portfolio_value": 14000.0,
            "sizing_version": 3, "capital_capped": False, "capital_pct": float(shares),
            "actual_risk": 144.0}


def test_partial_budget_uses_post_budget_shares():
    def pick(tk, comp):
        return {"ticker": tk, "composite_score": comp, "score": 70.0, "price": 100.0,
                "sector": "Tech", "thesis": "t", "sizing": _full_sizing(24)}
    brief = {"act_today": [], "review_list": [],
             "grow_today": {"tone": "bull", "new_picks": [pick("AAA", 80), pick("BBB", 70)],
                            "add_positions": [], "overflow_picks": []}}
    pre = bv.risk_footer(brief["grow_today"])
    out = bv.postpass(brief, INPUTS, NO_TRADES, {}, TODAY, NOW)
    grow = out["brief"]["grow_today"]
    trimmed = grow["new_picks"][1]["sizing"]
    assert trimmed.get("budget_capped") and trimmed["shares"] == 16
    post = bv.risk_footer(grow)
    # AAA 24 sh: 2400 - 24*94 = 144 ; BBB trimmed to 16 sh: 1600 - 16*94 = 96
    assert pre["risk_dollars"] == pytest.approx(288.0)
    assert post["risk_dollars"] == pytest.approx(240.0)
    assert post["risk_dollars"] != pre["risk_dollars"]
    assert post["state"] == "sized"


# 6. unknown ---------------------------------------------------------------------------
def test_sized_item_missing_stop_is_unknown():
    r = bv.risk_footer(_grow(picks=[_item("AAA", _sized(10, 1000.0, 94.0)),
                                    _item("BBB", {"shares": 5, "total_cost": 500.0})]))
    assert r["state"] == "unknown" and r["risk_dollars"] is None
    assert "$" not in r["text"]
    assert r["text"] == "Couldn't total the risk: BBB has a size but no usable stop."


def test_negative_risk_is_unknown():
    r = bv.risk_footer(_grow(picks=[_item("AAA", _sized(10, 1000.0, 120.0))]))
    assert r["state"] == "unknown" and r["risk_dollars"] is None
    assert r["text"] == "Couldn't total the risk: AAA's price is already below its stop."


def test_first_offending_item_decides_unknown_text():
    r = bv.risk_footer(_grow(picks=[_item("AAA", _sized(10, 1000.0, 120.0)),
                                    _item("BBB", {"shares": 5, "total_cost": 500.0})]))
    assert "AAA's price is already below its stop" in r["text"]
    r = bv.risk_footer(_grow(picks=[_item("BBB", {"shares": 5, "total_cost": 500.0}),
                                    _item("AAA", _sized(10, 1000.0, 120.0))]))
    assert "BBB has a size but no usable stop" in r["text"]


# called / capital-infeasible reasons ------------------------------------------------------
_CALLED = "net capital is at or below zero"


def _why(sz, called=False):
    return bv.risk_footer(_grow(picks=[_item("Z", sz)]), called=called)["unsized"][0]["why"]


def test_called_budget_exhausted_uses_net_capital_phrase():
    assert _why({"budget_exhausted": True}, called=True) == _CALLED
    assert _why({"budget_exhausted": True}, called=False) == "today's new-position budget is used up"


def test_capital_infeasible_phrase_depends_on_one_share_pct():
    assert _why({"capital_infeasible": True, "one_share_capital_pct": None}) == _CALLED
    assert _why({"capital_infeasible": True}) == _CALLED
    assert (_why({"capital_infeasible": True, "one_share_capital_pct": 31.2})
            == "one share exceeds the net-capital cap")


# 7. mask ---------------------------------------------------------------------------------
def test_mask_hides_dollars():
    r = bv.risk_footer(_grow(picks=[_item("A", _sized(10, 1000.0, 94.0))]), mask=True)
    assert not re.search(r"\$\d", r["text"])
    assert "••••••" in r["text"]


# 8. why phrases --------------------------------------------------------------------------
@pytest.mark.parametrize("flag,phrase", list(bv._UNSIZED_WHY))
def test_every_flag_maps_to_its_phrase(flag, phrase):
    # one_share_capital_pct set so capital_infeasible keeps its generic phrase
    r = bv.risk_footer(_grow(picks=[_item("ZZZ", {flag: True, "one_share_capital_pct": 31.2})]))
    assert r["unsized"] == [{"ticker": "ZZZ", "why": phrase}]
    assert f"ZZZ ({phrase})" in r["text"]


def test_first_matching_flag_wins_and_unrecognised_is_no_size():
    first, second = bv._UNSIZED_WHY[0], bv._UNSIZED_WHY[-1]
    r = bv.risk_footer(_grow(picks=[_item("A", {first[0]: True, second[0]: True})]))
    assert r["unsized"][0]["why"] == first[1]
    for sz in ({}, None, {"foo": True}, "x", {"shares": 0}, {"shares": float("nan")}):
        r = bv.risk_footer(_grow(picks=[_item("A", sz)]))
        assert r["unsized"][0]["why"] == "no size"


def test_empty_ticker_is_question_mark():
    r = bv.risk_footer(_grow(picks=[{"ticker": "  ", "sizing": {}}]))
    assert r["unsized"][0]["ticker"] == "?"


# 9. none inputs ---------------------------------------------------------------------------
@pytest.mark.parametrize("grow", [None, [], "x", {}, {"new_picks": [], "add_positions": []},
                                  {"new_picks": [1, None], "add_positions": "no"}])
def test_none_or_no_setups(grow):
    assert bv.risk_footer(grow) is None


def test_non_list_new_picks_does_not_raise():
    r = bv.risk_footer({"new_picks": "oops",
                        "add_positions": [_item("A", _sized(10, 1000.0, 94.0))]})
    assert r["state"] == "sized" and r["n_setups"] == 1


# 10. crash path ----------------------------------------------------------------------------
def test_crash_returns_unknown_dict(monkeypatch):
    def boom(*_a):
        raise RuntimeError("x")
    monkeypatch.setattr(bv, "_unsized_why", boom)
    r = bv.risk_footer(_grow(picks=[_item("A", {})]))
    assert r == {"state": "unknown", "n_setups": 0, "n_sized": 0, "risk_dollars": None,
                 "unsized": [], "act_risk": False,
                 "text": "Couldn't total the risk for this list."}


# 11. mutation sensitivity: never read actual_risk (Grow Today sizing dicts carry none,
# and apply_to_sizing wouldn't rescale it on a partial trim) -------------------------------------------
def test_risk_ignores_actual_risk():
    r = bv.risk_footer(_grow(picks=[
        _item("A", _sized(10, 1000.0, 94.0, actual_risk=9999.0)),
        _item("B", _sized(10, 1000.0, 94.0, actual_risk=1.0))]))
    assert r["risk_dollars"] == pytest.approx(120.0)
