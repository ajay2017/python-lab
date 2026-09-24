"""Tests for stock_analyzer/util.py and stock_analyzer/market_time.py — the
shared helpers introduced 2026-08-04 to make the safe idiom the default for two
bug-classes the audits kept re-finding (offline-sentinel collapse, XSS) and the
NY-tz date-boundary class. Pure logic, no I/O.
"""
from datetime import datetime

from stock_analyzer.market_time import ET, now_et, today_et
from stock_analyzer import util
from stock_analyzer.util import (
    factor_tilt_evidence_line,
    factor_tilt_state,
    dropped_holdings_banner_text,
    get_or_offline,
    holdings_write_failed_message,
    md_bold_to_html,
    bq_score_or_none,
    numeric_or,
    pillar_tile,
    sentiment_value_or_none,
    val_score_or_none,
    xcheck_is_alarm_worthy,
    safe_html,
    stop_recovery_state,
    catalyst_watch_mini_state,
    earnings_posture_alert_count,
    catalyst_watch_nav_badge,
    signals_advice_nav_badge,
    benchmark_mirror_summary_state,
    defense_facet_badge,
)
import pytest

pytestmark = pytest.mark.fast


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


class TestNumericOr:
    """`x or default` cannot distinguish absent from measured-zero. For a 0-100
    pillar score those are opposite claims, so this is the numeric sibling of
    the offline-sentinel collapse TestGetOrOffline covers."""

    def test_legitimate_zero_survives(self):
        # THE bug this exists for: `0.0 or 50` == 50, inverting the most
        # bearish possible pillar reading into a neutral one.
        assert numeric_or(0.0, 50) == 0.0

    def test_legitimate_int_zero_survives(self):
        assert numeric_or(0, 50) == 0.0

    def test_none_falls_back(self):
        assert numeric_or(None, 50) == 50.0

    def test_missing_dict_read_falls_back(self):
        assert numeric_or({}.get("t_score"), 50) == 50.0

    def test_nan_falls_back(self):
        # `or` cannot do this: float('nan') is truthy, so it passes straight
        # through and renders as "nan".
        assert numeric_or(float("nan"), 50) == 50.0

    def test_positive_infinity_falls_back(self):
        assert numeric_or(float("inf"), 50) == 50.0

    def test_negative_infinity_falls_back(self):
        assert numeric_or(float("-inf"), 50) == 50.0

    def test_non_numeric_falls_back(self):
        assert numeric_or("65", 50) == 50.0
        assert numeric_or([], 50) == 50.0
        assert numeric_or({"a": 1}, 50) == 50.0

    def test_bool_is_not_a_number_here(self):
        # bool subclasses int, so True would otherwise become 1.0 — a score of
        # 1/100, silently. Deliberately rejected.
        assert numeric_or(True, 50) == 50.0
        assert numeric_or(False, 50) == 50.0

    def test_real_values_pass_through_as_float(self):
        assert numeric_or(72.4, 50) == 72.4
        assert numeric_or(65, 50) == 65.0
        assert isinstance(numeric_or(65, 50), float)

    def test_negative_values_pass_through(self):
        # Not every caller is a 0-100 score; a negative P&L% is legitimate.
        assert numeric_or(-10.59, 0) == -10.59

    def test_distinguishes_zero_from_absent(self):
        # The whole point, stated as one assertion.
        assert numeric_or(0.0, 50) != numeric_or(None, 50)

    def test_upgrade_trigger_arithmetic_is_not_corrupted(self):
        """Regression for the app.py "What would change this signal?" block.

        It derives the other pillars' contribution by subtracting
        `pillar * weight` from the REAL composite. A fabricated 50 understates
        that term and overstates the required target by exactly 50.
        """
        composite, weight, threshold = 60.0, 0.25, 65.0
        true_technical = 0.0

        scored = numeric_or(true_technical, 50)
        others = composite - scored * weight
        needed = (threshold - others) / weight
        assert needed == 20.0          # correct advice: 0 -> 20

        collapsed = true_technical or 50        # the old idiom
        bad_others = composite - collapsed * weight
        bad_needed = (threshold - bad_others) / weight
        assert bad_needed == 70.0      # what the user was actually shown


class TestBqScoreOrNone:
    """D24: a fabricated neutral 50 must not persist as if measured."""

    def test_available_passes_the_value_through(self):
        assert bq_score_or_none(61.0, {"bq_available": True}) == 61.0

    def test_unavailable_nulls_the_value(self):
        # fundamentals.py's fabricated neutral — must not persist as real.
        assert bq_score_or_none(50.0, {"bq_available": False}) is None

    def test_legacy_bundle_missing_the_flag_fails_open(self):
        # Matches the 5 existing consumers' `.get(key, True)` convention —
        # a legacy-shaped bundle is not retroactively distrusted.
        assert bq_score_or_none(72.0, {}) == 72.0

    def test_honours_the_legacy_fundamentals_available_alias(self):
        assert bq_score_or_none(50.0, {"fundamentals_available": False}) is None

    def test_none_bundle_fails_open(self):
        assert bq_score_or_none(72.0, None) == 72.0

    def test_a_genuine_measured_50_still_survives(self):
        # The point: gate on the FLAG, never on the value itself.
        assert bq_score_or_none(50.0, {"bq_available": True}) == 50.0


class TestValScoreOrNone:
    def test_available_passes_the_value_through(self):
        assert val_score_or_none(44.0, {"val_available": True}) == 44.0

    def test_unavailable_nulls_the_value(self):
        assert val_score_or_none(50.0, {"val_available": False}) is None

    def test_legacy_bundle_missing_the_flag_fails_open(self):
        assert val_score_or_none(30.0, {}) == 30.0


class TestSentimentValueOrNone:
    """The sentiment sibling has no flag (that gap is D3) — availability is
    derived from whether any headline was actually scored."""

    def test_populated_headlines_passes_the_value_through(self):
        b = {"headlines": [{"headline": "x", "score": 0.1}]}
        assert sentiment_value_or_none(72.0, b) == 72.0

    def test_empty_headlines_nulls_the_value(self):
        # analyze_news([]) -> avg 0.0 -> sentiment_score_0_100(0.0) == exactly
        # 50.0 — the fabricated neutral this function exists to catch.
        assert sentiment_value_or_none(50.0, {"headlines": []}) is None

    def test_explicit_sentiment_available_flag_wins_over_headlines(self):
        # A narrowed bundle (cron path) carries the precomputed bool instead
        # of the list — that key must take precedence when both are present.
        b = {"sentiment_available": True, "headlines": []}
        assert sentiment_value_or_none(60.0, b) == 60.0

    def test_explicit_false_sentiment_available_nulls_even_without_headlines(self):
        assert sentiment_value_or_none(55.0, {"sentiment_available": False}) is None

    def test_neither_signal_present_fails_open(self):
        # No "headlines" AND no "sentiment_available" — a hand-built or future
        # partial bundle, not a real scored one (which always carries
        # `headlines`, even as []). Fails open like its bq/val siblings,
        # rather than silently nulling a score for a shape it was never
        # asked to carry.
        assert sentiment_value_or_none(55.0, {}) == 55.0
        assert sentiment_value_or_none(55.0, None) == 55.0

    def test_a_genuine_measured_50_with_real_headlines_still_survives(self):
        b = {"headlines": [{"headline": "x", "score": 0.0}]}
        assert sentiment_value_or_none(50.0, b) == 50.0


class TestDroppedHoldingsBannerText:
    """D4: a holding dropped for a data-entry problem and one dropped for a
    data-provider problem must not be told to the user with the same
    "check the entry" text — that would send them looking for a bug that
    isn't there on the provider-outage case."""

    def test_empty_or_none_returns_none(self):
        assert dropped_holdings_banner_text([]) is None
        assert dropped_holdings_banner_text(None) is None

    def test_names_the_ticker(self):
        text = dropped_holdings_banner_text(
            [{"ticker": "BBB", "reason": "invalid_shares_or_cost"}]
        )
        assert "BBB" in text

    def test_invalid_shares_or_cost_says_check_the_entry(self):
        text = dropped_holdings_banner_text(
            [{"ticker": "BBB", "reason": "invalid_shares_or_cost"}]
        )
        assert "check the entry" in text
        assert "data-provider" not in text

    def test_no_price_data_does_not_say_check_the_entry(self):
        # The load-bearing distinction: the entry is fine here, so telling
        # the user to check it would be actively misleading.
        text = dropped_holdings_banner_text(
            [{"ticker": "DDD", "reason": "no_price_data"}]
        )
        assert "check the entry" not in text
        assert "data-provider" in text

    def test_missing_reason_key_defaults_to_invalid_shares_or_cost(self):
        # Backward-compat: a row from before the "reason" field existed is
        # correctly assumed to be the only reason a drop could have meant
        # then, not a guess.
        text = dropped_holdings_banner_text([{"ticker": "BBB"}])
        assert "check the entry" in text

    def test_mixed_reasons_both_appear_and_stay_distinguishable(self):
        text = dropped_holdings_banner_text([
            {"ticker": "BBB", "reason": "invalid_shares_or_cost"},
            {"ticker": "DDD", "reason": "no_price_data"},
        ])
        assert "BBB" in text and "DDD" in text
        assert "check the entry" in text
        assert "data-provider" in text

    def test_count_in_the_message_matches_the_row_count(self):
        text = dropped_holdings_banner_text([
            {"ticker": "BBB", "reason": "invalid_shares_or_cost"},
            {"ticker": "CCC", "reason": "invalid_shares_or_cost"},
        ])
        assert "2 holdings" in text

    def test_singular_wording_for_one_row(self):
        text = dropped_holdings_banner_text([{"ticker": "BBB"}])
        assert "1 holding " in text
        assert "1 holdings" not in text


class TestHoldingsWriteFailedMessage:
    """D2: a holdings-table write that did not happen must never be told to
    the user as having happened."""

    def test_names_the_ticker(self):
        msg = holdings_write_failed_message("AAPL")
        assert "AAPL" in msg

    def test_never_claims_success(self):
        msg = holdings_write_failed_message("AAPL").lower()
        for word in ("success", "added", "sold", "recorded", "✅"):
            assert word not in msg, word

    def test_points_at_the_recovery_path(self):
        msg = holdings_write_failed_message("AAPL")
        assert "Rebuild from trades" in msg

    def test_different_tickers_produce_different_messages(self):
        assert holdings_write_failed_message("AAPL") != holdings_write_failed_message("MSFT")

    def test_ticker_is_bolded_for_st_error(self):
        # Rendered via st.error (native markdown support, not unsafe_allow_html
        # HTML) so ** here is intentional emphasis, not a literal-bold leak.
        msg = holdings_write_failed_message("AAPL")
        assert "**AAPL**" in msg


class TestXcheckIsAlarmWorthy:
    """D23: a settled prev-close disagreement is always a fault; a live-only
    gap outside regular trading hours usually isn't — two sources may
    legitimately be quoting different things (a pre/post-market tick vs a
    stale close)."""

    def test_prev_close_breach_alarms_regardless_of_market_hours(self):
        r = {"prev_ok": False}
        assert xcheck_is_alarm_worthy(r, market_is_open=True) is True
        assert xcheck_is_alarm_worthy(r, market_is_open=False) is True

    def test_prev_close_breach_alarms_even_alongside_a_live_pass(self):
        r = {"prev_ok": False, "live_ok": True}
        assert xcheck_is_alarm_worthy(r, market_is_open=False) is True

    def test_live_only_breach_alarms_during_regular_hours(self):
        # The whole reason DATA_XCHECK_LIVE_TOL_PCT exists: a stale/wrong
        # intraday price DURING the session is a real signal.
        r = {"live_ok": False}
        assert xcheck_is_alarm_worthy(r, market_is_open=True) is True

    def test_live_only_breach_stays_quiet_outside_regular_hours(self):
        r = {"live_ok": False}
        assert xcheck_is_alarm_worthy(r, market_is_open=False) is False

    def test_prev_close_absent_and_live_ok_never_alarms(self):
        # prev_ok not False (True or absent) and live_ok True -> nothing to
        # report at all.
        assert xcheck_is_alarm_worthy({"live_ok": True}, market_is_open=True) is False
        assert xcheck_is_alarm_worthy({}, market_is_open=True) is False

    def test_prev_close_true_with_live_breach_still_follows_the_live_rule(self):
        r = {"prev_ok": True, "live_ok": False}
        assert xcheck_is_alarm_worthy(r, market_is_open=True) is True
        assert xcheck_is_alarm_worthy(r, market_is_open=False) is False

    def test_prev_close_breach_dominates_a_simultaneous_quiet_live_breach(self):
        # Both legs failing, market closed: the live leg alone would stay
        # quiet, but the prev-close leg's unconditional alarm must still win.
        r = {"prev_ok": False, "live_ok": False}
        assert xcheck_is_alarm_worthy(r, market_is_open=False) is True

    def test_never_mutates_the_input(self):
        r = {"prev_ok": False, "live_ok": False}
        xcheck_is_alarm_worthy(r, market_is_open=False)
        assert r == {"prev_ok": False, "live_ok": False}


class TestPillarTile:
    """A measured 50 and a fabricated 50 must not render identically."""

    INPUTS = "P/E · FCF Yield · PT Upside · Consensus"
    REASON = "No objective valuation metric was available."

    def test_available_shows_the_score(self):
        hdr, cap = pillar_tile("Valuation", 72.4, True, self.INPUTS, self.REASON)
        assert hdr == "**Valuation — 72/100**"
        assert cap == self.INPUTS

    def test_unavailable_withholds_the_number_entirely(self):
        hdr, cap = pillar_tile("Valuation", 50.0, False, self.INPUTS, self.REASON)
        assert "50" not in hdr
        assert "/100" not in hdr
        assert "not measured" in hdr
        assert cap == self.REASON

    def test_unavailable_does_not_assert_the_inputs(self):
        # The old caption claimed four inputs had produced the number. On a
        # withheld pillar none of them contributed.
        _, cap = pillar_tile("Valuation", 50.0, False, self.INPUTS, self.REASON)
        assert "P/E" not in cap
        assert "Consensus" not in cap

    def test_measured_fifty_still_renders_as_a_number(self):
        # The whole point: a real 50 is a legitimate mid reading and must show.
        hdr, _ = pillar_tile("Business Quality", 50.0, True, self.INPUTS, self.REASON)
        assert hdr == "**Business Quality — 50/100**"

    def test_measured_fifty_and_withheld_fifty_differ(self):
        measured, _ = pillar_tile("Valuation", 50.0, True, self.INPUTS, self.REASON)
        withheld, _ = pillar_tile("Valuation", 50.0, False, self.INPUTS, self.REASON)
        assert measured != withheld

    def test_zero_score_is_not_rewritten_to_neutral(self):
        # Composes with numeric_or — a genuine 0 is the most bearish reading.
        hdr, _ = pillar_tile("Valuation", 0.0, True, self.INPUTS, self.REASON)
        assert hdr == "**Valuation — 0/100**"

    def test_none_score_on_an_available_pillar_falls_back_to_neutral(self):
        # Defensive: an available pillar with no number is a shape bug, but it
        # must not crash the tile.
        hdr, _ = pillar_tile("Valuation", None, True, self.INPUTS, self.REASON)
        assert hdr == "**Valuation — 50/100**"

    def test_nan_score_never_renders_as_nan(self):
        hdr, _ = pillar_tile("Technical", float("nan"), True, self.INPUTS, self.REASON)
        assert "nan" not in hdr.lower()

    def test_falsy_availability_always_withholds(self):
        # The caller owns the fail-open default; this honours what it is handed.
        for falsy in (False, None, 0, ""):
            hdr, _ = pillar_tile("Valuation", 72.0, falsy, self.INPUTS, self.REASON)
            assert "not measured" in hdr, falsy

    def test_no_literal_markdown_bold_leaks_into_the_caption(self):
        # feedback_streamlit_renderer_mismatch: captions are not HTML-rendered,
        # so assert here where it is constructible rather than at the call site.
        for avail in (True, False):
            _, cap = pillar_tile("Valuation", 60.0, avail, self.INPUTS, self.REASON)
            assert "**" not in cap
            assert "$" not in cap


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


class TestFactorTiltEvidenceLine:
    """F-260 (2026-08-28). Both LLM narrative surfaces that consume
    `_pi_factor_tilt_cache` used to omit the factor line entirely when the data
    was absent, so the model received an evidence block indistinguishable from
    one where factor concentration HAD been measured and found unremarkable —
    and the app persisted the resulting narrative as the day's reading.
    """

    _VALID = {"portfolio_tilt": {"MTUM": 0.81, "VLUE": -0.20}}
    _MEASURED_EMPTY = {"positions": [], "portfolio_tilt": {}, "n_included": 0}

    def test_never_returns_empty_in_any_state(self):
        """The whole fix. A caller appends this unconditionally, so an empty
        return would silently restore the original defect."""
        for state in (None, self._MEASURED_EMPTY, self._VALID, {},
                      {"portfolio_tilt": {"MTUM": None}}):
            assert factor_tilt_evidence_line(state).strip()

    def test_not_measured_and_measured_empty_are_distinguishable(self):
        """THE defect: these two produced identical output (nothing). If this
        ever passes trivially again, the class is back."""
        assert factor_tilt_evidence_line(None) != factor_tilt_evidence_line(self._MEASURED_EMPTY)

    def test_absent_states_forbid_the_inference_rather_than_going_quiet(self):
        not_measured = factor_tilt_evidence_line(None)
        assert "NOT MEASURED" in not_measured
        # must actively block the wrong reading, not merely omit a number
        assert "not evidence" in not_measured.lower()
        measured_empty = factor_tilt_evidence_line(self._MEASURED_EMPTY)
        assert "unknown" in measured_empty.lower()
        assert "not a reading of 'no tilt'" in measured_empty.lower()

    def test_valid_reading_is_byte_identical_to_the_pre_fix_format(self):
        """The fix must not change what a SUCCESSFUL measurement says — only
        what the two failure states say."""
        assert factor_tilt_evidence_line(self._VALID) == (
            "Factor tilt: portfolio leans MTUM-tilted (weighted correlation +0.81)"
        )

    def test_dominant_factor_is_by_absolute_magnitude_not_signed_max(self):
        """A strong NEGATIVE tilt is as concentrated as a strong positive one."""
        line = factor_tilt_evidence_line({"portfolio_tilt": {"MTUM": 0.20, "USMV": -0.77}})
        assert "USMV-tilted" in line and "-0.77" in line

    def test_all_none_correlations_are_treated_as_measured_but_unusable(self):
        assert factor_tilt_evidence_line(
            {"portfolio_tilt": {"MTUM": None, "VLUE": None}}
        ) == factor_tilt_evidence_line(self._MEASURED_EMPTY)

    def test_malformed_input_degrades_to_unknown_never_raises(self):
        for junk in ({}, {"portfolio_tilt": None}, "nonsense", 42, []):
            assert "unknown" in factor_tilt_evidence_line(junk).lower()


class TestFactorTiltState:
    """`factor_tilt_state` is the SINGLE classifier read by both the LLM
    evidence line and app.py's on-screen disclosure, so the prompt and the user
    can never be told different things about which state the app is in."""

    def test_three_states(self):
        assert factor_tilt_state(None) == "not_measured"
        assert factor_tilt_state({"positions": [], "portfolio_tilt": {}, "n_included": 0}) == "unusable"
        assert factor_tilt_state({"portfolio_tilt": {"MTUM": None}}) == "unusable"
        assert factor_tilt_state({"portfolio_tilt": {"MTUM": 0.4}}) == "measured"

    def test_state_and_line_never_disagree(self):
        """If these two ever diverge, the caption and the prompt describe
        different realities — which is the defect class, re-created in the fix."""
        for value in (None, {}, {"portfolio_tilt": {}}, {"portfolio_tilt": {"M": None}},
                      {"portfolio_tilt": {"M": 0.5}}, "junk", 42, []):
            state, line = factor_tilt_state(value), factor_tilt_evidence_line(value)
            if state == "not_measured":
                assert "NOT MEASURED" in line
            elif state == "unusable":
                assert "unknown" in line.lower()
            else:
                assert "-tilted" in line

    def test_unusable_arm_names_no_specific_cause(self):
        """Caught in review 2026-08-28. An earlier draft said "(insufficient
        overlapping return history)" — ONE of five distinct ways factor_tilt
        can return its empty shape. Naming it would hand the model a specific
        fabricated cause to restate as fact inside a PERSISTED narrative: the
        same fabrication class this helper exists to close, one clause down."""
        line = factor_tilt_evidence_line({"portfolio_tilt": {}}).lower()
        for invented in ("insufficient overlapping", "too little history",
                         "not enough data", "fetch failed"):
            assert invented not in line
        assert "cause not distinguished" in line


class TestSizingCapLines:
    """Production screenshot 2026-08-28: 📋 Watchlist showed
    "capped to 15% single-name ceiling (risk-based would be 159 sh / ~19%)"
    directly above a result of 63 shares = 7.6% of portfolio. Every number was
    true, but the line named a cap that did not produce the figure shown — the
    15% ceiling would have allowed ~124 shares. The net-capital cap bound, and
    appeared only as "also"."""

    # The real ONON card, numbers reconciled against the screenshot.
    _ONON = {
        "shares": 63, "portfolio_pct": 7.6, "capital_pct": 25.0,
        "ceiling_capped": True, "ceiling_pct": 15.0,
        "uncapped_shares": 159, "uncapped_pct": 19.0,
        "capital_capped": True,
    }

    def test_no_cap_bound_says_nothing(self):
        assert util.sizing_cap_lines({"shares": 10}, 25.0) == []
        assert util.sizing_cap_lines(
            {"shares": 10, "ceiling_capped": False, "capital_capped": False}, 25.0) == []

    def test_when_both_fire_only_the_binding_one_claims_the_result(self):
        lines = util.sizing_cap_lines(self._ONON, 25.0)
        assert len(lines) == 2
        ceiling, capital = lines
        # THE defect: the ceiling line must not present itself as the answer.
        assert "63" not in ceiling, f"ceiling line claims the final size: {ceiling!r}"
        assert "7.6" not in ceiling
        # It still discloses what it did, so the chain is followable.
        assert "159" in ceiling and "15" in ceiling
        # And the binding one carries the result.
        assert "bound by" in capital
        assert "63" in capital and "7.6" in capital and "25" in capital

    def test_ceiling_alone_does_claim_the_result(self):
        ps = dict(self._ONON, capital_capped=False)
        (line,) = util.sizing_cap_lines(ps, 25.0)
        assert "63" in line, "nothing tightened it further, so it DID produce this"

    def test_capital_alone_does_not_say_then(self):
        ps = dict(self._ONON, ceiling_capped=False)
        (line,) = util.sizing_cap_lines(ps, 25.0)
        assert "then" not in line, "there was no prior step to follow"
        assert "bound by" in line

    def test_the_cap_percentage_is_passed_in_not_imported(self):
        """Keeps util.py policy-free: no threshold may live here."""
        import inspect
        src = inspect.getsource(util.sizing_cap_lines)
        assert "NET_CAPITAL" not in src
        assert "constants" not in src
        a = util.sizing_cap_lines(dict(self._ONON, ceiling_capped=False), 25.0)[0]
        b = util.sizing_cap_lines(dict(self._ONON, ceiling_capped=False), 30.0)[0]
        assert a != b, "the passed-in percentage is ignored"

    def test_malformed_input_never_raises(self):
        for junk in (None, "x", 5, [], {}):
            assert util.sizing_cap_lines(junk, 25.0) == []
        # Missing numeric fields degrade to 0 rather than KeyError/TypeError.
        assert util.sizing_cap_lines({"capital_capped": True}, 25.0)
        assert util.sizing_cap_lines(dict(self._ONON, shares=None), None)

    def test_no_markdown_bold_that_would_print_literally(self):
        """These render via st.caption. A ** here would show as asterisks —
        the renderer-mismatch class that only a screenshot catches."""
        for line in util.sizing_cap_lines(self._ONON, 25.0):
            assert "**" not in line
            assert "$" not in line


class TestSizingUnavailableCaption:
    """The fallback "why can't I size this?" caption — extracted from two
    byte-identical inline copies (📈 Analysis, 📋 Watchlist), Part 2 #3 of the
    2026-08-26 app review ("F-255 capital-cap wiring"). Every string here is
    transcribed character-for-character from the pre-extraction app.py copies
    — a changed word here is a rendered-copy regression, not a refactor."""

    _KW = dict(price=100.0, portfolio_value=1000.0, net_capital=500.0,
               single_ceiling_pct=15.0, capital_cap_pct=25.0)

    def test_ceiling_caption(self):
        kw = dict(self._KW, reason="ceiling")
        line = util.sizing_unavailable_caption(**kw)
        assert line == (
            "Position sizing unavailable — one share is "
            "~10% of your portfolio, above the "
            "15% single-name ceiling. The stop is fine; "
            "this name is too large for this account at the current cap."
        )

    def test_capital_caption_when_net_capital_positive(self):
        kw = dict(self._KW, reason="capital")
        line = util.sizing_unavailable_caption(**kw)
        assert line == (
            "Position sizing unavailable — one share is "
            "~20% of your net capital, above the "
            "25% net-capital cap. This is separate from "
            "the single-name book cap."
        )

    def test_portfolio_caption(self):
        kw = dict(self._KW, reason="portfolio")
        line = util.sizing_unavailable_caption(**kw)
        assert line == (
            "Position sizing unavailable — your portfolio value isn't loaded in this "
            "session, so any share count would be a guess. Open 🏠 Home to "
            "load it, then come back."
        )

    def test_stop_reason_and_none_reason_both_get_the_generic_caption(self):
        expected = "Position sizing unavailable — stop price too close to entry or not set."
        assert util.sizing_unavailable_caption(**dict(self._KW, reason="stop")) == expected
        assert util.sizing_unavailable_caption(**dict(self._KW, reason=None)) == expected

    def test_margin_called_capital_reason_falls_through_to_generic_caption(self):
        """KNOWN, DELIBERATELY UNCHANGED latent gap (not fixed here — see the
        function's own docstring): a margin-called account can produce
        reason == "capital" with a non-positive net_capital, and this pins
        the current (mis-attributing) behaviour rather than silently
        changing it under a "byte-identical refactor" banner."""
        expected = "Position sizing unavailable — stop price too close to entry or not set."
        assert util.sizing_unavailable_caption(
            **dict(self._KW, reason="capital", net_capital=0.0)
        ) == expected
        assert util.sizing_unavailable_caption(
            **dict(self._KW, reason="capital", net_capital=-500.0)
        ) == expected
        assert util.sizing_unavailable_caption(
            **dict(self._KW, reason="capital", net_capital=None)
        ) == expected

    def test_ceiling_unreachable_at_non_positive_portfolio_value(self):
        """Boundary proof for why the ceiling branch needs no separate
        portfolio_value guard beyond truthiness: `sizing_unavailable_reason`
        returns "portfolio" for any portfolio_value <= 0 BEFORE it can ever
        return "ceiling", so reason == "ceiling" already implies a positive
        portfolio_value at the only real caller."""
        from stock_analyzer.risk import sizing_unavailable_reason
        assert sizing_unavailable_reason(0, 100.0, 90.0, 15.0) == "portfolio"
        assert sizing_unavailable_reason(-1, 100.0, 90.0, 15.0) == "portfolio"

    def test_malformed_input_never_raises(self):
        for reason in (None, "ceiling", "capital", "portfolio", "stop", "junk"):
            util.sizing_unavailable_caption(
                reason, price=None, portfolio_value=None, net_capital=None,
                single_ceiling_pct=None, capital_cap_pct=None,
            )
        util.sizing_unavailable_caption(
            "ceiling", price="x", portfolio_value="y", net_capital="z",
            single_ceiling_pct="a", capital_cap_pct="b",
        )

    def test_no_markdown_bold_that_would_print_literally(self):
        for reason in ("ceiling", "capital", "portfolio", "stop"):
            line = util.sizing_unavailable_caption(**dict(self._KW, reason=reason))
            assert "**" not in line


class TestMdBoldToHtml:
    """Verified live from an owner screenshot 2026-08-28: 📋 Watchlist printed
    `**Open the position.**` with literal asterisks. Streamlit does not process
    markdown inside a raw `unsafe_allow_html` block, and `watchlist_advisor`
    bolds the IMPERATIVE — so the phrases designed to stand out were exactly
    the ones rendering broken, on a surface whose job is issuing a call."""

    def test_converts_bold_to_b_tags(self):
        assert md_bold_to_html("**Open the position**") == "<b>Open the position</b>"

    def test_multiple_spans_each_convert(self):
        assert md_bold_to_html("**A** then **B**") == "<b>A</b> then <b>B</b>"

    def test_escapes_before_converting(self):
        """Order is load-bearing. Escaping first leaves `**` intact (no HTML
        metacharacters); converting first would let the escape mangle the tags
        it had just produced."""
        out = md_bold_to_html("<script>alert(1)</script> **x**")
        assert "&lt;script&gt;" in out
        assert "<b>x</b>" in out
        assert "<script>" not in out

    def test_the_call_sites_previously_interpolated_raw_so_this_also_escapes(self):
        assert "&amp;" in md_bold_to_html("Tom & Jerry")

    def test_a_lone_marker_stays_literal(self):
        assert md_bold_to_html("a ** b") == "a ** b"

    def test_odd_marker_counts_pair_left_to_right_and_strand_the_rest(self):
        """Documents the REAL behaviour rather than the docstring's original
        overclaim that unbalanced markers are 'left as literal'. Three markers
        bold the first span and strand the third — which would land on the very
        phrase an author chose to emphasise. Not reachable today (all advisor
        spans are paired), pinned so it stays visible if that changes."""
        assert md_bold_to_html("x ** y **Open the position** z") == (
            "x <b> y </b>Open the position** z"
        )
        assert md_bold_to_html("***bold***") == "<b>*bold</b>*"

    def test_tag_balance_is_structural_so_output_is_never_malformed(self):
        """Whatever the marker count, every substitution emits exactly one open
        and one close — so a stranded marker is a copy defect, never broken
        layout. This is what makes the case above non-urgent."""
        for probe in ("**a**", "a ** b", "***x***", "**a** **b**", "****", "**"):
            out = md_bold_to_html(probe)
            assert out.count("<b>") == out.count("</b>")

    def test_newline_spanning_bold_is_left_alone(self):
        """No re.DOTALL. Verified no advisor bold span crosses lines; re-check
        before applying this helper to a new producer."""
        probe = "**a\nb**"
        assert md_bold_to_html(probe) == probe

    def test_non_greedy_so_two_spans_do_not_merge_into_one(self):
        assert md_bold_to_html("**A** x **B**").count("<b>") == 2

    def test_plain_text_is_unchanged_apart_from_escaping(self):
        assert md_bold_to_html("no markup here") == "no markup here"


# ── Nav-badge / mini-card tri-state classification (2026-09-24 app review) ──

class TestCatalystWatchMiniState:
    """A2: Summary's Catalyst Watch mini-card must not render a failed
    lookup identically to a genuine clean check."""

    def test_check_failed_renders_unknown_grey_regardless_of_count(self):
        line, color = catalyst_watch_mini_state(0, True)
        assert line == "Unknown"
        assert color == "#9ca3af"

    def test_check_failed_takes_priority_even_with_a_nonzero_count(self):
        # Defensive: a caller bug could pass a stale count alongside the
        # failure flag. Failure must still win, never show a specific number
        # from a check that didn't actually complete.
        line, color = catalyst_watch_mini_state(3, True)
        assert line == "Unknown"
        assert color == "#9ca3af"

    def test_genuine_zero_is_green_none_soon(self):
        line, color = catalyst_watch_mini_state(0, False)
        assert line == "None soon"
        assert color == "#22c55e"

    def test_genuine_positive_count_is_amber_reporting(self):
        line, color = catalyst_watch_mini_state(3, False)
        assert line == "3 reporting"
        assert color == "#f59e0b"

    def test_three_states_are_pairwise_distinct(self):
        failed  = catalyst_watch_mini_state(0, True)
        clean   = catalyst_watch_mini_state(0, False)
        alert   = catalyst_watch_mini_state(3, False)
        assert failed != clean
        assert failed != alert
        assert clean != alert


class TestEarningsPostureAlertCount:
    """A3: the count published to the nav-badge coordination cache must be
    None (not the fabricated 0 a genuine clean day also produces) when the
    lookup that built the playbook failed."""

    def test_check_failed_returns_none_regardless_of_playbook_contents(self):
        playbook = [{"action": "EXIT"}, {"action": "REDUCE"}]
        assert earnings_posture_alert_count(playbook, True) is None

    def test_check_failed_with_none_playbook_still_returns_none(self):
        assert earnings_posture_alert_count(None, True) is None

    def test_genuine_empty_playbook_returns_zero_not_none(self):
        """Distinguishes 'checked, nothing due' from 'could not check' --
        both must be representable and must not collapse to the same value."""
        assert earnings_posture_alert_count([], False) == 0

    def test_counts_only_exit_and_reduce_actions(self):
        playbook = [
            {"action": "EXIT"},
            {"action": "REDUCE"},
            {"action": "MONITOR"},
            {"action": "HOLD"},
        ]
        assert earnings_posture_alert_count(playbook, False) == 2

    def test_missing_action_key_does_not_raise(self):
        playbook = [{"ticker": "AAA"}]
        assert earnings_posture_alert_count(playbook, False) == 0


class TestCatalystWatchNavBadge:
    """A4: two INDEPENDENT sources (risk alerts, earnings alerts) share one
    badge slot -- a failure in either must not hide a known-good count from
    the other."""

    def test_both_clean_and_silent_renders_no_parts(self):
        assert catalyst_watch_nav_badge(0, False, 0, False) == []

    def test_risk_offline_shows_grey_regardless_of_earnings_state(self):
        parts = catalyst_watch_nav_badge(0, True, 0, False)
        assert parts == [":grey-background[● ?]"]

    def test_earnings_offline_shows_grey_regardless_of_risk_state(self):
        parts = catalyst_watch_nav_badge(0, False, 0, True)
        assert parts == [":grey-background[● ?]"]

    def test_risk_alerts_present_and_earnings_offline_shows_both_a_red_count_and_grey(self):
        """The load-bearing case: a real, known-good risk count must survive
        even though the earnings check failed -- collapsing both into one
        generic '?' would silently drop the red count."""
        parts = catalyst_watch_nav_badge(2, False, 0, True)
        assert f":red-background[● 2]" in parts
        assert ":grey-background[● ?]" in parts
        assert len(parts) == 2

    def test_earnings_alerts_present_and_risk_offline_shows_both_grey_and_orange_count(self):
        parts = catalyst_watch_nav_badge(0, True, 3, False)
        assert ":grey-background[● ?]" in parts
        assert f":orange-background[● 3]" in parts
        assert len(parts) == 2

    def test_both_sources_genuinely_alerting_shows_both_colors(self):
        parts = catalyst_watch_nav_badge(2, False, 3, False)
        assert parts == [":red-background[● 2]", ":orange-background[● 3]"]

    def test_zero_count_with_check_ok_renders_nothing_for_that_source(self):
        parts = catalyst_watch_nav_badge(0, False, 3, False)
        assert parts == [":orange-background[● 3]"]


class TestSignalsAdviceNavBadge:
    """A4: n_danger/n_warning are ONE source (published together, always
    offline or online in lockstep) -- unlike the Catalyst Watch badge's two
    independent sources, one combined '?' correctly replaces both slots."""

    def test_offline_shows_one_grey_regardless_of_stale_counts(self):
        # Defensive: even if a stale nonzero count somehow accompanied the
        # offline flag, offline must still win and show only the grey token.
        assert signals_advice_nav_badge(5, 5, True) == [":grey-background[● ?]"]

    def test_online_and_clean_renders_no_parts(self):
        assert signals_advice_nav_badge(0, 0, False) == []

    def test_online_with_danger_only(self):
        assert signals_advice_nav_badge(2, 0, False) == [":red-background[● 2]"]

    def test_online_with_warning_only(self):
        assert signals_advice_nav_badge(0, 3, False) == [":orange-background[● 3]"]

    def test_online_with_both_renders_danger_then_warning(self):
        assert signals_advice_nav_badge(2, 3, False) == [
            ":red-background[● 2]", ":orange-background[● 3]",
        ]


class TestBenchmarkMirrorSummaryState:
    """C1 (2026-09-24 app review): 🧾 Summary's Benchmark Mirror disclosure
    must never show a stale/absent verdict as if it were fresh, must prefer
    beta-adjusted alpha, and must fall back to raw alpha only when beta
    wasn't available -- mirroring My Edge's own fallback."""

    TODAY = "2026-09-24"

    def _cache(self, **overrides):
        base = {
            "date": self.TODAY, "benchmark": "SPY", "range_label": "Last 12 months",
            "is_ann": True, "alpha_ann": -12.3, "beta_adj_alpha": -18.7,
            "actual_disp": -5.0, "shadow_disp": 7.3, "dollar_gap": -1100.0,
        }
        base.update(overrides)
        return base

    def test_none_cache_returns_none(self):
        assert benchmark_mirror_summary_state(None, self.TODAY) is None

    def test_non_dict_cache_returns_none(self):
        assert benchmark_mirror_summary_state("not a dict", self.TODAY) is None
        assert benchmark_mirror_summary_state([], self.TODAY) is None

    def test_stale_date_returns_none_even_with_a_real_value(self):
        """The load-bearing guard: a cache from an earlier day in a
        long-lived session must not render as today's verdict."""
        stale = self._cache(date="2026-09-23")
        assert benchmark_mirror_summary_state(stale, self.TODAY) is None

    def test_fresh_cache_prefers_beta_adjusted_alpha(self):
        state = benchmark_mirror_summary_state(self._cache(), self.TODAY)
        assert state is not None
        assert state["label"] == "Beta-Adj. Alpha"
        assert state["value_text"] == "-18.7pp"
        assert "beta-adjusted" in state["basis"]

    def test_falls_back_to_raw_alpha_when_beta_adjusted_is_none(self):
        """Same fallback My Edge's own KPI strip uses when portfolio beta
        wasn't available this session -- must not fabricate a beta-adjusted
        number, and must not silently show nothing when a real raw alpha
        exists."""
        state = benchmark_mirror_summary_state(
            self._cache(beta_adj_alpha=None), self.TODAY,
        )
        assert state is not None
        assert state["label"] == "Your Alpha"
        assert state["value_text"] == "-12.3pp"
        assert "beta-adjusted" not in state["basis"]

    def test_both_alpha_values_none_returns_none(self):
        """Nothing measurable this session -- must not fabricate a 0."""
        state = benchmark_mirror_summary_state(
            self._cache(beta_adj_alpha=None, alpha_ann=None), self.TODAY,
        )
        assert state is None

    def test_positive_alpha_is_green(self):
        state = benchmark_mirror_summary_state(
            self._cache(beta_adj_alpha=4.2), self.TODAY,
        )
        assert state["color"] == "#57d98a"

    def test_negative_alpha_is_red(self):
        state = benchmark_mirror_summary_state(
            self._cache(beta_adj_alpha=-4.2), self.TODAY,
        )
        assert state["color"] == "#fca5a5"

    def test_exactly_zero_alpha_is_neutral_not_fabricated_positive_or_negative(self):
        state = benchmark_mirror_summary_state(
            self._cache(beta_adj_alpha=0.0), self.TODAY,
        )
        assert state["color"] == "#9ca3af"

    def test_basis_names_the_benchmark_and_window(self):
        state = benchmark_mirror_summary_state(
            self._cache(benchmark="QQQ", range_label="Last 6 months"), self.TODAY,
        )
        assert "QQQ" in state["basis"]
        assert "Last 6 months" in state["basis"]

    def test_period_label_reflects_annualized_flag(self):
        ann_state    = benchmark_mirror_summary_state(self._cache(is_ann=True), self.TODAY)
        period_state = benchmark_mirror_summary_state(self._cache(is_ann=False), self.TODAY)
        assert "ann." in ann_state["basis"]
        assert "total, <30d" in period_state["basis"]


class TestDefenseFacetBadge:
    """B1 (2026-09-24 app review): the Defense facet's SIGN READING is
    inverted relative to Offense's (negative protect_alpha is the GOOD
    outcome here) even though both now share the identical alpha formula.
    Extracted from what was an untested inline app.py conditional."""

    def test_building_band_shows_building_regardless_of_alpha(self):
        state = defense_facet_badge("building", None, n_mature=3, min_calls=8)
        assert state["badge"] == "BUILDING"
        assert state["value_text"] == "—"
        assert "5 more" in state["basis"]

    def test_building_band_with_none_needed_shows_maturing(self):
        state = defense_facet_badge("building", None, n_mature=8, min_calls=8)
        assert state["basis"] == "maturing"

    def test_no_data_when_alpha_is_none_but_band_is_not_building(self):
        """Defensive: band != 'building' with a None alpha is a shape bug,
        but must render neutral, never crash or fabricate a number."""
        state = defense_facet_badge("firm", None, n_mature=10, min_calls=8)
        assert state["badge"] == "NO DATA"
        assert state["value_text"] == "—"

    def test_firm_negative_alpha_is_validated_green(self):
        """The core sign-flip assertion: NEGATIVE is good for Defense,
        unlike Offense where positive is good."""
        state = defense_facet_badge("firm", -17.7, n_mature=22, min_calls=8)
        assert state["badge"] == "VALIDATED ✓"
        assert state["badge_color"] == "#57d98a"
        assert state["value_color"] == "#57d98a"
        assert state["value_text"] == "-17.7pp"

    def test_firm_positive_alpha_is_ran_early_amber(self):
        state = defense_facet_badge("firm", 12.3, n_mature=22, min_calls=8)
        assert state["badge"] == "RAN EARLY"
        assert state["badge_color"] == "#f0c24b"
        assert state["value_color"] == "#fca5a5"
        assert state["value_text"] == "+12.3pp"

    def test_early_band_is_early_read_regardless_of_sign(self):
        """band='early' (below firm_calls) never renders VALIDATED/RAN EARLY
        -- only 'firm' band commits to a verdict label."""
        negative_early = defense_facet_badge("early", -5.0, n_mature=10, min_calls=8)
        positive_early = defense_facet_badge("early", 5.0, n_mature=10, min_calls=8)
        assert negative_early["badge"] == "EARLY READ"
        assert positive_early["badge"] == "EARLY READ"

    def test_basis_names_the_flagged_count(self):
        state = defense_facet_badge("firm", -5.0, n_mature=22, min_calls=8)
        assert "22 flagged" in state["basis"]

    def test_zero_alpha_at_firm_band_is_ran_early_not_validated(self):
        """Boundary: exactly 0.0 is not < 0, so it falls to the 'firm, not
        VALIDATED' branch -- RAN EARLY. This is the ORIGINAL inline app.py
        logic's own pre-existing tie behavior (only the `>`/`<` DIRECTION
        flipped in B1, not this structure) and is worth noting it disagrees
        with the standalone exit_early_cost_analysis.py::classify_direction's
        separately-disclosed tie-goes-to-validated convention at exactly
        0.0 -- a real but pre-existing inconsistency, not introduced by
        this fix, and out of B1's scope (B1 reconciles the FORMULA, not
        this hypothetical exact-zero tie edge case)."""
        state = defense_facet_badge("firm", 0.0, n_mature=22, min_calls=8)
        assert state["badge"] == "RAN EARLY"
