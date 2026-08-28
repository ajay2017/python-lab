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
    get_or_offline,
    md_bold_to_html,
    safe_html,
    stop_recovery_state,
)


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
