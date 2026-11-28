"""stock_analyzer/buy_check_state.py::earnings_state (entry-fail-closed C1,
docs/plans/entry-fail-closed.md §2 / §7). Pure: no clock, no streamlit, no db.

C1 changes no behaviour: these tests pin the reader only; nothing holds a buy on
it yet (C3)."""
from pathlib import Path

import pytest

from stock_analyzer import asset_type
from stock_analyzer import buy_check_state as bcs

pytestmark = pytest.mark.fast


def _b(**kw):
    base = {"asset_type": asset_type.ASSET_TYPE_STOCK}
    base.update(kw)
    return base


def test_etf_with_a_stray_date_within_seven_days_is_not_applicable():
    out = bcs.earnings_state(_b(asset_type=asset_type.ASSET_TYPE_ETF,
                                earnings="2026-12-02", earnings_status="date"))
    assert out == {"state": "not_applicable", "date": None}


def test_etf_with_failed_status_and_no_date_is_still_not_applicable():
    # A bare {} from Yahoo (the ETF shape) is not a failure for a fund.
    out = bcs.earnings_state(_b(asset_type=asset_type.ASSET_TYPE_ETF,
                                earnings=None, earnings_status="failed"))
    assert out["state"] == "not_applicable"


def test_parseable_date_is_date():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_timestamped_date_reads_its_first_ten_characters():
    out = bcs.earnings_state(_b(earnings="2026-12-02 16:00:00", earnings_status="date"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_past_date_is_still_date():
    out = bcs.earnings_state(_b(earnings="2020-01-15", earnings_status="date"))
    assert out == {"state": "date", "date": "2020-01-15"}


def test_no_date_status_without_a_date_is_no_date():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="no_date"))
    assert out == {"state": "no_date", "date": None}


@pytest.mark.parametrize("empty", [None, "", {}, []])
def test_no_date_status_with_any_empty_earnings_value_is_no_date(empty):
    assert bcs.earnings_state(_b(earnings=empty, earnings_status="no_date"))["state"] == "no_date"


def test_failed_status_without_a_date_is_failed():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="failed"))
    assert out == {"state": "failed", "date": None}


def test_absent_status_key_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None))["state"] == "failed"
    assert bcs.earnings_state(_b())["state"] == "failed"


def test_status_key_present_but_none_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None, earnings_status=None))["state"] == "failed"


def test_unknown_status_string_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None, earnings_status="weird"))["state"] == "failed"


def test_absent_status_key_with_a_date_is_date():
    out = bcs.earnings_state(_b(earnings="2026-12-02"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_stale_cached_bundle_is_failed_even_with_a_date_and_a_date_status():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date",
                                stale_as_of="2026-11-20"))
    assert out == {"state": "failed", "date": None}


def test_stale_cached_bundle_with_a_no_date_status_is_failed():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="no_date",
                                stale_as_of="2026-11-20"))
    assert out["state"] == "failed"


def test_fresh_bundle_stale_as_of_none_is_not_stale():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date",
                                stale_as_of=None))
    assert out["state"] == "date"


def test_unparseable_date_is_failed_not_no_date():
    out = bcs.earnings_state(_b(earnings="not a date", earnings_status="no_date"))
    assert out == {"state": "failed", "date": None}


@pytest.mark.parametrize("garbage", [None, [], "AAPL", 42, 3.5, ("a",), object()])
def test_non_dict_bundle_is_failed_and_never_raises(garbage):
    assert bcs.earnings_state(garbage) == {"state": "failed", "date": None}


@pytest.mark.parametrize("raw", [{"a": 1}, 20261202, "2026-13-45", "Dec 2, 2026"])
def test_garbage_earnings_value_is_failed_and_never_raises(raw):
    assert bcs.earnings_state(_b(earnings=raw, earnings_status="date"))["state"] == "failed"


def test_a_bundle_whose_get_raises_is_failed():
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert bcs.earnings_state(_Boom()) == {"state": "failed", "date": None}


def test_input_is_not_mutated():
    b = _b(earnings="2026-12-02", earnings_status="date")
    snap = dict(b)
    bcs.earnings_state(b)
    assert b == snap


def test_result_dicts_are_independent_copies():
    a = bcs.earnings_state(None)
    a["state"] = "date"
    assert bcs.earnings_state(None)["state"] == "failed"


def test_reads_no_clock_under_a_frozen_clock(monkeypatch):
    """The state is a function of the bundle alone: freezing time at two different
    instants (and making every clock accessor raise) cannot change it."""
    import time
    import datetime as _dt

    def _boom(*a, **k):
        raise AssertionError("earnings_state read the clock")
    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(time, "monotonic", _boom)
    b = _b(earnings="2026-12-02", earnings_status="date")
    first = bcs.earnings_state(b)
    second = bcs.earnings_state(b)
    assert first == second == {"state": "date", "date": "2026-12-02"}
    assert bcs.earnings_state(_b(earnings=None, earnings_status="no_date"))["state"] == "no_date"
    assert _dt.datetime is not None  # sanity: datetime itself still importable


def test_module_is_pure_no_streamlit_db_and_no_clock_read():
    src = (Path(__file__).resolve().parent.parent / "stock_analyzer"
           / "buy_check_state.py").read_text(encoding="utf-8")
    for banned in ("import streamlit", "from stock_analyzer import db", "market_time",
                   "utcnow", "date.today", "datetime.now", "time.time"):
        assert banned not in src, banned


# ── entry-fail-closed C2a: fit_state / beta_leg_could_bind ───────────────────
import pandas as pd  # noqa: E402

from stock_analyzer.constants import (  # noqa: E402
    PORTFOLIO_BETA_CEILING, TICKER_BETA_CRITICAL,
)


def _frame(priced=("MSFT",), price=100.0):
    """A priced frame holding exactly `priced` (Ticker / Sector / Price / Market Value)."""
    return pd.DataFrame([{"Ticker": t, "Sector": "Technology", "Price": price,
                          "Market Value": 1000.0} for t in priced])


_ONE_HOLDING = [{"Ticker": "MSFT", "Shares": 1, "Avg Cost ($)": 100.0}]


def _h(*tickers, shares=1.0, avg=50.0):
    return [{"Ticker": t, "Shares": shares, "Avg Cost ($)": avg} for t in tickers]


def test_fit_state_empty_holdings_is_flat_whatever_the_frame():
    for rows in ([], (), pd.DataFrame()):
        for frame in (None, pd.DataFrame(), _frame()):
            assert bcs.fit_state(rows, frame) == {
                "state": "flat", "unpriced_sectors": None, "unpriced": None}


def test_fit_state_holdings_with_no_usable_frame_is_unknown():
    for frame in (None, pd.DataFrame(), "frame", 7):
        assert bcs.fit_state(_ONE_HOLDING, frame) == {
            "state": "unknown", "unpriced_sectors": None, "unpriced": None}


def test_fit_state_holdings_dataframe_nonempty_counts_as_holdings():
    rows = pd.DataFrame(_ONE_HOLDING)
    assert bcs.fit_state(rows, None)["state"] == "unknown"
    assert bcs.fit_state(rows, _frame())["state"] == "ready"


def test_fit_state_ready_with_nothing_unpriced_has_an_empty_map_and_set():
    assert bcs.fit_state(_ONE_HOLDING, _frame()) == {
        "state": "ready", "unpriced_sectors": set(), "unpriced": {}}


def test_fit_state_attrs_dropped_holdings_rule_is_retired():
    """C5 flip: the old rule read `attrs["dropped_holdings"]`; the unpriced map is now
    holdings minus priced frame rows, so a stale / absent / hostile attrs list changes
    nothing in either direction."""
    f = _frame()
    f.attrs["dropped_holdings"] = [{"ticker": "NVDA", "reason": "no_price_data"}]
    assert bcs.fit_state(_ONE_HOLDING, f)["unpriced"] == {}
    f.attrs["dropped_holdings"] = "garbage"
    assert bcs.fit_state(_ONE_HOLDING, f)["unpriced"] == {}
    # and an unpriced holding is found with NO attrs at all
    g = _frame()
    assert "dropped_holdings" not in g.attrs
    assert bcs.fit_state(_h("MSFT", "NVDA"), g)["unpriced"] == {"NVDA": "Semiconductors"}


def test_fit_state_unpriced_sector_is_the_curated_one():
    from stock_analyzer.portfolio import resolve_sector
    out = bcs.fit_state(_h("MSFT", "NVDA"), _frame())
    assert out == {"state": "ready", "unpriced_sectors": {resolve_sector("NVDA")},
                   "unpriced": {"NVDA": resolve_sector("NVDA")}}
    assert resolve_sector("NVDA") != "Other"          # not vacuous: NVDA is curated


def test_fit_state_unresolvable_unpriced_ticker_reads_all():
    out = bcs.fit_state(_h("MSFT", "ZZZZQX"), _frame())
    assert out["unpriced"] == {"ZZZZQX": "__ALL__"}
    assert out["unpriced_sectors"] == {"__ALL__"}


def test_fit_state_etf_holding_with_no_price_reads_all():
    # No ETF is in TICKER_SECTORS: an unpriced VOO could hide ANY sector's weight.
    assert bcs.fit_state(_h("MSFT", "VOO"), _frame())["unpriced"] == {"VOO": "__ALL__"}


def test_fit_state_mixed_unpriced_holdings_collect_sectors():
    from stock_analyzer.portfolio import resolve_sector
    out = bcs.fit_state(_h("AAA", "NVDA", "MSFT", "AMD"), _frame(priced=("AAA",)))
    assert out["unpriced_sectors"] == {resolve_sector("NVDA"), resolve_sector("MSFT"),
                                       resolve_sector("AMD")}


def test_fit_state_zero_avg_cost_holding_counts_as_unpriced_qa4():
    """C5 flip (owner QA4): shares > 0 with a zero / invalid average cost never
    reaches the frame (build_portfolio_df drops it as invalid_shares_or_cost), and the
    old rule skipped it; it now counts as unpriced."""
    rows = [{"Ticker": "MSFT", "Shares": 1, "Avg Cost ($)": 100.0},
            {"Ticker": "NVDA", "Shares": 2, "Avg Cost ($)": 0}]
    assert bcs.fit_state(rows, _frame())["unpriced"] == {"NVDA": "Semiconductors"}


@pytest.mark.parametrize("bad", [[None], [5], [["MSFT"]]])
def test_fit_state_unreadable_holding_rows_read_unknown_never_empty(bad):
    # An unreadable row list is not "nothing was unpriced": fail closed (unknown).
    assert bcs.fit_state(bad, _frame()) == {
        "state": "unknown", "unpriced_sectors": None, "unpriced": None}


@pytest.mark.parametrize("rows", ["AAPL", 42, 3.5, object(), {"a": 1}])
def test_fit_state_garbage_holdings_is_unknown_and_never_raises(rows):
    assert bcs.fit_state(rows, _frame()) == {
        "state": "unknown", "unpriced_sectors": None, "unpriced": None}


def test_fit_state_none_holdings_is_unknown():
    assert bcs.fit_state(None, _frame()) == {
        "state": "unknown", "unpriced_sectors": None, "unpriced": None}


def test_fit_state_frame_that_raises_is_unknown():
    class _Boom:
        @property
        def empty(self):
            raise RuntimeError("boom")
    assert bcs.fit_state(_ONE_HOLDING, _Boom())["state"] == "unknown"


def test_fit_state_inputs_not_mutated_and_results_independent():
    rows = _h("MSFT", "NVDA")
    snap_rows = [dict(r) for r in rows]
    f = _frame()
    f.attrs["x"] = 1
    snap_attrs = dict(f.attrs)
    snap_frame = f.copy()
    a = bcs.fit_state(rows, f)
    a["unpriced_sectors"].add("X")
    a["unpriced"]["ZZ"] = "X"
    assert rows == snap_rows and f.attrs == snap_attrs and f.equals(snap_frame)
    again = bcs.fit_state(rows, f)
    assert "X" not in again["unpriced_sectors"] and "ZZ" not in again["unpriced"]


# beta_leg_could_bind at the REAL constants (strict `>`, as _portfolio_risk_gate)
def test_beta_boundaries_use_the_real_constants():
    # the plan's table (ticker 1.81 / 1.80, port 1.41 / 1.40) assumes these values
    assert TICKER_BETA_CRITICAL == 1.8 and PORTFOLIO_BETA_CEILING == 1.4


def test_port_beta_unknown_binds_only_when_ticker_beta_is_over_critical():
    assert bcs.beta_leg_could_bind(None, 1.81) is True
    assert bcs.beta_leg_could_bind(None, TICKER_BETA_CRITICAL + 0.01) is True
    assert bcs.beta_leg_could_bind(None, 1.80) is False
    assert bcs.beta_leg_could_bind(None, TICKER_BETA_CRITICAL) is False   # strict >
    assert bcs.beta_leg_could_bind(None, 0.9) is False


def test_ticker_beta_unknown_binds_only_when_port_beta_is_over_ceiling():
    assert bcs.beta_leg_could_bind(1.41, None) is True
    assert bcs.beta_leg_could_bind(PORTFOLIO_BETA_CEILING + 0.01, None) is True
    assert bcs.beta_leg_could_bind(1.40, None) is False
    assert bcs.beta_leg_could_bind(PORTFOLIO_BETA_CEILING, None) is False  # strict >
    assert bcs.beta_leg_could_bind(0.8, None) is False


def test_both_betas_unknown_could_bind():
    assert bcs.beta_leg_could_bind(None, None) is True


def test_both_betas_known_is_false_the_gate_itself_decides():
    assert bcs.beta_leg_could_bind(1.5, 2.0) is False
    assert bcs.beta_leg_could_bind(1.0, 2.0) is False


@pytest.mark.parametrize("unread", [float("nan"), float("inf"), "n/a", True, object()])
def test_unreadable_beta_values_read_as_unknown(unread):
    assert bcs.beta_leg_could_bind(unread, 1.81) is True
    assert bcs.beta_leg_could_bind(unread, 1.0) is False
    assert bcs.beta_leg_could_bind(1.5, unread) is True
    assert bcs.beta_leg_could_bind(1.0, unread) is False
    assert bcs.beta_leg_could_bind(unread, unread) is True


def test_beta_mirrors_the_advisor_comparison_exactly():
    """Pin the mirror: the advisor's hard G-06 branch calls `beta_fit_gate.breached`,
    which uses strict `>` on both legs (beta gate C1)."""
    import inspect
    from stock_analyzer import beta_fit_gate
    from stock_analyzer import watchlist_advisor as wa
    src = inspect.getsource(wa._portfolio_risk_gate)
    assert "_beta_fit_gate.breached(port_beta, ticker_beta)" in src
    bsrc = inspect.getsource(beta_fit_gate.breached)
    assert "pb > PORTFOLIO_BETA_CEILING and tb > TICKER_BETA_CRITICAL" in bsrc
    assert "PORTFOLIO_BETA_CEILING" in inspect.getsource(bcs.beta_leg_could_bind)
    assert "TICKER_BETA_CRITICAL" in inspect.getsource(bcs.beta_leg_could_bind)


def test_c2a_functions_read_no_clock(monkeypatch):
    import time

    def _boom(*a, **k):
        raise AssertionError("read the clock")
    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(time, "monotonic", _boom)
    assert bcs.fit_state(_ONE_HOLDING, _frame())["state"] == "ready"
    assert bcs.beta_leg_could_bind(None, 1.9) is True
