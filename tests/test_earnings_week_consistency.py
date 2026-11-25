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


def _book(weight, n, pnl, shares=_SHARES, score=70.0):
    rows = [{
        "Ticker": "AAA", "Weight (%)": weight, "P&L (%)": pnl, "Price": _PRICE,
        "Shares": shares, "Signal": "Hold", "Score": score, "Stop": _PRICE * 0.9,
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
def test_weak_score_variant_the_earnings_card_is_covered_by_the_one_act_trim(n):
    """U6 (docs/plans/brief-one-trim-per-name-u6.md): the matrix above uses score 70 (no
    weak-large rule). With score 50 the weak-large trim to 8% sells at least what any
    earnings decision sells, so in the window the 📅 card is a Watch covered by that one
    Act trim, and the Act trim is never smaller than the earnings decision's shares."""
    for w in _weights(n):
        for days in _DAYS:
            if not ts.in_earnings_window(days):
                continue
            where = f"n={n} w={w} days={days}"
            items = _review_list(_book(w, n, _PNLS[1], score=50.0), [], [], _held(days), _TODAY,
                                 portfolio_value=100_000.0)
            acts = [i for i in items if i["ticker"] == "AAA"
                    and i["action"]["type"] in ("TRIM_TO_TARGET", "TRIM_AND_TIGHTEN", "PROTECTIVE_TRIM")]
            assert len(acts) == 1, where
            assert acts[0]["action"]["reason_key"] == "weak_large", where
            dec = ts.earnings_week_decision(w, n, _SHARES)
            if dec is not None:
                assert acts[0]["action"]["trim_shares"] >= dec["trim_shares"], where
            e = next(i for i in items if i["ticker"] == "AAA" and i["icon"] == "📅")
            assert e["action"] == {"type": "WATCH", "covered_by": "AAA"}, where


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
        f"Weight {w:.1f}% is at or under your {floor:.1f}% position-count-adjusted earnings-week "
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
            f"Weight {w:.1f}% with earnings in 3d: at or under the {floor:.1f}% earnings-week line "
            f"but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger, so the "
            f"standing trim to {SINGLE_NAME_CEILING:.0f}% applies")
        assert a["earnings_note"] == (
            f"Earnings in 3d: at or under the {floor:.1f}% earnings-week line, so the standing "
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


# -- EW C3 / S2: the Tune-up single-name card defers to the Brief's sell -------------------

def _sn_rec(w):
    return {
        "type": "single_name_concentration", "priority": "HIGH", "title": f"AAA is {w:.1f}%",
        "problem": "p", "recommendation": "Trim AAA toward the 15% ceiling",
        "root_tickers": [{"ticker": "AAA"}],
    }


def _brief(w, n, days, pnl=_PNLS[1]):
    from stock_analyzer.daily_briefing import build_daily_briefing
    return build_daily_briefing(
        port_df=_book(w, n, pnl), alert_list=[], risk_recs=[_sn_rec(w)], news_items=[],
        macro_events=[], held_data=_held(days), scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY, market_context={"tone": "bull"},
    )


def _brief_sells_aaa(brief):
    from stock_analyzer import held_trim_owner as hto
    return "AAA" in hto.brief_sell_tickers(brief["act_today"], brief["review_list"])


@pytest.mark.parametrize("n", range(5, 13))
def test_s2_single_name_card_defers_exactly_when_the_brief_sells_the_name(n):
    from stock_analyzer.constants import WEAK_CONVICTION_SCORE
    assert 70.0 >= WEAK_CONVICTION_SCORE            # the book's AAA is not a weak-large holding
    for w in _weights(n):
        if w < SINGLE_NAME_CEILING:
            continue
        for days in _DAYS:
            where = f"n={n} w={w} days={days}"
            brief = _brief(w, n, days)
            dec = ts.earnings_week_decision(w, n, _SHARES) if ts.in_earnings_window(days) else None
            sold = dec is not None and dec["trim_shares"] >= 1
            cards = [c for c in brief["portfolio_tuneup"] if c["type"] == "single_name_concentration"]
            assert isinstance(brief["portfolio_tuneup_deferred"], dict), where
            if sold:
                assert _brief_sells_aaa(brief), where
                assert cards == [], where
                assert list(brief["portfolio_tuneup_deferred"]) == ["AAA"], where
            else:
                assert not _brief_sells_aaa(brief), where
                assert len(cards) == 1 and brief["portfolio_tuneup_deferred"] == {}, where
                # a note only when the window holds a known weight under the earnings-week line
                in_win_no_dec = ts.in_earnings_window(days) and dec is None
                assert ("earnings_note" in cards[0]) == in_win_no_dec, where


def test_s2_boundary_n10_w15_keeps_the_card_with_a_note_w15_1_defers():
    # N=10 -> earnings-week line 15.0 (== SINGLE_NAME_CEILING): 15.0 is NOT above it
    keep = _brief(15.0, 10, 3)
    cards = [c for c in keep["portfolio_tuneup"] if c["type"] == "single_name_concentration"]
    assert len(cards) == 1 and "earnings_note" in cards[0]
    assert keep["portfolio_tuneup_deferred"] == {} and not _brief_sells_aaa(keep)
    over = _brief(15.1, 10, 3)
    assert [c for c in over["portfolio_tuneup"] if c["type"] == "single_name_concentration"] == []
    assert list(over["portfolio_tuneup_deferred"]) == ["AAA"] and _brief_sells_aaa(over)


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


def _app_src():
    return Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")


def test_home_tuneup_settles_the_deferral_against_the_act_view_and_escapes_the_note():
    src = _app_src()
    assert '_db_tuneup_deferred = _daily_brief.get("portfolio_tuneup_deferred", {})' in src
    # R3-B1: reconciled against `_act_view` (built earlier on the page), and the cards replaced
    # so the Tune-up count and the render agree
    recon = src.index("held_trim_owner.reconcile_tuneup(")
    assert src.index("_act_view = act_today_view(") < recon
    seg0 = src[recon:recon + 400]
    assert "held_trim_owner.view_sell_tickers(_act_view)" in seg0
    assert '_db_tuneup = _db_tuneup_rec["cards"]' in src[recon:recon + 600]
    assert recon < src.index("_tuneup_n   = len(_db_tuneup)")
    start = src.index("if _db_tuneup:")
    seg = src[start:src.index("# News Sentiment Shift", start)]
    # the earnings note goes through safe_html, as a string only
    assert "_safe_html(_tu['earnings_note'])" in seg
    assert 'isinstance(_tu.get("earnings_note"), str)' in seg
    # one caption per surviving deferral, sorted, outside the `if _db_tuneup:` card block
    assert "for _tud_t in sorted(_db_tuneup_deferred_texts)" in seg
    assert seg.index("for _tud_t in sorted(_db_tuneup_deferred_texts)") > seg.index("act on them when you're")
    assert "_db_tuneup_deferred or" not in src


def test_risk_analysis_single_name_branch_reads_the_act_today_view_with_the_verified_check():
    src = _app_src()
    start = src.index('if _rtype == "single_name_concentration":')
    seg = src[start:src.index("_left, _right = st.columns([1, 1])", start)]
    assert "held_trim_owner.risk_analysis_single_name_line(" in seg
    # R3-B1: the sells come from the view Home renders, never the raw `_reduce_calls` keys
    assert "held_trim_owner.view_sell_tickers(_sn_view)" in seg
    assert "act_today_view(" in seg and '"_home_synth_cache"' in seg
    assert 'session_state.get("_reduce_calls")' not in seg
    assert "sell_tickers_from_reduce_calls" not in src
    assert '_coord_cache_state("_reduce_calls") == "ready"' in seg
    assert '"_daily_brief_offline"' in seg
    assert "coord_freshness.classify(" in seg and "coord_freshness.FRESH" in seg
    assert '"_last_port_df"' in seg and '"_last_held_data"' in seg
    # unverified => None (the pure helper then says "unverified"), never an empty set
    assert "_sn_sell = None" in seg and "if _sn_verified:" in seg
    # a cross-check must never take the Action Plan down, and a failure leaves a trace (R3-N4)
    assert "except Exception as _sn_err:" in seg
    assert "getLogger(__name__).warning(" in seg
    assert "pass" not in seg.split("except Exception as _sn_err:")[1]


# -- R3-B1: a recovered stop breach is not a sell (the view demotes it after the build) ----

def _breach_book(w=19.0, n=8):
    df = _book(w, n, _PNLS[1])
    df.loc[0, "Price"], df.loc[0, "Stop"], df.loc[0, "Gap to Stop (%)"] = 40.0, 45.0, -11.0
    return df


def _breach_brief(w=19.0, n=8):
    from stock_analyzer.daily_briefing import build_daily_briefing
    return build_daily_briefing(
        port_df=_breach_book(w, n), alert_list=[], risk_recs=[_sn_rec(w)], news_items=[],
        macro_events=[], held_data={"AAA": {}}, scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY, market_context={"tone": "bull"},
    )


def _live_view(brief, gap):
    from stock_analyzer.act_today_view import act_today_view
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    live = _breach_book()
    live.loc[0, "Gap to Stop (%)"] = gap
    return act_today_view(brief, False, live, {}, STOP_RECOVERY_MARGIN_PCT)


def test_r3_b1_active_breach_keeps_the_deferral_and_risk_analysis_says_follow_the_call():
    from stock_analyzer import held_trim_owner as hto
    brief = _breach_brief()
    assert [c for c in brief["portfolio_tuneup"] if c["type"] == "single_name_concentration"] == []
    assert list(brief["portfolio_tuneup_deferred"]) == ["AAA"]
    sells = hto.view_sell_tickers(_live_view(brief, -11.0))
    assert sells == {"AAA"}
    rec = hto.reconcile_tuneup(brief["portfolio_tuneup"], brief["portfolio_tuneup_deferred"], sells)
    assert rec["cards"] == [] and list(rec["deferred_texts"]) == ["AAA"]
    inp = hto.book_inputs(_breach_book(), "AAA")
    line = hto.risk_analysis_single_name_line(
        "AAA", sells, {}, inp["weight_pct"], inp["n_positions"], inp["shares"])
    assert line["state"] == "brief_owns"


def test_r3_b1_recovered_breach_restores_the_card_with_no_caption_and_no_follow_the_call():
    from stock_analyzer import held_trim_owner as hto
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    brief = _breach_brief()
    view = _live_view(brief, STOP_RECOVERY_MARGIN_PCT + 5.0)
    assert [i["ticker"] for i in view["resolved"]] == ["AAA"] and view["active"] == []
    sells = hto.view_sell_tickers(view)
    assert sells == set()
    rec = hto.reconcile_tuneup(brief["portfolio_tuneup"], brief["portfolio_tuneup_deferred"], sells)
    assert [c["type"] for c in rec["cards"]] == ["single_name_concentration"]
    assert rec["deferred_texts"] == {}
    inp = hto.book_inputs(_breach_book(), "AAA")
    line = hto.risk_analysis_single_name_line(
        "AAA", sells, {}, inp["weight_pct"], inp["n_positions"], inp["shares"])
    assert line["state"] != "brief_owns"


def test_r3_b1_offline_view_restores_the_card_and_risk_analysis_says_unverified():
    from stock_analyzer import held_trim_owner as hto
    from stock_analyzer.act_today_view import act_today_view
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    brief = _breach_brief()
    view = act_today_view(brief, True, _breach_book(), {}, STOP_RECOVERY_MARGIN_PCT)
    sells = hto.view_sell_tickers(view)
    assert sells is None
    rec = hto.reconcile_tuneup(brief["portfolio_tuneup"], brief["portfolio_tuneup_deferred"], sells)
    assert len(rec["cards"]) == 1 and rec["deferred_texts"] == {}
    assert hto.risk_analysis_single_name_line("AAA", sells, {}, 19.0, 8, 100)["state"] == "unverified"


def test_r3_b1_a_risk_attribution_plus_a_review_trim_on_one_name_reads_brief_owns():
    # the old `_reduce_calls` first-item-per-ticker gap: an Act `risk` item must not hide the trim
    from stock_analyzer import held_trim_owner as hto
    view = {"state": "act", "resolved": [], "aware": [], "n_active": 2, "active": [
        {"kind": "risk", "ticker": "AAA"},
        {"ticker": "AAA", "action": {"type": "TRIM_TO_TARGET"}},
    ]}
    sells = hto.view_sell_tickers(view)
    assert hto.risk_analysis_single_name_line("AAA", sells, {}, 19.0, 8, 100)["state"] == "brief_owns"


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
