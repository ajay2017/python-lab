"""Regression tests for stock_analyzer/decision_journal.py — the Decision
Journal's compute_patterns(): signal-followed vs. signal-ignored accuracy,
costly-deviation/good-override lists, the lessons library, structured
lesson-category cross-tabs, and the one-line behavioral insight. Pure
computation (pandas only, no I/O). See docs/plans/test-automation.md.
"""
import pandas as pd
import pytest

from stock_analyzer import decision_journal as dj


def _row(ticker="AAPL", action="SELL", realized_pnl=None, followed_signal=None,
         signal_seen="", deviation_reason="", lesson="", lesson_category="",
         traded_at="2026-01-01"):
    return {
        "ticker": ticker, "action": action, "realized_pnl": realized_pnl,
        "followed_signal": followed_signal, "signal_seen": signal_seen,
        "deviation_reason": deviation_reason, "lesson": lesson,
        "lesson_category": lesson_category, "traded_at": traded_at,
    }


def _trades(rows):
    return pd.DataFrame(rows)


_EMPTY = {
    "total_with_context": 0,
    "followed_wins": 0, "followed_losses": 0, "followed_pnl": 0.0,
    "ignored_wins":  0, "ignored_losses":  0, "ignored_pnl":  0.0,
    "signal_accuracy":        None,
    "override_accuracy":      None,
    "costly_deviations":      [],
    "good_overrides":         [],
    "lessons":                [],
    "lesson_category_counts": {},
    "lesson_category_by_outcome": {},
    "behavioral_insight": None,
}


# ── Empty / short-circuit paths ─────────────────────────────────────────────

def test_compute_patterns_none_trades_df():
    assert dj.compute_patterns(None) == _EMPTY


def test_compute_patterns_empty_trades_df():
    assert dj.compute_patterns(pd.DataFrame()) == _EMPTY


def test_compute_patterns_no_action_column_missing_followed_signal_col():
    df = pd.DataFrame([{"ticker": "AAPL", "realized_pnl": 100.0}])
    assert dj.compute_patterns(df) == _EMPTY


def test_compute_patterns_no_followed_signal_column():
    df = _trades([_row()])
    del df["followed_signal"]
    assert dj.compute_patterns(df) == _EMPTY


def test_compute_patterns_no_sell_rows():
    df = _trades([_row(action="BUY", followed_signal="yes")])
    assert dj.compute_patterns(df) == _EMPTY


def test_compute_patterns_no_rows_with_yes_or_no_followed_signal():
    df = _trades([_row(followed_signal=None), _row(followed_signal="maybe")])
    assert dj.compute_patterns(df) == _EMPTY


# ── Followed / ignored accuracy stats ───────────────────────────────────────

def test_compute_patterns_followed_wins_and_losses():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="yes", realized_pnl=50.0),
        _row(followed_signal="yes", realized_pnl=-30.0),
    ])
    result = dj.compute_patterns(df)
    assert result["followed_wins"] == 2
    assert result["followed_losses"] == 1
    assert result["followed_pnl"] == 120.0
    assert result["signal_accuracy"] == pytest.approx(66.7, abs=0.1)


def test_compute_patterns_ignored_wins_and_losses():
    df = _trades([
        _row(followed_signal="no", realized_pnl=-100.0),
        _row(followed_signal="no", realized_pnl=200.0),
    ])
    result = dj.compute_patterns(df)
    assert result["ignored_wins"] == 1
    assert result["ignored_losses"] == 1
    assert result["ignored_pnl"] == 100.0
    assert result["override_accuracy"] == 50.0


def test_compute_patterns_accuracy_none_when_no_wins_or_losses():
    # realized_pnl unparseable -> _pnl is None -> neither a win nor a loss.
    df = _trades([_row(followed_signal="yes", realized_pnl="bad")])
    result = dj.compute_patterns(df)
    assert result["signal_accuracy"] is None


def test_compute_patterns_followed_signal_normalized_case_and_whitespace():
    df = _trades([
        _row(followed_signal=" YES ", realized_pnl=100.0),
        _row(followed_signal="No", realized_pnl=-50.0),
    ])
    result = dj.compute_patterns(df)
    assert result["followed_wins"] == 1
    assert result["ignored_losses"] == 1


def test_compute_patterns_action_matching_case_insensitive():
    df = _trades([_row(action="sell", followed_signal="yes", realized_pnl=100.0)])
    result = dj.compute_patterns(df)
    assert result["total_with_context"] == 1


# ── Costly deviations / good overrides ─────────────────────────────────────

def test_compute_patterns_costly_deviations_sorted_worst_first():
    df = _trades([
        _row(ticker="A", followed_signal="no", realized_pnl=-50.0, traded_at="2026-01-01"),
        _row(ticker="B", followed_signal="no", realized_pnl=-200.0, traded_at="2026-01-02"),
    ])
    result = dj.compute_patterns(df)
    tickers = [c["ticker"] for c in result["costly_deviations"]]
    assert tickers == ["B", "A"]  # -200 (worse) before -50


def test_compute_patterns_good_overrides_sorted_best_first():
    df = _trades([
        _row(ticker="A", followed_signal="no", realized_pnl=50.0),
        _row(ticker="B", followed_signal="no", realized_pnl=200.0),
    ])
    result = dj.compute_patterns(df)
    tickers = [g["ticker"] for g in result["good_overrides"]]
    assert tickers == ["B", "A"]  # 200 (better) before 50


def test_compute_patterns_followed_losses_excluded_from_costly_deviations():
    # Costly deviations only apply to IGNORED (followed_signal='no') trades.
    df = _trades([_row(followed_signal="yes", realized_pnl=-100.0)])
    result = dj.compute_patterns(df)
    assert result["costly_deviations"] == []


def test_compute_patterns_costly_deviation_fields():
    df = _trades([_row(
        ticker="AAPL", followed_signal="no", realized_pnl=-75.0,
        signal_seen="Sell", deviation_reason="thought it would bounce",
        lesson="should have listened", traded_at="2026-02-15T10:00:00",
    )])
    result = dj.compute_patterns(df)
    c = result["costly_deviations"][0]
    assert c["ticker"] == "AAPL"
    assert c["signal_seen"] == "Sell"
    assert c["deviation_reason"] == "thought it would bounce"
    assert c["realized_pnl"] == -75.0
    assert c["lesson"] == "should have listened"
    assert c["traded_at"] == "2026-02-15"  # truncated to date-only


# ── Lessons library ──────────────────────────────────────────────────────

def test_compute_patterns_lessons_includes_free_text_only_rows():
    df = _trades([_row(followed_signal="yes", realized_pnl=10.0, lesson="good discipline")])
    result = dj.compute_patterns(df)
    assert len(result["lessons"]) == 1
    assert result["lessons"][0]["text"] == "good discipline"


def test_compute_patterns_lessons_includes_category_only_rows():
    df = _trades([_row(followed_signal="yes", realized_pnl=10.0,
                        lesson_category="Thesis broke — cut correctly")])
    result = dj.compute_patterns(df)
    assert len(result["lessons"]) == 1


def test_compute_patterns_lessons_excludes_rows_with_neither():
    df = _trades([_row(followed_signal="yes", realized_pnl=10.0)])
    result = dj.compute_patterns(df)
    assert result["lessons"] == []


def test_compute_patterns_lessons_sorted_most_recent_first():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=1.0, lesson="old", traded_at="2026-01-01"),
        _row(followed_signal="yes", realized_pnl=1.0, lesson="new", traded_at="2026-03-01"),
    ])
    result = dj.compute_patterns(df)
    assert [l["text"] for l in result["lessons"]] == ["new", "old"]


def test_compute_patterns_lessons_excludes_rows_without_yes_no_followed_signal():
    # A row with a lesson but no valid followed_signal never enters
    # with_context, so it's excluded from the lessons list even though it
    # has real lesson text.
    df = _trades([_row(followed_signal=None, realized_pnl=10.0, lesson="orphaned lesson")])
    result = dj.compute_patterns(df)
    assert result["lessons"] == []


# ── Lesson category analytics (broader scope than "lessons") ───────────────

def test_compute_patterns_lesson_category_counts_across_all_sell_rows():
    # Lesson-category analytics scope is ALL SELL rows with a category set,
    # not just rows with a valid yes/no followed_signal -- broader than the
    # "lessons" list and the with_context-derived accuracy stats.
    df = _trades([
        _row(followed_signal=None, realized_pnl=50.0, lesson_category="Earnings surprise — unexpected event"),
        _row(followed_signal="yes", realized_pnl=-20.0, lesson_category="Earnings surprise — unexpected event"),
    ])
    result = dj.compute_patterns(df)
    assert result["lesson_category_counts"]["Earnings surprise — unexpected event"] == 2
    # But "lessons" only includes the with_context (followed_signal='yes') row.
    assert len(result["lessons"]) == 1


def test_compute_patterns_lesson_category_by_outcome_wins_losses_avg_pnl():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0, lesson_category="Panic/fear sell — emotional, not analytical"),
        _row(followed_signal="yes", realized_pnl=-50.0, lesson_category="Panic/fear sell — emotional, not analytical"),
    ])
    result = dj.compute_patterns(df)
    stats = result["lesson_category_by_outcome"]["Panic/fear sell — emotional, not analytical"]
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["n"] == 2
    assert stats["avg_pnl"] == 25.0


def test_compute_patterns_blank_lesson_category_not_counted():
    df = _trades([_row(followed_signal="yes", realized_pnl=10.0, lesson_category="  ")])
    result = dj.compute_patterns(df)
    assert result["lesson_category_counts"] == {}


# ── Behavioral insight ──────────────────────────────────────────────────────

def test_behavioral_insight_none_with_insufficient_data():
    df = _trades([_row(followed_signal="yes", realized_pnl=10.0)])
    result = dj.compute_patterns(df)
    assert result["behavioral_insight"] is None


def test_behavioral_insight_costly_deviations_takes_priority_at_2_or_more():
    df = _trades([
        _row(followed_signal="no", realized_pnl=-100.0),
        _row(followed_signal="no", realized_pnl=-200.0),
    ])
    result = dj.compute_patterns(df)
    assert "overridden sell signals 2 times" in result["behavioral_insight"]
    assert "$-150" in result["behavioral_insight"]  # avg cost = (-100-200)/2


def test_behavioral_insight_one_costly_deviation_falls_to_accuracy_branch():
    df = _trades([
        _row(followed_signal="no", realized_pnl=-100.0),
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl=100.0),
    ])
    result = dj.compute_patterns(df)
    assert len(result["costly_deviations"]) == 1
    assert "Signals are working" in result["behavioral_insight"]


def test_behavioral_insight_signals_working_when_followed_much_more_accurate():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl=-100.0),
    ])
    result = dj.compute_patterns(df)
    assert result["signal_accuracy"] == 100.0
    assert result["override_accuracy"] == 50.0
    assert "Signals are working" in result["behavioral_insight"]


def test_behavioral_insight_overrides_outperforming():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="yes", realized_pnl=-100.0),
        _row(followed_signal="no", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl=100.0),
    ])
    result = dj.compute_patterns(df)
    assert result["signal_accuracy"] == 50.0
    assert result["override_accuracy"] == 100.0
    assert "overrides are outperforming" in result["behavioral_insight"]


def test_behavioral_insight_similar_accuracy():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="yes", realized_pnl=-100.0),
        _row(followed_signal="no", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl=-100.0),
    ])
    result = dj.compute_patterns(df)
    assert result["signal_accuracy"] == 50.0
    assert result["override_accuracy"] == 50.0
    assert "are similar" in result["behavioral_insight"]


def test_behavioral_insight_none_when_only_one_side_has_accuracy():
    df = _trades([
        _row(followed_signal="yes", realized_pnl=100.0),
        _row(followed_signal="no", realized_pnl="bad"),  # ignored side has no valid pnl
    ])
    result = dj.compute_patterns(df)
    assert result["override_accuracy"] is None
    assert result["behavioral_insight"] is None


# ── LESSON_CATEGORIES vocabulary ─────────────────────────────────────────────

def test_lesson_categories_is_fixed_vocabulary_list():
    assert isinstance(dj.LESSON_CATEGORIES, list)
    assert len(dj.LESSON_CATEGORIES) == 10
    assert "Pre-mortem call was right" in dj.LESSON_CATEGORIES
