"""P1 of docs/plans/grow-today-owns-buys.md (2026-10-15): Grow Today owns every
buy. The scanner-only `_buy_candidates` lane and its `buy_candidates` brief key
are gone; `_grow_today` now publishes four disclosure keys on EVERY return path:

  overflow_picks     cleared every check, lost only to pacing (no sizing)
  flat_day_held      curated names the flat-day check holds back, with why
  unevaluated_leads  scanner names Grow Today never evaluated (NO verdict)
  adds_paused        non-bull days: held names that WOULD qualify for an add

Every "held-out" assertion here is paired with a control that FIRES in the same
fixture and is asserted non-empty, so a no-op implementation cannot pass.
"""
import ast
from tests._ast_cache import parse_file_cached
import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest

from stock_analyzer import daily_briefing, gate_ledger
from stock_analyzer.act_today_view import offline_brief_stub
from stock_analyzer.constants import (
    ADD_WINNER_COOLDOWN_DAYS,
    ADD_WINNER_MIN_GAP_PCT,
    ATR_STOP_MULT,
    COMPOSITE_BUY,
    COMPOSITE_BUY_FLAT_DAY,
    DETERIORATION_TREND_MA,
    GROW_CANDIDATE_OVERFETCH,
    GROW_MAX_PICKS_BULL,
    GROW_MAX_PICKS_DEFAULT,
    MOVER_MAX_PICKS,
    SECTOR_CEILING,
    SINGLE_NAME_CEILING,
)
from stock_analyzer.daily_briefing import _grow_today, build_daily_briefing
from stock_analyzer.pillar_availability import verdict_basis
from tests.conftest import find_item, make_port_df
from tests.test_daily_briefing import _cab, _pab, _scanner_df, _winner_row

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 15)

_SECTORS = ["Energy", "Healthcare", "Financials", "Utilities", "Materials",
            "Real Estate", "Industrials", "Consumer", "Telecom"]


def _bundle(total=80.0, price=100.0, atr=3.0, **extra):
    b = {
        "total": total, "rec": {"label": "Buy"},
        "fundamentals_available": True,
        "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
        "entry_lo": 99.0, "entry_hi": 101.5,
    }
    b.update(extra)
    return b


def _held():
    return make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])


def _run(rows, composites, tone="bull", port_df=None, movers=None, **kw):
    return _grow_today(
        port_df if port_df is not None else _held(), _scanner_df(rows) if rows else None,
        [], {}, _TODAY, 100_000.0, {"tone": tone}, composites=composites,
        movers=movers, **kw)


def _tk(items):
    return [i["ticker"] for i in items]


_NEW_KEYS = ("overflow_picks", "flat_day_held", "unevaluated_leads", "adds_paused")

# every bucket a pick can be held out into -- overflow must be disjoint from all
_HELD_OUT = ("macro_blocked_picks", "sector_blocked_picks", "sector_unknown_picks",
             "composite_skipped", "composite_unavailable", "news_skipped",
             "earnings_waiting", "etf_screened_out")


def _cap_fixture(n_extra=2, score=95.0):
    """GROW_MAX_PICKS_BULL + n_extra qualifying names, each its own sector."""
    n = GROW_MAX_PICKS_BULL + n_extra
    rows = [{"ticker": f"P{i}", "score": score - i, "sector": _SECTORS[i]} for i in range(n)]
    comps = {r["ticker"]: _bundle(80.0) for r in rows}
    return rows, comps


# ── the four keys are on EVERY return path ──────────────────────────────────

@pytest.mark.parametrize("tone", ["bull", "flat", "bear"])
def test_four_keys_present_on_every_tone(tone):
    g = _run([], {}, tone=tone)
    for k in _NEW_KEYS:
        assert k in g and isinstance(g[k], list), (tone, k)
    assert "pair_buy_lane_skips" not in g


def test_brief_has_no_buy_candidates_key_and_stub_matches():
    brief = build_daily_briefing(
        port_df=_held(), alert_list=[], risk_recs=[], news_items=[], macro_events=[],
        held_data={}, scanner_results=None, portfolio_value=100_000.0, today=_TODAY,
        market_context={"tone": "bull"})
    assert "buy_candidates" not in brief
    # stub keys (minus the marker) must be a subset of the real brief's keys
    stub_keys = set(offline_brief_stub()) - {"_brief_offline_stub"}
    assert stub_keys <= set(brief)
    assert "buy_candidates" not in stub_keys


# ── overflow_picks ──────────────────────────────────────────────────────────

def test_cap_boundary_exactly_at_cap_has_no_overflow_one_over_has_pick_cap():
    rows, comps = _cap_fixture(n_extra=0)
    at_cap = _run(rows, comps)
    assert len(at_cap["new_picks"]) == GROW_MAX_PICKS_BULL     # control: picks DO fire
    assert at_cap["overflow_picks"] == []

    rows, comps = _cap_fixture(n_extra=1)
    over = _run(rows, comps)
    assert len(over["new_picks"]) == GROW_MAX_PICKS_BULL
    assert [(o["ticker"], o["reason"]) for o in over["overflow_picks"]] == [
        (f"P{GROW_MAX_PICKS_BULL}", "pick_cap")]


def test_sector_slot_vs_pick_cap_classification():
    rows = [
        {"ticker": "ZZA", "score": 95, "sector": "Energy"},
        {"ticker": "ZZB", "score": 94, "sector": "Healthcare"},
        {"ticker": "ZZC", "score": 93, "sector": "Financials"},
        {"ticker": "ZZD", "score": 92, "sector": "Energy"},      # sector taken by A
        {"ticker": "ZZE", "score": 91, "sector": "Utilities"},   # sector free, cap hit
    ]
    g = _run(rows, {r["ticker"]: _bundle(80.0) for r in rows})
    assert _tk(g["new_picks"]) == ["ZZA", "ZZB", "ZZC"]
    reasons = {o["ticker"]: o["reason"] for o in g["overflow_picks"]}
    assert reasons == {"ZZD": "sector_slot", "ZZE": "pick_cap"}


def test_mover_cap_overflow():
    movers = [{"ticker": f"M{i}", "price": 100.0, "sector": _SECTORS[i], "score": 50.0,
               "composite_score": 90.0 - i, "day_change": 6.0} for i in range(MOVER_MAX_PICKS + 1)]
    comps = {m["ticker"]: _bundle(80.0) for m in movers}
    g = _run([], comps, movers=movers)
    assert len([p for p in g["new_picks"] if p["is_mover"]]) == MOVER_MAX_PICKS  # control
    assert [(o["ticker"], o["reason"]) for o in g["overflow_picks"]] == [
        (f"M{MOVER_MAX_PICKS}", "mover_cap")]
    assert g["overflow_picks"][0]["is_mover"] is True


def test_overflow_is_unsized_disjoint_and_every_item_is_a_go_equity_pick():
    rows, comps = _cap_fixture(n_extra=2)
    g = _run(rows, comps)
    assert g["overflow_picks"], "control: overflow must fire in this fixture"
    assert any(p.get("sizing") for p in g["new_picks"]), "control: picks ARE sized"
    picked = set(_tk(g["new_picks"]))
    held_out = {t for b in _HELD_OUT for t in _tk(g[b])}
    for o in g["overflow_picks"]:
        assert "sizing" not in o and "shares" not in o
        assert set(o) == {"ticker", "sector", "composite_score", "momentum_score",
                          "price", "reason", "verdict", "is_mover"}
        assert o["reason"] in ("pick_cap", "sector_slot", "mover_cap")
        assert o["ticker"] not in picked and o["ticker"] not in held_out
        assert verdict_basis(comps[o["ticker"]]) == "equity"
        assert o["composite_score"] >= COMPOSITE_BUY


def test_withheld_stale_and_etf_never_overflow_but_land_in_unavailable():
    rows, comps = _cap_fixture(n_extra=2)
    extra = [
        {"ticker": "WH", "score": 99, "sector": "Industrials"},
        {"ticker": "ST", "score": 98, "sector": "Consumer"},
        {"ticker": "EF", "score": 97, "sector": "Telecom"},
    ]
    comps["WH"] = _bundle(80.0, fundamentals_available=False)
    comps["ST"] = _bundle(80.0, stale_as_of="2026-10-10")
    comps["EF"] = _bundle(80.0, asset_type="etf")
    g = _run(rows + extra, comps)
    assert g["overflow_picks"], "control: genuine overflow fires in the same fixture"
    assert {"WH", "ST", "EF"} <= set(_tk(g["composite_unavailable"]))
    assert not ({"WH", "ST", "EF"} & set(_tk(g["overflow_picks"])))


def test_tsla_shape_flat_vs_bull_with_composite_80_control():
    """Momentum 90 / composite 48.8 Hold: it fails the composite gate on ANY
    day, so flat AND bull -> composite_skipped (2026-10-15: the flat market is
    not why it is held). Never overflow, never flat_day_held. A composite-80
    name is the control in the same fixture."""
    rows = [
        {"ticker": "TSLA", "score": 90, "sector": "Consumer"},
        {"ticker": "OKAY", "score": 91, "sector": "Energy"},
    ]
    comps = {"TSLA": _bundle(48.8, rec={"label": "Hold"}), "OKAY": _bundle(80.0)}
    flat = _run(rows, comps, tone="flat")
    assert "OKAY" in _tk(flat["new_picks"])                 # control fires
    assert "TSLA" in _tk(flat["composite_skipped"])
    assert "TSLA" not in _tk(flat["flat_day_held"])
    assert "TSLA" not in _tk(flat["overflow_picks"])

    # bull: composite 80 beyond the cap is overflow; TSLA is composite_skipped
    rows2, comps2 = _cap_fixture(n_extra=1)
    rows2.append(rows[0])
    comps2["TSLA"] = comps["TSLA"]
    bull = _run(rows2, comps2)
    assert "TSLA" in _tk(bull["composite_skipped"])
    assert _tk(bull["overflow_picks"]) == [f"P{GROW_MAX_PICKS_BULL}"]
    assert bull["flat_day_held"] == []


# ── flat_day_held ───────────────────────────────────────────────────────────

def test_flat_day_held_earnings_caution_carries_a_derived_clears_when():
    rows = [{"ticker": "SPOT", "score": 90, "sector": "Telecom"},
            {"ticker": "OKAY", "score": 91, "sector": "Energy"}]
    comps = {"SPOT": _bundle(80.0), "OKAY": _bundle(80.0)}
    g = _run(rows, comps, tone="flat",
             earnings_lookup={"SPOT": (_TODAY + timedelta(days=3)).isoformat()})
    assert "OKAY" in _tk(g["new_picks"])                    # control
    spot = find_item(g["flat_day_held"], "SPOT")
    assert spot is not None and spot["verdict"] == "caution"
    assert spot["earnings_date"] == (_TODAY + timedelta(days=3)).isoformat()
    assert spot["clears_when"] == f"after earnings ({spot['earnings_date']}), if it still qualifies"
    assert spot["earn_days"] == 3
    assert spot["composite_score"] == 80.0
    assert set(spot) == {"ticker", "sector", "momentum_score", "composite_score", "verdict",
                         "reason", "clears_when", "earnings_date", "earn_days"}
    assert "SPOT" not in _tk(g["composite_skipped"] + g["composite_unavailable"])


def test_flat_day_subbar_names_route_to_composite_skipped_not_flat_held():
    """The 2026-10-15 bug: a conflicted 57 sat in 'held by the flat market'
    above a 62 in 'composite says no'. Sub-bar names (conflicted OR caution)
    go to composite_skipped; the composite-80 caution control stays held."""
    rows = [{"ticker": "SPOT", "score": 90, "sector": "Telecom"},   # conflicted, 57
            {"ticker": "CAUT", "score": 90, "sector": "Materials"},  # caution, 64.9
            {"ticker": "HELD80", "score": 90, "sector": "Utilities"},  # caution, 80
            {"ticker": "OKAY", "score": 91, "sector": "Energy"}]
    comps = {"SPOT": _bundle(57.0, rec={"label": "Hold"}),
             "CAUT": _bundle(COMPOSITE_BUY - 0.1),
             "HELD80": _bundle(80.0), "OKAY": _bundle(80.0)}
    earn = (_TODAY + timedelta(days=3)).isoformat()
    g = _run(rows, comps, tone="flat",
             earnings_lookup={"SPOT": earn, "CAUT": earn, "HELD80": earn})
    assert "OKAY" in _tk(g["new_picks"])                    # control
    held = find_item(g["flat_day_held"], "HELD80")
    assert held is not None and held["clears_when"]         # box's real purpose
    for t in ("SPOT", "CAUT"):
        assert t in _tk(g["composite_skipped"]), t
        assert t not in _tk(g["flat_day_held"]), t
    # no name lands in two buckets
    seen = [t for b in _HELD_OUT + ("new_picks", "overflow_picks", "flat_day_held")
            for t in _tk(g[b])]
    assert len(seen) == len(set(seen))


def test_flat_day_subbar_outranking_a_qualifier_does_not_leak_staged_hold():
    """SUB (95, composite 57) is staged then rejected ahead of OKAY (85, 80):
    the staged item must not leak onto OKAY, and OKAY must still be picked."""
    rows = [{"ticker": "SUB", "score": 95, "sector": "Telecom"},
            {"ticker": "OKAY", "score": 85, "sector": "Energy"}]
    comps = {"SUB": _bundle(57.0, rec={"label": "Hold"}), "OKAY": _bundle(80.0)}
    g = _run(rows, comps, tone="flat")
    assert _tk(g["new_picks"]) == ["OKAY"]
    assert "SUB" in _tk(g["composite_skipped"])
    assert "SUB" not in _tk(g["flat_day_held"])
    assert "OKAY" not in _tk(g["flat_day_held"])


def test_flat_day_composite_skip_item_matches_bull_day_item():
    rows = [{"ticker": "TSLA", "score": 90, "sector": "Consumer"}]
    comps = {"TSLA": _bundle(57.0, rec={"label": "Hold"})}
    flat = _run(rows, comps, tone="flat")
    bull = _run(rows, comps, tone="bull")
    f, b = find_item(flat["composite_skipped"], "TSLA"), find_item(bull["composite_skipped"], "TSLA")
    assert f is not None and f == b
    assert flat["flat_day_held"] == []


def test_flat_day_unmeasured_names_land_in_composite_unavailable_like_bull_day():
    rows = [{"ticker": "STAL", "score": 90, "sector": "Telecom"},
            {"ticker": "WITH", "score": 90, "sector": "Materials"},
            {"ticker": "OKAY", "score": 91, "sector": "Energy"}]
    comps = {"STAL": _bundle(80.0, stale_as_of="2026-10-10"),
             "WITH": _bundle(80.0, fundamentals_available=False),
             "OKAY": _bundle(80.0)}
    earn = (_TODAY + timedelta(days=3)).isoformat()
    el = {"STAL": earn, "WITH": earn}
    flat = _run(rows, comps, tone="flat", earnings_lookup=el)
    bull = _run(rows, comps, tone="bull", earnings_lookup=el)
    assert "OKAY" in _tk(flat["new_picks"])                 # control
    for t in ("STAL", "WITH"):
        assert t in _tk(flat["composite_unavailable"]), t
        assert t not in _tk(flat["flat_day_held"]), t
        assert find_item(flat["composite_unavailable"], t) == find_item(bull["composite_unavailable"], t)


def test_flat_day_held_composite_is_the_trusted_value():
    rows = [{"ticker": "SPOT", "score": 90, "sector": "Telecom"},
            {"ticker": "OKAY", "score": 91, "sector": "Energy"}]
    comps = {"SPOT": _bundle(80.0), "OKAY": _bundle(80.0)}
    g = _run(rows, comps, tone="flat",
             earnings_lookup={"SPOT": (_TODAY + timedelta(days=3)).isoformat()})
    assert "OKAY" in _tk(g["new_picks"])                    # control
    spot = find_item(g["flat_day_held"], "SPOT")
    assert spot is not None and spot["composite_score"] == 80.0


def test_flat_day_held_empty_on_bull_day():
    rows = [{"ticker": "TSLA", "score": 90, "sector": "Consumer"}]
    g = _run(rows, {"TSLA": _bundle(48.8, rec={"label": "Hold"})}, tone="bull")
    assert g["flat_day_held"] == []
    assert "TSLA" in _tk(g["composite_skipped"])            # control: it WAS evaluated


# ── unevaluated_leads ───────────────────────────────────────────────────────

def test_flat_day_unevaluated_leads_below_bar_and_beyond_working_set():
    n_work = GROW_MAX_PICKS_DEFAULT * GROW_CANDIDATE_OVERFETCH
    hi = [{"ticker": f"H{i}", "score": 99 - i, "sector": _SECTORS[i % len(_SECTORS)]}
          for i in range(n_work + 2)]                        # >= flat bar, 2 beyond set
    lo = [{"ticker": "LOW", "score": COMPOSITE_BUY_FLAT_DAY - 1, "sector": "Energy"}]
    comps = {r["ticker"]: _bundle(80.0) for r in hi}
    g = _run(hi + lo, comps, tone="flat")
    got = {i["ticker"] for i in g["unevaluated_leads"]}
    assert got == {f"H{n_work}", f"H{n_work + 1}", "LOW"}
    for i in g["unevaluated_leads"]:
        assert set(i) == {"ticker", "momentum_score"}        # NO verdict, ever
    assert g["new_picks"]                                     # control: day evaluated


def test_bear_day_every_scanner_lead_is_unevaluated_and_nothing_is_picked():
    rows = [{"ticker": "MRVL", "score": 75, "sector": "Energy"},
            {"ticker": "KLAC", "score": 66, "sector": "Utilities"},
            {"ticker": "WEAK", "score": COMPOSITE_BUY - 1, "sector": "Materials"}]
    g = _run(rows, {r["ticker"]: _bundle(80.0) for r in rows}, tone="bear")
    assert [(i["ticker"], i["momentum_score"]) for i in g["unevaluated_leads"]] == [
        ("MRVL", 75.0), ("KLAC", 66.0)]                      # below COMPOSITE_BUY excluded
    assert g["new_picks"] == [] and g["overflow_picks"] == []
    # control: the same scanner on a bull day IS evaluated (so not "unevaluated")
    bull = _run(rows, {r["ticker"]: _bundle(80.0) for r in rows}, tone="bull")
    assert bull["new_picks"] and bull["unevaluated_leads"] == []


# ── adds_paused ─────────────────────────────────────────────────────────────

def _adds_book():
    """CLEAN qualifies; the rest each trip exactly one add gate (13 positions so
    the drift floor 100/13+5 sits below SINGLE_NAME_CEILING)."""
    rows = [
        _winner_row(ticker="CLEAN", weight=5.0, sector="Healthcare"),
        _winner_row(ticker="T01", weight=5.0, sector="Financials"),     # risk trim
        _winner_row(ticker="T04", weight=SINGLE_NAME_CEILING, sector="Utilities"),
        _winner_row(ticker="T09", weight=SINGLE_NAME_CEILING - 1, sector="Materials"),
        _winner_row(ticker="T16", weight=SINGLE_NAME_CEILING - 1, sector="Energy"),
        _winner_row(ticker="T20", weight=5.0, sector="Industrials"),    # WATCH
        _winner_row(ticker="T24", weight=5.0, sector="Consumer"),       # cooldown
        _winner_row(ticker="T25", weight=5.0, sector="Telecom"),        # G-25
        _winner_row(ticker="T26", weight=5.0, sector="Real Estate"),    # G-26
        # fillers: push Energy over SECTOR_CEILING for T16, pad the count
        {"ticker": "F1", "weight": SINGLE_NAME_CEILING - 1, "sector": "Energy"},
        {"ticker": "F2", "weight": SINGLE_NAME_CEILING - 1, "sector": "Energy"},
        {"ticker": "F3", "weight": 1.0, "sector": "Insurance"},
        {"ticker": "F4", "weight": 1.0, "sector": "Insurance"},
    ]
    assert 3 * (SINGLE_NAME_CEILING - 1) >= SECTOR_CEILING
    return make_port_df(rows)


def _adds_kwargs():
    return dict(
        risk_recs=[{"priority": "HIGH", "type": "beta", "title": "Beta rec",
                    "root_tickers": [{"ticker": "T01"}]}],
        deterioration=[{"ticker": "T20", "tier": "WATCH", "dd_from_peak_pct": 12.0,
                        "peak": 110.0, "trend_ma": DETERIORATION_TREND_MA}],
        cluster_add_blocks=_cab("T25"),
        pair_add_blocks=_pab("T26"),
    )


_ADDS_HELD_DATA = {"T24": {"days_since_last_buy": 1},
                   "CLEAN": {"stop": 90.0, "entry_lo": 99.0, "entry_hi": 101.0}}


def _grow_adds_book(tone):
    return _grow_today(_adds_book(), None, [], _ADDS_HELD_DATA,
                       _TODAY, 100_000.0, {"tone": tone, "sp500_pct": -3.0 if tone == "bear" else 0.0},
                       **_adds_kwargs())


def test_bull_day_gate_parity_every_add_gate_still_binds():
    """Deleting the buy-candidates add half lost nothing: every add gate
    (G-01/04/09/16/20/24/25/26) still blocks on the surviving lane, and the
    control name is the only add."""
    g = _grow_adds_book("bull")
    assert _tk(g["add_positions"]) == ["CLEAN"]
    assert g["add_positions"][0]["sizing"]
    assert "T01" in _tk(g["risk_blocked_adds"])                              # G-01
    assert "T04" in _tk(g["concentration_blocked_adds"])                     # G-04
    t09 = find_item(g["concentration_blocked_adds"], "T09")
    assert t09 is not None and t09["gate_id"] == "G-09"                      # G-09
    assert "T16" in _tk(g["sector_blocked_adds"])                            # G-16
    assert find_item(g["deterioration_blocked_adds"], "T20")["counterfactual"] is True  # G-20
    assert "T24" in _tk(g["cooldown_adds"])                                  # G-24
    assert "T25" in _tk(g["cluster_blocked_adds"])                           # G-25
    assert "T26" in _tk(g["pair_blocked_adds"])                              # G-26
    assert g["adds_paused"] == []


@pytest.mark.parametrize("tone", ["flat", "bear"])
def test_non_bull_adds_paused_equals_what_bull_would_add(tone):
    bull = _grow_adds_book("bull")
    assert _tk(bull["add_positions"]) == ["CLEAN"]            # control FIRES
    g = _grow_adds_book(tone)
    assert g["add_positions"] == []
    assert _tk(g["adds_paused"]) == _tk(bull["add_positions"])
    assert set(g["adds_paused"][0]) == {"ticker", "composite_score"}   # no sizing
    assert g["adds_paused"][0]["composite_score"] == bull["add_positions"][0]["score"]


def test_bear_day_overflow_and_adds_empty_while_paused_is_populated():
    rows, comps = _cap_fixture(n_extra=2)
    g = _grow_today(_adds_book(), _scanner_df(rows), [], _ADDS_HELD_DATA,
                    _TODAY, 100_000.0, {"tone": "bear", "sp500_pct": -3.0},
                    composites=comps, **_adds_kwargs())
    assert g["overflow_picks"] == [] and g["add_positions"] == [] and g["new_picks"] == []
    assert _tk(g["adds_paused"]) == ["CLEAN"]
    # control: the same fixture on a bull day sizes the add AND has overflow
    bull = _grow_today(_adds_book(), _scanner_df(rows), [], _ADDS_HELD_DATA,
                       _TODAY, 100_000.0, {"tone": "bull"}, composites=comps, **_adds_kwargs())
    assert _tk(bull["add_positions"]) == ["CLEAN"] and bull["overflow_picks"]


def test_add_entry_rule_is_shared_not_copied():
    src = inspect.getsource(_grow_today)
    assert src.count("_add_entry_ok(") >= 2          # add loop + adds_paused
    assert '"Strong Buy" in' not in src              # the rule lives in ONE place
    # and the shared predicate itself is the documented rule
    row = {"Signal": "Strong Buy", "Score": COMPOSITE_BUY, "Gap to Stop (%)": ADD_WINNER_MIN_GAP_PCT}
    assert daily_briefing._add_entry_ok(row) is True
    assert daily_briefing._add_entry_ok({**row, "Score": COMPOSITE_BUY - 0.01}) is False
    assert daily_briefing._add_entry_ok({**row, "Gap to Stop (%)": ADD_WINNER_MIN_GAP_PCT - 0.01}) is False
    assert daily_briefing._add_entry_ok({**row, "Signal": "Buy"}) is False


# ── G-20 deterioration pre-pass survives the lane's deletion ────────────────

def test_g20_pre_pass_still_writes_a_non_bull_counterfactual_false_row():
    watch = [{"ticker": "T20", "tier": "WATCH", "dd_from_peak_pct": 12.0,
              "peak": 110.0, "trend_ma": DETERIORATION_TREND_MA}]
    pd_ = make_port_df([_winner_row(ticker="T20", weight=5.0)])
    # NOTE: the bear-day EARLY RETURN precedes this pre-pass (it always did), so a
    # bear day carries no deterioration_blocked_adds key at all -- the pre-pass
    # writes the non-bull G-20 rows on FLAT days only. (Plan said "bear-day";
    # reported to the lead rather than changed here.)
    for tone in ("flat",):
        g = _grow_today(pd_, None, [], {}, _TODAY, 100_000.0, {"tone": tone}, deterioration=watch)
        row = find_item(g["deterioration_blocked_adds"], "T20")
        assert row is not None and row["gate_id"] == "G-20", tone
        assert row["counterfactual"] is False, tone
        assert g["adds_paused"] == [], tone      # a WATCH name is never "would qualify"
    bear = _grow_today(pd_, None, [], {}, _TODAY, 100_000.0, {"tone": "bear"}, deterioration=watch)
    assert "deterioration_blocked_adds" not in bear
    assert bear["adds_paused"] == []
    bull = _grow_today(pd_, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"}, deterioration=watch)
    assert find_item(bull["deterioration_blocked_adds"], "T20")["counterfactual"] is True


# ── ledger: the new keys are not gates ──────────────────────────────────────

def test_ledger_emits_nothing_for_the_new_keys_on_a_real_grow_dict():
    rows, comps = _cap_fixture(n_extra=2)
    g = _grow_today(_adds_book(), _scanner_df(rows + [{"ticker": "LOWM", "score": 70, "sector": "Materials"}]),
                    [], {}, _TODAY, 100_000.0, {"tone": "flat"}, composites=comps)
    # flat day: make the new keys all populated in one dict via the bull+flat pair
    bull = _grow_today(_adds_book(), _scanner_df(rows), [], {}, _TODAY, 100_000.0,
                       {"tone": "bull"}, composites=comps)
    assert bull["overflow_picks"] and g["unevaluated_leads"]          # controls non-empty
    for d in (g, bull):
        base = gate_ledger.build_suppression_rows(
            d, rec_date=date(2026, 10, 15), source="app", tone=d["tone"], sp500_pct=0.0)
        stripped = {k: v for k, v in d.items() if k not in _NEW_KEYS}
        without = gate_ledger.build_suppression_rows(
            stripped, rec_date=date(2026, 10, 15), source="app", tone=d["tone"], sp500_pct=0.0)
        assert base == without
    for k in _NEW_KEYS:
        assert k not in gate_ledger._BUCKET_LANES


# ── AST: the retired lane is really gone ────────────────────────────────────

_PKG = Path(__file__).resolve().parent.parent / "stock_analyzer"
_RETIRED_NAMES = {"_buy_candidates", "buy_lane_only", "pair_skip_sink",
                  "buy_lane_pair_skips", "pair_buy_lane_skips"}
_RETIRED_STRINGS = {"buy_candidates", "pair_buy_lane_skips", "BUY — Scanner Pick"}
# Callers outside P1's files that P2/P3 remove. A second test pins that each
# entry is STILL offending, so the list can only shrink (a fixed file that stays
# listed fails loudly instead of rotting).
# P1 is committed ALONE first (snapshot = HEAD + P1 files), where HEAD still
# references the retired lane in these two files. The later combined commit
# removes both and empties this set (test_pending_list_only_shrinks then
# fails in the working tree until it does -- expected).
_PENDING_OTHER_COMMITS: set = set()   # emptied: P2/P3 landed (2026-10-15)


def _retired_hits(path):
    tree = parse_file_cached(path)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in _RETIRED_NAMES:
            hits.append((n.lineno, n.id))
        elif isinstance(n, ast.Attribute) and n.attr in _RETIRED_NAMES:
            hits.append((n.lineno, n.attr))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in _RETIRED_NAMES:
            hits.append((n.lineno, n.name))
        elif isinstance(n, ast.arg) and n.arg in _RETIRED_NAMES:
            hits.append((n.lineno, n.arg))
        elif isinstance(n, ast.keyword) and n.arg in _RETIRED_NAMES:
            hits.append((n.value.lineno, n.arg))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and (
                n.value in _RETIRED_STRINGS):
            hits.append((n.lineno, n.value))
    return hits


def test_retired_lane_is_gone_from_the_package():
    offenders = {}
    for f in sorted(_PKG.glob("*.py")):
        if f.name in _PENDING_OTHER_COMMITS:
            continue
        h = _retired_hits(f)
        if h:
            offenders[f.name] = h
    assert offenders == {}, offenders


def test_pending_list_only_shrinks():
    for name in _PENDING_OTHER_COMMITS:
        assert _retired_hits(_PKG / name), (
            f"{name} no longer references the retired lane -- remove it from "
            "_PENDING_OTHER_COMMITS")


# ── B2: ONE add-gate decision shared by the bull loop and adds_paused ───────

def _gate_book(gate):
    """CLEAN qualifies; BAD trips exactly `gate` (each book sized so no OTHER
    gate can fire: a 3-name book has drift floor 38%, the G-09 book has 13
    names, the G-16 book puts two Hold fillers in BAD's sector)."""
    clean = _winner_row(ticker="CLEAN", weight=5.0, sector="Healthcare")
    bad = _winner_row(ticker="BAD", weight=5.0, sector="Financials")
    fill = [{"ticker": "F1", "weight": 5.0, "sector": "Insurance"}]
    if gate == "G-04":
        bad["weight"] = SINGLE_NAME_CEILING
    elif gate == "G-09":
        bad["weight"] = SINGLE_NAME_CEILING - 1
        fill = [{"ticker": f"F{i}", "weight": 1.0, "sector": "Insurance"} for i in range(11)]
    elif gate == "G-16":
        bad["weight"] = SINGLE_NAME_CEILING - 1
        bad["sector"] = "Energy"
        fill = [{"ticker": f"F{i}", "weight": SINGLE_NAME_CEILING - 1, "sector": "Energy"}
                for i in range(2)]
        assert 3 * (SINGLE_NAME_CEILING - 1) >= SECTOR_CEILING
    return make_port_df([clean, bad] + fill)


_WATCH = [{"ticker": "BAD", "tier": "WATCH", "dd_from_peak_pct": 12.0,
           "peak": 110.0, "trend_ma": DETERIORATION_TREND_MA}]

# gate -> (kwargs, held_data extra, bucket the bull loop must record BAD in)
_GATE_CASES = {
    "act":     (dict(act_today=[{"ticker": "BAD"}]), {}, None),
    "review":  (dict(review_list=[{"ticker": "BAD", "action": {"type": "TRIM_TO_TARGET"}}]), {}, None),
    "sold":    (dict(sold_today={"BAD"}), {}, None),
    "G-01":    (dict(risk_recs=[{"priority": "HIGH", "type": "beta", "title": "Beta rec",
                                 "root_tickers": [{"ticker": "BAD"}]}]), {}, "risk_blocked_adds"),
    "G-04":    ({}, {}, "concentration_blocked_adds"),
    "G-09":    ({}, {}, "concentration_blocked_adds"),
    "G-16":    ({}, {}, "sector_blocked_adds"),
    "G-20":    (dict(deterioration=_WATCH), {}, "deterioration_blocked_adds"),
    "G-24":    ({}, {"BAD": {"days_since_last_buy": ADD_WINNER_COOLDOWN_DAYS - 1}}, "cooldown_adds"),
    "G-25":    (dict(cluster_add_blocks=_cab("BAD")), {}, "cluster_blocked_adds"),
    "G-26":    (dict(pair_add_blocks=_pab("BAD")), {}, "pair_blocked_adds"),
}


def _gate_run(gate, tone):
    kw, held_extra, _ = _GATE_CASES[gate]
    held = {"CLEAN": {"stop": 90.0, "entry_lo": 99.0, "entry_hi": 101.0}, **held_extra}
    return _grow_today(_gate_book(gate), None, [], held, _TODAY, 100_000.0,
                       {"tone": tone}, **kw)


@pytest.mark.parametrize("gate", list(_GATE_CASES))
def test_each_add_gate_blocks_on_bull_and_is_excluded_from_adds_paused(gate):
    bucket = _GATE_CASES[gate][2]
    bull = _gate_run(gate, "bull")
    assert _tk(bull["add_positions"]) == ["CLEAN"]            # firing control
    assert "BAD" not in _tk(bull["add_positions"])
    if bucket is not None:
        item = find_item(bull[bucket], "BAD")
        assert item is not None, (gate, bucket)
        if gate in ("G-04", "G-09"):
            assert item["gate_id"] == gate
    flat = _gate_run(gate, "flat")
    assert _tk(flat["adds_paused"]) == ["CLEAN"], gate        # control fires, BAD held out


# ── B3: held names never enter the new-pick lane ────────────────────────────

def _held_book():
    return make_port_df([
        {"ticker": "HELDS", "weight": 5.0, "sector": "Healthcare"},
        {"ticker": "HELDM", "weight": 5.0, "sector": "Utilities"},
    ])


def test_held_scanner_and_mover_names_never_become_picks_or_leads():
    rows = [{"ticker": "HELDS", "score": 90, "sector": "Healthcare"},
            {"ticker": "FREE", "score": 89, "sector": "Energy"}]
    movers = [{"ticker": "HELDM", "price": 100.0, "sector": "Utilities", "score": 50.0,
               "composite_score": 90.0, "day_change": 6.0},
              {"ticker": "FREEM", "price": 100.0, "sector": "Financials", "score": 50.0,
               "composite_score": 85.0, "day_change": 6.0}]
    comps = {t: _bundle(80.0) for t in ("HELDS", "FREE", "HELDM", "FREEM")}
    bull = _run(rows, comps, port_df=_held_book(), movers=movers)
    assert {"FREE", "FREEM"} <= set(_tk(bull["new_picks"]))     # unheld controls fire
    # a held name must be in NO bucket at all (not merely "not a pick": the
    # mutation that drops the held check leaves it in news_skipped)
    for key in ("new_picks", "overflow_picks", "unevaluated_leads") + _HELD_OUT:
        assert not ({"HELDS", "HELDM"} & set(_tk(bull[key]))), key
    bear = _run(rows, comps, tone="bear", port_df=_held_book(), movers=movers)
    assert "FREE" in _tk(bear["unevaluated_leads"])              # control fires
    assert "HELDS" not in _tk(bear["unevaluated_leads"])
    assert "HELDS" not in _tk(bear["composite_unavailable"])


# ── review follow-ups: caution leg + gate precedence ────────────────────────

def _neg_news(ticker, n=3):
    from stock_analyzer.constants import NEWS_SENTIMENT_NEGATIVE
    c = NEWS_SENTIMENT_NEGATIVE - 0.3
    return [{"ticker": ticker, "title": f"{ticker} slides on probe {i}",
             "compound": c, "label": "Negative"} for i in range(n)]


@pytest.mark.parametrize("days", [3, 20])
def test_flat_day_held_non_caution_verdict_never_gets_clears_when(days):
    # composite 80 clears the Buy bar, so ONLY the verdict=="caution" leg can
    # withhold the hint (negative news makes it conflicted/mixed, not caution).
    rows = [{"ticker": "SPOT", "score": 90, "sector": "Telecom"},
            {"ticker": "OKAY", "score": 91, "sector": "Energy"}]
    g = _grow_today(_held(), _scanner_df(rows), _neg_news("SPOT"), {}, _TODAY, 100_000.0,
                    {"tone": "flat"}, composites={"SPOT": _bundle(80.0), "OKAY": _bundle(80.0)},
                    earnings_lookup={"SPOT": (_TODAY + timedelta(days=days)).isoformat()})
    assert "OKAY" in _tk(g["new_picks"])                    # control
    spot = find_item(g["flat_day_held"], "SPOT")
    assert spot is not None and spot["verdict"] != "caution"
    assert spot["clears_when"] is None and spot["earnings_date"] is None


def test_gate_precedence_g04_beats_g09():
    bad = _winner_row(ticker="BAD", weight=SINGLE_NAME_CEILING, sector="Financials")
    fill = [{"ticker": f"F{i}", "weight": 1.0, "sector": "Insurance"} for i in range(12)]
    g = _grow_today(make_port_df([bad] + fill), None, [], {}, _TODAY, 100_000.0, {"tone": "bull"})
    item = find_item(g["concentration_blocked_adds"], "BAD")
    assert item is not None and item["gate_id"] == "G-04"   # (drift floor < weight too)


def test_gate_precedence_g16_beats_g01():
    bad = _winner_row(ticker="BAD", weight=SINGLE_NAME_CEILING - 1, sector="Energy")
    fill = [{"ticker": f"F{i}", "weight": SINGLE_NAME_CEILING - 1, "sector": "Energy"}
            for i in range(2)]
    risk = [{"priority": "HIGH", "type": "beta", "title": "Beta rec",
             "root_tickers": [{"ticker": "BAD"}]}]
    g = _grow_today(make_port_df([bad] + fill), None, [], {}, _TODAY, 100_000.0,
                    {"tone": "bull"}, risk_recs=risk)
    assert find_item(g["sector_blocked_adds"], "BAD") is not None
    assert find_item(g["risk_blocked_adds"], "BAD") is None
