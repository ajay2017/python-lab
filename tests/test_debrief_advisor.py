"""
Tests for stock_analyzer/debrief_advisor.py — Portfolio Debrief Advisor (F-3
AI Intelligence Layer), now on the period_attribution engine (C7, 2026-10-20):
the weekly retrospective data-package builder (from the engine's `core`
dict + rec/trade frames), prompt formatting, response parsing and the LLM call.

The numbers come from the REAL engine (`period_inputs.compute_period_core`)
over synthetic books built with `tests.test_period_attribution.world`, so the
package is exercised end-to-end, not against hand-written recon dicts.
`build_debrief_package`'s two enrichment blocks (behavioral fingerprint,
decision quality) locally import their helpers at call time, so they are
monkeypatched directly on `stock_analyzer.behavioral_fingerprint` /
`stock_analyzer.decision_quality`. `generate_debrief`'s real Anthropic call
is exercised via a fake `sys.modules["anthropic"]` module.
"""
import sys
import types
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import db
from stock_analyzer import debrief_advisor as da
from stock_analyzer import market_time
from stock_analyzer import period_attribution as pa
from stock_analyzer import period_inputs as pi
from stock_analyzer import performance_review as prv
from tests.test_performance_review import _base_kwargs as _review_kwargs
from tests.test_period_attribution import FRI, INT_EV, MON, D, week_world, world

pytestmark = pytest.mark.fast


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text=None, raise_exc=None, calls=None):
        self._response_text = response_text
        self._raise_exc = raise_exc
        self._calls = calls

    def create(self, **kwargs):
        if self._calls is not None:
            self._calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text=None, raise_exc=None, calls=None, **kwargs):
        self.messages = _FakeMessages(response_text, raise_exc, calls)


def _install_fake_anthropic(response_text=None, raise_exc=None, calls=None, constructed=None):
    fake_mod = types.ModuleType("anthropic")

    def _ctor(**kwargs):
        if constructed is not None:
            constructed.append(1)
        return _FakeClient(response_text, raise_exc, calls)
    fake_mod.Anthropic = _ctor
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


_RAW = (
    "**What happened**\nFacts text.\n\n"
    "**Decisions you made**\nDecisions text.\n\n"
    "**Patterns this week**\nPatterns text.\n\n"
    "**One thing to watch**\nWatch text."
)


# ─── _pct ──────────────────────────────────────────────────────────────────────

def test_pct_none_returns_na():
    assert da._pct(None) == "N/A"


def test_pct_nan_returns_na():
    assert da._pct(float("nan")) == "N/A"


def test_pct_normal_float_signed_one_decimal():
    assert da._pct(3.14159) == "+3.1%"
    assert da._pct(-2.5) == "-2.5%"


# ─── shape / escape helpers ───────────────────────────────────────────────────

@pytest.mark.parametrize("s0,s1,b,s,expected", [
    (10, 0, 0, 500, "closed"),
    (0, 4, 200, 0, "opened"),
    (0, 0, 200, 180, "round_trip"),
    (0, 0, 0, 0, None),
    (10, 10, 100, 100, "traded"),
    (10, 6, 0, 400, "trimmed"),
    (10, 14, 400, 0, "added"),
    (10, 10, 0, 0, "held"),
    (1e-9, 5, 0, 0, "opened"),     # float noise at the start is not a holding
    (10, 1e-9, 0, 50, "closed"),   # float noise at the end is not a holding
])
def test_shape_classification(s0, s1, b, s, expected):
    assert da.shape(s0, s1, b, s) == expected


def test_escape_md_dollars_escapes_and_is_idempotent():
    assert da.escape_md_dollars("lost $5 and $6") == "lost \\$5 and \\$6"
    once = da.escape_md_dollars("a $5")
    assert da.escape_md_dollars(once) == once
    assert da.escape_md_dollars("already \\$5 and $6") == "already \\$5 and \\$6"
    assert da.escape_md_dollars(None) == ""


def test_method_is_current():
    assert da.method_is_current(2) is True
    assert da.method_is_current(2.0) is True
    assert da.method_is_current(1) is False
    assert da.method_is_current(None) is False
    assert da.method_is_current(float("nan")) is False


# ─── the synthetic week (spec test 1) ─────────────────────────────────────────

SUN = D("2026-10-18")


def _inputs(trades, acct, daily, flows=(), income=()):
    return {
        "trades_df": trades, "account_snapshots_df": acct, "daily_snapshots_df": daily,
        "flows": None if flows is None else list(flows),
        "income_events": None if income is None else list(income),
    }


def _spy(d0, d1, p0=100.0, p1=101.0):
    return {d0: p0, d1: p1}


def _week_core(**kw):
    t, a, dl = week_world()
    inp = _inputs(t, a, dl, flows=[], income=INT_EV)
    for k, v in kw.items():
        inp[k] = v
    spy = {FRI: 100.0, MON: 101.0}
    return pi.compute_period_core(inp, D("2026-10-12"), SUN, spy_prices_by_date=spy,
                                  reference_leverage=2.0), (t, a, dl)


def test_synthetic_week_package():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["metrics_version"] == 2
    assert pkg["narratable"] is True
    assert pkg["d0"] == "2026-10-09" and pkg["d1"] == "2026-10-12"
    assert pkg["d1_short_of_period_end"] is True
    assert pkg["result_status"] == "ok"
    # equity 1000 -> 885, no flows: -11.5 (real value of account_return on these inputs)
    assert pkg["performance_pct"] == pytest.approx(-11.5)
    assert pkg["spy_pct"] == pytest.approx(1.0)
    assert pkg["alpha_pct"] == pytest.approx(-12.5)
    assert pkg["equity_change"] == pytest.approx(-115)
    assert pkg["dates_agree"] is True
    assert pkg["contributors"] == []
    # the four legs: A held -20, B sold -50, C bought -20, D round trip -20
    allrows = {r["ticker"]: r for r in pkg["detractors"]}
    assert allrows["B"]["pnl"] == pytest.approx(-50) and allrows["B"]["shape"] == "closed"
    # D was bought and sold inside the week: a round trip, NOT a closed
    # position (it was never held going into the week)
    assert pkg["closed_positions"] == ["B"]
    assert da.shape(0.0, 0.0, 100.0, 80.0) == "round_trip"
    assert all(r["pnl"] != pytest.approx(180) for r in pkg["detractors"])
    wf = pkg["waterfall"]
    assert wf["held_through"] == pytest.approx(-20) and wf["sold_leg"] == pytest.approx(-50)
    assert wf["bought_leg"] == pytest.approx(-20) and wf["round_trip"] == pytest.approx(-20)
    assert wf["interest"] == pytest.approx(-5)
    assert wf["unexplained"] == pytest.approx(0, abs=1e-9)


def test_synthetic_week_all_four_legs_and_shapes():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    # detractors are capped at 3: A(-20), C(-20), D(-20) tie behind B(-50); ties by ticker
    assert [r["ticker"] for r in pkg["detractors"]] == ["B", "A", "C"]
    shapes = {r["ticker"]: r["shape"] for r in pkg["detractors"]}
    assert shapes == {"B": "closed", "A": "held", "C": "opened"}
    # D is the 4th (round trip); verify it via the numbers-behind helper
    behind = da.numbers_behind(pkg, core)
    legs = {l["ticker"]: l for l in behind["legs"]}
    assert legs["D"]["shape"] == "round_trip" and legs["D"]["total"] == pytest.approx(-20)
    assert legs["A"]["shape"] == "held" and legs["C"]["shape"] == "opened"
    assert behind["legs_match"] is True
    assert behind["legs_sum"] == pytest.approx(behind["pos_total"])


def test_held_name_carries_price_pct_other_shapes_do_not():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    a = next(r for r in pkg["detractors"] if r["ticker"] == "A")
    assert a["price_pct"] == pytest.approx((98 / 100 - 1) * 100)
    assert next(r for r in pkg["detractors"] if r["ticker"] == "B")["price_pct"] is None


# ─── agreement with Reports (spec test 2) ─────────────────────────────────────

def test_package_agrees_with_reports_account_return_and_recon():
    t, a, dl = week_world()
    spy = {FRI: 100.0, MON: 101.0}
    inp = _inputs(t, a, dl, flows=[], income=INT_EV)
    core = pi.compute_period_core(inp, D("2026-10-12"), SUN, spy_prices_by_date=spy,
                                  reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    direct = pa.reconcile_period(
        period_start=D("2026-10-12"), period_end=SUN, trades_df=t, account_snapshots_df=a,
        daily_snapshots_df=dl, flows=[], income_events=INT_EV, historical_closes=None)
    assert core["recon"] == direct
    assert pkg["equity_change"] == direct["equity_change"]
    kw = _review_kwargs(today=D("2026-10-19"), account_snapshots_df=a,
                        account_return_snapshots_df=a, account_flows_rows=[],
                        spy_prices_by_date=spy)
    ar = prv.build_review(period_start=D("2026-10-12"), period_end=SUN, **kw)["account_return"]
    assert ar["status"] == "ok"
    assert pkg["performance_pct"] == ar["return_pct"]
    assert pkg["spy_pct"] == ar["spy_return_pct"]


# ─── sentinels (spec test 3) ──────────────────────────────────────────────────

def _fake_client_never_called():
    constructed, calls = [], []
    _install_fake_anthropic(_RAW, calls=calls, constructed=constructed)
    return constructed, calls


def test_core_none_is_not_narratable_and_generate_never_calls_client():
    constructed, calls = _fake_client_never_called()
    pkg = da.build_debrief_package(SUN, None, None, None)
    assert pkg["narratable"] is False and pkg["result_status"] == "offline"
    assert da.generate_debrief(pkg, api_key="k") is None
    assert not constructed and not calls


def test_trades_none_is_not_narratable_and_generate_never_calls_client():
    constructed, calls = _fake_client_never_called()
    _t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(None, a, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date=None, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, None)
    assert pkg["narratable"] is False
    assert da.generate_debrief(pkg, api_key="k") is None
    assert not constructed and not calls


def test_pre_coverage_week_is_still_narrated_without_a_headline_number():
    t, a, dl = week_world()
    a2 = a[a["snapshot_date"] != FRI.isoformat()]   # no valid start record
    core = pi.compute_period_core(_inputs(t, a2, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date={FRI: 100.0, MON: 101.0}, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["narratable"] is True and pkg["result_status"] == "pre_coverage"
    assert pkg["performance_pct"] is None and pkg["spy_pct"] is None and pkg["alpha_pct"] is None
    assert pkg["result_reason"] == pa.pre_coverage_reason_text(core["recon"])
    assert pkg["equity_change"] is None
    # holdings-only contributors/detractors are still there
    assert pkg["detractors"]
    prompt = da._format_prompt(pkg)
    assert "Account result: unknown" in prompt and pkg["result_reason"] in prompt
    assert "Account change:" not in prompt and "SPY over the same dates" not in prompt


def test_flows_none_makes_account_return_offline_but_contributors_remain():
    t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(t, a, dl, flows=None, income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date={FRI: 100.0, MON: 101.0}, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert core["account_return"]["status"] == "offline"
    assert pkg["performance_pct"] is None
    assert pkg["narratable"] is True and pkg["detractors"]


def test_income_none_makes_interest_unknown():
    t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=None), D("2026-10-12"), SUN,
                                  spy_prices_by_date={FRI: 100.0, MON: 101.0}, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    wf = pkg["waterfall"]
    assert wf["interest"] is None and wf["dividends"] is None and wf["fees"] is None
    assert "Margin interest posted: unknown" in da._format_prompt(pkg)


def test_spy_missing_d1_withholds_spy_and_alpha_and_marks_pending():
    t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date={FRI: 100.0}, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["spy_pct"] is None and pkg["alpha_pct"] is None and pkg["spy_pending"] is True
    assert pkg["performance_pct"] == pytest.approx(-11.5)
    assert "not posted yet" in da._format_prompt(pkg)


def test_dates_disagree_withholds_dollar_change_keeps_percent():
    core, (t, _a, _dl) = _week_core()
    core["account_return"] = dict(core["account_return"], d1=D("2026-10-09"))
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["dates_agree"] is False
    assert pkg["equity_change"] is None and pkg["equity_start"] is None
    assert pkg["performance_pct"] == pytest.approx(-11.5)
    prompt = da._format_prompt(pkg)
    assert "Account change: -11.5%" in prompt and "($" not in prompt.split("Account change:")[1].splitlines()[0]


def test_nan_spy_close_on_d1_reads_as_pending_never_a_return():
    """A NaN close is dropped by spy_closes_by_date, so d1 is missing from the SPY map and the
    package shows SPY as not posted yet -- never a NaN return or a stale-reuse +0.00%."""
    t, a, dl = week_world()
    hist = pd.DataFrame({"Close": [100.0, float("nan")]}, index=pd.to_datetime(["2026-10-09", "2026-10-12"]))
    spy = pi.spy_closes_by_date(hist)
    assert spy == {FRI: 100.0}
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date=spy, reference_leverage=2.0)
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["spy_pct"] is None and pkg["alpha_pct"] is None and pkg["spy_pending"] is True


def test_pre_coverage_secondary_figure_is_never_promoted_to_the_headline():
    t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date={FRI: 100.0, MON: 101.0}, reference_leverage=2.0)
    core["recon"] = dict(core["recon"], status="pre_coverage", pre_coverage_reason="no_start_record")
    core["account_return"] = {"status": "pre_coverage", "earliest_valid_date": D("2026-10-01"),
                              "secondary": {"d0": D("2026-10-01"), "d1": D("2026-10-12"),
                                            "return_pct": 7.7, "spy_return_pct": 3.3,
                                            "max_leverage": 2.0, "spy_d1_price_pending": False}}
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["performance_pct"] is None and pkg["spy_pct"] is None and pkg["alpha_pct"] is None
    assert pkg["max_leverage"] is None and pkg["return_d0"] is None
    prompt = da._format_prompt(pkg)
    assert "7.7" not in prompt and "3.3" not in prompt
    assert "Position P&L (account result unknown):" in prompt
    assert "Where the account change came from" not in prompt


def test_known_result_keeps_the_waterfall_title_and_the_returns_own_dates():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    prompt = da._format_prompt(pkg)
    assert "Where the account change came from:" in prompt
    assert pkg["return_d0"] == "2026-10-09" and pkg["return_d1"] == "2026-10-12"


def test_percent_label_uses_account_returns_dates_when_they_differ_from_the_recon():
    core, (t, _a, _dl) = _week_core()
    core["account_return"] = dict(core["account_return"], d0=D("2026-10-02"), d1=D("2026-10-09"))
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["dates_agree"] is False
    prompt = da._format_prompt(pkg)
    assert "Account change measured 2026-10-02 close to 2026-10-09 close" in prompt


def test_leverage_line_uses_reports_endpoint_figure_and_only_above_one_x():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert pkg["max_leverage"] == core["account_return"]["max_leverage"]
    assert f"Leverage: up to {pkg['max_leverage']:.1f}x at the week's endpoints" in da._format_prompt(pkg)
    pkg["max_leverage"] = 1.0
    assert "Leverage:" not in da._format_prompt(pkg)


# ─── unknown-result reason branches / signal window ───────────────────────────

def test_unknown_reason_flows_unreadable():
    t, a, dl = week_world()
    core = pi.compute_period_core(_inputs(t, a, dl, flows=None, income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date=None, reference_leverage=2.0)
    assert core["recon"]["status"] != "pre_coverage"
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert "deposit/withdrawal history couldn't be read" in pkg["result_reason"]
    assert "deposit/withdrawal history couldn't be read" in da._format_prompt(pkg)


def test_unknown_reason_pre_coverage_recon_uses_the_engine_text():
    r = {"status": "pre_coverage", "pre_coverage_reason": "no_start_record", "d0": D("2026-10-09")}
    assert da.unknown_result_reason(r, {"status": "pre_coverage"}) == pa.pre_coverage_reason_text(r)


def test_unknown_reason_account_return_uncomputable_while_recon_ok():
    msg = da.unknown_result_reason({"status": "ok"}, {"status": "pre_coverage", "secondary": None})
    assert "return couldn't be computed" in msg


def test_unknown_reason_generic_fallback_last():
    assert "couldn't be measured" in da.unknown_result_reason({"status": "ok"}, {"status": "ok"})
    assert "couldn't be measured" in da.unknown_result_reason(None, None)


def test_unknown_reason_offline_takes_precedence_over_pre_coverage_recon():
    r = {"status": "pre_coverage", "pre_coverage_reason": "no_start_record"}
    assert "deposit/withdrawal" in da.unknown_result_reason(r, {"status": "offline"})


def test_signal_days_back_reaches_the_weeks_first_day():
    ws = D("2026-07-20")
    assert da.signal_days_back(D("2026-07-26"), ws) == 7     # Sunday run
    assert da.signal_days_back(D("2026-07-24"), ws) == 5     # Friday click
    assert da.signal_days_back(D("2026-08-01"), ws) == 13    # Saturday retry a week later
    assert da.signal_days_back(D("2026-07-01"), ws) == 1     # never below 1


# ─── unpriced / journal-inconsistent names (spec test 4) ──────────────────────

def test_unmeasured_name_with_a_partial_nonzero_total_is_never_ranked():
    """AAA loses its close on ONE interior day, so the engine leaves that interval out
    (status partial, unpriced) but AAA still carries a partial NON-ZERO per_ticker total.
    Only the `tk in unmeasured` guard keeps it out of the ranked lists -- a vacuous
    fixture (no per_ticker entry, or a zero total) would pass without it."""
    t, a, dl = _generic_world()
    dl2 = dl[~((dl["ticker"] == "AAA") & (dl["snapshot_date"] == "2026-07-22"))]
    spy = {d: 100.0 + i for i, d in enumerate(_DATES)}
    core = pi.compute_period_core(_inputs(t, a, dl2, flows=[], income=[]), D("2026-07-20"),
                                  WEEK_ENDING, spy_prices_by_date=spy, reference_leverage=2.0)
    assert core["recon"]["status"] == "partial"
    assert ("AAA", D("2026-07-22")) in core["recon"]["unpriced"]
    assert abs(core["recon"]["per_ticker"]["AAA"]["total"]) > 1   # a real partial P&L, not 0
    pkg = da.build_debrief_package(WEEK_ENDING, core, None, t)
    assert "AAA" in pkg["unmeasured"]
    assert "AAA" not in [r["ticker"] for r in pkg["contributors"] + pkg["detractors"]]
    # the other legs are untouched (DDD +50 still leads)
    assert pkg["contributors"][0]["ticker"] == "DDD"
    assert "Not measured (P&L not included above): AAA" in da._format_prompt(pkg)


def test_journal_inconsistent_names_are_unmeasured():
    t, a, dl = week_world()
    bad = pd.concat([t, pd.DataFrame([{
        "id": 99, "ticker": "Z", "action": "SELL", "shares": 5, "price": 10,
        "traded_at": "2026-10-12T19:00:00+00:00"}])], ignore_index=True)   # oversell, never bought
    core = pi.compute_period_core(_inputs(bad, a, dl, flows=[], income=INT_EV), D("2026-10-12"), SUN,
                                  spy_prices_by_date=None, reference_leverage=2.0)
    assert core["recon"]["journal_inconsistent"]
    pkg = da.build_debrief_package(SUN, core, None, bad)
    assert "Z" in pkg["unmeasured"]
    assert "Z" not in [r["ticker"] for r in pkg["contributors"] + pkg["detractors"]]


# ─── generic week fixture: contributors / detractors / ties (spec test 5) ─────

WEEK_ENDING = date(2026, 7, 26)  # Mon 07-20 .. Sun 07-26; d0 = Fri 07-17
_DATES = [D("2026-07-17"), D("2026-07-20"), D("2026-07-21"), D("2026-07-22"),
          D("2026-07-23"), D("2026-07-24")]


def _generic_closes():
    path = {
        "AAA": [100, 101, 102, 103, 104, 105],      # +50
        "DDD": [50, 51, 52, 53, 54, 55],            # +50  (ties AAA)
        "FFF": [50, 50.4, 50.8, 51.2, 51.6, 52],    # +20
        "GGG": [50, 50.1, 50.2, 50.3, 50.4, 50.5],  # +5
        "BBB": [200, 196, 192, 188, 184, 180],      # -100
        "EEE": [50, 49.4, 48.8, 48.2, 47.6, 47],    # -30
        "HHH": [50, 49.8, 49.6, 49.4, 49.2, 49],    # -10
        "III": [50, 49.9, 49.8, 49.7, 49.6, 49.5],  # -5
        "CCC": [30, 30, 30, 30, 30, 30],
    }
    closes = {}
    for i, d in enumerate(_DATES):
        closes[d] = {t: v[i] for t, v in path.items()}
    # CCC is sold on 07-22 -> not held (no close needed) from 07-22 on
    for d in _DATES[3:]:
        del closes[d]["CCC"]
    return closes


def _generic_world():
    init = [("AAA", 10, 100), ("DDD", 10, 50), ("FFF", 10, 50), ("GGG", 10, 50), ("BBB", 5, 200),
            ("EEE", 10, 50), ("HHH", 10, 50), ("III", 10, 50), ("CCC", 8, 30)]
    steps = {_DATES[0]: {"cash": 3000.0},
             D("2026-07-22"): {"trades": [("CCC", "SELL", 8, 31)]}}
    return world(init, steps, _generic_closes(), init_date=D("2026-07-01"))


def _generic_core(flows=(), income=()):
    t, a, dl = _generic_world()
    spy = {d: 100.0 + i for i, d in enumerate(_DATES)}
    core = pi.compute_period_core(_inputs(t, a, dl, flows=flows, income=income),
                                  D("2026-07-20"), WEEK_ENDING, spy_prices_by_date=spy,
                                  reference_leverage=2.0)
    return core, t


def _generic_pkg(recs_df=None, trades_extra=None, **kw):
    core, t = _generic_core()
    tr = t if trades_extra is None else pd.concat([t, pd.DataFrame(trades_extra)], ignore_index=True)
    return da.build_debrief_package(WEEK_ENDING, core, recs_df, tr, **kw)


def test_generic_week_is_clean_ok():
    core, _t = _generic_core()
    assert core["recon"]["status"] == "ok" and core["recon"]["d0"] == date(2026, 7, 17)
    assert core["recon"]["d1"] == date(2026, 7, 24)


def test_contributors_sorted_descending_capped_at_3_with_deterministic_tie_break():
    pkg = _generic_pkg()
    # AAA and DDD both +50 -> alphabetical; then FFF +20; CCC (+8) / GGG (+5) fall off the cap
    assert [c["ticker"] for c in pkg["contributors"]] == ["AAA", "DDD", "FFF"]


def test_tie_break_is_stable_across_repeated_builds():
    first = [c["ticker"] for c in _generic_pkg()["contributors"]]
    for _ in range(3):
        assert [c["ticker"] for c in _generic_pkg()["contributors"]] == first


def test_detractors_sorted_ascending_most_negative_first_capped_at_3():
    pkg = _generic_pkg()
    assert [d["ticker"] for d in pkg["detractors"]] == ["BBB", "EEE", "HHH"]


def test_closed_position_is_listed_and_may_appear_with_its_real_move():
    pkg = _generic_pkg()
    assert pkg["closed_positions"] == ["CCC"]
    # CCC's real +$8 sold leg is a real number, not a -100% artifact; it just didn't make the top 3
    behind = da.numbers_behind(pkg, _generic_core()[0])
    ccc = next(l for l in behind["legs"] if l["ticker"] == "CCC")
    assert ccc["shape"] == "closed" and ccc["total"] == pytest.approx(8)


# ─── recs (ported) ────────────────────────────────────────────────────────────

def test_recs_surfaced_dedup_times_surfaced_and_dominant_verdict():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "AAA", "rec_date": "2026-07-22", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "AAA", "rec_date": "2026-07-23", "rec_type": "new_pick", "verdict": "Weakening"},
    ])
    package = _generic_pkg(recs_df)
    entry = next(r for r in package["recs_surfaced"] if r["ticker"] == "AAA")
    assert entry["times_surfaced"] == 3
    assert entry["verdict"] == "Confirmed"


def test_recs_surfaced_acted_flag_from_trades():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "DDD", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
    ])
    package = _generic_pkg(recs_df, trades_extra=[
        {"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T14:00:00+00:00"}])
    aaa = next(r for r in package["recs_surfaced"] if r["ticker"] == "AAA")
    ddd = next(r for r in package["recs_surfaced"] if r["ticker"] == "DDD")
    assert aaa["acted"] is True
    assert ddd["acted"] is False
    assert [r["ticker"] for r in package["signals_ignored"]] == ["DDD"]


def test_recs_surfaced_acted_excludes_fill_before_week_start_et():
    """A fill whose raw UTC timestamp date-slices to week_start's own date but whose
    ET wall-clock time is the PRIOR evening (Sunday 23:30 ET) must NOT count."""
    recs_df = pd.DataFrame([
        {"ticker": "DDD", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
    ])
    package = _generic_pkg(recs_df, trades_extra=[
        {"ticker": "DDD", "action": "BUY", "traded_at": "2026-07-20T03:30:00+00:00"}])
    ddd = next(r for r in package["recs_surfaced"] if r["ticker"] == "DDD")
    assert ddd["acted"] is False


def test_end_week_pct_is_the_price_move_and_none_for_closed_position():
    recs_df = pd.DataFrame([
        {"ticker": "CCC", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "ZZZ", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
    ])
    package = _generic_pkg(recs_df)
    by = {r["ticker"]: r for r in package["recs_surfaced"]}
    assert by["CCC"]["end_week_pct"] is None            # closed: never a -100% artifact
    assert by["AAA"]["end_week_pct"] == pytest.approx(5.0)   # 100 -> 105 price, not position value
    assert by["ZZZ"]["end_week_pct"] is None            # not held, not traded: unknown, never 0


def test_rec_type_filtered_to_new_pick_and_add_winner():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "DDD", "rec_date": "2026-07-20", "rec_type": "buy_candidate", "verdict": "Confirmed"},
    ])
    package = _generic_pkg(recs_df)
    tickers = [r["ticker"] for r in package["recs_surfaced"]]
    assert "AAA" in tickers
    assert "DDD" not in tickers


# ─── protective_signals (WATCH/TRIM/EXIT) ─────────────────────────────────────

def _exit_sig(ticker, signal_date, signal_type, pnl_pct=-5.0, dd_from_peak_pct=-8.0):
    return {"ticker": ticker, "signal_date": signal_date, "signal_type": signal_type,
            "pnl_pct": pnl_pct, "dd_from_peak_pct": dd_from_peak_pct}


def test_protective_signals_escalates_to_most_severe_tier_seen():
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-20", "WATCH"),
        _exit_sig("AAA", "2026-07-22", "EXIT"),
    ])
    package = _generic_pkg(exit_signals_df=sig_df)
    entry = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    assert entry["tier"] == "EXIT"
    assert entry["times_surfaced"] == 2


def test_protective_signals_does_not_downgrade_from_a_later_lower_tier():
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-20", "EXIT"),
        _exit_sig("AAA", "2026-07-22", "WATCH"),
    ])
    package = _generic_pkg(exit_signals_df=sig_df)
    entry = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    assert entry["tier"] == "EXIT"


def test_protective_signals_excludes_a_closed_position():
    sig_df = pd.DataFrame([_exit_sig("CCC", "2026-07-21", "EXIT")])
    package = _generic_pkg(exit_signals_df=sig_df)
    assert "CCC" in package["closed_positions"]
    assert "CCC" not in [s["ticker"] for s in package["protective_signals"]]


def test_protective_signals_sold_flag_true_when_sell_trade_coincides():
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-21", "TRIM"),
        _exit_sig("DDD", "2026-07-21", "TRIM"),
    ])
    package = _generic_pkg(exit_signals_df=sig_df, trades_extra=[
        {"ticker": "AAA", "action": "SELL", "traded_at": "2026-07-21T14:00:00+00:00"}])
    aaa = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    ddd = next(s for s in package["protective_signals"] if s["ticker"] == "DDD")
    assert aaa["sold"] is True
    assert ddd["sold"] is False


def test_protective_signals_sold_flag_true_for_late_sunday_et_fill():
    """A SELL executed 23:30 ET on the week's LAST calendar day (Sun 07-26)
    serializes to UTC as 2026-07-27T03:30 -- still this week in ET."""
    sig_df = pd.DataFrame([_exit_sig("AAA", "2026-07-21", "TRIM")])
    package = _generic_pkg(exit_signals_df=sig_df, trades_extra=[
        {"ticker": "AAA", "action": "SELL", "traded_at": "2026-07-27T03:30:00+00:00"}])
    aaa = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    assert aaa["sold"] is True


def test_protective_signals_empty_when_none_or_outside_window():
    assert _generic_pkg(exit_signals_df=None)["protective_signals"] == []
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-21", "HOLD"),
        _exit_sig("DDD", "2026-06-01", "EXIT"),
    ])
    assert _generic_pkg(exit_signals_df=sig_df)["protective_signals"] == []


# ─── behavioral / decision-quality enrichment ─────────────────────────────────

def test_behavioral_enrichment_populates_package(monkeypatch):
    fake_momentum = {"high": {"n": 10, "n_acted": 5, "action_rate": 50.0},
                     "low": {"n": 10, "n_acted": 2, "action_rate": 20.0},
                     "delta_pp": 30.0, "direction": "chases"}
    fake_conviction = {"strong_buy": {"n": 10, "n_acted": 8, "action_rate": 80.0},
                       "buy": {"n": 10, "n_acted": 3, "action_rate": 30.0},
                       "delta_pp": 50.0}
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.momentum_recency_pattern",
                        lambda matched, min_n: fake_momentum)
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.conviction_tier_pattern",
                        lambda matched, strong_buy_floor, min_n: fake_conviction)
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_type": "new_pick", "momentum_score": 80.0, "composite_score": 78.0},
    ])
    package = _generic_pkg(recs_df, trades_extra=[
        {"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T14:00:00+00:00"}],
        all_recs_df=recs_df)
    assert package["behavioral"]["momentum"] == fake_momentum
    assert package["behavioral"]["conviction"] == fake_conviction


def test_behavioral_enrichment_exception_caught_stays_default(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.momentum_recency_pattern", _boom)
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_type": "new_pick", "momentum_score": 80.0, "composite_score": 78.0},
    ])
    package = _generic_pkg(recs_df, trades_extra=[
        {"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T14:00:00+00:00"}],
        all_recs_df=recs_df)
    assert package["behavioral"] == {}


def test_decision_quality_populates_current_month(monkeypatch):
    cur_month = str(WEEK_ENDING)[:7]
    fake_grades = [{
        "month_str": cur_month, "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    }]
    monkeypatch.setattr("stock_analyzer.decision_quality.build_monthly_grades",
                        lambda trades_df, spy_monthly_returns=None: fake_grades)
    package = _generic_pkg()
    assert package["decision_quality_month"] == {
        "month_str": cur_month, "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    }


def test_decision_quality_exception_caught_stays_none(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("stock_analyzer.decision_quality.build_monthly_grades", _boom)
    assert _generic_pkg()["decision_quality_month"] is None


# ─── holiday weeks / week anchoring (spec tests 6, 7) ─────────────────────────

def test_thanksgiving_week_four_sessions_is_narratable():
    d0, mon, tue, wed, fri = D("2026-11-20"), D("2026-11-23"), D("2026-11-24"), D("2026-11-25"), D("2026-11-27")
    closes = {d: {"A": 100 + i} for i, d in enumerate([d0, mon, tue, wed, fri])}
    t, a, dl = world([("A", 10, 100)], {d0: {"cash": 500.0}}, closes, init_date=D("2026-11-02"))
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=[]), D("2026-11-23"), D("2026-11-29"),
                                  spy_prices_by_date={d: 400.0 + i for i, d in enumerate([d0, mon, tue, wed, fri])},
                                  reference_leverage=2.0)
    pkg = da.build_debrief_package(D("2026-11-29"), core, None, t)
    assert core["recon"]["coverage"]["trading_days"] == 4
    assert pkg["narratable"] is True and pkg["result_status"] == "ok"
    assert pkg["d0"] == "2026-11-20" and pkg["d1"] == "2026-11-27"
    assert pkg["d1_short_of_period_end"] is False
    assert pkg["performance_pct"] is not None


def test_labor_day_week_starts_from_the_prior_friday_close():
    fri_before, tue, wed = D("2026-09-04"), D("2026-09-08"), D("2026-09-09")
    closes = {d: {"A": 100 + i} for i, d in enumerate([fri_before, tue, wed])}
    t, a, dl = world([("A", 10, 100)], {fri_before: {"cash": 500.0}}, closes, init_date=D("2026-08-03"))
    core = pi.compute_period_core(_inputs(t, a, dl, flows=[], income=[]), D("2026-09-07"), D("2026-09-13"),
                                  spy_prices_by_date=None, reference_leverage=2.0)
    pkg = da.build_debrief_package(D("2026-09-13"), core, None, t)
    assert pkg["d0"] == "2026-09-04"
    assert pkg["narratable"] is True


def test_resolve_period_week_matches_most_recent_sunday_every_day():
    start = date(2026, 10, 5)
    for i in range(14):
        d = start + timedelta(days=i)
        ps, pe = pa.resolve_period("last_week", d)
        assert pe == market_time.most_recent_sunday(d)
        assert ps == pe - timedelta(days=6)
        pkg = da.build_debrief_package(pe, None, None, None)
        assert pkg["week_ending"] == str(pe) and pkg["week_start"] == str(ps)


# ─── prompt (spec test 8) ─────────────────────────────────────────────────────

def test_v2_prompts_no_legacy_framing():
    pkg = _generic_pkg()
    text = da._format_prompt(pkg)
    for banned in ("equity positions", "Mon–Fri", "these already exclude closed positions"):
        assert banned not in text
        assert banned not in da._SYSTEM_PROMPT
    assert "Positions fully closed (sold) this week: CCC" in text
    assert "NEVER as a price loss" in da._SYSTEM_PROMPT and "−100%" in da._SYSTEM_PROMPT
    assert "never estimate" in da._SYSTEM_PROMPT
    assert "Do not speculate about the unexplained remainder" in da._SYSTEM_PROMPT


def test_prompt_states_the_account_result_with_dates_and_leverage():
    core, (t, _a, _dl) = _week_core()
    text = da._format_prompt(da.build_debrief_package(SUN, core, None, t))
    assert "Account change measured 2026-10-09 close to 2026-10-12 close" in text
    assert "Account change: -11.5% (-$115)" in text
    assert "SPY over the same dates: +1.0%" in text
    assert "Account vs SPY: -12.5 percentage points" in text
    assert "Margin interest posted: -$5" in text
    assert "latest account record in the week is 2026-10-12" in text


def test_prompt_lists_interest_gap_months(monkeypatch):
    core, (t, _a, _dl) = _week_core()
    core["recon"]["income"]["interest_coverage_gap_months"] = ["2026-09"]
    text = da._format_prompt(da.build_debrief_package(SUN, core, None, t))
    assert "Margin interest not recorded for: 2026-09" in text


def test_generate_debrief_round_trip_is_stamped_v2_and_save_ready():
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    _install_fake_anthropic(_RAW)
    result = da.generate_debrief(pkg, api_key="fake-key")
    assert result["metrics_version"] == 2
    assert result["week_ending"] == str(SUN)
    assert result["performance_pct"] == pytest.approx(-11.5)
    assert result["section_facts"] == "Facts text." and result["email_sent"] is False
    assert set(result) <= set(db._WEEKLY_DEBRIEF_COLS)


# ─── _format_prompt blocks carried over (handwritten packages) ────────────────

def _base_package(**overrides):
    package = {
        "week_start": "2026-07-20", "week_ending": "2026-07-26",
        "d0": "2026-07-17", "d1": "2026-07-24",
        "performance_pct": None, "spy_pct": None, "alpha_pct": None,
        "contributors": [], "detractors": [],
        "recs_surfaced": [], "closed_positions": [], "broken_theses": [],
        "behavioral": {}, "decision_quality_month": None,
    }
    package.update(overrides)
    return package


def test_format_prompt_unknown_result_fallback_line():
    text = da._format_prompt(_base_package())
    assert "Account result: unknown" in text


def test_format_prompt_contributors_detractors_format_dollars_shape_and_pct():
    package = _base_package(
        contributors=[{"ticker": "AAA", "pnl": 100.0, "shape": "held", "price_pct": 5.0}],
        detractors=[{"ticker": "BBB", "pnl": -50.0, "shape": "closed", "price_pct": None}],
    )
    text = da._format_prompt(package)
    assert "AAA: +$100 (held all week; price +5.0%)" in text
    assert "BBB: -$50 (position sold this week — the move up to the sale)" in text


def test_format_prompt_recs_surfaced_times_suffix_only_when_greater_than_1():
    package = _base_package(recs_surfaced=[
        {"ticker": "AAA", "rec_type": "new_pick", "acted": True, "verdict": "Confirmed",
         "times_surfaced": 1, "end_week_pct": None},
        {"ticker": "BBB", "rec_type": "new_pick", "acted": False, "verdict": "Confirmed",
         "times_surfaced": 3, "end_week_pct": None},
    ])
    text = da._format_prompt(package)
    assert "AAA" in text and "surfaced" not in text.split("BBB")[0].split("AAA")[-1]
    assert "(surfaced 3× this week)" in text


def test_format_prompt_closed_positions_note_only_when_nonempty():
    assert "fully closed" not in da._format_prompt(_base_package())
    text_present = da._format_prompt(_base_package(closed_positions=["CCC"]))
    assert "Positions fully closed (sold) this week: CCC" in text_present


def test_format_prompt_broken_theses_line_only_when_nonempty():
    assert "BROKEN thesis" not in da._format_prompt(_base_package())
    assert "Positions with BROKEN thesis: XYZ" in da._format_prompt(_base_package(broken_theses=["XYZ"]))


def test_format_prompt_momentum_block_only_when_truthy():
    assert "momentum tendency" not in da._format_prompt(_base_package())
    package = _base_package(behavioral={"momentum": {
        "high": {"n": 10, "n_acted": 5, "action_rate": 50.0},
        "low": {"n": 10, "n_acted": 2, "action_rate": 20.0},
        "delta_pp": 30.0, "direction": "chases",
    }})
    text_present = da._format_prompt(package)
    assert "momentum tendency" in text_present
    assert "High-momentum signals: 10 surfaced, 5 acted on (50% rate)" in text_present


def test_format_prompt_conviction_block_only_when_truthy():
    assert "conviction tier" not in da._format_prompt(_base_package())
    package = _base_package(behavioral={"conviction": {
        "strong_buy": {"n": 10, "n_acted": 8, "action_rate": 80.0},
        "buy": {"n": 10, "n_acted": 3, "action_rate": 30.0},
        "delta_pp": 50.0,
    }})
    text_present = da._format_prompt(package)
    assert "conviction tier" in text_present
    assert "Strong Buy signals: 10 surfaced, 8 acted on (80% rate)" in text_present


def test_format_prompt_decision_quality_block_only_when_truthy():
    assert "Decision quality" not in da._format_prompt(_base_package())
    package = _base_package(decision_quality_month={
        "month_str": "2026-07", "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    })
    text_present = da._format_prompt(package)
    assert "Decision quality — 2026-07 month to date (8 trades):" in text_present
    assert "Grade: B (composite 72/100)" in text_present
    assert "Win rate: 60%" in text_present
    assert "Profit factor: 1.80" in text_present


# ─── _parse_response ──────────────────────────────────────────────────────────

def test_parse_response_all_4_sections_split_correctly():
    sections = da._parse_response(_RAW)
    assert sections["section_facts"] == "Facts text."
    assert sections["section_decisions"] == "Decisions text."
    assert sections["section_patterns"] == "Patterns text."
    assert sections["section_watchnext"] == "Watch text."


def test_parse_response_missing_section_stays_empty():
    sections = da._parse_response("**What happened**\nOnly this section.")
    assert sections["section_facts"] == "Only this section."
    assert sections["section_decisions"] == ""
    assert sections["section_patterns"] == ""
    assert sections["section_watchnext"] == ""


def test_parse_response_discards_text_before_first_header():
    sections = da._parse_response("Preamble that should be discarded.\n**What happened**\nReal text.")
    assert "Preamble" not in sections["section_facts"]
    assert sections["section_facts"] == "Real text."


# ─── generate_debrief guards ──────────────────────────────────────────────────

def test_generate_debrief_no_api_key_returns_none():
    assert da.generate_debrief({"narratable": True}, api_key="") is None


def test_generate_debrief_not_narratable_returns_none():
    assert da.generate_debrief({"narratable": False}, api_key="fake-key") is None
    assert da.generate_debrief({}, api_key="fake-key") is None


def test_generate_debrief_llm_failure_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("down"))
    core, (t, _a, _dl) = _week_core()
    pkg = da.build_debrief_package(SUN, core, None, t)
    assert da.generate_debrief(pkg, api_key="fake-key") is None


def test_classify_snapshot_read_is_retired():
    assert not hasattr(da, "classify_snapshot_read")
