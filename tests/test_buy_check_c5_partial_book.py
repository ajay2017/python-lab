"""Buy-check C5, chunk A1: a partly priced book holds new buys in the unpriced
holding's sector (docs/plans/entry-fail-closed-c5-spec.md; owner QA1-QA4).

Covers `buy_check_state.unpriced_holdings` / `fit_state["unpriced"]`, the new gate
module `sector_unpriced` (decide + wording), the Watchlist advisor's `fit_unpriced`
abstention and its binding order, Grow Today's `fit_unpriced_picks` bucket and the
add-lane `FIT_UNPRICED` hold, the cron wiring (diag, `fit_state`, checks held, drop
line, email lines) and both email renderers. Every boundary is read from
`constants.py`, never a literal. The app.py wiring is chunk A2 and not tested here.
"""
import json
import math
import re
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from stock_analyzer import (
    buy_check_state as bcs,
    gate_ledger,
    grow_dropoff,
    sector_unpriced as su,
    sizing_path,
)
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import notify
from stock_analyzer.constants import (
    BRIEF_AUTO_REFRESH_MINUTES,
    PORTFOLIO_BETA_CEILING,
    SECTOR_CEILING,
    TICKER_BETA_CRITICAL,
)
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.watchlist_advisor import (
    _portfolio_risk_gate,
    build_watchlist_recommendation,
)
from tests.conftest import make_port_df
from tests.test_beta_gate_grow_today import (
    _add_scn,
    _etf_scn,
    _pick_scn,
    _run,
    _tickers,
    _with_beta,
)
from tests.test_daily_briefing import _scanner_df
from tests.test_grow_today_golden import _PCT, _TODAY, _adds_scn, _bundle, _scn
from tests.test_watchlist_entry_cron import (
    FLAT_CTX,
    SEMIS,
    _enter_card,
    _morning_payload,
    _ok_ctx,
    _scan,
    _wle,
    _wle_payload,
)

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
ALL = bcs.UNPRICED_ALL
HIGH_BOOK = PORTFOLIO_BETA_CEILING + 0.2
HIGH_TICK = TICKER_BETA_CRITICAL + 0.2


# ═════════════════ 1. buy_check_state.unpriced_holdings ══════════════════════
def _frame(rows):
    """rows: [(ticker, price)] -> a frame with Ticker / Price."""
    return pd.DataFrame([{"Ticker": t, "Price": p} for t, p in rows])


def _h(ticker, shares=1.0, avg=50.0):
    return {"Ticker": ticker, "Shares": shares, "Avg Cost ($)": avg}


def test_fully_priced_book_has_no_unpriced_holdings():
    assert bcs.unpriced_holdings([_h("MSFT"), _h("AAPL")],
                                 _frame([("MSFT", 10.0), ("AAPL", 20.0)])) == {}


def test_a_missing_curated_holding_reads_its_curated_sector():
    out = bcs.unpriced_holdings([_h("MSFT"), _h("AMD")], _frame([("MSFT", 10.0)]))
    assert out == {"AMD": "Semiconductors"}


def test_an_uncurated_or_etf_holding_reads_all_sectors():
    out = bcs.unpriced_holdings([_h("MSFT"), _h("ZZZZQX"), _h("VOO")], _frame([("MSFT", 10.0)]))
    assert out == {"ZZZZQX": ALL, "VOO": ALL}


@pytest.mark.parametrize("px", [0, 0.0, -1.0, float("nan"), float("inf"), None, "x"])
def test_a_frame_row_without_a_usable_price_is_still_unpriced(px):
    out = bcs.unpriced_holdings([_h("AMD")], _frame([("AMD", px)]))
    assert out == {"AMD": "Semiconductors"}


def test_a_half_share_holding_with_a_real_price_is_priced():
    assert bcs.unpriced_holdings([_h("AMD", shares=0.5)], _frame([("AMD", 0.01)])) == {}


def test_zero_or_negative_shares_are_skipped_and_unreadable_shares_counted():
    rows = [_h("AMD", shares=0), _h("NVDA", shares=-3), _h("MSFT", shares="x"),
            _h("AAPL", shares=float("nan")), {"Ticker": "XOM"}]
    out = bcs.unpriced_holdings(rows, _frame([("JPM", 1.0)]))
    assert set(out) == {"MSFT", "AAPL", "XOM"}          # the first two are not held


def test_a_zero_or_invalid_avg_cost_holding_counts_as_unpriced_qa4():
    rows = [_h("AMD", avg=0), _h("NVDA", avg=float("nan")), _h("MSFT", avg=None)]
    out = bcs.unpriced_holdings(rows, _frame([("JPM", 1.0)]))
    assert set(out) == {"AMD", "NVDA", "MSFT"}


def test_an_empty_row_blank_tickers_and_lowercase_keys():
    assert bcs.unpriced_holdings([{}], _frame([("MSFT", 1.0)])) == {}
    assert bcs.unpriced_holdings([_h("  "), _h(None)], _frame([("MSFT", 1.0)])) == {}
    out = bcs.unpriced_holdings([{"ticker": " amd ", "shares": 2}], _frame([("MSFT", 1.0)]))
    assert out == {"AMD": "Semiconductors"}


def test_ticker_strings_and_a_dataframe_of_holdings():
    f = _frame([("MSFT", 1.0)])
    assert bcs.unpriced_holdings(["msft", "AMD"], f) == {"AMD": "Semiconductors"}
    assert bcs.unpriced_holdings(pd.DataFrame([_h("MSFT"), _h("AMD")]), f) == {
        "AMD": "Semiconductors"}


def test_a_frame_without_a_price_column_counts_every_present_ticker_priced():
    f = pd.DataFrame({"Ticker": ["MSFT"]})
    assert bcs.unpriced_holdings([_h("MSFT"), _h("AMD")], f) == {"AMD": "Semiconductors"}


def test_frame_ticker_case_and_whitespace_are_normalised():
    f = _frame([(" msft ", 10.0)])
    assert bcs.unpriced_holdings([_h("MSFT")], f) == {}


@pytest.mark.parametrize("rows, frame", [
    (None, _frame([("MSFT", 1.0)])),
    (5, _frame([("MSFT", 1.0)])),
    ("MSFT", _frame([("MSFT", 1.0)])),
    ([5], _frame([("MSFT", 1.0)])),
    ([_h("MSFT")], None),
    ([_h("MSFT")], "frame"),
    ([_h("MSFT")], pd.DataFrame({"Other": [1]})),     # no Ticker column
])
def test_garbage_reads_none_never_raises(rows, frame):
    assert bcs.unpriced_holdings(rows, frame) is None


def test_unpriced_holdings_does_not_mutate_its_inputs():
    rows = [_h("MSFT"), _h("AMD")]
    snap_rows = [dict(r) for r in rows]
    f = _frame([("MSFT", 10.0)])
    f.attrs["k"] = 1
    snap_f = f.copy()
    out = bcs.unpriced_holdings(rows, f)
    out["ZZ"] = "x"
    assert rows == snap_rows and f.equals(snap_f) and f.attrs == {"k": 1}
    assert "ZZ" not in bcs.unpriced_holdings(rows, f)


def test_fit_state_carries_unpriced_only_when_ready():
    f = _frame([("MSFT", 10.0)])
    ready = bcs.fit_state([_h("MSFT"), _h("AMD")], f)
    assert ready["state"] == "ready" and ready["unpriced"] == {"AMD": "Semiconductors"}
    assert ready["unpriced_sectors"] == {"Semiconductors"}
    for st in (bcs.fit_state([], f), bcs.fit_state(None, f), bcs.fit_state([_h("MSFT")], None)):
        assert st["state"] != "ready"
        assert st["unpriced"] is None and st["unpriced_sectors"] is None


def test_buy_check_state_stays_pure():
    src = (ROOT / "stock_analyzer" / "buy_check_state.py").read_text(encoding="utf-8")
    for banned in ("import streamlit", "from stock_analyzer import db", "market_time",
                   "utcnow", "date.today", "datetime.now", "time.time"):
        assert banned not in src, banned


# ═════════════════ 2. sector_unpriced ════════════════════════════════════════
def test_decide_same_sector_holds_other_sector_does_not():
    m = {"AMD": "Semiconductors"}
    d = su.decide("Semiconductors", m, lane="watchlist")
    assert d["check"] == "fit_unpriced" and d["all"] is False
    assert d["sector"] == "Semiconductors" and d["tickers"] == ["AMD"]
    assert d["gate_id"] is None and d["gate_value"] is None and d["gate_threshold"] is None
    assert su.decide("Energy", m, lane="watchlist") is None


def test_decide_wording_one_several_and_all_read_the_cap_from_constants():
    cap = f"{SECTOR_CEILING:.0f}%"
    one = su.decide("Semiconductors", {"AMD": "Semiconductors"}, lane="watchlist")
    assert one["reason"] == (
        f"AMD couldn't be priced, so it is missing from your Semiconductors weight and "
        f"the {cap} sector cap can't be checked for this name")
    two = su.decide("Semiconductors", {"AMD": "Semiconductors", "AVGO": "Semiconductors"},
                    lane="watchlist")
    assert two["reason"] == (
        f"AMD and AVGO couldn't be priced, so they are missing from your Semiconductors "
        f"weight and the {cap} sector cap can't be checked for this name")
    three = su.decide("Semiconductors", {"AMD": "Semiconductors", "AVGO": "Semiconductors",
                                          "NVDA": "Semiconductors"}, lane="watchlist")
    assert three["reason"].startswith("AMD, AVGO and NVDA couldn't be priced")
    allr = su.decide("Energy", {"VOO": ALL}, lane="watchlist")
    assert allr["all"] is True and allr["tickers"] == ["VOO"]
    assert allr["reason"] == (
        f"VOO couldn't be priced and its sector isn't in the app's sector map, so any "
        f"sector's weight may read low and the {cap} sector cap can't be checked")
    allr2 = su.decide("Energy", {"VOO": ALL, "QQQ": ALL}, lane="watchlist")
    assert "their sectors aren't in the app's sector map" in allr2["reason"]


def test_decide_clears_when_wording_by_lane():
    m = {"AMD": "Semiconductors"}
    base = "AMD can be priced again — re-checked at the next scan or page load"
    for lane in ("watchlist", "analysis"):
        assert su.decide("Semiconductors", m, lane=lane)["clears_when"] == base
    for lane in ("pick", "add"):
        assert su.decide("Semiconductors", m, lane=lane)["clears_when"] == (
            base + f" (🏠 Home rebuilds on its own every {BRIEF_AUTO_REFRESH_MINUTES} "
            "minutes in pre-market and market hours)")


def test_decide_all_sectors_holds_every_classified_sector_but_never_an_unclassified_one():
    m = {"VOO": ALL}
    assert su.decide("Energy", m, lane="pick") is not None
    assert su.decide("Semiconductors", m, lane="add") is not None
    for blank in (None, "", "  ", "Other", ALL):
        assert su.decide(blank, m, lane="pick") is None, blank


@pytest.mark.parametrize("m", [None, {}, [], "x", 5, {"": "Energy"}])
def test_decide_empty_or_unreadable_map_holds_nothing(m):
    assert su.decide("Energy", m, lane="watchlist") is None


def test_all_beats_a_same_sector_name_and_the_clear_names_every_blocking_holding():
    # A1 review: the reason names the unknown-sector holding (the "all" wording), but
    # the clear condition lists every holding still blocking this sector (VOO and AMD).
    d = su.decide("Semiconductors", {"AMD": "Semiconductors", "VOO": ALL}, lane="pick")
    assert d["all"] is True and d["tickers"] == ["AMD", "VOO"]
    assert d["reason"].startswith("VOO couldn't be priced and its sector isn't")
    assert d["clears_when"].startswith("AMD and VOO can be priced again")
    line = su.page_line({"AMD": "Semiconductors", "VOO": ALL})
    assert line.endswith("until AMD and VOO can be priced.")
    em = su.email_line({"state": "ready", "unpriced": {"AMD": "Semiconductors", "VOO": ALL}})
    assert em.startswith("Part of your book couldn't be priced this run (AMD, VOO):")


def test_decide_never_raises_on_hostile_input():
    class _Boom:
        def __str__(self):
            raise RuntimeError("boom")
    assert su.decide(_Boom(), {"A": "B"}, lane="pick") is None
    assert su.decide("Energy", {_Boom(): "Energy"}, lane="pick") is None


def test_page_line_wording():
    assert su.page_line({"AMD": "Semiconductors"}) == (
        "⚠️ AMD couldn't be priced, so it is missing from your Semiconductors weight. "
        "Enter Now is held for Semiconductors names until it can be priced.")
    assert su.page_line({"AMD": "Semiconductors", "AVGO": "Semiconductors"}) == (
        "⚠️ AMD and AVGO couldn't be priced, so they are missing from your Semiconductors "
        "weight. Enter Now is held for Semiconductors names until they can be priced.")
    assert su.page_line({"VOO": ALL}) == (
        "⚠️ VOO couldn't be priced and its sector isn't in the app's sector map, so Enter "
        "Now is held for every name the sector cap covers until it can be priced.")
    both = su.page_line({"AMD": "Semiconductors", "XOM": "Energy"})
    assert "Enter Now is held for Energy and Semiconductors names until they can be priced." in both
    for none in (None, {}, [], "x"):
        assert su.page_line(none) is None


def test_email_line_wording_and_only_for_a_ready_state():
    ready = {"state": "ready", "unpriced": {"AMD": "Semiconductors"}}
    assert su.email_line(ready) == (
        "Part of your book couldn't be priced this run (AMD): names in Semiconductors "
        "are held, and the sector check ran on the rest.")
    assert su.email_line({"state": "ready", "unpriced": {"VOO": ALL}}) == (
        "Part of your book couldn't be priced this run (VOO): its sector isn't known, so "
        "every name the sector check covers is held.")
    for bad in (None, {}, "x", {"state": "ready", "unpriced": {}},
                {"state": "ready", "unpriced": None},
                {"state": "unknown", "unpriced": {"AMD": "Semiconductors"}},
                {"state": "flat", "unpriced": {"AMD": "Semiconductors"}}):
        assert su.email_line(bad) is None, bad


def test_analysis_tile():
    assert su.analysis_tile("Semiconductors", {"AMD": "Semiconductors"}) == (
        None, "Weight incomplete (AMD unpriced)")
    assert su.analysis_tile("Energy", {"AMD": "Semiconductors"}) is None
    assert su.analysis_tile("Energy", {"VOO": ALL}) == (None, "Weight incomplete (VOO unpriced)")
    assert su.analysis_tile("Energy", {}) is None


def test_no_string_the_module_builds_has_a_dollar_sign_or_bold_marker():
    m = {"AMD": "Semiconductors", "AVGO": "Semiconductors", "VOO": ALL}
    texts = []
    for lane in ("watchlist", "pick", "add", "analysis"):
        for sec in ("Semiconductors", "Energy"):
            for mm in (m, {"AMD": "Semiconductors"}, {"VOO": ALL}):
                d = su.decide(sec, mm, lane=lane)
                if d:
                    texts += [d["reason"], d["clears_when"]]
    texts += [su.page_line(m), su.page_line({"AMD": "Semiconductors"}),
              su.email_line({"state": "ready", "unpriced": m}),
              su.analysis_tile("Energy", m)[1]]
    assert texts
    for t in texts:
        assert "$" not in t and "**" not in t, t


def test_sector_unpriced_is_pure_and_listed_as_a_gate_file():
    src = (ROOT / "stock_analyzer" / "sector_unpriced.py").read_text(encoding="utf-8")
    for banned in ("import streamlit", "from stock_analyzer import db", "market_time",
                   "utcnow", "date.today", "datetime.now", "time.time"):
        assert banned not in src, banned
    hook = (ROOT / ".claude" / "hooks" / "pre_tool_checks.py").read_text(encoding="utf-8")
    assert '"stock_analyzer/sector_unpriced.py"' in hook


# ═════════════════ 3. Watchlist advisor: fit_unpriced ════════════════════════
def _ctx(unpriced, *, weight=10.0, sector="Semiconductors", beta=1.0, classified=True,
         state="ready"):
    return {
        "fit_state": {"state": state, "unpriced_sectors": set(unpriced.values()),
                      "unpriced": unpriced},
        "portfolio_beta": beta, "sector_weight_pct": weight,
        "sector_of_ticker": sector, "sector_classified": classified,
    }


def _wl_data(**o):
    d = {"total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
         "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
         "targets": {"base": 130.0}, "earnings": None, "risk_metrics": {"beta": 1.0}}
    d.update(o)
    return d


def _rows(rec):
    return gate_ledger.build_watchlist_suppression_rows(
        [rec], rec_date="2026-11-27", source="cron")


def test_gate_holds_a_name_in_the_unpriced_sector_as_a_hard_abstention():
    g = _portfolio_risk_gate(1.0, _ctx({"AMD": "Semiconductors"}))
    assert g["severity"] == "hard" and g["kind"] == "fit_unpriced"
    assert g["gate_value"] is None and g["gate_threshold"] is None
    assert g["reason"].endswith("That is a reason to pause, not a green light.")
    assert "AMD couldn't be priced" in g["reason"]
    assert "$" not in g["reason"] and "**" not in g["reason"]


def test_gate_lets_another_sector_through():
    assert _portfolio_risk_gate(1.0, _ctx({"AMD": "Semiconductors"}, sector="Energy")) is None


def test_gate_flat_book_and_absent_fit_state_hold_nothing():
    flat = {"state": "flat", "unpriced_sectors": None, "unpriced": None}
    assert _portfolio_risk_gate(1.0, {"fit_state": flat, "sector_of_ticker": "Energy"}) is None
    legacy = {"sector_of_ticker": "Semiconductors", "portfolio_beta": 1.0}
    assert _portfolio_risk_gate(1.0, legacy) is None
    # a ready fit_state from before C5 (no "unpriced" key at all) changes nothing
    old = {"fit_state": {"state": "ready", "unpriced_sectors": set()},
           "sector_of_ticker": "Semiconductors", "portfolio_beta": 1.0}
    assert _portfolio_risk_gate(1.0, old) is None


def test_gate_all_sectors_holds_any_classified_name():
    g = _portfolio_risk_gate(1.0, _ctx({"VOO": ALL}, sector="Energy"))
    assert g["kind"] == "fit_unpriced" and "sector map" in g["reason"]


def test_g05_at_the_ceiling_binds_first_with_its_ledger_row():
    rec = build_watchlist_recommendation(
        "XYZ", _wl_data(), portfolio_ctx=_ctx({"AMD": "Semiconductors"},
                                              weight=SECTOR_CEILING))
    assert rec["action"] == "NEAR_ENTRY" and rec["suppression_kind"] == "sector"
    rows = _rows(rec)
    assert [r["gate_id"] for r in rows] == ["G-05"]


def test_just_under_the_ceiling_is_fit_unpriced_with_no_ledger_row():
    rec = build_watchlist_recommendation(
        "XYZ", _wl_data(), portfolio_ctx=_ctx({"AMD": "Semiconductors"},
                                              weight=SECTOR_CEILING - 0.01))
    assert rec["action"] == "NEAR_ENTRY" and rec["suppression_kind"] == "fit_unpriced"
    assert "Sector Weight Incomplete" in rec["title"]
    assert "breach" not in rec["summary"].lower()
    assert "Do not open the position at full size" not in rec["detail"]
    assert "**" not in rec["detail"] and "**" not in rec["summary"]
    assert rec["conditions_missing"] == [
        "Sector weight incomplete — a holding in this sector couldn't be priced"]
    assert _rows(rec) == []


def test_sector_unknown_binds_before_fit_unpriced():
    g = _portfolio_risk_gate(1.0, _ctx({"VOO": ALL}, sector="", classified=False))
    assert g["kind"] == "sector_unknown"


def test_a_real_g06_breach_binds_before_fit_unpriced():
    ctx = _ctx({"AMD": "Semiconductors"}, beta=HIGH_BOOK)
    g = _portfolio_risk_gate(HIGH_TICK, ctx)
    assert g["kind"] == "beta"


def test_fit_unknown_binds_before_fit_unpriced():
    g = _portfolio_risk_gate(1.0, {"fit_state": {"state": "unknown", "unpriced": None},
                                   "sector_of_ticker": "Semiconductors"})
    assert g["kind"] == "fit_unknown"


def test_fit_unpriced_binds_before_beta_unknown():
    # book beta unread + ticker beta over critical would be beta_unknown on its own
    ctx = _ctx({"AMD": "Semiconductors"}, beta=None)
    assert _portfolio_risk_gate(HIGH_TICK, {**ctx, "fit_state": {
        "state": "ready", "unpriced_sectors": set(), "unpriced": {}}})["kind"] == "beta_unknown"
    assert _portfolio_risk_gate(HIGH_TICK, ctx)["kind"] == "fit_unpriced"


def test_the_gate_does_not_mutate_the_unpriced_map():
    m = {"AMD": "Semiconductors"}
    ctx = _ctx(m)
    snap = json.dumps(m)
    _portfolio_risk_gate(1.0, ctx)
    assert json.dumps(m) == snap


# ═════════════════ 4. Grow Today ═════════════════════════════════════════════
def _fit_up(unpriced, port_beta=1.0):
    return {"portfolio_beta": port_beta,
            "fit_state": {"state": "ready", "unpriced_sectors": set(unpriced.values()),
                          "unpriced": unpriced}}


def _ledger(grow):
    return gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)


def test_pick_in_the_unpriced_sector_is_held_unsized_with_no_ledger_row():
    grow = _run(_pick_scn(1.0), _fit_up({"UPX": "Energy"}))
    assert "ZNEWX" not in _tickers(grow["new_picks"])
    assert "ZNEWX" not in _tickers(grow["overflow_picks"])
    item = next(i for i in grow["fit_unpriced_picks"] if i["ticker"] == "ZNEWX")
    assert item["kind"] == "fit_unpriced" and item["sector"] == "Energy"
    assert item["gate_id"] is None and item["counterfactual"] is True
    assert item["gate_value"] is None and item["gate_threshold"] is None
    assert item["unpriced"] == ["UPX"]
    assert "UPX couldn't be priced" in item["reason"]
    assert item["clears_when"].startswith("UPX can be priced again")
    assert "sizing" not in item
    # no ledger row, and no G-32 shadow row for a name that was never sized
    assert [r for r in _ledger(grow) if r["ticker"] == "ZNEWX"] == []
    from stock_analyzer import g32_shadow
    assert [r for r in g32_shadow.build_shadow_rows(grow, _TODAY, "app")
            if r.get("ticker") == "ZNEWX"] == []
    # the other sector is sized as before
    assert "ZCTRL" in _tickers(grow["new_picks"])
    assert grow["fit_unpriced_picks"] and _tickers(grow["fit_unpriced_picks"]) == ["ZNEWX"]


def test_a_mover_in_the_unpriced_sector_is_held_too():
    s = _pick_scn(1.0)
    s["rows"] = []
    s["composites"] = {"ZMOVE": _with_beta(_bundle(80.0), 1.0)}
    s["movers"] = [{"ticker": "ZMOVE", "price": 100.0, "sector": "Energy", "score": 50.0,
                    "composite_score": 85.0, "day_change": 6.0}]
    grow = _run(s, _fit_up({"UPX": "Energy"}))
    assert _tickers(grow["fit_unpriced_picks"]) == ["ZMOVE"]
    assert grow["fit_unpriced_picks"][0]["is_mover"] is True
    assert "ZMOVE" not in _tickers(grow["new_picks"])


def test_all_sectors_unknown_holds_every_classified_pick():
    grow = _run(_pick_scn(1.0), _fit_up({"VOO": ALL}))
    assert grow["new_picks"] == []
    assert {"ZNEWX", "ZCTRL"} <= set(_tickers(grow["fit_unpriced_picks"]))
    assert all("sector map" in i["reason"] for i in grow["fit_unpriced_picks"])


def test_g16_at_the_ceiling_binds_first_with_its_row():
    s = _pick_scn(1.0)
    s["port_rows"] = [{"ticker": "ZHELD", "weight": SECTOR_CEILING, "sector": "Energy"}]
    grow = _run(s, _fit_up({"UPX": "Energy"}))
    assert "ZNEWX" in _tickers(grow["sector_blocked_picks"])
    assert "ZNEWX" not in _tickers(grow["fit_unpriced_picks"])
    g16 = [r for r in _ledger(grow) if r["ticker"] == "ZNEWX"]
    assert [r["gate_id"] for r in g16] == ["G-16"]


def test_just_under_the_g16_ceiling_is_fit_unpriced_and_writes_no_row():
    s = _pick_scn(1.0)
    s["port_rows"] = [{"ticker": "ZHELD", "weight": SECTOR_CEILING - 0.01, "sector": "Energy"}]
    grow = _run(s, _fit_up({"UPX": "Energy"}))
    assert "ZNEWX" in _tickers(grow["fit_unpriced_picks"])
    assert grow["sector_blocked_picks"] == []
    assert [r for r in _ledger(grow) if r["ticker"] == "ZNEWX"] == []


def test_a_real_g06_hold_binds_before_fit_unpriced():
    grow = _run(_pick_scn(HIGH_TICK), _fit_up({"UPX": "Energy"}, port_beta=HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["beta_blocked_picks"])
    assert "ZNEWX" not in _tickers(grow["fit_unpriced_picks"])
    unknown = _run(_pick_scn(HIGH_TICK), _fit_up({"UPX": "Energy"}, port_beta=None))
    assert "ZNEWX" in _tickers(unknown["beta_unknown_picks"])
    assert "ZNEWX" not in _tickers(unknown["fit_unpriced_picks"])


def test_an_unclassified_pick_is_already_held_by_sector_unknown():
    s = _pick_scn(1.0)
    s["rows"] = [{"ticker": "ZNEWX", "score": 90.0, "sector": "Other", "price": 100.0}]
    grow = _run(s, _fit_up({"VOO": ALL}))
    assert "ZNEWX" in _tickers(grow["sector_unknown_picks"])
    assert "ZNEWX" not in _tickers(grow["fit_unpriced_picks"])


def test_an_etf_pick_is_never_held_even_when_every_sector_is_unknown():
    grow = _run(_etf_scn(0.9), _fit_up({"VOO": ALL}))
    assert "SPY" in _tickers(grow["new_picks"])
    assert grow["etf_screened_out"] == []
    assert grow["fit_unpriced_picks"] == []


def test_a_flat_or_fully_priced_book_holds_nothing():
    for fit in (_fit_up({}), {"portfolio_beta": 1.0, "fit_state": {
            "state": "flat", "unpriced_sectors": None, "unpriced": None}}):
        grow = _run(_pick_scn(1.0), fit)
        assert grow["fit_unpriced_picks"] == []
        assert {"ZNEWX", "ZCTRL"} <= set(_tickers(grow["new_picks"]))


def test_a_pre_c5_fit_state_without_the_unpriced_key_changes_nothing():
    old = {"portfolio_beta": 1.0,
           "fit_state": {"state": "ready", "unpriced_sectors": set()}}
    a = _run(_pick_scn(1.0), old)
    b = _run(_pick_scn(1.0), _fit_up({}))
    assert json.dumps(a, sort_keys=True, default=repr) == json.dumps(b, sort_keys=True, default=repr)


def test_beta_fit_none_is_byte_identical_and_the_key_is_an_empty_list():
    s = _pick_scn(1.0)
    grow = _run(s, None)
    assert grow["fit_unpriced_picks"] == []
    port_df = make_port_df(s["port_rows"])
    ctx = {"tone": s["tone"], "sp500_pct": s["pct"], "nasdaq_pct": s["pct"] * 1.2,
           "leading_sectors": []}
    baseline = _grow_today(
        port_df, _scanner_df(s["rows"]), s["news"], s["held_data"], _TODAY, 100_000.0, ctx,
        act_today=s["act_today"], review_list=s["review_list"], composites=s["composites"],
        earnings_lookup=s["earnings"], macro_events=s["macro"], movers=s["movers"],
        etf_candidates=s["etf"])
    assert json.dumps(grow, sort_keys=True, default=repr) == json.dumps(
        baseline, sort_keys=True, default=repr)


def test_every_return_shape_carries_the_key():
    fit = _fit_up({"UPX": "Energy"})
    bear = _run(_scn(_PCT["bear"]), fit)
    assert bear["tone"] == "bear" and bear["fit_unpriced_picks"] == []
    port_df = make_port_df([{"ticker": "ZHELD", "weight": 5.0, "sector": "Tech"}])
    unread = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0,
                         {"tone": "flat", "sp500_pct": 0.0, "tone_available": False,
                          "leading_sectors": []}, beta_fit=fit)
    assert unread["entry_hold"] is not None and unread["fit_unpriced_picks"] == []
    macro = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0,
                        {"tone": "bull", "sp500_pct": 0.8, "leading_sectors": []},
                        beta_fit=fit, macro_unread=True)
    assert macro["entry_hold"] is not None and macro["fit_unpriced_picks"] == []
    assert _run(_pick_scn(1.0), fit)["fit_unpriced_picks"] != []      # the main return


# ── adds ─────────────────────────────────────────────────────────────────────
def test_held_add_in_the_unpriced_sector_rides_entry_held_adds_and_is_paused():
    grow = _run(_add_scn(1.0), _fit_up({"UPX": "Tech"}))
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["check"] == "fit_unpriced" and item["gate_id"] is None
    assert item["holds"] == [] and item["unchecked"] == []
    assert item["gate_value"] is None and item["gate_threshold"] is None
    assert item["sector"] == "Tech" and "UPX couldn't be priced" in item["reason"]
    assert item["clears_when"].startswith("UPX can be priced again")
    v = sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date=_TODAY, today=_TODAY, brief_epoch=1, epoch=1)
    assert v["state"] == "paused" and v["reason"] == item["reason"]
    assert [r for r in _ledger(grow) if r["ticker"] == "ZADDOK"] == []


def test_held_add_in_another_sector_still_adds_and_all_sectors_holds_it():
    assert "ZADDOK" in _tickers(_run(_add_scn(1.0), _fit_up({"UPX": "Energy"}))["add_positions"])
    grow = _run(_add_scn(1.0), _fit_up({"VOO": ALL}))
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    assert any(i["ticker"] == "ZADDOK" and i["check"] == "fit_unpriced"
               for i in grow["entry_held_adds"])


def test_other_add_holds_bind_before_fit_unpriced():
    # a real G-06 add hold keeps its own bucket and ledger row
    grow = _run(_add_scn(HIGH_TICK), _fit_up({"UPX": "Tech"}, port_beta=HIGH_BOOK))
    assert "ZADDOK" in _tickers(grow["beta_blocked_adds"])
    assert not any(i["ticker"] == "ZADDOK" and i["check"] == "fit_unpriced"
                   for i in grow["entry_held_adds"])
    # and a beta_unknown add hold binds before fit_unpriced too
    unk = _run(_add_scn(HIGH_TICK), _fit_up({"UPX": "Tech"}, port_beta=None))
    checks = [i["check"] for i in unk["entry_held_adds"] if i["ticker"] == "ZADDOK"]
    assert checks == ["beta_unknown"]


def test_non_bull_day_a_fit_unpriced_add_is_not_listed_as_adds_paused():
    s = _adds_scn(_PCT["flat"], news=[], earnings={}, act_today=None, review_list=None,
                  held_data={"ZADDOK": {"risk_metrics": {"beta": 1.0}}})
    held = _run(s, _fit_up({"UPX": "Tech"}))
    clear = _run(s, _fit_up({}))
    assert "ZADDOK" not in _tickers(held["adds_paused"])
    assert "ZADDOK" in _tickers(clear["adds_paused"])


# ── readers of the new bucket ────────────────────────────────────────────────
def test_the_new_bucket_is_never_ledgered():
    assert "fit_unpriced_picks" not in gate_ledger._BUCKET_LANES


def test_evening_debrief_counts_the_unpriced_hold():
    from stock_analyzer import evening_debrief
    assert ("fit_unpriced_picks", "sector weight incomplete") in evening_debrief._HELD_BACK_OTHER


def test_grow_dropoff_names_the_unpriced_hold_after_the_beta_holds():
    surfaced = [{"ticker": t, "first_seen_at": "2026-11-26T14:00:00+00:00",
                 "composite_at_surface": 80.0} for t in ("AAA", "BBB")]
    buckets = {
        "beta_unknown_picks": [{"ticker": "AAA", "reason": "R-beta", "composite_score": 81.0}],
        "fit_unpriced_picks": [{"ticker": "AAA", "reason": "R-unp", "composite_score": 81.0},
                               {"ticker": "BBB", "reason": "R-unp-B", "composite_score": 82.0}],
    }
    out = {d["ticker"]: d for d in grow_dropoff.derive_dropoffs(
        surfaced, set(), buckets, None, set())}
    assert out["AAA"]["reason_code"] == "beta_unknown"        # the beta hold wins the tie
    assert out["BBB"]["reason_code"] == "fit_unpriced"
    assert out["BBB"]["reason_text"] == "R-unp-B"
    assert out["BBB"]["has_confident_reason"] is True
    # a bucket dict built before C5 (no key) still works
    old = {d["ticker"]: d for d in grow_dropoff.derive_dropoffs(
        surfaced, set(), {}, None, set())}
    assert old["BBB"]["reason_code"] == "unattributed"


# ═════════════════ 5. cron: compute_morning_picks / watchlist entries ═══════
class _OpenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        naive = datetime.combine(_TODAY, time(10, 30))
        return tz.localize(naive) if tz is not None else naive


def _cron_ctx(held_tickers):
    port_df = make_port_df([{"ticker": "AAPL", "weight": 10.0, "sector": "Tech"}])
    return {
        "ok": True, "errors": [], "port_df": port_df,
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None,
        "vix": None,
        "holdings_df": pd.DataFrame([{"Ticker": t, "Shares": 1.0, "Avg Cost ($)": 10.0}
                                     for t in held_tickers]),
        "trades_df": None, "port_risk": {"beta": 1.0},
    }


def _run_morning(held_tickers):
    scanner = _scanner_df([{"ticker": "NVDA", "score": 90.0, "sector": "Healthcare",
                            "price": 100.0}])
    bundle = _with_beta(_bundle(80.0), 1.0)
    with patch.object(hae, "_build_context", return_value=_cron_ctx(held_tickers)), \
         patch.object(hae, "datetime", _OpenClock), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": 1.5},
                             {"short": "NASDAQ", "change_pct": 1.5}]), \
         patch.object(hae, "load_bundle", return_value=bundle), \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.04), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]):
        return hae.compute_morning_picks(_TODAY, scanner_results=scanner)


def test_compute_morning_picks_holds_a_scan_name_in_the_unpriced_sector():
    res = _run_morning(["AAPL", "AMD"])          # AMD is held but not in the priced frame
    assert "NVDA" not in _tickers(res["picks"])
    assert res["diag"]["fit_unpriced"] == 1
    assert res["fit_state"]["state"] == "ready"
    assert res["fit_state"]["unpriced"] == {"AMD": "Semiconductors"}
    assert res["grow"]["fit_unpriced_picks"][0]["ticker"] == "NVDA"


def test_compute_morning_picks_fully_priced_book_still_picks_and_carries_fit_state():
    res = _run_morning(["AAPL"])
    assert "NVDA" in _tickers(res["picks"])
    assert res["diag"]["fit_unpriced"] == 0
    assert res["fit_state"]["unpriced"] == {}


def test_watchlist_cron_holds_a_name_in_the_unpriced_sector_and_lists_it():
    r, save, spy, *_ = _wle(entry_context=FLAT_CTX, ctx=_ok_ctx(), held=("HELD1", "AMD"))
    card = spy[0][3]
    assert card["action"] == "NEAR_ENTRY" and card["suppression_kind"] == "fit_unpriced"
    assert r["entries"] == [] and r["gate_ledger_rows"] == []
    save.assert_not_called()
    assert r["checks_held"]["fit_unpriced"] == [SEMIS]
    assert r["checks_held"]["fit_unknown"] == []


def test_watchlist_cron_other_sector_unpriced_holding_holds_nothing():
    r, _s, spy, *_ = _wle(entry_context=FLAT_CTX, ctx=_ok_ctx(), held=("HELD1", "XOM"))
    assert spy[0][3]["action"] == "ENTER_NOW"
    assert [e["ticker"] for e in r["entries"]] == [SEMIS]
    assert r["checks_held"]["fit_unpriced"] == []
    assert r["fit_state"]["unpriced"] == {"XOM": "Energy"}


def test_watchlist_cron_a_real_g05_breach_binds_first_with_its_row():
    ctx = _ok_ctx(sector="Semiconductors", weight=SECTOR_CEILING + 5.0)
    r, _s, spy, *_ = _wle(entry_context=FLAT_CTX, ctx=ctx, held=("HELD1", "AMD"))
    assert spy[0][3]["suppression_kind"] == "sector"
    assert [(x["ticker"], x["gate_id"]) for x in r["gate_ledger_rows"]] == [(SEMIS, "G-05")]
    assert r["checks_held"]["fit_unpriced"] == []


def test_the_held_checks_line_names_fit_unpriced_in_its_own_slot():
    f = __import__("cron_runner")._watchlist_checks_held_line
    assert f({"checks_held": {"fit_unpriced": ["NVDA", "AVGO"]}}) == (
        "watchlist: 2 held, a check couldn't run (fit_unpriced: NVDA, AVGO)")
    assert f({"checks_held": {"fit_unknown": ["A"], "beta_unknown": ["B"],
                              "fit_unpriced": ["C"], "tone_unread": ["D"]}}) == (
        "watchlist: 4 held, a check couldn't run "
        "(fit_unknown: A; beta_unknown: B; fit_unpriced: C; tone_unread: D)")


# ── scan wiring: drop line + email lines ─────────────────────────────────────
def _go_pick(tk="AAA"):
    return {"ticker": tk, "composite_score": 80.0, "score": 70.0, "price": 100.0,
            "sector": "Tech", "conviction": "High", "thesis": "t", "asset_type": "stock",
            "xref": {"verdict": "go", "verdict_reconciled": {"verdict": "go"}},
            "sizing": {"shares": 5, "total_cost": 500.0, "entry_lo": 99.0,
                       "entry_hi": 101.0, "stop": 94.0, "port_pct": 1.0}}


_UNPRICED_FS = {"state": "ready", "unpriced_sectors": {"Semiconductors"},
                "unpriced": {"AMD": "Semiconductors"}}


def _recording(monkeypatch):
    import cron_runner as cr
    calls = {"action": [], "wl": []}

    def _fake_action(*a, **k):
        calls["action"].append(k)
        return "subject", "<html>action</html>"

    def _fake_wl(*a, **k):
        calls["wl"].append(k)
        return "subject", "<html>wl</html>"
    monkeypatch.setattr(cr, "render_daily_action_email", _fake_action)
    monkeypatch.setattr(cr, "render_watchlist_entries_email", _fake_wl)
    return calls


def test_scan_passes_the_unpriced_line_to_both_emails_only_when_part_of_the_book_is_unpriced(
        monkeypatch):
    calls = _recording(monkeypatch)
    morning = _morning_payload(picks=[_go_pick()], fit_state=_UNPRICED_FS)
    wle = _wle_payload(entries=[_enter_card("AAA")], fit_state=_UNPRICED_FS)
    _scan(monkeypatch, morning=morning, wle=wle)
    line = su.email_line(_UNPRICED_FS)
    assert calls["action"][0]["book_unpriced_line"] == line
    assert calls["wl"][0]["unpriced_line"] == line


def test_scan_calls_the_renderers_exactly_as_before_on_a_fully_priced_run(monkeypatch):
    calls = _recording(monkeypatch)
    clean = {"state": "ready", "unpriced_sectors": set(), "unpriced": {}}
    morning = _morning_payload(picks=[_go_pick()], fit_state=clean)
    wle = _wle_payload(entries=[_enter_card("AAA")], fit_state=clean)
    _scan(monkeypatch, morning=morning, wle=wle)
    assert "book_unpriced_line" not in calls["action"][0]
    assert "unpriced_line" not in calls["wl"][0]
    # and a payload that predates C5 (no fit_state key) too
    calls["action"].clear()
    calls["wl"].clear()
    _scan(monkeypatch, morning=_morning_payload(picks=[_go_pick()]),
          wle=_wle_payload(entries=[_enter_card("AAA")]))
    assert "book_unpriced_line" not in calls["action"][0]
    assert "unpriced_line" not in calls["wl"][0]


def test_scan_zero_pick_log_counts_the_unpriced_hold_and_the_lane_is_not_red(monkeypatch):
    import cron_runner as cr
    logs: list = []
    monkeypatch.setattr(cr, "_log", lambda m: logs.append(m))
    morning = _morning_payload(diag={"tone": "bull", "sp500_pct": 0.5, "bar": 65,
                                     "fit_unpriced": 2})
    rc, *_ = _scan(monkeypatch, morning=morning, wle=_wle_payload())
    assert rc == 0                                       # lane not red (G-36 owns that)
    assert any("2 sector weight incomplete" in m for m in logs)


# ═════════════════ 6. email renderers ════════════════════════════════════════
_LINE = su.email_line(_UNPRICED_FS)
_TOP = {"ticker": "AAA", "composite_score": 80.0, "score": 70.0, "sector": "Tech",
        "thesis": "t", "sizing": {}}


def test_pick_email_has_one_amber_box_with_the_escaped_line_and_none_without():
    _, plain = notify.render_daily_action_email(_TOP, [], [], "2026-11-27T09:45:00")
    _, same = notify.render_daily_action_email(_TOP, [], [], "2026-11-27T09:45:00",
                                               book_unpriced_line=None)
    assert plain == same and "Sector weights may read low" not in plain
    _, html = notify.render_daily_action_email(_TOP, [], [], "2026-11-27T09:45:00",
                                               book_unpriced_line=_LINE)
    assert html.count("Sector weights may read low") == 1
    assert "Part of your book couldn&#x27;t be priced" in html or \
        "Part of your book couldn't be priced" in html
    _, esc = notify.render_daily_action_email(
        _TOP, [], [], "2026-11-27T09:45:00", book_unpriced_line="<b>x</b> & y")
    assert "<b>x</b>" not in esc and "&lt;b&gt;x&lt;/b&gt; &amp; y" in esc


def test_watchlist_email_shows_the_line_even_when_both_legs_read():
    entries = [_enter_card("AAA")]
    _, plain = notify.render_watchlist_entries_email(entries, built_at="2026-11-27T09:46:00")
    _, same = notify.render_watchlist_entries_email(
        entries, built_at="2026-11-27T09:46:00", unpriced_line=None)
    assert plain == same and "Sector weights may read low" not in plain
    _, html = notify.render_watchlist_entries_email(
        entries, built_at="2026-11-27T09:46:00", fit_read=True, beta_read=True,
        unpriced_line=_LINE)
    assert html.count("Sector weights may read low") == 1
    assert "Part of your book couldn" in html


def test_watchlist_email_beta_only_box_gets_the_line_appended_never_a_second_box():
    entries = [_enter_card("AAA")]
    _, html = notify.render_watchlist_entries_email(
        entries, built_at="2026-11-27T09:46:00", gate_degraded=True, fit_read=True,
        beta_read=False, unpriced_line=_LINE)
    assert "Portfolio-beta fit check unavailable this run" in html
    assert "The concentration/sector check below still applied" in html
    assert "Part of your book couldn" in html
    assert "Sector weights may read low" not in html           # one box, not two
    assert html.count("border-left:4px solid #f59e0b") == 1


def test_watchlist_email_fit_unread_box_also_takes_the_line_in_the_same_box():
    entries = [_enter_card("AAA")]
    _, html = notify.render_watchlist_entries_email(
        entries, built_at="2026-11-27T09:46:00", gate_degraded=True, fit_read=False,
        beta_read=False, unpriced_line=_LINE)
    assert "Portfolio fit checks unavailable this run" in html
    assert html.count("border-left:4px solid #f59e0b") == 1


def test_degraded_box_markup_is_unchanged_without_a_line():
    entries = [_enter_card("AAA")]
    _, html = notify.render_watchlist_entries_email(
        entries, built_at="2026-11-27T09:46:00", gate_degraded=True, fit_read=True,
        beta_read=False)
    assert ('<div style="color:#f59e0b;font-weight:700;font-size:12px">'
            '⚠️ Portfolio-beta fit check unavailable this run</div>') in html


# ═════════════════ 7. source pins (both ends) ════════════════════════════════
def test_producer_and_consumer_ends_are_wired():
    hl = (ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")
    assert '"fit_state": _beta_fit["fit_state"]' in hl
    assert '"fit_unpriced":     len(grow.get("fit_unpriced_picks", []))' in hl
    assert '"fit_unpriced", ' in hl or '"fit_unpriced"' in hl
    cron = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert 'sector_unpriced.email_line(payload.get("fit_state"))' in cron
    assert 'sector_unpriced.email_line(_wle_payload.get("fit_state"))' in cron
    assert "sector_unpriced.CHECK" in cron
    db_src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    assert db_src.count('"fit_unpriced_picks"') >= 3          # hold return, bear, main
    adv = (ROOT / "stock_analyzer" / "watchlist_advisor.py").read_text(encoding="utf-8")
    assert '"fit_unpriced": {' in adv and "_sector_unpriced.decide(" in adv
    # the gate sits between fit_unknown and beta_unknown inside _portfolio_risk_gate
    gate = adv[adv.index("def _portfolio_risk_gate"):]
    assert gate.index('"kind":     "fit_unknown"') < gate.index("_sector_unpriced.decide(") \
        < gate.index('"kind":     "beta_unknown"')


def test_no_comparison_against_a_threshold_in_the_wiring_files():
    """The only constants the new module reads are printed, never compared."""
    src = (ROOT / "stock_analyzer" / "sector_unpriced.py").read_text(encoding="utf-8")
    assert not re.search(r"(<|>|<=|>=)\s*(SECTOR_CEILING|BRIEF_AUTO_REFRESH_MINUTES)", src)
    assert math.isfinite(SECTOR_CEILING)


def test_analysis_tile_never_downgrades_a_measured_breach():
    # A2 review: an unpriced holding can only raise the sector's true weight, so a
    # failing (✗) Sector cap tile stays failing; only a pass / unknown becomes "—".
    m = {"AMD": "Semiconductors"}
    assert su.analysis_tile("Semiconductors", m, current_ok=False) is None
    assert su.analysis_tile("Semiconductors", m, current_ok=True)[0] is None
    assert su.analysis_tile("Semiconductors", m)[0] is None
