"""Beta-gate C2: G-06 (portfolio beta) on 🏠 Grow Today and the cron pick emails.

Covers `beta_fit_gate.hold_text`, `_grow_today` / `build_daily_briefing` with the
`beta_fit` kwarg (picks, movers, adds, ETFs, bear return, precedence, ledger,
reader lists) and the cron `compute_morning_picks` wiring. Every boundary is read
from `constants.py`, never a literal. Spec: docs/plans/beta-gate-c2-spec.md.
"""
import ast
import json
import re
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from stock_analyzer import beta_fit_gate, gate_ledger, grow_dropoff, sizing_path
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.buy_check_state import FIT_FLAT, FIT_READY, FIT_UNKNOWN
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    NEWS_SENTIMENT_NEGATIVE,
    PORTFOLIO_BETA_CEILING,
    SECTOR_CEILING,
    TICKER_BETA_CRITICAL,
)
from stock_analyzer.daily_briefing import _grow_today
from tests._ast_cache import parse_file_cached
from tests.conftest import make_port_df
from tests.test_daily_briefing import _etf_candidate, _scanner_df
from tests.test_grow_today_golden import (
    _PCT,
    _TODAY,
    _adds_scn,
    _bundle,
    _iso,
    _news,
    _scn,
)

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent

HIGH_BOOK = PORTFOLIO_BETA_CEILING + 0.2
HIGH_TICK = TICKER_BETA_CRITICAL + 0.2


def _fit(port_beta, state=FIT_READY):
    return {"portfolio_beta": port_beta,
            "fit_state": {"state": state, "unpriced_sectors": set()}}


def _with_beta(bundle, beta):
    b = dict(bundle)
    b["risk_metrics"] = {"beta": beta}
    return b


def _run(s, beta_fit):
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = _scanner_df(s["rows"]) if s["rows"] else None
    ctx = {"tone": s["tone"], "sp500_pct": s["pct"], "nasdaq_pct": s["pct"] * 1.2,
           "leading_sectors": []}
    return _grow_today(
        port_df, scanner, s["news"], s["held_data"], _TODAY, 100_000.0, ctx,
        act_today=s["act_today"], review_list=s["review_list"],
        composites=s["composites"], earnings_lookup=s["earnings"],
        macro_events=s["macro"], movers=s["movers"], etf_candidates=s["etf"],
        beta_fit=beta_fit)


def _pick_scn(newx_beta, ctrl_beta=1.0, **o):
    s = _scn(_PCT["bull"], **o)
    s["composites"] = {
        "ZNEWX": _with_beta(_bundle(80.0), newx_beta),
        "ZCTRL": _with_beta(_bundle(80.0), ctrl_beta),
    }
    return s


def _tickers(items):
    return [i["ticker"] for i in items]


# ── hold_text ────────────────────────────────────────────────────────────────

def _blocked():
    return beta_fit_gate.decide(port_beta=HIGH_BOOK, ticker_beta=HIGH_TICK,
                                fit_state={"state": FIT_READY})


def test_hold_text_blocked_pick_and_etf_wording_reads_constants():
    d = _blocked()
    for lane in ("pick", "etf"):
        t = beta_fit_gate.hold_text(d, "ZNEWX", lane=lane)
        assert t["reason"].startswith("Not opened today — portfolio beta.")
        assert f"{HIGH_BOOK:.2f}" in t["reason"] and f"{HIGH_TICK:.2f}" in t["reason"]
        assert f"{PORTFOLIO_BETA_CEILING:.1f}" in t["reason"]
        assert f"over {TICKER_BETA_CRITICAL:.1f}" in t["reason"]
        assert "ZNEWX's beta" in t["reason"]
        assert "The 📋 Watchlist applies the same check." in t["reason"]
        assert t["clears_when"] == (
            f"when the book's beta is back at or under {PORTFOLIO_BETA_CEILING:.1f}")
        assert "$" not in t["reason"]


def test_hold_text_blocked_add_wording():
    t = beta_fit_gate.hold_text(_blocked(), "ZADDOK", lane="add")
    assert t["reason"].startswith("Add paused — portfolio beta.")
    assert "adding would raise the book's market sensitivity further." in t["reason"]
    assert "Watchlist" not in t["reason"]


def test_hold_text_unknown_kinds():
    fs_ok = {"state": FIT_READY}
    port_unread = beta_fit_gate.decide(port_beta=None, ticker_beta=HIGH_TICK, fit_state=fs_ok)
    tick_unread = beta_fit_gate.decide(port_beta=HIGH_BOOK, ticker_beta=None, fit_state=fs_ok)
    both = beta_fit_gate.decide(port_beta=None, ticker_beta=None, fit_state=fs_ok)
    fit = beta_fit_gate.decide(port_beta=1.0, ticker_beta=1.0, fit_state={"state": FIT_UNKNOWN})
    for d in (port_unread, tick_unread, both, fit):
        assert d["state"] == "unknown"
    cw = "at the next Home build or scan that can read it"
    t = beta_fit_gate.hold_text(port_unread, "T", lane="pick")
    assert t["reason"] == ("Held — portfolio beta couldn't be checked: the book's beta couldn't "
                           "be computed. That's a reason to pause, not a green light.")
    assert t["clears_when"] == cw
    assert "T's beta couldn't be read" in beta_fit_gate.hold_text(tick_unread, "T", lane="pick")["reason"]
    assert "neither the book's beta nor T's could be read" in (
        beta_fit_gate.hold_text(both, "T", lane="etf")["reason"])
    assert "your holdings couldn't be loaded and priced" in (
        beta_fit_gate.hold_text(fit, "T", lane="pick")["reason"])
    add = beta_fit_gate.hold_text(port_unread, "T", lane="add")
    assert add["reason"].startswith("Add held — portfolio beta couldn't be checked: ")


def test_hold_text_none_on_pass_and_unrecognised():
    passed = beta_fit_gate.decide(port_beta=PORTFOLIO_BETA_CEILING, ticker_beta=HIGH_TICK,
                                  fit_state={"state": FIT_READY})
    assert passed["state"] == "pass"
    assert beta_fit_gate.hold_text(passed, "T", lane="pick") is None
    assert beta_fit_gate.hold_text({"state": "garbage"}, "T", lane="pick") is None
    assert beta_fit_gate.hold_text(None, "T", lane="pick") is None
    assert beta_fit_gate.hold_text("x", "T", lane="pick") is None


@pytest.mark.parametrize("bad", [
    {"state": "blocked"},                                     # no betas to format
    {"state": "blocked", "port_beta": "x", "ticker_beta": object()},
    {"state": "unknown", "kind": object(), "port_beta": object()},
    {"state": "unknown"},
])
def test_hold_text_never_raises_and_never_none_for_a_non_pass(bad):
    for lane in ("pick", "add", "etf", "bogus"):
        t = beta_fit_gate.hold_text(bad, "T", lane=lane)
        assert isinstance(t, dict) and t["reason"] and t["clears_when"]


# ── picks: G-06 hold ─────────────────────────────────────────────────────────

def test_high_beta_pick_on_high_beta_book_is_held_and_ledgered():
    grow = _run(_pick_scn(HIGH_TICK), _fit(HIGH_BOOK))
    assert "ZNEWX" not in _tickers(grow["new_picks"])
    assert "ZNEWX" not in _tickers(grow["overflow_picks"])
    item = next(i for i in grow["beta_blocked_picks"] if i["ticker"] == "ZNEWX")
    assert item["gate_id"] == "G-06"
    assert item["counterfactual"] is True
    assert item["gate_threshold"] == TICKER_BETA_CRITICAL
    assert item["gate_value"] == HIGH_TICK
    assert item["port_beta"] == HIGH_BOOK and item["ticker_beta"] == HIGH_TICK
    assert item["reason"].startswith("Not opened today — portfolio beta.")
    assert item["clears_when"]
    assert "sizing" not in item
    # the control name (beta under the critical line) is untouched
    assert "ZCTRL" in _tickers(grow["new_picks"])
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    g06 = [r for r in rows if r["gate_id"] == "G-06"]
    assert len(g06) == 1
    assert g06[0]["lane"] == "new_pick" and g06[0]["ticker"] == "ZNEWX"
    assert g06[0]["gate_threshold"] == TICKER_BETA_CRITICAL


def test_book_beta_exactly_at_ceiling_is_picked():
    grow = _run(_pick_scn(HIGH_TICK), _fit(PORTFOLIO_BETA_CEILING))
    assert "ZNEWX" in _tickers(grow["new_picks"])
    assert grow["beta_blocked_picks"] == []


def test_ticker_beta_exactly_at_critical_is_picked():
    grow = _run(_pick_scn(TICKER_BETA_CRITICAL), _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["new_picks"])
    assert grow["beta_blocked_picks"] == []


def test_mover_is_held_by_g06_too():
    s = _pick_scn(1.0)
    s["rows"] = []
    s["composites"] = {"ZMOVE": _with_beta(_bundle(80.0), HIGH_TICK)}
    s["movers"] = [{"ticker": "ZMOVE", "price": 100.0, "sector": "Energy", "score": 50.0,
                    "composite_score": 85.0, "day_change": 6.0}]
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZMOVE" not in _tickers(grow["new_picks"])
    item = next(i for i in grow["beta_blocked_picks"] if i["ticker"] == "ZMOVE")
    assert item["is_mover"] is True and item["gate_id"] == "G-06"


# ── picks: precedence (G-06 is checked after every earlier gate) ────────────

def test_precedence_sector_over_cap_beats_g06():
    s = _pick_scn(HIGH_TICK)
    s["port_rows"] = [{"ticker": "ZHELD", "weight": SECTOR_CEILING + 5.0, "sector": "Energy"}]
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["sector_blocked_picks"])
    assert "ZNEWX" not in _tickers(grow["beta_blocked_picks"])


def test_precedence_negative_news_skip_beats_g06():
    s = _pick_scn(HIGH_TICK, news=_news("ZNEWX", NEWS_SENTIMENT_NEGATIVE))
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["news_skipped"])
    assert "ZNEWX" not in _tickers(grow["beta_blocked_picks"])


def test_precedence_earnings_caution_beats_g06():
    s = _pick_scn(HIGH_TICK, earnings={"ZNEWX": _iso(1)})
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["earnings_waiting"])
    assert "ZNEWX" not in _tickers(grow["beta_blocked_picks"])


def test_precedence_sub_buy_composite_beats_g06():
    s = _pick_scn(HIGH_TICK)
    s["composites"]["ZNEWX"] = _with_beta(_bundle(COMPOSITE_BUY - 5.0), HIGH_TICK)
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["composite_skipped"])
    assert "ZNEWX" not in _tickers(grow["beta_blocked_picks"])


# ── picks: couldn't check ────────────────────────────────────────────────────

def test_unread_book_beta_with_high_ticker_beta_is_a_beta_unknown_hold_no_ledger_row():
    grow = _run(_pick_scn(HIGH_TICK), _fit(None))
    assert "ZNEWX" not in _tickers(grow["new_picks"])
    item = next(i for i in grow["beta_unknown_picks"] if i["ticker"] == "ZNEWX")
    assert item["gate_id"] is None and item["kind"] == "beta_unknown"
    assert item["gate_value"] is None and item["gate_threshold"] is None
    assert "sizing" not in item
    assert grow["beta_blocked_picks"] == []
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert [r for r in rows if r["ticker"] == "ZNEWX"] == []


def test_unread_book_beta_with_ticker_at_critical_passes():
    # the unread leg could not have tipped the gate
    grow = _run(_pick_scn(TICKER_BETA_CRITICAL), _fit(None))
    assert "ZNEWX" in _tickers(grow["new_picks"])
    assert grow["beta_unknown_picks"] == []


def test_unread_ticker_beta_on_a_high_book_is_held():
    grow = _run(_pick_scn(None), _fit(HIGH_BOOK))
    assert "ZNEWX" in _tickers(grow["beta_unknown_picks"])
    assert "ZNEWX" not in _tickers(grow["new_picks"])


def test_unknown_fit_holds_every_evaluated_pick():
    grow = _run(_pick_scn(1.0), _fit(1.0, state=FIT_UNKNOWN))
    assert grow["new_picks"] == []
    held = grow["beta_unknown_picks"]
    assert {"ZNEWX", "ZCTRL"} <= set(_tickers(held))
    assert all(i["kind"] == "fit_unknown" for i in held)


def test_flat_book_never_holds():
    grow = _run(_pick_scn(HIGH_TICK), _fit(None, state=FIT_FLAT))
    assert "ZNEWX" in _tickers(grow["new_picks"])


def test_non_dict_beta_fit_reads_as_fit_unknown_hold():
    grow = _run(_pick_scn(1.0), "garbage")
    assert grow["new_picks"] == []
    assert {"ZNEWX", "ZCTRL"} <= set(_tickers(grow["beta_unknown_picks"]))


# ── beta_fit=None: byte-identical to the unwired output ─────────────────────

def test_beta_fit_none_skips_every_check_and_keys_are_empty_lists():
    s = _pick_scn(HIGH_TICK)
    grow = _run(s, None)
    assert "ZNEWX" in _tickers(grow["new_picks"])
    for k in ("beta_blocked_picks", "beta_unknown_picks", "beta_blocked_adds"):
        assert grow[k] == []
    # every OTHER key equals a build where the kwarg was never passed
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


def test_bear_return_carries_the_three_keys_as_empty_lists():
    s = _scn(_PCT["bear"])
    grow = _run(s, _fit(HIGH_BOOK))
    assert grow["tone"] == "bear"
    for k in ("beta_blocked_picks", "beta_unknown_picks", "beta_blocked_adds"):
        assert grow[k] == []


# ── adds ─────────────────────────────────────────────────────────────────────

def _add_scn(add_beta, **o):
    """Bull day, held rows from the golden add fixtures; ZADDOK is the add under test."""
    kw = dict(news=[], earnings={}, act_today=None, review_list=None,
              held_data={"ZADDOK": {"risk_metrics": {"beta": add_beta}}})
    kw.update(o)
    return _adds_scn(_PCT["bull"], **kw)


def test_add_with_g06_alone_is_beta_blocked_paused_and_ledgered():
    grow = _run(_add_scn(HIGH_TICK), _fit(HIGH_BOOK))
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    item = next(i for i in grow["beta_blocked_adds"] if i["ticker"] == "ZADDOK")
    assert item["gate_id"] == "G-06" and item["counterfactual"] is True
    assert item["gate_threshold"] == TICKER_BETA_CRITICAL
    assert item["reason"].startswith("Add paused — portfolio beta.")
    assert item["clears_when"]
    v = sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date=_TODAY, today=_TODAY, brief_epoch=1, epoch=1)
    assert v["state"] == "paused"
    assert v["reason"] == item["reason"]
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    mine = [r for r in rows if r["ticker"] == "ZADDOK"]
    assert len(mine) == 1 and mine[0]["gate_id"] == "G-06" and mine[0]["lane"] == "add_winner"


def test_add_at_ceiling_book_still_adds():
    grow = _run(_add_scn(HIGH_TICK), _fit(PORTFOLIO_BETA_CEILING))
    assert "ZADDOK" in _tickers(grow["add_positions"])
    assert grow["beta_blocked_adds"] == []


def test_add_g01_beats_g06():
    s = _add_scn(HIGH_TICK)
    risk_recs = [{"type": "beta", "priority": "HIGH", "title": "Beta too high",
                  "root_tickers": ["ZADDOK"]}]
    port_df = make_port_df(s["port_rows"])
    ctx = {"tone": s["tone"], "sp500_pct": s["pct"], "nasdaq_pct": 1.0, "leading_sectors": []}
    with patch("stock_analyzer.daily_briefing._trim_targets",
               return_value={"ZADDOK": {"reason": "beta", "title": "t", "priority": "HIGH"}}):
        grow = _grow_today(port_df, None, [], s["held_data"], _TODAY, 100_000.0, ctx,
                           risk_recs=risk_recs, beta_fit=_fit(HIGH_BOOK))
    assert "ZADDOK" in _tickers(grow["risk_blocked_adds"])
    assert "ZADDOK" not in _tickers(grow["beta_blocked_adds"])


def test_add_entry_news_hold_beats_g06():
    s = _add_scn(HIGH_TICK, news=_news("ZADDOK", NEWS_SENTIMENT_NEGATIVE - 0.2, n=3))
    grow = _run(s, _fit(HIGH_BOOK))
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["gate_id"] == "G-30"
    assert "ZADDOK" not in _tickers(grow["beta_blocked_adds"])


def test_beta_unknown_add_rides_entry_held_adds_with_no_ledger_row():
    grow = _run(_add_scn(HIGH_TICK), _fit(None))
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["check"] == "beta_unknown"
    assert item["gate_id"] is None
    assert item["holds"] == [] and item["unchecked"] == []
    assert item["reason"].startswith("Add held — portfolio beta couldn't be checked: ")
    assert grow["beta_blocked_adds"] == []
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert [r for r in rows if r["ticker"] == "ZADDOK"] == []
    v = sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date=_TODAY, today=_TODAY, brief_epoch=1, epoch=1)
    assert v["state"] == "paused"


def test_non_bull_day_g06_add_is_not_listed_as_adds_paused():
    # `_adds_paused_list` inherits through `_add_block_gate`
    s = _adds_scn(_PCT["flat"], news=[], earnings={}, act_today=None, review_list=None,
                  held_data={"ZADDOK": {"risk_metrics": {"beta": HIGH_TICK}}})
    held = _run(s, _fit(HIGH_BOOK))
    clear = _run(s, _fit(PORTFOLIO_BETA_CEILING))
    assert "ZADDOK" not in _tickers(held["adds_paused"])
    assert "ZADDOK" in _tickers(clear["adds_paused"])


# ── ETF lane ─────────────────────────────────────────────────────────────────

def _etf_scn(beta):
    cand = _etf_candidate()
    cand["bundle"]["risk_metrics"] = {"beta": beta}
    return _scn(_PCT["bull"], rows=[], composites={}, etf=[cand])


def test_high_beta_etf_on_a_high_beta_book_is_screened_out_kind_beta():
    grow = _run(_etf_scn(TICKER_BETA_CRITICAL + 1.0), _fit(HIGH_BOOK))
    assert "SPY" not in _tickers(grow["new_picks"])
    e = next(i for i in grow["etf_screened_out"] if i["ticker"] == "SPY")
    assert e["kind"] == "beta"
    assert e["reason"].startswith("Not opened today — portfolio beta.")
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert [r for r in rows if r["ticker"] == "SPY"] == []


def test_etf_with_unread_beta_on_high_book_is_screened_out_beta_unknown():
    grow = _run(_etf_scn(None), _fit(HIGH_BOOK))
    e = next(i for i in grow["etf_screened_out"] if i["ticker"] == "SPY")
    assert e["kind"] == "beta_unknown"


def test_low_beta_etf_still_picked():
    grow = _run(_etf_scn(0.9), _fit(HIGH_BOOK))
    assert "SPY" in _tickers(grow["new_picks"])


# ── readers of the new buckets ───────────────────────────────────────────────

def test_readers_know_the_new_buckets():
    assert "beta_blocked_adds" in sizing_path._PAUSE_BUCKETS
    assert gate_ledger._BUCKET_LANES["beta_blocked_picks"] == "new_pick"
    assert gate_ledger._BUCKET_LANES["beta_blocked_adds"] == "add_winner"
    assert "beta_unknown_picks" not in gate_ledger._BUCKET_LANES


def test_grow_dropoff_names_the_beta_holds():
    surfaced = [{"ticker": "AAA", "first_seen_at": "2026-11-26T14:00:00+00:00",
                 "composite_at_surface": 80.0},
                {"ticker": "BBB", "first_seen_at": "2026-11-26T14:00:00+00:00",
                 "composite_at_surface": 80.0}]
    buckets = {
        "beta_blocked_picks": [{"ticker": "AAA", "reason": "R-blocked",
                                "composite_score": 81.0}],
        "beta_unknown_picks": [{"ticker": "BBB", "reason": "R-unknown",
                                "composite_score": 82.0}],
    }
    out = {d["ticker"]: d for d in grow_dropoff.derive_dropoffs(
        surfaced, set(), buckets, None, set())}
    assert out["AAA"]["reason_code"] == "beta_blocked" and out["AAA"]["reason_text"] == "R-blocked"
    assert out["BBB"]["reason_code"] == "beta_unknown" and out["BBB"]["reason_text"] == "R-unknown"
    assert out["AAA"]["has_confident_reason"] and out["BBB"]["has_confident_reason"]


def test_evening_debrief_counts_the_beta_holds():
    from stock_analyzer import evening_debrief
    keys = [k for k, _ in evening_debrief._HELD_BACK_OTHER]
    assert "beta_blocked_picks" in keys and "beta_unknown_picks" in keys


# ── wiring: AST pins on BOTH ends ────────────────────────────────────────────

def _calls(tree, name):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                    isinstance(f, ast.Attribute) and f.attr == name):
                out.append(n)
    return out


def test_every_build_daily_briefing_call_passes_beta_fit():
    for rel in ("app.py", "stock_analyzer/headless_alert_engine.py"):
        tree = parse_file_cached(ROOT / rel)
        calls = _calls(tree, "build_daily_briefing")
        assert calls, rel
        for c in calls:
            assert "beta_fit" in {k.arg for k in c.keywords}, f"{rel}:{c.lineno}"
    # app.py has exactly the two Brief builds (the stale-bundle rebuild and the main build)
    app_tree = parse_file_cached(ROOT / "app.py")
    assert len(_calls(app_tree, "build_daily_briefing")) == 2


def test_build_daily_briefing_forwards_beta_fit_to_grow_today():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "daily_briefing.py")
    bdb = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "build_daily_briefing")
    assert "beta_fit" in {a.arg for a in bdb.args.kwonlyargs + bdb.args.args}
    gt = [c for c in _calls(bdb, "_grow_today")]
    assert len(gt) == 1
    kw = {k.arg: k.value for k in gt[0].keywords}
    assert isinstance(kw.get("beta_fit"), ast.Name) and kw["beta_fit"].id == "beta_fit"


def test_app_beta_fit_helper_reads_the_memoised_risk_through_get_or_offline():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _brief_beta_fit(" in src
    body = src[src.index("def _brief_beta_fit("):][:900]
    assert "_get_or_offline(_pr, \"beta\")" in body
    assert "buy_check_state.fit_state(" in body
    assert "_SYNTH_SCHEMA_VER = 40" in src   # 39 -> 40 by brief-consolidation C1


def test_app_grow_shown_and_dropoff_buckets_include_the_new_keys():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for k in ("beta_blocked_picks", "beta_unknown_picks", "beta_blocked_adds"):
        assert f'"{k}"' in src[src.index("_grow_shown: set = set()"):][:1800], k
    assert re.search(r'"beta_unknown_picks":\s+grow\.get\("beta_unknown_picks", \[\]\)', src)


# ── cron: compute_morning_picks ──────────────────────────────────────────────

def _cron_ctx(port_risk):
    port_df = make_port_df([{"ticker": "AAPL", "weight": 10.0, "sector": "Tech"}])
    return {
        "ok": True, "errors": [], "port_df": port_df,
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None,
        "vix": None, "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]}),
        "trades_df": None, "port_risk": port_risk,
    }


class _OpenClock(datetime):
    """hae's clock pinned to 10:30 ET on `_TODAY`: Grow Today (buy-check C4b) holds a
    read that isn't `today`'s post-open session, and `_TODAY` is a fixed past date."""
    @classmethod
    def now(cls, tz=None):
        naive = datetime.combine(_TODAY, time(10, 30))
        return tz.localize(naive) if tz is not None else naive


def _run_cron(port_risk, nvda_beta):
    bundle = _with_beta(_bundle(80.0), nvda_beta)
    scanner = _scanner_df([{"ticker": "NVDA", "score": 90.0, "sector": "Healthcare",
                            "price": 100.0}])
    with patch.object(hae, "_build_context", return_value=_cron_ctx(port_risk)), \
         patch.object(hae, "datetime", _OpenClock), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": 1.5},
                             {"short": "NASDAQ", "change_pct": 1.5}]), \
         patch.object(hae, "load_bundle", return_value=bundle), \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.04), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]):
        return hae.compute_morning_picks(_TODAY, scanner_results=scanner)


def test_cron_holds_a_high_beta_scan_name_on_a_high_beta_book():
    res = _run_cron({"beta": HIGH_BOOK}, HIGH_TICK)
    assert "NVDA" not in _tickers(res["picks"])
    assert res["diag"]["beta_blocked"] == 1
    assert res["diag"]["beta_unknown"] == 0


def test_cron_unreadable_book_beta_holds_a_high_beta_name_as_unknown():
    res = _run_cron(None, HIGH_TICK)
    assert "NVDA" not in _tickers(res["picks"])
    assert res["diag"]["beta_unknown"] == 1
    assert res["diag"]["beta_blocked"] == 0


def test_cron_low_beta_name_is_still_picked():
    res = _run_cron({"beta": HIGH_BOOK}, 1.0)
    assert "NVDA" in _tickers(res["picks"])
    assert res["diag"]["beta_blocked"] == 0


def test_cron_dropped_explainer_names_both_beta_holds():
    src = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert "portfolio-beta hold" in src and "beta couldn't be checked" in src


def test_empty_state_caption_counts_beta_and_sector_unknown_holds_as_cleared():
    """Opus FIX-FIRST (C2): a G-06 / beta-unchecked / sector-unknown pick cleared
    the buy bar, so the Grow Today empty-state caption must count it as held back,
    never fold it into 'none of the scored candidates cleared the buy threshold'."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    i = src.index("_held_out_groups = (")
    block = src[i:i + 900]
    assert 'grow.get("beta_blocked_picks", [])' in block
    assert 'grow.get("beta_unknown_picks", [])' in block
    assert "sector_unknown)" in block
    assert "beta_blocked_adds" not in block
    assert "_news_skip_n = sum(" in block
