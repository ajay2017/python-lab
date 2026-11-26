"""stock_analyzer/corr_window.py + risk_window.classify_holdings (CorrYoung C1,
docs/plans/corr-young-holding.md).

The old builder (portfolio.correlation_matrix) cut the price frame listwise, so ONE
holding with four closes measured every pair of the book on four shared days. These
pin the replacement: a holding under CORR_MIN_OBS_TRUSTED returns is left out and
named, and each pair of the others is measured on its own shared sessions. The old
builder is inlined below as the oracle for an aligned book.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import corr_window, market_time, portfolio, risk_window
from stock_analyzer.constants import BETA_MIN_RETURNS, CORR_MIN_OBS_TRUSTED

FLOOR = CORR_MIN_OBS_TRUSTED
END = "2026-11-20"    # a Friday


# ── fixtures ────────────────────────────────────────────────────────────────
def _walk(n, seed, vol=0.012):
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1 + rng.normal(0.0004, vol, n))


def _frame(vals, idx):
    return {"df": pd.DataFrame({"Close": np.asarray(vals, dtype=float)}, index=idx)}


def _tail(n, seed, end=END, tz=None):
    """n daily bars on business days ending `end`."""
    idx = pd.bdate_range(end=end, periods=n, tz=tz)
    return _frame(_walk(n, seed), idx)


def _book(n_long=10, n=130, **young):
    """n_long aligned long names L0.. plus young names {ticker: n_returns} that
    end on the same last session (n_returns + 1 bars)."""
    idx = pd.bdate_range(end=END, periods=n)
    common = _walk(n, 1000, vol=0.008)
    held = {}
    for i in range(n_long):
        rets = np.diff(common) / common[:-1] * 0.6 + np.random.default_rng(i).normal(0, 0.01, n - 1)
        vals = 100.0 * np.cumprod(np.r_[1.0, 1 + rets])
        held[f"L{i}"] = _frame(vals, idx)
    for t, n_ret in young.items():
        held[t] = _tail(n_ret + 1, seed=sum(map(ord, t)) + 7)
    return held


def _old_matrix(held):
    """The pre-CorrYoung portfolio.correlation_matrix, verbatim (the oracle)."""
    series = {}
    for ticker, data in held.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            series[ticker] = hist["Close"]
    if len(series) < 2:
        return pd.DataFrame()
    prices = pd.DataFrame(series).dropna()
    returns = prices.pct_change().dropna()
    return returns.corr().round(3)


def _old_coverage(held):
    """The pre-CorrYoung portfolio.correlation_coverage, verbatim (the oracle)."""
    series = {}
    for ticker, data in held.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            series[ticker] = hist["Close"]
    if len(series) < 2:
        return None
    lengths = {t: int(s.dropna().shape[0]) for t, s in series.items()}
    prices = pd.DataFrame(series).dropna()
    shortest = min(lengths, key=lambda t: lengths[t])
    return {
        "n_tickers": len(series),
        "n_obs": max(0, len(prices) - 1),
        "shortest_ticker": shortest,
        "shortest_len": lengths[shortest],
        "longest_len": max(lengths.values()),
        "lengths": lengths,
    }


def _pair_corr_on_tail(held, a, b, n_returns):
    """Independent check: corr of two names over their last n_returns+1 shared closes."""
    ca = held[a]["df"]["Close"]
    cb = held[b]["df"]["Close"]
    shared = ca.index.intersection(cb.index)[-(n_returns + 1):]
    ra = ca.loc[shared].pct_change().iloc[1:]
    rb = cb.loc[shared].pct_change().iloc[1:]
    return round(float(np.corrcoef(ra, rb)[0, 1]), 3)


# ── classify_holdings ───────────────────────────────────────────────────────
def test_classify_holdings_counts_at_the_floor_and_names_the_rest():
    held = {
        "OLD": _tail(40, 1),
        "AT": _tail(BETA_MIN_RETURNS + 1, 2),       # exactly 20 returns: counts
        "JUST_UNDER": _tail(BETA_MIN_RETURNS, 3),   # 19 returns: left out
        "NOPX": {"df": None},
        "EMPTY": {"df": pd.DataFrame()},
        "NOCLOSE": {"df": pd.DataFrame({"Open": [1.0, 2.0]}, index=pd.bdate_range(end=END, periods=2))},
    }
    counted, left_out = risk_window.classify_holdings(
        held, list(held), min_returns=BETA_MIN_RETURNS)
    assert list(counted) == ["OLD", "AT"]
    assert all(isinstance(s, pd.Series) and s.index.tz is None for s in counted.values())
    assert left_out == [
        {"ticker": "JUST_UNDER", "reason": "short_history", "n_returns": BETA_MIN_RETURNS - 1},
        {"ticker": "NOPX", "reason": "no_prices", "n_returns": 0},
        {"ticker": "EMPTY", "reason": "no_prices", "n_returns": 0},
        {"ticker": "NOCLOSE", "reason": "no_prices", "n_returns": 0},
    ]


def test_classify_holdings_dedupes_tickers_and_reads_history_key():
    held = {"A": {"history": _tail(30, 1)["df"]}}
    counted, left_out = risk_window.classify_holdings(held, ["A", "A", "B"], min_returns=20)
    assert list(counted) == ["A"]
    assert left_out == [{"ticker": "B", "reason": "no_prices", "n_returns": 0}]


def test_classify_holdings_non_dict_held_data_is_all_no_prices():
    counted, left_out = risk_window.classify_holdings(None, ["A"], min_returns=20)
    assert counted == {} and left_out[0]["reason"] == "no_prices"


def test_book_returns_and_corr_window_share_one_definition_of_young():
    """Parity: the same left-out set and the same n_returns from both modules."""
    held = _book(n_long=4, ANT=4, NEW=19, EDGE=20)
    held["GONE"] = {"df": None}
    port = pd.DataFrame({
        "Ticker": list(held), "Weight (%)": [100.0 / len(held)] * len(held)})
    _series, cov = risk_window.book_returns(port, held)
    cw = corr_window.corr_coverage(held)
    key = lambda x: (x["ticker"], x["reason"], x["n_returns"])   # noqa: E731
    rw_left = sorted(key(x) for x in cov["left_out"])
    cw_left = sorted(key(x) for x in cw["left_out"])
    assert rw_left == cw_left
    assert {x["ticker"] for x in cw["left_out"]} == {"ANT", "NEW", "GONE"}   # EDGE (20) counts
    assert cw["min_returns"] == cov["min_returns"] == BETA_MIN_RETURNS


def test_tripwire_corr_floor_equals_beta_floor():
    assert CORR_MIN_OBS_TRUSTED == BETA_MIN_RETURNS, (
        "CORR_MIN_OBS_TRUSTED and BETA_MIN_RETURNS diverged: the young-holding rule "
        "(one 'young' definition shared by risk_window.classify_holdings, the book "
        "return series and the correlation matrix) must be revisited before either "
        "moves -- a name could count for the matrix and not for the book, or the "
        "reverse (docs/plans/corr-young-holding.md section 7).")


# ── a young holding at 1 / 4 / 19 / 20 returns ──────────────────────────────
@pytest.mark.parametrize("n_young", [1, 4, FLOOR - 1])
def test_young_holding_below_the_floor_is_left_out_and_named(n_young):
    held = _book(n_long=10, ANT=n_young)
    corr = corr_window.corr_matrix(held)
    cov = corr_window.corr_coverage(held)
    assert "ANT" not in corr.columns and len(corr.columns) == 10
    assert cov["left_out"] == [{"ticker": "ANT", "reason": "short_history", "n_returns": n_young}]
    assert cov["n_obs"] == 129                       # the long names keep their whole window
    assert cov["shortest_ticker"] == "ANT" and cov["shortest_len"] == n_young + 1
    # Exactly the matrix of the book without it.
    long_only = {t: d for t, d in held.items() if t != "ANT"}
    pd.testing.assert_frame_equal(corr, corr_window.corr_matrix(long_only))
    # ...and it is named by correlation_unchecked with no further change (Q1 = B).
    assert portfolio.correlation_unchecked(corr, list(held)) == ["ANT"]
    assert portfolio.correlation_claim_verified(
        portfolio.correlation_unchecked(corr, list(held)), cov) is False


def test_young_holding_at_the_floor_counts_and_pairs_on_its_own_days():
    held = _book(n_long=10, ANT=FLOOR)
    corr = corr_window.corr_matrix(held)
    cov = corr_window.corr_coverage(held)
    assert "ANT" in corr.columns and len(corr.columns) == 11
    assert cov["left_out"] == []
    assert cov["n_obs"] == FLOOR                      # fewest shared returns behind any pair
    assert cov["n_obs_max"] == 129
    assert portfolio.correlation_unchecked(corr, list(held)) == []
    assert corr.loc["ANT", "L0"] == _pair_corr_on_tail(held, "ANT", "L0", FLOOR)
    # a long x long pair is NOT cut to ANT's window
    assert corr.loc["L0", "L1"] == _pair_corr_on_tail(held, "L0", "L1", 129)


def test_no_re_entry_cliff_a_counted_name_pairs_on_its_own_days_only():
    held = _book(n_long=10, ANT=25)
    corr = corr_window.corr_matrix(held)
    cov = corr_window.corr_coverage(held)
    assert cov["n_obs"] == 25 and cov["n_obs_max"] == 129
    without = corr_window.corr_matrix({t: d for t, d in held.items() if t != "ANT"})
    sub = corr.loc[without.index, without.columns]
    pd.testing.assert_frame_equal(sub, without)       # the long pairs keep ~125+ days
    assert corr.loc["ANT", "L3"] == _pair_corr_on_tail(held, "ANT", "L3", 25)


def test_pair_over_the_long_window_stays_flagged_with_a_young_holding_in_the_book():
    idx = pd.bdate_range(end=END, periods=130)
    base = _walk(130, 5)
    rng = np.random.default_rng(6)
    twin = base * np.cumprod(1 + rng.normal(0, 0.003, 130))     # a genuine co-mover
    held = {"AAA": _frame(base, idx), "BBB": _frame(twin, idx), "CCC": _frame(_walk(130, 9), idx),
            "ANT": _tail(5, 3)}
    corr = corr_window.corr_matrix(held)
    assert float(corr.loc["AAA", "BBB"]) >= 0.80
    pairs = portfolio.diversification_score(corr)["risk_pairs"]
    assert any(p["level"] == "danger" and {p["t1"], p["t2"]} == {"AAA", "BBB"} for p in pairs)
    assert "ANT" not in {p["t1"] for p in pairs} | {p["t2"] for p in pairs}


# ── the 0-day all-NaN case and the short-download failure ──────────────────
def test_disjoint_counted_names_give_an_all_nan_matrix_with_n_obs_zero():
    a = _frame(_walk(40, 1), pd.bdate_range("2026-01-05", periods=40))
    b = _frame(_walk(40, 2), pd.bdate_range("2026-06-01", periods=40))
    corr = corr_window.corr_matrix({"A": a, "B": b})
    cov = corr_window.corr_coverage({"A": a, "B": b})
    assert list(corr.columns) == ["A", "B"]
    assert np.isnan(corr.loc["A", "B"]) and np.isnan(corr.loc["B", "A"])   # no shared return
    assert corr.loc["A", "A"] == corr.loc["B", "B"] == 1.0                 # each name moves on its own window
    assert cov["n_obs"] == 0 and cov["n_obs_max"] == 0 and cov["left_out"] == []
    assert portfolio.correlation_sample_state(cov) == portfolio.CORR_SAMPLE_THIN
    assert portfolio.diversification_score(corr)["score"] is None


def test_one_long_name_with_a_short_download_is_left_out_not_governing():
    held = _book(n_long=6)
    held["TGT"] = _tail(5 + 1, 77)              # a bad provider day: 5 bars
    cov = corr_window.corr_coverage(held)
    assert cov["left_out"] == [{"ticker": "TGT", "reason": "short_history", "n_returns": 5}]
    assert cov["n_obs"] == 129
    assert portfolio.correlation_sample_state(cov) == portfolio.CORR_SAMPLE_TRUSTED
    assert "TGT" not in corr_window.corr_matrix(held).columns


def test_fewer_than_two_counted_is_an_empty_matrix_and_a_zero_n_obs_dict():
    held = {"LONG": _tail(60, 1), "ANT": _tail(5, 2)}
    assert corr_window.corr_matrix(held).empty
    cov = corr_window.corr_coverage(held)
    assert cov is not None and cov["n_obs"] == 0 and cov["n_obs_max"] == 0
    assert [x["ticker"] for x in cov["left_out"]] == ["ANT"]
    # fewer than two USABLE series stays the offline contract
    assert corr_window.corr_coverage({"LONG": _tail(60, 1), "X": {"df": None}}) is None
    assert corr_window.corr_matrix({"LONG": _tail(60, 1), "X": {"df": None}}).empty
    assert corr_window.corr_coverage({}) is None and corr_window.corr_matrix({}).empty


# ── an aligned book is identical to the old listwise builder ───────────────
@pytest.mark.parametrize("tz", [None, "America/New_York"])
def test_aligned_book_matrix_and_coverage_match_the_old_builder(tz):
    idx = pd.bdate_range(end=END, periods=120, tz=tz)
    held = {f"S{i}": _frame(_walk(120, i), idx) for i in range(8)}
    pd.testing.assert_frame_equal(corr_window.corr_matrix(held), _old_matrix(held))
    new, old = corr_window.corr_coverage(held), _old_coverage(held)
    assert {k: new[k] for k in old} == old
    assert new["n_obs"] == new["n_obs_max"] == 119 and new["left_out"] == []
    assert new["min_returns"] == FLOOR
    # portfolio's public wrappers return the same thing
    pd.testing.assert_frame_equal(portfolio.correlation_matrix(held), _old_matrix(held))
    assert {k: portfolio.correlation_coverage(held)[k] for k in old} == old


def test_aligned_book_with_a_flat_holding_keeps_the_nan_diagonal():
    idx = pd.bdate_range(end=END, periods=60)
    held = {"A": _frame(_walk(60, 1), idx), "B": _frame(_walk(60, 2), idx),
            "FLAT": _frame(np.full(60, 1.0), idx)}
    new = corr_window.corr_matrix(held)
    pd.testing.assert_frame_equal(new, _old_matrix(held))
    assert np.isnan(new.loc["FLAT", "FLAT"])


def test_flat_holding_on_the_pairwise_path_is_still_flat():
    a, b = _tail(60, 1), _tail(50, 2)
    held = {"A": a, "B": b, "FLAT": _frame(np.full(40, 1.0), pd.bdate_range(end=END, periods=40))}
    corr = corr_window.corr_matrix(held)
    assert np.isnan(corr.loc["FLAT", "FLAT"]) and corr.loc[["FLAT"], ["A", "B"]].isna().all().all()
    assert portfolio.correlation_flat(corr, list(held), corr_window.corr_coverage(held)) == ["FLAT"]


def test_matrix_is_symmetric_with_a_unit_diagonal_on_the_pairwise_path():
    held = _book(n_long=4, ANT=30, NEW=45)
    corr = corr_window.corr_matrix(held)
    assert (corr.values == corr.values.T).all() or np.allclose(corr.values, corr.values.T, equal_nan=True)
    assert np.allclose(np.diag(corr.values), 1.0)
    assert list(corr.index) == list(corr.columns) == list(held)


# ── coverage shape ──────────────────────────────────────────────────────────
def test_coverage_keys_types_and_weights():
    held = _book(n_long=3, ANT=4)
    held["GONE"] = {"df": None}
    cov = corr_window.corr_coverage(held, weights={"ANT": 3.6, "GONE": float("nan")})
    for k in ("n_tickers", "n_obs", "n_obs_max", "shortest_ticker", "shortest_len",
              "longest_len", "lengths", "min_returns", "left_out"):
        assert k in cov
    assert type(cov["n_obs"]) is int and type(cov["n_obs_max"]) is int      # plain ints
    assert cov["n_tickers"] == 4                       # holdings with usable prices
    assert cov["left_out"] == [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6},
        {"ticker": "GONE", "reason": "no_prices", "n_returns": 0, "weight_pct": None},
    ]
    assert all("weight_pct" not in x for x in corr_window.corr_coverage(held)["left_out"])


def test_min_returns_is_a_parameter_not_a_literal():
    held = _book(n_long=3, ANT=25)
    cov = corr_window.corr_coverage(held, min_returns=30)
    assert cov["min_returns"] == 30 and [x["ticker"] for x in cov["left_out"]] == ["ANT"]
    assert "ANT" not in corr_window.corr_matrix(held, min_returns=30).columns


def test_portfolio_wrappers_read_the_floor_at_call_time(monkeypatch):
    held = _book(n_long=3, ANT=25)
    assert "ANT" in portfolio.correlation_matrix(held).columns
    monkeypatch.setattr(portfolio, "CORR_MIN_OBS_TRUSTED", 30)
    assert "ANT" not in portfolio.correlation_matrix(held).columns
    assert portfolio.correlation_coverage(held)["min_returns"] == 30


# ── never raises ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    None, [], "x", 7,
    {"A": None, "B": 5},
    {"A": {"df": "nope"}, "B": {"df": [1, 2]}},
    {"A": {"df": pd.DataFrame({"Close": [1.0, 2.0, 3.0]})},          # RangeIndex: one session
     "B": {"df": pd.DataFrame({"Close": ["a", "b", "c"]})}},
    {"A": {"df": pd.DataFrame({"Close": [1.0] * 30}, index=list("abcdefghijklmnopqrstuvwxyzabcd"))},
     "B": _tail(30, 1)},
])
def test_never_raises_on_bad_input(bad):
    assert isinstance(corr_window.corr_matrix(bad), pd.DataFrame)
    cov = corr_window.corr_coverage(bad)
    assert cov is None or isinstance(cov, dict)


# ── time context: the module is clock-free and session-dated ────────────────
@pytest.fixture
def _no_clock(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("corr_window must not read the clock")
    monkeypatch.setattr(market_time, "now_et", boom)
    monkeypatch.setattr(market_time, "today_et", boom)


def test_result_does_not_depend_on_the_visit_day(_no_clock):
    """A weekend / holiday visit, an after-hours one: the data decides, not today."""
    held = _book(n_long=4, ANT=30)
    first = corr_window.corr_coverage(held)
    assert first["n_obs"] == 30 and first["n_obs_max"] == 129
    pd.testing.assert_frame_equal(corr_window.corr_matrix(held), corr_window.corr_matrix(held))


def test_tz_aware_et_index_matches_the_naive_one(_no_clock):
    idx_n = pd.bdate_range(end=END, periods=80)
    idx_e = idx_n.tz_localize("America/New_York")
    vals = {t: _walk(80, i) for i, t in enumerate("ABC")}
    naive = {t: _frame(v, idx_n) for t, v in vals.items()}
    aware = {t: _frame(v, idx_e) for t, v in vals.items()}
    pd.testing.assert_frame_equal(corr_window.corr_matrix(naive), corr_window.corr_matrix(aware))
    assert corr_window.corr_coverage(naive)["n_obs"] == corr_window.corr_coverage(aware)["n_obs"] == 79


def test_intraday_stamped_bar_merges_into_its_session(_no_clock):
    """A live bar stamped 11:30 ET on the last session lines up with the other
    names' midnight stamps instead of dropping off the shared window."""
    idx = pd.bdate_range(end=END, periods=60, tz="America/New_York")
    live = idx.copy()
    live = live.where(live != live[-1], live[-1] + pd.Timedelta(hours=11, minutes=30))
    held = {"A": _frame(_walk(60, 1), idx), "B": _frame(_walk(60, 2), live),
            "C": _frame(_walk(60, 3), idx)}
    assert corr_window.corr_coverage(held)["n_obs"] == 59
    assert corr_window.corr_coverage({t: _frame(d["df"]["Close"].to_numpy(), idx)
                                      for t, d in held.items()})["n_obs"] == 59
    pd.testing.assert_frame_equal(
        corr_window.corr_matrix(held),
        corr_window.corr_matrix({t: _frame(d["df"]["Close"].to_numpy(), idx) for t, d in held.items()}))


def test_seoul_stamped_name_lines_up_with_us_sessions(_no_clock):
    """A KST-midnight bar is the session date it names; converting to ET first
    would move every bar onto the previous calendar day and break the overlap."""
    us_idx = pd.bdate_range(end=END, periods=60)
    seoul_idx = us_idx.tz_localize("Asia/Seoul")
    held = {"US1": _frame(_walk(60, 1), us_idx), "US2": _frame(_walk(60, 2), us_idx),
            "KR": _frame(_walk(60, 3), seoul_idx)}
    cov = corr_window.corr_coverage(held)
    assert cov["n_obs"] == 59
    ref = {t: _frame(d["df"]["Close"].to_numpy(), us_idx) for t, d in held.items()}
    pd.testing.assert_frame_equal(corr_window.corr_matrix(held), corr_window.corr_matrix(ref))


def test_us_holiday_with_a_non_us_name_trading_pairs_on_shared_sessions(_no_clock):
    """The KR name trades on a US holiday (no US bar that day): the pair is
    measured on the sessions both have, returns spanning the gap on both sides."""
    us_idx = pd.bdate_range(end=END, periods=60)
    holiday = us_idx[30]
    us_days = us_idx.delete(30)
    held = {"US": _frame(_walk(59, 1), us_days), "US2": _frame(_walk(59, 2), us_days),
            "KR": _frame(_walk(60, 3), us_idx)}
    cov = corr_window.corr_coverage(held)
    assert holiday not in us_days
    assert cov["n_obs"] == 58 and cov["n_obs_max"] == 58      # 59 shared closes -> 58 returns
    assert corr_window.corr_matrix(held).loc["US", "KR"] == _pair_corr_on_shared(held, "US", "KR")


def _pair_corr_on_shared(held, a, b):
    ca, cb = held[a]["df"]["Close"], held[b]["df"]["Close"]
    shared = ca.index.intersection(cb.index)
    ra, rb = ca.loc[shared].pct_change().iloc[1:], cb.loc[shared].pct_change().iloc[1:]
    return round(float(np.corrcoef(ra, rb)[0, 1]), 3)


def test_duplicate_session_rows_keep_the_last_and_count_once(_no_clock):
    idx = pd.bdate_range(end=END, periods=40)
    dup = idx.append(idx[-1:] + pd.Timedelta(hours=10))        # two rows on the last date
    vals = np.r_[_walk(40, 1), 130.0]
    held = {"A": _frame(vals, dup), "B": _tail(40, 2)}
    cov = corr_window.corr_coverage(held)
    assert cov["lengths"]["A"] == 40 and cov["n_obs"] == 39
