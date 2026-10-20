"""S-D chunk C1a (docs/plans/one-sizing-path.md): the caps bind on the WHOLE
position after an add (owner decision D1, 2026-10-19), plus the pure
`sizing_path` module, the Grow Today add-loop wiring and G-31.

The MRVL regression comes first: it is the live 2026-10-19 case the whole
change exists for.
"""
import itertools
import math
from datetime import date
from unittest import mock

import pandas as pd
import pytest

from stock_analyzer import concentration, daily_briefing, daily_budget, gate_ledger, risk
from stock_analyzer import sizing_path as sp
from stock_analyzer.constants import (
    ADD_WINNER_MIN_GAP_PCT,
    COMPOSITE_BUY,
    NET_CAPITAL_POSITION_CAP_PCT,
    RISK_PCT_PER_TRADE,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    SINGLE_NAME_CEILING,
)
from stock_analyzer.daily_briefing import _grow_today, _position_size_for_render
from tests.conftest import make_port_df

pytestmark = pytest.mark.fast

# MRVL, 2026-10-19
ENTRY, STOP, EXISTING, GROSS, NET = 238.86, 215.00, 1672.02, 19000.0, 7300.0


def _ps(pv, entry, stop, net=None, existing=0.0, ceil=SINGLE_NAME_CEILING,
        cap=NET_CAPITAL_POSITION_CAP_PCT):
    return risk.position_sizing(pv, RISK_PCT_PER_TRADE, entry, stop, ceil,
                                net_capital=net, max_capital_pct=cap,
                                existing_value=existing)


# ── 1. MRVL regression ────────────────────────────────────────────────────────

def test_mrvl_today_arithmetic_with_no_existing_is_7_shares():
    assert _ps(GROSS, ENTRY, STOP, NET)["shares"] == 7


def test_mrvl_whole_position_cap_leaves_no_room():
    r = sp.size_position(entry=ENTRY, stop=STOP, gross_book=GROSS, net_capital=NET,
                         existing_value=EXISTING)
    assert r["state"] == "no_size" and r["code"] == "room_capital"
    assert "22.9%" in r["text"] and "26.2%" in r["text"]
    assert r["existing_capital_pct"] == 22.9 and r["existing_gross_pct"] == 8.8
    assert "$" not in r["text"]


def test_mrvl_gross_room_alone_is_4_shares_so_net_capital_is_what_binds():
    assert _ps(GROSS, ENTRY, STOP, None, EXISTING)["shares"] == 4


def test_mrvl_pair_smaller_holding_is_sized_and_room_capped_on_capital():
    r = sp.size_position(entry=ENTRY, stop=STOP, gross_book=GROSS, net_capital=NET,
                         existing_value=1400.0)
    assert r["state"] == "sized"
    assert r["ps"]["shares"] >= 1 and r["ps"]["room_capped"] is True
    assert r["ps"]["room_binding"] == "capital" and r["binding"] == "capital"
    assert "Capped by what you already hold" in r["text"] and "of net capital" in r["text"]
    assert "$" not in r["text"]


# ── 2. byte-identity at existing_value == 0 ───────────────────────────────────

def _head_position_sizing(pv, risk_pct, entry, stop, max_pos, net, max_cap):
    """A local copy of position_sizing as it stood before S-D (frozen HEAD)."""
    def _n(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        return v if v == v else 0.0
    p, e, s = _n(pv), _n(entry), _n(stop)
    if e <= 0 or s <= 0 or e <= s or p <= 0:
        return None
    if max_pos is not None and int((p * (_n(max_pos) / 100.0)) / e) < 1:
        return None
    if net is not None and net <= 0:
        return None
    if net is not None and net > 0 and max_cap is not None and int((net * _n(max_cap) / 100.0) / e) < 1:
        return None
    rd = pv * risk_pct
    rps = entry - stop
    rb = max(1, int(rd / rps))
    shares, cc = rb, False
    if max_pos is not None and pv > 0 and entry > 0:
        cs = int((pv * (max_pos / 100.0)) / entry)
        if shares > cs:
            shares, cc = cs, True
    kc = False
    if net is not None and net > 0 and max_cap is not None and entry > 0:
        ks = int((net * max_cap / 100.0) / entry)
        if shares > ks:
            shares, kc = ks, True
    tc = round(shares * entry, 2)
    ar = round(shares * rps, 2)
    out = {"shares": shares, "risk_budget": round(rd, 2), "actual_risk": ar,
           "risk_per_share": round(rps, 2), "total_cost": tc,
           "portfolio_pct": round(tc / pv * 100, 1) if pv else 0.0,
           "risk_pct_actual": round(ar / pv * 100, 2) if pv else 0.0}
    if max_pos is not None:
        out["ceiling_pct"] = max_pos
        out["ceiling_capped"] = cc
        out["uncapped_shares"] = rb
        out["uncapped_pct"] = round(rb * entry / pv * 100, 1) if pv else 0.0
    if net is not None:
        out["capital_capped"] = kc
        out["capital_pct"] = round(tc / net * 100.0, 1)
    return out


_GRID = list(itertools.product(
    [5_000.0, 19_000.0, 100_000.0],          # pv
    [12.0, 100.0, 238.86, 900.0],            # entry
    [0.88, 0.93],                            # stop as a fraction of entry
    [None, 3_000.0, 7_300.0, 40_000.0],      # net_capital
    [None, 10.0, 15.0],                      # ceiling
))


def test_grid_is_big_enough():
    assert len(_GRID) >= 50


def test_position_sizing_existing_zero_equals_head_over_grid_and_adds_no_keys():
    for pv, entry, frac, net, ceil in _GRID:
        stop = round(entry * frac, 2)
        want = _head_position_sizing(pv, RISK_PCT_PER_TRADE, entry, stop, ceil, net,
                                     NET_CAPITAL_POSITION_CAP_PCT)
        got = _ps(pv, entry, stop, net, 0.0, ceil)
        no_kwarg = risk.position_sizing(pv, RISK_PCT_PER_TRADE, entry, stop, ceil,
                                        net_capital=net,
                                        max_capital_pct=NET_CAPITAL_POSITION_CAP_PCT)
        assert got == want == no_kwarg, (pv, entry, stop, net, ceil)
        if got is not None:
            for k in ("existing_value", "post_portfolio_pct", "post_capital_pct",
                      "room_capped", "room_binding"):
                assert k not in got


def test_render_adapter_existing_zero_has_no_new_keys_and_equals_no_kwarg():
    for pv, entry, frac, net, _ in _GRID:
        stop = round(entry * frac, 2)
        a = _position_size_for_render(pv, entry, stop, 1.0, 2.0, net_capital=net)
        b = _position_size_for_render(pv, entry, stop, 1.0, 2.0, net_capital=net,
                                      existing_value=0.0)
        assert a == b
        for k in ("room_capped", "room_binding", "post_portfolio_pct", "post_capital_pct",
                  "existing_value", "room_infeasible", "holding_unknown"):
            assert k not in b


# ── 3. room boundaries ────────────────────────────────────────────────────────

def test_exactly_one_share_of_room_sizes_one_share():
    # cap_dollars 1750 (net 7000 * 25%); existing 1649 -> room 101 -> 1 share @100
    ps = _ps(1_000_000.0, 100.0, 90.0, 7_000.0, 1649.0)
    assert ps is not None and ps["shares"] == 1 and ps["room_capped"] is True


def test_room_of_0_999_share_is_no_size():
    # existing 1650.1 -> room 99.9 -> 0.999 share -> none
    assert risk.sizing_unavailable_reason(1_000_000.0, 100.0, 90.0, SINGLE_NAME_CEILING,
                                          7_000.0, NET_CAPITAL_POSITION_CAP_PCT,
                                          1650.1) == "room_capital"
    assert _ps(1_000_000.0, 100.0, 90.0, 7_000.0, 1650.1) is None


def test_existing_exactly_at_the_cap_is_no_size():
    assert risk.sizing_unavailable_reason(1_000_000.0, 100.0, 90.0, SINGLE_NAME_CEILING,
                                          7_000.0, NET_CAPITAL_POSITION_CAP_PCT,
                                          1750.0) == "room_capital"


def test_existing_above_the_cap_alb_oxy_shape_no_size_never_negative():
    # ALB/OXY: ~45% of net capital held. Gross ceiling room (15% of 24,500 =
    # 3,675) is checked first, so at 3,555 it is the ceiling that reports; at 40%
    # of net capital (3,160) gross still has room and the capital cap reports.
    r = sp.size_position(entry=143.25, stop=130.0, gross_book=24_500.0, net_capital=7_900.0,
                         existing_value=0.45 * 7_900.0)
    assert r["state"] == "no_size" and r["code"] in ("room_ceiling", "room_capital")
    assert _ps(24_500.0, 143.25, 130.0, 7_900.0, 0.45 * 7_900.0) is None
    r = sp.size_position(entry=143.25, stop=130.0, gross_book=24_500.0, net_capital=7_900.0,
                         existing_value=0.40 * 7_900.0)
    assert r["state"] == "no_size" and r["code"] == "room_capital"
    assert _ps(24_500.0, 143.25, 130.0, 7_900.0, 0.40 * 7_900.0) is None


def test_gross_ceiling_room_reason_when_unlevered_and_above_ceiling():
    assert risk.sizing_unavailable_reason(10_000.0, 100.0, 90.0, SINGLE_NAME_CEILING,
                                          None, None, 1_450.0) == "room_ceiling"
    r = sp.size_position(entry=100.0, stop=90.0, gross_book=10_000.0, net_capital=None,
                         existing_value=2_000.0)
    assert r["code"] == "room_ceiling" and "of your book" in r["text"] and "15%" in r["text"]


def test_ceiling_binds_when_net_capital_none():
    ps = _ps(10_000.0, 10.0, 9.0, None, 500.0)     # risk-based 150, ceiling room 100
    assert ps["shares"] == 100 and ps["ceiling_capped"] is True
    assert ps["room_capped"] is True and ps["room_binding"] == "ceiling"
    assert ps["post_portfolio_pct"] == 15.0


def test_strict_below_equality_case_steps_back_one_share():
    # cap 1750; existing 1000 + 7.5 * 100 would hit it only fractionally; use an exact hit:
    # entry 125, existing 500 -> (1750-500)/125 = 10 exactly -> landing ON the cap -> 9
    ps = _ps(1_000_000.0, 125.0, 100.0, 7_000.0, 500.0)
    assert ps["shares"] == 9
    assert 500.0 + 9 * 125.0 < 1750.0
    # and the Trade Journal's own >= rule agrees
    a = concentration.assess_add_concentration(
        ticker="X", add_shares=ps["shares"], price=125.0, existing_name_mv=500.0,
        sector_mv=0.0, portfolio_value=1_000_000.0, single_ceiling=SINGLE_NAME_CEILING,
        sector_ceiling=SECTOR_CEILING, sector_elevated=SECTOR_ELEVATED,
        net_capital=7_000.0, capital_ceiling=NET_CAPITAL_POSITION_CAP_PCT)
    assert a is None or not a["capital_breach"]


def test_new_pick_equality_case_is_unchanged_by_the_strict_rule():
    # existing == 0 keeps the pre-existing (non-strict) behaviour byte for byte
    ps = _ps(1_000_000.0, 125.0, 100.0, 7_000.0, 0.0)
    assert ps["shares"] == 14 and ps["total_cost"] == 1750.0


def test_output_keys_when_existing_positive():
    ps = _ps(GROSS, ENTRY, STOP, NET, 1400.0)
    assert ps["existing_value"] == 1400.0
    assert ps["post_portfolio_pct"] == round((1400.0 + ps["total_cost"]) / GROSS * 100, 1)
    assert ps["post_capital_pct"] == round((1400.0 + ps["total_cost"]) / NET * 100, 1)
    noc = _ps(GROSS, ENTRY, STOP, None, 1400.0)
    assert "post_capital_pct" not in noc and "post_portfolio_pct" in noc


# ── 4. sentinels ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -1.0, -0.01, "5", True])
def test_bad_existing_value_is_holding_never_coerced_to_zero(bad):
    assert risk.sizing_unavailable_reason(GROSS, ENTRY, STOP, 15.0, NET, 25.0, bad) == "holding"
    assert risk.position_sizing(GROSS, 0.015, ENTRY, STOP, 15.0, NET, 25.0, bad) is None
    r = sp.size_position(entry=ENTRY, stop=STOP, gross_book=GROSS, net_capital=NET,
                         existing_value=bad)
    assert r["state"] == "no_size" and r["code"] == "holding_unknown"
    assert "couldn't be read" in r["text"]


def test_reason_order_stop_portfolio_holding_then_caps():
    assert risk.sizing_unavailable_reason(GROSS, 100.0, 100.0, 15.0, NET, 25.0, None) == "stop"
    assert risk.sizing_unavailable_reason(0.0, 100.0, 90.0, 15.0, NET, 25.0, None) == "portfolio"
    assert risk.sizing_unavailable_reason(1_000.0, 900.0, 800.0, 15.0, NET, 25.0, None) == "holding"


def test_net_capital_at_or_below_zero_is_still_capital():
    for existing in (0.0, 500.0):
        assert risk.sizing_unavailable_reason(GROSS, ENTRY, STOP, 15.0, 0.0, 25.0, existing) == "capital"
        assert risk.sizing_unavailable_reason(GROSS, ENTRY, STOP, 15.0, -5.0, 25.0, existing) == "capital"


def test_size_position_never_raises():
    with mock.patch.object(risk, "position_sizing", side_effect=RuntimeError("boom")):
        r = sp.size_position(entry=ENTRY, stop=STOP, gross_book=GROSS, net_capital=NET,
                             existing_value=0.0)
    assert r["state"] == "no_size" and r["code"] == "error"
    assert r["text"] == "Sizing couldn't run — no size is shown."
    with mock.patch.object(risk, "sizing_unavailable_reason", side_effect=RuntimeError("boom")):
        r = sp.size_position(entry=ENTRY, stop=STOP, gross_book=GROSS, net_capital=NET,
                             existing_value=0.0)
    assert r["code"] == "error"


@pytest.mark.parametrize("kwargs,code", [
    (dict(entry=100.0, stop=100.0, gross_book=GROSS, net_capital=NET), "stop"),
    (dict(entry=100.0, stop=90.0, gross_book=0.0, net_capital=NET), "portfolio"),
    (dict(entry=5_000.0, stop=4_000.0, gross_book=10_000.0, net_capital=None), "ceiling"),
    (dict(entry=2_000.0, stop=1_900.0, gross_book=1_000_000.0, net_capital=1_000.0), "capital"),
])
def test_every_no_size_code_has_a_plain_text(kwargs, code):
    r = sp.size_position(existing_value=0.0, **kwargs)
    assert r["state"] == "no_size" and r["code"] == code and r["text"]
    assert "$" not in r["text"] and "**" not in r["text"]


def test_sized_with_no_cap_has_empty_text_and_risk_binding():
    r = sp.size_position(entry=100.0, stop=90.0, gross_book=100_000.0, net_capital=None,
                         existing_value=0.0)
    assert r["state"] == "sized" and r["binding"] == "risk" and r["text"] == ""


# ── 5. Trade Journal coherence ────────────────────────────────────────────────

def test_a_suggested_size_never_trips_the_trade_journal_breach_flags():
    n_sized = 0
    for pv, entry, frac, net, _ in _GRID:
        stop = round(entry * frac, 2)
        for ex_frac in (0.0, 0.02, 0.05, 0.09, 0.14, 0.2):
            existing = round(ex_frac * (net or pv), 2)
            r = sp.size_position(entry=entry, stop=stop, gross_book=pv, net_capital=net,
                                 existing_value=existing)
            if r["state"] != "sized":
                continue
            n_sized += 1
            a = concentration.assess_add_concentration(
                ticker="X", add_shares=r["ps"]["shares"], price=entry,
                existing_name_mv=existing, sector_mv=0.0, portfolio_value=pv,
                single_ceiling=SINGLE_NAME_CEILING, sector_ceiling=SECTOR_CEILING,
                sector_elevated=SECTOR_ELEVATED, net_capital=net,
                capital_ceiling=NET_CAPITAL_POSITION_CAP_PCT if net else None)
            if existing > 0 and a is not None:
                assert not a["name_breach"], (pv, entry, net, existing)
                assert not a["capital_breach"], (pv, entry, net, existing)
    assert n_sized > 50


# ── 6. held_value ─────────────────────────────────────────────────────────────

def _pdf(rows):
    return pd.DataFrame(rows, columns=["Ticker", "Market Value"])


def test_held_value_match_and_case_whitespace_insensitive():
    df = _pdf([["MRVL", 1672.02], ["AAPL", 100.0]])
    assert sp.held_value("MRVL", df, "ready") == pytest.approx(1672.02)
    assert sp.held_value(" mrvl ", df, "ready") == pytest.approx(1672.02)
    df2 = _pdf([[" mrvl", 10.0], ["MRVL ", 5.0]])
    assert sp.held_value("MRVL", df2, "ready") == 15.0


def test_held_value_no_match_depends_on_state():
    df = _pdf([["AAPL", 100.0]])
    assert sp.held_value("MRVL", df, "ready") == 0.0
    assert sp.held_value("MRVL", df, "flat") == 0.0
    assert sp.held_value("MRVL", df, "unknown") is None
    assert sp.held_value("MRVL", df, None) is None


def test_held_value_missing_frame_or_columns():
    assert sp.held_value("A", None, "flat") == 0.0
    assert sp.held_value("A", None, "ready") is None
    assert sp.held_value("A", pd.DataFrame(), "flat") == 0.0
    assert sp.held_value("A", pd.DataFrame(), "unknown") is None
    assert sp.held_value("A", pd.DataFrame({"Ticker": ["A"]}), "ready") is None


def test_held_value_nan_negative_or_garbage_is_none():
    assert sp.held_value("A", _pdf([["A", float("nan")]]), "ready") is None
    assert sp.held_value("A", _pdf([["A", float("inf")]]), "ready") is None
    assert sp.held_value("A", _pdf([["A", -5.0]]), "ready") is None
    assert sp.held_value("A", object(), "ready") is None


# ── 7. held_add_verdict ───────────────────────────────────────────────────────

_PAUSE_BUCKETS = [
    "card_blocked_adds", "deterioration_blocked_adds", "cooldown_adds",
    "sector_blocked_adds", "risk_blocked_adds", "concentration_blocked_adds",
    "pair_blocked_adds", "cluster_blocked_adds", "entry_held_adds", "room_blocked_adds",
]
_TODAY = date(2026, 10, 19)


def _v(grow, ticker="MRVL", brief_date=_TODAY, today=_TODAY, brief_epoch=3, epoch=3):
    return sp.held_add_verdict(ticker, grow, brief_date=brief_date, today=today,
                               brief_epoch=brief_epoch, epoch=epoch)


def test_pause_bucket_list_matches_the_module():
    assert list(sp._PAUSE_BUCKETS) == _PAUSE_BUCKETS


@pytest.mark.parametrize("bucket", _PAUSE_BUCKETS)
def test_each_pause_bucket_pauses_with_its_gate_id(bucket):
    item = {"ticker": "mrvl", "reason": f"why {bucket}", "gate_id": f"G-{bucket[:3]}",
            "where": "Act Today"}
    grow = {"tone": "bull", bucket: [item]}
    out = _v(grow)
    assert out["state"] == "paused" and out["item"] is item
    assert out["gate_id"] == f"G-{bucket[:3]}"
    if bucket == "card_blocked_adds":
        assert out["reason"] == "Paused by a card in today's Brief (Act Today)"
    else:
        assert out["reason"] == f"why {bucket}"


def test_pause_without_reason_gets_a_generic_sentence_and_other_tickers_ignored():
    out = _v({"tone": "bull", "cooldown_adds": [{"ticker": "MRVL"}]})
    assert out["state"] == "paused" and out["reason"] and out["gate_id"] is None
    other = _v({"tone": "bull", "cooldown_adds": [{"ticker": "AAPL"}]})
    assert other["state"] == "not_called"


def test_deterioration_bucket_pauses_even_on_a_flat_day():
    out = _v({"tone": "flat", "deterioration_blocked_adds": [
        {"ticker": "MRVL", "gate_id": "G-20", "counterfactual": False, "reason": "weak"}]})
    assert out["state"] == "paused" and out["gate_id"] == "G-20"


def test_add_positions_is_an_add_with_the_item():
    item = {"ticker": "MRVL", "sizing": {"shares": 3}}
    out = _v({"tone": "bull", "add_positions": [item]})
    assert out["state"] == "add" and out["item"] is item and out["gate_id"] is None


def test_ticker_in_both_a_pause_bucket_and_add_positions_is_paused():
    out = _v({"tone": "bull", "add_positions": [{"ticker": "MRVL"}],
              "cooldown_adds": [{"ticker": "MRVL", "gate_id": "G-24", "reason": "r"}]})
    assert out["state"] == "paused" and out["gate_id"] == "G-24"


def test_bear_flat_and_bull_absent_are_not_called_with_distinct_wording():
    bear = _v({"tone": "bear"})
    flat = _v({"tone": "flat"})
    bull = _v({"tone": "bull"})
    assert bear["state"] == flat["state"] == bull["state"] == "not_called"
    assert "risk-off" in bear["reason"]
    assert "up day" in flat["reason"]
    assert f"{COMPOSITE_BUY:.0f}" in bull["reason"] and f"{ADD_WINNER_MIN_GAP_PCT:.0f}%" in bull["reason"]
    paused_flat = _v({"tone": "flat", "adds_paused": [{"ticker": "MRVL", "reason": "custom"}]})
    assert paused_flat["state"] == "not_called" and paused_flat["reason"] == "custom"


def test_unknown_when_stale_offline_or_other_epoch():
    grow = {"tone": "bull", "add_positions": [{"ticker": "MRVL"}]}
    assert _v(grow, brief_date=date(2026, 10, 18))["state"] == "unknown"
    assert _v(grow, brief_date=None)["state"] == "unknown"
    assert _v(grow, brief_epoch=None)["state"] == "unknown"
    assert _v(grow, brief_epoch=2, epoch=3)["state"] == "unknown"
    assert _v(None)["state"] == "unknown"
    assert _v("nope")["state"] == "unknown"
    r = _v(None)
    assert "open 🏠 Home" in r["reason"] and r["item"] is None and r["gate_id"] is None


def test_date_compare_is_robust_to_iso_strings_and_datetimes():
    grow = {"tone": "bull", "add_positions": [{"ticker": "MRVL"}]}
    assert _v(grow, brief_date="2026-10-19", today=_TODAY)["state"] == "add"
    assert _v(grow, brief_date="2026-10-19T09:30:00", today="2026-10-19")["state"] == "add"


def test_verdict_never_raises_on_garbage_buckets():
    assert _v({"tone": "bull", "cooldown_adds": 5})["state"] in ("unknown", "not_called")


# ── 8. concentration_tile ─────────────────────────────────────────────────────

def test_tile_mrvl_shape_is_false_on_capital():
    t = sp.concentration_tile(existing_value=EXISTING, add_cost=ENTRY, gross_book=GROSS,
                              net_capital=NET)
    assert t["state"] is False
    assert t["detail"] == "8.8% → 10.1% of book · 22.9% → 26.2% of net capital"
    assert "$" not in t["detail"]


def test_tile_small_holding_is_true_and_without_add_has_no_arrow():
    t = sp.concentration_tile(existing_value=500.0, add_cost=None, gross_book=GROSS,
                              net_capital=NET)
    assert t["state"] is True and "→" not in t["detail"]
    assert t["detail"] == "2.6% of book · 6.8% of net capital"


def test_tile_breach_without_an_add_and_gross_only():
    assert sp.concentration_tile(existing_value=0.2 * GROSS, add_cost=0, gross_book=GROSS,
                                 net_capital=None)["state"] is False
    t = sp.concentration_tile(existing_value=100.0, add_cost=100.0, gross_book=GROSS,
                              net_capital=None)
    assert t["state"] is True and "net capital" not in t["detail"]


def test_tile_unknown_states():
    assert sp.concentration_tile(existing_value=None, add_cost=1.0, gross_book=GROSS,
                                 net_capital=NET) == {"state": None, "detail": "Holding unknown"}
    assert sp.concentration_tile(existing_value=1.0, add_cost=1.0, gross_book=0.0,
                                 net_capital=NET) == {"state": None, "detail": "See Account page"}


# ── 9. Grow Today add loop ────────────────────────────────────────────────────

_T = "ZT"


class _FixedDT(daily_briefing._dt):
    @classmethod
    def now(cls, tz=None):
        from datetime import datetime
        naive = datetime(2026, 10, 19, 7, 0)
        return tz.localize(naive) if tz is not None else naive


def _grow(market_value, *, net_capital=7_000.0, pv=100_000.0, tone="bull", drop_mv=False):
    row = {"ticker": _T, "weight": 5.0, "sector": "SecA", "score": COMPOSITE_BUY + 15,
           "signal": "Strong Buy", "gap_to_stop": ADD_WINNER_MIN_GAP_PCT + 2, "price": 100.0}
    if market_value is not None:
        row["market_value"] = market_value
    df = make_port_df([row])
    if drop_mv:
        df = df.drop(columns=["Market Value"])
    pct = {"bull": 0.8, "flat": 0.0, "bear": -0.8}[tone]
    ctx = {"tone": tone, "sp500_pct": pct, "nasdaq_pct": pct * 1.2, "leading_sectors": []}
    with mock.patch.object(daily_briefing, "_dt", _FixedDT):
        return _grow_today(df, None, [], {_T: {"stop": 90.0}}, date(2026, 10, 19), pv, ctx,
                           composites={}, net_capital=net_capital)


def test_add_with_no_capital_room_lands_in_room_blocked_adds_with_g31():
    g = _grow(1_700.0)               # cap 1750 -> room 0.5 share -> none
    assert [x["ticker"] for x in g["add_positions"]] == []
    assert [x["ticker"] for x in g["room_blocked_adds"]] == [_T]
    it = g["room_blocked_adds"][0]
    assert it["gate_id"] == "G-31" and it["counterfactual"] is True
    assert it["room_binding"] == "capital"
    assert it["gate_threshold"] == NET_CAPITAL_POSITION_CAP_PCT
    assert it["gate_value"] == 24.3 == it["existing_capital_pct"]
    assert "24.3% of your net capital" in it["reason"] and "(cap 25%)" in it["reason"]
    assert it["price"] == 100.0 and it["weight"] == 5.0 and it["momentum_score"] is None
    assert it["composite_score"] == COMPOSITE_BUY + 15


def test_add_with_gross_binding_uses_gross_wording():
    g = _grow(14_950.0, net_capital=None)    # ceiling 15,000, price 100 -> room 0.5 share
    it = g["room_blocked_adds"][0]
    assert it["room_binding"] == "ceiling" and it["gate_threshold"] == SINGLE_NAME_CEILING
    assert "of your book" in it["reason"] and "(cap 15%)" in it["reason"]


def test_add_with_partial_room_is_sized_smaller_with_a_room_note():
    full = _grow(0.0)["add_positions"][0]
    part = _grow(1_000.0)
    assert part["room_blocked_adds"] == []
    a = part["add_positions"][0]
    assert a["sizing"]["shares"] == 7 and a["sizing"]["shares"] < full["sizing"]["shares"]
    assert a["sizing"]["room_capped"] is True and a["sizing"]["post_capital_pct"] < 25.0
    # The note is derived from the FINAL sizing by the shared helper, never stored on the item.
    assert "room_note" not in a and "room_note" not in full
    assert sp.room_note(a["sizing"]) == (
        "Capped by what you already hold — 14.3% → 24.3% of net capital (cap 25%)")
    assert sp.room_note(full["sizing"]) is None


def test_unchanged_add_has_no_room_note():
    a = _grow(0.0, net_capital=None)["add_positions"][0]
    assert "room_note" not in a and "room_capped" not in a["sizing"]
    assert "net_capital" not in a["sizing"]    # existing_value == 0 stays byte-identical


def test_missing_market_value_is_holding_unknown_never_a_fresh_size():
    g = _grow(None, drop_mv=True)
    a = g["add_positions"][0]
    assert a["sizing"].get("holding_unknown") is True and "shares" not in a["sizing"]
    assert g["room_blocked_adds"] == []


def test_room_check_runs_after_other_gates_so_they_keep_their_bucket():
    # A cooldown'd name never reaches the room check, even with no room.
    row = {"ticker": _T, "weight": 5.0, "sector": "SecA", "score": COMPOSITE_BUY + 15,
           "signal": "Strong Buy", "gap_to_stop": ADD_WINNER_MIN_GAP_PCT + 2, "price": 100.0,
           "market_value": 1_700.0}
    df = make_port_df([row])
    ctx = {"tone": "bull", "sp500_pct": 0.8, "nasdaq_pct": 0.9, "leading_sectors": []}
    with mock.patch.object(daily_briefing, "_dt", _FixedDT):
        g = _grow_today(df, None, [], {_T: {"stop": 90.0}}, date(2026, 10, 19), 100_000.0, ctx,
                        composites={}, net_capital=7_000.0, sold_today={_T})
    assert g["room_blocked_adds"] == [] and [x["ticker"] for x in g["card_blocked_adds"]] == [_T]


@pytest.mark.parametrize("tone", ["bear", "flat", "bull"])
def test_room_blocked_adds_is_always_a_list_never_omitted(tone):
    g = _grow(0.0, tone=tone)
    assert isinstance(g["room_blocked_adds"], list)


def test_render_adapter_room_infeasible_and_holding_unknown_markers():
    m = _position_size_for_render(GROSS, ENTRY, STOP, 1.0, 2.0, net_capital=NET,
                                  existing_value=EXISTING)
    assert m["room_infeasible"] is True and "shares" not in m
    assert m["room_binding"] == "capital" and m["cap_pct"] == NET_CAPITAL_POSITION_CAP_PCT
    assert m["existing_capital_pct"] == 22.9 and m["one_more_pct"] == 26.2
    assert m["existing_gross_pct"] == 8.8 and m["existing_value"] == EXISTING
    assert m["sizing_version"] == daily_briefing.SIZING_FORMULA_VERSION
    g = _position_size_for_render(10_000.0, 100.0, 90.0, 1.0, 2.0, net_capital=None,
                                  existing_value=2_000.0)
    assert g["room_binding"] == "ceiling" and g["existing_capital_pct"] is None
    assert g["one_more_pct"] == 21.0 and g["cap_pct"] == SINGLE_NAME_CEILING
    h = _position_size_for_render(GROSS, ENTRY, STOP, 1.0, 2.0, net_capital=NET,
                                  existing_value=float("nan"))
    assert h == {"holding_unknown": True,
                 "sizing_version": daily_briefing.SIZING_FORMULA_VERSION}


def test_render_adapter_sized_add_copies_the_room_fields():
    s = _position_size_for_render(GROSS, ENTRY, STOP, 1.0, 2.0, net_capital=NET,
                                  existing_value=1400.0)
    assert s["room_capped"] is True and s["room_binding"] == "capital"
    assert s["existing_value"] == 1400.0
    assert s["post_capital_pct"] > 0 and s["post_portfolio_pct"] > 0


# ── 10. budget ────────────────────────────────────────────────────────────────

def test_versions_are_five_and_equal():
    assert daily_budget.BUDGET_SIZING_VERSION == daily_briefing.SIZING_FORMULA_VERSION == 5


@pytest.mark.parametrize("marker", [
    {"room_infeasible": True, "room_binding": "capital", "sizing_version": 5},
    {"holding_unknown": True, "sizing_version": 5},
])
def test_room_and_holding_markers_are_budget_passthrough_consuming_zero(marker):
    assert daily_budget._is_marker(marker) is True
    basis = {"state": "ok", "basis": 10_000.0}
    r = daily_budget.allocate(
        basis, {}, {},
        [{"ticker": "AAA", "composite": 90, "price": 100.0, "sizing": marker},
         {"ticker": "BBB", "composite": 80, "price": 100.0,
          "sizing": {"shares": 10, "total_cost": 1000.0, "stop": 90.0}}])
    assert r["per_ticker"]["AAA"]["status"] == daily_budget.PASSTHROUGH
    assert r["used_list"] == 1000.0


# ── 11. gate ledger / registry ────────────────────────────────────────────────

def test_room_blocked_adds_item_writes_one_add_winner_g31_row():
    item = {"ticker": "mrvl", "gate_id": "G-31", "counterfactual": True, "gate_value": 24.3,
            "gate_threshold": NET_CAPITAL_POSITION_CAP_PCT, "price": 238.86,
            "composite_score": 82.0, "momentum_score": None, "sector": "Semis",
            "reason": "r"}
    rows = gate_ledger.build_suppression_rows(
        {"room_blocked_adds": [item]}, rec_date=_TODAY, source="app", tone="bull")
    assert len(rows) == 1
    r = rows[0]
    assert r["gate_id"] == "G-31" and r["lane"] == "add_winner" and r["ticker"] == "MRVL"
    assert r["gate_value"] == 24.3 and r["gate_threshold"] == NET_CAPITAL_POSITION_CAP_PCT
    assert r["price_at_suppress"] == 238.86 and r["counterfactual"] is True


def test_g31_is_registered_and_bucket_mapped():
    from stock_analyzer.gate_registry import GATE_IDS
    assert "G-31" in GATE_IDS
    assert gate_ledger._BUCKET_LANES["room_blocked_adds"] == "add_winner"


def test_end_to_end_room_blocked_item_produces_a_ledger_row():
    g = _grow(1_700.0)
    rows = gate_ledger.build_suppression_rows(g, rec_date=_TODAY, source="app", tone="bull")
    assert [r["gate_id"] for r in rows if r["gate_id"] == "G-31"] == ["G-31"]


# ── 12. notify ────────────────────────────────────────────────────────────────

def test_notify_cap_note_covers_the_new_states_and_never_blanks():
    from stock_analyzer import notify
    room = _position_size_for_render(GROSS, ENTRY, STOP, 1.0, 2.0, net_capital=NET,
                                     existing_value=EXISTING)
    n = notify._sizing_cap_note(room)
    assert "No add size suggested" in n and "25% cap" in n
    unk = notify._sizing_cap_note({"holding_unknown": True})
    assert "couldn&#39;t be read" in unk
    capped = _position_size_for_render(GROSS, ENTRY, STOP, 1.0, 2.0, net_capital=NET,
                                       existing_value=1400.0)
    c = notify._sizing_cap_note(capped)
    assert "already hold" in c
    gross_only = notify._sizing_cap_note({"shares": 3, "room_capped": True,
                                          "post_portfolio_pct": 14.6, "room_binding": "ceiling"})
    assert "of the book" in gross_only


def test_sizing_path_is_a_gate_file_and_never_imports_margin():
    import pathlib
    import re
    hook = pathlib.Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_checks.py"
    assert '"stock_analyzer/sizing_path.py"' in hook.read_text(encoding="utf-8")
    root = pathlib.Path(__file__).resolve().parent.parent / "stock_analyzer"
    for f in ("sizing_path.py", "risk.py"):
        src = (root / f).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+stock_analyzer(\.| import )margin", src, re.M), f
        assert "streamlit" not in src


# ── C1a review fixes ──────────────────────────────────────────────────────────

def _room_capped_add():
    return _grow(1_000.0)["add_positions"][0]["sizing"]    # 7 sh, post_capital_pct 24.3


def test_budget_partial_recomputes_post_pct_and_drops_the_room_claim():
    sz = _room_capped_add()
    assert sz["shares"] == 7 and sz["room_capped"] is True and sz["net_capital"] == 7000.0
    out = daily_budget.apply_to_sizing(
        sz, {"status": daily_budget.BUDGET_PARTIAL, "shares": 2, "dollars": 200.0,
             "uncapped_shares": 7})
    assert out["shares"] == 2
    assert out["post_capital_pct"] == round((1000.0 + 200.0) / 7000.0 * 100, 1) == 17.1
    assert out["post_portfolio_pct"] == round(1200.0 / 100_000.0 * 100, 1)
    assert out["room_capped"] is False and out["room_binding"] is None
    assert sp.room_note(out) is None
    assert sz["room_capped"] is True      # input never mutated


@pytest.mark.parametrize("status", [daily_budget.BUDGET_EXHAUSTED, daily_budget.BUDGET_UNKNOWN])
def test_budget_without_a_size_strips_every_room_key(status):
    out = daily_budget.apply_to_sizing(_room_capped_add(), {"status": status, "reason": "x"})
    for k in ("post_portfolio_pct", "post_capital_pct", "room_capped", "room_binding",
              "existing_value", "net_capital", "shares"):
        assert k not in out, k
    assert sp.room_note(out) is None


def test_room_note_none_without_shares_or_flag():
    assert sp.room_note({"room_capped": True}) is None
    assert sp.room_note({"room_capped": True, "room_binding": "ceiling", "existing_value": 1000.0,
                         "portfolio_value": 10_000.0, "post_portfolio_pct": 14.0}) is None  # no shares
    assert sp.room_note({"shares": 3, "room_capped": True, "room_binding": "ceiling",
                         "existing_value": 1000.0, "portfolio_value": 10_000.0,
                         "post_portfolio_pct": 14.0}) is not None
    assert sp.room_note({"shares": 3, "room_capped": False}) is None
    assert sp.room_note(None) is None


def test_room_note_book_variant():
    g = _grow(14_000.0, net_capital=None)["add_positions"][0]["sizing"]
    assert g["room_binding"] == "ceiling"
    n = sp.room_note(g)
    assert n.endswith("of the book (cap 15%)") and n.startswith("Capped by what you already hold")


def test_concentration_tile_exactly_on_the_cap_of_the_pre_trade_book_is_not_a_breach():
    t = sp.concentration_tile(existing_value=500.0, add_cost=1000.0,
                              gross_book=10_000.0, net_capital=None)
    assert t["state"] is True and "15.0% of book" in t["detail"]   # displayed pre-trade; ✗ test is post-trade


def test_margin_called_account_holding_the_name_reads_capital_not_room():
    assert risk.sizing_unavailable_reason(
        100_000.0, 100.0, 90.0, SINGLE_NAME_CEILING, -50.0,
        NET_CAPITAL_POSITION_CAP_PCT, 14_950.0) == "capital"


def test_entry_held_add_with_no_room_stays_in_entry_held_not_room_blocked():
    row = {"ticker": _T, "weight": 5.0, "sector": "SecA", "score": COMPOSITE_BUY + 15,
           "signal": "Strong Buy", "gap_to_stop": ADD_WINNER_MIN_GAP_PCT + 2, "price": 100.0,
           "market_value": 1_700.0}          # no capital room (see the G-31 test above)
    ctx = {"tone": "bull", "sp500_pct": 0.8, "nasdaq_pct": 0.9, "leading_sectors": []}
    with mock.patch.object(daily_briefing, "_dt", _FixedDT):
        g = _grow_today(make_port_df([row]), None, [],
                        {_T: {"stop": 90.0, "stale_as_of": "2026-10-01"}},
                        date(2026, 10, 19), 100_000.0, ctx, composites={}, net_capital=7_000.0)
    assert [x["ticker"] for x in g["entry_held_adds"]] == [_T]
    assert g["room_blocked_adds"] == [] and g["add_positions"] == []
