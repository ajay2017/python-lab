"""SlidingStop C3 commit 2 (display only): the "Stop Carried" portfolio columns, the
explainer's "Carried Stop" row and `driver_label` (docs/plans/sliding-stop-c3-spec.md
section 7). The decision path is in test_held_stop_carry_wiring.py (which this reuses
for its builders)."""
from __future__ import annotations

import pandas as pd

from stock_analyzer import held_stop_carry as hc
from stock_analyzer import held_stop_view as hv
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import portfolio as pf
from tests.test_held_stop_carry_wiring import (  # noqa: F401  (early_trail: the autouse fixture)
    AVG, HOLD, T, build, early_trail, falling, make_row, mrow, s, _entry, _holdings)


def _port(auto, price=140.0, manual=None, hold=None):
    loaded = {T: {"current_price": price, "stop": 1.0, "sector": "Technology",
                  "rec": {"icon": "G", "label": "Buy"}, "total": 70}}
    ms = {T: {"stop_price": manual}} if manual is not None else None
    return pf.build_portfolio_df(HOLD if hold is None else hold, loaded, manual_stops=ms,
                                 held_stops={T: auto})


def test_stop_carried_columns_only_when_the_carry_bound():
    days, _, fall = falling()
    raw = build(fall, days[60], {})
    row = mrow(fall, days, 60, round(raw["stop"] + 5.0, 2))
    eff = build(fall, days[60], {T: row})
    r = _port(eff).iloc[0]
    assert eff["carry"]["state"] == hc.APPLIED
    assert r["Stop Carried"] == eff["stop"] == r["Auto Stop"] == row["stop"]
    assert r["Stop Carried On"] == eff["carry"]["set_on"].isoformat()
    assert r["Stop Type"] == hw.LABEL_CARRIED and r["Stop Type Auto"] == hw.LABEL_CARRIED
    assert eff["carry"]["note"] in r["Stop Note"] and r["Stop Note"].startswith(eff["note"])
    unbound = [
        raw,                                                                    # none
        build(fall, days[60], None),                                            # unreadable
        build(fall, days[60], {T: mrow(fall, days, 60, round(raw["stop"] - 5.0, 2))}),   # not binding
        build(fall, days[60], {T: mrow(fall, days, 60, round(raw["stop"] + 5.0, 2),
                                       avg_cost=AVG + 1.0)}),                   # lapsed
        None,
    ]
    for h in unbound:
        r2 = _port(h).iloc[0]
        assert pd.isna(r2["Stop Carried"]) and pd.isna(r2["Stop Carried On"])
    # a manual stop composes after the carry and still wins when higher
    above = round(eff["stop"] + 1.0, 2)
    r3 = _port(eff, manual=above).iloc[0]
    assert r3["Stop"] == above and r3["Stop Type"] == "Manual" and r3["Stop Carried"] == eff["stop"]


def test_the_explainer_has_a_carried_row_only_when_it_binds():
    days, _, fall = falling()
    raw = build(fall, days[60], {})
    row = mrow(fall, days, 60, round(raw["stop"] + 5.0, 2))
    eff = build(fall, days[60], {T: row})
    on = eff["carry"]["set_on"]
    for h in (eff, hv.from_row(_port(eff).iloc[0])):                 # a result, and a portfolio row
        rows = [x for x in hv.component_rows(h) if x["kind"] == "carried"]
        assert len(rows) == 1 and rows[0]["label"] == "Carried Stop"
        assert rows[0]["value"] == round(row["stop"], 2)
        assert rows[0]["text"] == (f"The stop carried from {on.isoformat()}. It is held here because "
                                   "your trades in this position haven't changed since then and "
                                   "today's figure came out lower.")
    anon = dict(eff, carry=dict(eff["carry"], set_on=None))
    assert [x for x in hv.component_rows(anon) if x["kind"] == "carried"][0]["text"].startswith(
        "The stop carried. It is held here")
    for h in (raw, build(fall, days[60], None)):
        assert not [x for x in hv.component_rows(h) if x["kind"] == "carried"]
        assert not [x for x in hv.component_rows(hv.from_row(_port(h).iloc[0])) if x["kind"] == "carried"]


def test_driver_label_names_a_carried_stop_that_binds_above_every_rebuilt_part(s):
    def _down(t, p):
        raise RuntimeError("unadjusted history unavailable")
    i = 120
    bundle = {T: {"df": s.df.iloc[max(0, i - 80):i]}}

    def _b(carry):
        return hw.build_held_stops(_holdings(s), bundle, _entry(s), s.days[i], fetch_history=_down,
                                   floors={}, carry=carry)[T]
    raw = _b({})
    assert raw["basis"] == "partial"
    row = make_row(s.df, s.days, i, stop=round(raw["stop"] + 2.0, 2), avg_cost=s.avg,
                   journal_buy_count=1, journal_buy_max_id=1)
    eff = _b({T: row})
    assert eff["carry"]["state"] == hc.APPLIED and eff["stop"] == row["stop"]
    h = hv.from_row(_port(eff, hold=_holdings(s)).iloc[0])
    assert h["carried"] == row["stop"]
    low = hv.what_if(h, 100.0, s.avg)               # the rebuilt parts stay below the carried stop
    assert low["stop"] == eff["stop"] and low["saved"] == eff["stop"] and not low["raised"]
    assert low["saved_label"] == "Carried Stop" and hv.driver_label(low) == "Carried Stop"
    high = hv.what_if(h, 160.0, s.avg)               # a rally lifts them above it
    assert high["stop"] > eff["stop"] and hv.driver_label(high) != "Carried Stop"
    # nothing carried: the old keys are unchanged
    plain = hv.what_if(hv.from_row(_port(raw, hold=_holdings(s)).iloc[0]), 100.0, s.avg)
    assert plain["saved"] is None and plain["saved_label"] is None
