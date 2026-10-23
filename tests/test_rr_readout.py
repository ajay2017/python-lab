"""Pure R:R readout helpers (stock_analyzer/rr_readout.py)."""
import pytest

from stock_analyzer.constants import (
    RR_ENTRY_MIN, RR_FAVOURABLE_MIN, RR_MARGINAL_MIN,
)
from stock_analyzer.rr_readout import fmt_ratio_vs, redeploy_headline, rr_quality

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
