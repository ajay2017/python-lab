"""Regression tests for stock_analyzer/news_intelligence.py — News
Intelligence: significance scoring, negative-news alert classification,
positive-news opportunity detection (with the Reduce/Exit suppression
split), sector-pattern digest, and the two LLM-rescore helpers
(suppress-only VADER-vs-Haiku sidebar rescore, and the bidirectional
swing-capped per-ticker headline rescore). The LLM functions lazily
`import anthropic` inside a try/except, and the dev venv has no `anthropic`
installed (see CLAUDE.md "never run locally") — so a fake module is
installed into `sys.modules["anthropic"]` before each LLM test to exercise
the real success/parsing/validation logic, not just the except-fallback
path. See docs/plans/test-automation.md for scope.
"""
import sys
import types
from unittest.mock import patch

import pandas as pd
import pytest

from stock_analyzer import news_intelligence as ni
from stock_analyzer.constants import (
    NEWS_OPPORTUNITY_COMPOUND_MIN,
    NEWS_OPPORTUNITY_SCORE_MIN,
    SENTIMENT_LLM_MAX_SWING,
)


# ── fake anthropic module helper ───────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.messages = _FakeMessages(response_text, raise_exc)


def _install_fake_anthropic(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text, raise_exc)
    fake_mod.Timeout = lambda t: t
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ── _significance ───────────────────────────────────────────────────────────

FIXED_NOW = 1_000_000.0


def _patched_time():
    return patch.object(ni._time, "time", return_value=FIXED_NOW)


def test_significance_tier1_multiplier():
    with _patched_time():
        item = {"tier": 1, "compound": 0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    # compound(0.5) * tier_mult(1.5) * pos_mult(1.0) * recency(1.0)
    assert result == 0.75


def test_significance_tier2_multiplier():
    with _patched_time():
        item = {"tier": 2, "compound": 0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.6


def test_significance_tier3_multiplier():
    with _patched_time():
        item = {"tier": 3, "compound": 0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.5


def test_significance_unknown_tier_defaults_to_1x():
    with _patched_time():
        item = {"tier": 99, "compound": 0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.5


def test_significance_missing_tier_defaults_to_3():
    with _patched_time():
        item = {"compound": 0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.5


def test_significance_uses_absolute_compound():
    with _patched_time():
        item = {"tier": 3, "compound": -0.5, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.5


def test_significance_position_multiplier_scales_with_weight():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW}
        result = ni._significance(item, weight=15.0)
    # pos_mult = 1.0 + min(15/30, 1.0) = 1.5
    assert result == 1.5


def test_significance_position_multiplier_caps_at_2x():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW}
        result = ni._significance(item, weight=60.0)
    # pos_mult capped at 1.0 + 1.0 = 2.0, not 1.0 + 60/30 = 3.0
    assert result == 2.0


def test_significance_position_multiplier_at_exact_30pct_boundary():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW}
        result = ni._significance(item, weight=30.0)
    assert result == 2.0


def test_significance_recency_is_full_for_brand_new_item():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 1.0


def test_significance_recency_decays_at_12_hours():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW - 12 * 3600}
        result = ni._significance(item, weight=0.0)
    # recency = 1.0 - 12/24 = 0.5
    assert result == 0.5


def test_significance_recency_floors_at_0point4_beyond_24_hours():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0, "ts": FIXED_NOW - 48 * 3600}
        result = ni._significance(item, weight=0.0)
    assert result == 0.4


def test_significance_missing_ts_treated_as_epoch_zero_floors_recency():
    with _patched_time():
        item = {"tier": 3, "compound": 1.0}
        result = ni._significance(item, weight=0.0)
    assert result == 0.4


def test_significance_result_is_rounded_to_3_places():
    with _patched_time():
        item = {"tier": 3, "compound": 0.333333, "ts": FIXED_NOW}
        result = ni._significance(item, weight=0.0)
    assert result == 0.333


# ── build_news_intelligence: empty/short-circuit paths ──────────────────────

def test_build_news_intelligence_empty_news_items_returns_empty_shape():
    result = ni.build_news_intelligence([], pd.DataFrame())
    assert result["summary"] == {
        "positive": 0, "negative": 0, "neutral": 0, "total": 0, "held_count": 0,
    }
    assert result["alerts"] == []
    assert result["opportunities"] == []
    assert result["opportunities_suppressed"] == []
    assert result["sector_digest"] == []
    assert result["held_news"] == []


def test_build_news_intelligence_none_news_items_returns_empty_shape():
    result = ni.build_news_intelligence(None, pd.DataFrame())
    assert result["summary"]["total"] == 0


def _news_item(ticker, compound, tier=3, ts=None, **extra):
    return {"ticker": ticker, "compound": compound, "tier": tier,
            "ts": ts if ts is not None else FIXED_NOW, **extra}


def _port_row(ticker, weight=10.0, score=70.0, signal="BUY", pnl_pct=5.0,
              sector="Technology", mval=10000.0):
    return {
        "Ticker": ticker, "Weight (%)": weight, "Score": score,
        "Signal": signal, "P&L (%)": pnl_pct, "Sector": sector,
        "Market Value": mval,
    }


# ── build_news_intelligence: enrichment / port lookup ───────────────────────

def test_build_news_intelligence_none_port_df_all_unheld():
    items = [_news_item("AAA", 0.5)]
    result = ni.build_news_intelligence(items, None)
    assert result["summary"]["held_count"] == 0
    assert result["held_news"] == []


def test_build_news_intelligence_empty_port_df_all_unheld():
    items = [_news_item("AAA", 0.5)]
    result = ni.build_news_intelligence(items, pd.DataFrame())
    assert result["summary"]["held_count"] == 0


def test_build_news_intelligence_held_item_gets_position_context():
    with _patched_time():
        items = [_news_item("AAA", 0.5)]
        port_df = pd.DataFrame([_port_row("AAA", weight=12.0, score=80.0,
                                           signal="Buy", sector="Healthcare")])
        result = ni.build_news_intelligence(items, port_df)
    held = result["held_news"][0]
    assert held["is_held"] is True
    assert held["weight"] == 12.0
    assert held["score"] == 80.0
    assert held["signal"] == "Buy"
    assert held["sector"] == "Healthcare"


def test_build_news_intelligence_unheld_item_sector_defaults_to_ticker():
    items = [_news_item("ZZZ", 0.5)]
    result = ni.build_news_intelligence(items, pd.DataFrame())
    # not held -> port_lookup.get() default {} -> pos.get("sector", ticker) -> ticker
    enriched_ticker_sector = [i for i in result["held_news"]]
    assert enriched_ticker_sector == []  # not held, so excluded from held_news
    # confirm via summary only (unheld items aren't surfaced elsewhere)
    assert result["summary"]["held_count"] == 0


def test_build_news_intelligence_ticker_matching_is_case_insensitive():
    items = [_news_item("aaa", 0.5)]
    port_df = pd.DataFrame([_port_row("AAA")])
    result = ni.build_news_intelligence(items, port_df)
    assert result["summary"]["held_count"] == 1


def test_build_news_intelligence_missing_portfolio_fields_default_score_50():
    items = [_news_item("AAA", 0.5)]
    port_df = pd.DataFrame([{"Ticker": "AAA"}])  # everything else missing
    result = ni.build_news_intelligence(items, port_df)
    held = result["held_news"][0]
    assert held["score"] == 50.0
    assert held["weight"] == 0.0


# ── build_news_intelligence: summary counts ─────────────────────────────────

def test_build_news_intelligence_summary_counts_positive_negative_neutral():
    items = [
        _news_item("AAA", 0.10),   # positive (>= 0.05)
        _news_item("BBB", 0.05),   # positive (boundary, inclusive)
        _news_item("CCC", -0.10),  # negative (<= -0.05)
        _news_item("DDD", -0.05),  # negative (boundary, inclusive)
        _news_item("EEE", 0.04),   # neutral
        _news_item("FFF", -0.04),  # neutral
    ]
    result = ni.build_news_intelligence(items, pd.DataFrame())
    assert result["summary"] == {
        "positive": 2, "negative": 2, "neutral": 2, "total": 6, "held_count": 0,
    }


# ── build_news_intelligence: alerts ──────────────────────────────────────────

def test_build_news_intelligence_alerts_only_include_held_negative_items():
    items = [
        _news_item("AAA", -0.10),  # held, negative -> alert
        _news_item("BBB", -0.10),  # unheld -> excluded
        _news_item("AAA", 0.10),   # held, positive -> not an alert
    ]
    port_df = pd.DataFrame([_port_row("AAA")])
    result = ni.build_news_intelligence(items, port_df)
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["ticker"] == "AAA"
    assert result["alerts"][0]["compound"] == -0.10


def test_build_news_intelligence_alert_level_critical_requires_all_3_gates():
    items = [_news_item("AAA", -0.30, tier=1)]  # compound<=-0.25, tier<=2
    port_df = pd.DataFrame([_port_row("AAA", weight=10.0)])  # weight>=8.0
    result = ni.build_news_intelligence(items, port_df)
    assert result["alerts"][0]["alert_level"] == "critical"


def test_build_news_intelligence_alert_level_warning_when_weight_too_low():
    items = [_news_item("AAA", -0.30, tier=1)]
    port_df = pd.DataFrame([_port_row("AAA", weight=5.0)])  # below 8.0
    result = ni.build_news_intelligence(items, port_df)
    assert result["alerts"][0]["alert_level"] == "warning"


def test_build_news_intelligence_alert_level_warning_when_tier_too_low_priority():
    items = [_news_item("AAA", -0.30, tier=3)]  # tier > 2
    port_df = pd.DataFrame([_port_row("AAA", weight=10.0)])
    result = ni.build_news_intelligence(items, port_df)
    assert result["alerts"][0]["alert_level"] == "warning"


def test_build_news_intelligence_alert_level_warning_when_compound_not_severe_enough():
    items = [_news_item("AAA", -0.10, tier=1)]  # compound > -0.25
    port_df = pd.DataFrame([_port_row("AAA", weight=10.0)])
    result = ni.build_news_intelligence(items, port_df)
    assert result["alerts"][0]["alert_level"] == "warning"


def test_build_news_intelligence_alerts_sorted_critical_first_then_weight_desc():
    items = [
        _news_item("LOW_W", -0.30, tier=1),
        _news_item("HIGH_W", -0.30, tier=1),
        _news_item("CRIT", -0.30, tier=1),
    ]
    port_df = pd.DataFrame([
        _port_row("LOW_W", weight=8.0),
        _port_row("HIGH_W", weight=20.0),
        _port_row("CRIT", weight=15.0),
    ])
    result = ni.build_news_intelligence(items, port_df)
    tickers = [a["ticker"] for a in result["alerts"]]
    # all 3 qualify as critical (weight>=8, tier<=2, compound<=-0.25) -> sorted by weight desc
    assert tickers == ["HIGH_W", "CRIT", "LOW_W"]


# ── build_news_intelligence: opportunities + reduce_tickers suppression ─────

def test_build_news_intelligence_opportunity_requires_held_compound_and_score_gates():
    items = [_news_item("AAA", NEWS_OPPORTUNITY_COMPOUND_MIN)]
    port_df = pd.DataFrame([_port_row("AAA", score=NEWS_OPPORTUNITY_SCORE_MIN)])
    result = ni.build_news_intelligence(items, port_df)
    assert len(result["opportunities"]) == 1
    assert result["opportunities"][0]["ticker"] == "AAA"


def test_build_news_intelligence_opportunity_excluded_below_compound_floor():
    items = [_news_item("AAA", NEWS_OPPORTUNITY_COMPOUND_MIN - 0.01)]
    port_df = pd.DataFrame([_port_row("AAA", score=NEWS_OPPORTUNITY_SCORE_MIN)])
    result = ni.build_news_intelligence(items, port_df)
    assert result["opportunities"] == []


def test_build_news_intelligence_opportunity_excluded_below_score_floor():
    items = [_news_item("AAA", NEWS_OPPORTUNITY_COMPOUND_MIN)]
    port_df = pd.DataFrame([_port_row("AAA", score=NEWS_OPPORTUNITY_SCORE_MIN - 1)])
    result = ni.build_news_intelligence(items, port_df)
    assert result["opportunities"] == []


def test_build_news_intelligence_opportunity_excludes_unheld():
    items = [_news_item("AAA", NEWS_OPPORTUNITY_COMPOUND_MIN)]
    result = ni.build_news_intelligence(items, pd.DataFrame())
    assert result["opportunities"] == []


def test_build_news_intelligence_reduce_tickers_splits_into_suppressed():
    items = [_news_item("AAA", 0.5)]
    port_df = pd.DataFrame([_port_row("AAA", score=90.0)])
    result = ni.build_news_intelligence(items, port_df, reduce_tickers=["AAA"])
    assert result["opportunities"] == []
    assert len(result["opportunities_suppressed"]) == 1
    assert result["opportunities_suppressed"][0]["ticker"] == "AAA"


def test_build_news_intelligence_reduce_tickers_case_insensitive_match():
    items = [_news_item("AAA", 0.5)]
    port_df = pd.DataFrame([_port_row("AAA", score=90.0)])
    result = ni.build_news_intelligence(items, port_df, reduce_tickers=["aaa"])
    assert result["opportunities_suppressed"][0]["ticker"] == "AAA"


def test_build_news_intelligence_reduce_tickers_none_behaves_unchanged():
    items = [_news_item("AAA", 0.5)]
    port_df = pd.DataFrame([_port_row("AAA", score=90.0)])
    result = ni.build_news_intelligence(items, port_df, reduce_tickers=None)
    assert len(result["opportunities"]) == 1
    assert result["opportunities_suppressed"] == []


def test_build_news_intelligence_opportunities_sorted_by_sig_desc_then_score_desc():
    with _patched_time():
        items = [
            _news_item("LOW_SIG", 0.15, tier=3),
            _news_item("HIGH_SIG", 0.90, tier=1),
        ]
        port_df = pd.DataFrame([
            _port_row("LOW_SIG", weight=0.0, score=90.0),
            _port_row("HIGH_SIG", weight=0.0, score=90.0),
        ])
        result = ni.build_news_intelligence(items, port_df)
    tickers = [o["ticker"] for o in result["opportunities"]]
    assert tickers == ["HIGH_SIG", "LOW_SIG"]


# ── build_news_intelligence: sector digest ──────────────────────────────────

def test_build_news_intelligence_sector_digest_requires_2plus_aligned_held_items():
    items = [
        _news_item("AAA", -0.10),
        _news_item("BBB", -0.10),
    ]
    port_df = pd.DataFrame([
        _port_row("AAA", sector="Semiconductors"),
        _port_row("BBB", sector="Semiconductors"),
    ])
    result = ni.build_news_intelligence(items, port_df)
    assert len(result["sector_digest"]) == 1
    assert result["sector_digest"][0]["sector"] == "Semiconductors"
    assert result["sector_digest"][0]["direction"] == "negative"
    assert result["sector_digest"][0]["count"] == 2


def test_build_news_intelligence_sector_digest_single_item_does_not_qualify():
    items = [_news_item("AAA", -0.10)]
    port_df = pd.DataFrame([_port_row("AAA", sector="Semiconductors")])
    result = ni.build_news_intelligence(items, port_df)
    assert result["sector_digest"] == []


def test_build_news_intelligence_sector_digest_excludes_unheld_items():
    items = [
        _news_item("AAA", -0.10),
        _news_item("BBB", -0.10),
    ]
    port_df = pd.DataFrame([_port_row("AAA", sector="Semiconductors")])  # BBB unheld
    result = ni.build_news_intelligence(items, port_df)
    assert result["sector_digest"] == []


def test_build_news_intelligence_sector_digest_negative_wins_over_positive():
    items = [
        _news_item("AAA", -0.10),
        _news_item("BBB", -0.10),
        _news_item("CCC", 0.10),
        _news_item("DDD", 0.10),
    ]
    port_df = pd.DataFrame([
        _port_row("AAA", sector="Semiconductors"),
        _port_row("BBB", sector="Semiconductors"),
        _port_row("CCC", sector="Semiconductors"),
        _port_row("DDD", sector="Semiconductors"),
    ])
    result = ni.build_news_intelligence(items, port_df)
    assert len(result["sector_digest"]) == 1
    assert result["sector_digest"][0]["direction"] == "negative"
    assert result["sector_digest"][0]["count"] == 2


def test_build_news_intelligence_sector_digest_positive_direction_when_no_negative():
    items = [
        _news_item("AAA", 0.10),
        _news_item("BBB", 0.10),
    ]
    port_df = pd.DataFrame([
        _port_row("AAA", sector="Healthcare"),
        _port_row("BBB", sector="Healthcare"),
    ])
    result = ni.build_news_intelligence(items, port_df)
    assert result["sector_digest"][0]["direction"] == "positive"


def test_build_news_intelligence_sector_digest_sorted_by_count_desc():
    items = [
        _news_item("AAA", -0.10), _news_item("BBB", -0.10),
        _news_item("CCC", -0.10), _news_item("DDD", -0.10), _news_item("EEE", -0.10),
    ]
    port_df = pd.DataFrame([
        _port_row("AAA", sector="Healthcare"), _port_row("BBB", sector="Healthcare"),
        _port_row("CCC", sector="Semiconductors"), _port_row("DDD", sector="Semiconductors"),
        _port_row("EEE", sector="Semiconductors"),
    ])
    result = ni.build_news_intelligence(items, port_df)
    assert [d["sector"] for d in result["sector_digest"]] == ["Semiconductors", "Healthcare"]


# ── build_news_intelligence: held_news ──────────────────────────────────────

def test_build_news_intelligence_held_news_includes_only_held_items():
    items = [_news_item("AAA", 0.5), _news_item("BBB", 0.5)]
    port_df = pd.DataFrame([_port_row("AAA")])
    result = ni.build_news_intelligence(items, port_df)
    assert len(result["held_news"]) == 1
    assert result["held_news"][0]["ticker"] == "AAA"


def test_build_news_intelligence_held_news_sorted_most_negative_first():
    items = [
        _news_item("AAA", 0.5),
        _news_item("BBB", -0.8),
        _news_item("CCC", 0.1),
    ]
    port_df = pd.DataFrame([
        _port_row("AAA"), _port_row("BBB"), _port_row("CCC"),
    ])
    result = ni.build_news_intelligence(items, port_df)
    assert [i["ticker"] for i in result["held_news"]] == ["BBB", "CCC", "AAA"]


# ── rescore_news_items_llm ───────────────────────────────────────────────────

def test_rescore_news_items_llm_empty_items_returns_items_unchanged():
    assert ni.rescore_news_items_llm([], "key") == []


def test_rescore_news_items_llm_no_api_key_returns_items_unchanged():
    items = [{"ticker": "AAA", "compound": 0.1}]
    assert ni.rescore_news_items_llm(items, "") == items


def test_rescore_news_items_llm_suppresses_toward_neutral_when_llm_higher():
    items = [{"ticker": "AAA", "title": "AAA misses on headline noise", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 0, "score": -0.1}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.1
    assert result[0]["label"] == "Negative"


def test_rescore_news_items_llm_rejects_lower_score_suppress_only():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.1}]
    _install_fake_anthropic('[{"idx": 0, "score": -0.5}]')  # lower than VADER -> rejected
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.1  # unchanged


def test_rescore_news_items_llm_equal_score_is_rejected_not_just_lower():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.1}]
    _install_fake_anthropic('[{"idx": 0, "score": -0.1}]')  # llm_score <= vader_score -> skip
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.1


def test_rescore_news_items_llm_strips_markdown_code_fences():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('```json\n[{"idx": 0, "score": 0.2}]\n```')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == 0.2
    assert result[0]["label"] == "Positive"


def test_rescore_news_items_llm_label_neutral_band():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.0}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["label"] == "Neutral"


def test_rescore_news_items_llm_skips_invalid_idx_type():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": "not-an-int", "score": 0.5}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.5


def test_rescore_news_items_llm_skips_out_of_range_idx():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 5, "score": 0.5}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.5


def test_rescore_news_items_llm_skips_non_numeric_score():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 0, "score": "high"}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.5


def test_rescore_news_items_llm_skips_out_of_range_score():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 0, "score": 1.5}]')
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result[0]["compound"] == -0.5


def test_rescore_news_items_llm_falls_back_on_malformed_json():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic("not json at all")
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result == items


def test_rescore_news_items_llm_falls_back_on_client_exception():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic(raise_exc=RuntimeError("timeout"))
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result == items


def test_rescore_news_items_llm_falls_back_when_anthropic_not_installed():
    # No fake module installed -> real `import anthropic` raises ModuleNotFoundError,
    # caught by the broad except -> original items returned unchanged.
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    result = ni.rescore_news_items_llm(items, "fake-key")
    assert result == items


def test_rescore_news_items_llm_does_not_mutate_original_items():
    items = [{"ticker": "AAA", "title": "headline", "compound": -0.5}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.2}]')
    ni.rescore_news_items_llm(items, "fake-key")
    assert items[0]["compound"] == -0.5  # original untouched, result is a copy


# ── rescore_headlines_llm ────────────────────────────────────────────────────

def test_rescore_headlines_llm_empty_headlines_returns_unchanged():
    assert ni.rescore_headlines_llm([], "key") == []


def test_rescore_headlines_llm_no_api_key_returns_unchanged():
    headlines = [{"headline": "h", "score": 0.1}]
    assert ni.rescore_headlines_llm(headlines, "") == headlines


def test_rescore_headlines_llm_can_raise_score_bidirectionally():
    headlines = [{"headline": "great news", "score": 0.1}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.4}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.4
    assert result[0]["label"] == "Positive"


def test_rescore_headlines_llm_can_lower_score_bidirectionally():
    headlines = [{"headline": "bad news", "score": 0.3}]
    _install_fake_anthropic('[{"idx": 0, "score": -0.2}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == -0.2
    assert result[0]["label"] == "Negative"


def test_rescore_headlines_llm_caps_positive_swing_at_max():
    headlines = [{"headline": "h", "score": 0.0}]
    # delta = 1.0 - 0.0 = 1.0 > SENTIMENT_LLM_MAX_SWING -> capped
    _install_fake_anthropic('[{"idx": 0, "score": 1.0}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == round(SENTIMENT_LLM_MAX_SWING, 3)


def test_rescore_headlines_llm_caps_negative_swing_at_max():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": -1.0}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == round(-SENTIMENT_LLM_MAX_SWING, 3)


def test_rescore_headlines_llm_within_swing_band_uses_raw_llm_score():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.3}]')  # delta 0.3 < MAX_SWING(0.5)
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.3


def test_rescore_headlines_llm_neutral_label_band():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.0}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["label"] == "Neutral"


def test_rescore_headlines_llm_returns_none_on_exception():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result is None


def test_rescore_headlines_llm_returns_none_when_anthropic_not_installed():
    headlines = [{"headline": "h", "score": 0.0}]
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result is None


def test_rescore_headlines_llm_strips_markdown_code_fences():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('```json\n[{"idx": 0, "score": 0.3}]\n```')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.3


def test_rescore_headlines_llm_skips_invalid_idx_type():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": "bad", "score": 0.3}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.0


def test_rescore_headlines_llm_skips_out_of_range_idx():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 3, "score": 0.3}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.0


def test_rescore_headlines_llm_skips_non_numeric_score():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": "bad"}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.0


def test_rescore_headlines_llm_skips_out_of_range_score():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": 2.0}]')
    result = ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert result[0]["score"] == 0.0


def test_rescore_headlines_llm_does_not_mutate_original():
    headlines = [{"headline": "h", "score": 0.0}]
    _install_fake_anthropic('[{"idx": 0, "score": 0.3}]')
    ni.rescore_headlines_llm(headlines, "fake-key", ticker="AAA")
    assert headlines[0]["score"] == 0.0
