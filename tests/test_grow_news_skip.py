"""F-302 -- a new pick whose RECONCILED verdict is "skip" is held out of New
Positions (2026-10-14).

Grow Today's legacy filter dropped only xref["verdict"] == "conflicted", but
the card renders reconcile_signals()' verdict. A "❌ Skip — Negative News"
pick has legacy verdict "mixed", so it reached New Positions WITH a share size
and was rec-logged as a new_pick -- the Engine Track Record then graded a call
the card itself said to skip. The morning email was already go-only
(cron_runner), so app and email now agree.
"""
import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

from stock_analyzer import grow_dropoff
from stock_analyzer.constants import (
    ATR_STOP_MULT, COMPOSITE_BUY, MOVER_MAX_PICKS, NEWS_SENTIMENT_NEGATIVE,
)
from stock_analyzer.daily_briefing import _grow_today
from tests._ast_cache import parse_file_cached
from tests.test_daily_briefing import _scanner_df, make_port_df

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
_TODAY = date(2026, 10, 14)


def _bundle(total=COMPOSITE_BUY + 10, price=100.0, atr=3.0):
    return {
        "total": total, "rec": {"label": "Buy"},
        "fundamentals_available": True,
        "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
        "entry_lo": 99.0, "entry_hi": 101.5,
    }


def _neg_news(ticker, n=3):
    c = NEWS_SENTIMENT_NEGATIVE - 0.3
    return [{"ticker": ticker, "title": f"{ticker} slides on probe {i}",
             "compound": c, "label": "Negative"} for i in range(n)]


def _run(scanner_rows, news, composites):
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    return _grow_today(port_df, _scanner_df(scanner_rows), news, {}, _TODAY,
                       100_000.0, {"tone": "bull"}, composites=composites)


def _tickers(items):
    return [i["ticker"] for i in items]


def test_control_without_news_is_a_new_pick():
    g = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
             [], {"NEW": _bundle()})
    assert _tickers(g["new_picks"]) == ["NEW"]
    assert g["new_picks"][0]["xref"]["verdict_reconciled"]["verdict"] != "skip"
    assert g["news_skipped"] == []


def test_negative_news_pick_is_held_out_with_its_reason():
    g = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
             _neg_news("NEW"), {"NEW": _bundle()})
    assert "NEW" not in _tickers(g["new_picks"])
    assert _tickers(g["news_skipped"]) == ["NEW"]
    item = g["news_skipped"][0]
    assert "Negative News" in item["label"]
    assert item["one_liner"]                          # the reason is carried, not dropped
    assert item["composite_score"] == COMPOSITE_BUY + 10
    assert "sizing" not in item                       # never sized


def test_curated_skip_and_earnings_wait_are_both_held_out():
    # F-302 freed a curated skip's 1-per-sector slot, but the only name that
    # could take it was another unverified pick -- in practice an earnings-
    # caution one, which F-303 now holds out too. Pre-F-302: ['NEW'] (skip,
    # sized); between F-302 and F-303: ['ALT'] (caution, sized).
    rows = [{"ticker": "NEW", "score": COMPOSITE_BUY + 20, "sector": "Energy"},
            {"ticker": "ALT", "score": COMPOSITE_BUY + 5, "sector": "Energy"}]
    comps = {"NEW": _bundle(COMPOSITE_BUY + 20), "ALT": _bundle(COMPOSITE_BUY + 5)}
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    g = _grow_today(port_df, _scanner_df(rows), _neg_news("NEW"), {}, _TODAY, 100_000.0,
                    {"tone": "bull"}, composites=comps,
                    earnings_lookup={"ALT": (_TODAY + timedelta(days=2)).isoformat()})
    assert g["new_picks"] == []
    assert _tickers(g["news_skipped"]) == ["NEW"]
    assert _tickers(g["earnings_waiting"]) == ["ALT"]


def _movers(n=4):
    sectors = ["Energy", "Healthcare", "Financials", "Industrials", "Utilities"]
    ms = [{"ticker": f"M{i}", "price": 100.0, "sector": sectors[i], "score": COMPOSITE_BUY + 10,
           "composite_score": 90 - i, "day_change": 6.0} for i in range(n)]
    return ms, {m["ticker"]: _bundle(90 - i) for i, m in enumerate(ms)}


def test_a_skipped_mover_frees_a_mover_slot():
    # The mover allowance (MOVER_MAX_PICKS) used to be spent on the skip pick;
    # the next "go" mover now takes it -- which the go-only email also gains.
    ms, comps = _movers(MOVER_MAX_PICKS + 1)
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    clean = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                        composites=comps, movers=ms)
    last = f"M{MOVER_MAX_PICKS}"
    assert last not in _tickers(clean["new_picks"]), "precondition: allowance is full"
    g = _grow_today(port_df, None, _neg_news("M0"), {}, _TODAY, 100_000.0, {"tone": "bull"},
                    composites=comps, movers=ms)
    assert "M0" not in _tickers(g["new_picks"]) and last in _tickers(g["new_picks"])
    assert _tickers(g["news_skipped"]) == ["M0"]


@pytest.mark.parametrize("bad", [{"stale_as_of": "2026-10-10"}, {"fundamentals_available": False}])
def test_a_data_quality_failure_keeps_its_own_reason(bad):
    # Ordering: the skip hold-out runs AFTER every data-quality gate.
    g = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
             _neg_news("NEW"), {"NEW": {**_bundle(), **bad}})
    assert _tickers(g["composite_unavailable"]) == ["NEW"]
    assert g["news_skipped"] == []


def test_etf_pick_with_negative_news_is_screened_out_not_sized():
    from tests.test_daily_briefing import _etf_candidate
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    clean = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                        etf_candidates=[_etf_candidate()])
    assert "SPY" in _tickers(clean["new_picks"]), "precondition: the ETF fixture is a pick"
    g = _grow_today(port_df, None, _neg_news("SPY"), {}, _TODAY, 100_000.0, {"tone": "bull"},
                    etf_candidates=[_etf_candidate()])
    assert "SPY" not in _tickers(g["new_picks"])
    out = [e for e in g["etf_screened_out"] if e["ticker"] == "SPY"]
    assert len(out) == 1 and out[0]["kind"] == "skip_verdict"
    assert "Negative News" in out[0]["reason"]


def test_no_new_pick_ever_carries_a_skip_verdict():
    rows = [{"ticker": t, "score": COMPOSITE_BUY + 10, "sector": s}
            for t, s in (("AAA", "Energy"), ("BBB", "Healthcare"), ("CCC", "Financials"))]
    g = _run(rows, _neg_news("AAA") + _neg_news("CCC"), {t: _bundle() for t in ("AAA", "BBB", "CCC")})
    assert _tickers(g["new_picks"]) == ["BBB"]
    assert sorted(_tickers(g["news_skipped"])) == ["AAA", "CCC"]
    for p in g["new_picks"]:
        assert p["xref"]["verdict_reconciled"]["verdict"] != "skip"


def test_bear_day_early_return_carries_the_key():
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    g = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bear", "sp500_pct": -2.0})
    assert g["news_skipped"] == []


# ── drop-off attribution ─────────────────────────────────────────────────────

def _drop(buckets):
    return grow_dropoff.derive_dropoffs(
        [{"ticker": "NEW", "first_seen_at": "2026-10-14T13:00:00+00:00", "composite_at_surface": 75.0}],
        set(), buckets, {}, set())


def test_dropoff_names_the_skip_verdict():
    d = _drop({"news_skipped": [{"ticker": "new", "composite_score": 74.0,
                                 "label": "❌ Skip — Negative News"}]})
    assert d[0]["reason_code"] == "skip_verdict"
    assert d[0]["has_confident_reason"] is True
    assert "Negative News" in d[0]["reason_text"]
    assert d[0]["current_composite"] == 74.0


def test_dropoff_skip_ranks_below_sector_unknown():
    d = _drop({"news_skipped": [{"ticker": "NEW", "label": "x"}],
               "sector_unknown_picks": [{"ticker": "NEW", "reason": "sector unknown"}]})
    assert d[0]["reason_code"] == "sector_unknown"


# ── app wiring ───────────────────────────────────────────────────────────────

def _app():
    return parse_file_cached(ROOT / "app.py")


def test_render_reads_the_bucket_from_grow():
    gets = [n for n in ast.walk(_app()) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "grow"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "news_skipped"]
    assert len(gets) == 1


def test_dropoff_buckets_include_news_skipped():
    for n in ast.walk(_app()):
        if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id == "_do_buckets" and isinstance(n.value, ast.Dict)):
            pairs = {k.value: v for k, v in zip(n.value.keys, n.value.values)
                     if isinstance(k, ast.Constant)}
            v = pairs.get("news_skipped")
            assert isinstance(v, ast.Name) and v.id == "news_skipped"
            return
    pytest.fail("_do_buckets dict not found")


def test_grow_shown_dedup_covers_news_skipped():
    # Reversed 2026-10-15 (was test_buy_candidates_dedup_covers_news_skipped):
    # the "More Buy Candidates" lane is gone, but app.py's _grow_shown still
    # dedups every other Brief section against Grow Today's held-out buckets.
    for n in ast.walk(_app()):
        if (isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "_gk"
                and isinstance(n.iter, ast.Tuple)):
            keys = {e.value for e in n.iter.elts if isinstance(e, ast.Constant)}
            assert {"news_skipped", "composite_skipped", "new_picks"} <= keys
            return
    pytest.fail("_grow_shown key loop not found")


def test_the_visible_box_is_rendered():
    # The box is the visibility guarantee -- deleting it must fail the suite.
    for n in ast.walk(_app()):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "news_skipped"):
            calls = {c.func.attr for c in ast.walk(n) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)}
            assert "markdown" in calls and "container" in calls
            text = ast.unparse(n)
            assert "Signal Check Says Skip" in text and "one_liner" in text
            return
    pytest.fail("no `if news_skipped:` render block in app.py")


# ── F-302 follow-up: legacy "conflicted" picks are no longer dropped silently ──

def test_hold_labelled_composite_lands_in_composite_skipped():
    # Pre-follow-up: legacy "conflicted" -> dropped before the composite gate,
    # in NO bucket (so "Composite Says No" only ever filled for unlabelled
    # bundles).
    g = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}], [],
             {"NEW": {**_bundle(COMPOSITE_BUY - 10), "rec": {"label": "Hold"}}})
    assert _tickers(g["composite_skipped"]) == ["NEW"]
    assert g["composite_skipped"][0]["composite_label"] == "Hold"
    assert "NEW" not in _tickers(g["new_picks"]) and g["news_skipped"] == []


def test_conflicted_with_bad_data_keeps_the_data_reason():
    g = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}], [],
             {"NEW": {**_bundle(COMPOSITE_BUY - 10), "rec": {"label": "Hold"},
                      "stale_as_of": "2026-10-10"}})
    assert _tickers(g["composite_unavailable"]) == ["NEW"]
    assert g["composite_skipped"] == [] and g["news_skipped"] == []


def test_conflicted_residual_is_held_out_visibly():
    # A low-score mover with negative news AND earnings in 2 days: legacy
    # "conflicted" (earnings + signal), but reconciled is NOT "skip" -- the
    # negative-news skip needs momentum >= COMPOSITE_BUY -- so only the
    # residual branch can catch it. Pre-follow-up it vanished.
    ms = [{"ticker": "MV", "price": 100.0, "sector": "Energy", "score": COMPOSITE_BUY - 20,
           "composite_score": 80, "day_change": 6.0}]
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    g = _grow_today(port_df, None, _neg_news("MV"), {}, _TODAY, 100_000.0, {"tone": "bull"},
                    composites={"MV": _bundle(80)}, movers=ms,
                    earnings_lookup={"MV": (_TODAY + timedelta(days=2)).isoformat()})
    assert "MV" not in _tickers(g["new_picks"])
    item = next(i for i in g["news_skipped"] if i["ticker"] == "MV")
    assert "Conflicted" in item["label"]
    assert "Earnings" in item["one_liner"] and "News sentiment" in item["one_liner"]


def test_no_new_pick_is_ever_legacy_conflicted():
    rows = [{"ticker": t, "score": COMPOSITE_BUY + 10, "sector": s}
            for t, s in (("AAA", "Energy"), ("BBB", "Healthcare"), ("CCC", "Financials"))]
    comps = {"AAA": {**_bundle(), "rec": {"label": "Hold"}}, "BBB": _bundle(), "CCC": _bundle()}
    g = _grow_today(make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}]),
                    _scanner_df(rows), _neg_news("CCC"), {}, _TODAY, 100_000.0, {"tone": "bull"},
                    composites=comps, earnings_lookup={"CCC": (_TODAY + timedelta(days=1)).isoformat()})
    assert _tickers(g["new_picks"]) == ["BBB"]
    for p in g["new_picks"]:
        assert p["xref"]["verdict"] != "conflicted"


def test_cron_logs_the_skip_count():
    eng = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    keys = {k.value for n in ast.walk(eng) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)}
    assert "news_skipped" in keys
    assert 'd.get("news_skipped")' in ast.unparse(parse_file_cached(ROOT / "cron_runner.py")).replace("'", '"')


def test_composite_skipped_mover_carries_its_breakout_for_the_render():
    ms = [{"ticker": "MV", "price": 100.0, "sector": "Energy", "score": COMPOSITE_BUY - 20,
           "composite_score": 50, "day_change": 6.0}]
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    g = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                    composites={"MV": {**_bundle(COMPOSITE_BUY - 15), "rec": {"label": "Hold"}}}, movers=ms)
    item = next(i for i in g["composite_skipped"] if i["ticker"] == "MV")
    assert item["is_mover"] is True and item["day_change"] == 6.0
    # The render branches on it, so a mover never reads "Momentum X/100".
    assert "Breakout" in ast.unparse(next(
        n for n in ast.walk(_app()) if isinstance(n, ast.FunctionDef) and n.name == "_cs_trigger"))


# ── F-303: earnings-wait hold-out (owner decision 2026-10-14) ────────────────

def _earn(t, days=2):
    return {t: (_TODAY + timedelta(days=days)).isoformat()}


def test_earnings_imminent_pick_waits_unsized():
    port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
    clean = _run([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}], [], {"NEW": _bundle()})
    assert _tickers(clean["new_picks"]) == ["NEW"], "precondition"
    g = _grow_today(port_df, _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}]),
                    [], {}, _TODAY, 100_000.0, {"tone": "bull"}, composites={"NEW": _bundle()},
                    earnings_lookup=_earn("NEW"))
    assert g["new_picks"] == []
    item = g["earnings_waiting"][0]
    assert item["ticker"] == "NEW" and item["earn_days"] == 2
    assert "Earnings" in item["label"] and "sizing" not in item


def test_every_new_pick_left_is_reconciled_go():
    rows = [{"ticker": t, "score": COMPOSITE_BUY + 10, "sector": s}
            for t, s in (("AAA", "Energy"), ("BBB", "Healthcare"), ("CCC", "Financials"), ("DDD", "Materials"))]
    comps = {t: _bundle() for t in ("AAA", "BBB", "CCC", "DDD")}
    comps["DDD"] = {**_bundle(), "rec": {"label": "Hold"}}
    g = _grow_today(make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}]),
                    _scanner_df(rows), _neg_news("AAA"), {}, _TODAY, 100_000.0, {"tone": "bull"},
                    composites=comps, earnings_lookup=_earn("CCC", 1))
    assert _tickers(g["new_picks"]) == ["BBB"]
    assert all(p["xref"]["verdict_reconciled"]["verdict"] == "go" for p in g["new_picks"])
    assert _tickers(g["earnings_waiting"]) == ["CCC"]


def test_etf_with_imminent_earnings_is_screened_out():
    from tests.test_daily_briefing import _etf_candidate
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    g = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                    etf_candidates=[_etf_candidate()], earnings_lookup=_earn("SPY"))
    assert "SPY" not in _tickers(g["new_picks"])
    assert [e["kind"] for e in g["etf_screened_out"] if e["ticker"] == "SPY"] == ["earnings_wait"]


def test_dropoff_names_the_earnings_wait_after_skip():
    d = _drop({"earnings_waiting": [{"ticker": "NEW", "label": "⚠️ Caution — Earnings in 2d"}]})
    assert d[0]["reason_code"] == "earnings_wait" and "post-print" in d[0]["reason_text"]
    d = _drop({"earnings_waiting": [{"ticker": "NEW"}], "news_skipped": [{"ticker": "NEW", "label": "x"}]})
    assert d[0]["reason_code"] == "skip_verdict"


def test_app_wires_earnings_waiting():
    tree = _app()
    gets = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "grow"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "earnings_waiting"]
    assert len(gets) == 1
    box = [n for n in ast.walk(tree) if isinstance(n, ast.If)
           and isinstance(n.test, ast.Name) and n.test.id == "earnings_waiting"]
    assert len(box) == 1 and "Waiting on Earnings" in ast.unparse(box[0])
    assert "container" in {c.func.attr for c in ast.walk(box[0])
                           if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    src = ast.unparse(tree)
    assert "'earnings_waiting': earnings_waiting" in src           # _do_buckets
    assert "'news_skipped', 'earnings_waiting'," in src            # dedup loop
    # _ns_comp is shared by both boxes, so it must not live inside either if-block.
    for b in box + [n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and isinstance(n.test, ast.Name) and n.test.id == "news_skipped"]:
        assert not any(isinstance(f, ast.FunctionDef) and f.name == "_ns_comp" for f in ast.walk(b))


def test_cron_logs_the_earnings_wait_count():
    eng = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    keys = {k.value for n in ast.walk(eng) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)}
    assert "earnings_waiting" in keys
    assert 'd.get("earnings_waiting")' in ast.unparse(parse_file_cached(ROOT / "cron_runner.py")).replace("'", '"')


# ── Evening Debrief: breakout movers aren't graded as skips ──────────────────

def _debrief(skipped, pct):
    from stock_analyzer.evening_debrief import _plan_vs_reality
    brief = {"grow_today": {"new_picks": [], "add_positions": [], "composite_skipped": skipped},
             "buy_candidates": []}
    return _plan_vs_reality(brief, [], pct)


def test_debrief_excludes_movers_from_the_skip_scorecard():
    r = _debrief([{"ticker": "MV", "composite_score": 50, "is_mover": True},
                  {"ticker": "CU", "composite_score": 50, "is_mover": False},
                  {"ticker": "OLD", "composite_score": 50}],          # legacy brief: no key
                 {"MV": 6.0, "CU": 3.0, "OLD": -3.0})
    assert sorted(s["ticker"] for s in r["skip_picks"]) == ["CU", "OLD"]
    assert r["skip_movers_excluded"] == 1


def test_debrief_render_never_says_nothing_filtered_when_movers_were():
    src = ast.unparse(_app())
    assert "_ed_pvr.get('skip_movers_excluded', 0)" in src
    assert "No other composite-filtered names to grade today." in src
    assert "Nothing was filtered out today." not in src     # the card grades one box only
    assert "_ed_pvr.get('held_back_other')" in src


def test_debrief_counts_the_other_held_back_boxes():
    from stock_analyzer.evening_debrief import _plan_vs_reality
    grow = {"new_picks": [], "add_positions": [], "composite_skipped": [],
            "news_skipped": [{"ticker": "A"}], "earnings_waiting": [{"ticker": "B"}, {"ticker": "C"}],
            "macro_blocked_picks": []}
    r = _plan_vs_reality({"grow_today": grow, "buy_candidates": []}, [], {})
    assert r["skip_picks"] == []
    assert r["held_back_other"] == {"❌ signal-check skip": 1, "⏳ waiting on earnings": 2}


def test_debrief_graded_rows_are_the_else_of_the_empty_state():
    # The held-back caption once landed between `if not _ed_skips:` and its
    # `else:`, so Python bound the else to the caption's if and the graded
    # rows vanished whenever any other box fired (review FIX-FIRST 2026-10-14).
    tree = _app()
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
             and isinstance(n.iter, ast.Name) and n.iter.id == "_ed_skips"]
    assert len(loops) == 1
    owner = [n for n in ast.walk(tree) if isinstance(n, ast.If) and loops[0] in n.orelse]
    assert len(owner) == 1
    t = owner[0].test
    assert (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
            and isinstance(t.operand, ast.Name) and t.operand.id == "_ed_skips")
