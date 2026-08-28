"""
Tests for stock_analyzer/watchlist_advisor.py — previously zero test
coverage despite containing an actual portfolio-risk GATE
(_portfolio_risk_gate) that can downgrade a stock-level ENTER_NOW call.
"""
from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    TICKER_BETA_HIGH,
)
from stock_analyzer.watchlist_advisor import (
    _f,
    _earn_days_until,
    _pct_from_entry,
    _portfolio_risk_gate,
    build_watchlist_recommendation,
    sort_key_for_action,
)


# ─── Small helpers ────────────────────────────────────────────────────────────

def test_f_none_defaults_to_zero():
    assert _f(None) == 0.0


def test_f_nan_defaults_to_zero():
    assert _f(float("nan")) == 0.0


def test_f_valid_numeric_string_converts():
    assert _f("12.5") == 12.5


def test_f_unparseable_string_defaults():
    assert _f("not a number") == 0.0


def test_earn_days_until_none_returns_none():
    assert _earn_days_until(None) is None


def test_earn_days_until_malformed_string_returns_none():
    assert _earn_days_until("not-a-date") is None


def test_pct_from_entry_zero_entry_hi_returns_none():
    assert _pct_from_entry(100.0, 0) is None


def test_pct_from_entry_none_entry_hi_returns_none():
    assert _pct_from_entry(100.0, None) is None


def test_pct_from_entry_computes_correctly():
    assert _pct_from_entry(105.0, 100.0) == 5.0


# ─── _portfolio_risk_gate ─────────────────────────────────────────────────────

def test_gate_no_portfolio_ctx_returns_none():
    assert _portfolio_risk_gate(1.2, None) is None


def test_gate_empty_portfolio_ctx_returns_none():
    assert _portfolio_risk_gate(1.2, {}) is None


def test_gate_hard_breach_sector_at_ceiling():
    ctx = {"sector_weight_pct": 36.0, "sector_of_ticker": "Technology"}
    gate = _portfolio_risk_gate(1.0, ctx)
    assert gate is not None
    assert gate["severity"] == "hard"
    assert gate["kind"] == "sector"


def test_gate_no_breach_sector_just_under_ceiling():
    ctx = {"sector_weight_pct": 34.9, "sector_of_ticker": "Technology"}
    gate = _portfolio_risk_gate(1.0, ctx)
    assert gate is None or gate["severity"] != "hard"


def test_gate_hard_breach_beta():
    ctx = {"portfolio_beta": 1.5}  # > PORTFOLIO_BETA_CEILING (1.4)
    gate = _portfolio_risk_gate(2.0, ctx)  # ticker_beta > TICKER_BETA_CRITICAL (1.8)
    assert gate is not None
    assert gate["severity"] == "hard"
    assert gate["kind"] == "beta"


def test_gate_no_beta_breach_when_portfolio_beta_missing():
    ctx = {"portfolio_beta": None}
    assert _portfolio_risk_gate(2.0, ctx) is None


def test_gate_no_beta_breach_when_ticker_beta_missing():
    ctx = {"portfolio_beta": 1.5}
    assert _portfolio_risk_gate(None, ctx) is None


def test_gate_soft_sector_elevated():
    ctx = {"sector_weight_pct": 26.0, "sector_of_ticker": "Technology"}  # >= 25.0 elevated, < 35 ceiling
    gate = _portfolio_risk_gate(1.0, ctx)
    assert gate is not None
    assert gate["severity"] == "soft"


def test_gate_soft_beta_elevated_pair():
    ctx = {"portfolio_beta": 1.35}  # > PORTFOLIO_BETA_ELEVATED (1.3), < CEILING (1.4)
    gate = _portfolio_risk_gate(1.6, ctx)  # > TICKER_BETA_HIGH (1.5), < CRITICAL (1.8)
    assert gate is not None
    assert gate["severity"] == "soft"


def test_gate_soft_active_high_risk_alerts():
    ctx = {"active_high_risk_alerts": ["Concentration Risk"]}
    gate = _portfolio_risk_gate(1.0, ctx)
    assert gate is not None
    assert gate["severity"] == "soft"
    assert "Concentration Risk" in gate["reason"]


def test_high_risk_alert_caution_does_not_read_as_a_precondition():
    """It said "resolve in Portfolio → Risk Advisor first." — a blocking
    instruction — on a card this function deliberately KEEPS at ENTER_NOW,
    whose action block says "Open the position". Two opposing imperatives, no
    way to tell which governed. Found on a production screenshot 2026-08-28.

    The fix is the copy, not the severity: making it genuinely blocking would
    suppress every watchlist entry for as long as any HIGH alert stands, which
    is an investment-policy decision and not this one."""
    gate = _portfolio_risk_gate(1.0, {"active_high_risk_alerts": ["Concentration Risk"]})
    reason = gate["reason"]
    assert "Risk Advisor first" not in reason
    # Still names the alert, and still points at where to clear it.
    assert "Concentration Risk" in reason
    # The destination must be a page that EXISTS. There is no nav page named
    # "Risk Advisor" — verified against app.py's nav; the real path is
    # 🔗 Risk Analysis → Action Plan. Sending a user to a page name the app
    # does not have is a small fabrication, and it was copied verbatim at two
    # other sites, so pin the correct one rather than just "some pointer".
    assert "Risk Analysis" in reason
    assert "Risk Advisor" not in reason
    # But offers a way to PROCEED, like its three siblings do.
    assert " or " in reason
    # Not the sector concern's exact affordance: the two commonly co-occur and
    # are joined into one string, so identical wording read as two independent
    # halvings.
    assert "half-size" not in reason


def test_no_soft_concern_reads_as_a_blocking_precondition():
    """The CLASS, not the one string. Every soft concern rides on a card whose
    action stays ENTER_NOW, so each must state its condition and then offer a
    way to proceed — the pattern the three original siblings established
    ("consider a half-size entry", "use conservative sizing", "pick the
    higher-conviction setup, or wait a day"). A bare imperative here silently
    contradicts the recommendation it is attached to.

    HONEST SCOPE (narrowed after Opus review, 2026-08-28). This is a VOCABULARY
    proxy, not the invariant its name suggests. It genuinely regresses the bug
    it was written for — the old copy contained none of these words, verified by
    mutation — and it survives a rewrite of the wording, so it is not a plain
    change-detector. But it does NOT prove the class: "reduce exposure or
    de-risk first." and "Consider closing the position first." are both flat
    preconditions that would PASS. The invariant is not lexically testable in
    either direction — the current copy itself contains "before opening" — so a
    negative-keyword check would be equally brittle.

    Claiming more than this would make it the thing it is guarding against: a
    test that reads as a ratchet while proving something weaker."""
    _AFFORDANCES = ("consider", "conservative", "half-size", " or ")
    cases = {
        "sector":      {"sector_of_ticker": "Tech", "sector_weight_pct": SECTOR_ELEVATED},
        "beta":        {"portfolio_beta": PORTFOLIO_BETA_ELEVATED + 0.01},
        "high_alerts": {"active_high_risk_alerts": ["Concentration Risk"]},
        "grow_today":  {"sector_of_ticker": "Energy", "grow_today_sectors": {"Energy"}},
    }
    for label, ctx in cases.items():
        gate = _portfolio_risk_gate(TICKER_BETA_HIGH + 0.01, ctx)
        assert gate is not None, f"{label} produced no gate — test premise is stale"
        assert gate["severity"] == "soft", f"{label} is not soft; re-check this test"
        reason = gate["reason"].lower()
        assert any(a in reason for a in _AFFORDANCES), (
            f"{label} soft concern offers no way to proceed: {gate['reason']!r}"
        )


def test_gate_soft_same_sector_as_grow_today():
    ctx = {"sector_of_ticker": "Energy", "grow_today_sectors": {"Energy"}}
    gate = _portfolio_risk_gate(1.0, ctx)
    assert gate is not None
    assert gate["severity"] == "soft"


def test_gate_all_clear_returns_none():
    ctx = {
        "sector_weight_pct": 5.0, "sector_of_ticker": "Technology",
        "portfolio_beta": 0.9, "active_high_risk_alerts": [],
        "grow_today_sectors": set(),
    }
    assert _portfolio_risk_gate(1.0, ctx) is None


def test_gate_hard_breach_takes_priority_over_soft_concerns():
    # Sector at hard ceiling AND beta elevated -- must return the hard breach,
    # not merge both into a "soft" bucket.
    ctx = {
        "sector_weight_pct": 40.0, "sector_of_ticker": "Technology",
        "portfolio_beta": 1.35,
    }
    gate = _portfolio_risk_gate(1.6, ctx)
    assert gate["severity"] == "hard"
    assert gate["kind"] == "sector"


# ─── build_watchlist_recommendation — action classification ─────────────────

def _base_data(**overrides):
    data = {
        "total": 70.0,
        "rec": {"label": "Buy"},
        "current_price": 100.0,
        "entry_lo": 95.0,
        "entry_hi": 100.0,
        "stop": 90.0,
        "targets": {"base": 130.0},
        "earnings": None,
    }
    data.update(overrides)
    return data


def test_remove_on_low_score():
    rec = build_watchlist_recommendation("XYZ", _base_data(total=30.0))
    assert rec["action"] == "REMOVE"
    assert rec["priority"] == "HIGH"


# ─── Data-availability gate (2026-08-04 audit finding) ───────────────────────

def test_data_unavailable_withholds_instead_of_remove():
    """A low fabricated score with fundamentals unavailable must NOT issue a
    REMOVE call — it's a data outage, not a broken thesis."""
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(total=30.0, fundamentals_available=False)
    )
    assert rec["action"] == "DATA_UNAVAILABLE"
    assert rec["priority"] == "MEDIUM"


def test_data_unavailable_withholds_instead_of_enter_now():
    """A high fabricated score with valuation unavailable must NOT issue an
    ENTER_NOW call — same failure mode, other pillar."""
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(total=80.0, val_available=False)
    )
    assert rec["action"] == "DATA_UNAVAILABLE"


def test_data_available_both_flags_true_scores_normally():
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(total=30.0, fundamentals_available=True, val_available=True)
    )
    assert rec["action"] == "REMOVE"


def test_data_availability_flags_default_true_for_legacy_bundles():
    """Bundles built before this fix have neither key — must not be gated."""
    rec = build_watchlist_recommendation("XYZ", _base_data(total=30.0))
    assert rec["action"] == "REMOVE"


def test_remove_on_sell_signal_even_with_decent_score():
    rec = build_watchlist_recommendation("XYZ", _base_data(total=50.0, rec={"label": "Sell"}))
    assert rec["action"] == "REMOVE"


def test_remove_on_strong_sell_signal():
    rec = build_watchlist_recommendation("XYZ", _base_data(total=50.0, rec={"label": "Strong Sell"}))
    assert rec["action"] == "REMOVE"


def test_hold_off_earnings_when_imminent_and_score_good():
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(total=60.0, earnings="2026-08-01")
    )
    # today is mocked implicitly via _today_et() at call time; use a date
    # comfortably within 7 days by computing dynamically instead:
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=3)).isoformat()
    rec = build_watchlist_recommendation("XYZ", _base_data(total=60.0, earnings=soon))
    assert rec["action"] == "HOLD_OFF_EARNINGS"


def test_enter_now_when_all_conditions_align():
    rec = build_watchlist_recommendation("XYZ", _base_data())
    assert rec["action"] == "ENTER_NOW"
    assert rec["portfolio_caution"] is None


def test_enter_now_downgrades_to_near_entry_on_hard_portfolio_breach():
    ctx = {"sector_weight_pct": 40.0, "sector_of_ticker": "Technology"}
    rec = build_watchlist_recommendation("XYZ", _base_data(), portfolio_ctx=ctx)
    assert rec["action"] == "NEAR_ENTRY"
    assert rec["portfolio_caution"] is not None
    assert "35" in rec["portfolio_caution"] or "%" in rec["portfolio_caution"]


def test_enter_now_keeps_action_but_adds_soft_caution():
    ctx = {"sector_weight_pct": 27.0, "sector_of_ticker": "Technology"}
    rec = build_watchlist_recommendation("XYZ", _base_data(), portfolio_ctx=ctx)
    assert rec["action"] == "ENTER_NOW"
    assert rec["portfolio_caution"] is not None


def test_enter_now_never_files_a_soft_caution_as_a_pending_condition():
    """A soft concern is NOT an unmet condition — this branch is reached
    precisely because every condition passed, and the card says so in its own
    summary ("All conditions align for opening a position") and its action
    ("READY TO ENTER").

    It used to put the same caution string into conditions_missing as well, so
    the renderer filed it under "⏳ Conditions pending" beside both of those
    claims. Three statements from one branch, two contradicting the third.
    Seen on a production screenshot 2026-08-28.

    The caution is NOT lost: portfolio_caution carries the identical text into
    an amber banner above, which is the more prominent of the two surfaces."""
    ctx = {"sector_weight_pct": SECTOR_ELEVATED + 2.0, "sector_of_ticker": "Technology"}
    rec = build_watchlist_recommendation("XYZ", _base_data(), portfolio_ctx=ctx)
    assert rec["action"] == "ENTER_NOW"
    assert rec["conditions_missing"] == []
    # Still disclosed, just once and in the right place. Pins the sector FIGURE:
    # `or "%" in ...` would be near-tautological, since 3 of the 4 soft strings
    # contain a percent sign.
    assert rec["portfolio_caution"]
    assert f"{SECTOR_ELEVATED + 2.0:.0f}" in rec["portfolio_caution"]


def test_a_downgraded_card_DOES_still_list_its_blocker():
    """The mirror of the test above — proof the fix was scoped to the branch
    where the claim was false, not applied blindly. A HARD breach genuinely IS
    an unmet condition, so NEAR_ENTRY must keep populating conditions_missing;
    emptying it there would hide the reason the call was downgraded."""
    ctx = {"sector_weight_pct": SECTOR_CEILING + 5.0, "sector_of_ticker": "Technology"}
    rec = build_watchlist_recommendation("XYZ", _base_data(), portfolio_ctx=ctx)
    assert rec["action"] == "NEAR_ENTRY"
    assert rec["conditions_missing"], "a downgraded card must say what blocked it"
    # Assert WHAT it says, not merely that something is there — a non-empty
    # check would still pass if the blocker text were replaced by something
    # unrelated, which is the very failure this test claims to guard.
    assert any("Portfolio fit" in c for c in rec["conditions_missing"])


def test_enter_now_requires_validated_rr_not_just_present_price():
    # No target price at all -> rr stays None -> ENTER_NOW's rr gate fails,
    # so this must NOT enter even though score/zone conditions are met.
    rec = build_watchlist_recommendation("XYZ", _base_data(targets={}))
    assert rec["action"] != "ENTER_NOW"


def test_near_entry_in_zone_missing_target_gets_distinct_copy_not_approaching():
    # Regression test for the 2026-07-27 flagged UX gap (fixed 2026-07-28):
    # price is ALREADY inside the zone (entry_lo=95 <= 100 <= entry_hi=100)
    # but there's no target price at all, so rr stays None and ENTER_NOW's
    # gate fails. The generic NEAR_ENTRY "Approaching Entry Zone (+0.0% above
    # zone)" copy was actively misleading here -- price isn't approaching
    # anything, it already arrived; the real blocker is the missing target.
    rec = build_watchlist_recommendation("XYZ", _base_data(targets={}))
    assert rec["action"] == "NEAR_ENTRY"
    assert rec["title"] == "XYZ — In Entry Zone, R:R Not Yet Validated"
    assert "Approaching Entry Zone" not in rec["title"]
    assert "no validated price target" in rec["summary"]


def test_near_entry_in_zone_low_rr_gets_distinct_copy_not_approaching():
    # Same distinct-copy branch, but the target price IS known -- R:R is just
    # below the RR_ENTRY_MIN floor rather than missing entirely.
    rec = build_watchlist_recommendation("XYZ", _base_data(targets={"base": 115.0}))
    # base=115, price=100, stop=90 -> rr = (115-100)/(100-90) = 1.5 < RR_ENTRY_MIN(2.0)
    assert rec["action"] == "NEAR_ENTRY"
    assert rec["title"] == "XYZ — In Entry Zone, R:R Not Yet Validated"
    assert "1.5" in rec["summary"]
    assert "below the 2:1 minimum" in rec["summary"]


def test_near_entry_generic_approaching_copy_unaffected_when_out_of_zone():
    # The distinct in-zone branch must NOT swallow the ordinary "price above
    # zone, R:R would be fine if you were at the right price" case.
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(current_price=105.0, entry_lo=90.0, entry_hi=100.0)
    )
    assert rec["action"] == "NEAR_ENTRY"
    assert "Approaching Entry Zone" in rec["title"]


def test_near_entry_when_price_moderately_above_zone():
    # price 5% above entry_hi of 100 -> pct_above=5, not near_zone(<=3) but <=8
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(current_price=105.0, entry_lo=90.0, entry_hi=100.0)
    )
    assert rec["action"] == "NEAR_ENTRY"


def test_wait_entry_when_price_far_above_zone():
    rec = build_watchlist_recommendation(
        "XYZ", _base_data(current_price=120.0, entry_lo=90.0, entry_hi=100.0)
    )
    assert rec["action"] == "WAIT_ENTRY"


def test_wait_catalyst_for_middling_score():
    rec = build_watchlist_recommendation("XYZ", _base_data(total=50.0))
    assert rec["action"] == "WAIT_CATALYST"


def test_readiness_pct_is_higher_for_enter_now_than_wait_catalyst():
    enter = build_watchlist_recommendation("XYZ", _base_data())
    wait = build_watchlist_recommendation("XYZ", _base_data(total=50.0))
    assert enter["readiness_pct"] > wait["readiness_pct"]


def test_conditions_missing_never_contains_empty_strings():
    # _card() filters falsy entries out of conditions_met/conditions_missing
    rec = build_watchlist_recommendation("XYZ", _base_data())
    assert all(c for c in rec["conditions_met"])
    assert all(c for c in rec["conditions_missing"])


# ─── sort_key_for_action ──────────────────────────────────────────────────────

def test_sort_key_puts_enter_now_first():
    assert sort_key_for_action("ENTER_NOW") == 0


def test_sort_key_puts_near_entry_second():
    assert sort_key_for_action("NEAR_ENTRY") == 1


def test_sort_key_ranks_enter_now_ahead_of_remove_and_hold_off():
    # The watchlist page's actionability fix: opportunities must outrank
    # thesis-broken/earnings-hold in the default display order.
    assert sort_key_for_action("ENTER_NOW") < sort_key_for_action("REMOVE")
    assert sort_key_for_action("ENTER_NOW") < sort_key_for_action("HOLD_OFF_EARNINGS")
    assert sort_key_for_action("NEAR_ENTRY") < sort_key_for_action("REMOVE")
    assert sort_key_for_action("NEAR_ENTRY") < sort_key_for_action("HOLD_OFF_EARNINGS")


def test_sort_key_orders_remove_ahead_of_hold_off_earnings():
    assert sort_key_for_action("REMOVE") < sort_key_for_action("HOLD_OFF_EARNINGS")


def test_sort_key_orders_hold_off_ahead_of_wait_states():
    assert sort_key_for_action("HOLD_OFF_EARNINGS") < sort_key_for_action("WAIT_ENTRY")
    assert sort_key_for_action("HOLD_OFF_EARNINGS") < sort_key_for_action("WAIT_CATALYST")


def test_sort_key_orders_wait_entry_ahead_of_wait_catalyst():
    assert sort_key_for_action("WAIT_ENTRY") < sort_key_for_action("WAIT_CATALYST")


def test_sort_key_unknown_action_sorts_last():
    known_ranks = [
        sort_key_for_action(a)
        for a in (
            "ENTER_NOW", "NEAR_ENTRY", "REMOVE",
            "HOLD_OFF_EARNINGS", "WAIT_ENTRY", "WAIT_CATALYST",
        )
    ]
    assert sort_key_for_action("SOMETHING_UNRECOGNIZED") > max(known_ranks)


# ── The advisor must not claim a UI affordance exists (2026-08-28) ───────────
# The ENTER NOW copy used to end "Use the position sizing below to calibrate
# shares" — but that panel is WITHHELD whenever the portfolio value is unknown
# (F-261 refuses to size against a book it cannot measure), so a cold session
# was told to use something that was not on screen. Verified live from an owner
# screenshot. An advisor cannot see its own renderer; the concrete pointer now
# lives at the render layer, gated on the panel actually having rendered.

def _emitted_strings():
    """String CONSTANTS from the module AST, not a grep of the file.

    Deliberate: the comment explaining this fix necessarily contains the old
    phrase, and a substring search over the source could be made to pass or
    fail by that comment alone. Python `#` comments are absent from the AST.
    (Docstrings ARE ast.Constant nodes, so the explanation must stay a comment.)
    """
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "stock_analyzer" / "watchlist_advisor.py").read_text(encoding="utf-8")
    return [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_no_emitted_string_points_at_a_ui_element():
    joined = " ".join(_emitted_strings()).lower()
    for phrase in ("sizing below", "the panel below", "see below to calibrate"):
        assert phrase not in joined, phrase


def test_the_imperative_is_still_issued():
    """The app decides, it does not inform — removing the false pointer must
    not soften the call itself."""
    assert any("Open the position" in s for s in _emitted_strings())
