"""Tests for stock_analyzer.thesis_card — the 🧵 Thesis page's pure assembly
logic (Chunk A). No Streamlit, no DB, no LLM calls anywhere in the module
under test."""
from stock_analyzer import thesis_card as tc


# ── tag_for ───────────────────────────────────────────────────────────────

def test_tag_for_known_fields_match_documented_tags():
    assert tc.tag_for("header", "composite") == tc.TAG_DERIVED
    assert tc.tag_for("header", "shares") == tc.TAG_FACT
    assert tc.tag_for("header", "unrealized_pct") == tc.TAG_FACT
    assert tc.tag_for("thesis", "text") == tc.TAG_USER
    assert tc.tag_for("f1_review", "status") == tc.TAG_AI
    assert tc.tag_for("f1_review", "summary") == tc.TAG_AI
    assert tc.tag_for("red_team", "erosion_score") == tc.TAG_DERIVED
    assert tc.tag_for("red_team", "components") == tc.TAG_DERIVED
    assert tc.tag_for("red_team", "counter_evidence") == tc.TAG_AI
    assert tc.tag_for("debate", "verdict") == tc.TAG_AI
    assert tc.tag_for("debate", "key_dispute") == tc.TAG_AI
    assert tc.tag_for("analyst_coverage", "consensus_rating") == tc.TAG_ANALYST
    assert tc.tag_for("analyst_coverage", "price_at_article_date") == tc.TAG_FACT
    assert tc.tag_for("pre_mortem", "case_against") == tc.TAG_AI
    assert tc.tag_for("pre_mortem", "commitment") == tc.TAG_USER


def test_tag_for_unrecognized_field_defaults_to_ai_inference():
    assert tc.tag_for("thesis", "some_new_field_nobody_wired_yet") == tc.TAG_AI
    assert tc.tag_for("totally_unknown_section", "whatever") == tc.TAG_AI
    # explicitly never TAG_FACT for an unrecognized key
    assert tc.tag_for("unknown", "x") != tc.TAG_FACT


# ── assemble_thesis_card: everything missing ─────────────────────────────

def test_assemble_all_none_gives_disclosed_unavailable_everywhere():
    card = tc.assemble_thesis_card("NVDA")
    assert card["ticker"] == "NVDA"
    for section in ("thesis", "f1_review", "red_team", "debate",
                    "analyst_coverage", "pre_mortem"):
        assert card[section]["available"] is False
        assert isinstance(card[section]["reason"], str) and card[section]["reason"]
    # header: default portfolio_loaded=True + no position_row -> "not held"
    assert card["header"]["available"] is False
    assert card["header"]["reason"] == tc.REASON_NOT_HELD


def test_assemble_header_distinguishes_cold_session_from_not_held():
    cold = tc.assemble_thesis_card("NVDA", portfolio_loaded=False)
    assert cold["header"]["reason"] == tc.REASON_NO_PORTFOLIO

    not_held = tc.assemble_thesis_card("NVDA", portfolio_loaded=True)
    assert not_held["header"]["reason"] == tc.REASON_NOT_HELD
    assert cold["header"]["reason"] != not_held["header"]["reason"]


def test_assemble_all_none_never_raises():
    # Explicit None for every optional arg — must not raise.
    card = tc.assemble_thesis_card(
        "XYZ", position_row=None, trade_row=None, thesis_review_row=None,
        erosion=None, debate_row=None, analyst_row=None,
    )
    assert card["ticker"] == "XYZ"


# ── assemble_thesis_card: each section populated individually ───────────

def test_header_populated():
    card = tc.assemble_thesis_card(
        "NVDA", position_row={"composite": 72.0, "shares": 40, "unrealized_pct": 14.0},
    )
    hdr = card["header"]
    assert hdr["available"] is True
    assert hdr["composite"] == 72.0
    assert hdr["shares"] == 40
    assert hdr["unrealized_pct"] == 14.0
    assert hdr["composite_tag"] == tc.TAG_DERIVED
    assert hdr["shares_tag"] == tc.TAG_FACT
    assert "reason" not in hdr


def test_thesis_populated():
    card = tc.assemble_thesis_card(
        "NVDA", trade_row={
            "user_thesis": "Data-center demand compounds.",
            "thesis_source": "ai_edited",
            "trade_date": "2026-08-02",
        },
    )
    th = card["thesis"]
    assert th["available"] is True
    assert th["text"] == "Data-center demand compounds."
    assert th["thesis_source"] == "ai_edited"
    assert th["tag"] == tc.TAG_USER
    assert "reason" not in th


def test_thesis_blank_string_is_treated_as_missing():
    card = tc.assemble_thesis_card("NVDA", trade_row={"user_thesis": "   "})
    assert card["thesis"]["available"] is False
    assert card["thesis"]["reason"] == tc.REASON_NO_THESIS


def test_no_buy_row_says_no_buy_not_no_thesis():
    # A watchlist-only ticker has no BUY row at all — the card must not say
    # "no thesis saved for this ticker's most recent buy" (implies a buy that
    # never happened). A BUY with a blank thesis still gets REASON_NO_THESIS.
    card = tc.assemble_thesis_card("COIN", trade_row=None)
    assert card["thesis"]["reason"] == tc.REASON_NO_BUY
    assert card["pre_mortem"]["reason"] == tc.REASON_NO_BUY
    assert tc.REASON_NO_BUY != tc.REASON_NO_THESIS


def test_f1_review_populated():
    card = tc.assemble_thesis_card(
        "NVDA", thesis_review_row={
            "status": "WEAKENING", "summary": "Momentum has rolled over.",
            "reviewed_at": "2026-09-28T10:00:00",
        },
    )
    rv = card["f1_review"]
    assert rv["available"] is True
    assert rv["status"] == "WEAKENING"
    assert rv["summary"] == "Momentum has rolled over."
    assert rv["note"] == tc.EVIDENCE_NOTE_F1
    assert "reason" not in rv


def test_f1_review_three_states_not_collapsed():
    """Chunk B: offline (None) vs. checked-no-review-yet ({}) vs. a real
    saved review row must be three distinct, correctly-labeled outcomes —
    same tri-state contract as analyst_coverage below."""
    offline = tc.assemble_thesis_card("NVDA", thesis_review_row=None)
    assert offline["f1_review"]["available"] is False
    assert offline["f1_review"]["reason"] == tc.REASON_REVIEW_OFFLINE

    no_rows = tc.assemble_thesis_card("NVDA", thesis_review_row={})
    assert no_rows["f1_review"]["available"] is False
    assert no_rows["f1_review"]["reason"] == tc.REASON_NO_REVIEW

    populated = tc.assemble_thesis_card(
        "NVDA", thesis_review_row={
            "status": "INTACT", "summary": "Thesis holds.",
            "reviewed_at": "2026-09-28T10:00:00",
        },
    )
    rv = populated["f1_review"]
    assert rv["available"] is True
    assert rv["status"] == "INTACT"
    assert "reason" not in rv

    # the two unavailable reasons must be genuinely different messages
    assert offline["f1_review"]["reason"] != no_rows["f1_review"]["reason"]


def test_red_team_populated_preserves_none_vs_empty_counter_evidence():
    erosion_no_counter = {
        "erosion_score": 46.0, "erosion_label": "Eroding",
        "signals_snapshot": {"tier": "WATCH", "rs_vs_spy": -4.2,
                              "comp_delta": -3.1, "pt_pts": 7.0},
        "counter_evidence": None,
    }
    card1 = tc.assemble_thesis_card("NVDA", erosion=erosion_no_counter)
    rt1 = card1["red_team"]
    assert rt1["available"] is True
    assert rt1["erosion_score"] == 46.0
    assert rt1["components"]["tier"] == "WATCH"
    assert rt1["counter_evidence"] is None  # not attempted/failed, never []

    erosion_empty_counter = dict(erosion_no_counter, counter_evidence=[])
    card2 = tc.assemble_thesis_card("NVDA", erosion=erosion_empty_counter)
    assert card2["red_team"]["counter_evidence"] == []  # attempted, genuinely none

    erosion_with_counter = dict(
        erosion_no_counter,
        counter_evidence=["RS -4.2pp vs SPY undercuts the momentum leg."],
    )
    card3 = tc.assemble_thesis_card("NVDA", erosion=erosion_with_counter)
    assert card3["red_team"]["counter_evidence"] == [
        "RS -4.2pp vs SPY undercuts the momentum leg."
    ]


def test_red_team_not_scored_today():
    card = tc.assemble_thesis_card("NVDA", erosion=None)
    assert card["red_team"]["available"] is False
    assert card["red_team"]["reason"] == tc.REASON_EROSION_NOT_SCORED


def test_debate_populated():
    card = tc.assemble_thesis_card(
        "NVDA", debate_row={
            "verdict": "contested", "debate_type": "entry", "debate_date": "2026-09-12",
            "key_dispute": "whether the capex cycle has peaked.",
            "bull_case_score": 58, "bear_case_score": 55,
        },
    )
    d = card["debate"]
    assert d["available"] is True
    assert d["verdict"] == "contested"
    assert d["key_dispute"] == "whether the capex cycle has peaked."
    assert d["bull_case_score"] == 58
    assert "reason" not in d


def test_analyst_coverage_three_states_not_collapsed():
    offline = tc.assemble_thesis_card("NVDA", analyst_row=None)
    assert offline["analyst_coverage"]["reason"] == tc.REASON_ANALYST_OFFLINE

    no_rows = tc.assemble_thesis_card("NVDA", analyst_row={})
    assert no_rows["analyst_coverage"]["reason"] == tc.REASON_ANALYST_NONE

    populated = tc.assemble_thesis_card(
        "NVDA", analyst_row={
            "consensus_rating": "Strong Buy", "avg_pt": 180.0,
            "analysts": [
                {"firm": "Baird", "rating": "Buy", "price_target": 190},
                {"firm": "Goldman Sachs", "rating": "Buy", "price_target": 170},
            ],
            "article_date": "2026-09-15", "price_at_article_date": 171.20,
        },
    )
    ac = populated["analyst_coverage"]
    assert ac["available"] is True
    assert ac["consensus_rating"] == "Strong Buy"
    assert ac["avg_pt"] == 180.0
    # the raw per-firm list must never reach the render layer directly --
    # only a count (n_firms) does, so a firm's raw dict can't leak onto the
    # card as literal text (the real bug this pins: a live screenshot showed
    # "avg PT 370.0 ([{'firm': 'Bank of America', ...}] firms)" on screen).
    assert ac["n_firms"] == 2
    assert "analysts" not in ac
    assert "reason" not in ac

    # the two unavailable reasons must be genuinely different messages
    assert offline["analyst_coverage"]["reason"] != no_rows["analyst_coverage"]["reason"]


def test_count_analysts_handles_json_string_and_bad_input():
    assert tc._count_analysts([{"firm": "A"}, {"firm": "B"}]) == 2
    assert tc._count_analysts([]) == 0
    assert tc._count_analysts(None) == 0
    assert tc._count_analysts('[{"firm": "A"}]') == 1
    assert tc._count_analysts("not json") == 0
    assert tc._count_analysts(4) == 0


def test_pre_mortem_populated_and_missing():
    card = tc.assemble_thesis_card(
        "NVDA", trade_row={
            "premortem_case_against": [
                {"angle": "pillar", "argument": "Momentum-driven; reverts hard."},
            ],
            "premortem_commitment": "I'm wrong if bookings decelerate.",
            "trade_date": "2026-08-02",
        },
    )
    pm = card["pre_mortem"]
    assert pm["available"] is True
    assert pm["case_against"][0]["argument"] == "Momentum-driven; reverts hard."
    assert pm["commitment"] == "I'm wrong if bookings decelerate."
    assert "reason" not in pm

    empty = tc.assemble_thesis_card("NVDA", trade_row={"premortem_case_against": []})
    assert empty["pre_mortem"]["available"] is False
    assert empty["pre_mortem"]["reason"] == tc.REASON_NO_PREMORTEM

    none_row = tc.assemble_thesis_card("NVDA", trade_row=None)
    assert none_row["pre_mortem"]["available"] is False


# ── analyst_side_detailed ─────────────────────────────────────────────────

def test_analyst_side_detailed_distinguishes_strong_buy_from_buy():
    assert tc.analyst_side_detailed("Strong Buy") == "strong_buy"
    assert tc.analyst_side_detailed("Buy") == "buy"
    assert tc.analyst_side_detailed("Sell") == "sell"
    assert tc.analyst_side_detailed("Hold") == "neutral"
    assert tc.analyst_side_detailed(None) is None
    assert tc.analyst_side_detailed("") is None


# ── detect_tension ────────────────────────────────────────────────────────

def test_detect_tension_condition1_bullish_analyst_low_composite():
    msg = tc.detect_tension(60.0, "strong_buy", None, None)
    assert msg is not None
    assert "60" in msg


def test_detect_tension_condition1_plain_buy_also_qualifies():
    msg = tc.detect_tension(60.0, "buy", None, None)
    assert msg is not None


def test_detect_tension_condition2_weakening_with_strong_buy():
    msg = tc.detect_tension(None, "strong_buy", "WEAKENING", None)
    assert msg is not None
    assert "Weakening" in msg or "WEAKENING" in msg.upper() or "weakening" in msg.lower()


def test_detect_tension_condition2_broken_with_strong_buy():
    msg = tc.detect_tension(None, "strong_buy", "BROKEN", None)
    assert msg is not None


def test_detect_tension_condition3_bear_wins_high_composite():
    msg = tc.detect_tension(70.0, None, None, "bear_wins")
    assert msg is not None
    assert "70" in msg


def test_detect_tension_priority_condition1_beats_condition2_when_both_match():
    # analyst side "strong_buy", composite below COMPOSITE_BUY (65), and F-1
    # verdict WEAKENING: condition 1 (bullish analyst + low composite) and
    # condition 2 (WEAKENING + strong_buy) BOTH match on this input.
    # Condition 1 is checked first -> its message must win.
    # (Condition 1 and condition 3 can never co-fire: they require composite
    # below and at-or-above COMPOSITE_BUY respectively, which is a
    # contradiction — flagged separately as spec drift.)
    msg = tc.detect_tension(50.0, "strong_buy", "WEAKENING", None)
    assert msg is not None
    assert "50" in msg  # condition 1's message cites the composite value
    assert "Thesis Review" not in msg  # condition 2's message text must NOT appear


def test_detect_tension_none_when_nothing_matches():
    assert tc.detect_tension(None, None, None, None) is None
    assert tc.detect_tension(80.0, "buy", None, None) is None  # composite already >= COMPOSITE_BUY
    assert tc.detect_tension(80.0, "sell", None, None) is None
    assert tc.detect_tension(80.0, None, "INTACT", None) is None


def test_detect_tension_never_raises_on_none_inputs():
    # Every arg None simultaneously — must not raise, must return None.
    assert tc.detect_tension(None, None, None, None) is None
    # composite None specifically must not blow up the numeric comparisons.
    assert tc.detect_tension(None, "strong_buy", None, None) is None
    assert tc.detect_tension(None, None, None, "bear_wins") is None
    # non-numeric composite must not raise either.
    assert tc.detect_tension("not-a-number", "buy", None, None) is None
