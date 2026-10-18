"""Headline-relevance chunk 2 (R9): decision paths de-dup headlines PER TICKER
so a headline naming several holdings reaches each; the sidebar stays
title-deduped. Fixed dates only."""
from datetime import date
from pathlib import Path
import re

import pytest

from stock_analyzer import held_news_check as hnc
from stock_analyzer.data import (
    HELD_NEWS_DISPLAY_COUNT, curate_brief_news, curate_news_items, dedupe_titles,
)

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 19)
_S = hnc.news_window_start(_TODAY).timestamp()
_SHARED = "Entegris, Micron, AMD, Marvell Technology, Lam Research plunge as fraud probe and lawsuit hit shares"


def _raw(title, ts=None, pub="Reuters"):
    return {"title": title, "publisher": pub, "link": "http://x",
            "providerPublishTime": int(_S + 100 if ts is None else ts)}


def _neg(t, n):
    return _raw(f"{t} faces fraud lawsuit, plunges amid crash and scandal {n}")


def test_per_ticker_shared_headline_once_per_ticker():
    d = {t: {"news_raw": [_raw(_SHARED)]} for t in ("MU", "AMD", "MRVL")}
    pt = curate_news_items(d, max_items=None, per_ticker=True)
    assert sorted(i["ticker"] for i in pt) == ["AMD", "MRVL", "MU"]
    assert len(curate_news_items(d, max_items=None)) == 1
    assert len(curate_news_items(d, max_items=None, per_ticker=False)) == 1


def test_per_ticker_still_dedupes_within_a_ticker():
    d = {"MU": {"news_raw": [_raw(_SHARED), _raw(_SHARED)]}}
    assert len(curate_news_items(d, max_items=None, per_ticker=True)) == 1


def test_dedupe_titles_keeps_first_order_preserved():
    items = [{"title": "A x"}, {"title": "B"}, {"title": "a X"}, {"title": "C"}]
    assert [i["title"] for i in dedupe_titles(items)] == ["A x", "B", "C"]


def test_dedupe_titles_differing_copies_keep_fresher_or_higher_tier():
    # Documented: parity with the old title-only list needs copies sharing the
    # ts//1800 bucket and tier; otherwise the fresher / higher-tier copy wins.
    d = {"AAA": {"news_raw": [_raw(_SHARED, ts=_S + 100, pub="Zacks")]},
         "BBB": {"news_raw": [_raw(_SHARED, ts=_S + 100 + 7200, pub="Zacks")]}}
    kept = dedupe_titles(curate_news_items(d, max_items=None, per_ticker=True))
    assert [i["ticker"] for i in kept] == ["BBB"]   # fresher copy, not first-seen AAA
    d = {"AAA": {"news_raw": [_raw(_SHARED, ts=_S + 100, pub="Zacks")]},
         "BBB": {"news_raw": [_raw(_SHARED, ts=_S + 100, pub="Reuters")]}}
    kept = dedupe_titles(curate_news_items(d, max_items=None, per_ticker=True))
    assert [i["ticker"] for i in kept] == ["BBB"]   # same bucket, higher tier


def test_sidebar_parity_with_ties():
    d = {}
    for k in range(18):
        t = f"T{k:02d}"
        d[t] = {"news_raw": [_raw(_SHARED), _raw("Market wide rally lifts chips"),
                             _raw(f"{t} own headline one"), _raw(f"{t} own headline two")]}
    full = curate_news_items(d, max_items=None, per_ticker=True)
    assert len(full) > len(curate_news_items(d, max_items=None))
    assert dedupe_titles(full)[:HELD_NEWS_DISPLAY_COUNT] == curate_news_items(d)


def _end_to_end_data():
    return {
        "MU": {"news_raw": [_raw(_SHARED)]},
        "AMD": {"news_raw": [_raw(_SHARED), _neg("AMD", 1)]},
        "MRVL": {"news_raw": [_raw(_SHARED), _neg("MRVL", 1)]},
    }


def test_critical_news_fires_for_every_named_holding():
    held = _end_to_end_data()
    held_t = set(held)
    new = curate_brief_news(held, None)
    crit = hnc.critical_news_by_ticker(new, held_t, _S)
    assert {"MU", "AMD", "MRVL"} >= set(crit) and {"AMD", "MRVL"} <= set(crit)
    old = curate_news_items(held, max_items=None)          # title-only de-dup
    crit_old = hnc.critical_news_by_ticker(old, held_t, _S)
    assert "AMD" not in crit_old and "MRVL" not in crit_old


def test_curate_brief_news_candidate_part_per_ticker_and_d_e():
    held = {"MU": {"news_raw": [_raw(_SHARED)]}}
    cands = {"AMD": {"news_raw": [_raw(_SHARED)]}, "MRVL": {"news_raw": [_raw(_SHARED)]}}
    out = curate_brief_news(held, cands)
    assert sorted(i["ticker"] for i in out) == ["AMD", "MRVL", "MU"]


def test_rescore_selection_head_is_title_unique():
    items = [{"title": "dup", "ticker": "A"},
             {"title": "dup", "ticker": "B", "ts": _S + 100, "compound": -0.9},
             {"title": "x", "ticker": "A"}, {"title": "y", "ticker": "A"}]
    send, over = hnc.rescore_selection(items, _S, keep_first=3, max_items=10)
    # the repeat is an ordinary extra: in-window + negative -> sent AFTER the head
    assert send == [0, 2, 3, 1] and over == []


_APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def test_app_pins():
    assert "_held_all = curate_news_items(held_data, max_items=None, per_ticker=True)" in _APP
    assert "_news = dedupe_titles(_held_all)[:HELD_NEWS_DISPLAY_COUNT]" in _APP
    assert 'st.session_state["_sidebar_news"] = dedupe_titles(_rescored)[:HELD_NEWS_DISPLAY_COUNT]' in _APP
    assert re.search(r"_brief_news = _rescored\b", _APP)
