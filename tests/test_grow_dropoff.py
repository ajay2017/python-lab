"""Tests for stock_analyzer.grow_dropoff — firmness badge and drop-off trace.

Spec: test exact boundaries (2026-08-04 edge-case lesson), reason honesty,
reduce-call precedence, never-resurrect, acted/held suppression, no-action
shape, and None-reduce_calls safety.
"""
import pytest

from stock_analyzer.grow_dropoff import derive_dropoffs, firmness, tier_floor_for
from stock_analyzer.constants import COMPOSITE_BUY, COMPOSITE_FIRMNESS_MARGIN, COMPOSITE_STRONG_BUY


# ── firmness() ────────────────────────────────────────────────────────────────

class TestFirmness:
    """Boundary tests for firmness(). Boundary is <=, meaning (composite -
    tier_floor) == margin is still 'at_line'."""

    def test_strong_buy_floor_at_line_below_margin(self):
        # 77.9 - 75 = 2.9 <= 3  → at_line
        assert firmness(77.9, COMPOSITE_STRONG_BUY, COMPOSITE_FIRMNESS_MARGIN) == "at_line"

    def test_strong_buy_floor_at_line_exact_margin(self):
        # 78.0 - 75 = 3.0 <= 3  → at_line  (inclusive boundary)
        assert firmness(78.0, COMPOSITE_STRONG_BUY, COMPOSITE_FIRMNESS_MARGIN) == "at_line"

    def test_strong_buy_floor_well_clear_above_margin(self):
        # 78.1 - 75 = 3.1 > 3   → well_clear
        assert firmness(78.1, COMPOSITE_STRONG_BUY, COMPOSITE_FIRMNESS_MARGIN) == "well_clear"

    def test_buy_floor_at_line_exact_margin(self):
        # 68.0 - 65 = 3.0 <= 3  → at_line  (inclusive boundary)
        assert firmness(68.0, COMPOSITE_BUY, COMPOSITE_FIRMNESS_MARGIN) == "at_line"

    def test_buy_floor_well_clear_above_margin(self):
        # 68.1 - 65 = 3.1 > 3   → well_clear
        assert firmness(68.1, COMPOSITE_BUY, COMPOSITE_FIRMNESS_MARGIN) == "well_clear"

    def test_at_tier_floor_itself_is_at_line(self):
        # 65.0 - 65 = 0.0 <= 3  → at_line
        assert firmness(65.0, COMPOSITE_BUY, COMPOSITE_FIRMNESS_MARGIN) == "at_line"

    def test_zero_margin_exact_floor_is_at_line(self):
        # 65.0 - 65 = 0 <= 0    → at_line
        assert firmness(65.0, COMPOSITE_BUY, 0) == "at_line"

    def test_zero_margin_above_floor_is_well_clear(self):
        # 65.1 - 65 = 0.1 > 0   → well_clear
        assert firmness(65.1, COMPOSITE_BUY, 0) == "well_clear"


# ── tier_floor_for() ──────────────────────────────────────────────────────────

class TestTierFloorFor:
    def test_strong_buy_returns_strong_buy_floor(self):
        assert tier_floor_for(COMPOSITE_STRONG_BUY) == float(COMPOSITE_STRONG_BUY)

    def test_above_strong_buy_returns_strong_buy_floor(self):
        assert tier_floor_for(90.0) == float(COMPOSITE_STRONG_BUY)

    def test_at_buy_returns_buy_floor(self):
        assert tier_floor_for(COMPOSITE_BUY) == float(COMPOSITE_BUY)

    def test_between_buy_and_strong_buy_returns_buy_floor(self):
        assert tier_floor_for(70.0) == float(COMPOSITE_BUY)

    def test_just_below_strong_buy_returns_buy_floor(self):
        # 74.9 < 75 → floor is COMPOSITE_BUY
        assert tier_floor_for(74.9) == float(COMPOSITE_BUY)


# ── derive_dropoffs() ─────────────────────────────────────────────────────────

def _make_surfaced(ticker, first_seen_at="2026-08-12T09:45:00+00:00", composite=70.0):
    return {
        "ticker": ticker,
        "first_seen_at": first_seen_at,
        "composite_at_surface": composite,
    }


def _make_buckets(
    composite_skipped=None,
    sector_blocked_picks=None,
    macro_blocked_picks=None,
    composite_unavailable=None,
):
    return {
        "composite_skipped":     composite_skipped or [],
        "sector_blocked_picks":  sector_blocked_picks or [],
        "macro_blocked_picks":   macro_blocked_picks or [],
        "composite_unavailable": composite_unavailable or [],
    }


class TestDeriveDropoffs:

    # ── reason honesty ────────────────────────────────────────────────────────

    def test_composite_skipped_reason_contains_composite_number(self):
        """When a dropped ticker is in composite_skipped, the reason must
        reference the bucket's composite_score (never fabricate a number)."""
        surfaced = [_make_surfaced("AAPL")]
        buckets = _make_buckets(
            composite_skipped=[{
                "ticker": "AAPL",
                "composite_score": 58.0,
                "composite_label": "Hold",
            }]
        )
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert len(result) == 1
        row = result[0]
        assert row["reason_code"] == "composite_below_bar"
        # The known composite number must appear in the reason text
        assert "58" in row["reason_text"]
        assert row["has_confident_reason"] is True
        assert row["current_composite"] == 58.0

    def test_unattributed_reason_contains_no_composite_digits(self):
        """When dropped ticker matches NONE of the buckets and no reduce call
        exists, reason_code must be 'unattributed' and reason_text must NOT
        echo back the composite_at_surface value as a fabricated recommendation
        number. The timestamp reference in the reason is legitimate; what's
        prohibited is using the surface composite to imply current signal."""
        # Use a distinctive composite that cannot appear in a timestamp
        surfaced = [_make_surfaced("MSFT", composite=99.0)]
        buckets = _make_buckets()  # empty — no bucket match
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert len(result) == 1
        row = result[0]
        assert row["reason_code"] == "unattributed"
        assert row["has_confident_reason"] is False
        # The specific composite_at_surface (99) must not appear in reason_text
        # as a fabricated signal number. current_composite must also be None.
        assert row["current_composite"] is None
        assert "99" not in row["reason_text"], (
            f"Unattributed reason must not echo the surface composite (99), "
            f"got: {row['reason_text']!r}"
        )

    # ── reduce-call precedence ────────────────────────────────────────────────

    def test_reduce_call_wins_over_composite_skipped(self):
        """When ticker is in BOTH composite_skipped and reduce_calls, the
        reduce_call reason must win (higher priority)."""
        surfaced = [_make_surfaced("TSLA")]
        buckets = _make_buckets(
            composite_skipped=[{
                "ticker": "TSLA",
                "composite_score": 48.8,
                "composite_label": "Hold",
            }]
        )
        reduce_calls = {"TSLA": {"action": "TRIM", "why": "deterioration"}}
        result = derive_dropoffs(surfaced, set(), buckets, reduce_calls, set())
        assert len(result) == 1
        row = result[0]
        assert row["reason_code"] == "reduce_call"
        assert "Reduce/Exit" in row["reason_text"]
        assert row["has_confident_reason"] is True

    def test_reduce_calls_none_does_not_crash(self):
        """Passing reduce_calls=None must not raise; should fall through to
        composite_skipped reason."""
        surfaced = [_make_surfaced("NVDA")]
        buckets = _make_buckets(
            composite_skipped=[{
                "ticker": "NVDA",
                "composite_score": 60.0,
                "composite_label": "Hold",
            }]
        )
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert len(result) == 1
        assert result[0]["reason_code"] == "composite_below_bar"

    # ── never-resurrect ───────────────────────────────────────────────────────

    def test_currently_showing_ticker_not_in_dropped(self):
        """A ticker that surfaced today AND is still in current_new_pick_tickers
        must NOT appear in the dropped output."""
        surfaced = [_make_surfaced("AMZN")]
        current = {"AMZN"}
        buckets = _make_buckets()
        result = derive_dropoffs(surfaced, current, buckets, None, set())
        assert result == []

    def test_currently_showing_ticker_case_insensitive(self):
        """current_new_pick_tickers comparison is case-insensitive."""
        surfaced = [_make_surfaced("amzn")]
        current = {"AMZN"}
        buckets = _make_buckets()
        result = derive_dropoffs(surfaced, current, buckets, None, set())
        assert result == []

    # ── acted/held suppression ────────────────────────────────────────────────

    def test_acted_on_ticker_excluded_from_dropped(self):
        """A ticker that is in acted_or_held_tickers must be excluded from the
        dropped output, even if it no longer shows in new_picks."""
        surfaced = [_make_surfaced("GOOG")]
        buckets = _make_buckets()
        acted = {"GOOG"}
        result = derive_dropoffs(surfaced, set(), buckets, None, acted)
        assert result == []

    def test_acted_held_case_insensitive(self):
        """acted_or_held_tickers comparison is case-insensitive."""
        surfaced = [_make_surfaced("goog")]
        buckets = _make_buckets()
        acted = {"GOOG"}
        result = derive_dropoffs(surfaced, set(), buckets, None, acted)
        assert result == []

    # ── no-action shape ───────────────────────────────────────────────────────

    def test_output_dicts_have_no_action_keys(self):
        """Output dicts must not carry sizing/entry/stop/shares keys."""
        surfaced = [_make_surfaced("META")]
        buckets = _make_buckets()
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert len(result) == 1
        row = result[0]
        forbidden = {"sizing", "entry", "stop", "shares", "entry_lo", "entry_hi",
                     "total_cost", "port_pct", "stop_pct"}
        present = forbidden & set(row.keys())
        assert not present, f"Forbidden keys present: {present}"

    def test_output_contains_expected_keys(self):
        """Output dicts carry the required trace fields."""
        surfaced = [_make_surfaced("META")]
        buckets = _make_buckets()
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        row = result[0]
        expected = {
            "ticker", "first_seen_at", "composite_at_surface",
            "current_composite", "reason_code", "reason_text",
            "has_confident_reason",
        }
        assert expected <= set(row.keys())

    # ── sector / macro / unavailable reason paths ─────────────────────────────

    def test_sector_blocked_reason(self):
        surfaced = [_make_surfaced("INTC")]
        buckets = _make_buckets(
            sector_blocked_picks=[{
                "ticker": "INTC",
                "sector": "Technology",
                "score": 80.0,
                "reason": "Technology sector already ≥ 35% hard cap",
            }]
        )
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert result[0]["reason_code"] == "sector_blocked"
        assert "35%" in result[0]["reason_text"]

    def test_macro_blocked_reason(self):
        surfaced = [_make_surfaced("JPM")]
        buckets = _make_buckets(
            macro_blocked_picks=[{
                "ticker": "JPM",
                "sector": "Financials",
                "score": 76.0,
                "reason": "CPI release in 2 days — imminent high-impact event",
            }]
        )
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert result[0]["reason_code"] == "macro_blocked"
        assert "CPI" in result[0]["reason_text"]

    def test_composite_unavailable_reason(self):
        surfaced = [_make_surfaced("COIN")]
        buckets = _make_buckets(
            composite_unavailable=[{"ticker": "COIN", "sector": "Financials"}]
        )
        result = derive_dropoffs(surfaced, set(), buckets, None, set())
        assert result[0]["reason_code"] == "composite_unavailable"
        assert result[0]["has_confident_reason"] is True

    # ── deduplication / multiple tickers ─────────────────────────────────────

    def test_multiple_dropped_tickers_all_appear(self):
        surfaced = [_make_surfaced("AAPL"), _make_surfaced("GOOG"), _make_surfaced("MSFT")]
        current = {"MSFT"}  # MSFT is still showing
        buckets = _make_buckets()
        result = derive_dropoffs(surfaced, current, buckets, None, set())
        tickers = {r["ticker"] for r in result}
        assert "AAPL" in tickers
        assert "GOOG" in tickers
        assert "MSFT" not in tickers

    def test_empty_surfaced_returns_empty(self):
        result = derive_dropoffs([], set(), _make_buckets(), None, set())
        assert result == []

    def test_all_current_returns_empty(self):
        surfaced = [_make_surfaced("AAPL"), _make_surfaced("GOOG")]
        current = {"AAPL", "GOOG"}
        result = derive_dropoffs(surfaced, current, _make_buckets(), None, set())
        assert result == []

    def test_output_sorted_by_ticker(self):
        """derive_dropoffs returns rows sorted ascending by ticker."""
        surfaced = [_make_surfaced("TSLA"), _make_surfaced("AAPL"), _make_surfaced("MSFT")]
        result = derive_dropoffs(surfaced, set(), _make_buckets(), None, set())
        tickers = [r["ticker"] for r in result]
        assert tickers == sorted(tickers)

    # ── duplicate surfaced rows (earliest wins) ───────────────────────────────

    def test_duplicate_surfaced_rows_keep_earliest(self):
        """If two rows arrive for the same ticker, the one with the earlier
        first_seen_at timestamp must be kept."""
        earlier = _make_surfaced("NVDA", first_seen_at="2026-08-12T09:30:00+00:00", composite=68.0)
        later   = _make_surfaced("NVDA", first_seen_at="2026-08-12T10:15:00+00:00", composite=71.0)
        result = derive_dropoffs([later, earlier], set(), _make_buckets(), None, set())
        assert len(result) == 1
        assert result[0]["first_seen_at"] == "2026-08-12T09:30:00+00:00"
        assert result[0]["composite_at_surface"] == 68.0
