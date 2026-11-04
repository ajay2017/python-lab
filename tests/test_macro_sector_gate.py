"""C2 of docs/plans/macro-sector-gate.md: `macro_sector_gate.decide`, the
`check_macro` / `macro_hold` mappers, the Watchlist / Analysis / Grow Today
wiring, and the unknown-sector hold's wording and timing."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from stock_analyzer import entry_eligibility as ee
from stock_analyzer import macro_sector_gate as msg
from stock_analyzer import watchlist_advisor
from stock_analyzer.constants import (
    COMPOSITE_BUY, MACRO_IMMINENT_DAYS, UNCLASSIFIED_SECTOR,
)
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.macro_calendar import HIGH
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 11, 0)
FLAT = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "live", "as_of": None}
NFP_REASON = "Non-Farm Payrolls (2026-11-06) — 3d away"
NFP_MAP = {"sectors": {"Financials", "Industrials"},
           "reasons": {"Financials": NFP_REASON, "Industrials": "Other (x) — 3d away"}}
EMPTY_MAP = {"sectors": set(), "reasons": {}}


def _nfp(days=MACRO_IMMINENT_DAYS, **kw):
    d = (TODAY + timedelta(days=days)).isoformat()
    return {"impact": HIGH, "date": d, "category": "Employment",
            "event": "Non-Farm Payrolls", **kw}


# ═══════════════════════════════ decide ════════════════════════════════════
def test_decide_unchecked_states():
    d = msg.decide("Financials", None)
    assert d["state"] == "unchecked" and "calendar unavailable" in d["reason"]
    for bad in ("x", [], {}, {"sectors": None}, 5):
        d = msg.decide("Financials", bad)
        assert d["state"] == "unchecked" and "calendar unreadable" in d["reason"]


def test_decide_membership_blocks_curated_and_raw_provider_label():
    assert msg.decide("Financials", NFP_MAP)["state"] == "blocked"
    d = msg.decide("Financial Services", NFP_MAP)       # the 11-03 ENVA case
    assert d["state"] == "blocked" and d["event_reason"] == NFP_REASON
    assert msg.decide("  financial services ", NFP_MAP)["state"] == "blocked"


def test_decide_a_raw_keyed_label_still_blocks_even_if_its_translation_is_clear():
    # Translation may only ever ADD a block: a raw provider / bucket label keyed
    # into the impact map blocks on its own, whatever it translates to.
    bm = {"sectors": {"Financials & Fintech"}, "reasons": {"Financials & Fintech": "x"}}
    d = msg.decide("Financials & Fintech", bm)
    assert d["state"] == "blocked" and d["event_reason"] == "x"


def test_impact_map_keys_are_curated_labels_only():
    # decide() translates before the membership test; a non-curated key would
    # rely on the raw fallback alone. Keep the map on the curated vocabulary.
    from stock_analyzer.macro_calendar import _SECTOR_IMPACT
    from stock_analyzer.sector_vocab import CURATED_SECTORS
    for cat, row in _SECTOR_IMPACT.items():
        extra = set(row) - set(CURATED_SECTORS) - {"__ALL__"}
        assert not extra, f"{cat}: non-curated impact-map keys {sorted(extra)}"


def test_decide_blocked_with_empty_reason_is_still_blocked_with_generic_text():
    for reasons in ({}, {"Financials": ""}, {"Financials": None}, None):
        d = msg.decide("Financials", {"sectors": {"Financials"}, "reasons": reasons})
        assert d["state"] == "blocked"
        assert d["event_reason"] == "imminent HIGH-impact macro event"


def test_decide_all_sectors_blocks_everything_even_blank():
    bm = {"sectors": {"__ALL__"}, "reasons": {"__ALL__": "FOMC (x) — 0d away"}}
    for s in ("Technology", None, "", "Other", "Energy"):
        d = msg.decide(s, bm)
        assert d["state"] == "blocked" and d["event_reason"] == "FOMC (x) — 0d away"
    assert msg.decide("x", {"sectors": {"__ALL__"}, "reasons": {}})["event_reason"]


def test_decide_curated_not_in_set_is_clear():
    assert msg.decide("Energy", NFP_MAP)["state"] == "clear"
    # a translated label whose target is not in the set is clear too
    assert msg.decide("Health Care", NFP_MAP)["state"] == "clear"


@pytest.mark.parametrize("sector", [None, "", "   ", "Other", "Watchlist", "nan"])
def test_decide_no_label_is_unknown_with_no_label_wording(sector):
    d = msg.decide(sector, NFP_MAP)
    assert d["state"] == "unknown" and d["label"] is None
    assert "no sector label from the data provider" in d["reason"]
    assert d["event_reason"] == NFP_REASON or d["event_reason"] == "Other (x) — 3d away"


def test_decide_technology_is_unknown_and_names_the_label_and_event():
    d = msg.decide("Technology", NFP_MAP)
    assert d["state"] == "unknown" and d["label"] == "Technology"
    # alphabetically-first blocked sector's reason (Financials < Industrials)
    assert d["event_reason"] == NFP_REASON
    assert d["reason"] == ("sector unknown — “Technology” isn't on the app's sector map, "
                           f"so the macro-event check couldn't run while {NFP_REASON}")
    for banned in ("spans several sectors", "sector ceiling couldn't run"):
        assert banned not in d["reason"]


def test_decide_unknown_without_any_reason_text_names_the_event_generically():
    d = msg.decide("Technology", {"sectors": {"Financials"}, "reasons": {}})
    assert d["state"] == "unknown"
    assert "while an imminent HIGH-impact macro event" in d["reason"]


def test_decide_unknown_with_empty_set_is_clear_with_no_note():
    for s in ("Technology", None, "", "Other"):
        d = msg.decide(s, EMPTY_MAP)
        assert d == {"state": "clear", "reason": None, "event_reason": None, "label": None}


def test_decide_etf_non_empty_set_is_unknown_with_etf_wording():
    d = msg.decide("Healthcare", NFP_MAP, is_etf=True)
    assert d["state"] == "unknown"
    assert d["reason"] == ("an ETF has no single sector in the app's map, so it's held while "
                           f"{NFP_REASON} (the same rule Grow Today uses for ETF picks)")
    assert msg.decide("Healthcare", EMPTY_MAP, is_etf=True)["state"] == "clear"
    # membership still comes first
    assert msg.decide("Financials", NFP_MAP, is_etf=True)["state"] == "blocked"
    assert msg.decide(None, {"sectors": {"__ALL__"}, "reasons": {}}, is_etf=True)["state"] == "blocked"


def test_unknown_clears_when_wording():
    assert msg.unknown_clears_when() == (
        "the release is out, or the ticker gets a sector on the app's curated "
        "map that the event doesn't affect")
    assert msg.unknown_clears_when(is_etf=True) == "the release is out"


def test_sector_unknown_banner_words_each_basis_truthfully():
    assert msg.sector_unknown_banner(None) is None and msg.sector_unknown_banner([]) is None
    only_other = msg.sector_unknown_banner([{"ticker": "A"}, {"ticker": "B", "basis": "unclassified"}])
    assert only_other == ("🧭 Sector unknown — 2 pick(s) held back: the sector ceiling and macro "
                          "sector checks couldn't run, so these aren't recommended today: A, B")
    only_macro = msg.sector_unknown_banner([{"ticker": "T", "basis": "macro_unknown"}])
    assert "macro-event check couldn't run on their sector label" in only_macro
    assert "sector ceiling" not in only_macro
    mixed = msg.sector_unknown_banner([{"ticker": "A", "basis": "unclassified"},
                                       {"ticker": "T", "basis": "macro_unknown"}])
    assert "A (the sector ceiling and macro sector checks couldn't run)" in mixed
    assert "T (the macro-event check couldn't run on their sector label)" in mixed


# ═══════════════════ check_macro / macro_hold mappers ═══════════════════════
def test_check_macro_in_set_is_the_g28_hold():
    r = ee.check_macro({"macro_map": NFP_MAP, "sector": "Financials",
                        "gate_ids": {"macro": "G-28"}})
    assert r == {"kind": "hold", "check": "macro", "gate_id": "G-28",
                 "reason": f"macro event · {NFP_REASON}", "clears_when": "the release is out",
                 "gate_value": None, "gate_threshold": MACRO_IMMINENT_DAYS}


def test_check_macro_raw_financial_services_is_a_macro_hold():
    r = ee.check_macro({"macro_map": NFP_MAP, "sector": "Financial Services",
                        "gate_ids": {"macro": "G-28"}})
    assert r["check"] == "macro" and r["gate_id"] == "G-28"


def test_check_macro_curated_not_in_set_passes():
    assert ee.check_macro({"macro_map": NFP_MAP, "sector": "Energy"}) is None


def test_check_macro_technology_is_the_unknown_sector_hold_without_a_gate_id():
    r = ee.check_macro({"macro_map": NFP_MAP, "sector": "Technology",
                        "gate_ids": {"macro": "G-28"}})
    assert r["kind"] == "hold" and r["check"] == "macro_unknown_sector"
    assert r["gate_id"] is None and r["gate_value"] is None
    assert r["gate_threshold"] == MACRO_IMMINENT_DAYS
    assert "“Technology”" in r["reason"] and NFP_REASON in r["reason"]
    assert "curated map" in r["clears_when"]


@pytest.mark.parametrize("sector", [None, "", "Other"])
def test_check_macro_no_label_with_a_non_empty_set_is_an_unknown_hold(sector):
    r = ee.check_macro({"macro_map": NFP_MAP, "sector": sector})
    assert r["check"] == "macro_unknown_sector" and "no sector label" in r["reason"]


@pytest.mark.parametrize("sector", [None, "", "Other", "Technology"])
def test_check_macro_unknown_with_empty_set_passes_without_a_note(sector):
    assert ee.check_macro({"macro_map": EMPTY_MAP, "sector": sector}) is None
    r = ee.entry_check(sector=sector, macro_map=EMPTY_MAP, bundle={})
    assert not [u for u in r["unchecked"] if u["check"] == "macro"] and r["eligible"]


def test_check_macro_all_sectors_and_unchecked_notes_unchanged():
    allm = {"sectors": {"__ALL__"}, "reasons": {"__ALL__": "FOMC"}}
    assert ee.check_macro({"macro_map": allm, "sector": None})["check"] == "macro"
    assert ee.check_macro({"macro_map": None, "sector": "Energy"}) == {
        "kind": "unchecked", "check": "macro",
        "why": "macro calendar unavailable — the macro-event check couldn't run"}
    assert ee.check_macro({"macro_map": {"reasons": {}}, "sector": "Energy"}) == {
        "kind": "unchecked", "check": "macro",
        "why": "macro calendar unreadable — the macro-event check couldn't run"}


def test_check_macro_etf_from_the_bundle_asset_type():
    r = ee.check_macro({"macro_map": NFP_MAP, "sector": "Healthcare",
                        "bundle": {"asset_type": "etf"}})
    assert r["check"] == "macro_unknown_sector"
    assert r["reason"].startswith("an ETF has no single sector")
    assert r["clears_when"] == "the release is out"
    # a stock bundle with the same curated, un-blocked sector passes
    assert ee.check_macro({"macro_map": NFP_MAP, "sector": "Healthcare",
                           "bundle": {"asset_type": "equity"}}) is None
    assert ee.check_macro({"macro_map": NFP_MAP, "sector": "Healthcare", "bundle": None}) is None


def test_macro_hold_returns_a_reason_only_when_blocked():
    assert ee.macro_hold("Financial Services", NFP_MAP) == NFP_REASON
    assert ee.macro_hold("Technology", NFP_MAP) is None       # unknown is not a G-07 hold
    assert ee.macro_hold("Energy", NFP_MAP) is None
    assert ee.macro_hold("Technology", None) is None and ee.macro_hold("Technology", {}) is None
    # fail-closed on membership: an empty reason is still a truthy hold
    assert ee.macro_hold("Financials", {"sectors": {"Financials"}, "reasons": {"Financials": ""}})


def test_held_kind_label_and_ledger_kind():
    assert ee.HELD_KIND_LABELS["macro_unknown_sector"] == "sector unknown (macro check)"
    assert ee.held_label({"hold_kind": "macro_unknown_sector"}) == "Held: sector unknown (macro check)"
    assert "macro_unknown_sector" not in watchlist_advisor._HOLD_SUPPRESSION_KIND


# ══════════════════════════ Watchlist path ═════════════════════════════════
def _data():
    return {"total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
            "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
            "targets": {"base": 130.0}, "earnings": None}


def _wl(sector, events, now=NOW, classified=True):
    run = ee.watchlist_run_ctx(FLAT, events, TODAY, now)
    ctx = ee.watchlist_ticker_ctx(run, sector=sector, classified=classified, news_items=[])
    ctx["news"] = {"compound": 0.1, "n": 3, "headline": "fine"}
    return build_watchlist_recommendation("XYZ", _data(), entry_ctx=ctx)


def test_enva_regression_financial_services_during_nfp_is_entry_held_g28():
    r = _wl("Financial Services", [_nfp()])
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "macro"
    assert r["suppression_kind"] == "entry_macro"       # the G-28 ledger / cron row
    assert r["holds"][0]["gate_id"] == "G-28"
    assert NFP_REASON.split(" (")[0] in r["hold_reason"]


def test_technology_during_nfp_is_held_as_unknown_sector_with_no_ledger_row():
    r = _wl("Technology", [_nfp()])
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "macro_unknown_sector"
    assert r.get("suppression_kind") is None
    assert r["holds"][0]["gate_id"] is None
    assert ee.held_label(r) == "Held: sector unknown (macro check)"
    assert "“Technology”" in r["hold_reason"]
    assert "curated map" in r["clears_when"]


def test_cron_ledger_writes_g28_for_a_translated_name_and_nothing_for_unknown():
    from stock_analyzer.gate_ledger import build_watchlist_suppression_rows
    enva = _wl("Financial Services", [_nfp()])
    enva["ticker"] = "ENVA"
    tech = _wl("Technology", [_nfp()])
    tech["ticker"] = "TECHX"
    rows = build_watchlist_suppression_rows([enva, tech], rec_date=TODAY, source="cron")
    assert [(r["ticker"], r["gate_id"]) for r in rows] == [("ENVA", "G-28")]


def test_unknown_sector_with_no_event_is_a_normal_entry():
    assert _wl("Technology", [])["action"] == "ENTER_NOW"
    assert _wl(None, [], classified=False)["action"] == "ENTER_NOW"


def test_unclassified_candidate_during_an_event_is_held_not_a_note():
    r = _wl("Technology", [_nfp()], classified=False)      # sector passed as None
    assert r["hold_kind"] == "macro_unknown_sector" and "no sector label" in r["hold_reason"]


# timing on the unknown branch, through the real block-map builder
def test_unknown_branch_day3_held_day4_not():
    assert msg.decide("Technology", ee.macro_block_map([_nfp(MACRO_IMMINENT_DAYS)], TODAY, NOW))["state"] == "unknown"
    assert msg.decide("Technology", ee.macro_block_map([_nfp(MACRO_IMMINENT_DAYS + 1)], TODAY, NOW))["state"] == "clear"


def test_unknown_branch_same_day_non_fed_release_time():
    ev = _nfp(0, time_et="08:30")
    before = ee.macro_block_map([ev], TODAY, datetime(2026, 11, 3, 7, 0))
    at = ee.macro_block_map([ev], TODAY, datetime(2026, 11, 3, 8, 30))
    after = ee.macro_block_map([ev], TODAY, datetime(2026, 11, 3, 9, 0))
    assert msg.decide("Technology", before)["state"] == "unknown"
    assert msg.decide("Technology", at)["state"] == "clear"
    assert msg.decide("Technology", after)["state"] == "clear"
    assert msg.decide("Technology", ee.macro_block_map([_nfp(0, released=True)], TODAY, NOW))["state"] == "clear"
    # no clock: the gate stays on (fail safe)
    assert msg.decide("Technology", ee.macro_block_map([ev], TODAY, None))["state"] == "unknown"


def test_unknown_branch_same_day_fomc_is_held_all_day():
    fomc = {"impact": HIGH, "date": TODAY.isoformat(), "category": "Fed Policy",
            "event": "FOMC Rate Decision", "time_et": "14:00"}
    for now in (datetime(2026, 11, 3, 9, 0), datetime(2026, 11, 3, 15, 0)):
        bm = ee.macro_block_map([fomc], TODAY, now)
        d = msg.decide("Technology", bm)
        assert d["state"] == "blocked" and "FOMC" in d["event_reason"]


# ══════════════════════════ Analysis path ══════════════════════════════════
def _analysis(sector, events=None):
    bm = ee.macro_block_map([_nfp()] if events is None else events, TODAY, NOW)
    chk = ee.entry_check(bundle={}, tone_state=FLAT, sector=sector, macro_map=bm,
                         news={"compound": 0.1, "n": 3, "headline": "h"})
    return ee.analysis_entry_hold(basis="equity", label="Buy", held=False, entry=chk)


def test_analysis_macro_tile_and_banner_for_the_unknown_hold():
    dec = _analysis("Technology")
    assert dec["offer_size"] is False and dec["hold"]["check"] == "macro_unknown_sector"
    macro = next(t for t in ee.analysis_gate_tiles(dec) if t["label"] == "Macro")
    assert macro["state"] is False and "“Technology”" in macro["detail"]
    banner = ee.analysis_hold_banner(dec)
    assert banner is not None and banner["title"]
    assert "“Technology”" in banner["body"] or "“Technology”" in str(banner)


def test_analysis_macro_tile_for_the_translated_label_is_the_g28_hold():
    dec = _analysis("Financial Services")
    assert dec["hold"]["check"] == "macro"
    macro = next(t for t in ee.analysis_gate_tiles(dec) if t["label"] == "Macro")
    assert macro["state"] is False and "macro event" in macro["detail"]


def test_analysis_macro_tile_passes_for_unknown_sector_with_no_event():
    dec = _analysis("Technology", events=[])
    assert dec["offer_size"] is True
    macro = next(t for t in ee.analysis_gate_tiles(dec) if t["label"] == "Macro")
    assert macro["state"] is True


# ══════════════════════════ Grow Today ═════════════════════════════════════
GT = date(2026, 7, 27)


def _scanner(rows):
    return pd.DataFrame([
        {"Ticker": r["ticker"], "Score": r.get("score", 70.0), "Price": 50.0,
         "Signal": "Buy", "Sector": r.get("sector", "Watchlist"), "RSI": 60.0,
         "1M Momentum": 5.0, "Trend": "Up"} for r in rows])


def _composite(sector, total=COMPOSITE_BUY + 10, **kw):
    c = {"total": total, "rec": {"label": "Buy"}, "fundamentals_available": True,
         "val_available": True}
    if sector is not None:
        c["sector"] = sector
    c.update(kw)
    return c


def _grow(sector, events, row_sector="Watchlist", total=COMPOSITE_BUY + 10, **ckw):
    port = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Healthcare"}])
    scanner = _scanner([{"ticker": "NEW", "sector": row_sector, "score": COMPOSITE_BUY + 10}])
    ev = None if events is None else [
        {"impact": HIGH, "date": (GT + timedelta(days=1)).isoformat(),
         "category": c, "event": c} for c in events]
    return _grow_today(port, scanner, [], {}, GT, 100_000.0, {"tone": "bull"},
                       composites={"NEW": _composite(sector, total, **ckw)}, macro_events=ev)


def test_grow_today_financial_services_mover_during_nfp_is_g07_blocked():
    grow = _grow("Financial Services", ["Employment"])
    item = find_item(grow["macro_blocked_picks"], "NEW")
    assert item is not None and item["gate_id"] == "G-07"
    assert find_item(grow["new_picks"], "NEW") is None
    assert find_item(grow["sector_unknown_picks"], "NEW") is None


def test_grow_today_technology_during_nfp_abstains_as_macro_unknown_not_ledgered():
    grow = _grow("Technology", ["Employment"])
    item = find_item(grow["sector_unknown_picks"], "NEW")
    assert item is not None and item["basis"] == "macro_unknown"
    assert "gate_id" not in item
    assert "“Technology”" in item["reason"] and "macro-event check couldn't run" in item["reason"]
    assert find_item(grow["new_picks"], "NEW") is None
    assert find_item(grow["macro_blocked_picks"], "NEW") is None


def test_grow_today_composite_failure_still_lands_in_its_own_bucket_first():
    grow = _grow("Technology", ["Employment"], total=COMPOSITE_BUY - 10, rec=None)
    assert find_item(grow["sector_unknown_picks"], "NEW") is None
    assert find_item(grow["composite_skipped"], "NEW") is not None or \
        find_item(grow["composite_unavailable"], "NEW") is not None


def test_grow_today_no_event_unknown_sector_can_still_be_a_pick():
    for events in ([], None):
        grow = _grow("Technology", events)
        assert find_item(grow["sector_unknown_picks"], "NEW") is None
    grow = _grow("Technology", [])
    assert find_item(grow["new_picks"], "NEW") is not None


def test_grow_today_other_is_unchanged_and_basis_unclassified():
    for events in ([], ["Employment"]):
        grow = _grow(None, events)
        item = find_item(grow["sector_unknown_picks"], "NEW")
        assert item is not None and item["sector"] == UNCLASSIFIED_SECTOR
        assert item["basis"] == "unclassified"
        assert item["reason"] == ("Sector unknown — the sector ceiling and macro sector "
                                  "checks couldn't run")


def test_grow_dropoff_reads_the_macro_unknown_item_reason():
    from stock_analyzer.grow_dropoff import derive_dropoffs
    grow = _grow("Technology", ["Employment"])
    item = find_item(grow["sector_unknown_picks"], "NEW")
    buckets = {k: [] for k in ("composite_skipped", "sector_blocked_picks",
                               "macro_blocked_picks", "composite_unavailable")}
    buckets["sector_unknown_picks"] = [item]
    surfaced = [{"ticker": "NEW", "first_seen_at": "2026-07-27T13:45:00+00:00",
                 "composite_at_surface": 75.0}]
    out = derive_dropoffs(surfaced, set(), buckets, None, set())
    assert out[0]["reason_code"] == "sector_unknown"
    assert "macro-event check couldn't run" in out[0]["reason_text"]
