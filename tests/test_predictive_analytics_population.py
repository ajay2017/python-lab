"""
Tests for the Predictive Analytics correctness fix (2026-09-30): the page-level
population (`prepare_population` / `collapse_by_ticker_rec_type`), the D1
threshold criterion, `threshold_banner`, the D2 directive demotion, and the
sentiment Unknown exclusion.

The defect these guard against: the page banded one row per DAILY surfacing,
so a ticker that stayed a pick for 12 days counted 12 times, mixed composites
from two weight regimes and ETFs into one band, and called a band "consistently
positive" on hit rate alone while its mean alpha was negative.
"""
import copy
from datetime import date, timedelta

import pytest

from stock_analyzer import predictive_analytics as pa
from stock_analyzer.constants import COMPOSITE_WEIGHTS_VERSION, PREDICTIVE_MIN_BAND_N

pytestmark = pytest.mark.fast

V = COMPOSITE_WEIGHTS_VERSION


def _r(ticker="AAA", rec_date=date(2026, 8, 1), rec_type="new_pick",
       composite_score=70.0, acted_on=False, alpha_pct=1.0, outcome_pct=1.0,
       outcome_maturing=False, weights_version=V, asset_type="stock",
       verdict="Confirmed", sector="Tech", conviction="BUY"):
    return {
        "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
        "composite_score": composite_score, "acted_on": acted_on,
        "alpha_pct": alpha_pct, "outcome_pct": outcome_pct,
        "outcome_maturing": outcome_maturing, "weights_version": weights_version,
        "asset_type": asset_type, "verdict": verdict, "sector": sector,
        "conviction": conviction,
    }


def _daily(ticker, n, start=date(2026, 8, 1), **kw):
    return [_r(ticker=ticker, rec_date=start + timedelta(days=i), **kw) for i in range(n)]


# ─── collapse_by_ticker_rec_type ─────────────────────────────────────────────

def test_collapse_by_ticker_rec_type_ten_daily_rows_to_one():
    out = pa.collapse_by_ticker_rec_type(_daily("AAA", 10))
    assert len(out) == 1
    assert out[0]["rec_date"] == date(2026, 8, 1)


def test_collapse_by_ticker_rec_type_two_types_two_reps_never_two_in_one_type():
    rows = _daily("AAA", 4, rec_type="new_pick") + _daily("AAA", 3, rec_type="enter_now")
    out = pa.collapse_by_ticker_rec_type(rows)
    assert sorted(r["rec_type"] for r in out) == ["enter_now", "new_pick"]


def test_collapse_by_ticker_rec_type_acted_row_preferred():
    rows = _daily("AAA", 5)
    rows[3]["acted_on"] = True
    rows[3]["alpha_pct"] = 42.0
    out = pa.collapse_by_ticker_rec_type(rows)
    assert out[0]["acted_on"] is True
    assert out[0]["alpha_pct"] == 42.0


def test_collapse_by_ticker_rec_type_earliest_graded_beats_earlier_priced_ungraded():
    early_ungraded = _r(rec_date=date(2026, 8, 1), alpha_pct=None, outcome_pct=3.0)
    later_graded = _r(rec_date=date(2026, 8, 5), alpha_pct=2.0, outcome_pct=2.5)
    out = pa.collapse_by_ticker_rec_type([early_ungraded, later_graded])
    assert out[0]["rec_date"] == date(2026, 8, 5)


def test_collapse_by_ticker_rec_type_maturing_row_is_not_graded():
    early_maturing = _r(rec_date=date(2026, 8, 1), alpha_pct=5.0, outcome_maturing=True)
    later_graded = _r(rec_date=date(2026, 8, 5), alpha_pct=2.0)
    out = pa.collapse_by_ticker_rec_type([early_maturing, later_graded])
    assert out[0]["rec_date"] == date(2026, 8, 5)


def test_collapse_by_ticker_rec_type_priced_fallback_then_dated():
    a = _r(rec_date=date(2026, 8, 1), alpha_pct=None, outcome_pct=None)
    b = _r(rec_date=date(2026, 8, 3), alpha_pct=None, outcome_pct=1.0)
    assert pa.collapse_by_ticker_rec_type([a, b])[0]["rec_date"] == date(2026, 8, 3)
    c = _r(rec_date=date(2026, 8, 2), alpha_pct=None, outcome_pct=None)
    assert pa.collapse_by_ticker_rec_type([c, a])[0]["rec_date"] == date(2026, 8, 1)


def test_collapse_by_ticker_rec_type_undated_fallback_first_row():
    a = _r(rec_date=None, alpha_pct=None, outcome_pct=None, composite_score=61.0)
    b = _r(rec_date=None, alpha_pct=None, outcome_pct=None, composite_score=62.0)
    out = pa.collapse_by_ticker_rec_type([a, b])
    assert out[0]["composite_score"] == 61.0


def test_collapse_by_ticker_rec_type_out_of_scope_ignored():
    out = pa.collapse_by_ticker_rec_type(_daily("AAA", 3, rec_type="buy_candidate"))
    assert out == []


# ─── prepare_population ──────────────────────────────────────────────────────

def test_prepare_population_old_version_excluded_and_counted():
    rows = _daily("AAA", 3, weights_version=V - 1) + _daily("BBB", 2)
    pop = pa.prepare_population(rows)
    assert {r["ticker"] for r in pop["reps"]} == {"BBB"}
    assert pop["n_excluded_version"] == 3
    assert pop["n_excluded_version_none"] == 0


def test_prepare_population_none_version_excluded_and_counted_separately():
    rows = _daily("AAA", 2, weights_version=None) + _daily("BBB", 1, weights_version=V - 1)
    pop = pa.prepare_population(rows)
    assert pop["reps"] == []
    assert pop["n_excluded_version_none"] == 2
    assert pop["n_excluded_version"] == 1


def test_prepare_population_etf_excluded_none_asset_type_is_stock():
    rows = (_daily("SPY", 3, asset_type="etf") + _daily("QQQ", 1, asset_type=" ETF ")
            + _daily("AAA", 2, asset_type=None))
    pop = pa.prepare_population(rows)
    assert {r["ticker"] for r in pop["reps"]} == {"AAA"}
    assert pop["n_excluded_etf"] == 4


def test_prepare_population_buy_candidate_only_ticker_absent_from_reps():
    rows = _daily("BCX", 5, rec_type="buy_candidate") + _daily("AAA", 1)
    pop = pa.prepare_population(rows)
    assert {r["ticker"] for r in pop["reps"]} == {"AAA"}
    assert {r["ticker"] for r in pop["reps_by_type"]} == {"AAA"}
    assert pop["n_excluded_out_of_scope_rows"] == 5
    assert all(r["rec_type"] != "buy_candidate" for r in pop["scoped_raw"])


def test_prepare_population_bought_on_buy_candidate_day_acted_rep_and_anchor_counted():
    np_rows = _daily("AAA", 3, rec_type="new_pick", alpha_pct=-5.0)
    bc = _r(ticker="AAA", rec_type="buy_candidate", rec_date=date(2026, 8, 10),
            acted_on=True, alpha_pct=7.0)
    pop = pa.prepare_population(np_rows + [bc])
    reps = pop["reps"]
    assert len(reps) == 1
    assert reps[0]["acted_on"] is True
    assert reps[0]["alpha_pct"] == 7.0
    assert pop["n_out_of_scope_anchor"] == 1
    # The per-type view stays within its own type's rows.
    by_type = pop["reps_by_type"]
    assert [r["rec_type"] for r in by_type] == ["new_pick"]
    assert by_type[0]["acted_on"] is False


def test_prepare_population_counts_and_surfacings():
    rows = (_daily("AAA", 4) + _daily("BBB", 2, alpha_pct=None)
            + _daily("CCC", 1, rec_type="enter_now"))
    pop = pa.prepare_population(rows)
    assert pop["n_raw_rows"] == 7
    assert pop["n_raw_graded"] == 5
    assert pop["n_tickers_graded"] == 2
    assert pop["surfacings_by_ticker"] == {"AAA": 4, "BBB": 2, "CCC": 1}


def test_prepare_population_does_not_mutate_input():
    rows = _daily("AAA", 3) + [_r(ticker="AAA", rec_type="buy_candidate",
                                  rec_date=date(2026, 8, 9), acted_on=True)]
    rows[0]["acted_on"] = False
    before = copy.deepcopy(rows)
    pop = pa.prepare_population(rows)
    pop["reps"][0]["acted_on"] = "tampered"
    assert rows == before


# ─── invariant + grey-flip ───────────────────────────────────────────────────

def test_calibration_n_sums_to_distinct_graded_tickers():
    rows = []
    for i, tk in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
        rows += _daily(tk, 3 + i, composite_score=60.0 + 3 * i)
    rows += _daily("H", 2, alpha_pct=None)   # never graded
    pop = pa.prepare_population(rows)
    bands = pa.calibration_by_score_band(pop["reps"])
    graded_reps = [r for r in pop["reps"]
                   if not r.get("outcome_maturing") and r.get("alpha_pct") is not None]
    assert sum(b["n"] for b in bands) == len(graded_reps) == pop["n_tickers_graded"] == 7


def test_grey_flip_twelve_rows_two_tickers_is_thin_and_ineligible():
    rows = _daily("AAA", 6, composite_score=76.0, alpha_pct=3.0) \
        + _daily("BBB", 6, composite_score=77.0, alpha_pct=4.0)
    pop = pa.prepare_population(rows)
    bands = pa.calibration_by_score_band(pop["reps"], min_n=PREDICTIVE_MIN_BAND_N)
    assert len(bands) == 1 and bands[0]["n"] == 2
    assert bands[0]["is_thin"] is True
    assert pa.personal_alpha_threshold(bands, min_n=PREDICTIVE_MIN_BAND_N) is None


def test_grey_flip_exactly_min_n_tickers_stays_eligible():
    rows = []
    for i in range(PREDICTIVE_MIN_BAND_N):
        rows += _daily(f"T{i}", 3, composite_score=76.0, alpha_pct=2.0)
    pop = pa.prepare_population(rows)
    bands = pa.calibration_by_score_band(pop["reps"], min_n=PREDICTIVE_MIN_BAND_N)
    assert bands[0]["n"] == PREDICTIVE_MIN_BAND_N
    assert bands[0]["is_thin"] is False
    assert pa.personal_alpha_threshold(bands, min_n=PREDICTIVE_MIN_BAND_N) == 75


# ─── personal_alpha_threshold D1 ─────────────────────────────────────────────

def _b(floor, n, hit, avg):
    return {"band_floor": floor, "band_label": f"{floor}–{floor + 4}", "n": n,
            "p_positive_alpha": hit, "avg_alpha": avg}


def test_threshold_d1_hit_half_mean_zero_does_not_qualify():
    assert pa.personal_alpha_threshold([_b(70, 10, 0.5, 0.0)], min_n=5) is None


def test_threshold_d1_hit_half_mean_just_positive_qualifies():
    assert pa.personal_alpha_threshold([_b(70, 10, 0.5, 0.01)], min_n=5) == 70


def test_threshold_d1_screenshot_shape_returns_none():
    # Hit rate >= 0.5 but mean alpha -1.7pp, one eligible band — used to read
    # as "alpha turns consistently positive".
    bands = [_b(65, 3, 0.9, 4.0), _b(70, 12, 0.58, -1.7), _b(75, 2, 1.0, 6.0)]
    assert pa.personal_alpha_threshold(bands, min_n=5) is None


def test_threshold_d1_thin_negative_band_above_ignored_but_disclosed():
    bands = [_b(70, 8, 0.6, 2.0), _b(75, 7, 0.7, 3.0), _b(80, 2, 0.0, -9.0)]
    t = pa.personal_alpha_threshold(bands, min_n=5)
    assert t == 70
    banner = pa.threshold_banner(bands, t, min_n=5)
    assert banner["thin_above"] == ["80–84"]
    assert "Not counted (fewer than 5 tickers): 80–84." in banner["text"]


def test_threshold_d1_none_avg_alpha_does_not_qualify():
    assert pa.personal_alpha_threshold([_b(70, 10, 0.8, None)], min_n=5) is None


# ─── threshold_banner ────────────────────────────────────────────────────────

def test_threshold_banner_none_thresh_is_none():
    assert pa.threshold_banner([_b(70, 10, 0.6, 1.0)], None, min_n=5) is None


def test_threshold_banner_k2_fields_and_text():
    bands = [_b(60, 9, 0.4, -2.0), _b(70, 6, 0.5, 1.0), _b(75, 4, 0.75, 4.0)]
    t = pa.personal_alpha_threshold(bands, min_n=4)
    assert t == 70
    bn = pa.threshold_banner(bands, t, min_n=4)
    assert bn["k"] == 2
    assert bn["qualifying"] == ["70–74", "75–79"]
    assert bn["n_tickers"] == 10                     # qualifying only, not the 60 band
    assert bn["weighted_avg_alpha"] == pytest.approx((6 * 1.0 + 4 * 4.0) / 10, abs=0.01)
    assert bn["min_hit_rate"] == 0.5
    assert bn["thin_above"] == []
    assert bn["text"].startswith("**Where the engine has worked for you: composite ≥ 70.**")
    assert "(10 tickers; avg alpha +2.2pp, lowest hit rate 50%)" in bn["text"]
    assert bn["text"].endswith(
        "\n\nCounts are distinct tickers, each at its first surfacing. "
        "Outcomes run from the call to today's price."
    )


def test_threshold_banner_k1_path():
    bands = [_b(70, 6, 0.67, 2.5), _b(75, 2, 1.0, 5.0)]
    t = pa.personal_alpha_threshold(bands, min_n=5)
    bn = pa.threshold_banner(bands, t, min_n=5)
    assert bn["k"] == 1
    assert bn["n_tickers"] == 6
    assert "**Only one band at or above 70 (70–74) has enough tickers to read** (6)" in bn["text"]
    assert "One band is a lead, not a pattern." in bn["text"]
    assert "Not counted (fewer than 5 tickers): 75–79." in bn["text"]


def test_threshold_banner_never_claims_beat_when_a_qualifying_avg_is_nonpositive():
    # A mismatched thresh (not from personal_alpha_threshold) must not yield
    # "beat SPY" text beside a band whose mean alpha is <= 0.
    for bad_avg in (0.0, -1.7):
        bands = [_b(70, 8, 0.6, bad_avg), _b(75, 8, 0.7, 3.0)]
        bn = pa.threshold_banner(bands, 70, min_n=5)
        assert bn is None or ("beat" not in bn["text"] and "positive" not in bn["text"])


# ─── synthesize_directives D2 ────────────────────────────────────────────────

def _synth(**kw):
    base = dict(bands=[], thresh=None, avm={}, conv=[], rtype=[], sec_alph=[],
                n_graded=10, min_n=5)
    base.update(kw)
    return pa.synthesize_directives(**base)


def test_d2_demoted_kinds_are_never_action():
    out = _synth(
        thresh=70,
        sec_alph=[{"sector": "Tech", "avg_alpha": 5.0, "n": 9}],
        rtype=[{"label": "New Position", "avg_alpha": 4.0},
               {"label": "Add to Winner", "avg_alpha": 1.0}],
        conv=[{"conviction": "Strong BUY", "avg_alpha": 5.0},
              {"conviction": "BUY", "avg_alpha": 1.0}],
    )
    demoted = [d for d in out if d["source_tab"] in
               ("🎯 Score Calibration", "🌐 Sector Alpha", "🏷️ Signal Breakdown")]
    assert len(demoted) == 4
    assert all(d["type"] == "watch" for d in demoted)
    joined = " ".join(d["text"].lower() for d in demoted)
    for word in ("skip", "reduc", "larger position", "lean into", "prioritis", "sizing"):
        assert word not in joined


def test_d2_threshold_directive_has_no_skip_or_reduce():
    out = _synth(thresh=70)
    t = [d for d in out if d["source_tab"] == "🎯 Score Calibration"][0]["text"].lower()
    assert "skip" not in t and "reduc" not in t
    assert "70" in t


def test_d2_context_line_n_is_the_ticker_count_passed_in():
    out = _synth(n_graded=37)
    ctx = [d for d in out if d["source_tab"] == "all models"][0]["text"]
    assert ctx.startswith("Based on 37 graded tickers")


# ─── sentiment Unknown exclusion ─────────────────────────────────────────────

def test_sentiment_unknown_excluded_from_both_sides():
    by_verdict = [
        {"verdict": "Confirmed", "n": 6, "avg_alpha": 3.0},
        {"verdict": "Conflicted", "n": 5, "avg_alpha": 1.0},
        {"verdict": "Unknown", "n": 40, "avg_alpha": -10.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["n_unknown"] == 40
    assert out["other_n"] == 5
    assert out["other_avg_alpha"] == pytest.approx(1.0)
    assert out["conclusion"] == "confirmed_wins"


def test_sentiment_others_only_unknown_is_insufficient():
    by_verdict = [
        {"verdict": "Confirmed", "n": 6, "avg_alpha": 3.0},
        {"verdict": "Unknown", "n": 40, "avg_alpha": -10.0},
        {"verdict": "", "n": 2, "avg_alpha": -1.0},
    ]
    out = pa.sentiment_alignment_summary(by_verdict, min_n=3)
    assert out["other_n"] == 0
    assert out["n_unknown"] == 42
    assert out["conclusion"] == "insufficient_data"


def test_calibration_by_verdict_is_thin_flag():
    rows = [_r(ticker=f"T{i}", verdict="Confirmed") for i in range(3)]
    out = pa.calibration_by_verdict(rows, thin_n=3)
    assert out[0]["is_thin"] is False
    out2 = pa.calibration_by_verdict(rows, thin_n=4)
    assert out2[0]["is_thin"] is True
