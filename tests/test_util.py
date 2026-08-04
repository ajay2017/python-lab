"""Tests for stock_analyzer/util.py and stock_analyzer/market_time.py — the
shared helpers introduced 2026-08-04 to make the safe idiom the default for two
bug-classes the audits kept re-finding (offline-sentinel collapse, XSS) and the
NY-tz date-boundary class. Pure logic, no I/O.
"""
from datetime import datetime

from stock_analyzer.market_time import ET, now_et, today_et
from stock_analyzer.util import get_or_offline, safe_html


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
