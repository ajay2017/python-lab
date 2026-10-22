"""Held-news coverage chunk 1 (docs/plans/held-news-coverage.md): window,
selection, batched/memoized/backoff rescore, uncapped curate, Home wiring."""
import re
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from stock_analyzer import held_news_check as hnc
from stock_analyzer import news_intelligence as ni
from stock_analyzer.constants import NEWS_SENTIMENT_WARN
from stock_analyzer.data import curate_news_items

pytestmark = pytest.mark.fast

_APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


# ── news_window_start ──────────────────────────────────────────────────────
@pytest.mark.parametrize("today,expected,lb", [
    (date(2026, 10, 19), date(2026, 10, 16), 1),   # Mon -> Fri
    (date(2026, 1, 20), date(2026, 1, 16), 1),     # Tue after MLK
    (date(2026, 10, 17), date(2026, 10, 16), 1),   # Sat
    (date(2026, 10, 18), date(2026, 10, 16), 1),   # Sun
    (date(2026, 11, 27), date(2026, 11, 25), 1),   # Fri after Thanksgiving
    (date(2026, 11, 2), date(2026, 10, 30), 1),
    (date(2026, 10, 21), date(2026, 10, 20), 1),
    (date(2026, 10, 19), date(2026, 10, 15), 2),
])
def test_window_start_examples(today, expected, lb):
    w = hnc.news_window_start(today, lb)
    assert w.tzinfo is not None
    assert w.date() == expected and (w.hour, w.minute) == (0, 0)


def test_window_start_dst_offsets():
    w = hnc.news_window_start(date(2026, 11, 2))   # 10-30 is EDT
    assert w.utcoffset().total_seconds() == -4 * 3600
    assert w.astimezone(timezone.utc) == datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
    w2 = hnc.news_window_start(date(2026, 11, 10))  # 11-09 is EST
    assert w2.utcoffset().total_seconds() == -5 * 3600


def test_window_start_default_uses_constant():
    assert hnc.news_window_start(date(2026, 10, 19)) == hnc.news_window_start(date(2026, 10, 19), 1)


def test_window_start_bounded():
    with pytest.raises(ValueError):
        hnc.news_window_start(date(2026, 10, 19), 100)


# ── item_ts / in_window ────────────────────────────────────────────────────
@pytest.mark.parametrize("v", [None, 0, -5, "x", True, float("nan"), "123"])
def test_item_ts_invalid(v):
    assert hnc.item_ts({"ts": v}) == 0.0
    assert hnc.in_window({"ts": v}, 0.0) is False


def test_item_ts_missing_and_valid():
    assert hnc.item_ts({}) == 0.0
    assert hnc.item_ts({"ts": 5}) == 5.0


def test_in_window_boundary():
    assert hnc.in_window({"ts": 1000}, 1000.0) is True
    assert hnc.in_window({"ts": 999}, 1000.0) is False


# ── rescore_selection ──────────────────────────────────────────────────────
def _it(i, c=0.5, ts=2000, ticker="T"):
    return {"ticker": ticker, "title": f"headline {i}", "compound": c, "ts": ts, "tier": 3}


def test_selection_head_always_sent_extras_filtered():
    items = [_it(i, c=0.9) for i in range(20)]              # positive head: still sent
    items += [_it(20, c=-0.5),                              # in-window negative
              _it(21, c=0.3),                               # positive: never
              _it(22, c=-0.8, ts=10),                       # out-of-window: never
              _it(23, c=-0.8, ts=0),                        # undated: never
              _it(24, c=-0.9),                              # most negative
              _it(25, c=NEWS_SENTIMENT_WARN),               # boundary counts
              _it(26, c=-0.5)]                              # tie with 20
    send, over = hnc.rescore_selection(items, 1000.0, max_items=75)
    assert send[:20] == list(range(20))
    assert send[20:] == [24, 20, 26, 25]
    assert over == []


def test_selection_over_bound_is_least_negative_extras():
    items = [_it(i) for i in range(20)]
    items += [_it(20 + k, c=-0.1 - 0.01 * k) for k in range(10)]
    send, over = hnc.rescore_selection(items, 1000.0, max_items=25)
    assert send[:20] == list(range(20)) and len(send) == 25
    assert send[20:] == [29, 28, 27, 26, 25]
    assert over == [24, 23, 22, 21, 20]
    assert not set(over) & set(range(20))


# ── rescore_brief_news ─────────────────────────────────────────────────────
def _items(n, c=-0.5, start=0):
    return [_it(start + i, c=c, ticker=f"T{(start + i) % 7}") for i in range(n)]


class _Spy:
    def __init__(self, fail_on=(), score=0.4):
        self.calls = []
        self.fail_on = set(fail_on)
        self.score = score

    def __call__(self, items, api_key, timeout=8.0):
        n = len(self.calls)
        self.calls.append([i["title"] for i in items])
        if n in self.fail_on:
            return None
        return [self.score] * len(items)


def test_no_key_returns_copies_and_none():
    items = _items(3)
    out, st = ni.rescore_brief_news(items, "", window_start_ts=0.0, memo={}, now_ts=1.0)
    assert st is None and out == items and out[0] is not items[0]


def test_batches_25_plus_20_and_suppress(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    items = _items(45)
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo={}, now_ts=1.0)
    assert sorted(len(c) for c in spy.calls) == [20, 25]
    assert st["sent"] == 45 and st["selected"] == 45 and st["filtered"] == 45
    assert st["unfiltered"] == 0 and st["reason"] is None
    assert all(o["compound"] == 0.4 and o["label"] == "Positive" for o in out)
    assert [o["title"] for o in out] == [i["title"] for i in items]   # order kept
    assert items[0]["compound"] == -0.5                               # input untouched


def test_failed_batch_leaves_only_its_items_raw(monkeypatch):
    spy = _Spy(fail_on=(0,))
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    memo = {}
    out, st = ni.rescore_brief_news(_items(45), "k", window_start_ts=1000.0, memo=memo, now_ts=50.0)
    assert st["failed_batches"] == 1 and st["reason"] == "error"
    assert st["unfiltered"] in (20, 25) and st["filtered"] == 45 - st["unfiltered"]
    assert memo["failed_at"] == 50.0
    assert sum(1 for o in out if o["compound"] == -0.5) == st["unfiltered"]


def test_memo_prevents_resend_and_new_headline_only(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    memo = {}
    items = _items(30)
    ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=1.0)
    n = len(spy.calls)
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=2.0)
    assert len(spy.calls) == n and st["sent"] == 0 and st["from_memo"] == 30
    assert all(o["compound"] == 0.4 for o in out)
    items2 = items + [_it(999, c=-0.7, ticker="NEW")]
    ni.rescore_brief_news(items2, "k", window_start_ts=1000.0, memo=memo, now_ts=3.0)
    assert spy.calls[n] == ["headline 999"]


def test_backoff_then_retry(monkeypatch):
    spy = _Spy(fail_on=(0,))
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    memo = {}
    items = _items(5)
    ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=100.0)
    assert len(spy.calls) == 1
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=699.0)
    assert len(spy.calls) == 1 and st["reason"] == "backoff" and st["unfiltered"] == 5
    assert all(o["compound"] == -0.5 for o in out)
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=700.0)
    assert len(spy.calls) == 2 and st["unfiltered"] == 0


def test_over_bound_stays_raw_and_not_memoized(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    monkeypatch.setattr(ni, "_RESCORE_MAX_ITEMS", 22)
    items = _items(20, c=0.5) + [_it(20 + k, c=-0.2 - 0.01 * k, ticker="X") for k in range(5)]
    memo = {}
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=1.0)
    assert st["reason"] == "over_bound" and st["unfiltered"] == 3 and st["selected"] == 25
    sent_titles = {t for c in spy.calls for t in c}
    for k in (20, 21, 22):
        assert items[k]["title"] not in sent_titles
        assert out[k]["compound"] == items[k]["compound"]
        assert ("X", items[k]["title"].lower()[:70]) not in memo["scores"]


def test_suppress_only_never_lowers(monkeypatch):
    monkeypatch.setattr(ni, "llm_score_batch", _Spy(score=-0.9))
    items = _items(3, c=-0.5)
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo={}, now_ts=1.0)
    assert out == items and st["filtered"] == 0


def test_llm_score_batch_max_tokens(monkeypatch):
    seen = {}

    class _Msgs:
        def create(self, **kw):
            seen.update(kw)
            txt = '[{"idx": 0, "score": 0.2}, {"idx": 1, "score": 9}]'
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=txt)])

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    fake = types.SimpleNamespace(Anthropic=_Client, Timeout=lambda t: t)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    res = ni.llm_score_batch(_items(3), "k")
    assert seen["max_tokens"] == 64 + 32 * 3
    assert res == [0.2, None, None]
    ni.llm_score_batch(_items(25), "k")
    assert seen["max_tokens"] == 64 + 32 * 25


def test_llm_score_batch_none_on_exception(monkeypatch):
    class _Client:
        def __init__(self, **kw):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=_Client, Timeout=lambda t: t))
    assert ni.llm_score_batch(_items(2), "k") is None


def test_apply_suppress_only_rules():
    it = {"compound": -0.5, "label": "Negative"}
    assert ni._apply_suppress_only(it, 0.3) == {"compound": 0.3, "label": "Positive"}
    assert it["compound"] == -0.5
    assert ni._apply_suppress_only(it, -0.5) is None
    assert ni._apply_suppress_only(it, -0.9) is None
    assert ni._apply_suppress_only(it, True) is None
    assert ni._apply_suppress_only(it, "0.3") is None
    assert ni._apply_suppress_only(it, 1.5) is None
    assert ni._apply_suppress_only(it, -0.01)["label"] == "Neutral"


# ── curate_news_items(max_items=None) ──────────────────────────────────────
def test_curate_uncapped_prefix_equals_default():
    held = {}
    for k in range(18):
        t = f"T{k:02d}"
        news = [{"title": f"{t} story {j}", "publisher": "Reuters" if j % 2 else "Blog",
                 "providerPublishTime": 1_700_000_000 + (j % 2) * 5} for j in range(4)]
        held[t] = {"news_raw": news}
    held["T01"]["news_raw"].append({"title": "T00 story 0", "publisher": "Reuters",
                                    "providerPublishTime": 1_700_000_000})
    full = curate_news_items(held, max_items=None)
    assert len(full) > 20
    assert full[:20] == curate_news_items(held)


# ── app.py wiring ──────────────────────────────────────────────────────────
def test_home_wiring():
    assert "_held_all = curate_news_items(held_data, max_items=None, per_ticker=True)" in _APP
    assert re.search(r"_news\s*=\s*dedupe_titles\(_held_all\)\[:HELD_NEWS_DISPLAY_COUNT\]", _APP)
    assert re.search(r'st\.session_state\["_sidebar_news"\]\s*=\s*dedupe_titles\(_rescored\)\[:HELD_NEWS_DISPLAY_COUNT\]', _APP)
    home = _APP.split("# Full held headline list")[1].split('st.session_state.pop("_brief_held_news"')[0]
    assert "_news_llm_{" not in home.replace("_news_llm_memo_{", "")
    assert "date.today" not in home
    assert "_news_llm_memo_" in _APP


def test_status_carried_in_synth_bundle():
    assert re.search(r'"_brief_news_rescore_status":\s*st\.session_state\.get\("_brief_news_rescore_status"\)', _APP)
    assert '_b.get("_brief_news_rescore_status")' in _APP
    assert re.search(r"_SYNTH_SCHEMA_VER = 19\b", _APP)


# ── review fix-first round ─────────────────────────────────────────────────
class _PartialSpy:
    def __init__(self):
        self.calls = 0

    def __call__(self, items, api_key, timeout=8.0):
        self.calls += 1
        return [0.4 if i % 2 == 0 else None for i in range(len(items))]


def test_unscored_items_are_memoized_not_resent(monkeypatch):
    spy = _PartialSpy()
    monkeypatch.setattr(ni, "llm_score_batch", spy)
    memo = {}
    items = _items(6)
    out, st1 = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=1.0)
    assert spy.calls == 1 and st1["unfiltered"] == 3 and st1["unfiltered_negative"] == 3
    out, st2 = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo=memo, now_ts=2.0)
    assert spy.calls == 1                       # no resend
    assert st2["unfiltered"] == 3 and st2["sent"] == 0
    assert sum(1 for o in out if o["compound"] == -0.5) == 3


def test_unfiltered_negative_counts_only_negative(monkeypatch):
    monkeypatch.setattr(ni, "llm_score_batch", lambda items, k, timeout=8.0: None)
    items = ([_it(i, c=0.6) for i in range(20)]
             + [_it(20, c=-0.5), _it(21, c=NEWS_SENTIMENT_WARN), _it(22, c=NEWS_SENTIMENT_WARN + 0.01)])
    out, st = ni.rescore_brief_news(items, "k", window_start_ts=1000.0, memo={}, now_ts=1.0)
    assert st["unfiltered"] == 21 + 1 and st["unfiltered_negative"] == 2   # boundary counts


def test_rescore_failure_status():
    items = [_it(0, c=-0.5), _it(1, c=0.6), _it(2, c=-0.3), _it(3, c=0.2)]
    st = ni.rescore_failure_status(items, None)
    assert st["reason"] == "error" and st["unfiltered"] == 4 and st["unfiltered_negative"] == 2
    st = ni.rescore_failure_status(items, 1000.0)
    assert st["selected"] == 4 and st["sent"] == 0 and st["unfiltered_negative"] == 2


def test_stale_rebuild_and_lock_status_wiring():
    assert "_rs_status_live = None" in _APP
    tail = _APP.split('["bundle"]["_daily_brief"] = _daily_brief' + chr(10))[1][:700]
    assert 'st.session_state["_brief_news_rescore_status"] = _rs_status_live' in tail
    assert '["bundle"]["_brief_news_rescore_status"] = _rs_status_live' in tail
    lock = _APP.split("_daily_brief = _brief_snapshot")[1][:400]
    assert '= None' not in lock.split("_brief_built_at")[0]
    assert "_rs_status_live = _rs_status" in _APP
    assert (_APP.index("_rescore_brief(")
            < _APP.index("_rs_status_live = _rs_status")
            < _APP.index('st.session_state["_brief_held_news"] = _brief_news'))
    assert 'startswith("_news_llm_memo_")' in _APP and 'startswith("_news_llm_")' not in _APP


def test_caption_gated_on_negative_and_not_offline():
    i = _APP.index("couldn't check {_rs_n} negative headline")
    block = _APP[i - 500:i]
    assert "not _db_offline" in block and "unfiltered_negative" in block
    cap = _APP[i - 700:i + 200]
    assert '_rs_n = int(_rs_note.get("unfiltered_negative") or 0)' in cap
    assert '.get("unfiltered")' not in cap


def test_lock_status_store_restore_and_pops():
    assert re.search(r'\["_brief_locked_rescore_status"\] = st\.session_state\.get\("_brief_news_rescore_status"\)', _APP)
    lock = _APP.split("_daily_brief = _brief_snapshot")[1][:500]
    assert 'st.session_state["_brief_news_rescore_status"] = st.session_state.get("_brief_locked_rescore_status")' in lock
    n_snap_pops = len(re.findall(r'pop\("_brief_locked_snapshot", None\)', _APP))
    n_stat_pops = len(re.findall(r'pop\("_brief_locked_rescore_status", None\)', _APP))
    assert n_snap_pops >= 2 and n_stat_pops == n_snap_pops
