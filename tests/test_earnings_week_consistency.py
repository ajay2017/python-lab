"""EW C2 (docs/plans/earnings-week-trim-consistency.md, spec earnings-week-trim-c1c2-spec.md):
in earnings week a held name is sized ONCE, by trim_sizing.earnings_week_decision, and the
🏠 Brief (S1), 🔔 Catalyst Watch / 📅 Playbook (S4) and 📡 Signals (S3) all read it. Outside the
window each surface keeps its own standing rule. Also pins the history guard, the Brief
skip set (U5), the WATCH wording and the app.py render branch."""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import earnings_advisor as ea
from stock_analyzer import rec_events_capture as rec
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    REBALANCE_TRIM_PNL_PCT,
    SINGLE_NAME_CEILING,
    SINGLE_NAME_TRIM_TRIGGER,
)
from stock_analyzer.daily_briefing import _review_list
from stock_analyzer.portfolio import rebalance_actions

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 26)          # a Monday
_SHARES = 100
_PRICE = 50.0
_NO_TRIM = 100.0                     # "no trim" counts as the position staying at 100%

_PNLS = (REBALANCE_TRIM_PNL_PCT - 1, REBALANCE_TRIM_PNL_PCT + 1)
_DAYS = (-1, 0, EARNINGS_IMMINENT_DAYS, EARNINGS_IMMINENT_DAYS + 1)


def _weights(n):
    f = ts.earnings_overweight_floor(n)
    return (f - 0.1, f, f + 0.1, SINGLE_NAME_CEILING, SINGLE_NAME_TRIM_TRIGGER,
            SINGLE_NAME_TRIM_TRIGGER + 0.1, 22.1)


def _book(weight, n, pnl, shares=_SHARES):
    rows = [{
        "Ticker": "AAA", "Weight (%)": weight, "P&L (%)": pnl, "Price": _PRICE,
        "Shares": shares, "Signal": "Hold", "Score": 70.0, "Stop": _PRICE * 0.9,
        "Gap to Stop (%)": 10.0, "Market Value": shares * _PRICE,
        "Avg Cost": _PRICE / 1.5, "Stop Type": "ATR Stop", "Sector": "Tech",
    }]
    for i in range(n - 1):
        rows.append({
            "Ticker": f"F{i}", "Weight (%)": 5.0, "P&L (%)": 0.0, "Price": 10.0,
            "Shares": 10, "Signal": "Hold", "Score": 60.0, "Stop": 9.0,
            "Gap to Stop (%)": 10.0, "Market Value": 100.0, "Avg Cost": 10.0,
            "Stop Type": "ATR Stop", "Sector": "Tech",
        })
    return pd.DataFrame(rows)


def _held(days, today=_TODAY, stamp=None):
    d = (today + timedelta(days=days)).isoformat()
    return {"AAA": {"earnings": stamp or d}}


def _s1(df, days, act_today=None, today=_TODAY):
    items = _review_list(df, [], [], _held(days, today), today, portfolio_value=100_000.0,
                         act_today=act_today)
    return next((i for i in items if i["ticker"] == "AAA" and i["icon"] == "📅"), None)


def _s3(df, days):
    acts = rebalance_actions(df, earnings_days={"AAA": days})
    return next((a for a in acts if a["type"] == "trim" and a["ticker"] == "AAA"), None)


def _s4(w, n, days):
    return ea._recommend(days, 70.0, w, 70.0, 10.0, 0, "Buy", _SHARES, _SHARES * _PRICE, 8.0,
                         n_positions=n, sizing_shares=_SHARES)


def _s4_target(w, n, days):
    """Target weight S4 sizes to (100 = no REDUCE), parsed from the detail text."""
    action, _p, detail, _l = _s4(w, n, days)
    if action != "REDUCE":
        return _NO_TRIM
    import re
    m = re.search(r"Trim to ≈(\d+)% weight", detail)
    assert m, detail
    return float(m.group(1))


# -- the matrix: every surface equals the one decision ---------------------------

@pytest.mark.parametrize("n", range(5, 13))
def test_cross_surface_matrix_equals_the_decision(n):
    for w in _weights(n):
        for days in _DAYS:
            for pnl in _PNLS:
                df = _book(w, n, pnl)
                dec = ts.earnings_week_decision(w, n, _SHARES) if ts.in_earnings_window(days) else None
                sized = dec is not None and dec["trim_shares"] >= 1
                where = f"n={n} w={w} days={days} pnl={pnl}"

                # S1 (the Brief) exists only in the window
                s1 = _s1(df, days)
                if not ts.in_earnings_window(days):
                    assert s1 is None, where
                else:
                    assert s1 is not None, where
                    if sized:
                        assert s1["action"]["type"] == "TRIM_TO_TARGET", where
                        assert s1["action"]["target_weight"] == dec["target_pct"], where
                        assert s1["action"]["trim_shares"] == dec["trim_shares"], where
                    else:
                        assert s1["action"]["type"] == "WATCH", where

                # S3 (Signals)
                s3 = _s3(df, days)
                if dec is not None:
                    assert s3 is not None, where
                    assert s3["basis"] == dec["basis"], where
                    assert s3["target_weight"] == dec["target_pct"], where
                    assert s3["trim_shares"] == dec["trim_shares"], where
                elif ts.in_earnings_window(days):
                    # a None decision implies w <= SINGLE_NAME_TRIM_TRIGGER: no trim at any P&L
                    assert w <= SINGLE_NAME_TRIM_TRIGGER, where
                    assert s3 is None, where
                else:
                    # outside the window: the standing ceiling rule, unchanged
                    if w > SINGLE_NAME_TRIM_TRIGGER and pnl > REBALANCE_TRIM_PNL_PCT:
                        assert s3 is not None and s3["basis"] == "ceiling", where
                        assert s3["target_weight"] == SINGLE_NAME_CEILING, where
                    else:
                        assert s3 is None, where

                # S4 (Playbook / Catalyst Watch): only called for 0 <= days <= lookahead
                if days >= 0:
                    t4 = _s4_target(w, n, days)
                    if ts.in_earnings_window(days):
                        assert t4 == (dec["target_pct"] if sized else _NO_TRIM), where
                    elif w > SINGLE_NAME_TRIM_TRIGGER and ts.shares_to_trim(
                            _SHARES, w, SINGLE_NAME_CEILING) >= 1:
                        assert t4 == SINGLE_NAME_CEILING, where
                    else:
                        assert t4 == _NO_TRIM, where


@pytest.mark.parametrize("n", range(5, 13))
def test_earnings_week_never_loosens_on_signals_and_the_playbook(n):
    last = EARNINGS_IMMINENT_DAYS
    for w in _weights(n):
        for pnl in _PNLS:
            df = _book(w, n, pnl)
            s3_in, s3_out = _s3(df, last), _s3(df, last + 1)
            t3_in = _NO_TRIM if s3_in is None or s3_in["trim_shares"] < 1 else s3_in["target_weight"]
            t3_out = _NO_TRIM if s3_out is None or s3_out["trim_shares"] < 1 else s3_out["target_weight"]
            assert t3_in <= t3_out, f"S3 n={n} w={w} pnl={pnl}"
        t4_in, t4_out = _s4_target(w, n, last), _s4_target(w, n, last + 1)
        assert t4_in <= t4_out, f"S4 n={n} w={w}"


# -- S1 WATCH wording --------------------------------------------------------------

@pytest.mark.parametrize("n", [5, 8, 10])
def test_watch_never_says_conservative_at_or_over_the_ceiling(n):
    floor = ts.earnings_overweight_floor(n)
    for w in (SINGLE_NAME_CEILING, SINGLE_NAME_CEILING + 0.5, SINGLE_NAME_TRIM_TRIGGER):
        if ts.earnings_week_decision(w, n, _SHARES) is not None:
            continue                      # sized (or zero-share WATCH), a different text
        item = _s1(_book(w, n, _PNLS[1]), 3)
        assert item["action"]["type"] == "WATCH"
        assert "already conservative" not in item["why"]
        # R2-B2: true even when the weak-large rule trims the name (no "nothing is due",
        # no "(🔧 Tune-up)" claim).
        assert (f"under the {floor:.1f}% position-count-adjusted earnings-week line "
                f"(N={n} positions), so the earnings-week rule sizes nothing before the "
                f"report; it is over the {SINGLE_NAME_CEILING:.0f}% single-name ceiling "
                f"(standing work).") in item["why"]
        assert "nothing is due" not in item["why"] and "Tune-up" not in item["why"]


def test_watch_keeps_the_old_text_below_the_ceiling():
    item = _s1(_book(SINGLE_NAME_CEILING - 3.0, 15, _PNLS[1]), 3)
    assert item["action"]["type"] == "WATCH"
    assert "within tolerance" in item["why"]
    assert "Existing sizing already conservative for the binary risk." in item["why"]


def test_standing_basis_why_and_zero_share_watch_wording():
    n = 7
    w = SINGLE_NAME_TRIM_TRIGGER + 1.0
    floor = ts.earnings_overweight_floor(n)
    item = _s1(_book(w, n, _PNLS[0]), 3)
    assert item["action"]["type"] == "TRIM_TO_TARGET"
    assert item["action"]["reason_key"] == "earnings"
    assert item["why"] == (
        f"Weight {w:.1f}% is under your {floor:.1f}% position-count-adjusted earnings-week "
        f"line (N={n} positions) but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name "
        f"trigger, so the standing trim to {SINGLE_NAME_CEILING:.0f}% applies before the "
        f"report: earnings week never sizes looser than the week before.")
    one = _s1(_book(w, n, _PNLS[0], shares=1), 3)
    assert one["action"] == {"type": "WATCH"}
    assert one["why"] == (
        f"Weight {w:.1f}% is over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger by "
        f"less than one share at today's price, so no trim is sized (N={n} positions).")


# -- S3 titles, triggers, notes ----------------------------------------------------

def test_signals_text_per_basis():
    # earnings_week
    n, w = 8, 19.0
    floor = ts.earnings_overweight_floor(n)
    a = _s3(_book(w, n, _PNLS[1]), 3)
    assert a["title"] == "Oversized Going Into Earnings" and a["floor_pct"] == floor
    assert a["trigger"] == (f"Weight {w:.1f}% with earnings in 3d, above the {floor:.1f}% "
                            f"earnings-week line, so the earnings-week rule trims to "
                            f"{EARNINGS_OVERWEIGHT_TRIM_TO_PCT:.0f}%")
    assert a["earnings_note"] == ("Earnings in 3d — sized by the same earnings-week rule the "
                                  "🏠 Brief and 🔔 Catalyst Watch use.")
    # day 0 says "today"
    assert "with earnings today," in _s3(_book(w, n, _PNLS[1]), 0)["trigger"]
    # standing_in_window, at ANY P&L (U1b)
    n, w = 7, 19.0
    floor = ts.earnings_overweight_floor(n)
    for pnl in (-5.0, _PNLS[0], _PNLS[1]):
        a = _s3(_book(w, n, pnl), 3)
        assert a["basis"] == "standing_in_window"
        assert a["title"] == "Over the Single-Name Ceiling Going Into Earnings"
        assert a["trigger"] == (
            f"Weight {w:.1f}% with earnings in 3d: under the {floor:.1f}% earnings-week line "
            f"but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger, so the "
            f"standing trim to {SINGLE_NAME_CEILING:.0f}% applies")
        assert a["earnings_note"] == (
            f"Earnings in 3d: under the {floor:.1f}% earnings-week line, so the standing "
            f"{SINGLE_NAME_CEILING:.0f}% ceiling trim applies before the report (the same "
            f"earnings-week rule the 🏠 Brief and 🔔 Catalyst Watch use).")
    # ceiling (outside the window): unchanged text, no note
    a = _s3(_book(19.0, 8, _PNLS[1]), EARNINGS_IMMINENT_DAYS + 1)
    assert a["basis"] == "ceiling" and a["title"] == "Oversized Position with Strong Gain"
    assert a["earnings_note"] is None and a["floor_pct"] is None
    assert a["trigger"] == (f"Weight 19% exceeds {SINGLE_NAME_TRIM_TRIGGER:.0f}% threshold "
                            f"with +{_PNLS[1]:.0f}% profit")


def test_s4_standing_basis_text():
    n, w = 7, 19.0
    floor = ts.earnings_overweight_floor(n)
    action, prio, detail, lens = _s4(w, n, 3)
    sh = ts.shares_to_trim(_SHARES, w, SINGLE_NAME_CEILING)
    assert (action, prio) == ("REDUCE", "HIGH")
    assert f"under the {floor:.1f}% earnings-week line, but over the " \
           f"{SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger" in detail
    assert f"Trim to ≈{SINGLE_NAME_CEILING:.0f}% weight: sell {sh:,} shares" in detail
    # R2-N2: the lens explains THIS rule (the line scales with N; never looser than the
    # week before), not the day-8 "no single position above 15%" wording.
    assert (f"the earnings-week line scales with how many positions you hold "
            f"(N={n}: {floor:.1f}%), and earnings week never sizes looser than the week "
            f"before, so a position over {SINGLE_NAME_TRIM_TRIGGER:.0f}% is still trimmed "
            f"to {SINGLE_NAME_CEILING:.0f}% going into the report, regardless of "
            f"conviction. The reason is not that you're wrong") in lens
    assert "no single position above" not in lens


# -- U5: the Brief's skip set --------------------------------------------------------

def _w19():
    return _book(19.0, 8, _PNLS[1])


@pytest.mark.parametrize("kind", ["risk", "critical_news"])
def test_attribution_and_news_cards_keep_the_earnings_trim(kind):
    item = _s1(_w19(), 3, act_today=[{"kind": kind, "ticker": "AAA"}])
    assert item is not None and item["action"]["type"] == "TRIM_TO_TARGET"


@pytest.mark.parametrize("kind", ["stop_breach", "deterioration_trim"])
def test_a_card_that_reduces_the_name_removes_the_earnings_trim(kind):
    assert _s1(_w19(), 3, act_today=[{"kind": kind, "ticker": "AAA"}]) is None


def test_another_tickers_exit_does_not_remove_it():
    item = _s1(_w19(), 3, act_today=[{"kind": "stop_breach", "ticker": "ZZZ"}])
    assert item is not None


# -- time context ----------------------------------------------------------------

def test_saturday_visit_with_a_monday_report_reads_two_days_on_s1_and_s4():
    sat = date(2026, 11, 28)
    assert sat.weekday() == 5
    held = {"AAA": {"earnings": "2026-11-30"}}
    df = _book(19.0, 8, _PNLS[1])
    items = _review_list(df, [], [], held, sat, portfolio_value=100_000.0)
    s1 = next(i for i in items if i["ticker"] == "AAA" and i["icon"] == "📅")
    assert "in 2d" in s1["headline"] and s1["action"]["type"] == "TRIM_TO_TARGET"
    pb = ea.build_earnings_playbook(df, held, today=sat)
    row = next(p for p in pb if p["ticker"] == "AAA")
    assert row["days_until"] == 2 and row["action"] == "REDUCE"


def test_day_zero_is_identical_on_s1_s3_s4():
    df = _book(19.0, 8, _PNLS[0])
    dec = ts.earnings_week_decision(19.0, 8, _SHARES)
    assert _s1(df, 0)["action"]["trim_shares"] == dec["trim_shares"]
    assert _s1(df, 0)["headline"].startswith("earnings TODAY")
    assert _s3(df, 0)["trim_shares"] == dec["trim_shares"]
    assert _s4_target(19.0, 8, 0) == dec["target_pct"]


def test_timestamped_earnings_string_is_read_the_same_by_the_brief_and_the_playbook():
    stamp = "2026-11-02 16:00:00"             # 7 days after the Monday _TODAY
    df = _book(19.0, 8, _PNLS[1])
    held = _held(7, stamp=stamp)
    items = _review_list(df, [], [], held, _TODAY, portfolio_value=100_000.0)
    assert any(i["ticker"] == "AAA" and i["icon"] == "📅" for i in items)
    pb = ea.build_earnings_playbook(df, held, today=_TODAY)
    row = next((p for p in pb if p["ticker"] == "AAA"), None)
    assert row is not None, "the Playbook used to drop a timestamped date"
    assert row["days_until"] == 7 and row["earnings_date"] == date(2026, 11, 2)


# -- the history guard (rec_events_capture; DoD #8) -------------------------------------

def _old_rule_rows(df, days):
    """What _build_rebal_trim_rows produced BEFORE EW C2, computed with the old rule."""
    rows = []
    for _, r in df.iterrows():
        w, pnl, shares = r["Weight (%)"], r["P&L (%)"], ts.exact_shares(r)
        if not (w > SINGLE_NAME_TRIM_TRIGGER and pnl > REBALANCE_TRIM_PNL_PCT):
            continue
        ew = ts.earnings_week_trim(w, len(df), shares) if ts.in_earnings_window(days) else None
        target = ew["target_pct"] if ew is not None else SINGLE_NAME_CEILING
        if ts.shares_to_trim(shares, w, target) < 1:
            continue
        rows.append((r["Ticker"], w, target, r["Market Value"] * (w - target) / w))
    return rows


@pytest.mark.parametrize("n", range(5, 13))
def test_rebal_trim_rows_are_unchanged_by_the_new_rule(n):
    for w in _weights(n):
        for days in _DAYS:
            for pnl in _PNLS:
                df = _book(w, n, pnl)
                got = rec._build_rebal_trim_rows(df, "2026-10-26", None, earnings_days={"AAA": days})
                want = _old_rule_rows(df, days)
                where = f"n={n} w={w} days={days} pnl={pnl}"
                assert [g["ticker"] for g in got] == [x[0] for x in want], where
                for g, x in zip(got, want):
                    assert g["metric_before"] == pytest.approx(x[1]), where
                    assert g["metric_predicted_after"] == pytest.approx(x[2]), where
                    assert g["rec_dollars"] == pytest.approx(x[3]), where


def test_low_gain_in_window_trim_writes_no_row_and_is_not_in_skipped_out():
    df = _book(19.0, 8, REBALANCE_TRIM_PNL_PCT - 1)
    assert _s3(df, 3) is not None                       # Signals does fire (U1b) ...
    skipped: set = set()
    rows = rec._build_rebal_trim_rows(df, "2026-10-26", skipped, earnings_days={"AAA": 3})
    assert rows == [] and skipped == set()              # ... but history does not record it


# -- app.py render branch (app.py has no unit tests) ----------------------------------------

def test_signals_trim_branch_reads_the_note_and_gates_the_gain_sentence():
    src = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = src.index('elif act["type"] == "trim":')
    branch = src[start:src.index('elif act["type"] == "add":', start)]
    assert 'act.get("earnings_note")' in branch
    assert 'act.get("basis") == "ceiling"' in branch
    gain = branch.index("This locks in a portion of")
    assert branch.rindex('act.get("basis") == "ceiling"', 0, gain) > branch.index("if ts >= 1:")
    assert 'act.get("basis") == "earnings_week"' not in branch


# -- R2-N4: the other two date parses read the same first-10-chars string -----------------

def test_alerts_earnings_proximity_reads_a_timestamped_date():
    from stock_analyzer import portfolio as pf
    df = _book(15.0, 8, _PNLS[1])
    stamp = (pf._today_et() + timedelta(days=2)).isoformat() + " 16:00:00"
    out = pf.alerts(df, {"AAA": {"earnings": stamp}})
    msgs = [a["msg"] for a in out if a.get("category") == "earnings"]
    assert any("AAA" in m and "in **2 days**" in m for m in msgs), msgs


def test_alerts_earnings_proximity_skips_a_malformed_date():
    from stock_analyzer import portfolio as pf
    out = pf.alerts(_book(15.0, 8, _PNLS[1]), {"AAA": {"earnings": "soon"}})
    assert not [a for a in out if a.get("category") == "earnings"]


def test_signals_checklist_never_prints_a_nan_stop():
    # R2-N5: a young holding has no stop; the checklist says so instead of "Stop at $nan".
    src = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = src.index('gap = act["gap"]')
    seg = src[start:start + 1500]
    assert "No stop level available for this name." in seg
    assert "pd.isna(stop)" in seg and "pd.isna(gap)" in seg
    assert seg.index("No stop level available") < seg.index("elif gap < 4:")
