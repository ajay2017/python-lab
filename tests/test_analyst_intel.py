"""
Tests for stock_analyzer/analyst_intel.py — Ideas Inbox analyst-coverage
extraction and pure-Python consensus/accuracy derivation. Zero coverage
before this batch. `extract_report`'s real Anthropic call is exercised via a
fake `sys.modules["anthropic"]` module for the 3 documented response shapes,
a non-dict-element filter, markdown-fence stripping, and an exception path;
the guard clauses (`api_key=""`, blank text) return before `import anthropic`
runs and need no mocking. `fetch_anchor_price`'s guard is likewise tested
without mocking the network call.
"""
import sys
import types
from datetime import date, timedelta

import pytest

from stock_analyzer import analyst_intel as ai
from stock_analyzer.constants import (
    ANALYST_ACCURACY_DIRECTION_DAYS, ANALYST_ACCURACY_PT_HIT_PCT,
    ANALYST_CALIBRATION_MIN_CASES, COMPOSITE_BUY,
)

pytestmark = pytest.mark.fast


# ─── fake anthropic module helper ────────────────────────────────────────────

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
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── is_bullish_rating ────────────────────────────────────────────────────────

def test_is_bullish_rating_none_or_empty_false():
    assert ai.is_bullish_rating(None) is False
    assert ai.is_bullish_rating("") is False


def test_is_bullish_rating_recognized_ratings_true():
    assert ai.is_bullish_rating("Buy") is True
    assert ai.is_bullish_rating("Strong Buy") is True
    assert ai.is_bullish_rating("Outperform") is True
    assert ai.is_bullish_rating("BUY") is True


def test_is_bullish_rating_bearish_or_unrecognized_false():
    assert ai.is_bullish_rating("Sell") is False
    assert ai.is_bullish_rating("Hold") is False
    assert ai.is_bullish_rating("Whatever") is False


# ─── extract_report ────────────────────────────────────────────────────────────

def test_extract_report_no_api_key_returns_none_and_sets_error():
    result = ai.extract_report("some article text", api_key="")
    assert result is None
    assert ai.LAST_EXTRACT_ERROR == "no API key configured or empty text"


def test_extract_report_blank_text_returns_none_guard():
    result = ai.extract_report("   ", api_key="fake-key")
    assert result is None
    assert ai.LAST_EXTRACT_ERROR == "no API key configured or empty text"


def test_extract_report_canonical_shape_stamps_article_date():
    raw = '{"article_date": "2026-01-01", "reports": [{"ticker": "SPOT"}, {"ticker": "V", "article_date": "2026-01-02"}]}'
    _install_fake_anthropic(raw)
    result = ai.extract_report("article text", api_key="fake-key")
    assert result[0]["ticker"] == "SPOT"
    assert result[0]["article_date"] == "2026-01-01"
    assert result[1]["article_date"] == "2026-01-02"  # already has its own -- not overwritten


def test_extract_report_bare_single_dict_wrapped_into_list():
    raw = '{"ticker": "AAPL", "company": "Apple"}'
    _install_fake_anthropic(raw)
    result = ai.extract_report("article text", api_key="fake-key")
    assert result == [{"ticker": "AAPL", "company": "Apple"}]


def test_extract_report_raw_array_used_directly():
    raw = '[{"ticker": "AAPL"}, {"ticker": "MSFT"}]'
    _install_fake_anthropic(raw)
    result = ai.extract_report("article text", api_key="fake-key")
    assert len(result) == 2


def test_extract_report_non_dict_element_filtered_out():
    raw = '{"article_date": "2026-01-01", "reports": [{"ticker": "AAPL"}, "not a dict", 42]}'
    _install_fake_anthropic(raw)
    result = ai.extract_report("article text", api_key="fake-key")
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_extract_report_markdown_fenced_response_stripped():
    raw = "```json\n" + '{"article_date": "2026-01-01", "reports": [{"ticker": "AAPL"}]}' + "\n```"
    _install_fake_anthropic(raw)
    result = ai.extract_report("article text", api_key="fake-key")
    assert result[0]["ticker"] == "AAPL"


def test_extract_report_exception_sets_last_error_and_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    result = ai.extract_report("article text", api_key="fake-key")
    assert result is None
    assert ai.LAST_EXTRACT_ERROR.startswith("RuntimeError")


# ─── derive_consensus ──────────────────────────────────────────────────────────

def test_derive_consensus_empty_or_none_all_none():
    empty = {"consensus_rating": None, "avg_pt": None, "high_pt": None, "low_pt": None}
    assert ai.derive_consensus([]) == empty
    assert ai.derive_consensus(None) == empty


def test_derive_consensus_mix_of_ratings_tallies_correctly():
    analysts = [
        {"rating": "Buy"}, {"rating": "Hold"}, {"rating": "Sell"},
        {"rating": "gibberish rating"},
    ]
    result = ai.derive_consensus(analysts)
    # n_rated=3 (gibberish excluded); bull_frac=bear_frac=1/3 (<0.5 each), and
    # neut_n(1) >= max(bull_n, bear_n)(1) -> "Hold", not "Mixed".
    assert result["consensus_rating"] == "Hold (1 Buy / 1 Hold / 1 Sell)"


def test_derive_consensus_strong_buy_boundary():
    analysts = [{"rating": "Buy"}] * 4 + [{"rating": "Sell"}]
    result = ai.derive_consensus(analysts)
    assert result["consensus_rating"] == "Strong Buy (4 Buy / 0 Hold / 1 Sell)"


def test_derive_consensus_buy_label_below_strong_buy_threshold():
    analysts = [{"rating": "Buy"}] * 3 + [{"rating": "Sell"}] * 2
    result = ai.derive_consensus(analysts)
    assert result["consensus_rating"] == "Buy (3 Buy / 0 Hold / 2 Sell)"


def test_derive_consensus_sell_label_when_bear_frac_at_or_above_half():
    analysts = [{"rating": "Sell"}] * 3 + [{"rating": "Hold"}]
    result = ai.derive_consensus(analysts)
    assert result["consensus_rating"] == "Sell (0 Buy / 1 Hold / 3 Sell)"


def test_derive_consensus_hold_label_when_neutral_dominates():
    analysts = [{"rating": "Buy"}, {"rating": "Sell"}, {"rating": "Hold"}, {"rating": "Hold"}]
    result = ai.derive_consensus(analysts)
    assert result["consensus_rating"] == "Hold (1 Buy / 2 Hold / 1 Sell)"


def test_derive_consensus_mixed_label_when_tie_and_neutral_not_dominant():
    analysts = [{"rating": "Buy"}] * 2 + [{"rating": "Sell"}] * 2 + [{"rating": "Hold"}]
    result = ai.derive_consensus(analysts)
    assert result["consensus_rating"] == "Mixed (2 Buy / 1 Hold / 2 Sell)"


def test_derive_consensus_nan_price_target_excluded():
    analysts = [{"rating": "Buy", "price_target": float("nan")}, {"rating": "Buy", "price_target": 100.0}]
    result = ai.derive_consensus(analysts)
    assert result["avg_pt"] == 100.0


def test_derive_consensus_non_numeric_price_target_excluded():
    analysts = [{"rating": "Buy", "price_target": "not-a-number"}, {"rating": "Buy", "price_target": 100.0}]
    result = ai.derive_consensus(analysts)
    assert result["avg_pt"] == 100.0


def test_derive_consensus_avg_high_low_pt_computed_correctly():
    analysts = [{"rating": "Buy", "price_target": 100.0}, {"rating": "Buy", "price_target": 200.0}]
    result = ai.derive_consensus(analysts)
    assert result["avg_pt"] == 150.0
    assert result["high_pt"] == 200.0
    assert result["low_pt"] == 100.0


def test_derive_consensus_no_valid_targets_all_none():
    analysts = [{"rating": "Buy"}]
    result = ai.derive_consensus(analysts)
    assert result["avg_pt"] is None
    assert result["high_pt"] is None
    assert result["low_pt"] is None


# ─── trustworthy_composite ──────────────────────────────────────────────────

def test_trustworthy_composite_none_bundle_returns_none():
    assert ai.trustworthy_composite(None) is None


def test_trustworthy_composite_non_dict_bundle_returns_none():
    assert ai.trustworthy_composite("not-a-dict") is None


def test_trustworthy_composite_empty_dict_returns_none():
    assert ai.trustworthy_composite({}) is None


def test_trustworthy_composite_missing_total_returns_none():
    assert ai.trustworthy_composite({"fundamentals_available": True, "val_available": True}) is None


def test_trustworthy_composite_non_numeric_total_returns_none():
    assert ai.trustworthy_composite({"total": "not-a-number"}) is None


def test_trustworthy_composite_nan_total_returns_none():
    assert ai.trustworthy_composite({"total": float("nan")}) is None


def test_trustworthy_composite_fundamentals_unavailable_rejected():
    bundle = {"total": 72.0, "fundamentals_available": False, "val_available": True}
    assert ai.trustworthy_composite(bundle) is None


def test_trustworthy_composite_val_unavailable_rejected():
    bundle = {"total": 72.0, "fundamentals_available": True, "val_available": False}
    assert ai.trustworthy_composite(bundle) is None


def test_trustworthy_composite_legacy_bundle_missing_both_flags_accepted():
    # Neither flag present — defaults to True (not rejected) so a legacy bundle
    # from before the flags existed is not silently discarded.
    assert ai.trustworthy_composite({"total": 68.0}) == 68.0


def test_trustworthy_composite_happy_path_returns_float():
    bundle = {"total": 81.5, "fundamentals_available": True, "val_available": True}
    assert ai.trustworthy_composite(bundle) == 81.5


def test_trustworthy_composite_int_total_returns_float():
    bundle = {"total": 70, "fundamentals_available": True, "val_available": True}
    result = ai.trustworthy_composite(bundle)
    assert result == 70.0
    assert isinstance(result, float)


def test_trustworthy_composite_stale_as_of_set_rejected():
    bundle = {"total": 72.0, "stale_as_of": "2026-07-10"}
    assert ai.trustworthy_composite(bundle) is None


def test_trustworthy_composite_stale_as_of_none_accepted():
    bundle = {"total": 72.0, "stale_as_of": None}
    assert ai.trustworthy_composite(bundle) == 72.0


def test_trustworthy_composite_fund_cache_age_over_limit_rejected():
    from stock_analyzer.constants import GROW_TODAY_MAX_FUND_AGE_DAYS
    bundle = {"total": 72.0, "fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS + 1}
    assert ai.trustworthy_composite(bundle) is None


def test_trustworthy_composite_fund_cache_age_at_limit_accepted():
    from stock_analyzer.constants import GROW_TODAY_MAX_FUND_AGE_DAYS
    bundle = {"total": 72.0, "fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS}
    assert ai.trustworthy_composite(bundle) == 72.0


def test_trustworthy_composite_fund_cache_age_none_accepted():
    bundle = {"total": 72.0, "fund_cache_age_days": None}
    assert ai.trustworthy_composite(bundle) == 72.0


def test_trustworthy_composite_inf_total_rejected():
    assert ai.trustworthy_composite({"total": float("inf")}) is None


def test_trustworthy_composite_negative_total_rejected():
    assert ai.trustworthy_composite({"total": -1.0}) is None


def test_trustworthy_composite_over_100_total_rejected():
    assert ai.trustworthy_composite({"total": 100.1}) is None


def test_trustworthy_composite_zero_total_accepted():
    assert ai.trustworthy_composite({"total": 0.0}) == 0.0


def test_trustworthy_composite_100_total_accepted():
    assert ai.trustworthy_composite({"total": 100.0}) == 100.0


# ─── classify_call ─────────────────────────────────────────────────────────────

def _fetch_window_ok(close=110.0, high=120.0):
    def _f(ticker, start, end):
        return {"close": close, "high": high}
    return _f


def test_classify_call_no_article_date_no_anchor():
    row = {"article_date": None, "price_at_article_date": 100.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok())
    assert result == {"status": "no_anchor"}


def test_classify_call_unparseable_price_no_anchor():
    row = {"article_date": date(2026, 6, 1), "price_at_article_date": "bad", "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok())
    assert result == {"status": "no_anchor"}


def test_classify_call_nan_price_no_anchor():
    row = {"article_date": date(2026, 6, 1), "price_at_article_date": float("nan"), "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok())
    assert result == {"status": "no_anchor"}


def test_classify_call_nonpositive_price_no_anchor():
    row = {"article_date": date(2026, 6, 1), "price_at_article_date": 0.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok())
    assert result == {"status": "no_anchor"}


def test_classify_call_blank_consensus_no_consensus():
    row = {"article_date": date(2026, 6, 1), "price_at_article_date": 100.0, "consensus_rating": ""}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok())
    assert result == {"status": "no_consensus"}


def test_classify_call_pending_when_within_direction_window():
    today = date(2026, 6, 10)
    article_date = today - timedelta(days=ANALYST_ACCURACY_DIRECTION_DAYS - 1)
    row = {"article_date": article_date, "price_at_article_date": 100.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, today, _fetch_window_ok())
    assert result == {"status": "pending"}


def test_classify_call_sell_date_after_used_regardless_of_30day_rule():
    today = date(2026, 6, 2)  # only 1 day after article -- would be "pending" without a sale
    article_date = date(2026, 6, 1)
    sell_date = date(2026, 6, 5)
    row = {"article_date": article_date, "price_at_article_date": 100.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, sell_date, today, _fetch_window_ok())
    assert result["window"] == "sold"
    assert result["window_end"] == sell_date


def test_classify_call_no_price_when_fetch_returns_none():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), lambda t, s, e: None)
    assert result == {"status": "no_price"}


def test_classify_call_no_price_when_close_is_none():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Buy (1/0/0)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), lambda t, s, e: {"close": None, "high": 100})
    assert result == {"status": "no_price"}


def test_classify_call_bullish_positive_return_hit():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Buy (5 Buy / 0 Hold / 0 Sell)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok(close=110.0))
    assert result["status"] == "hit"


def test_classify_call_bearish_negative_return_hit():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Sell (0 Buy / 0 Hold / 5 Sell)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok(close=90.0))
    assert result["status"] == "hit"


def test_classify_call_mismatched_direction_miss():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Buy (5 Buy / 0 Hold / 0 Sell)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok(close=90.0))
    assert result["status"] == "miss"


def test_classify_call_sell_tally_does_not_false_positive_as_bullish():
    # consensus label is "Sell" even though the parenthetical tally contains
    # the literal substring "Buy" -- only the LEADING label is matched.
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0, "consensus_rating": "Sell (0 Buy / 0 Hold / 5 Sell)"}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok(close=110.0))
    assert result["status"] == "miss"  # bearish call, positive return -> miss


def test_classify_call_pt_hit_boundary():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0,
           "consensus_rating": "Buy (5/0/0)", "avg_pt": 100.0}
    at_boundary = ai.classify_call(row, None, date(2026, 7, 29),
                                    _fetch_window_ok(close=105.0, high=100.0 * ANALYST_ACCURACY_PT_HIT_PCT))
    just_below = ai.classify_call(row, None, date(2026, 7, 29),
                                   _fetch_window_ok(close=105.0, high=100.0 * ANALYST_ACCURACY_PT_HIT_PCT - 0.01))
    assert at_boundary["pt_hit"] is True
    assert just_below["pt_hit"] is False


def test_classify_call_pt_proximity_can_exceed_100():
    row = {"article_date": date(2026, 1, 1), "price_at_article_date": 100.0,
           "consensus_rating": "Buy (5/0/0)", "avg_pt": 100.0}
    result = ai.classify_call(row, None, date(2026, 7, 29), _fetch_window_ok(close=105.0, high=150.0))
    assert result["pt_proximity"] == pytest.approx(150.0)


# ─── consensus_side ────────────────────────────────────────────────────────────

def test_consensus_side_none_and_blank_return_none():
    assert ai.consensus_side(None) is None
    assert ai.consensus_side("") is None
    assert ai.consensus_side("   ") is None


def test_consensus_side_buy_labels():
    assert ai.consensus_side("Buy (5/0/0)") == "buy"
    assert ai.consensus_side("Strong Buy (5/0/0)") == "buy"


def test_consensus_side_sell_label():
    assert ai.consensus_side("Sell (0/0/5)") == "sell"


def test_consensus_side_hold_and_mixed_are_neutral_not_forced_onto_an_axis():
    assert ai.consensus_side("Hold (2/3/0)") == "neutral"
    assert ai.consensus_side("Mixed (2/1/2)") == "neutral"


def test_consensus_side_matches_leading_label_not_the_tally_substring():
    # The parenthetical tally always contains the literal word "Buy" —
    # a bare substring match would false-positive every Sell/Hold row.
    assert ai.consensus_side("Sell (3 Buy / 0 Hold / 5 Sell)") == "sell"
    assert ai.consensus_side("Hold (3 Buy / 2 Hold / 0 Sell)") == "neutral"


# ─── calibration_matrix ─────────────────────────────────────────────────────────

def _cal_row(consensus, composite, ret_pct=1.0, directional_hit=True, status="hit"):
    return {
        "status": status, "consensus_rating": consensus,
        "composite_score_at_save": composite,
        "ret_pct": ret_pct, "directional_hit": directional_hit,
    }


def test_calibration_matrix_empty_input_never_raises():
    out = ai.calibration_matrix([])
    assert out["n_classifiable"] == 0
    for cell in out["cells"].values():
        assert cell["n"] == 0
        assert cell["avg_ret_pct"] is None
    out_none = ai.calibration_matrix(None)
    assert out_none["n_classifiable"] == 0


def test_calibration_matrix_engine_axis_boundary_is_inclusive():
    # composite_score_at_save == COMPOSITE_BUY classifies as engine-bullish
    # (>=), matching the app's own entry-gate boundary convention.
    at_boundary = ai.calibration_matrix([_cal_row("Buy (5/0/0)", COMPOSITE_BUY)])
    assert at_boundary["cells"]["buy_agree"]["n"] == 1
    assert at_boundary["cells"]["buy_disagree"]["n"] == 0

    sell_at_boundary = ai.calibration_matrix([_cal_row("Sell (0/0/5)", COMPOSITE_BUY)])
    assert sell_at_boundary["cells"]["sell_disagree"]["n"] == 1
    assert sell_at_boundary["cells"]["sell_agree"]["n"] == 0


def test_calibration_matrix_four_quadrant_placement():
    out = ai.calibration_matrix([
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5),   # buy_agree
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5),   # buy_disagree
        _cal_row("Sell (0/0/5)", COMPOSITE_BUY - 5),  # sell_agree
        _cal_row("Sell (0/0/5)", COMPOSITE_BUY + 5),  # sell_disagree
    ])
    assert out["cells"]["buy_agree"]["n"] == 1
    assert out["cells"]["buy_disagree"]["n"] == 1
    assert out["cells"]["sell_agree"]["n"] == 1
    assert out["cells"]["sell_disagree"]["n"] == 1
    assert out["n_classifiable"] == 4


def test_calibration_matrix_neutral_excluded_never_forced_onto_sell():
    out = ai.calibration_matrix([
        _cal_row("Hold (2/3/0)", COMPOSITE_BUY + 5),
        _cal_row("Mixed (2/1/2)", COMPOSITE_BUY - 5),
    ])
    assert out["n_excluded_neutral"] == 2
    assert out["n_classifiable"] == 0
    for cell in out["cells"].values():
        assert cell["n"] == 0


def test_calibration_matrix_missing_engine_score_excluded_not_treated_as_below_gate():
    out = ai.calibration_matrix([
        _cal_row("Buy (5/0/0)", None),
        _cal_row("Buy (5/0/0)", float("nan")),
    ])
    assert out["n_excluded_no_engine_score"] == 2
    assert out["n_classifiable"] == 0


def test_calibration_matrix_only_evaluable_rows_count():
    out = ai.calibration_matrix([
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5, status="pending"),
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5, status="no_anchor"),
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5, status="no_price"),
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5, status="no_consensus"),
    ])
    assert out["n_classifiable"] == 0
    assert out["n_excluded_neutral"] == 0
    assert out["n_excluded_no_engine_score"] == 0


def test_calibration_matrix_who_was_right_partition_invariant_buy_side():
    out = ai.calibration_matrix([
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5, directional_hit=True),   # analyst right
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5, directional_hit=False),  # engine right
        _cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5, directional_hit=False),  # engine right
    ])
    cell = out["cells"]["buy_disagree"]
    assert cell["n"] == 3
    assert cell["analyst_right"] == 1
    assert cell["engine_right"] == 2
    assert cell["engine_right"] + cell["analyst_right"] == cell["n"]


def test_calibration_matrix_who_was_right_partition_invariant_sell_side():
    out = ai.calibration_matrix([
        _cal_row("Sell (0/0/5)", COMPOSITE_BUY + 5, directional_hit=True),  # analyst right
        _cal_row("Sell (0/0/5)", COMPOSITE_BUY + 5, directional_hit=False), # engine right
    ])
    cell = out["cells"]["sell_disagree"]
    assert cell["n"] == 2
    assert cell["analyst_right"] == 1
    assert cell["engine_right"] == 1
    assert cell["engine_right"] + cell["analyst_right"] == cell["n"]


def test_calibration_matrix_min_cases_gate_on_disagreement_cells():
    below = ai.calibration_matrix(
        [_cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5) for _ in range(ANALYST_CALIBRATION_MIN_CASES - 1)]
    )
    at_threshold = ai.calibration_matrix(
        [_cal_row("Buy (5/0/0)", COMPOSITE_BUY - 5) for _ in range(ANALYST_CALIBRATION_MIN_CASES)]
    )
    assert below["cells"]["buy_disagree"]["verdict_shown"] is False
    assert at_threshold["cells"]["buy_disagree"]["verdict_shown"] is True


def test_calibration_matrix_agree_cells_have_no_min_cases_gate_key():
    # Agree cells never disagree, so there is no "who was right" to gate —
    # confirm the key simply isn't present rather than silently False.
    out = ai.calibration_matrix([_cal_row("Buy (5/0/0)", COMPOSITE_BUY + 5)])
    assert "verdict_shown" not in out["cells"]["buy_agree"]


def test_calibration_matrix_never_reads_a_valuation_or_gate_module():
    """Awareness-only invariant: this function must have no import-time or
    call-time path into scoring/valuation/gate modules. Checked against the
    compiled bytecode's referenced names (`co_names`), not raw source text —
    a substring scan of the source would false-positive on this docstring's
    own prose explaining the invariant."""
    forbidden = ("valuation", "scoring", "risk_advisor", "watchlist_advisor")
    names = ai.calibration_matrix.__code__.co_names
    consts = ai.calibration_matrix.__code__.co_consts
    for f in forbidden:
        assert f not in names
        assert not any(isinstance(c, str) and f in c for c in consts if c is not ai.calibration_matrix.__doc__)


# ─── fetch_anchor_price ────────────────────────────────────────────────────────

def test_fetch_anchor_price_none_ticker_returns_none():
    assert ai.fetch_anchor_price(None, date(2026, 1, 1)) is None


def test_fetch_anchor_price_none_article_date_returns_none():
    assert ai.fetch_anchor_price("AAPL", None) is None


# ─── valid_anchor_price ────────────────────────────────────────────────────────
# The NaN case is the whole point of this validator: `float(float('nan'))`
# SUCCEEDS, so the bare `try: float(v) except (TypeError, ValueError)` this
# replaced let NaN through into price_at_article_date — the denominator of
# every Scorecard ret_pct, and invalid JSON on the Supabase write. A missing
# price read out of a pandas column arrives as NaN, not None.

def test_valid_anchor_price_none_returns_none():
    assert ai.valid_anchor_price(None) is None


def test_valid_anchor_price_nan_returns_none():
    assert ai.valid_anchor_price(float("nan")) is None


def test_valid_anchor_price_numpy_nan_returns_none():
    np = pytest.importorskip("numpy")
    assert ai.valid_anchor_price(np.nan) is None


def test_valid_anchor_price_nan_from_pandas_column_returns_none():
    """The real arrival path: a missing value in a DataFrame column is NaN."""
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"Ticker": ["AAPL"], "Price": [None]})
    assert ai.valid_anchor_price(df.iloc[0]["Price"]) is None


def test_valid_anchor_price_positive_infinity_returns_none():
    assert ai.valid_anchor_price(float("inf")) is None


def test_valid_anchor_price_negative_infinity_returns_none():
    assert ai.valid_anchor_price(float("-inf")) is None


def test_valid_anchor_price_zero_returns_none():
    assert ai.valid_anchor_price(0) is None


def test_valid_anchor_price_negative_returns_none():
    assert ai.valid_anchor_price(-12.5) is None


def test_valid_anchor_price_non_numeric_string_returns_none():
    assert ai.valid_anchor_price("not-a-price") is None


def test_valid_anchor_price_numeric_string_is_coerced():
    assert ai.valid_anchor_price("143.25") == pytest.approx(143.25)


def test_valid_anchor_price_int_returns_float():
    out = ai.valid_anchor_price(60)
    assert out == pytest.approx(60.0)
    assert isinstance(out, float)


def test_valid_anchor_price_passes_a_real_price_through_unchanged():
    assert ai.valid_anchor_price(935.39) == pytest.approx(935.39)


def test_valid_anchor_price_smallest_positive_price_is_accepted():
    """Sub-penny prices are real (delisted/OTC names) — only <= 0 is rejected."""
    assert ai.valid_anchor_price(0.0001) == pytest.approx(0.0001)


def test_fetch_anchor_price_shares_the_validator_so_the_paths_cannot_drift():
    """fetch_anchor_price must route through valid_anchor_price, not re-implement
    the rule — the two are written to the same DB column from different entry
    points (save-time resolver vs. the Fetch button / weekly backfill)."""
    import inspect
    assert "valid_anchor_price" in inspect.getsource(ai.fetch_anchor_price)


def test_valid_anchor_price_huge_int_does_not_raise():
    """float() raises OverflowError (not ValueError) on an arbitrary-precision
    int above ~1e308; the app-side call site has no try/except, so the
    "never raises" guarantee has to hold here."""
    assert ai.valid_anchor_price(10 ** 400) is None


def test_trustworthy_composite_huge_int_total_does_not_raise():
    assert ai.trustworthy_composite({"total": 10 ** 400}) is None
