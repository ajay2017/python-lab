"""Pure R:R readout helpers (stock_analyzer/rr_readout.py)."""
import pytest

from stock_analyzer.constants import (
    RR_ENTRY_MIN, RR_FAVOURABLE_MIN, RR_MARGINAL_MIN,
)
from stock_analyzer.rr_readout import (
    fmt_ratio_vs, is_entry_ready, rank_add_candidates, redeploy_headline, rr_quality,
)

pytestmark = pytest.mark.fast


@pytest.mark.parametrize("value,thr,expected", [
    (1.97, (2.0,), "1.97"),
    (1.999, (2.0,), "1.99"),
    (2.04, (2.0,), "2.0"),
    (2.46, (2.5,), "2.46"),
    (0.7, (2.0,), "0.7"),
    (2.0, (2.0,), "2.0"),
    (1.97, (1.5, 2.5), "2.0"),
    (2.46, (1.5, 2.5), "2.46"),
])
def test_fmt_ratio_vs(value, thr, expected):
    assert fmt_ratio_vs(value, *thr) == expected


def test_fmt_ratio_vs_no_thresholds_is_plain_one_decimal():
    assert fmt_ratio_vs(1.97) == "2.0"


def test_rr_quality_bands_and_boundaries():
    assert rr_quality(RR_FAVOURABLE_MIN) == "favourable"
    assert rr_quality(RR_FAVOURABLE_MIN - 0.01) == "marginal"
    assert rr_quality(RR_MARGINAL_MIN) == "marginal"
    assert rr_quality(RR_MARGINAL_MIN - 0.01) == "poor"
    assert rr_quality(0.1) == "poor"
    # ❌ is exactly "fails the entry minimum" (owner decision 2026-10-23)
    assert RR_MARGINAL_MIN == RR_ENTRY_MIN
    assert rr_quality(RR_ENTRY_MIN - 0.01) == "poor"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, 0.0, -1.0, "2.0", True])
def test_rr_quality_none_for_unusable(bad):
    assert rr_quality(bad) is None


def _c(t, rr, passes=True, score=75.0, sector="Energy"):
    return {"ticker": t, "score": score, "rr": rr, "passes": passes, "sector": sector}


def test_headline_entry_at_exact_boundary():
    h = redeploy_headline([_c("AAA", RR_ENTRY_MIN)])
    assert h["state"] == "entry" and h["ticker"] == "AAA"
    assert h["rr"] == RR_ENTRY_MIN and h["sector"] == "Energy" and h["score"] == 75.0


def test_headline_names_later_passer_that_clears_rr():
    h = redeploy_headline([_c("EOG", 0.7), _c("CVX", 2.5)])
    assert h["state"] == "entry" and h["ticker"] == "CVX"


def test_headline_rr_fails_names_first_passer():
    h = redeploy_headline([_c("EOG", 0.7), _c("XOM", 1.2)])
    assert h["state"] == "rr_fails" and h["ticker"] == "EOG"


@pytest.mark.parametrize("rr", [None, float("nan"), float("inf")])
def test_headline_rr_unknown(rr):
    h = redeploy_headline([_c("AAA", rr)])
    assert h["state"] == "rr_unknown" and h["ticker"] == "AAA"


@pytest.mark.parametrize("rr", [0, 0.0, -1.0])
def test_headline_no_upside_for_finite_nonpositive_rr(rr):
    h = redeploy_headline([_c("AAA", rr)])
    assert h["state"] == "no_upside" and h["ticker"] == "AAA"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "x"])
def test_fmt_ratio_vs_non_finite_is_na(bad):
    assert fmt_ratio_vs(bad, 2.0) == "n/a"


def test_headline_none_pass_and_none_scored():
    assert redeploy_headline([_c("A", 3.0, passes=False)])["state"] == "none_pass"
    assert redeploy_headline([_c("A", 3.0, passes=None)])["state"] == "none_scored"
    assert redeploy_headline([])["state"] == "none_scored"


def test_headline_custom_rr_min():
    assert redeploy_headline([_c("A", 1.5)], rr_min=1.5)["state"] == "entry"


def _tickers(rows):
    return [r["ticker"] for r in rows]


def test_rank_live_case_entry_first():
    rows = [
        _c("OXY", 1.8, True, 74), _c("COP", None, True, 69),
        _c("CVX", 2.3, True, 68), _c("EOG", 0.3, True, 72),
    ]
    ranked = rank_add_candidates(rows)
    assert _tickers(ranked) == ["CVX", "OXY", "EOG", "COP"]
    h = redeploy_headline(ranked[:3])
    assert h["state"] == "entry" and h["ticker"] == "CVX"
    by_score = sorted(rows, key=lambda c: -c["score"])[:3]
    assert redeploy_headline(by_score)["state"] != "entry"


def test_rank_boundary_rr():
    at = _c("AT", RR_ENTRY_MIN, True, 60)
    below = _c("BELOW", RR_ENTRY_MIN - 0.01, True, 90)
    assert is_entry_ready(at) and not is_entry_ready(below)
    assert _tickers(rank_add_candidates([below, at])) == ["AT", "BELOW"]


def test_rank_failer_with_high_rr_not_promoted():
    rows = [_c("FAIL", 5.0, False, 99), _c("PASS", 0.5, True, 66)]
    assert _tickers(rank_add_candidates(rows)) == ["PASS", "FAIL"]
    assert not is_entry_ready(rows[0])


@pytest.mark.parametrize("rr", [float("nan"), float("inf"), True, None])
def test_entry_ready_rejects_non_finite_or_bool_rr(rr):
    assert not is_entry_ready(_c("X", rr))


def test_rank_unscored_last_and_composite_desc_and_stable_and_pure():
    rows = [
        _c("U", None, None, None), _c("P1", 1.0, True, 70), _c("F", 1.0, False, 90),
        _c("P2", 1.0, True, 80), _c("P3", 1.0, True, 70),
    ]
    snapshot = [dict(r) for r in rows]
    ranked = rank_add_candidates(rows)
    assert _tickers(ranked) == ["P2", "P1", "P3", "F", "U"]
    assert rows == snapshot
    assert ranked is not rows


def test_is_entry_ready_custom_rr_min():
    c = _c("X", 1.5)
    assert is_entry_ready(c, 1.5) and not is_entry_ready(c, 1.6)


def test_rank_unscored_with_high_rr_stays_last():
    # Withheld-verdict shape: R:R is computed even when the score is withheld.
    rows = [_c("W", 3.0, passes=None, score=None), _c("F", 0.5, passes=False, score=60.0)]
    assert [r["ticker"] for r in rank_add_candidates(rows)] == ["F", "W"]


def test_rank_passer_with_non_positive_rr_is_tier_one():
    rows = [_c("Z", 0.0, score=80.0), _c("N", -0.4, score=79.0),
            _c("E", RR_ENTRY_MIN, score=66.0), _c("F", 3.0, passes=False, score=60.0)]
    assert [r["ticker"] for r in rank_add_candidates(rows)] == ["E", "Z", "N", "F"]
