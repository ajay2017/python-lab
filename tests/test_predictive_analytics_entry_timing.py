"""Regression tests for the Entry Timing tab's pure functions in
stock_analyzer/predictive_analytics.py — dedupe_repeated_tickers,
divergence_at_entry, forward_alpha_at_horizon, by_divergence_band,
find_illustrating_case, band_narrative.
See docs/plans/entry-timing-tab.md for the design this implements.
"""
from datetime import date

from stock_analyzer.predictive_analytics import (
    band_narrative,
    by_divergence_band,
    dedupe_repeated_tickers,
    divergence_at_entry,
    find_illustrating_case,
    forward_alpha_at_horizon,
    synthesize_directives,
)


def _rec(ticker, d, rec_type="new_pick", composite=70.0, momentum=95.0, **extra):
    row = {
        "ticker": ticker, "rec_date": d, "rec_type": rec_type,
        "composite_score": composite, "momentum_score": momentum,
    }
    row.update(extra)
    return row


# ── divergence_at_entry ──────────────────────────────────────────────────────

def test_divergence_at_entry_positive():
    assert divergence_at_entry(_rec("AMD", date(2026, 7, 9))) == 25.0


def test_divergence_at_entry_missing_scores_returns_none():
    assert divergence_at_entry({"ticker": "AMD"}) is None
    assert divergence_at_entry({"composite_score": 70}) is None


def test_divergence_at_entry_negative_is_still_computed():
    # Negative divergence is a valid computation here — filtering it out of
    # scope happens downstream in by_divergence_band, not in this function.
    assert divergence_at_entry(_rec("XYZ", date(2026, 7, 9), composite=90, momentum=60)) == -30.0


# ── dedupe_repeated_tickers ──────────────────────────────────────────────────

def test_dedupe_collapses_amd_style_repeat_firings():
    rows = [
        _rec("AMD", date(2026, 7, 9)),
        _rec("AMD", date(2026, 7, 10)),
        _rec("AMD", date(2026, 7, 14)),
        _rec("AMD", date(2026, 7, 15)),
        _rec("AMD", date(2026, 7, 22)),
    ]
    out = dedupe_repeated_tickers(rows, window_days=5)
    # 07-09 anchor absorbs 07-10/07-14 (both within 5 days of 07-09); 07-15 is
    # 6 days out -> new anchor; 07-22 is 7 days after 07-15 -> new anchor.
    assert [r["rec_date"] for r in out] == [date(2026, 7, 9), date(2026, 7, 15), date(2026, 7, 22)]


def test_dedupe_leaves_distinct_tickers_alone():
    rows = [_rec("AMD", date(2026, 7, 9)), _rec("NVDA", date(2026, 7, 9))]
    out = dedupe_repeated_tickers(rows, window_days=5)
    assert {r["ticker"] for r in out} == {"AMD", "NVDA"}


def test_dedupe_scopes_to_rec_types_and_passes_others_through():
    rows = [
        _rec("AMD", date(2026, 7, 9), rec_type="new_pick"),
        _rec("AMD", date(2026, 7, 10), rec_type="new_pick"),
        _rec("AMD", date(2026, 7, 9), rec_type="add_winner"),
        _rec("AMD", date(2026, 7, 10), rec_type="add_winner"),
    ]
    out = dedupe_repeated_tickers(rows, window_days=5, rec_types=("new_pick",))
    new_picks = [r for r in out if r["rec_type"] == "new_pick"]
    add_winners = [r for r in out if r["rec_type"] == "add_winner"]
    assert len(new_picks) == 1
    assert len(add_winners) == 2   # untouched, not deduped


def test_dedupe_keeps_undated_rows():
    rows = [_rec("AMD", None), _rec("AMD", date(2026, 7, 9))]
    out = dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 2


# ── forward_alpha_at_horizon ─────────────────────────────────────────────────

def test_forward_alpha_at_horizon_basic():
    spy_by_date = {date(2026, 7, 9): 500.0, date(2026, 7, 16): 505.0}

    def fake_fetch(ticker, start, end):
        assert ticker == "AMD"
        return 110.0   # forward close

    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, spy_by_date, historical_close_fn=fake_fetch,
    )
    # stock: +10%, SPY: +1% -> alpha ~ +9pp
    assert alpha == 9.0


def test_forward_alpha_at_horizon_missing_entry_price():
    assert forward_alpha_at_horizon("AMD", date(2026, 7, 9), None, 5, {}) is None
    assert forward_alpha_at_horizon("AMD", date(2026, 7, 9), 0.0, 5, {}) is None


def test_forward_alpha_at_horizon_fetch_returns_none():
    alpha = forward_alpha_at_horizon(
        "DELISTED", date(2026, 7, 9), 100.0, 5, {"x": 1},
        historical_close_fn=lambda t, s, e: None,
    )
    assert alpha is None


def test_forward_alpha_at_horizon_fetch_raises_is_caught():
    def boom(t, s, e):
        raise RuntimeError("network down")
    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, {}, historical_close_fn=boom,
    )
    assert alpha is None


def test_forward_alpha_at_horizon_missing_spy_series_returns_none():
    alpha = forward_alpha_at_horizon(
        "AMD", date(2026, 7, 9), 100.0, 5, None,
        historical_close_fn=lambda t, s, e: 110.0,
    )
    assert alpha is None


# ── by_divergence_band ───────────────────────────────────────────────────────

def _band_row(divergence, day1_alpha=None, day5_alpha=None, alpha_pct=None, maturing=False):
    return {
        "divergence": divergence, "day1_alpha": day1_alpha, "day5_alpha": day5_alpha,
        "alpha_pct": alpha_pct, "outcome_maturing": maturing,
    }


def test_by_divergence_band_excludes_non_positive_divergence():
    rows = [
        _band_row(divergence=-5, day1_alpha=-10),
        _band_row(divergence=0, day1_alpha=-10),
        _band_row(divergence=None, day1_alpha=-10),
    ]
    assert by_divergence_band(rows, aligned_max=15, diverging_max=25) == []


def test_by_divergence_band_buckets_by_threshold():
    rows = [
        _band_row(divergence=10, day1_alpha=-2),    # Aligned
        _band_row(divergence=20, day1_alpha=-8),    # Diverging
        _band_row(divergence=30, day1_alpha=-15),   # Extreme
    ]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    labels = {b["band_label"]: b for b in bands}
    assert set(labels) == {"Aligned", "Diverging", "Extreme"}
    assert labels["Aligned"]["day1_alpha"] == -2
    assert labels["Extreme"]["day1_alpha"] == -15


def test_by_divergence_band_pct_red_and_day20_reuses_mature_alpha():
    rows = [
        _band_row(divergence=30, day1_alpha=-5, alpha_pct=-3, maturing=False),
        _band_row(divergence=35, day1_alpha=5,  alpha_pct=8,  maturing=False),
        _band_row(divergence=40, day1_alpha=-2, alpha_pct=None, maturing=True),  # excluded from day20
    ]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    extreme = next(b for b in bands if b["band_label"] == "Extreme")
    assert extreme["day1_n"] == 3
    assert extreme["day1_pct_red"] == round(2 / 3, 3)
    assert extreme["day20_n"] == 2   # the maturing row is excluded
    assert extreme["day20_alpha"] == round((-3 + 8) / 2, 2)
    assert extreme["p_positive_alpha"] == 0.5   # 1 of 2 day20 outcomes positive


def test_by_divergence_band_missing_horizon_data_is_none_not_zero():
    rows = [_band_row(divergence=10, day1_alpha=None, day5_alpha=None, alpha_pct=None, maturing=True)]
    bands = by_divergence_band(rows, aligned_max=15, diverging_max=25)
    aligned = bands[0]
    assert aligned["n"] == 1
    assert aligned["day1_alpha"] is None
    assert aligned["day1_n"] == 0
    assert aligned["day20_alpha"] is None


# ── find_illustrating_case ───────────────────────────────────────────────────

def test_find_illustrating_case_amd_style_repeat_offender():
    rows = [
        _rec("AMD", date(2026, 7, 9),  composite=69, momentum=95),   # divergence 26
        _rec("AMD", date(2026, 7, 10), composite=69, momentum=94),   # divergence 25 -- NOT Extreme (not > 25)
        _rec("AMD", date(2026, 7, 14), composite=71, momentum=100, alpha_pct=-9.0, outcome_maturing=False),   # divergence 29
        _rec("AMD", date(2026, 7, 15), composite=70, momentum=96, alpha_pct=-11.0, outcome_maturing=False),   # divergence 26
        _rec("NVDA", date(2026, 7, 9), composite=90, momentum=91),   # divergence 1 -- not Extreme
    ]
    case = find_illustrating_case(rows, diverging_max=25)
    assert case is not None
    assert case["ticker"] == "AMD"
    assert case["n_firings"] == 3   # row2 (divergence 25) is Diverging, not Extreme -- excluded
    assert case["first_date"] == date(2026, 7, 9)
    assert case["last_date"] == date(2026, 7, 15)
    assert case["alpha_min"] == -11.0
    assert case["alpha_max"] == -9.0


def test_find_illustrating_case_returns_none_below_two_firings():
    rows = [_rec("AMD", date(2026, 7, 9), composite=70, momentum=95)]
    assert find_illustrating_case(rows, diverging_max=25) is None


def test_find_illustrating_case_ignores_aligned_and_diverging_rows():
    rows = [
        _rec("XYZ", date(2026, 7, 9), composite=70, momentum=75),   # divergence 5 -- Aligned
        _rec("XYZ", date(2026, 7, 10), composite=70, momentum=75),
    ]
    assert find_illustrating_case(rows, diverging_max=25) is None


def test_find_illustrating_case_picks_most_repeated_ticker():
    rows = [
        _rec("AMD", date(2026, 7, 9), composite=70, momentum=95),
        _rec("AMD", date(2026, 7, 10), composite=70, momentum=95),
        _rec("TSLA", date(2026, 7, 9), composite=68, momentum=98),
        _rec("TSLA", date(2026, 7, 10), composite=68, momentum=98),
        _rec("TSLA", date(2026, 7, 11), composite=68, momentum=98),
    ]
    case = find_illustrating_case(rows, diverging_max=25)
    assert case["ticker"] == "TSLA"
    assert case["n_firings"] == 3


# ── band_narrative ────────────────────────────────────────────────────────────

def test_band_narrative_missing_day1_data():
    text = band_narrative({"day1_alpha": None, "day5_alpha": None, "day20_alpha": None})
    assert "not enough" in text.lower()


def test_band_narrative_flat_to_positive():
    text = band_narrative({"day1_alpha": 0.2, "day5_alpha": 0.9, "day20_alpha": 2.4})
    assert "flat-to-positive" in text.lower()


def test_band_narrative_late_developing_drawdown_not_masked_by_flat_day1():
    # Regression: a live screenshot (2026-07-28) showed Day+1 ~0, Day+5 slightly
    # positive, Day+20 -14pp -- and the narrative wrongly said "not enough
    # Day+20 history" even though day20_alpha/day20_n were both present. The
    # "Day+20 is the worst point" branch must fire before the flat/positive
    # early-day branches, regardless of Day+1's sign.
    band = {"day1_alpha": -0.04, "day5_alpha": 0.3, "day20_alpha": -14.0, "day20_n": 9}
    text = band_narrative(band)
    assert "not enough" not in text.lower()
    assert "-14.0pp" in text
    assert "9 outcomes" in text
    assert "shows up late" in text.lower()


def test_band_narrative_late_developing_drawdown_singular_outcome_count():
    band = {"day1_alpha": 0.0, "day5_alpha": 0.1, "day20_alpha": -5.0, "day20_n": 1}
    text = band_narrative(band)
    assert "(1 outcome)" in text
    assert "1 outcomes" not in text


def test_band_narrative_monotonic_worsening_is_not_the_late_blowup_case():
    # Day+1 is ALREADY clearly negative (not "calm") -- this is a different,
    # already-covered pattern (consistent worsening), not the surprise-late-
    # loss case, even though Day+20 is still numerically the worst point.
    text = band_narrative({"day1_alpha": -8.0, "day5_alpha": -9.0, "day20_alpha": -10.0})
    assert "looks calm" not in text.lower()
    assert "no recovery pattern" in text.lower()


def test_band_narrative_recovers_by_day5():
    text = band_narrative({"day1_alpha": -2.0, "day5_alpha": 1.0, "day20_alpha": 3.0})
    assert "recovers quickly" in text.lower()
    assert "+3.0pp" in text


def test_band_narrative_recovers_by_day20_only():
    text = band_narrative({"day1_alpha": -1.0, "day5_alpha": -0.5, "day20_alpha": 1.5})
    assert "turns positive by day+20" in text.lower()


def test_band_narrative_deepest_drawdown_partial_recovery():
    text = band_narrative({"day1_alpha": -8.0, "day5_alpha": -4.0, "day20_alpha": -2.0})
    assert "deepest drawdown" in text.lower()


def test_band_narrative_no_recovery():
    text = band_narrative({"day1_alpha": -8.0, "day5_alpha": -9.0, "day20_alpha": -10.0})
    assert "no recovery pattern" in text.lower()


def test_band_narrative_appends_illustrating_ticker():
    text = band_narrative(
        {"day1_alpha": -8.0, "day5_alpha": -4.0, "day20_alpha": -2.0},
        illustrating_ticker="AMD",
    )
    assert text.endswith("This is the AMD-shaped case.")


# ── synthesize_directives — Entry Timing directive ───────────────────────────
# Regression coverage for a bug caught during Phase 2 design review
# (2026-07-28): the ORIGINAL directive was gated on day1_n/day1_pct_red and
# claimed picks "open red on Day+1... though the effect fades by maturity" --
# backwards from the validated shape (calm at Day+1, damage shows up by
# Day+20, per band_narrative's primary branch). The directive must be gated
# on day20_n/day20_alpha and describe the correct direction.

def _avm_insufficient():
    return {"edge": "insufficient", "edge_pp": None}


def test_entry_timing_directive_cites_day20_and_correct_direction():
    bands = [{
        "band_label": "Extreme", "n": 20,
        "day1_alpha": -0.04, "day1_pct_red": 0.5, "day1_n": 20,
        "day5_alpha": 0.3, "day5_pct_red": 0.41, "day5_n": 17,
        "day20_alpha": -14.2, "p_positive_alpha": 0.5, "day20_n": 20,
    }]
    directives = synthesize_directives(
        bands=[], thresh=None, avm=_avm_insufficient(), conv=[], rtype=[], sec_alph=[],
        n_graded=0, min_n=5, entry_timing_bands=bands,
    )
    et_dirs = [d for d in directives if d["source_tab"] == "⏱️ Entry Timing"]
    assert len(et_dirs) == 1
    assert et_dirs[0]["type"] == "caution"
    text = et_dirs[0]["text"]
    assert "opened red on day+1" not in text.lower()
    assert "fade by the time" not in text.lower()
    assert "-14.2pp" in text
    assert "calm" in text.lower()
    assert "late" in text.lower()


def test_entry_timing_directive_absent_when_day20_sample_thin():
    # OLD (buggy) gate checked day1_n/day1_pct_red, which would have fired
    # here despite a thin Day+20 sample (n=2) -- the directive is ABOUT
    # Day+20, so that's the horizon that must clear min_n.
    bands = [{
        "band_label": "Extreme", "n": 20,
        "day1_alpha": -5.0, "day1_pct_red": 0.9, "day1_n": 20,
        "day5_alpha": -3.0, "day5_pct_red": 0.8, "day5_n": 17,
        "day20_alpha": -14.2, "p_positive_alpha": 0.2, "day20_n": 2,
    }]
    directives = synthesize_directives(
        bands=[], thresh=None, avm=_avm_insufficient(), conv=[], rtype=[], sec_alph=[],
        n_graded=0, min_n=5, entry_timing_bands=bands,
    )
    assert not [d for d in directives if d["source_tab"] == "⏱️ Entry Timing"]


def test_entry_timing_directive_absent_when_day20_alpha_not_negative():
    bands = [{
        "band_label": "Extreme", "n": 20,
        "day1_alpha": 0.0, "day1_pct_red": 0.5, "day1_n": 20,
        "day5_alpha": 0.3, "day5_pct_red": 0.4, "day5_n": 17,
        "day20_alpha": 2.0, "p_positive_alpha": 0.8, "day20_n": 20,
    }]
    directives = synthesize_directives(
        bands=[], thresh=None, avm=_avm_insufficient(), conv=[], rtype=[], sec_alph=[],
        n_graded=0, min_n=5, entry_timing_bands=bands,
    )
    assert not [d for d in directives if d["source_tab"] == "⏱️ Entry Timing"]


def test_entry_timing_directive_absent_when_no_extreme_band():
    directives = synthesize_directives(
        bands=[], thresh=None, avm=_avm_insufficient(), conv=[], rtype=[], sec_alph=[],
        n_graded=0, min_n=5, entry_timing_bands=[{"band_label": "Aligned", "n": 12}],
    )
    assert not [d for d in directives if d["source_tab"] == "⏱️ Entry Timing"]
