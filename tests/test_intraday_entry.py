"""Tests for stock_analyzer/intraday_entry.py — intraday pullback entry
window detection (_safe_float, compute_intraday_entries). Pure module, no I/O
(fetch_intraday_prices does a live yfinance network call and is explicitly out
of scope, per house convention for pure-logic passes). compute_intraday_entries
carries real decision logic (a fail-safe SPY suppression gate, and from G-35 /
beta-gate C4 the pick's own stop) and gets the most rigorous coverage.
"""
from stock_analyzer import intraday_entry as ie
from stock_analyzer.constants import ATR_STOP_MULT, PULLBACK_ENTRY_DIP_PCT
import pytest

pytestmark = pytest.mark.fast


# ─── _safe_float ─────────────────────────────────────────────────────────────

def test_safe_float_none_returns_default():
    assert ie._safe_float(None) is None
    assert ie._safe_float(None, default=5.0) == 5.0


def test_safe_float_nan_returns_default():
    assert ie._safe_float(float("nan")) is None
    assert ie._safe_float(float("nan"), default=-1.0) == -1.0


def test_safe_float_valid_numeric_string():
    assert ie._safe_float("192.5") == 192.5


def test_safe_float_unconvertible_string_returns_default():
    assert ie._safe_float("not-a-number") is None
    assert ie._safe_float("not-a-number", default=0.0) == 0.0


def test_safe_float_valid_number_passthrough():
    assert ie._safe_float(100) == 100.0


# ─── compute_intraday_entries — builders ────────────────────────────────────

_CALM_SPY = {"current": 500.0, "open": 500.0}


def _pick(ticker="AAA", composite_score=70.0, stop=50.0, atr=None):
    p = {"ticker": ticker, "composite_score": composite_score,
         "sizing": {"stop": stop}}
    if atr is not None:
        p["atr"] = atr
    return p


def _run(picks, price_data, spy=None, dip_pct=1.5, spy_max_down=1.0):
    return ie.compute_intraday_entries(
        picks, price_data, spy_data=spy if spy is not None else _CALM_SPY,
        dip_pct=dip_pct, spy_max_down=spy_max_down)


# ─── compute_intraday_entries — SPY guard branches ──────────────────────────

def test_spy_data_none_is_unavailable_guard():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data=None, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == {"entries": [], "excluded": [], "spy_guard": "unavailable"}


def test_spy_current_unreadable_is_unavailable_guard():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data={"current": None, "open": 500.0}, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == {"entries": [], "excluded": [], "spy_guard": "unavailable"}


def test_spy_open_zero_or_negative_is_unavailable_guard():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data={"current": 500.0, "open": 0.0}, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result["entries"] == [] and result["spy_guard"] == "unavailable"


def test_spy_down_more_than_max_is_freefall_guard():
    # SPY down 2% > spy_max_down=1.0 -> freefall guard suppresses all entries.
    spy = {"current": 98.0, "open": 100.0}
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == {"entries": [], "excluded": [], "spy_guard": "freefall"}


def test_spy_down_exactly_at_max_boundary_suppresses():
    # spy_drop == -spy_max_down exactly -> suppressed, per `<=`.
    spy = {"current": 99.0, "open": 100.0}  # -1.0% exactly
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result["entries"] == [] and result["spy_guard"] == "freefall"


def test_spy_down_just_inside_max_boundary_proceeds():
    # spy_drop == -0.99% -- just inside the -1.0% ceiling, does not suppress.
    spy = {"current": 99.01, "open": 100.0}
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},  # -10% dip, well past dip_pct
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert len(result["entries"]) == 1 and result["spy_guard"] is None


# ─── compute_intraday_entries — pick-level filtering ────────────────────────

def test_pick_not_in_price_data_is_excluded_no_price_not_silently_dropped():
    result = _run([_pick(ticker="ZZZ")], {"AAA": {"current": 90.0, "open": 100.0}})
    assert result["entries"] == []
    assert [(x["ticker"], x["why"]) for x in result["excluded"]] == [("ZZZ", "no_price")]


def test_blank_ticker_is_skipped_silently():
    result = _run([_pick(ticker="")], {})
    assert result["entries"] == [] and result["excluded"] == []


def test_dip_below_threshold_is_neither_entry_nor_excluded():
    # -1.0% drop, dip_pct=1.5 -> doesn't reach the threshold.
    result = _run([_pick(ticker="AAA")], {"AAA": {"current": 99.0, "open": 100.0}})
    assert result["entries"] == [] and result["excluded"] == []


def test_dip_exactly_at_the_constant_threshold_is_an_entry():
    # drop exactly -PULLBACK_ENTRY_DIP_PCT -> included, per `<=`.
    cur = round(100.0 * (1 - PULLBACK_ENTRY_DIP_PCT / 100.0), 6)
    result = _run([_pick(ticker="AAA")], {"AAA": {"current": cur, "open": 100.0}},
                  dip_pct=PULLBACK_ENTRY_DIP_PCT)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["intraday_drop_pct"] == -PULLBACK_ENTRY_DIP_PCT


def test_enriches_pick_preserving_original_keys():
    pick = _pick(ticker="AAA", composite_score=82.0, stop=90.0)
    pick["thesis"] = "Strong setup"
    result = _run([pick], {"AAA": {"current": 94.567, "open": 100.0}})
    assert len(result["entries"]) == 1
    enriched = result["entries"][0]
    assert enriched["ticker"] == "AAA"
    assert enriched["composite_score"] == 82.0
    assert enriched["thesis"] == "Strong setup"
    assert enriched["current_price"] == 94.57  # rounded to 2dp
    assert enriched["open_price"] == 100.0
    assert enriched["intraday_drop_pct"] == round((94.567 - 100.0) / 100.0 * 100, 2)
    assert enriched["stop_distance_pct"] == round((94.567 - 90.0) / 94.567 * 100, 1)


def test_sort_order_is_composite_first_then_bigger_dip():
    picks = [_pick(ticker="AAA", composite_score=70.0),
             _pick(ticker="BBB", composite_score=70.0),
             _pick(ticker="CCC", composite_score=80.0)]
    price_data = {
        "AAA": {"current": 97.0, "open": 100.0},   # -3.0%
        "BBB": {"current": 90.0, "open": 100.0},   # -10.0%
        "CCC": {"current": 98.0, "open": 100.0},   # -2.0%, but the highest composite
    }
    result = _run(picks, price_data)
    assert [r["ticker"] for r in result["entries"]] == ["CCC", "BBB", "AAA"]


def test_equal_composite_falls_back_to_biggest_dip_first():
    price_data = {"AAA": {"current": 97.0, "open": 100.0},
                  "BBB": {"current": 90.0, "open": 100.0}}
    result = _run([_pick(ticker="AAA"), _pick(ticker="BBB")], price_data)
    assert [r["ticker"] for r in result["entries"]] == ["BBB", "AAA"]


# ─── the stop check (G-35, IntradayBelowStop) ───────────────────────────────

def test_current_equal_to_stop_is_excluded_below_stop():
    result = _run([_pick(stop=95.0)], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["entries"] == []
    (x,) = result["excluded"]
    assert x["ticker"] == "AAA" and x["why"] == "below_stop"
    assert x["current_price"] == 95.0 and x["stop"] == 95.0
    assert x["intraday_drop_pct"] == -5.0 and x["open_price"] == 100.0


def test_current_below_stop_is_excluded():
    result = _run([_pick(stop=96.0)], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["entries"] == []
    assert result["excluded"][0]["why"] == "below_stop"


def test_current_one_cent_above_stop_is_an_entry():
    result = _run([_pick(stop=94.99)], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["excluded"] == []
    assert len(result["entries"]) == 1


@pytest.mark.parametrize("stop", [None, 0, 0.0, -1.0, "x", float("nan")])
def test_missing_or_nonpositive_stop_is_excluded_stop_unknown(stop):
    result = _run([_pick(stop=stop)], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["entries"] == []
    assert [x["why"] for x in result["excluded"]] == ["stop_unknown"]


@pytest.mark.parametrize("pick", [
    {"ticker": "AAA", "composite_score": 70.0},                      # no sizing key
    {"ticker": "AAA", "composite_score": 70.0, "sizing": None},      # sizing None
    {"ticker": "AAA", "composite_score": 70.0, "sizing": {}},        # sizing empty
])
def test_pick_with_no_sizing_stop_is_excluded_stop_unknown(pick):
    result = _run([pick], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["entries"] == []
    assert [x["why"] for x in result["excluded"]] == ["stop_unknown"]


def test_sized_out_marker_with_stop_at_uses_it_as_the_stop():
    # stop_infeasible carries `stop_at` instead of `stop`
    pk = {"ticker": "AAA", "composite_score": 70.0,
          "sizing": {"stop_infeasible": True, "stop_at": 96.0}}
    out = _run([pk], {"AAA": {"current": 95.0, "open": 100.0}})
    assert out["entries"] == [] and out["excluded"][0]["why"] == "below_stop"
    assert out["excluded"][0]["stop"] == 96.0
    pk["sizing"]["stop_at"] = 94.0
    out = _run([pk], {"AAA": {"current": 95.0, "open": 100.0}})
    assert out["entries"][0]["stop"] == 94.0 and out["excluded"] == []


@pytest.mark.parametrize("marker", ["ceiling_infeasible", "capital_infeasible",
                                    "portfolio_unknown", "holding_unknown",
                                    "room_infeasible"])
def test_sized_out_marker_without_a_stop_is_excluded_no_size(marker):
    pk = {"ticker": "AAA", "composite_score": 70.0, "sizing": {marker: True}}
    out = _run([pk], {"AAA": {"current": 95.0, "open": 100.0}})
    assert out["entries"] == []
    assert [x["why"] for x in out["excluded"]] == ["no_size"]


def test_not_dipped_pick_below_its_stop_is_neither_entry_nor_excluded():
    # -0.5% from the open, but already below its stop: not emailed, not "excluded".
    result = _run([_pick(stop=200.0)], {"AAA": {"current": 99.5, "open": 100.0}})
    assert result["entries"] == [] and result["excluded"] == []


def test_a_pick_that_cannot_be_read_is_excluded_not_raised():
    class _Bad(dict):
        def get(self, k, d=None):
            if k == "sizing":
                raise RuntimeError("boom")
            return super().get(k, d)
    bad = _Bad(ticker="AAA", composite_score=70.0)
    result = _run([bad], {"AAA": {"current": 95.0, "open": 100.0}})
    assert result["entries"] == []
    assert [(x["ticker"], x["why"]) for x in result["excluded"]] == [("AAA", "unreadable")]


def test_no_entry_is_ever_at_or_below_its_stop():
    picks = [_pick(ticker=t, stop=s) for t, s in
             [("AAA", 94.0), ("BBB", 95.0), ("CCC", 96.0), ("DDD", None)]]
    pd_ = {t: {"current": 95.0, "open": 100.0} for t in ("AAA", "BBB", "CCC", "DDD")}
    result = _run(picks, pd_)
    for e in result["entries"]:
        assert e["current_price"] > e["sizing"]["stop"]
    assert [e["ticker"] for e in result["entries"]] == ["AAA"]
    assert {x["ticker"]: x["why"] for x in result["excluded"]} == {
        "BBB": "below_stop", "CCC": "below_stop", "DDD": "stop_unknown"}


# ─── drop_atr is measurement only (owner U9 = a) ────────────────────────────

def test_drop_atr_is_computed_on_entries_and_excluded():
    result = _run([_pick(ticker="AAA", stop=90.0, atr=2.0),
                   _pick(ticker="BBB", stop=99.0, atr=4.0)],
                  {"AAA": {"current": 96.0, "open": 100.0},
                   "BBB": {"current": 96.0, "open": 100.0}})
    assert result["entries"][0]["drop_atr"] == 2.0           # (100 - 96) / 2
    assert result["excluded"][0]["drop_atr"] == 1.0          # (100 - 96) / 4


@pytest.mark.parametrize("atr", [None, 0, 0.0, -1.0, "x", float("nan")])
def test_drop_atr_is_none_on_missing_or_nonpositive_atr(atr):
    pick = _pick(stop=90.0)
    pick["atr"] = atr
    result = _run([pick], {"AAA": {"current": 96.0, "open": 100.0}})
    assert result["entries"][0]["drop_atr"] is None


def test_drop_atr_never_changes_entries_or_excluded():
    prices = {"AAA": {"current": 90.0, "open": 100.0},     # a 10-point drop
              "BBB": {"current": 90.0, "open": 100.0}}

    def build(atr):
        return [_pick(ticker="AAA", stop=80.0, atr=atr),
                _pick(ticker="BBB", stop=95.0, atr=atr)]

    huge = _run(build(0.01), prices)      # a 1000-ATR drop
    tiny = _run(build(1000.0), prices)    # a 0.01-ATR drop
    none = _run(build(None), prices)

    def shape(r):
        return ([e["ticker"] for e in r["entries"]],
                [(x["ticker"], x["why"]) for x in r["excluded"]])
    assert shape(huge) == shape(tiny) == shape(none) == (["AAA"], [("BBB", "below_stop")])


# ─── log lines ──────────────────────────────────────────────────────────────

def test_log_lines_name_every_candidate_and_exclusion():
    res = _run([_pick(ticker="AAA", stop=90.0, atr=2.0), _pick(ticker="BBB", stop=99.0),
                _pick(ticker="CCC"), _pick(ticker="DDD", stop=None)],
               {"AAA": {"current": 96.0, "open": 100.0},
                "BBB": {"current": 96.0, "open": 100.0},
                "DDD": {"current": 96.0, "open": 100.0}})
    cand = ie.candidates_log_line(res)
    assert "AAA (drop -4.0%, 2.00 ATR, stop 90.00 vs now 96.00)" in cand
    assert "BBB (drop -4.0%, n/a ATR, stop 99.00 vs now 96.00)" in cand
    assert "DDD (drop -4.0%, n/a ATR, stop n/a vs now 96.00)" in cand
    line = ie.excluded_log_line(res)
    assert "BBB below_stop (now 96.00 ≤ stop 99.00)" in line
    assert "CCC no_price" in line and "DDD stop_unknown" in line
    assert ie.excluded_log_line({"entries": [], "excluded": []}) == "intraday excluded: none"
    assert ie.candidates_log_line({"entries": [], "excluded": []}) == "intraday candidates: none"


def test_no_entry_reason_names_the_guard_or_the_count():
    assert ie.no_entry_reason({"spy_guard": "unavailable"}) == "SPY unavailable"
    assert ie.no_entry_reason({"spy_guard": "freefall"}) == "SPY in freefall"
    assert ie.no_entry_reason({"spy_guard": None, "excluded": []}) == "none pulled back enough"
    assert "2 excluded" in ie.no_entry_reason({"spy_guard": None, "excluded": [1, 2]})


# ─── ETF picks: the stop is measured from the open (reviewer FIX-FIRST) ─────

def _etf(ticker="VOO", stop=None, atr=2.0, composite_score=70.0):
    """An ETF pick re-anchors its stop at the run price, so its sizing.stop sits
    ATR_STOP_MULT x ATR below the CURRENT price, never below the open."""
    p = {"ticker": ticker, "composite_score": composite_score, "asset_type": "etf",
         "sizing": {"stop": stop}}
    if atr is not None:
        p["atr"] = atr
    return p


def test_etf_below_the_open_based_stop_is_excluded_below_stop():
    band = ATR_STOP_MULT * 2.0
    cur = 100.0 - band - 0.5                    # fell past open - MULT x ATR
    # the re-anchored stop is below the current price, so the pick stop alone passes
    out = _run([_etf(stop=cur - band)], {"VOO": {"current": cur, "open": 100.0}})
    assert out["entries"] == []
    (x,) = out["excluded"]
    assert x["why"] == "below_stop" and x["stop_basis"] == "open"
    assert x["stop"] == round(100.0 - band, 2)


def test_etf_at_exactly_the_open_based_stop_is_excluded_and_a_cent_above_is_in():
    band = ATR_STOP_MULT * 2.0
    out = _run([_etf(stop=1.0)], {"VOO": {"current": 100.0 - band, "open": 100.0}})
    assert [x["why"] for x in out["excluded"]] == ["below_stop"]
    out = _run([_etf(stop=1.0)], {"VOO": {"current": 100.0 - band + 0.01, "open": 100.0}})
    assert out["excluded"] == [] and len(out["entries"]) == 1


def test_etf_dip_inside_the_band_is_an_entry_with_open_basis():
    band = ATR_STOP_MULT * 2.0
    cur = 98.0                                   # -2%, well inside the band
    out = _run([_etf(stop=cur - band)], {"VOO": {"current": cur, "open": 100.0}})
    assert out["excluded"] == []
    (e,) = out["entries"]
    assert e["stop_basis"] == "open" and e["stop"] == round(100.0 - band, 2)
    assert e["stop_distance_pct"] == round((cur - (100.0 - band)) / cur * 100, 1)


def test_etf_keeps_its_own_stop_when_that_is_higher():
    band = ATR_STOP_MULT * 2.0
    higher = 100.0 - band + 1.0
    out = _run([_etf(stop=higher)], {"VOO": {"current": 98.0, "open": 100.0}})
    (e,) = out["entries"]
    assert e["stop_basis"] == "pick" and e["stop"] == higher


def test_etf_with_no_sizing_stop_is_never_an_entry_on_the_open_stop_alone():
    """Lead fix after review: an ETF with no morning size / stop is excluded like a
    stock (the open-based stop can only RAISE a pick's stop, never stand in for one)."""
    pk = _etf(stop=None)
    pk["sizing"] = {}
    out = _run([pk], {"VOO": {"current": 98.0, "open": 100.0}})
    assert out["entries"] == []
    assert [x["why"] for x in out["excluded"]] == ["stop_unknown"]


def test_a_sized_out_etf_is_no_size():
    pk = _etf(stop=None)
    pk["sizing"] = {"ceiling_infeasible": True, "one_share_pct": 30.0}
    out = _run([pk], {"VOO": {"current": 98.0, "open": 100.0}})
    assert out["entries"] == []
    assert [x["why"] for x in out["excluded"]] == ["no_size"]


@pytest.mark.parametrize("atr", [None, 0, -1.0, "x"])
def test_etf_with_no_readable_atr_is_excluded_stop_unknown(atr):
    pk = _etf(stop=50.0, atr=None)
    pk["atr"] = atr
    out = _run([pk], {"VOO": {"current": 98.0, "open": 100.0}})
    assert out["entries"] == []
    assert [x["why"] for x in out["excluded"]] == ["stop_unknown"]


def test_a_stock_keeps_its_pick_stop_whatever_the_atr():
    pk = _pick(stop=50.0, atr=2.0)               # no asset_type: a stock
    out = _run([pk], {"AAA": {"current": 90.0, "open": 100.0}})
    (e,) = out["entries"]
    assert e["stop"] == 50.0 and e["stop_basis"] == "pick"
    pk["asset_type"] = "stock"
    assert _run([pk], {"AAA": {"current": 90.0, "open": 100.0}})["entries"][0]["stop"] == 50.0


def test_open_basis_shows_in_the_excluded_log_line():
    band = ATR_STOP_MULT * 2.0
    out = _run([_etf(stop=1.0)], {"VOO": {"current": 100.0 - band - 1, "open": 100.0}})
    assert "from today's open" in ie.excluded_log_line(out)
