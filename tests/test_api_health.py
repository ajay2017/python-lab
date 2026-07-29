"""Tests for stock_analyzer/api_health.py — a pure/stateful (no I/O) module-
level stats accumulator tracking per-source API call health. `get_fmp_daily_
quota`/`invalidate_fmp_quota_cache` are skipped — they call `stock_analyzer.
db`, a real DB dependency out of scope for this pure-logic pass.

IMPORTANT: `_stats` is module-level mutable state that persists across the
whole test process. An autouse fixture calls `api_health.reset()` before
every test in this file so tests never see another test's leftover counts.
No other test file in this suite imports api_health (confirmed via grep),
so this file's state has no cross-file bleed to guard against.
"""
import pytest

from stock_analyzer import api_health


@pytest.fixture(autouse=True)
def _reset_stats():
    api_health.reset()
    yield


# ─── record — per-event-type field mutation ─────────────────────────────────

def test_record_success_increments_calls_successes_and_resets_consecutive_errors():
    api_health.record("test_src", "error")  # bump consecutive_errors first
    api_health.record("test_src", "success")
    s = api_health._stats["test_src"]
    assert s["calls"] == 2
    assert s["successes"] == 1
    assert s["consecutive_errors"] == 0
    assert s["last_success_ts"] is not None


def test_record_error_increments_calls_errors_and_consecutive_errors():
    api_health.record("test_src", "error", msg="boom")
    s = api_health._stats["test_src"]
    assert s["calls"] == 1
    assert s["errors"] == 1
    assert s["consecutive_errors"] == 1
    assert s["last_error_ts"] is not None
    assert s["last_error_msg"] == "boom"


def test_record_error_msg_truncated_to_120_chars():
    api_health.record("test_src", "error", msg="x" * 200)
    assert len(api_health._stats["test_src"]["last_error_msg"]) == 120


def test_record_rate_limit_does_not_increment_calls_but_increments_rate_limits():
    api_health.record("test_src", "rate_limit")
    s = api_health._stats["test_src"]
    assert s["calls"] == 0
    assert s["rate_limits"] == 1
    assert s["consecutive_errors"] == 1
    assert s["last_error_ts"] is not None


def test_record_empty_increments_calls_and_empty_returns_only():
    api_health.record("test_src", "empty")
    s = api_health._stats["test_src"]
    assert s["calls"] == 1
    assert s["empty_returns"] == 1
    assert s["consecutive_errors"] == 0


def test_record_quota_increments_calls_quotas_and_consecutive_errors():
    api_health.record("test_src", "quota")
    s = api_health._stats["test_src"]
    assert s["calls"] == 1
    assert s["quotas"] == 1
    assert s["consecutive_errors"] == 1
    assert s["last_error_msg"] == "402 — Payment Required (plan limit)"


def test_record_auth_increments_calls_auth_errors_and_consecutive_errors():
    api_health.record("test_src", "auth")
    s = api_health._stats["test_src"]
    assert s["calls"] == 1
    assert s["auth_errors"] == 1
    assert s["consecutive_errors"] == 1


def test_record_parse_increments_calls_parse_errors_and_consecutive_errors():
    api_health.record("test_src", "parse")
    s = api_health._stats["test_src"]
    assert s["calls"] == 1
    assert s["parse_errors"] == 1
    assert s["consecutive_errors"] == 1


def test_record_unregistered_source_auto_creates_blank_entry():
    assert "brand_new_src" not in api_health._stats
    api_health.record("brand_new_src", "success")
    assert "brand_new_src" in api_health._stats
    assert api_health._stats["brand_new_src"]["calls"] == 1


# ─── get_health — unregistered source ────────────────────────────────────────

def test_get_health_unregistered_source_returns_gray_default_without_mutating():
    result = api_health.get_health("never_seen_before")
    assert result["level"] == "gray"
    assert result["icon"] == "⚪"
    assert "never_seen_before" not in api_health._stats


def test_get_health_registered_source_no_calls_is_gray():
    # After reset(), the default sources exist but have zero calls.
    result = api_health.get_health("yahoo_finance")
    assert result["level"] == "gray"
    assert result["icon"] == "⚪"


# ─── get_health — level boundaries ───────────────────────────────────────────

def test_get_health_red_via_auth_errors():
    api_health.record("test_src", "auth")
    assert api_health.get_health("test_src")["level"] == "red"


def test_get_health_red_via_rate_limits_at_3():
    for _ in range(3):
        api_health.record("test_src", "rate_limit")
    assert api_health.get_health("test_src")["level"] == "red"


def test_get_health_red_via_consecutive_errors_at_5():
    for _ in range(5):
        api_health.record("test_src", "error")
    assert api_health.get_health("test_src")["level"] == "red"


def test_get_health_yellow_via_quota():
    api_health.record("test_src", "success")
    api_health.record("test_src", "quota")
    assert api_health.get_health("test_src")["level"] == "yellow"


def test_get_health_yellow_via_rate_limit_below_red_threshold():
    api_health.record("test_src", "rate_limit")
    assert api_health.get_health("test_src")["level"] == "yellow"


def test_get_health_yellow_via_consecutive_errors_at_2():
    api_health.record("test_src", "error")
    api_health.record("test_src", "error")
    assert api_health.get_health("test_src")["level"] == "yellow"


def test_get_health_yellow_via_parse_errors_at_3():
    for _ in range(3):
        api_health.record("test_src", "parse")
    assert api_health.get_health("test_src")["level"] == "yellow"


def _record_alternating_errors(source, n_errors, total_calls):
    """Interleave `n_errors` error events with successes (one success right
    after each error) so consecutive_errors never exceeds 1, then top up
    with plain successes to reach `total_calls` total recorded calls."""
    for _ in range(n_errors):
        api_health.record(source, "error")
        api_health.record(source, "success")
    remaining = total_calls - (n_errors * 2)
    for _ in range(remaining):
        api_health.record(source, "success")


def test_get_health_error_rate_exactly_20pct_is_not_yellow():
    _record_alternating_errors("test_src", n_errors=20, total_calls=100)
    result = api_health.get_health("test_src")
    assert result["calls"] == 100
    assert result["errors"] == 20
    assert result["level"] == "green"


def test_get_health_error_rate_just_above_20pct_is_yellow():
    _record_alternating_errors("test_src", n_errors=21, total_calls=100)
    result = api_health.get_health("test_src")
    assert result["errors"] == 21
    assert result["level"] == "yellow"


def test_get_health_green_when_calls_positive_and_no_stress_conditions():
    for _ in range(5):
        api_health.record("test_src", "success")
    assert api_health.get_health("test_src")["level"] == "green"


# ─── overall_level — worst-of-all precedence ────────────────────────────────

def test_overall_level_gray_when_no_calls_recorded():
    assert api_health.overall_level() == ("gray", "⚪")


def test_overall_level_yellow_beats_green():
    api_health.record("yahoo_finance", "success")     # green
    api_health.record("fmp", "quota")                  # yellow
    assert api_health.overall_level() == ("yellow", "🟡")


def test_overall_level_red_beats_everything():
    api_health.record("yahoo_finance", "success")      # green
    api_health.record("fmp", "quota")                   # yellow
    api_health.record("finnhub", "auth")                # red
    assert api_health.overall_level() == ("red", "🔴")


# ─── in_cooldown ──────────────────────────────────────────────────────────────

def test_in_cooldown_unregistered_source_is_false():
    assert api_health.in_cooldown("never_registered", 60) is False


def test_in_cooldown_not_tripped_is_false():
    api_health.record("test_src", "success")
    assert api_health.in_cooldown("test_src", 60) is False


def test_in_cooldown_tripped_but_no_last_error_ts_is_false():
    # Not normally reachable via record() (every error-ish event also sets
    # last_error_ts), but it's a real branch in the source -- construct it
    # directly via the internal state.
    api_health._stats["test_src"] = api_health._blank()
    api_health._stats["test_src"]["rate_limits"] = 3
    assert api_health.in_cooldown("test_src", 60) is False


def test_in_cooldown_tripped_and_within_window_is_true():
    for _ in range(3):
        api_health.record("test_src", "rate_limit")
    assert api_health.in_cooldown("test_src", 60) is True


def test_in_cooldown_tripped_but_elapsed_is_false(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr(api_health._t, "time", lambda: times[0])
    for _ in range(3):
        api_health.record("test_src", "rate_limit")
    times[0] = 1000.0 + 9999.0  # well past any reasonable cooldown
    assert api_health.in_cooldown("test_src", 60) is False


# ─── _age_str ─────────────────────────────────────────────────────────────────

def test_age_str_none_timestamp_returns_dash():
    assert api_health._age_str(None) == "—"


def test_age_str_seconds_bucket_just_below_60(monkeypatch):
    times = [1000.0 + 59]
    monkeypatch.setattr(api_health._t, "time", lambda: times[0])
    assert api_health._age_str(1000.0) == "59s ago"


def test_age_str_transitions_to_minutes_at_60(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr(api_health._t, "time", lambda: times[0])
    times[0] = 1000.0 + 60
    assert api_health._age_str(1000.0) == "1m ago"


def test_age_str_minutes_bucket_just_below_3600(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr(api_health._t, "time", lambda: times[0])
    times[0] = 1000.0 + 3599
    assert api_health._age_str(1000.0) == "59m ago"


def test_age_str_transitions_to_hours_at_3600(monkeypatch):
    times = [1000.0]
    monkeypatch.setattr(api_health._t, "time", lambda: times[0])
    times[0] = 1000.0 + 3600
    assert api_health._age_str(1000.0) == "1h ago"
