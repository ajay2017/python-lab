"""
Tests for stock_analyzer/predictive_analytics.py — the calibration/synthesis
substrate behind the "📊 Predictive Analytics" page (Score Calibration,
Decision Quality, Signal Breakdown, Sector Alpha, and Entry Timing tabs).

Pure functions only, operating on plain lists/dicts shaped like
recommendations_history.compute_outcomes()'s output. Previously zero test
coverage despite driving `synthesize_directives()` — the single function that
turns four independent statistical readouts into the ranked, plain-English
directives a user actually reads. A silent ordering/threshold bug here either
buries an actionable directive under noise or promotes a non-finding to
"action" — exactly the kind of miscalibration this app exists to avoid.
`band_narrative()`'s branch ORDERING is a deliberately-fixed, previously-live
bug (a near-zero Day+1 reading masking a real Day+20 loss); this file verifies
the fix thoroughly rather than re-breaking it.
"""
from datetime import date, timedelta

import pytest

from stock_analyzer import predictive_analytics as pa
from stock_analyzer.constants import NYSE_HOLIDAYS


# ─── builders ───────────────────────────────────────────────────────────────

def _row(ticker="AAA", rec_date=date(2026, 1, 1), rec_type="new_pick",
         verdict="Confirmed", conviction="High", sector="Tech",
         composite_score=70.0, momentum_score=60.0, acted_on=False,
         alpha_pct=1.0, outcome_pct=1.0, outcome_maturing=False):
    return {
        "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
        "verdict": verdict, "conviction": conviction, "sector": sector,
        "composite_score": composite_score, "momentum_score": momentum_score,
        "acted_on": acted_on, "alpha_pct": alpha_pct, "outcome_pct": outcome_pct,
        "outcome_maturing": outcome_maturing,
    }


def _cband(n, avg_alpha, band_floor=0, band_label=None):
    return {"n": n, "avg_alpha": avg_alpha, "band_floor": band_floor,
            "band_label": band_label or f"{band_floor}-{band_floor+4}"}


def _sband(sector, avg_alpha, n):
    return {"sector": sector, "avg_alpha": avg_alpha, "n": n}


def _rtband(label, avg_alpha):
    return {"label": label, "avg_alpha": avg_alpha}


def _convband(conviction, avg_alpha):
    return {"conviction": conviction, "avg_alpha": avg_alpha}


def _dband_row(divergence=None, day1_alpha=None, day5_alpha=None,
                outcome_maturing=False, alpha_pct=None):
    return {"divergence": divergence, "day1_alpha": day1_alpha,
            "day5_alpha": day5_alpha, "outcome_maturing": outcome_maturing,
            "alpha_pct": alpha_pct}


def _fic_row(ticker="AAA", rec_date=None, composite_score=50.0,
             momentum_score=80.0, outcome_maturing=False, alpha_pct=None):
    return {"ticker": ticker, "rec_date": rec_date,
            "composite_score": composite_score, "momentum_score": momentum_score,
            "outcome_maturing": outcome_maturing, "alpha_pct": alpha_pct}


def _band(day1_alpha=None, day5_alpha=None, day20_alpha=None, day20_n=1):
    return {"day1_alpha": day1_alpha, "day5_alpha": day5_alpha,
            "day20_alpha": day20_alpha, "day20_n": day20_n}


# ══════════════════════════════ Signal Calibration ══════════════════════════

# ─── calibration_by_score_band ───────────────────────────────────────────────

def test_calibration_by_score_band_excludes_maturing_rows():
    rows = [_row(outcome_maturing=True, alpha_pct=5.0)]
    assert pa.calibration_by_score_band(rows) == []


def test_calibration_by_score_band_excludes_none_alpha():
    rows = [_row(alpha_pct=None)]
    assert pa.calibration_by_score_band(rows) == []


def test_calibration_by_score_band_floor_67_lands_in_65():
    out = pa.calibration_by_score_band([_row(composite_score=67.0)], band_size=5)
    assert out[0]["band_floor"] == 65
    assert out[0]["band_label"] == "65–69"


def test_calibration_by_score_band_floor_64_99_lands_in_60():
    out = pa.calibration_by_score_band([_row(composite_score=64.99)], band_size=5)
    assert out[0]["band_floor"] == 60
    assert out[0]["band_label"] == "60–64"


def test_calibration_by_score_band_floor_exactly_65_lands_in_65():
    out = pa.calibration_by_score_band([_row(composite_score=65.0)], band_size=5)
    assert out[0]["band_floor"] == 65


def test_calibration_by_score_band_label_uses_en_dash():
    out = pa.calibration_by_score_band([_row(composite_score=70.0)], band_size=5)
    assert "–" in out[0]["band_label"]
    assert "-" not in out[0]["band_label"]


def test_calibration_by_score_band_p_positive_alpha_mixed():
    rows = [
        _row(ticker="AAA", composite_score=70.0, alpha_pct=5.0),
        _row(ticker="BBB", composite_score=71.0, alpha_pct=-2.0),
    ]
    out = pa.calibration_by_score_band(rows)
    assert out[0]["p_positive_alpha"] == pytest.approx(0.5)


def test_calibration_by_score_band_p_positive_alpha_all_exactly_zero_is_zero():
    rows = [
        _row(ticker="AAA", composite_score=70.0, alpha_pct=0.0),
        _row(ticker="BBB", composite_score=71.0, alpha_pct=0.0),
    ]
    out = pa.calibration_by_score_band(rows)
    assert out[0]["p_positive_alpha"] == 0.0  # strict > 0, not >=


def test_calibration_by_score_band_avg_alpha_acted_none_when_all_missed():
    rows = [
        _row(ticker="AAA", composite_score=70.0, alpha_pct=5.0, acted_on=False),
        _row(ticker="BBB", composite_score=71.0, alpha_pct=3.0, acted_on=False),
    ]
    out = pa.calibration_by_score_band(rows)
    assert out[0]["avg_alpha_acted"] is None
    assert out[0]["avg_alpha_missed"] == pytest.approx(4.0)


def test_calibration_by_score_band_avg_outcome_pct_none_when_outcome_pct_missing():
    rows = [_row(composite_score=70.0, alpha_pct=5.0, outcome_pct=None)]
    out = pa.calibration_by_score_band(rows)
    assert out[0]["avg_outcome_pct"] is None


def test_calibration_by_score_band_unparseable_composite_score_skipped():
    rows = [
        _row(ticker="AAA", composite_score="not-a-number", alpha_pct=5.0),
        _row(ticker="BBB", composite_score=None, alpha_pct=5.0),
        _row(ticker="CCC", composite_score=70.0, alpha_pct=5.0),
    ]
    out = pa.calibration_by_score_band(rows)
    assert len(out) == 1
    assert out[0]["n"] == 1


def test_calibration_by_score_band_sorted_by_band_floor_ascending():
    rows = [
        _row(ticker="AAA", composite_score=90.0, alpha_pct=1.0),
        _row(ticker="BBB", composite_score=60.0, alpha_pct=1.0),
        _row(ticker="CCC", composite_score=70.0, alpha_pct=1.0),
    ]
    out = pa.calibration_by_score_band(rows)
    assert [b["band_floor"] for b in out] == [60, 70, 90]


# ─── calibration_by_sector ────────────────────────────────────────────────────

def _sector_rows(sector, score, n, min_n=None):
    return [_row(ticker=f"{sector}{i}", sector=sector, composite_score=score, alpha_pct=1.0)
            for i in range(n)]


def test_calibration_by_sector_broad_below_65():
    out = pa.calibration_by_sector(_sector_rows("Tech", 64.99, 3))
    assert list(out["Tech"].keys()) == ["< 65"]


def test_calibration_by_sector_broad_exactly_65():
    out = pa.calibration_by_sector(_sector_rows("Tech", 65.0, 3))
    assert list(out["Tech"].keys()) == ["65–74"]


def test_calibration_by_sector_broad_74_99_still_mid():
    out = pa.calibration_by_sector(_sector_rows("Tech", 74.99, 3))
    assert list(out["Tech"].keys()) == ["65–74"]


def test_calibration_by_sector_broad_exactly_75():
    out = pa.calibration_by_sector(_sector_rows("Tech", 75.0, 3))
    assert list(out["Tech"].keys()) == ["75+"]


def test_calibration_by_sector_broad_bands_source_from_constants_not_literals(monkeypatch):
    # 2026-08-04 audit finding: _broad()'s 65/75 boundaries were bare literals
    # duplicating COMPOSITE_BUY/COMPOSITE_STRONG_BUY. Monkeypatching the
    # constants and confirming the label boundary shifts proves the fix reads
    # them dynamically -- the boundary tests above alone wouldn't catch a
    # regression back to hardcoded literals, since today's values coincide.
    monkeypatch.setattr(pa, "COMPOSITE_BUY", 50)
    monkeypatch.setattr(pa, "COMPOSITE_STRONG_BUY", 80)
    out = pa.calibration_by_sector(_sector_rows("Tech", 60.0, 3))
    assert list(out["Tech"].keys()) == ["50–79"]


def test_calibration_by_sector_cell_below_min_n_omitted():
    out = pa.calibration_by_sector(_sector_rows("Tech", 70.0, 2), min_n=3)
    assert out == {}


def test_calibration_by_sector_cell_at_min_n_present():
    out = pa.calibration_by_sector(_sector_rows("Tech", 70.0, 3), min_n=3)
    assert out["Tech"]["65–74"]["n"] == 3


def test_calibration_by_sector_blank_sector_defaults_unknown():
    rows = [_row(sector="", composite_score=70.0, alpha_pct=1.0) for _ in range(3)]
    out = pa.calibration_by_sector(rows)
    assert "Unknown" in out


def test_calibration_by_sector_result_key_order_alphabetical():
    rows = (
        _sector_rows("Zeta", 70.0, 3)
        + _sector_rows("Alpha", 70.0, 3)
        + _sector_rows("Mid", 70.0, 3)
    )
    out = pa.calibration_by_sector(rows)
    assert list(out.keys()) == ["Alpha", "Mid", "Zeta"]


def test_calibration_by_sector_maturing_and_none_alpha_excluded():
    rows = [
        _row(sector="Tech", composite_score=70.0, outcome_maturing=True, alpha_pct=1.0),
        _row(sector="Tech", composite_score=70.0, alpha_pct=None),
    ]
    out = pa.calibration_by_sector(rows)
    assert out == {}


# ─── calibration_by_verdict ───────────────────────────────────────────────────

def test_calibration_by_verdict_excludes_maturing_and_none_alpha():
    rows = [
        _row(verdict="Confirmed", outcome_maturing=True, alpha_pct=1.0),
        _row(verdict="Confirmed", alpha_pct=None),
    ]
    assert pa.calibration_by_verdict(rows) == []


def test_calibration_by_verdict_blank_defaults_unknown():
    rows = [_row(verdict="", alpha_pct=1.0)]
    out = pa.calibration_by_verdict(rows)
    assert out[0]["verdict"] == "Unknown"


def test_calibration_by_verdict_confirmed_first_then_n_desc():
    rows = (
        [_row(ticker=f"C{i}", verdict="Confirmed", alpha_pct=1.0) for i in range(2)]
        + [_row(ticker=f"X{i}", verdict="Conflicted", alpha_pct=1.0) for i in range(5)]
        + [_row(ticker=f"Y{i}", verdict="Unverified", alpha_pct=1.0) for i in range(3)]
    )
    out = pa.calibration_by_verdict(rows)
    assert [b["verdict"] for b in out] == ["Confirmed", "Conflicted", "Unverified"]


def test_calibration_by_verdict_min_n_parameter_unused_no_filtering():
    # `min_n` is declared in the signature but never referenced in the body —
    # a genuinely dead parameter. This documents the CURRENT (accidental)
    # behavior — a huge min_n does not remove a 1-row bucket — rather than
    # asserting it as intended filtering semantics. Flagged separately in the
    # report as a maintainer follow-up, not fixed here.
    rows = [_row(verdict="Confirmed", alpha_pct=1.0)]
    out = pa.calibration_by_verdict(rows, min_n=1000)
    assert len(out) == 1
    assert out[0]["n"] == 1


# ─── sentiment_alignment_summary ─────────────────────────────────────────────

def test_sentiment_alignment_summary_no_confirmed_bucket_is_none():
    by_verdict = [{"verdict": "Conflicted", "n": 5, "avg_alpha": 1.0}]
    out = pa.sentiment_alignment_summary(by_verdict)
    assert out["confirmed_avg_alpha"] is None
    assert out["confirmed_n"] == 0


def test_sentiment_alignment_summary_other_alpha_weighted_mean_skips_none():
    by_verdict = [
        {"verdict": "Confirmed", "n": 10, "avg_alpha": 5.0},
        {"verdict": "Conflicted", "n": 4, "avg_alpha": 2.0},
        {"verdict": "Unverified", "n": 6, "avg_alpha": None},  # skipped from both num & denom
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=1)
    assert out["other_avg_alpha"] == pytest.approx(2.0)  # only the Conflicted bucket counted
    assert out["other_n"] == 10  # n sum still includes the None-alpha bucket


def test_sentiment_alignment_summary_edge_pp_none_when_either_side_none():
    by_verdict = [{"verdict": "Confirmed", "n": 10, "avg_alpha": 5.0}]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=1)
    assert out["edge_pp"] is None  # no "other" bucket at all


def test_sentiment_alignment_summary_insufficient_confirmed_side():
    by_verdict = [
        {"verdict": "Confirmed", "n": 2, "avg_alpha": 5.0},
        {"verdict": "Conflicted", "n": 10, "avg_alpha": 1.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["conclusion"] == "insufficient_data"


def test_sentiment_alignment_summary_insufficient_other_side():
    by_verdict = [
        {"verdict": "Confirmed", "n": 10, "avg_alpha": 5.0},
        {"verdict": "Conflicted", "n": 2, "avg_alpha": 1.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["conclusion"] == "insufficient_data"


def test_sentiment_alignment_summary_edge_pp_exactly_zero_not_confirmed_wins():
    by_verdict = [
        {"verdict": "Confirmed", "n": 10, "avg_alpha": 5.0},
        {"verdict": "Conflicted", "n": 10, "avg_alpha": 5.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["edge_pp"] == 0.0
    assert out["conclusion"] == "no_edge"


def test_sentiment_alignment_summary_positive_edge_confirmed_wins():
    by_verdict = [
        {"verdict": "Confirmed", "n": 10, "avg_alpha": 5.0},
        {"verdict": "Conflicted", "n": 10, "avg_alpha": 1.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["conclusion"] == "confirmed_wins"
    assert out["edge_pp"] == pytest.approx(4.0)


# ─── personal_alpha_threshold ─────────────────────────────────────────────────

def test_personal_alpha_threshold_empty_bands_returns_none():
    assert pa.personal_alpha_threshold([]) is None


def test_personal_alpha_threshold_no_eligible_bands_returns_none():
    bands = [_cband(n=2, avg_alpha=5.0, band_floor=60, band_label="x")]
    bands[0]["p_positive_alpha"] = 0.9
    assert pa.personal_alpha_threshold(bands, min_n=5) is None  # n < min_n


def test_personal_alpha_threshold_simple_all_good_suffix():
    bands = [
        {"band_floor": 60, "n": 10, "p_positive_alpha": 0.6},
        {"band_floor": 65, "n": 10, "p_positive_alpha": 0.7},
    ]
    assert pa.personal_alpha_threshold(bands, min_n=5) == 60


def test_personal_alpha_threshold_poisoned_suffix_returns_none():
    # Bands 1 and 2 individually pass >= 0.5, but the highest band (3) does
    # not — the "all x in eligible[i:]" check requires the FULL remaining
    # suffix to hold, so no i qualifies and the result must be None, not the
    # first band's floor.
    bands = [
        {"band_floor": 60, "n": 10, "p_positive_alpha": 0.6},
        {"band_floor": 65, "n": 10, "p_positive_alpha": 0.7},
        {"band_floor": 70, "n": 10, "p_positive_alpha": 0.3},
    ]
    assert pa.personal_alpha_threshold(bands, min_n=5) is None


def test_personal_alpha_threshold_recovers_at_a_higher_floor():
    bands = [
        {"band_floor": 60, "n": 10, "p_positive_alpha": 0.6},
        {"band_floor": 65, "n": 10, "p_positive_alpha": 0.7},
        {"band_floor": 70, "n": 10, "p_positive_alpha": 0.3},
        {"band_floor": 75, "n": 10, "p_positive_alpha": 0.9},
    ]
    assert pa.personal_alpha_threshold(bands, min_n=5) == 75


# ─── synthesize_directives ────────────────────────────────────────────────────

def _synth(bands=None, thresh=None, avm=None, conv=None, rtype=None, sec_alph=None,
           n_graded=10, min_n=5, sentiment_alignment=None, entry_timing_bands=None):
    return pa.synthesize_directives(
        bands=bands or [], thresh=thresh, avm=avm or {}, conv=conv or [],
        rtype=rtype or [], sec_alph=sec_alph or [], n_graded=n_graded, min_n=min_n,
        sentiment_alignment=sentiment_alignment, entry_timing_bands=entry_timing_bands,
    )


def _types(directives):
    return [d["type"] for d in directives]


def _has_source(directives, source_tab):
    return [d for d in directives if d["source_tab"] == source_tab]


# Score Calibration

def test_synthesize_directives_score_calibration_thresh_not_none_action():
    out = _synth(thresh=70)
    matches = _has_source(out, "🎯 Score Calibration")
    assert len(matches) == 1
    assert matches[0]["type"] == "action"
    assert "70" in matches[0]["text"]


def test_synthesize_directives_score_calibration_all_neg_thick_bands_watch():
    bands = [_cband(n=10, avg_alpha=-2.0, band_floor=60), _cband(n=10, avg_alpha=-1.0, band_floor=65)]
    out = _synth(bands=bands, thresh=None)
    matches = _has_source(out, "🎯 Score Calibration")
    assert matches[0]["type"] == "watch"
    assert "regime" in matches[0]["text"]


def test_synthesize_directives_score_calibration_mixed_positive_not_all_watch():
    bands = [_cband(n=10, avg_alpha=2.0, band_floor=60), _cband(n=10, avg_alpha=-1.0, band_floor=65)]
    out = _synth(bands=bands, thresh=None)
    matches = _has_source(out, "🎯 Score Calibration")
    assert matches[0]["type"] == "watch"
    assert "not consistently enough" in matches[0]["text"]


def test_synthesize_directives_score_calibration_no_bands_no_directive():
    out = _synth(bands=[], thresh=None)
    assert _has_source(out, "🎯 Score Calibration") == []


# Decision Quality

def test_synthesize_directives_decision_quality_acting_action():
    out = _synth(avm={"edge": "acting", "edge_pp": 0.5})
    matches = _has_source(out, "⚖️ Decision Quality")
    assert matches[0]["type"] == "action"


def test_synthesize_directives_acting_states_both_sample_sizes():
    """The only card asserting something about the USER'S judgment had no basis
    on screen, while the sector and signal cards both quote their n.

    It matters here more than elsewhere because the two sides are structurally
    lopsided — you act on a small fraction of what the engine surfaces — and
    acted_vs_missed_comparison's floor is deliberately "BOTH sides < 3", so a
    1-vs-300 split still classifies as a confident verdict. The counts are what
    let a reader discount it.
    """
    out = _synth(avm={
        "edge": "acting", "edge_pp": 8.3,
        "acted": {"n": 34}, "missed": {"n": 310},
    })
    text = _has_source(out, "⚖️ Decision Quality")[0]["text"]
    assert "34 acted vs 310 passed" in text


def test_synthesize_directives_passing_states_both_sample_sizes():
    out = _synth(avm={
        "edge": "passing", "edge_pp": 2.0,
        "acted": {"n": 4}, "missed": {"n": 120},
    })
    matches = _has_source(out, "⚖️ Decision Quality")
    assert matches[0]["type"] == "caution"
    assert "4 acted vs 120 passed" in matches[0]["text"]


def test_synthesize_directives_thin_acted_side_is_visible_in_the_text():
    """The case the basis clause exists for: 1 trade vs 300 still classifies as
    "acting" by design, so the only defence is that the reader can SEE the 1."""
    out = _synth(avm={
        "edge": "acting", "edge_pp": 12.0,
        "acted": {"n": 1}, "missed": {"n": 300},
    })
    assert "1 acted vs 300 passed" in _has_source(out, "⚖️ Decision Quality")[0]["text"]


def test_synthesize_directives_omits_the_basis_when_side_counts_absent():
    """Callers legitimately pass an avm carrying only edge/edge_pp. That must
    render clean prose, not "(None acted vs None passed)"."""
    out = _synth(avm={"edge": "acting", "edge_pp": 5.0})
    text = _has_source(out, "⚖️ Decision Quality")[0]["text"]
    assert "acted vs" not in text
    assert "None" not in text


def test_synthesize_directives_decision_quality_acting_below_threshold_no_directive():
    out = _synth(avm={"edge": "acting", "edge_pp": 0.4999})
    assert _has_source(out, "⚖️ Decision Quality") == []


def test_synthesize_directives_decision_quality_passing_caution():
    out = _synth(avm={"edge": "passing", "edge_pp": 0.5})
    matches = _has_source(out, "⚖️ Decision Quality")
    assert matches[0]["type"] == "caution"


def test_synthesize_directives_decision_quality_neutral_and_insufficient_context():
    out = _synth(avm={"edge": "neutral", "edge_pp": None})
    matches = _has_source(out, "⚖️ Decision Quality")
    assert matches[0]["type"] == "context"

    out2 = _synth(avm={"edge": "insufficient"})
    matches2 = _has_source(out2, "⚖️ Decision Quality")
    assert matches2[0]["type"] == "context"


# Sector Alpha

def test_synthesize_directives_sector_alpha_best_positive_action():
    out = _synth(sec_alph=[_sband("Tech", 5.0, 10)])
    matches = _has_source(out, "🌐 Sector Alpha")
    assert matches[0]["type"] == "action"
    assert "Tech" in matches[0]["text"]


def test_synthesize_directives_sector_alpha_best_exactly_zero_watch():
    out = _synth(sec_alph=[_sband("Tech", 0.0, 10)])
    matches = _has_source(out, "🌐 Sector Alpha")
    assert matches[0]["type"] == "watch"


def test_synthesize_directives_sector_alpha_worst_below_neg3_extra_caution():
    sec_alph = [_sband("Tech", 5.0, 10), _sband("Energy", -3.0001, 5)]
    out = _synth(sec_alph=sec_alph)
    matches = _has_source(out, "🌐 Sector Alpha")
    assert [m["type"] for m in matches] == ["action", "caution"]


def test_synthesize_directives_sector_alpha_worst_exactly_neg3_no_caution():
    sec_alph = [_sband("Tech", 5.0, 10), _sband("Energy", -3.0, 5)]
    out = _synth(sec_alph=sec_alph)
    matches = _has_source(out, "🌐 Sector Alpha")
    assert [m["type"] for m in matches] == ["action"]


# Signal Breakdown

def test_synthesize_directives_signal_breakdown_rtype_gap_at_1_0_fires():
    out = _synth(rtype=[_rtband("New Position", 3.0), _rtband("Opportunity Watch", 2.0)])
    matches = _has_source(out, "🏷️ Signal Breakdown")
    assert any(m["type"] == "action" for m in matches)


def test_synthesize_directives_signal_breakdown_rtype_gap_just_below_1_0_no_fire():
    out = _synth(rtype=[_rtband("New Position", 2.9999), _rtband("Opportunity Watch", 2.0)])
    matches = _has_source(out, "🏷️ Signal Breakdown")
    assert matches == []


def test_synthesize_directives_signal_breakdown_conv_gap_at_1_5_fires():
    out = _synth(conv=[_convband("Strong BUY", 4.5), _convband("BUY", 3.0)])
    matches = _has_source(out, "🏷️ Signal Breakdown")
    assert any(m["type"] == "action" for m in matches)


def test_synthesize_directives_signal_breakdown_conv_gap_just_below_1_5_no_fire():
    out = _synth(conv=[_convband("Strong BUY", 4.4999), _convband("BUY", 3.0)])
    matches = _has_source(out, "🏷️ Signal Breakdown")
    assert matches == []


def test_synthesize_directives_signal_breakdown_both_can_co_occur():
    out = _synth(
        rtype=[_rtband("New Position", 3.0), _rtband("Opportunity Watch", 2.0)],
        conv=[_convband("Strong BUY", 4.5), _convband("BUY", 3.0)],
    )
    matches = _has_source(out, "🏷️ Signal Breakdown")
    assert len(matches) == 2


# Context

def test_synthesize_directives_context_singular_band():
    bands = [_cband(n=2, avg_alpha=1.0, band_floor=60)]
    out = _synth(bands=bands, min_n=5)
    ctx = _has_source(out, "all models")[0]
    assert "1 score band still" in ctx["text"]
    assert "1 score bands" not in ctx["text"]


def test_synthesize_directives_context_plural_bands():
    bands = [_cband(n=2, avg_alpha=1.0, band_floor=60), _cband(n=3, avg_alpha=1.0, band_floor=65)]
    out = _synth(bands=bands, min_n=5)
    ctx = _has_source(out, "all models")[0]
    assert "2 score bands still" in ctx["text"]


def test_synthesize_directives_context_zero_thin_omits_clause():
    bands = [_cband(n=10, avg_alpha=1.0, band_floor=60)]
    out = _synth(bands=bands, min_n=5)
    ctx = _has_source(out, "all models")[0]
    assert "still below" not in ctx["text"]
    assert ctx["text"].startswith("Based on 10 graded outcomes.")


def test_synthesize_directives_context_always_appended_exactly_once():
    out = _synth()
    assert len(_has_source(out, "all models")) == 1


# Sentiment alignment

def test_synthesize_directives_sentiment_confirmed_wins_at_2pp_action():
    out = _synth(sentiment_alignment={"conclusion": "confirmed_wins", "edge_pp": 2.0})
    matches = _has_source(out, "🧭 Sentiment Alignment")
    assert matches[0]["type"] == "action"


def test_synthesize_directives_sentiment_confirmed_wins_below_2pp_no_directive():
    out = _synth(sentiment_alignment={"conclusion": "confirmed_wins", "edge_pp": 1.999})
    assert _has_source(out, "🧭 Sentiment Alignment") == []


def test_synthesize_directives_sentiment_no_edge_watch():
    out = _synth(sentiment_alignment={"conclusion": "no_edge", "edge_pp": None})
    matches = _has_source(out, "🧭 Sentiment Alignment")
    assert matches[0]["type"] == "watch"


def test_synthesize_directives_sentiment_insufficient_data_no_directive():
    out = _synth(sentiment_alignment={"conclusion": "insufficient_data", "edge_pp": 99.0})
    assert _has_source(out, "🧭 Sentiment Alignment") == []


# Entry timing

def test_synthesize_directives_entry_timing_extreme_negative_caution():
    bands = [{"band_label": "Extreme", "day20_n": 5, "day20_alpha": -1.0}]
    out = _synth(entry_timing_bands=bands, min_n=5)
    matches = _has_source(out, "⏱️ Entry Timing")
    assert matches[0]["type"] == "caution"


def test_synthesize_directives_entry_timing_below_min_n_no_directive():
    bands = [{"band_label": "Extreme", "day20_n": 4, "day20_alpha": -1.0}]
    out = _synth(entry_timing_bands=bands, min_n=5)
    assert _has_source(out, "⏱️ Entry Timing") == []


def test_synthesize_directives_entry_timing_zero_or_positive_no_directive():
    bands = [{"band_label": "Extreme", "day20_n": 5, "day20_alpha": 0.0}]
    out = _synth(entry_timing_bands=bands, min_n=5)
    assert _has_source(out, "⏱️ Entry Timing") == []

    bands2 = [{"band_label": "Extreme", "day20_n": 5, "day20_alpha": 3.0}]
    out2 = _synth(entry_timing_bands=bands2, min_n=5)
    assert _has_source(out2, "⏱️ Entry Timing") == []


def test_synthesize_directives_entry_timing_no_extreme_band_no_directive():
    bands = [{"band_label": "Aligned", "day20_n": 5, "day20_alpha": -1.0}]
    out = _synth(entry_timing_bands=bands, min_n=5)
    assert _has_source(out, "⏱️ Entry Timing") == []


def test_synthesize_directives_entry_timing_none_default_no_crash():
    out = _synth(entry_timing_bands=None)
    assert _has_source(out, "⏱️ Entry Timing") == []


# Final ordering

def test_synthesize_directives_final_order_action_caution_watch_context():
    out = _synth(
        thresh=70,  # -> action
        avm={"edge": "passing", "edge_pp": 1.0},  # -> caution
        sec_alph=[_sband("Tech", 0.0, 10)],  # -> watch
    )
    types = _types(out)
    assert types == ["action", "caution", "watch", "context"]


def test_synthesize_directives_sorted_relative_order_with_multiple_per_type():
    bands = [_cband(n=10, avg_alpha=-2.0, band_floor=60)]  # -> watch (all_neg)
    out = _synth(
        bands=bands, thresh=None,
        avm={"edge": "acting", "edge_pp": 1.0},  # -> action
        sec_alph=[_sband("Tech", 5.0, 10), _sband("Energy", -5.0, 5)],  # -> action + caution
        sentiment_alignment={"conclusion": "no_edge", "edge_pp": None},  # -> watch
    )
    order_map = {"action": 0, "caution": 1, "watch": 2, "context": 3}
    ranks = [order_map[t] for t in _types(out)]
    assert ranks == sorted(ranks)


# ─── total_graded ─────────────────────────────────────────────────────────────

def test_total_graded_counts_only_mature_priced_rows():
    rows = [
        _row(outcome_maturing=False, alpha_pct=1.0),
        _row(outcome_maturing=True, alpha_pct=1.0),
        _row(outcome_maturing=False, alpha_pct=None),
    ]
    assert pa.total_graded(rows) == 1


def test_total_graded_empty_input_zero():
    assert pa.total_graded([]) == 0


# ══════════════════════════════ Decision Quality ════════════════════════════

def test_acted_vs_missed_comparison_avg_alpha_none_when_side_empty():
    rows = [_row(acted_on=False, alpha_pct=1.0, outcome_pct=1.0)]
    out = pa.acted_vs_missed_comparison(rows)
    assert out["acted"]["avg_alpha"] is None
    assert out["acted"]["p_positive_alpha"] is None
    assert out["acted"]["avg_outcome_pct"] is None


def test_acted_vs_missed_comparison_insufficient_when_one_side_empty():
    rows = [_row(ticker=f"A{i}", acted_on=False, alpha_pct=1.0, outcome_pct=1.0) for i in range(5)]
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "insufficient"
    assert out["edge_pp"] is None


def test_acted_vs_missed_comparison_and_not_or_one_side_below_3_other_above():
    # acted n=2 (<3), missed n=5 (>=3) — this must NOT trip "insufficient"
    # since the rule is BOTH sides <3, not EITHER.
    rows = (
        [_row(ticker=f"A{i}", acted_on=True, alpha_pct=5.0, outcome_pct=5.0) for i in range(2)]
        + [_row(ticker=f"M{i}", acted_on=False, alpha_pct=1.0, outcome_pct=1.0) for i in range(5)]
    )
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "acting"
    assert out["edge_pp"] == pytest.approx(4.0)


def test_acted_vs_missed_comparison_neutral_boundary_just_below_0_5():
    # avg_alpha on each side is rounded to 2dp before the edge comparison, so
    # the gap must survive that rounding to stay just under 0.5.
    rows = (
        [_row(ticker=f"A{i}", acted_on=True, alpha_pct=5.0, outcome_pct=5.0) for i in range(3)]
        + [_row(ticker=f"M{i}", acted_on=False, alpha_pct=5.49, outcome_pct=5.0) for i in range(3)]
    )
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "neutral"
    assert out["edge_pp"] == pytest.approx(0.49)


def test_acted_vs_missed_comparison_exactly_0_5_not_neutral():
    rows = (
        [_row(ticker=f"A{i}", acted_on=True, alpha_pct=5.5, outcome_pct=5.0) for i in range(3)]
        + [_row(ticker=f"M{i}", acted_on=False, alpha_pct=5.0, outcome_pct=5.0) for i in range(3)]
    )
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "acting"
    assert out["edge_pp"] == pytest.approx(0.5)


def test_acted_vs_missed_comparison_acted_beats_missed_is_acting():
    rows = (
        [_row(ticker=f"A{i}", acted_on=True, alpha_pct=10.0, outcome_pct=1.0) for i in range(3)]
        + [_row(ticker=f"M{i}", acted_on=False, alpha_pct=1.0, outcome_pct=1.0) for i in range(3)]
    )
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "acting"
    assert out["edge_pp"] == pytest.approx(9.0)


def test_acted_vs_missed_comparison_missed_beats_acted_is_passing():
    rows = (
        [_row(ticker=f"A{i}", acted_on=True, alpha_pct=1.0, outcome_pct=1.0) for i in range(3)]
        + [_row(ticker=f"M{i}", acted_on=False, alpha_pct=10.0, outcome_pct=1.0) for i in range(3)]
    )
    out = pa.acted_vs_missed_comparison(rows)
    assert out["edge"] == "passing"
    assert out["edge_pp"] == pytest.approx(9.0)


def test_acted_vs_missed_comparison_excludes_maturing_and_none_alpha():
    rows = [
        _row(acted_on=True, outcome_maturing=True, alpha_pct=1.0),
        _row(acted_on=True, alpha_pct=None),
    ]
    out = pa.acted_vs_missed_comparison(rows)
    assert out["acted"]["n"] == 0
    assert out["missed"]["n"] == 0


# ══════════════════════════════ Signal Breakdown ════════════════════════════

def test_by_conviction_groups_and_sorts_desc():
    rows = (
        [_row(ticker=f"A{i}", conviction="High", alpha_pct=5.0) for i in range(3)]
        + [_row(ticker=f"B{i}", conviction="Low", alpha_pct=1.0) for i in range(3)]
    )
    out = pa.by_conviction(rows)
    assert [b["conviction"] for b in out] == ["High", "Low"]


def test_by_conviction_blank_defaults_unknown():
    rows = [_row(ticker=f"A{i}", conviction="", alpha_pct=1.0) for i in range(3)]
    out = pa.by_conviction(rows)
    assert out[0]["conviction"] == "Unknown"


def test_by_conviction_filters_below_min_n_after_sort():
    rows = (
        [_row(ticker=f"A{i}", conviction="High", alpha_pct=9.0) for i in range(1)]  # n=1, best alpha
        + [_row(ticker=f"B{i}", conviction="Low", alpha_pct=1.0) for i in range(3)]
    )
    out = pa.by_conviction(rows, min_n=3)
    assert [b["conviction"] for b in out] == ["Low"]  # High excluded despite higher alpha


def test_by_conviction_avg_alpha_is_never_none_for_a_real_bucket():
    # Every row in `recs` for a bucket has alpha_pct not None by construction
    # of the upstream filter, so avg_alpha can never legitimately compute to
    # None here — noted rather than forced with a fabricated case.
    rows = [_row(ticker=f"A{i}", conviction="High", alpha_pct=1.0) for i in range(3)]
    out = pa.by_conviction(rows)
    assert out[0]["avg_alpha"] is not None


def test_by_rec_type_stats_unmapped_type_falls_back_to_itself():
    rows = [_row(ticker=f"A{i}", rec_type="mystery_type", alpha_pct=1.0) for i in range(3)]
    out = pa.by_rec_type_stats(rows)
    assert out[0]["label"] == "mystery_type"


def test_by_rec_type_stats_mapped_labels():
    rows = (
        [_row(ticker=f"A{i}", rec_type="new_pick", alpha_pct=5.0) for i in range(3)]
        + [_row(ticker=f"B{i}", rec_type="add_winner", alpha_pct=1.0) for i in range(3)]
        + [_row(ticker=f"C{i}", rec_type="buy_candidate", alpha_pct=1.0) for i in range(3)]
    )
    out = pa.by_rec_type_stats(rows)
    labels = {r["rec_type"]: r["label"] for r in out}
    assert labels["new_pick"] == "New Position"
    assert labels["add_winner"] == "Add to Winner"
    assert labels["buy_candidate"] == "Opportunity Watch"


def test_by_rec_type_stats_sorted_desc_by_avg_alpha():
    rows = (
        [_row(ticker=f"A{i}", rec_type="new_pick", alpha_pct=1.0) for i in range(3)]
        + [_row(ticker=f"B{i}", rec_type="add_winner", alpha_pct=9.0) for i in range(3)]
    )
    out = pa.by_rec_type_stats(rows)
    assert [r["rec_type"] for r in out] == ["add_winner", "new_pick"]


# ══════════════════════════════ Sector Alpha ════════════════════════════════

def test_by_sector_alpha_groups_sorts_and_filters():
    rows = (
        [_row(ticker=f"A{i}", sector="Tech", alpha_pct=9.0) for i in range(3)]
        + [_row(ticker=f"B{i}", sector="Energy", alpha_pct=1.0) for i in range(2)]  # n<3 dropped
    )
    out = pa.by_sector_alpha(rows, min_n=3)
    assert [r["sector"] for r in out] == ["Tech"]


def test_by_sector_alpha_blank_defaults_unknown():
    rows = [_row(ticker=f"A{i}", sector=None, alpha_pct=1.0) for i in range(3)]
    out = pa.by_sector_alpha(rows)
    assert out[0]["sector"] == "Unknown"


# ══════════════════════════════ Entry Timing ════════════════════════════════

# ─── dedupe_repeated_tickers ─────────────────────────────────────────────────

def test_dedupe_repeated_tickers_out_of_scope_passes_through_untouched():
    out_of_scope = _row(ticker="ZZZ", rec_type="buy_candidate", rec_date=date(2026, 1, 1))
    in_scope = _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 1))
    out = pa.dedupe_repeated_tickers([out_of_scope, in_scope])
    assert out == [in_scope, out_of_scope]  # in-scope kept rows first, then other appended


def test_dedupe_repeated_tickers_gap_exactly_window_days_dropped():
    rows = [
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 1)),
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 6)),  # gap=5
    ]
    out = pa.dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 1
    assert out[0]["rec_date"] == date(2026, 1, 1)


def test_dedupe_repeated_tickers_gap_window_plus_one_kept():
    rows = [
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 1)),
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 7)),  # gap=6
    ]
    out = pa.dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 2
    assert [r["rec_date"] for r in out] == [date(2026, 1, 1), date(2026, 1, 7)]


def test_dedupe_repeated_tickers_rec_date_none_always_kept_unclustered():
    rows = [
        _row(ticker="AAA", rec_type="new_pick", rec_date=None),
        _row(ticker="AAA", rec_type="new_pick", rec_date=None),
    ]
    out = pa.dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 2


def test_dedupe_repeated_tickers_three_firing_cluster_collapses_into_anchor():
    rows = [
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 1)),
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 4)),  # +3d, within window
        _row(ticker="AAA", rec_type="new_pick", rec_date=date(2026, 1, 5)),  # +4d, within window
    ]
    out = pa.dedupe_repeated_tickers(rows, window_days=5)
    assert len(out) == 1
    assert out[0]["rec_date"] == date(2026, 1, 1)


# ─── divergence_at_entry ──────────────────────────────────────────────────────

def test_divergence_at_entry_momentum_none_returns_none():
    assert pa.divergence_at_entry({"momentum_score": None, "composite_score": 50.0}) is None


def test_divergence_at_entry_composite_none_returns_none():
    assert pa.divergence_at_entry({"momentum_score": 80.0, "composite_score": None}) is None


def test_divergence_at_entry_unparseable_returns_none():
    assert pa.divergence_at_entry({"momentum_score": "bad", "composite_score": 50.0}) is None


def test_divergence_at_entry_normal_case():
    assert pa.divergence_at_entry({"momentum_score": 80.0, "composite_score": 50.0}) == pytest.approx(30.0)


# ─── _advance_trading_days ────────────────────────────────────────────────────

def test_advance_trading_days_midweek_plain_case():
    start = date(2026, 3, 2)  # Monday, no nearby NYSE holidays
    assert start.isoformat() not in NYSE_HOLIDAYS
    result = pa._advance_trading_days(start, 3)
    assert result == date(2026, 3, 5)  # Tue, Wed, Thu


def test_advance_trading_days_skips_weekend():
    start = date(2026, 3, 6)  # Friday
    result = pa._advance_trading_days(start, 1)
    assert result == date(2026, 3, 9)  # skips Sat/Sun to Monday


# ─── forward_alpha_at_horizon ─────────────────────────────────────────────────

def _boom(ticker, start, end):
    raise AssertionError("historical_close_fn must not be called when a guard short-circuits")


def test_forward_alpha_guard_empty_ticker_none():
    assert pa.forward_alpha_at_horizon("", date(2026, 1, 1), 100.0, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_guard_none_ticker_none():
    assert pa.forward_alpha_at_horizon(None, date(2026, 1, 1), 100.0, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_guard_none_rec_date_none():
    assert pa.forward_alpha_at_horizon("AAA", None, 100.0, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_guard_none_price_at_entry_none():
    assert pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), None, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_guard_zero_price_at_entry_none():
    assert pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 0, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_guard_negative_price_at_entry_none():
    assert pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), -5.0, 3, {}, historical_close_fn=_boom) is None


def test_forward_alpha_fn_raises_returns_none():
    def _raise(t, s, e):
        raise RuntimeError("transport failure")
    out = pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 100.0, 3, {}, historical_close_fn=_raise)
    assert out is None


def test_forward_alpha_fn_returns_none_returns_none():
    out = pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 100.0, 3, {},
                                       historical_close_fn=lambda t, s, e: None)
    assert out is None


def test_forward_alpha_fn_returns_nan_returns_none():
    out = pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 100.0, 3, {},
                                       historical_close_fn=lambda t, s, e: float("nan"))
    assert out is None


def test_forward_alpha_fn_returns_nonpositive_returns_none():
    out = pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 100.0, 3, {},
                                       historical_close_fn=lambda t, s, e: 0.0)
    assert out is None
    out2 = pa.forward_alpha_at_horizon("AAA", date(2026, 1, 1), 100.0, 3, {},
                                        historical_close_fn=lambda t, s, e: -5.0)
    assert out2 is None


def test_forward_alpha_empty_spy_series_returns_none():
    out = pa.forward_alpha_at_horizon("AAA", date(2026, 3, 2), 100.0, 3, {},
                                       historical_close_fn=lambda t, s, e: 112.5)
    assert out is None  # _spy_return_pct sees an empty series and returns None


def test_forward_alpha_normal_case_rounds_stock_minus_spy():
    spy = {date(2026, 3, 2): 200.0, date(2026, 3, 5): 210.0}
    calls = []

    def _capture(t, s, e):
        calls.append((t, s, e))
        return 112.5

    out = pa.forward_alpha_at_horizon("AAA", date(2026, 3, 2), 100.0, 3, spy, historical_close_fn=_capture)
    # stock_ret = 12.5%, spy_ret = 5.0% -> alpha = 7.5
    assert out == pytest.approx(7.5)
    target = date(2026, 3, 5)
    assert calls == [("AAA", target, target + timedelta(days=7))]


# ─── by_divergence_band ───────────────────────────────────────────────────────

def test_by_divergence_band_divergence_none_excluded():
    rows = [_dband_row(divergence=None, day1_alpha=1.0)]
    assert pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0) == []


def test_by_divergence_band_divergence_exactly_zero_excluded():
    rows = [_dband_row(divergence=0.0, day1_alpha=1.0)]
    assert pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0) == []


def test_by_divergence_band_divergence_just_above_zero_included():
    rows = [_dband_row(divergence=0.0001, day1_alpha=1.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert out[0]["band_label"] == "Aligned"


def test_by_divergence_band_aligned_boundary_inclusive():
    rows = [_dband_row(divergence=1.0, day1_alpha=1.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert [b["band_label"] for b in out] == ["Aligned"]


def test_by_divergence_band_diverging_just_above_aligned_max():
    rows = [_dband_row(divergence=1.0001, day1_alpha=1.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert [b["band_label"] for b in out] == ["Diverging"]


def test_by_divergence_band_diverging_boundary_inclusive():
    rows = [_dband_row(divergence=3.0, day1_alpha=1.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert [b["band_label"] for b in out] == ["Diverging"]


def test_by_divergence_band_extreme_just_above_diverging_max():
    rows = [_dband_row(divergence=3.0001, day1_alpha=1.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert [b["band_label"] for b in out] == ["Extreme"]


def test_by_divergence_band_day1_present_day5_absent_independent():
    rows = [_dband_row(divergence=5.0, day1_alpha=2.0, day5_alpha=None)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    band = out[0]
    assert band["day1_n"] == 1
    assert band["day5_n"] == 0
    assert band["day5_alpha"] is None


def test_by_divergence_band_day20_excludes_maturing_even_if_alpha_present():
    rows = [_dband_row(divergence=5.0, outcome_maturing=True, alpha_pct=9.0)]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert out[0]["day20_n"] == 0
    assert out[0]["day20_alpha"] is None


def test_by_divergence_band_p_positive_alpha_none_when_day20_n_zero():
    rows = [_dband_row(divergence=5.0, day1_alpha=1.0)]  # no day20 data
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert out[0]["day20_n"] == 0
    assert out[0]["p_positive_alpha"] is None


def test_by_divergence_band_p_positive_alpha_computed_when_day20_present():
    rows = [
        _dband_row(divergence=5.0, outcome_maturing=False, alpha_pct=1.0),
        _dband_row(divergence=6.0, outcome_maturing=False, alpha_pct=-1.0),
    ]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    assert out[0]["day20_n"] == 2
    assert out[0]["p_positive_alpha"] == pytest.approx(0.5)


def test_by_divergence_band_zero_qualifying_rows_band_omitted():
    rows = [
        _dband_row(divergence=0.5, day1_alpha=1.0),   # Aligned
        _dband_row(divergence=5.0, day1_alpha=1.0),   # Extreme
        # nothing in the (1.0, 3.0] Diverging range
    ]
    out = pa.by_divergence_band(rows, aligned_max=1.0, diverging_max=3.0)
    labels = [b["band_label"] for b in out]
    assert labels == ["Aligned", "Extreme"]
    assert "Diverging" not in labels


# ─── find_illustrating_case ───────────────────────────────────────────────────

def test_find_illustrating_case_below_threshold_excluded():
    rows = [_fic_row(ticker="AAA", composite_score=70.0, momentum_score=72.0)]  # div=2.0
    assert pa.find_illustrating_case(rows, diverging_max=5.0) is None


def test_find_illustrating_case_single_firing_ticker_excluded():
    rows = [_fic_row(ticker="AAA", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0)]
    assert pa.find_illustrating_case(rows, diverging_max=5.0) is None


def test_find_illustrating_case_zero_candidates_returns_none():
    assert pa.find_illustrating_case([], diverging_max=5.0) is None


def test_find_illustrating_case_qualifies_with_two_firings():
    rows = [
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0),
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 5), composite_score=52.0, momentum_score=85.0),
    ]
    out = pa.find_illustrating_case(rows, diverging_max=5.0)
    assert out["ticker"] == "AAA"
    assert out["n_firings"] == 2


def test_find_illustrating_case_tie_break_earliest_first_date_wins():
    rows = [
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 10), composite_score=50.0, momentum_score=80.0),
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 15), composite_score=50.0, momentum_score=80.0),
        _fic_row(ticker="BBB", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0),
        _fic_row(ticker="BBB", rec_date=date(2026, 1, 3), composite_score=50.0, momentum_score=80.0),
    ]
    out = pa.find_illustrating_case(rows, diverging_max=5.0)
    assert out["ticker"] == "BBB"  # tie in firing count (2 each), earlier first-firing wins


def test_find_illustrating_case_most_firings_wins_over_earlier_date():
    rows = (
        [_fic_row(ticker="AAA", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0),
         _fic_row(ticker="AAA", rec_date=date(2026, 1, 3), composite_score=50.0, momentum_score=80.0)]
        + [_fic_row(ticker="BBB", rec_date=date(2026, 1, 10), composite_score=50.0, momentum_score=80.0),
           _fic_row(ticker="BBB", rec_date=date(2026, 1, 12), composite_score=50.0, momentum_score=80.0),
           _fic_row(ticker="BBB", rec_date=date(2026, 1, 14), composite_score=50.0, momentum_score=80.0)]
    )
    out = pa.find_illustrating_case(rows, diverging_max=5.0)
    assert out["ticker"] == "BBB"  # 3 firings beats AAA's 2, despite a later start


def test_find_illustrating_case_aggregates_min_max_across_firings():
    # NOTE: a row can only enter `rows` for a ticker if divergence_at_entry()
    # succeeded, which itself requires BOTH composite_score and
    # momentum_score present — so a qualifying firing can never have a None
    # composite/momentum in practice. This test verifies the min/max
    # aggregation across present values rather than a None-composite gap,
    # which the upstream gate makes unreachable (see report).
    rows = [
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0),
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 3), composite_score=55.0, momentum_score=90.0),
    ]
    out = pa.find_illustrating_case(rows, diverging_max=5.0)
    assert out["composite_min"] == 50.0
    assert out["composite_max"] == 55.0
    assert out["momentum_min"] == 80.0
    assert out["momentum_max"] == 90.0


def test_find_illustrating_case_alpha_respects_maturing_gate():
    rows = [
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 1), composite_score=50.0, momentum_score=80.0,
                  outcome_maturing=True, alpha_pct=99.0),
        _fic_row(ticker="AAA", rec_date=date(2026, 1, 3), composite_score=50.0, momentum_score=80.0,
                  outcome_maturing=False, alpha_pct=3.0),
    ]
    out = pa.find_illustrating_case(rows, diverging_max=5.0)
    assert out["alpha_min"] == 3.0
    assert out["alpha_max"] == 3.0  # the maturing row's alpha is excluded


# ─── band_narrative ────────────────────────────────────────────────────────────

def test_band_narrative_no_day1_data():
    text = pa.band_narrative(_band(day1_alpha=None))
    assert "Not enough Day+1 data" in text


def test_band_narrative_looks_calm_but_fades_hard_priority_over_flat_positive():
    # d1 near-zero (>= -0.05, "calm"), d5 calm too, but d20 is a real loss.
    # This must route to the "fades hard" branch, NOT the generic
    # flat-to-positive branch below it — the exact live bug this ordering fixed.
    band = _band(day1_alpha=-0.04, day5_alpha=0.0, day20_alpha=-14.0, day20_n=5)
    text = pa.band_narrative(band)
    assert "fades" in text
    assert "Stays flat-to-positive" not in text


def test_band_narrative_looks_calm_but_fades_hard_with_positive_day1():
    band = _band(day1_alpha=0.02, day5_alpha=0.01, day20_alpha=-6.0, day20_n=3)
    text = pa.band_narrative(band)
    assert "fades" in text


def test_band_narrative_flat_to_positive_when_day20_also_nonneg():
    band = _band(day1_alpha=0.5, day5_alpha=0.2, day20_alpha=0.1, day20_n=2)
    text = pa.band_narrative(band)
    assert "Stays flat-to-positive" in text


def test_band_narrative_recovers_quickly_by_day5():
    band = _band(day1_alpha=-2.0, day5_alpha=1.0, day20_alpha=3.0, day20_n=2)
    text = pa.band_narrative(band)
    assert "Recovers quickly" in text
    assert "3.0" in text  # optional Day+20 clause appended


def test_band_narrative_recovers_quickly_no_day20_clause_when_none():
    band = _band(day1_alpha=-2.0, day5_alpha=1.0, day20_alpha=None, day20_n=0)
    text = pa.band_narrative(band)
    assert "Recovers quickly" in text
    assert text.endswith(".")


def test_band_narrative_mildly_negative_turns_positive_by_day20():
    band = _band(day1_alpha=-1.0, day5_alpha=-0.5, day20_alpha=0.5, day20_n=2)
    text = pa.band_narrative(band)
    assert "turns positive by Day+20" in text


def test_band_narrative_deepest_drawdown_still_recovers():
    band = _band(day1_alpha=-10.0, day5_alpha=-8.0, day20_alpha=-4.0, day20_n=2)
    text = pa.band_narrative(band)
    assert "Deepest drawdown" in text


def test_band_narrative_negative_persists_no_recovery():
    band = _band(day1_alpha=-2.0, day5_alpha=-3.0, day20_alpha=-5.0, day20_n=2)
    text = pa.band_narrative(band)
    assert "losses persist" in text


def test_band_narrative_catch_all_no_day20_history():
    band = _band(day1_alpha=-2.0, day5_alpha=-3.0, day20_alpha=None, day20_n=0)
    text = pa.band_narrative(band)
    assert "not enough Day+20 history" in text


def test_band_narrative_illustrating_ticker_suffix_appended():
    band = _band(day1_alpha=0.5, day5_alpha=0.2, day20_alpha=0.1, day20_n=2)
    text = pa.band_narrative(band, illustrating_ticker="AMD")
    assert text.endswith(" This is the AMD-shaped case.")


def test_band_narrative_illustrating_ticker_omitted_when_none():
    band = _band(day1_alpha=0.5, day5_alpha=0.2, day20_alpha=0.1, day20_n=2)
    text = pa.band_narrative(band, illustrating_ticker=None)
    assert "-shaped case" not in text
