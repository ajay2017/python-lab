"""
Tests for stock_analyzer/decision_bucket.py — the documented source-of-truth
for Act Today vs Awareness bucketing (previously zero test coverage). Also
pins the "SPCX split-brain" reconciliation: a ticker can't show both an
actionable reduce AND a contradictory "hold for now" news card.
"""
from stock_analyzer.decision_bucket import (
    classify_bucket,
    split_defensive,
    reduce_call_items,
    reduce_call_tickers,
    all_flagged_tickers,
    suppress_orphans_under_reduce_call,
    bucket_act_by_type,
    EXIT_KINDS,
    TRIM_KINDS,
    _REDUCE_ACT_KINDS,
)


# ─── classify_bucket ──────────────────────────────────────────────────────────

def test_act_kind_stop_breach_classifies_act():
    assert classify_bucket({"_source": "act", "kind": "stop_breach"}) == "act"


def test_act_kind_deterioration_exit_classifies_act():
    # Explicitly called out in the module docstring — its card language is
    # "ACT: Reduce aggressively" and must land in Act, not Monitoring.
    assert classify_bucket({"_source": "act", "kind": "deterioration_exit"}) == "act"


def test_act_kind_deterioration_trim_classifies_act():
    assert classify_bucket({"_source": "act", "kind": "deterioration_trim"}) == "act"


def test_act_critical_news_classifies_act_per_constant():
    # BUCKET_CRITICAL_NEWS_IS_ACT=True in constants.py
    assert classify_bucket({"_source": "act", "kind": "critical_news"}) == "act"


def test_act_unknown_kind_classifies_aware():
    assert classify_bucket({"_source": "act", "kind": "macro"}) == "aware"


def test_review_trim_to_target_classifies_act():
    assert classify_bucket({"_source": "review", "action": {"type": "TRIM_TO_TARGET"}}) == "act"


def test_review_trim_and_tighten_classifies_act():
    assert classify_bucket({"_source": "review", "action": {"type": "TRIM_AND_TIGHTEN"}}) == "act"


def test_review_protective_trim_classifies_act():
    assert classify_bucket({"_source": "review", "action": {"type": "PROTECTIVE_TRIM"}}) == "act"


def test_review_tighten_only_classifies_aware_per_constant():
    # BUCKET_TIGHTEN_ONLY_IS_ACT=False in constants.py
    assert classify_bucket({"_source": "review", "action": {"type": "TIGHTEN_ONLY"}}) == "aware"


def test_review_watch_classifies_aware():
    assert classify_bucket({"_source": "review", "action": {"type": "WATCH"}}) == "aware"


def test_review_missing_action_classifies_aware_not_crash():
    assert classify_bucket({"_source": "review"}) == "aware"


def test_no_source_classifies_aware():
    assert classify_bucket({"kind": "stop_breach"}) == "aware"


# ─── split_defensive — basic partition ───────────────────────────────────────

def test_split_defensive_empty_inputs():
    result = split_defensive(None, None)
    assert result == {"act": [], "aware": []}


def test_split_defensive_partitions_by_classification():
    act_today = [
        {"ticker": "AAPL", "kind": "stop_breach"},
        {"ticker": "MSFT", "kind": "macro"},
    ]
    review_list = [
        {"ticker": "NVDA", "action": {"type": "TRIM_TO_TARGET"}},
        {"ticker": "TSLA", "action": {"type": "WATCH"}},
    ]
    result = split_defensive(act_today, review_list)
    act_tickers = {it["ticker"] for it in result["act"]}
    aware_tickers = {it["ticker"] for it in result["aware"]}
    assert act_tickers == {"AAPL", "NVDA"}
    assert aware_tickers == {"MSFT", "TSLA"}


def test_split_defensive_preserves_original_fields_via_shallow_copy():
    act_today = [{"ticker": "AAPL", "kind": "stop_breach", "why": "gap breach"}]
    result = split_defensive(act_today, None)
    item = result["act"][0]
    assert item["why"] == "gap breach"
    assert item["_source"] == "act"
    # Original list's dict must not be mutated (shallow copy, not in-place add)
    assert "_source" not in act_today[0]


def test_split_defensive_order_is_act_today_then_review_within_bucket():
    act_today = [{"ticker": "A", "kind": "stop_breach"}]
    review_list = [{"ticker": "B", "action": {"type": "TRIM_TO_TARGET"}}]
    result = split_defensive(act_today, review_list)
    assert [it["ticker"] for it in result["act"]] == ["A", "B"]


# ─── split_defensive — SPCX split-brain reconciliation ───────────────────────

def test_reconcile_drops_critical_news_when_same_ticker_has_reduce_card():
    act_today = [
        {"ticker": "SPCX", "kind": "stop_breach", "why": "Price closed below stop"},
        {"ticker": "SPCX", "kind": "critical_news", "why": "Hold for now — earnings miss"},
    ]
    result = split_defensive(act_today, None)
    kinds = [it["kind"] for it in result["act"]]
    assert "critical_news" not in kinds
    assert "stop_breach" in kinds


def test_reconcile_folds_note_into_the_surviving_reduce_card():
    act_today = [
        {"ticker": "SPCX", "kind": "stop_breach", "why": "Price closed below stop"},
        {"ticker": "SPCX", "kind": "critical_news", "why": "Hold for now — earnings miss"},
    ]
    result = split_defensive(act_today, None)
    reduce_card = next(it for it in result["act"] if it["kind"] == "stop_breach")
    assert "already factored" in reduce_card["why"]
    assert "Price closed below stop" in reduce_card["why"]  # original reason preserved


def test_reconcile_leaves_news_card_alone_when_no_reduce_card_same_ticker():
    # A winner with a single headline keeps its monitor card (untouched)
    act_today = [{"ticker": "NVDA", "kind": "critical_news", "why": "Analyst upgrade"}]
    result = split_defensive(act_today, None)
    assert len(result["act"]) == 1
    assert result["act"][0]["kind"] == "critical_news"
    assert "already factored" not in result["act"][0]["why"]


def test_reconcile_only_collapses_same_ticker_not_other_tickers():
    act_today = [
        {"ticker": "SPCX", "kind": "stop_breach", "why": "breach"},
        {"ticker": "SPCX", "kind": "critical_news", "why": "hold"},
        {"ticker": "AAPL", "kind": "critical_news", "why": "unrelated news"},
    ]
    result = split_defensive(act_today, None)
    aapl_cards = [it for it in result["act"] if it["ticker"] == "AAPL"]
    assert len(aapl_cards) == 1
    assert aapl_cards[0]["kind"] == "critical_news"


def test_reconcile_reduce_from_review_stream_also_folds_act_origin_news():
    # Cross-stream: a review-origin TRIM_TO_TARGET counts as a reduce for the
    # SAME ticker's act-origin critical_news card — both streams share the
    # _is_reduce/_ticker canon.
    act_today = [{"ticker": "SPCX", "kind": "critical_news", "why": "hold for now"}]
    review_list = [{"ticker": "SPCX", "action": {"type": "TRIM_TO_TARGET"}, "why": "trim to target"}]
    result = split_defensive(act_today, review_list)
    kinds = [it.get("kind") for it in result["act"]]
    assert "critical_news" not in kinds
    trim_card = next(it for it in result["act"] if it.get("action", {}).get("type") == "TRIM_TO_TARGET")
    assert "already factored" in trim_card["why"]


def test_reconcile_macro_protective_trim_ticker_fallback_via_trim_ticker():
    # A macro PROTECTIVE_TRIM review card carries ticker=None with its real
    # subject in action.trim_ticker (per _ticker()'s documented fallback).
    act_today = [{"ticker": "XYZ", "kind": "critical_news", "why": "hold for now"}]
    review_list = [{"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "XYZ"}, "why": "trim"}]
    result = split_defensive(act_today, review_list)
    kinds = [it.get("kind") for it in result["act"]]
    assert "critical_news" not in kinds  # matched via trim_ticker fallback


# ─── reduce_call_items / reduce_call_tickers ──────────────────────────────────

def test_reduce_call_items_returns_reduce_cards_only():
    act_today = [
        {"ticker": "AAPL", "kind": "stop_breach"},
        {"ticker": "MSFT", "kind": "macro"},
    ]
    result = reduce_call_items(act_today, None)
    assert set(result.keys()) == {"AAPL"}
    assert result["AAPL"]["kind"] == "stop_breach"


def test_reduce_call_items_act_bucket_wins_over_aware_first_per_ticker():
    # A ticker with a reduce item in BOTH streams: act_today is iterated
    # before review_list in split order, so act wins as "first per ticker."
    act_today = [{"ticker": "AAPL", "kind": "deterioration_exit", "why": "act-origin"}]
    review_list = [{"ticker": "AAPL", "action": {"type": "TRIM_TO_TARGET"}, "why": "review-origin"}]
    result = reduce_call_items(act_today, review_list)
    assert result["AAPL"]["why"] == "act-origin"


def test_reduce_call_tickers_returns_just_the_keys():
    act_today = [{"ticker": "AAPL", "kind": "stop_breach"}]
    assert reduce_call_tickers(act_today, None) == {"AAPL"}


def test_reduce_call_items_empty_on_no_reduce_signals():
    act_today = [{"ticker": "AAPL", "kind": "macro"}]
    assert reduce_call_items(act_today, None) == {}
    assert reduce_call_tickers(act_today, None) == set()


def test_reduce_call_items_safe_on_none_and_empty():
    assert reduce_call_items(None, None) == {}
    assert reduce_call_items([], []) == {}


# ─── all_flagged_tickers ───────────────────────────────────────────────────────
# The broader "any card, either lane" set (2026-07-29 audit H6), reused to close
# the Orphan Conviction / Grow Today / add-to-winner coordination gaps (2026-07-30).

def test_all_flagged_tickers_includes_act_and_review():
    act_today = [{"ticker": "AAA", "kind": "stop_breach"}]
    review_list = [{"ticker": "BBB", "action": {"type": "TRIM_TO_TARGET"}}]
    assert all_flagged_tickers(act_today, review_list) == {"AAA", "BBB"}


def test_all_flagged_tickers_includes_non_reduce_kinds_too():
    # Broader than reduce_call_tickers: a WATCH-type / macro card still counts.
    act_today = [{"ticker": "CCC", "kind": "macro"}]
    assert all_flagged_tickers(act_today, None) == {"CCC"}
    assert reduce_call_tickers(act_today, None) == set()


def test_all_flagged_tickers_resolves_trim_ticker_fallback():
    review_list = [{"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "DDD"}}]
    assert all_flagged_tickers(None, review_list) == {"DDD"}


def test_all_flagged_tickers_safe_on_none_and_empty():
    assert all_flagged_tickers(None, None) == set()
    assert all_flagged_tickers([], []) == set()


# ─── suppress_orphans_under_reduce_call ─────────────────────────────────────────
# Closes the Orphan Conviction / Act Today reduce-call collision (BKNG incident,
# 2026-07-30): a "size up" candidate that's also under an active reduce call must
# be excluded, not silently — the caller renders the reduce call from _reduce_call.

def test_suppress_orphans_excludes_ticker_with_active_reduce_call():
    orphans = [{"Ticker": "BKNG", "Score": 80.0, "Weight (%)": 21.4}]
    reduce_calls = {"BKNG": {"action": "TRIM_TO_TARGET", "why": "earnings overweight"}}
    actionable, suppressed = suppress_orphans_under_reduce_call(orphans, reduce_calls)
    assert actionable == []
    assert len(suppressed) == 1
    assert suppressed[0]["_reduce_call"] == reduce_calls["BKNG"]


def test_suppress_orphans_keeps_ticker_with_no_reduce_call():
    orphans = [{"Ticker": "AAPL", "Score": 80.0, "Weight (%)": 3.0}]
    actionable, suppressed = suppress_orphans_under_reduce_call(orphans, {})
    assert actionable == orphans
    assert suppressed == []


def test_suppress_orphans_ticker_match_is_case_insensitive():
    orphans = [{"Ticker": "msft", "Score": 80.0, "Weight (%)": 3.0}]
    reduce_calls = {"MSFT": {"action": "stop_breach"}}
    actionable, suppressed = suppress_orphans_under_reduce_call(orphans, reduce_calls)
    assert actionable == []
    assert len(suppressed) == 1


def test_suppress_orphans_safe_on_none_and_empty():
    assert suppress_orphans_under_reduce_call(None, None) == ([], [])
    assert suppress_orphans_under_reduce_call([], {}) == ([], [])


# ─── EXIT_KINDS / TRIM_KINDS canon (F-204 Summary chips + badges) ─────────────
# These two sets are the SINGLE source of the severity split. summary_view
# imports them rather than re-declaring a copy; these tests pin the properties
# that made a copy dangerous.

def test_exit_and_trim_kinds_exactly_cover_the_reduce_set():
    # If a kind is added to _REDUCE_ACT_KINDS it must land in exactly one
    # bucket. Before TRIM_KINDS was DERIVED as the remainder, a new kind fell
    # through to WATCH in two separate parallel copies — a reduce directive
    # silently displayed as mere awareness.
    assert EXIT_KINDS | TRIM_KINDS == _REDUCE_ACT_KINDS


def test_exit_and_trim_kinds_are_disjoint():
    assert not (EXIT_KINDS & TRIM_KINDS)


# ─── bucket_act_by_type ──────────────────────────────────────────────────────

def test_bucket_act_by_type_empty_is_all_zero():
    out = bucket_act_by_type([])
    assert out["counts"] == {"EXIT": 0, "TRIM": 0, "WATCH": 0}
    assert out["EXIT"] == [] and out["TRIM"] == [] and out["WATCH"] == []


def test_bucket_act_by_type_none_is_safe():
    assert bucket_act_by_type(None)["counts"]["EXIT"] == 0


def test_bucket_act_by_type_splits_the_three_severities():
    items = [
        {"_source": "act", "kind": "stop_breach",        "ticker": "COF"},
        {"_source": "act", "kind": "deterioration_trim", "ticker": "LLY"},
        {"_source": "act", "kind": "critical_news",      "ticker": "PLTR"},
    ]
    out = bucket_act_by_type(items)
    assert out["counts"] == {"EXIT": 1, "TRIM": 1, "WATCH": 1}


def test_bucket_act_by_type_review_trim_lands_in_trim():
    items = [{"_source": "review", "action": {"type": "TRIM_AND_TIGHTEN"}, "ticker": "MSFT"}]
    assert bucket_act_by_type(items)["counts"]["TRIM"] == 1


def test_bucket_act_by_type_non_reduce_review_lands_in_watch():
    items = [{"_source": "review", "action": {"type": "RECOMMIT"}, "ticker": "MSFT"}]
    assert bucket_act_by_type(items)["counts"]["WATCH"] == 1


def test_bucket_act_by_type_never_invents_an_enter_bucket():
    # Act Today is defensive-only by design (user decision 2026-08-28): the
    # buy-side stream is a different producer and must not leak in here.
    out = bucket_act_by_type([{"_source": "act", "kind": "stop_breach"}])
    assert set(out.keys()) == {"EXIT", "TRIM", "WATCH", "counts"}


def test_bucket_act_by_type_unknown_source_is_watch_not_dropped():
    # A malformed item must degrade to awareness, never vanish silently.
    out = bucket_act_by_type([{"kind": "stop_breach"}])   # no _source
    assert out["counts"]["WATCH"] == 1


def test_bucket_act_by_type_counts_match_list_lengths():
    items = [
        {"_source": "act", "kind": "sell_signal"},
        {"_source": "act", "kind": "risk"},
        {"_source": "act", "kind": "risk_off_derisk"},
    ]
    out = bucket_act_by_type(items)
    for k in ("EXIT", "TRIM", "WATCH"):
        assert out["counts"][k] == len(out[k])
