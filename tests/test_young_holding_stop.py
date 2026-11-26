"""A holding with no automatic stop yet (a new listing under HELD_STOP_MIN_BARS
sessions): `young_holding_stop` and its three readers (the composite-withheld
notice in `util`, `build_portfolio_df`'s `score_withheld` entries, and the
headless pre-market footer). Spec: docs/plans/stop-unavailable-holdings.md."""
import ast
import datetime
import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import young_holding_stop as y
from stock_analyzer.portfolio import build_portfolio_df
from stock_analyzer.util import score_withheld_banner_text
from tests._ast_cache import parse_file_cached, source_segment

_APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
_APP = _APP_PATH.read_text(encoding="utf-8")
B = chr(92)   # a backslash, to write the markdown escape without a string-literal warning


def _unavail(ticker="ANT", bars=3, need=15, note=None):
    """What `build_held_stops` returns for a too-few-bars holding."""
    r = hw.unavailable_result(
        ticker, note or f"No stop could be built: only {bars} completed bars (need {need}).")
    return {**r, "bars": bars, "need": need}


def _auto(stop=100.0, basis="full", label="Trailing Stop", note=""):
    return {"ticker": "X", "stop": stop, "basis": basis, "label": label, "note": note,
            "bars": None, "need": None}


# ── stop_state ───────────────────────────────────────────────────────────────
def test_state_auto_when_the_app_built_a_stop():
    s = y.stop_state(_auto(), None)
    assert s["state"] == "auto" and s["reason"] is None and s["manual_price"] is None


def test_state_none_with_bar_count_and_more_arithmetic():
    s = y.stop_state(_unavail(bars=3, need=15), None)
    assert s == {"state": "none", "manual_price": None, "bars": 3, "need": 15,
                 "more": 12, "reason": "short_history"}


def test_state_manual_only_when_there_is_no_automatic_stop():
    s = y.stop_state(_unavail(), {"stop_price": 80.0})
    assert s["state"] == "manual" and s["manual_price"] == 80.0 and s["more"] == 12


def test_manual_price_may_be_a_bare_number():
    assert y.stop_state(_unavail(), 80)["state"] == "manual"


def test_manual_looser_than_auto_stays_auto():
    s = y.stop_state(_auto(stop=100.0), {"stop_price": 90.0})
    assert s["state"] == "auto" and s["manual_price"] is None


def test_manual_tighter_than_auto_is_still_state_auto():
    # the state says whether the app built a stop; the stop in force is port_df's job
    assert y.stop_state(_auto(stop=100.0), {"stop_price": 105.0})["state"] == "auto"


@pytest.mark.parametrize("bad", [None, {}, {"stop_price": 0}, {"stop_price": -1},
                                 {"stop_price": float("nan")}, {"stop_price": "abc"},
                                 {"stop_price": float("inf")}])
def test_unusable_manual_row_is_none_state(bad):
    assert y.stop_state(_unavail(), bad)["state"] == "none"


def test_non_bar_count_reason_is_other_and_has_no_more():
    r = hw.unavailable_result("ZZ", "Stop could not be computed (KeyError).")
    s = y.stop_state(r, None)
    assert s["state"] == "none" and s["reason"] == "other" and s["more"] is None
    assert s["bars"] is None and s["need"] is None


def test_enough_bars_but_unavailable_is_other():
    # e.g. no price frame at all: a bar count at or over the floor is not "too short"
    s = y.stop_state(_unavail(bars=20, need=15), None)
    assert s["reason"] == "other" and s["more"] == 0


@pytest.mark.parametrize("bad", [True, -1, 2.5, "x", None, float("nan")])
def test_bad_bar_counts_read_as_unknown(bad):
    r = {**_unavail(), "bars": bad}
    s = y.stop_state(r, None)
    assert s["bars"] is None and s["more"] is None and s["reason"] == "other"


@pytest.mark.parametrize("res", [None, "x", [], 5])
def test_missing_or_non_dict_result_is_none_state(res):
    s = y.stop_state(res, None)
    assert s["state"] == "none" and s["reason"] == "other"
    assert y.stop_state(res, {"stop_price": 7})["state"] == "manual"


# ── bar count at the source ──────────────────────────────────────────────────
def _walk(n):
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    idx = pd.bdate_range(end="2026-10-29", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close}, index=idx)


def test_compute_held_stop_unavailable_carries_bars_and_need():
    df = _walk(3)
    r = hs.compute_held_stop(
        df, {"status": "ok", "start": df.index[0].date(), "first_buy_price": 100.0,
             "split_ratio": 1.0}, 100.0, date(2026, 10, 30),
        initial_mult=2.0, trail_mult=3.0, trail_start=None,
        ratchet_levels=C.STOP_RATCHET_LEVELS, warmup_bars=60,
        min_bars=C.HELD_STOP_MIN_BARS, fill_tol_pct=2.0)
    assert r["basis"] == "unavailable" and r["bars"] == 3 and r["need"] == C.HELD_STOP_MIN_BARS


def test_build_held_stops_passes_bars_through_to_the_state(monkeypatch):
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2025-01-01")
    df = _walk(3)
    start = df.index[0].date()
    trades = pd.DataFrame([{"id": 1, "ticker": "ANT", "action": "BUY", "shares": 10.0,
                            "price": 100.0, "traded_at": f"{start}T14:00:00+00:00"}])
    h = [{"Ticker": "ANT", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": 100.0}]
    out = hw.build_held_stops(h, {}, trades, date(2026, 10, 30),
                              fetch_history=lambda t, p: df, floors={}, carry={})
    assert out["ANT"]["basis"] == "unavailable"
    s = y.stop_state(out["ANT"], None)
    assert s["state"] == "none" and s["bars"] == 3 and s["need"] == C.HELD_STOP_MIN_BARS
    assert s["more"] == C.HELD_STOP_MIN_BARS - 3 and s["reason"] == "short_history"


# ── split_stop_rows ──────────────────────────────────────────────────────────
def test_split_mixes_none_manual_approx_and_skips_full():
    held = {
        "ANT": _unavail("ANT"),
        "NEW": _unavail("NEW", bars=10),
        "FULL": _auto(100.0),
        "PART": _auto(95.0, basis="partial", label="Trailing Stop", note="a note"),
    }
    parts = y.split_stop_rows(held, {"NEW": {"stop_price": 7.5}})
    assert [r["ticker"] for r in parts["none"]] == ["ANT"]
    assert [r["ticker"] for r in parts["manual"]] == ["NEW"]
    assert parts["manual"][0]["manual_price"] == 7.5 and parts["manual"][0]["more"] == 5
    assert parts["approx"] == [("PART", "Trailing Stop", "a note")]


def test_an_unavailable_row_never_lands_in_approx():
    parts = y.split_stop_rows({"ANT": _unavail()}, {})
    assert parts["approx"] == [] and len(parts["none"]) == 1


def test_unread_manual_table_reads_as_no_manual_stop():
    parts = y.split_stop_rows({"ANT": _unavail()}, None)
    assert len(parts["none"]) == 1 and parts["manual"] == []


@pytest.mark.parametrize("held", [None, {}, [], "x"])
def test_split_with_no_held_stops_is_empty(held):
    assert y.split_stop_rows(held, {}) == {"none": [], "manual": [], "approx": []}


def test_a_non_dict_result_is_a_none_row_with_the_generic_note():
    parts = y.split_stop_rows({"ANT": None}, {})
    assert parts["none"][0]["note"] == "No stop could be built for this holding."


def test_rows_are_sorted_by_ticker():
    parts = y.split_stop_rows({"ZED": _unavail("ZED"), "ABC": _unavail("ABC")}, {})
    assert [r["ticker"] for r in parts["none"]] == ["ABC", "ZED"]


# ── Home text ────────────────────────────────────────────────────────────────
def _none_rows(held, manual=None):
    return y.split_stop_rows(held, manual if manual is not None else {})["none"]


def test_none_notice_singular_exact_text():
    txt = y.none_notice_markdown(_none_rows({"ANT": _unavail()}))
    assert txt == (
        "⚠️ **No stop yet for 1 holding.** Nothing in the app will tell you to exit it "
        "until a stop exists:\n\n"
        "- **ANT**: only 3 completed sessions (15 needed, 12 more). Set your own stop "
        "below, or place one at your broker.")


def test_none_notice_plural_grammar_and_one_session():
    txt = y.none_notice_markdown(_none_rows({"AAA": _unavail("AAA", bars=1),
                                             "BBB": _unavail("BBB", bars=14)}))
    assert "No stop yet for 2 holdings." in txt and "exit them until" in txt
    assert "- **AAA**: only 1 completed session (15 needed, 14 more)." in txt
    assert "- **BBB**: only 14 completed sessions (15 needed, 1 more)." in txt


def test_none_notice_non_bar_reason_keeps_its_own_note():
    r = hw.unavailable_result("ZZ", "Stop could not be computed (KeyError).")
    txt = y.none_notice_markdown(_none_rows({"ZZ": r}))
    assert "- **ZZ**: Stop could not be computed (KeyError). Set your own stop below" in txt
    assert "completed session" not in txt


def test_none_notice_note_with_dollars_is_escaped_for_markdown():
    r = hw.unavailable_result("ZZ", "No usable fill ($1.00 and $2.00).")
    txt = y.none_notice_markdown(_none_rows({"ZZ": r}))
    assert B + "$1.00" in txt and B + "$2.00" in txt


def test_none_notice_is_none_without_rows():
    assert y.none_notice_markdown([]) is None
    assert y.none_notice_markdown(None) is None


def test_manual_notice_escapes_dollar_and_does_arithmetic():
    row = y.split_stop_rows({"ANT": _unavail()}, {"ANT": {"stop_price": 80}})["manual"][0]
    txt = y.manual_notice_markdown(row)
    assert txt == (f"ℹ️ **ANT**: your manual stop {B}$80.00 is in force. The app builds its "
                   "own stop after 12 more sessions; yours stays in force only while it "
                   "is the higher of the two.")


def test_manual_notice_one_more_session_and_other_reason():
    row = y.split_stop_rows({"ANT": _unavail(bars=14)}, {"ANT": {"stop_price": 80}})["manual"][0]
    assert "after 1 more session;" in y.manual_notice_markdown(row)
    other = hw.unavailable_result("ZZ", "Stop could not be computed (KeyError).")
    row = y.split_stop_rows({"ZZ": other}, {"ZZ": {"stop_price": 5}})["manual"][0]
    txt = y.manual_notice_markdown(row)
    assert "more session" not in txt and "could not build its own stop" in txt


def test_clear_confirm_short_history_plural_exact_text():
    row = y.split_stop_rows({"ANT": _unavail()}, {"ANT": {"stop_price": 80}})["manual"][0]
    assert y.clear_confirm_markdown(row) == (
        f"Remove your stop {B}$80.00 for ANT? ANT has no automatic stop yet, so nothing in "
        "the app will tell you to exit it until you set one again or the app builds its own "
        "(after 12 more sessions).")
    assert y.FORM_CLEAR_CONFIRM_LABEL == "Yes, remove my stop"


def test_clear_confirm_short_history_singular_session():
    row = y.split_stop_rows({"ANT": _unavail(bars=14)}, {"ANT": {"stop_price": 80}})["manual"][0]
    txt = y.clear_confirm_markdown(row)
    assert "(after 1 more session)." in txt and "1 more sessions" not in txt


def test_clear_confirm_other_reason_exact_text():
    other = hw.unavailable_result("ZZ", "Stop could not be computed (KeyError).")
    row = y.split_stop_rows({"ZZ": other}, {"ZZ": {"stop_price": 5}})["manual"][0]
    assert y.clear_confirm_markdown(row) == (
        f"Remove your stop {B}$5.00 for ZZ? The app could not build its own stop for ZZ, so "
        "nothing in the app will tell you to exit it until you set one again.")


def test_clear_confirm_unknown_more_reads_as_other_reason():
    row = {"ticker": "ANT", "manual_price": 80.0, "reason": y.REASON_SHORT_HISTORY,
           "more": None}
    txt = y.clear_confirm_markdown(row)
    assert "could not build its own stop for ANT" in txt and "more session" not in txt


def test_clear_confirm_escapes_every_dollar_sign():
    row = y.split_stop_rows({"ANT": _unavail()}, {"ANT": {"stop_price": 1234.5}})["manual"][0]
    txt = y.clear_confirm_markdown(row)
    assert f"{B}$1234.50" in txt and txt.count("$") == txt.count(f"{B}$") == 1


# ── email footer (plain text) ────────────────────────────────────────────────
def test_plain_notes_three_ways_no_escape():
    held = {"ANT": _unavail("ANT"), "NEW": _unavail("NEW", bars=10),
            "PART": _auto(95.0, basis="partial", label="Trailing Stop",
                          note="Costs " + B + "$5.00 here.")}
    notes = y.plain_notes(held, {"NEW": {"stop_price": 7.5}})
    assert notes == [
        "ANT: no stop yet (3 of 15 sessions). Nothing will flag an exit until one exists; "
        "set one on 🏠 Home or at your broker.",
        "NEW: your manual stop $7.50 is in force (no automatic stop for 5 more sessions).",
        "PART (Trailing Stop): Costs $5.00 here.",
    ]
    assert not any(B in n for n in notes)


def test_plain_notes_other_reason_variants():
    r = hw.unavailable_result("ZZ", "Stop could not be computed (KeyError).")
    assert y.plain_notes({"ZZ": r}, {}) == [
        "ZZ: no stop yet (Stop could not be computed (KeyError)). Nothing will flag an exit "
        "until one exists; set one on 🏠 Home or at your broker."]
    assert y.plain_notes({"ZZ": r}, {"ZZ": {"stop_price": 5}}) == [
        "ZZ: your manual stop $5.00 is in force (no automatic stop could be built)."]


def test_plain_notes_unread_manual_table_keeps_todays_lines():
    held = {"ANT": _unavail("ANT"), "FULL": _auto(100.0)}
    assert y.plain_notes(held, None) == [
        "ANT (Stop Unavailable): No stop could be built: only 3 completed bars (need 15)."]
    assert y.plain_notes(None, None) == [] and y.plain_notes(None, {}) == []


# ── validator ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("price", [None, 0, 0.0, -5, float("nan"), float("inf"), "abc", 0.004])
def test_validate_refuses_blank_zero_negative_and_non_finite(price):
    ok, msg, pct = y.validate_manual_stop(price, 100.0)
    assert (ok, msg, pct) == (False, "Enter a stop price above $0.", None)


def test_validate_refuses_at_or_above_today_s_price():
    for p in (100.0, 100.004, 101.0):
        ok, msg, pct = y.validate_manual_stop(p, 100.0)
        assert not ok and pct is None
        assert msg == ("A stop at or above today's price ($100.00) would trigger at once. "
                       "Enter a price below $100.00.")


def test_validate_accepts_a_cent_below_and_reports_percent():
    ok, msg, pct = y.validate_manual_stop(99.99, 100.0)
    assert ok and msg == "" and pct == pytest.approx(0.01)
    ok, _, pct = y.validate_manual_stop(80.0, 100.0)
    assert ok and pct == pytest.approx(20.0)


@pytest.mark.parametrize("cur", [None, 0, -1, float("nan"), "x"])
def test_validate_refuses_when_today_s_price_is_unknown(cur):
    ok, msg, pct = y.validate_manual_stop(50.0, cur)
    assert not ok and pct is None and "isn't available" in msg


def test_form_text():
    assert y.form_label("ANT") == "✋ Set your own stop for ANT"
    assert y.form_caption() == (
        "The app advises; your broker executes. Enter the stop you placed (or want the app "
        "to watch). From session 15 the app builds its own stop; yours stays in force only "
        "while it is the higher of the two.")
    assert y.form_pct_text(12.34, 100.0) == "That is 12.3% below today's price ($100.00)."
    assert y.form_saved_text("ANT", 80.0) == "Stop $80.00 saved for ANT."
    assert y.FORM_SAVE_LABEL == "💾 Save stop" and y.FORM_SOURCE_ACTION == "stop_unavailable"
    assert y.md_dollars("$1 and $2") == B + "$1 and " + B + "$2"


def test_form_caption_floor_comes_from_the_constant(monkeypatch):
    monkeypatch.setattr(C, "HELD_STOP_MIN_BARS", 20)
    assert "From session 20 the app builds" in y.form_caption()


# ── composite-withheld notice (util) ─────────────────────────────────────────
def _w(ticker="ANT", bars=3, **kw):
    return {"ticker": ticker, "missing": ["Technical"], "t_bars": bars, **kw}


_LEGACY = ("ℹ️ 1 holding shown without a composite score. ANT: price history too short "
           "(3 sessions; 50 needed) to measure the technical trend, so the composite is "
           "withheld, and the trend-break exit ladder can't assess it either — only its "
           "stop and concentration limits protect it.")


def test_withheld_legacy_entry_without_stop_state_is_todays_text():
    assert score_withheld_banner_text([_w()]) == _LEGACY


def test_withheld_auto_state_is_todays_text_byte_for_byte():
    assert score_withheld_banner_text([_w(stop_state="auto", manual_stop=None,
                                          stop_reason=None)]) == _LEGACY


def test_withheld_manual_state_names_the_owner_stop():
    t = score_withheld_banner_text([_w(stop_state="manual", manual_stop=80.0,
                                       stop_reason="short_history")])
    assert t.endswith("either — only your manual stop (" + B + "$80.00) and concentration "
                      "limits protect it.")
    assert "only its stop" not in t


def test_withheld_none_state_says_no_stop_and_points_to_home():
    t = score_withheld_banner_text([_w(stop_state="none", manual_stop=None,
                                       stop_reason="short_history")])
    assert t == (
        "ℹ️ 1 holding shown without a composite score. ANT: price history too short "
        "(3 sessions; 50 needed) to measure the technical trend, so the composite is "
        "withheld, and the trend-break exit ladder can't assess it either. No stop can be "
        "built yet (15 sessions needed), so only concentration limits protect it: set your "
        "own stop under the stop warning on 🏠 Home, or place one at your broker.")


def test_withheld_none_state_floor_comes_from_the_constant(monkeypatch):
    monkeypatch.setattr(C, "HELD_STOP_MIN_BARS", 12)
    t = score_withheld_banner_text([_w(stop_state="none", stop_reason="short_history")])
    assert "No stop can be built yet (12 sessions needed)" in t


def test_withheld_none_state_other_cause_does_not_claim_a_floor():
    t = score_withheld_banner_text([_w(stop_state="none", stop_reason="other")])
    assert "No stop could be built for it, so only concentration limits protect it" in t
    assert "sessions needed)," not in t.split("either.")[1]
    assert "yet" not in t.split("either.")[1]


def test_withheld_mixed_states_each_sentence_true_for_its_tickers():
    t = score_withheld_banner_text([
        _w("AAA", 20, stop_state="auto"),
        _w("BBB", 4, stop_state="manual", manual_stop=5.0, stop_reason="short_history"),
        _w("CCC", 3, stop_state="none", stop_reason="short_history"),
        _w("DDD", 2, stop_state="none", stop_reason="short_history"),
    ])
    assert t.startswith("ℹ️ 4 holdings shown without a composite score. ")
    assert ("AAA: price history too short (20 sessions; 50 needed) to measure the technical "
            "trend, so the composite is withheld, and the trend-break exit ladder can't assess "
            "it either — only its stop and concentration limits protect it.") in t
    assert ("BBB: price history too short (4 sessions; 50 needed)") in t
    assert "only your manual stop (" + B + "$5.00) and concentration limits protect it." in t
    assert ("CCC, DDD: price history too short (CCC 3 sessions, DDD 2 sessions; 50 needed) "
            "to measure the technical trend, so the composites are withheld, and the "
            "trend-break exit ladder can't assess them either. No stop can be built yet "
            "(15 sessions needed), so only concentration limits protect them: set your own "
            "stops under the stop warning on 🏠 Home, or place them at your broker.") in t
    # the auto and manual sentences never name a ticker that has no stop
    for name in ("CCC", "DDD"):
        assert t.count(name) == 2          # the lead and the span, in one sentence only


def test_withheld_two_manual_stops_name_each_price():
    t = score_withheld_banner_text([
        _w("AAA", 4, stop_state="manual", manual_stop=5.0),
        _w("BBB", 4, stop_state="manual", manual_stop=6.5)])
    assert "only your manual stops (AAA " + B + "$5.00, BBB " + B + "$6.50) and concentration " \
           "limits protect them." in t


def test_withheld_fundamentals_sentence_follows_the_stop_state():
    orig = score_withheld_banner_text([{"ticker": "SPY"}])
    assert orig.endswith("Price, weight, stops and risk metrics still apply.")
    assert score_withheld_banner_text([{"ticker": "SPY", "stop_state": "auto"}]) == orig
    none = score_withheld_banner_text([{"ticker": "SPY", "stop_state": "none"}])
    assert none.endswith("Price, weight and risk metrics still apply; no stop could be built "
                         "for it yet (see the stop warning).")
    both = score_withheld_banner_text([{"ticker": "SPY", "stop_state": "none"},
                                       {"ticker": "QQQ", "stop_state": "none"}])
    assert "no stop could be built for them yet (see the stop warning)." in both
    mixed = score_withheld_banner_text([{"ticker": "SPY", "stop_state": "none"},
                                        {"ticker": "QQQ", "stop_state": "auto"}])
    assert mixed.endswith("Price, weight, stops and risk metrics still apply, except that no "
                          "stop could be built yet for SPY (see the stop warning).")


# ── build_portfolio_df: the withheld entry carries the stop state ────────────
def _row(price=100.0):
    return {"current_price": price, "stop": 90.0, "sector": "Technology",
            "rec": {"icon": "G", "label": "Buy"}, "total": 70,
            "t_available": False, "t_bars": 3}


_H = [{"Ticker": "ANT", "Shares": 10, "Avg Cost ($)": 50.0}]


def _withheld(held_stops, manual_stops=None):
    df = build_portfolio_df(_H, {"ANT": _row()}, manual_stops=manual_stops,
                            held_stops=held_stops)
    return df.attrs["score_withheld"]


def test_withheld_entry_none_when_no_stop_and_no_manual():
    w = _withheld({"ANT": _unavail()})
    assert w == [{"ticker": "ANT", "missing": ["Technical"], "t_bars": 3,
                  "stop_state": "none", "manual_stop": None, "stop_reason": "short_history"}]


def test_withheld_entry_manual_when_only_the_owner_has_a_stop():
    w = _withheld({"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}})
    assert w[0]["stop_state"] == "manual" and w[0]["manual_stop"] == 80.0
    assert w[0]["stop_reason"] == "short_history"


def test_withheld_entry_auto_when_the_app_built_one():
    held = {"ANT": {**_auto(90.0), "components": {"entry": 90.0, "trailing": None, "ratchet": None}}}
    w = _withheld(held, {"ANT": {"stop_price": 70.0}})
    assert w[0]["stop_state"] == "auto" and w[0]["manual_stop"] is None


def test_withheld_entry_with_no_held_stop_result_reads_none_other():
    w = _withheld(None)
    assert w[0]["stop_state"] == "none" and w[0]["stop_reason"] == "other"


def test_the_stop_state_agrees_with_the_stop_the_frame_carries():
    for held, manual, state in (
            ({"ANT": _unavail()}, None, "none"),
            ({"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, "manual")):
        df = build_portfolio_df(_H, {"ANT": _row()}, manual_stops=manual, held_stops=held)
        stop = df.iloc[0]["Stop"]
        assert (state == "none") == (stop is None or pd.isna(stop))


# ── headless pre-market footer ───────────────────────────────────────────────
TODAY = datetime.date(2026, 11, 24)


def _ctx(held_stops, manual):
    with patch.object(hae.db, "has_db", return_value=True), \
         patch.object(hae.db, "load_holdings_or_none",
                      return_value=pd.DataFrame({"Ticker": ["ANT"]})), \
         patch.object(hae.db, "load_trades_or_none", return_value=pd.DataFrame({"ticker": ["ANT"]})), \
         patch.object(hae.db, "load_manual_stops_or_none", return_value=manual), \
         patch.object(hae.db, "load_held_stop_floors_or_none", return_value={}), \
         patch.object(hae.db, "load_held_stop_carry_or_none", return_value={}), \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.045), \
         patch.object(hae, "fetch_spy", return_value=pd.DataFrame({"Close": [1.0, 2.0]})), \
         patch.object(hae, "_vix_level", return_value=16.0), \
         patch.object(hae, "load_bundle", return_value={"financials": {}}), \
         patch.object(hae, "_build_open_lots", return_value=[]), \
         patch.object(hae.exit_advisor, "material_add_window_days", return_value=None), \
         patch.object(hae._held_stop_wiring, "build_held_stops", return_value=held_stops), \
         patch.object(hae, "build_portfolio_df",
                      return_value=pd.DataFrame({"Ticker": ["ANT"], "Weight (%)": [10.0]})), \
         patch.object(hae, "compute_portfolio_risk_metrics", return_value={"beta": 1.1}), \
         patch.object(hae, "run_scenario", return_value={"port_impact_pct": -5.0}), \
         patch.object(hae, "assess_fragility", return_value={"severity": "elevated"}):
        return hae._build_context(TODAY)


def test_footer_says_no_stop_yet_when_no_manual_stop_is_set():
    r = _ctx({"ANT": _unavail()}, {})
    assert r["held_stop_notes"] == [
        "ANT: no stop yet (3 of 15 sessions). Nothing will flag an exit until one exists; "
        "set one on 🏠 Home or at your broker."]


def test_footer_names_the_manual_stop_in_force():
    r = _ctx({"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}})
    assert r["held_stop_notes"] == [
        "ANT: your manual stop $80.00 is in force (no automatic stop for 12 more sessions)."]


def test_footer_keeps_todays_note_when_the_manual_stops_could_not_be_read():
    r = _ctx({"ANT": _unavail()}, None)
    assert r["manual_stops_unreadable"] is True
    assert r["held_stop_notes"] == [
        "ANT (Stop Unavailable): No stop could be built: only 3 completed bars (need 15)."]
    assert not any("manual stop" in n for n in r["held_stop_notes"])


# ── app.py is render-only for the split ──────────────────────────────────────
def _fn_src(name):
    tree = parse_file_cached(_APP_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return source_segment(_APP, node)
    raise AssertionError(name)


def test_app_banner_helpers_do_not_decide_the_split_themselves():
    for name in ("_held_stops_banner_text", "_render_held_stop_notices"):
        src = _fn_src(name)
        code = ast.unparse(ast.parse(src))      # no docstring, no comments
        assert "degraded_notes" not in code and "unavailable" not in code.lower(), name
        assert not re.search(r"""["']basis["']""", code), name
    assert "split_stop_rows(" in _fn_src("_render_held_stop_notices")


def test_home_passes_none_to_the_split_when_manual_stops_are_unreadable():
    assert ("_render_held_stop_notices(_held_stops, None if _manual_stops_unreadable "
            "else _manual_stops") in _APP


def test_explainer_does_not_describe_an_automatic_stop_that_does_not_exist():
    src = _fn_src("_render_stop_ladder")
    assert '_no_auto = _basis == "unavailable"' in src
    assert "if not _no_auto:" in src
    assert 'elif _basis in ("partial", "prior_session"):' in src
    assert '"unavailable")' not in src.split("_lines.append(f\"**Right now:**")[1]
