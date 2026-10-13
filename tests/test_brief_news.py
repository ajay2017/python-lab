"""curate_brief_news -- the Daily Brief's news_items, shared by the app and
the cron (audit 2026-10-11 B-M7, fixed 2026-10-13).

Two defects, one helper:
  * the APP passed held news only, so a new-pick candidate's own negative
    headlines never reached _cross_reference layer 3 / reconcile_signals --
    Grow Today could show "Go" on a pick the morning email's go-only list
    had left out ("Skip — Negative News");
  * the CRON merged held + candidate bundles into ONE top-20-most-recent list,
    so fresh candidate headlines could push a held stock's negative news out
    of Act Today's critical-news check.
"""
import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer.data import curate_brief_news, curate_news_items
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent


def _bundle(prefix, n, ts0, title_fmt="{p} update {i}"):
    return {"news_raw": [
        {"title": title_fmt.format(p=prefix, i=i), "publisher": "Reuters",
         "link": f"https://x/{prefix}{i}", "providerPublishTime": ts0 + i}
        for i in range(n)
    ]}


def test_held_news_is_never_displaced_by_fresher_candidate_news():
    # 3 OLD held headlines vs 40 NEWER candidate headlines (5 candidates x 8).
    held = {"HLD": _bundle("HLD", 3, ts0=1_000)}
    cands = {f"C{k}": _bundle(f"C{k}", 8, ts0=9_000_000 + k * 100) for k in range(5)}
    merged_old_way = curate_news_items({**held, **cands})        # the cron's old behaviour
    assert not any(i["ticker"] == "HLD" for i in merged_old_way), "precondition: old way drops held"
    out = curate_brief_news(held, cands)
    assert sum(i["ticker"] == "HLD" for i in out) == 3


def test_every_candidate_keeps_its_own_headlines():
    cands = {f"C{k}": _bundle(f"C{k}", 8, ts0=9_000_000 + k * 100) for k in range(5)}
    out = curate_brief_news({}, cands)
    for k in range(5):
        assert sum(i["ticker"] == f"C{k}" for i in out) == 8   # not cut by a global 20


def test_held_part_without_held_news_is_the_raw_curated_list():
    # The cron path: it has never rescored, so its held news is raw VADER.
    held = {"HLD": _bundle("HLD", 30, ts0=1_000)}
    assert curate_brief_news(held, {}) == curate_news_items(held)
    assert curate_brief_news(held, None) == curate_news_items(held)


def test_a_passed_held_news_list_is_used_verbatim():
    """The app passes its FINAL held list -- after the suppress-only LLM
    rescore that neutralises VADER false positives. Recomputing it here would
    let Act Today fire "Critical News" on headlines the app had neutralised
    (review FIX-FIRST, 2026-10-13)."""
    held = {"HLD": _bundle("HLD", 3, ts0=1_000)}
    rescored = [dict(i, compound=0.0, label="Neutral") for i in curate_news_items(held)]
    assert curate_brief_news(held, {}, held_news=rescored) == rescored
    out = curate_brief_news(held, {"NEW": _bundle("NEW", 2, ts0=5_000)}, held_news=rescored)
    assert out[:3] == rescored and sum(i["ticker"] == "NEW" for i in out) == 2
    # An explicitly EMPTY held list is honoured, never silently recomputed.
    assert curate_brief_news(held, {}, held_news=[]) == []


def test_malformed_candidate_news_falls_back_to_held_only():
    held = {"HLD": _bundle("HLD", 2, ts0=1_000)}
    bad = {"NEW": {"news_raw": ["not a dict", 42]}}
    with pytest.warns(UserWarning, match="candidate news unavailable"):
        assert curate_brief_news(held, bad) == curate_news_items(held)


def test_a_held_ticker_in_candidates_is_not_double_counted():
    held = {"HLD": _bundle("HLD", 3, ts0=1_000)}
    out = curate_brief_news(held, {"HLD": _bundle("HLD", 3, ts0=1_000), "NEW": _bundle("NEW", 2, ts0=5_000)})
    assert sum(i["ticker"] == "HLD" for i in out) == 3
    assert sum(i["ticker"] == "NEW" for i in out) == 2


def test_duplicate_headline_across_held_and_candidate_appears_once():
    same = "Shared sector headline"
    held = {"HLD": _bundle("HLD", 1, ts0=1_000, title_fmt=same)}
    cand = {"NEW": _bundle("NEW", 1, ts0=2_000, title_fmt=same)}
    out = curate_brief_news(held, cand)
    assert [i["title"] for i in out].count(same) == 1


def test_none_and_non_dict_inputs_never_raise():
    assert curate_brief_news(None, None) == []
    assert curate_brief_news({}, {"BAD": None, "ALSO": "x"}) == []


def test_candidate_negative_news_now_reaches_the_cross_check():
    """Through the REAL _cross_reference: a non-held candidate with strongly
    negative headlines gets a news conflict with the shared helper's list --
    and none with the old held-only list the app used to pass."""
    from stock_analyzer.daily_briefing import _cross_reference
    neg = {"news_raw": [
        {"title": t, "publisher": "Reuters", "link": f"https://x/{i}", "providerPublishTime": 5_000 + i}
        for i, t in enumerate([
            "CAND plunges as fraud probe deepens and lawsuit filed",
            "CAND crashes on terrible losses, bankruptcy fears grow",
            "CAND shares collapse after disastrous guidance cut",
        ])
    ]}
    held = {"HLD": _bundle("HLD", 2, ts0=1_000)}
    port_df = pd.DataFrame({"Ticker": ["HLD"], "Weight (%)": [100.0]})
    row = {"Ticker": "CAND", "Signal": "BUY", "Score": 80}

    def _conflicts(news):
        x = _cross_reference("CAND", row, port_df, news, held, date(2026, 10, 13))
        return [c for c in x["conflicts"] if c.startswith("News sentiment: Negative")]

    assert _conflicts(curate_news_items(held)) == []                       # old app input
    got = _conflicts(curate_brief_news(held, {"CAND": neg}))                # shared helper
    assert len(got) == 1
    # Curated items carry `title`, not `headline` -- the message used to
    # quote an empty string ("").
    assert '""' not in got[0] and "CAND" in got[0]


# ── wiring: app (both Brief builds) and cron use the SAME helper ─────────────

def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == name)
        or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def test_both_app_brief_builds_pass_curate_brief_news():
    tree = parse_file_cached(ROOT / "app.py")
    briefs = _calls(tree, "build_daily_briefing")
    assert len(briefs) >= 2
    for call in briefs:
        kw = {k.arg: k.value for k in call.keywords}
        v = kw.get("news_items")
        assert isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
            and v.func.id == "curate_brief_news", f"line {call.lineno}"
        assert isinstance(v.args[0], ast.Name) and v.args[0].id == "held_data"
        hn = {k.arg: k.value for k in v.keywords}.get("held_news")
        assert (isinstance(hn, ast.Call) and isinstance(hn.func, ast.Attribute)
                and hn.func.attr == "get" and isinstance(hn.args[0], ast.Constant)
                and hn.args[0].value == "_brief_held_news"), f"line {call.lineno}: held_news"


def test_app_publishes_the_final_held_list_for_the_brief():
    # Assigned from `_news` -- the variable the LLM rescore overwrites -- and
    # never from _sidebar_news, which Market Scanner overwrites.
    tree = parse_file_cached(ROOT / "app.py")
    sets = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Subscript)
            and isinstance(n.targets[0].slice, ast.Constant)
            and n.targets[0].slice.value == "_brief_held_news"]
    assert len(sets) == 1
    assert isinstance(sets[0].value, ast.Name) and sets[0].value.id == "_news"


def test_cron_brief_uses_curate_brief_news():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    assert _calls(tree, "curate_brief_news"), "the cron must build news via the shared helper"
    assert not _calls(tree, "curate_news_items"), "no second, divergent news builder in the cron"
