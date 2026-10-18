"""G-30 (chunk 7b / N-B2 of docs/plans/shared-entry-check.md, 2026-10-18):
the negative-news Gate Suppression Ledger rows.

  * add lane  -- `entry_held_adds` carries G-30 ONLY when the binding hold is news
  * new-pick  -- `news_skipped` carries G-30 ONLY for the reconciled
                 "Skip -- Negative News" verdict (legacy-conflicted and
                 "Signals Disagree" skips write no row)
"""
from datetime import date

import pytest

from stock_analyzer import daily_briefing, gate_ledger
from stock_analyzer.constants import COMPOSITE_BUY, NEWS_SENTIMENT_NEGATIVE
from stock_analyzer.gate_ledger import build_suppression_rows
from stock_analyzer.gate_ledger_readout import readout_footnotes
from stock_analyzer.gate_registry import GATE_IDS
from stock_analyzer.signal_reconciliation import reconcile_signals
from tests.test_add_entry_check import _T, _iso, _news, _run as _run_add
from tests.test_grow_news_skip import _bundle, _neg_news, _run as _run_pick

pytestmark = pytest.mark.fast

_D = date(2026, 10, 18)


def _rows(grow):
    return build_suppression_rows(grow, rec_date=_D, source="app", tone="bull")


def _g30(rows):
    return [r for r in rows if r["gate_id"] == "G-30"]


# -- unit: ledger over hand-built items ---------------------------------------

def _item(gate_id="G-30", ticker="AAA", **kw):
    base = {"ticker": ticker, "gate_id": gate_id, "counterfactual": True,
            "gate_value": -0.3, "gate_threshold": NEWS_SENTIMENT_NEGATIVE,
            "price": 12.5, "composite_score": 80.0, "momentum_score": None,
            "reason": "r"}
    base.update(kw)
    return base


def test_add_lane_item_yields_one_add_winner_row():
    rows = _g30(_rows({"entry_held_adds": [_item()]}))
    assert len(rows) == 1
    r = rows[0]
    assert r["lane"] == "add_winner" and r["counterfactual"] is True
    assert r["gate_value"] == -0.3 and r["gate_threshold"] == NEWS_SENTIMENT_NEGATIVE
    assert r["price_at_suppress"] == 12.5


def test_new_pick_item_yields_one_new_pick_row_with_price():
    rows = _g30(_rows({"news_skipped": [_item(price=44.0)]}))
    assert len(rows) == 1 and rows[0]["lane"] == "new_pick"
    assert rows[0]["price_at_suppress"] == 44.0


def test_unlabelled_items_write_no_row():
    g = {"entry_held_adds": [_item(gate_id=None)], "news_skipped": [_item(gate_id=None)]}
    assert _rows(g) == []


def test_buckets_are_lane_mapped_and_registered():
    assert gate_ledger._BUCKET_LANES["entry_held_adds"] == "add_winner"
    assert gate_ledger._BUCKET_LANES["news_skipped"] == "new_pick"
    assert "G-30" in GATE_IDS


def test_readout_footnote_present():
    txt = " ".join(readout_footnotes("G-30"))
    assert "2026-10-19" in txt and "F-309" in txt and "VADER" in txt and "bull-day" in txt
    assert "2026-10-18" not in txt  # a Sunday: no row can be dated then
    assert "firm band" in txt and "12 months" in txt


# -- the label G-30 keys on is the real reconcile_signals output --------------

def test_negative_news_label_matches_reconcile_signals():
    rv = reconcile_signals(
        ticker="X", momentum_score=COMPOSITE_BUY + 10, momentum_signal="Buy",
        composite_score=COMPOSITE_BUY + 10, composite_signal="Buy",
        is_held=False, is_mover=False, earnings_days=None,
        news_sentiment=NEWS_SENTIMENT_NEGATIVE - 0.2, momentum_available=True)
    assert rv["verdict"] == "skip"
    assert rv["label"] == daily_briefing._NEGATIVE_NEWS_SKIP_LABEL


# -- add lane through _grow_today: only a NEWS binding hold is ledgered -------

def test_held_winner_with_negative_news_writes_a_g30_add_row():
    g = _run_add(news=_news(-0.30))
    assert [i["ticker"] for i in g["entry_held_adds"]] == [_T]
    rows = _g30(_rows(g))
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == _T and r["lane"] == "add_winner" and r["counterfactual"] is True
    assert r["gate_value"] == pytest.approx(-0.30)
    assert r["gate_threshold"] == NEWS_SENTIMENT_NEGATIVE
    assert r["price_at_suppress"] == 100.0 and r["composite_score"] is not None
    assert r["momentum_score"] is None


def test_stale_binding_hold_writes_no_row():
    g = _run_add(held_data={_T: {"stale_as_of": "2026-10-10"}}, news=_news(-0.30))
    assert g["entry_held_adds"][0]["check"] == "stale"
    assert _g30(_rows(g)) == []


def test_earnings_binding_hold_writes_no_row():
    g = _run_add(earnings={_T: _iso(1)})
    assert g["entry_held_adds"][0]["check"] == "earnings"
    assert _g30(_rows(g)) == []


def test_check_failed_binding_hold_writes_no_row(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("x")
    monkeypatch.setattr(daily_briefing.entry_eligibility, "entry_check", boom)
    g = _run_add(news=_news(-0.30))
    assert g["entry_held_adds"][0]["check"] == "check_failed"
    assert _g30(_rows(g)) == []


# -- new-pick lane through _grow_today ----------------------------------------

def test_not_held_candidate_with_negative_news_writes_a_g30_new_pick_row():
    g = _run_pick([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
                  _neg_news("NEW"), {"NEW": _bundle()})
    item = g["news_skipped"][0]
    assert item["gate_id"] == "G-30" and item["counterfactual"] is True
    assert item["gate_threshold"] == NEWS_SENTIMENT_NEGATIVE
    assert item["gate_value"] == pytest.approx(NEWS_SENTIMENT_NEGATIVE - 0.3)
    rows = _g30(_rows(g))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NEW" and rows[0]["lane"] == "new_pick"
    assert rows[0]["price_at_suppress"] > 0 and rows[0]["reason"]


def test_signals_disagree_skip_writes_no_row(monkeypatch):
    # Not reachable with a real composite >= the buy bar, so force the reconciled
    # verdict: a skip that is NOT the negative-news one must never be ledgered.
    real = daily_briefing.reconcile_signals

    def fake(**kw):
        out = dict(real(**kw))
        out.update(verdict="skip", label="❌ Skip — Signals Disagree",
                   one_liner="signals disagree")
        return out
    monkeypatch.setattr(daily_briefing, "reconcile_signals", fake)
    g = _run_pick([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
                  [], {"NEW": _bundle()})
    assert [i["ticker"] for i in g["news_skipped"]] == ["NEW"]
    assert g["news_skipped"][0].get("gate_id") is None
    assert _g30(_rows(g)) == []


def test_legacy_conflicted_skip_writes_no_row(monkeypatch):
    # The legacy-conflicted branch: reconciled verdict is not a skip, but the
    # legacy xref verdict is "conflicted". Its reason may not be news -> no row.
    real = daily_briefing._cross_reference

    def fake(*a, **kw):
        x = dict(real(*a, **kw))
        x.update(verdict="conflicted", verdict_label="❌ Conflicted",
                 conflicts=["momentum vs composite"])
        return x
    monkeypatch.setattr(daily_briefing, "_cross_reference", fake)
    g = _run_pick([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
                  [], {"NEW": _bundle()})
    assert [i["ticker"] for i in g["news_skipped"]] == ["NEW"]
    assert g["news_skipped"][0].get("gate_id") is None
    assert _g30(_rows(g)) == []


def test_new_pick_gate_value_is_the_mean_of_the_headlines():
    # Review follow-up: pin the multi-headline AVERAGE on the new-pick path --
    # mutating the shared _ticker_news_avg to "first headline" survived the suite.
    news = [{"ticker": "NEW", "title": "NEW slides on probe", "compound": -0.10, "label": "Negative"},
            {"ticker": "NEW", "title": "NEW plunges on fraud charge", "compound": -0.50, "label": "Negative"}]
    g = _run_pick([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Energy"}],
                  news, {"NEW": _bundle()})
    assert [i["ticker"] for i in g["news_skipped"]] == ["NEW"]
    assert g["news_skipped"][0]["gate_value"] == pytest.approx(-0.30)
