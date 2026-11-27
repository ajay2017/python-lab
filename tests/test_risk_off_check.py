"""RiskListwiseYoung C4 (RiskOffUnchecked): stock_analyzer/risk_off_check.derisk_unchecked,
its wiring into the Brief / headless engine / premarket lane / Home, and the
risk_window outage wording. Spec: docs/plans/risk-off-unchecked-c4-spec.md.
Clock-free: every input is a frame or a number.
"""
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import risk_off_check
from stock_analyzer.constants import (
    BETA_MIN_RETURNS,
    PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
    RISK_OFF_TREND_MA,
    RISK_OFF_VIX_LEVEL,
)
from stock_analyzer.risk_off_check import derisk_unchecked

pytestmark = pytest.mark.fast

_ROOT = Path(__file__).resolve().parent.parent


def _spy(last_below_average: bool) -> pd.DataFrame:
    """RISK_OFF_TREND_MA bars; the last close sits below (True) or above (False)
    the window's average."""
    closes = [100.0] * (RISK_OFF_TREND_MA - 1) + [50.0 if last_below_average else 150.0]
    return pd.DataFrame({"Close": closes})


_CALM_VIX = RISK_OFF_VIX_LEVEL - 5.0
_HIGH_VIX = RISK_OFF_VIX_LEVEL + 5.0


def _cov(state="unmeasured", unmeasured_pct=15.0, reason="short_history"):
    return {"state": state,
            "left_out": [{"ticker": "ANT", "reason": reason, "n_returns": 3,
                          "weight_pct": unmeasured_pct}],
            "window_returns": 0, "min_returns": BETA_MIN_RETURNS,
            "max_unmeasured_pct": PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
            "unmeasured_pct": unmeasured_pct, "late_start": []}


_SHORT = pd.DataFrame({"Close": [100.0] * (RISK_OFF_TREND_MA - 1)})

# regime name -> (spy_trend_df, vix_level, expected regime_read state)
_REGIMES = {
    "tripped_trend":        (_spy(True), _CALM_VIX, "risk_off"),
    "tripped_vix":          (_spy(False), _HIGH_VIX, "risk_off"),
    "tripped_both":         (_spy(True), _HIGH_VIX, "risk_off"),
    "tripped_trend_vix_unread": (_spy(True), None, "risk_off"),
    "tripped_vix_trend_unread": (None, _HIGH_VIX, "risk_off"),
    "calm":                 (_spy(False), _CALM_VIX, "calm"),
    "vix_unread":           (_spy(False), None, "partial"),
    "trend_unread_no_data": (None, _CALM_VIX, "partial"),
    "trend_unread_short":   (_SHORT, _CALM_VIX, "partial"),
    "both_unread_no_data":  (None, None, "unread"),
    "both_unread_short":    (_SHORT, None, "unread"),
}
_TRIPPED = [k for k, v in _REGIMES.items() if v[2] == "risk_off"]
_UNREAD_STATES = [k for k, v in _REGIMES.items() if v[2] in ("partial", "unread")]
_UNMEASURED_BOOKS = [None, {}, {"severity": "ok"}, {"severity": None}, "junk", 5]


def _d(fragility, regime, **kw):
    spy, vix, _ = _REGIMES[regime]
    return derisk_unchecked(fragility, spy, vix, **kw)


# ── the decision (plan docs/plans/sell-check-unread-3a-3b.md §2 table) ────────
@pytest.mark.parametrize("regime", list(_REGIMES))
def test_a_calm_book_can_never_arm_so_none_for_every_regime_state(regime):
    assert _d({"severity": "calm"}, regime) is None


@pytest.mark.parametrize("sev", ["caution", "fragile"])
@pytest.mark.parametrize("regime", list(_REGIMES))
def test_an_armable_book_is_none_when_the_regime_is_read_else_regime_unread(sev, regime):
    r = _d({"severity": sev}, regime)
    if _REGIMES[regime][2] in ("risk_off", "calm"):
        assert r is None            # the check ran (or the regime was read as calm)
    else:
        assert r["kind"] == "regime_unread"
        assert r["reasons"] == []   # no leg tripped on an unread read


@pytest.mark.parametrize("fr", _UNMEASURED_BOOKS, ids=repr)
@pytest.mark.parametrize("regime", list(_REGIMES))
def test_an_unmeasured_book_row_of_the_table(fr, regime):
    # Owner decision U-C 2026-11-27: this REVERSES the old pins "{} / ok is a dict
    # so None" and "regime inputs missing -> None". A dict with no recognised
    # severity is treated like None (assess_risk_off_derisk can't arm on it).
    r = _d(fr, regime)
    state = _REGIMES[regime][2]
    if state == "risk_off":
        assert r["kind"] == "beta_unmeasured" and r["reasons"]
    elif state == "calm":
        assert r is None
    else:
        assert r["kind"] == "both_unread" and r["reasons"] == []


def test_none_fragility_armed_by_the_trend_leg():
    r = derisk_unchecked(None, _spy(True), _CALM_VIX)
    assert r is not None and len(r["reasons"]) == 1
    assert "downtrend" in r["reasons"][0]
    assert r["text"].startswith("Risk-off de-risk check couldn't run: the market is in a risk-off regime (")
    assert r["reasons"][0] in r["text"]
    assert "no de-risk trim was considered today" in r["text"]


def test_none_fragility_armed_by_the_vix_leg():
    r = derisk_unchecked(None, _spy(False), _HIGH_VIX)
    assert r is not None and len(r["reasons"]) == 1 and "VIX" in r["reasons"][0]


def test_none_fragility_both_legs_join_with_semicolon():
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX)
    assert len(r["reasons"]) == 2
    assert "; ".join(r["reasons"]) in r["text"]


def test_regime_not_armed_is_none():
    assert derisk_unchecked(None, _spy(False), _CALM_VIX) is None


def test_vix_exactly_at_the_threshold_arms_it():
    # the regime's own boundary (>=), not re-decided here
    assert derisk_unchecked(None, _spy(False), RISK_OFF_VIX_LEVEL) is not None


def test_missing_regime_inputs_with_an_unmeasured_book_are_both_unread_not_none():
    # FLIPPED by owner decision U-C 2026-11-27 (was: "degrade to not armed so None",
    # docs/plans/risk-off-unchecked-c4-spec.md ~20). Arming is unchanged; only the
    # disclosure is new.
    r = derisk_unchecked(None, None, None)
    assert r["kind"] == "both_unread" and r["reasons"] == []


def test_short_spy_history_and_no_vix_with_an_unmeasured_book_is_both_unread():
    # FLIPPED by owner decision U-C 2026-11-27 (was None).
    r = derisk_unchecked(None, _SHORT, None)
    assert r["kind"] == "both_unread"


def test_beta_unmeasured_text_is_byte_identical_to_the_pre_change_string():
    r = derisk_unchecked(None, _spy(True), _CALM_VIX)
    assert r == {
        "kind": "beta_unmeasured",
        "reasons": [f"S&P 500 is below its {RISK_OFF_TREND_MA}-day average (downtrend)"],
        "text": (
            "Risk-off de-risk check couldn't run: the market is in a risk-off regime ("
            f"S&P 500 is below its {RISK_OFF_TREND_MA}-day average (downtrend)), but your "
            "portfolio beta couldn't be measured, so no de-risk trim was considered "
            "today. Review your highest-beta holdings yourself before adding risk."),
    }
    both = derisk_unchecked(None, _spy(True), _HIGH_VIX)
    assert both["text"].startswith(
        "Risk-off de-risk check couldn't run: the market is in a risk-off regime ("
        f"S&P 500 is below its {RISK_OFF_TREND_MA}-day average (downtrend); VIX ")
    assert "; ".join(both["reasons"]) in both["text"]


@pytest.mark.parametrize("fr", _UNMEASURED_BOOKS, ids=repr)
@pytest.mark.parametrize("regime", _TRIPPED)
def test_beta_unmeasured_text_does_not_depend_on_which_unmeasured_shape(fr, regime):
    assert _d(fr, regime)["text"] == _d(None, regime)["text"]


# ── regime_unread / both_unread texts (plan §6) ───────────────────────────────
_TAIL = " Review your highest-beta holdings yourself before adding risk."
_BOOK = {"caution": "more volatile than the market",
         "fragile": "would fall outsized in a routine pullback"}
_AVG = f"{RISK_OFF_TREND_MA}-day"
_THR = f"{RISK_OFF_VIX_LEVEL:g}"


@pytest.mark.parametrize("sev", ["caution", "fragile"])
def test_regime_unread_text_vix_unread(sev):
    r = _d({"severity": sev}, "vix_unread")
    assert r["text"] == (
        "Risk-off de-risk check couldn't fully run: VIX couldn't be read today, so "
        f"the market regime is only partly known (the S&P 500 is not below its {_AVG} "
        f"average). Your book is {_BOOK[sev]}, so a VIX reading of {_THR} or more "
        "would have armed a de-risk trim." + _TAIL)


@pytest.mark.parametrize("sev", ["caution", "fragile"])
def test_regime_unread_text_trend_unread_no_data(sev):
    r = _d({"severity": sev}, "trend_unread_no_data")
    assert r["text"] == (
        "Risk-off de-risk check couldn't fully run: the S&P 500's 1-year history "
        "couldn't be read today, so the market regime is only partly known "
        f"(VIX is {_CALM_VIX:.1f}, below {_THR}). Your book is {_BOOK[sev]}, so the "
        f"S&P 500 closing below its {_AVG} average would have armed a de-risk trim."
        + _TAIL)


def test_regime_unread_text_trend_unread_short_history_says_so():
    r = _d({"severity": "caution"}, "trend_unread_short")
    assert r["text"].startswith(
        "Risk-off de-risk check couldn't fully run: the S&P 500's history was too "
        f"short for the {_AVG} check today, so the market regime is only partly known")
    assert "1-year history" not in r["text"]
    assert f"(VIX is {_CALM_VIX:.1f}, below {_THR})" in r["text"]


@pytest.mark.parametrize("sev", ["caution", "fragile"])
@pytest.mark.parametrize("regime", ["both_unread_no_data", "both_unread_short"])
def test_regime_unread_text_both_legs_unread(sev, regime):
    r = _d({"severity": sev}, regime)
    assert r["kind"] == "regime_unread"
    assert r["text"] == (
        "Risk-off de-risk check couldn't run: neither VIX nor the S&P 500's 1-year "
        "history could be read today, so the market regime is unknown. "
        f"Your book is {_BOOK[sev]}, so either signal (VIX at {_THR} or more, or the "
        f"S&P 500 below its {_AVG} average) would have armed a de-risk trim." + _TAIL)


def test_regime_unread_appends_no_coverage_note():
    # the book beta WAS measured, so a coverage "unmeasured" note has nothing to explain
    r = _d({"severity": "caution"}, "vix_unread", coverage=_cov("unmeasured"))
    assert r["text"].endswith("before adding risk.") and "ANT" not in r["text"]


@pytest.mark.parametrize("regime,phrase,verb", [
    ("vix_unread", "VIX", "couldn't be read either"),
    ("trend_unread_no_data", "the S&P 500's 1-year history", "couldn't be read either"),
    ("trend_unread_short", "the S&P 500's 1-year history", "couldn't be read either"),
    # no double negative: "neither ... could be read" (lead fix 2026-11-27)
    ("both_unread_no_data", "neither VIX nor the S&P 500's 1-year history", "could be read"),
])
def test_both_unread_text_names_the_unread_leg(regime, phrase, verb):
    r = _d(None, regime)
    assert r["kind"] == "both_unread"
    assert r["text"] == (
        "Risk-off de-risk check couldn't run: your portfolio beta couldn't be "
        f"measured, and {phrase} {verb}, so the check had nothing "
        "to go on today." + _TAIL)
    assert "neither" not in r["text"] or "couldn't be read either" not in r["text"]


def test_both_unread_appends_the_coverage_note_only_when_unmeasured():
    from stock_analyzer import risk_window
    cov = _cov("unmeasured")
    bare = _d(None, "vix_unread")
    r = _d(None, "vix_unread", coverage=cov)
    assert r["text"] == bare["text"] + " " + risk_window.display_note(cov)
    partial = _cov("partial", unmeasured_pct=4.0)
    assert _d(None, "vix_unread", coverage=partial)["text"] == bare["text"]
    for junk in (None, {}, "junk", {"state": "full"}):
        assert _d(None, "vix_unread", coverage=junk)["text"] == bare["text"]


def test_numbers_come_from_the_parameters_not_literals():
    n = 150
    above = pd.DataFrame({"Close": [100.0] * (n - 1) + [150.0]})
    short = pd.DataFrame({"Close": [100.0] * (n - 1)})
    # VIX unread, caution book
    r = derisk_unchecked({"severity": "caution"}, above, None,
                         trend_ma=n, vix_threshold=30.0)
    assert "below its 150-day average" in r["text"] and "VIX reading of 30 or more" in r["text"]
    # trend unread (short for 150), VIX 18.46 floors to 18.4, never rounds up
    r = derisk_unchecked({"severity": "fragile"}, short, 18.46,
                         trend_ma=n, vix_threshold=30.0)
    assert "too short for the 150-day check" in r["text"]
    assert "VIX is 18.4, below 30" in r["text"] and "18.5" not in r["text"]
    assert "closing below its 150-day average" in r["text"]
    # both unread
    r = derisk_unchecked({"severity": "caution"}, None, None, trend_ma=n, vix_threshold=30.0)
    assert "VIX at 30 or more, or the S&P 500 below its 150-day average" in r["text"]
    # a 150-bar frame is READ under trend_ma=150 (it is short under the default 200)
    assert derisk_unchecked({"severity": "caution"}, above, 18.0,
                            trend_ma=n, vix_threshold=30.0) is None


def test_a_below_threshold_vix_never_prints_as_the_threshold():
    r = derisk_unchecked({"severity": "caution"}, None, RISK_OFF_VIX_LEVEL - 0.04)
    assert f"VIX is {RISK_OFF_VIX_LEVEL - 0.1:.1f}, below" in r["text"]


# ── arming is unchanged: the grid against exit_advisor.assess_risk_off_derisk ─
def _arming_inputs():
    from stock_analyzer.constants import RISK_OFF_NAME_MIN_BETA
    port_df = pd.DataFrame([{"Ticker": "AAA", "Weight (%)": 20.0, "Price": 100.0,
                             "Shares": 10, "P&L (%)": 5.0}])
    held = {"AAA": {"risk_metrics": {"beta": RISK_OFF_NAME_MIN_BETA + 0.5}, "atr": 2.0}}
    return port_df, held


@pytest.mark.parametrize("fr", [None, {}, {"severity": "ok"}, {"severity": "calm"},
                                {"severity": "caution"}, {"severity": "fragile"}],
                         ids=repr)
@pytest.mark.parametrize("regime", list(_REGIMES))
def test_arming_unchanged_and_the_disclosure_is_none_whenever_a_card_exists(fr, regime):
    from stock_analyzer import exit_advisor
    port_df, held = _arming_inputs()
    spy, vix, state = _REGIMES[regime]
    cards = exit_advisor.assess_risk_off_derisk(
        port_df, held, fragility=fr, spy_trend_df=spy, vix_level=vix)
    sev = (fr or {}).get("severity")
    armed = sev in ("caution", "fragile") and state == "risk_off"
    assert bool(cards) is armed          # arming = book armable AND a leg tripped, exactly
    r = derisk_unchecked(fr, spy, vix)
    if cards:
        assert r is None
    # and the disclosure never depends on, or changes, what the check returned
    again = exit_advisor.assess_risk_off_derisk(
        port_df, held, fragility=fr, spy_trend_df=spy, vix_level=vix)
    assert [c["ticker"] for c in again] == [c["ticker"] for c in cards]


def test_text_is_plain_no_dollar_no_markdown():
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX, coverage=_cov())
    assert "$" not in r["text"] and "**" not in r["text"] and "<" not in r["text"]


# ── coverage clause ───────────────────────────────────────────────────────────
def test_unmeasured_coverage_appends_its_note():
    from stock_analyzer import risk_window
    cov = _cov("unmeasured")
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)
    note = risk_window.display_note(cov)
    assert note and r["text"].endswith(" " + note)
    assert "ANT" in r["text"]


def test_partial_coverage_appends_no_note():
    # "measured without X" beside "beta couldn't be measured" would mislead.
    from stock_analyzer import risk_window
    cov = _cov("partial", unmeasured_pct=4.0)
    assert risk_window.display_note(cov)        # the note exists ...
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)
    assert r["text"].endswith("before adding risk.")   # ... but is not appended
    assert "measured without" not in r["text"]


@pytest.mark.parametrize("cov", [None, {}, "junk", {"state": "full", "left_out": []},
                                 {"left_out": []}])
def test_full_or_absent_coverage_appends_nothing(cov):
    bare = derisk_unchecked(None, _spy(True), _CALM_VIX)
    assert derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)["text"] == bare["text"]
    assert bare["text"].endswith("before adding risk.")


def test_a_raising_display_note_does_not_raise(monkeypatch):
    def _boom(_c):
        raise RuntimeError("x")
    monkeypatch.setattr(risk_off_check.risk_window, "display_note", _boom)
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=_cov())
    assert r is not None and r["text"].endswith("before adding risk.")


# ── fail visible ──────────────────────────────────────────────────────────────
def test_a_raising_regime_read_returns_the_loud_both_unread_dict(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("regime exploded")
    monkeypatch.setattr(risk_off_check.regime_read, "read_regime", _boom)
    for fr in (None, {"severity": "caution"}, {"severity": "fragile"}):
        r = derisk_unchecked(fr, _spy(True), _HIGH_VIX)
        assert r == {"kind": "both_unread", "reasons": [], "text": (
            "Risk-off de-risk check couldn't run: your portfolio beta couldn't be "
            "measured, and the market regime couldn't be read either, so the check "
            "had nothing to go on today. "
            "Review your highest-beta holdings yourself before adding risk.")}


def test_a_raising_exit_advisor_leg_reads_as_unread_not_calm(monkeypatch):
    # regime_read degrades a raising leg to UNREAD (never "clear"), so the
    # disclosure stays loud instead of reading calm.
    def _boom(*a, **k):
        raise RuntimeError("regime exploded")
    monkeypatch.setattr(risk_off_check.regime_read.exit_advisor, "risk_off_regime", _boom)
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX)
    assert r["kind"] == "both_unread"


def test_a_raising_regime_read_is_moot_for_a_calm_book(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("never called")
    monkeypatch.setattr(risk_off_check.regime_read, "read_regime", _boom)
    assert derisk_unchecked({"severity": "calm"}, _spy(True), _HIGH_VIX) is None


@pytest.mark.parametrize("garbage", [None, "x", 5, float("nan"), object(), [], {}, -1])
def test_never_raises_on_garbage_inputs(garbage):
    for fr in (garbage, {"severity": garbage}, {"severity": "caution"}):
        r = derisk_unchecked(fr, garbage, garbage, coverage=garbage,
                             trend_ma=RISK_OFF_TREND_MA, vix_threshold=RISK_OFF_VIX_LEVEL)
        assert r is None or (r["kind"] in ("beta_unmeasured", "regime_unread", "both_unread")
                             and isinstance(r["text"], str))


@pytest.mark.parametrize("fr", [None, {"severity": "caution"}, {"severity": "fragile"}])
@pytest.mark.parametrize("regime", _UNREAD_STATES + _TRIPPED)
def test_every_text_is_plain_no_dollar_no_markdown_no_html(fr, regime):
    r = _d(fr, regime, coverage=_cov())
    if r is not None:
        for bad in ("$", "**", "<", ">", "`"):
            assert bad not in r["text"]
        assert r["text"].endswith(("before adding risk.", "."))


def test_defaults_are_the_constants():
    import inspect
    p = inspect.signature(derisk_unchecked).parameters
    assert p["trend_ma"].default == RISK_OFF_TREND_MA
    assert p["vix_threshold"].default == RISK_OFF_VIX_LEVEL


# ── risk_window outage wording (spec E) ───────────────────────────────────────
def test_all_no_prices_unmeasured_reads_as_an_outage_not_a_limit():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured", reason="no_prices")
    cov["left_out"][0]["n_returns"] = 0
    note = coverage_note(cov)
    assert note.startswith("Portfolio risk can't be measured: no price history could be loaded for ANT")
    assert note.endswith("(market data offline?).")
    assert "limit" not in note and "$" not in note


def test_outage_wording_lists_tickers_only_for_two_and_three_names():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured", reason="no_prices")
    cov["left_out"] = [{"ticker": t, "reason": "no_prices", "n_returns": 0,
                        "weight_pct": 8.0} for t in ("AAA", "BBB")]
    assert coverage_note(cov) == (
        "Portfolio risk can't be measured: no price history could be loaded for "
        "AAA and BBB (market data offline?).")
    cov["left_out"].append({"ticker": "CCC", "reason": "no_prices", "n_returns": 0,
                            "weight_pct": 8.0})
    note = coverage_note(cov)
    assert "for AAA, BBB and CCC (market data offline?)." in note
    assert "%" not in note and "no price history)" not in note


def test_mixed_no_prices_and_short_history_keeps_the_above_cap_text():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured")
    cov["left_out"].append({"ticker": "GONE", "reason": "no_prices", "n_returns": 0,
                            "weight_pct": 5.0})
    cov["unmeasured_pct"] = 20.0
    note = coverage_note(cov)
    assert f"more than the {PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}% limit" in note


def test_short_history_above_cap_text_is_unchanged():
    from stock_analyzer.risk_window import coverage_note
    note = coverage_note(_cov("unmeasured"))
    assert note == (
        f"Portfolio risk can't be measured: ANT (15.0% of the book: 3 trading days "
        f"since its first close, {BETA_MIN_RETURNS} needed) leaves out 15.0% of the "
        f"book, more than the {PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}% limit.")


def test_no_prices_left_out_with_a_partial_state_is_not_the_outage_text():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("partial", unmeasured_pct=4.0, reason="no_prices")
    assert "market data offline" not in coverage_note(cov)


# ── Brief wiring ──────────────────────────────────────────────────────────────
def _brief(**kw):
    from stock_analyzer.daily_briefing import build_daily_briefing
    from tests.conftest import make_port_df
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Tech"}])
    return build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[],
        macro_events=[], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=date(2026, 11, 25),
        market_context={"tone": "bull"}, **kw)


def test_brief_carries_the_disclosure_on_a_risk_off_day_with_no_fragility():
    b = _brief(fragility=None, spy_trend_df=_spy(True), vix_level=_CALM_VIX,
               risk_coverage=_cov())
    assert isinstance(b["risk_off_unchecked"], dict)
    assert "ANT" in b["risk_off_unchecked"]["text"]
    # the de-risk trim itself was never considered: no card of that kind anywhere
    assert all(a.get("kind") != "risk_off_derisk" for a in b["act_today"])
    assert all(a.get("kind") != "risk_off_derisk" for a in b["review_list"])


def test_brief_disclosure_is_none_when_fragility_is_checked_or_regime_calm():
    # (was {"severity": "ok"}: that shape is unmeasured since U-C 2026-11-27)
    assert _brief(fragility={"severity": "caution"}, spy_trend_df=_spy(True),
                  vix_level=_HIGH_VIX)["risk_off_unchecked"] is None
    assert _brief(fragility=None, spy_trend_df=_spy(False),
                  vix_level=_CALM_VIX)["risk_off_unchecked"] is None


def test_brief_with_no_regime_inputs_and_no_fragility_is_both_unread():
    # FLIPPED by owner decision U-C 2026-11-27 (was None: "regime inputs missing").
    assert _brief()["risk_off_unchecked"]["kind"] == "both_unread"


def test_brief_caution_book_with_vix_unread_is_regime_unread_and_act_unchanged():
    caution = {"severity": "caution"}
    unread = _brief(fragility=caution, spy_trend_df=_spy(False), vix_level=None)
    assert unread["risk_off_unchecked"]["kind"] == "regime_unread"
    calm_regime = _brief(fragility=caution, spy_trend_df=_spy(False), vix_level=_CALM_VIX)
    assert calm_regime["risk_off_unchecked"] is None
    assert unread["act_today"] == calm_regime["act_today"]
    assert unread["review_list"] == calm_regime["review_list"]
    # a calm book with the same unread inputs: nothing could have armed, so nothing to say
    assert _brief(fragility={"severity": "calm"}, spy_trend_df=_spy(False),
                  vix_level=None)["risk_off_unchecked"] is None


def test_brief_without_risk_coverage_still_discloses_without_the_note():
    b = _brief(fragility=None, spy_trend_df=_spy(True), vix_level=_CALM_VIX)
    assert b["risk_off_unchecked"]["text"].endswith("before adding risk.")


# ── source-pinned wiring (app.py has no importable tests) ─────────────────────
_APP = (_ROOT / "app.py").read_text(encoding="utf-8")
_CRON = (_ROOT / "cron_runner.py").read_text(encoding="utf-8")
_HAE = (_ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")


def test_every_app_build_daily_briefing_call_passes_the_risk_coverage():
    calls = [m.start() for m in re.finditer(r"\bbuild_daily_briefing\(", _APP)]
    # the import line has no "(", so every hit is a call
    assert len(calls) == 2
    for pos in calls:
        assert "risk_coverage" in _APP[pos:pos + 4000], \
            "a build_daily_briefing call lacks risk_coverage="


def test_home_renders_the_warning_from_the_brief_key_without_a_constants_comparison():
    i = _APP.index('_ro_unchk = (_daily_brief or {}).get("risk_off_unchecked")')
    block = _APP[i:i + 4000]
    # The header never says "you're set" on an unchecked day ...
    j_label = block.index('''_act_label  = "⚠️ Act Today — de-risk check couldn't run"''')
    j_set = block.index('''_act_label  = "✅ Act Today — you're set"''')
    assert j_label < j_set
    # ... the warning renders as plain text (no raw HTML) ...
    assert 'st.warning("⚠️ " + str(_ro_unchk["text"]))' in block
    # ... and the empty-Act case shows the not-an-all-clear caption before the calm box.
    j_cap = block.index("No trade calls today, but this is not an all-clear")
    j_calm = block.index("Nothing to act on — you're set for today.")
    assert j_cap < j_calm


def test_engine_and_cron_wiring():
    assert "_risk_off_check.derisk_unchecked(" in _HAE
    assert '"risk_off_unchecked": risk_off_unchecked' in _HAE
    assert 'payload.get("risk_off_unchecked")' in _CRON


def test_the_module_is_a_gate_file():
    import sys
    sys.path.insert(0, str(_ROOT / ".claude" / "hooks"))
    import pre_tool_checks as p
    assert "stock_analyzer/risk_off_check.py" in p._GATE_FILES
    # (Its review-policy.md reason line is a doc convention; a test reading that
    # doc would make it a push-suite input, see test_git_hook_adapter.)


# ── fix round: Summary / dock / Home-memo wiring (source pins) ────────────────
def test_summary_replaces_the_green_clear_and_the_zone4_no_protective_calls():
    i = _APP.index('_sm_ro_unchk = (_sm_daily_brief or {}).get("risk_off_unchecked")')
    block = _APP[i:i + 3200]
    assert 'No trade calls today, but this is not an all-clear: ' in block
    assert 'st.warning(' in block
    # the warning branch comes before the green "you're clear" container
    assert block.index("pass   # the warning above replaces") < block.index('key="sm_act_clear"')
    # Zone 4's evidence-based green is guarded by the same flag
    j = _APP.index("# RiskOffUnchecked (C4): the Brief was built but its risk-off")
    assert j < _APP.index("No protective calls</div>")


def test_dock_digest_publish_passes_the_brief_value():
    assert "risk_off_unchecked=risk_off_unchecked," in _APP
    assert re.search(
        r'_publish_home_dock_digest\(\s*_act_view,\s*'
        r'risk_off_unchecked=\(_daily_brief or \{\}\)\.get\("risk_off_unchecked"\),', _APP)


def test_user_guide_names_the_unchecked_header_and_every_surface():
    assert "Act Today — de-risk check couldn't run" in _APP
    assert "🧾 Summary and the dock line say the same instead of an all-clear" in _APP
