"""RiskListwiseYoung C2: the two book-return builders use risk_window.book_returns.

Spec: docs/plans/risk-young-holding-c2-spec.md. Pure frames only; nothing here
reads a clock. The headless _build_context leg lives in
tests/test_risk_young_holding_headless.py.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import beta_repair, portfolio, risk, risk_window
from stock_analyzer.constants import BETA_MIN_RETURNS
from tests.test_risk_window import N_BOOK, _frame, _held, _old_listwise, _port

pytestmark = pytest.mark.fast


def _book(young_closes=None, young_pct=3.6):
    if young_closes is None:
        port = _port([("AAA", 60.0), ("BBB", 40.0)])
        held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2))
    else:
        big = 100.0 - young_pct
        port = _port([("AAA", big * 0.6), ("BBB", big * 0.4), ("YNG", young_pct)])
        held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2),
                     YNG=_frame(young_closes, 5))
    return port, held


def _spy():
    return _frame(N_BOOK, 99)


# 1. no-op regression -----------------------------------------------------------
def test_book_without_a_young_holding_matches_the_old_listwise_code():
    port, held = _book()
    spy = _spy()
    cov: dict = {}
    got = risk.compute_portfolio_risk_metrics(port, held, spy, coverage_out=cov)

    old = _old_listwise(port, held)                      # the oracle
    cum_old = (1 + old).cumprod()
    pd.testing.assert_series_equal(
        got["cum_returns"], cum_old, check_names=False, check_freq=False)

    # the old beta / vol / Sharpe / VaR / drawdown, recomputed from the oracle
    rf = 0.045 / 252
    excess = old - rf
    assert got["ann_volatility"] == round(float(old.std() * np.sqrt(252) * 100), 1)
    assert got["sharpe"] == round(float((excess.mean() / old.std()) * np.sqrt(252)), 2)
    assert got["var_95_pct"] == round(float(np.percentile(old, 5) * 100), 2)
    dd = (cum_old - cum_old.cummax()) / cum_old.cummax() * 100
    assert got["max_drawdown"] == round(float(dd.min()), 1)
    spy_ret = spy["Close"].pct_change().dropna()
    comb = pd.concat([old, spy_ret], axis=1, keys=["p", "s"]).dropna()
    assert got["beta"] == round(float(comb.cov().loc["p", "s"] / comb["s"].var()), 2)

    assert cov["state"] == "full"
    assert got["coverage"] == cov


# 2. ANT-like ------------------------------------------------------------------
def test_young_holding_under_the_cap_does_not_blank_the_book():
    base_port, base_held = _book()
    spy = _spy()
    base = risk.compute_portfolio_risk_metrics(base_port, base_held, spy)

    port, held = _book(young_closes=12, young_pct=3.6)
    cov: dict = {}
    got = risk.compute_portfolio_risk_metrics(port, held, spy, coverage_out=cov)
    assert got is not None and got["beta"] is not None
    assert len(got["cum_returns"]) == len(base["cum_returns"])
    assert cov["state"] == "partial"
    assert [(x["ticker"], x["reason"]) for x in cov["left_out"]] == [("YNG", "short_history")]


def test_young_holding_over_the_cap_is_unmeasured_and_fills_coverage_out():
    port, held = _book(young_closes=12, young_pct=15.0)
    cov: dict = {}
    assert risk.compute_portfolio_risk_metrics(port, held, _spy(), coverage_out=cov) is None
    assert cov["state"] == "unmeasured"
    assert [x["ticker"] for x in cov["left_out"]] == ["YNG"]


def test_coverage_out_is_cleared_and_filled_on_every_call():
    port, held = _book(young_closes=12, young_pct=15.0)
    cov = {"stale": 1}
    risk.compute_portfolio_risk_metrics(port, held, _spy(), coverage_out=cov)
    assert "stale" not in cov and cov["state"] == "unmeasured"
    # and the no-argument call still works (keyword-only, optional)
    assert risk.compute_portfolio_risk_metrics(port, held, _spy()) is None


# 3. the 19 / 20 returns line --------------------------------------------------
@pytest.mark.parametrize("n_closes", [20, 21])
def test_counted_exactly_when_the_holding_beta_is_measurable(n_closes):
    port, held = _book(young_closes=n_closes, young_pct=5.0)
    spy = _spy()
    cov: dict = {}
    risk.compute_portfolio_risk_metrics(port, held, spy, coverage_out=cov)
    counted = "YNG" not in {x["ticker"] for x in cov["left_out"]}
    assert counted == (risk.beta_vs_market(held["YNG"]["df"], spy) is not None)
    assert counted == (n_closes - 1 >= BETA_MIN_RETURNS)


# 4. SPY stamps -----------------------------------------------------------------
def test_intraday_tz_aware_spy_bar_still_joins_its_session():
    port, held = _book()
    spy = _spy()
    flat = risk.compute_portfolio_risk_metrics(port, held, spy)

    stamped = spy.copy()
    idx = stamped.index.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
    idx = idx.where(idx != idx[-1], idx[-1] + pd.Timedelta(minutes=45))   # partial last bar 10:15
    stamped.index = idx
    got = risk.compute_portfolio_risk_metrics(port, held, stamped)
    assert got["beta"] is not None
    assert got["beta"] == flat["beta"]


# 5. SPY missing ----------------------------------------------------------------
@pytest.mark.parametrize("spy", [None, pd.DataFrame(), pd.DataFrame({"Open": [1.0, 2.0]})])
def test_missing_spy_gives_no_beta_but_every_other_metric_and_coverage(spy):
    port, held = _book()
    got = risk.compute_portfolio_risk_metrics(port, held, spy)
    assert got is not None and got["beta"] is None
    assert got["ann_volatility"] is not None and got["coverage"]["state"] == "full"


# 6. one series for both builders ---------------------------------------------
def test_return_series_and_risk_metrics_share_one_index():
    port, held = _book(young_closes=12, young_pct=3.6)
    series = portfolio.portfolio_return_series(port, held)
    metrics = risk.compute_portfolio_risk_metrics(port, held, _spy())
    direct, _cov = risk_window.book_returns(port, held)
    pd.testing.assert_series_equal(series, direct)
    assert series.index.equals(metrics["cum_returns"].index)


def test_return_series_keeps_its_none_contract():
    port, held = _book()
    assert portfolio.portfolio_return_series(None, held) is None
    assert portfolio.portfolio_return_series(port, {}) is None
    p2, h2 = _book(young_closes=12, young_pct=15.0)
    assert portfolio.portfolio_return_series(p2, h2) is None


def test_aligned_beta_window_is_the_books_window_with_a_young_holding():
    port, held = _book(young_closes=12, young_pct=3.6)
    book = portfolio.portfolio_return_series(port, held)
    cand = _frame(N_BOOK, 11)["Close"]
    beta, n = beta_repair.aligned_beta(
        candidate_close=cand, spy_close=_spy()["Close"], on_index=book.index)
    assert beta is not None and n == len(book)


# 8. the log line ---------------------------------------------------------------
def test_coverage_log_text_full_partial_unmeasured_and_not_built():
    port, held = _book()
    _, full = risk_window.book_returns(port, held)
    assert risk_window.coverage_log_text(full).startswith("portfolio risk coverage: state=full left_out=none")

    port, held = _book(young_closes=12, young_pct=3.6)
    _, part = risk_window.book_returns(port, held)
    txt = risk_window.coverage_log_text(part)
    assert "state=partial" in txt and "YNG:short_history:11:3.6" in txt
    assert f"window={part['window_returns']}" in txt

    port, held = _book(young_closes=12, young_pct=15.0)
    _, un = risk_window.book_returns(port, held)
    txt = risk_window.coverage_log_text(un)
    assert "state=unmeasured" in txt and "YNG:short_history:11:15.0" in txt

    for bad in (None, {}, "x", 5):
        assert risk_window.coverage_log_text(bad) == "portfolio risk coverage: not built"
    # a malformed dict never raises
    assert risk_window.coverage_log_text({"state": "partial", "left_out": [5]}) \
        == "portfolio risk coverage: not built"
    assert "left_out=X:?:None:?" not in risk_window.coverage_log_text({"state": "partial"})


def test_session_closes_normalises_stamps_and_drops_nan():
    df = pd.DataFrame(
        {"Close": [1.0, np.nan, 2.0, 3.0]},
        index=pd.DatetimeIndex(["2026-11-02 09:30", "2026-11-03 09:30",
                                "2026-11-04 09:30", "2026-11-04 15:59"]).tz_localize("America/New_York"))
    s = risk_window.session_closes(df)
    assert list(s.index) == [pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-04")]
    assert list(s.values) == [1.0, 3.0]            # duplicate date keeps the last
    assert risk_window.session_closes(None) is None
    assert risk_window.session_closes(pd.DataFrame()) is None


# 9. the bare 20 is now the constant -------------------------------------------
@pytest.mark.parametrize("fn", ["beta_vs_market", "pearson_corr_vs_benchmark"])
def test_overlap_floor_is_beta_min_returns(fn):
    func = getattr(risk, fn)
    for n_closes, expect_value in ((BETA_MIN_RETURNS, False), (BETA_MIN_RETURNS + 1, True)):
        a = _frame(n_closes, 3)
        b = _frame(n_closes, 4)
        out = func(a, b)
        assert (out is not None) == expect_value, (fn, n_closes)


def test_default_floors_in_portfolio_and_beta_repair_are_the_constant():
    import inspect
    assert inspect.signature(portfolio.correlation_to_portfolio).parameters[
        "min_overlap"].default == BETA_MIN_RETURNS
    assert inspect.signature(beta_repair.aligned_beta).parameters[
        "min_obs"].default == BETA_MIN_RETURNS
