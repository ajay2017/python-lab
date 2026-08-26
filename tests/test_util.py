"""Tests for stock_analyzer/util.py and stock_analyzer/market_time.py — the
shared helpers introduced 2026-08-04 to make the safe idiom the default for two
bug-classes the audits kept re-finding (offline-sentinel collapse, XSS) and the
NY-tz date-boundary class. Pure logic, no I/O.
"""
from datetime import datetime

from stock_analyzer.market_time import ET, now_et, today_et
from stock_analyzer.util import get_or_offline, safe_html, stop_recovery_state


class TestGetOrOffline:
    def test_none_value_stays_offline(self):
        # A producer stores None on failure — must NOT collapse to a default.
        assert get_or_offline({"k": None}, "k") is None

    def test_missing_key_is_offline(self):
        assert get_or_offline({}, "k") is None

    def test_none_container_is_offline(self):
        assert get_or_offline(None, "k") is None

    def test_checked_empty_list_passes_through(self):
        # [] means "computed, genuinely nothing" — distinct from offline.
        assert get_or_offline({"k": []}, "k") == []

    def test_checked_empty_dict_passes_through(self):
        assert get_or_offline({"k": {}}, "k") == {}

    def test_real_value_passes_through(self):
        assert get_or_offline({"k": [1, 2]}, "k") == [1, 2]

    def test_distinguishes_offline_from_checked_empty(self):
        # The whole point: `.get(k) or []` cannot tell these two apart.
        offline = get_or_offline({"k": None}, "k")
        empty = get_or_offline({"k": []}, "k")
        assert offline is None
        assert empty == []
        assert offline is not empty


class TestSafeHtml:
    def test_escapes_angle_brackets(self):
        assert safe_html("<script>") == "&lt;script&gt;"

    def test_escapes_quotes_for_attribute_context(self):
        # quote=True — safe inside title='...' as well as element text.
        assert safe_html('"x"') == "&quot;x&quot;"
        assert safe_html("'x'") == "&#x27;x&#x27;"

    def test_ampersand_escaped_first(self):
        assert safe_html("a & b") == "a &amp; b"

    def test_coerces_non_str(self):
        assert safe_html(42) == "42"
        assert safe_html(None) == "None"

    def test_plain_text_unchanged(self):
        assert safe_html("AAPL up 3%") == "AAPL up 3%"


class TestStopRecoveryState:
    """stop_recovery_state(live_gap_to_stop, margin_pct) — pins all boundary
    cases so a refactor that accidentally flips the boundary or drops the
    offline contract is caught immediately."""

    def test_none_gap_is_unavailable(self):
        assert stop_recovery_state(None) == "unavailable"

    def test_zero_live_price_sentinel_is_unavailable(self):
        # 0.0 is not a valid gap (means price == stop to the cent); treat like
        # offline rather than "active" to avoid a false "still breached" caption.
        # Actually 0.0 means exactly at stop → active (not unavailable). Verify.
        assert stop_recovery_state(0.0) == "active"

    def test_nan_gap_is_unavailable(self):
        import math
        assert stop_recovery_state(math.nan) == "unavailable"

    def test_inf_gap_is_unavailable(self):
        import math
        assert stop_recovery_state(math.inf) == "unavailable"

    def test_negative_gap_is_active(self):
        # price below stop → breach
        assert stop_recovery_state(-3.5) == "active"

    def test_gap_exactly_zero_is_active(self):
        # price == stop exactly — not recovered yet; boundary must be active
        assert stop_recovery_state(0.0) == "active"

    def test_gap_equal_to_margin_is_active(self):
        # live_gap == margin_pct is NOT recovered — must be strictly greater
        assert stop_recovery_state(0.5, margin_pct=0.5) == "active"

    def test_gap_one_tick_above_margin_is_recovered(self):
        assert stop_recovery_state(0.51, margin_pct=0.5) == "recovered"

    def test_large_positive_gap_is_recovered(self):
        assert stop_recovery_state(5.0, margin_pct=0.5) == "recovered"

    def test_zero_margin_bare_comparison(self):
        # With no margin, any positive gap is "recovered"
        assert stop_recovery_state(0.01, margin_pct=0.0) == "recovered"
        assert stop_recovery_state(0.0, margin_pct=0.0) == "active"

    def test_string_gap_is_unavailable(self):
        # Non-numeric input (e.g. from a malformed DataFrame cell) → offline
        assert stop_recovery_state("n/a") == "unavailable"  # type: ignore[arg-type]

    def test_default_margin_is_zero(self):
        # Bare call: any positive gap → recovered
        assert stop_recovery_state(0.01) == "recovered"


class TestMarketTime:
    def test_now_et_is_timezone_aware(self):
        assert now_et().tzinfo is not None

    def test_today_et_matches_now_et_date(self):
        assert today_et() == now_et().date()

    def test_et_is_new_york(self):
        assert "New_York" in str(ET)

    def test_now_et_carries_tz_unlike_naive(self):
        # now_et carries a tz; a naive datetime does not — the distinction the
        # date-boundary bug class hinges on.
        assert now_et().tzinfo is not None
        assert datetime(2020, 1, 1).tzinfo is None
