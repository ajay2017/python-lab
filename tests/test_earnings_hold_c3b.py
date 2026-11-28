"""Buy-check C3b (docs/plans/entry-fail-closed-c3-spec.md, owner amendments 2026-11-27):
a next earnings date that couldn't be confirmed HOLDS new buys and adds.

Covers `buy_check_state.earnings_unknown_hold` / `earnings_pass_note`, the shared entry
check (`check_earnings` order, `entry_check` binding, `_ABSTENTION_CHECKS`, the held label,
the Watchlist banner), the Watchlist card and the cron `checks_held`, Grow Today's
`earnings_unknown_picks` bucket (every return shape, bucket order, adds), its readers
(`grow_dropoff`, `evening_debrief`, the gate ledger), the redeploy headline (Q9), the sell-
side Review caption (Q6), and source pins on the app.py wiring no test imports.

Every stamp comes from the real resolver (`earnings_date.resolve`) with an injected `today`
(never the clock). Boundaries read `constants.py`, never a literal. Time-context matrix:
Friday / Thanksgiving / weekend, the 14-day boundary, report day, the day after (with and
without an FMP actual), a read after midnight, and the DST change days.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import buy_check_state as bcs
from stock_analyzer import constants as C
from stock_analyzer import earnings_date as ed
from stock_analyzer import entry_eligibility as ee
from stock_analyzer import entry_posture as ep
from stock_analyzer import evening_debrief, gate_ledger, grow_dropoff, sizing_path, trim_sizing
from stock_analyzer.daily_briefing import _earnings_unconfirmed_items, _grow_today
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation
from tests.conftest import make_port_df
from tests.test_beta_gate_grow_today import (
    HIGH_BOOK,
    HIGH_TICK,
    _add_scn,
    _fit,
    _pick_scn,
    _run,
    _tickers,
    _with_beta,
)
from tests.test_earnings_c3a import TODAY, _d, _res, _xc
from tests.test_grow_today_golden import _PCT, _TODAY, _bundle, _iso, _news, _scn
from tests.test_watchlist_entry_cron import FLAT_CTX, SEMIS, _bundle as _cron_bundle, _wle

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")

CRWD_YAHOO = "2026-11-23"
FAR = "2099-01-01"      # a real future date for the paths that read the wall clock
FLAT = {"tone": "flat", "sp500_pct": 0.1, "available": True, "source": "live", "as_of": None}
BULL = {"tone": "bull", "sp500_pct": 0.8, "available": True, "source": "live", "as_of": None,
        "why": None, "home_read_at": None}
CLEAR_MAP = {"sectors": set(), "reasons": {}}
GOOD_NEWS = {"compound": 0.1, "n": 3, "headline": "fine"}


def _crwd(today=TODAY, xc=None):
    """CRWD on the real 11-27 problem: Yahoo still says 11-23, FMP has nothing."""
    return _res(yahoo=[CRWD_YAHOO], xc=xc, today=today)


def _b(res, **extra):
    """A bundle carrying a resolved stamp (and its resolved `earnings`, like load_bundle)."""
    out = {"earnings_resolved": res, "earnings": res["date"]}
    out.update(extra)
    return out


# ═══ earnings_unknown_hold: wording, shape, passes ═══════════════════════════

def test_a_bundle_without_a_stamp_holds_nothing():
    assert bcs.earnings_unknown_hold({}, None) is None
    assert bcs.earnings_unknown_hold({"earnings": _d(3)}, 3) is None      # legacy bundle
    assert bcs.earnings_unknown_hold(None, None) is None
    assert bcs.earnings_unknown_hold("x", None) is None
    assert bcs.earnings_unknown_hold({"earnings_resolved": "x"}, None) is None


@pytest.mark.parametrize("res", [
    _res(yahoo=[], status="date", fund=True),                  # a fund
    _res(yahoo=[], status="no_date"),                          # a fresh listing
    _res(yahoo=[_d(-3)], xc=_xc(reported=_d(-3))),             # reported (FMP actual)
    _res(yahoo=[_d(-(C.EARNINGS_PAST_DATE_HOLD_DAYS + 1))]),   # older than the hold window
])
def test_the_pass_states_hold_nothing(res):
    assert bcs.earnings_unknown_hold(_b(res), None) is None


def test_an_etf_bundle_holds_nothing_even_with_a_failed_stamp():
    assert bcs.earnings_unknown_hold(
        _b(_crwd(), asset_type="etf"), None) is None


def test_the_failed_reasons_each_get_their_wording():
    h = bcs.earnings_unknown_hold(_b(_crwd()), None)
    assert h["reason"] == ("the earnings date on file (Mon Nov 23) has passed and the next "
                           "one couldn't be confirmed")
    h = bcs.earnings_unknown_hold(_b(_crwd(xc=_xc(state="failed"))), None)
    assert h["reason"] == ("the earnings date on file (Mon Nov 23) has passed and the "
                           "earnings calendar couldn't be read to confirm the next one")
    h = bcs.earnings_unknown_hold(_b(_res(yahoo=[], status="failed")), None)
    assert h["reason"] == "the next earnings date couldn't be read"
    h = bcs.earnings_unknown_hold(_b(_res(yahoo=[_d(3)], stale=True)), None)
    assert h["reason"] == "a saved copy of the data can't show the earnings date"


def test_the_hold_shape_is_an_abstention_with_no_ledger_id_and_no_markup():
    h = bcs.earnings_unknown_hold(_b(_crwd()), None)
    assert h["kind"] == "hold" and h["check"] == "earnings_unknown"
    assert h["gate_id"] is None and h["gate_value"] is None and h["gate_threshold"] is None
    n = C.EARNINGS_PAST_DATE_HOLD_DAYS
    assert h["clears_when"] == (
        "the next earnings date is confirmed, or on Tue Dec 8 "
        f"(more than {n} days after the date on file, when the app stops treating it as a recent "
        "report)")
    for txt in (h["reason"], h["clears_when"]):
        assert "$" not in txt and "**" not in txt


def test_clears_when_is_true_for_each_kind_of_hold():
    n = C.EARNINGS_PAST_DATE_HOLD_DAYS
    generic = "the next earnings date can be read (re-checked each time the data reloads)"
    # the age exit is the date on file + N + 1, in calendar days (no clock, no DST drift)
    for today in (date(2026, 11, 1), date(2027, 3, 14), TODAY):
        y = (today - timedelta(days=3)).isoformat()
        h = bcs.earnings_unknown_hold(_b(_res(yahoo=[y], today=today)), None)
        end = date.fromisoformat(y) + timedelta(days=n + 1)
        assert f"or on {end:%a %b} {end.day} (more than {n} days after" in h["clears_when"]
        assert "30 minutes" not in h["clears_when"]
    unchecked = bcs.earnings_unknown_hold(_b(_crwd(xc=_xc(state="failed"))), None)
    assert "or on Tue Dec 8" in unchecked["clears_when"]
    for res, days in ((_res(yahoo=[], status="failed"), None),
                      (_res(yahoo=[_d(3)], stale=True), None),
                      (_res(yahoo=[_d(3)]), None), (_res(yahoo=[_d(3)]), -1)):
        assert bcs.earnings_unknown_hold(_b(res), days)["clears_when"] == generic
    # the age exit is real: on that day the same Yahoo date passes
    on_day = _res(yahoo=[CRWD_YAHOO], today=date(2026, 12, 8))
    assert on_day["state"] == "reported_unconfirmed"
    assert _res(yahoo=[CRWD_YAHOO], today=date(2026, 12, 7))["state"] == "failed"


def test_a_date_state_holds_only_when_it_cannot_be_placed_or_has_passed():
    b = _b(_res(yahoo=[_d(3)]))
    assert b["earnings_resolved"]["state"] == "date"
    assert bcs.earnings_unknown_hold(b, 3) is None
    assert bcs.earnings_unknown_hold(b, 0) is None            # imminent is the policy hold's
    assert bcs.earnings_unknown_hold(b, C.EARNINGS_IMMINENT_DAYS + 1) is None
    h = bcs.earnings_unknown_hold(b, None)
    assert h["reason"] == "the earnings date couldn't be placed on the calendar"
    for bad in (float("nan"), True, "3"):
        assert bcs.earnings_unknown_hold(b, bad)["reason"] == (
            "the earnings date couldn't be placed on the calendar")
    h = bcs.earnings_unknown_hold(b, -1)
    d = date.fromisoformat(_d(3))
    assert h["reason"] == (f"the earnings date on file ({d:%a %b} {d.day}) has passed since "
                           "the data loaded")


def test_a_malformed_failed_stamp_still_holds():
    for er in ({"state": "failed"}, {"state": "banana"}, {"state": "failed", "reason": "past_unconfirmed"}):
        h = bcs.earnings_unknown_hold({"earnings_resolved": er}, None)
        assert h is not None and h["check"] == "earnings_unknown"


# ═══ earnings_pass_note ═══════════════════════════════════════════════════════

def test_pass_notes():
    assert bcs.earnings_pass_note(_b(_res(yahoo=[], status="no_date"))) == (
        "no earnings date announced yet")
    note = bcs.earnings_pass_note(_b(_res(yahoo=["2026-11-19"], xc=_xc(reported="2026-11-19"))))
    assert note == "reported on Thu Nov 19; the next date isn't on the calendar yet"
    old = bcs.earnings_pass_note(_b(_res(yahoo=[_d(-(C.EARNINGS_PAST_DATE_HOLD_DAYS + 1))])))
    assert old.endswith("; the next date isn't announced yet") and old.startswith("reported on ")
    dis = bcs.earnings_pass_note(_b(_res(yahoo=["2026-12-03"], xc=_xc(upcoming=["2026-12-01"]))))
    assert dis == "Yahoo shows Thu Dec 3, the FMP calendar Tue Dec 1; the earlier date is used."


def test_no_pass_note_for_a_plain_date_a_fund_a_failure_or_no_stamp():
    assert bcs.earnings_pass_note(_b(_res(yahoo=[_d(10)]))) is None
    assert bcs.earnings_pass_note(_b(_res(yahoo=[], fund=True))) is None
    assert bcs.earnings_pass_note(_b(_crwd())) is None
    assert bcs.earnings_pass_note({}) is None
    assert bcs.earnings_pass_note(None) is None


# ═══ check_earnings: binding order, notes, legacy ════════════════════════════

def test_the_imminent_policy_hold_binds_first_even_beside_an_unconfirmed_stamp():
    # a failed stamp with a stray imminent day count: the policy hold wins, and it is the
    # one that carries the existing wording (its ledger path is unchanged)
    r = ee.check_earnings({"bundle": _b(_crwd()), "earnings_days": 3})
    assert r["check"] == "earnings" and r["reason"] == "earnings in 3d"
    r0 = ee.check_earnings({"bundle": _b(_crwd()), "earnings_days": 0})
    assert r0["check"] == "earnings" and r0["reason"] == "earnings today"
    last = ee.check_earnings({"bundle": _b(_crwd()), "earnings_days": C.EARNINGS_IMMINENT_DAYS})
    assert last["check"] == "earnings"


def test_after_the_window_a_confirmed_date_passes_and_a_failed_one_holds():
    ok = _b(_res(yahoo=[_d(C.EARNINGS_IMMINENT_DAYS + 1)]))
    assert ee.check_earnings({"bundle": ok, "earnings_days": C.EARNINGS_IMMINENT_DAYS + 1}) is None
    r = ee.check_earnings({"bundle": _b(_crwd()), "earnings_days": None})
    assert r["kind"] == "hold" and r["check"] == "earnings_unknown"


def test_a_pass_note_is_a_passed_entry_not_a_hold():
    b = _b(_res(yahoo=[], status="no_date"))
    r = ee.check_earnings({"bundle": b, "earnings_days": None})
    assert r == {"kind": "passed", "check": "earnings",
                 "detail": "no earnings date announced yet"}


def test_a_legacy_bundle_and_no_bundle_are_unchanged():
    assert ee.check_earnings({"bundle": {}, "earnings_days": None}) is None
    assert ee.check_earnings({"bundle": None, "earnings_days": None}) is None
    assert ee.check_earnings({"earnings_days": 8}) is None
    assert ee.check_earnings({"bundle": {"earnings": _d(3)}, "earnings_days": 3})["check"] == "earnings"


def test_an_etf_with_a_not_applicable_stamp_passes_silently():
    b = _b(_res(yahoo=[], fund=True), asset_type="etf")
    assert ee.check_earnings({"bundle": b, "earnings_days": None}) is None


def _chk(bundle, **kw):
    base = dict(bundle=bundle, tone_state=FLAT, sector="Technology", macro_map=CLEAR_MAP,
                news=GOOD_NEWS, earnings_days=None)
    base.update(kw)
    return ee.entry_check(**base)


def test_alone_it_binds_with_no_ledger_id_and_is_an_abstention():
    r = _chk(_b(_crwd()))
    assert r["eligible"] is False and r["binding"]["check"] == "earnings_unknown"
    assert r["binding"]["gate_id"] is None
    assert "earnings_unknown" in ee._ABSTENTION_CHECKS
    from stock_analyzer.watchlist_advisor import _HOLD_SUPPRESSION_KIND
    assert _HOLD_SUPPRESSION_KIND.get("earnings_unknown") is None
    assert gate_ledger._WATCHLIST_KIND.get("earnings_unknown") is None


def test_negative_news_binds_before_it():
    r = _chk(_b(_crwd()), news={"compound": C.NEWS_SENTIMENT_NEGATIVE - 0.1, "n": 2,
                                "headline": "h"})
    assert [h["check"] for h in r["holds"]] == ["news", "earnings_unknown"]
    assert r["binding"]["check"] == "news"


def test_the_macro_hold_and_the_tone_hold_bind_before_it_keeping_their_ledger_ids():
    bm = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI (2026-12-10) — 3d away"}}
    r = _chk(_b(_crwd()), macro_map=bm, gate_ids={"tone": "G-29", "macro": "G-28"})
    assert r["binding"]["check"] == "macro" and r["binding"]["gate_id"] == "G-28"
    bear = {"tone": "bear", "sp500_pct": -1.2, "available": True, "source": "live",
            "as_of": None}
    r = _chk(_b(_crwd()), tone_state=bear, gate_ids={"tone": "G-29", "macro": "G-28"})
    assert r["binding"]["check"] == "tone" and r["binding"]["gate_id"] == "G-29"
    assert "earnings_unknown" in [h["check"] for h in r["holds"]]


def test_the_imminent_policy_hold_binds_before_it_in_the_ordered_checks():
    # the policy `earnings` check ends its own function with the imminent hold, so a
    # name with an imminent date never reaches the abstention; and with other abstentions
    # (the journal) beside it, the non-abstention binds
    r = _chk(_b(_crwd()), earnings_days=2, trades_read=False)
    assert r["binding"]["check"] == "earnings" and r["binding"]["gate_id"] is None
    r = _chk(_b(_crwd()), trades_read=False)
    assert {h["check"] for h in r["holds"]} == {"earnings_unknown", "trades_unread"}


def test_held_label_and_card_text():
    assert ee.HELD_KIND_LABELS["earnings_unknown"] == "earnings date unconfirmed"
    assert ee.held_label({"hold_kind": "earnings_unknown"}) == "Held: earnings date unconfirmed"


# ═══ the Watchlist banner ═════════════════════════════════════════════════════

def _held(ticker, *checks):
    return {"action": "ENTRY_HELD", "ticker": ticker,
            "holds": [{"check": c} for c in checks]}


def test_banner_lists_only_names_held_solely_by_it_and_honours_exclude():
    recs = [_held("AAA", "earnings_unknown"), _held("BBB", "earnings_unknown", "news"),
            _held("CCC", "earnings_unknown"), _held("DDD", "tone")]
    b = ee.watchlist_banner(BULL, CLEAR_MAP, recs, exclude={"ccc"})
    eu = b["earnings_unknown"]
    assert eu["title"] == "Earnings date unconfirmed — new entries held"
    assert eu["body"] == ("1 name would otherwise be Ready: AAA. Its next earnings date "
                          "couldn't be confirmed, and a buy just before a report is what "
                          "this check stops. Clears when the date is confirmed (re-checked "
                          "each time the data reloads); a date on file that passed more "
                          f"than {C.EARNINGS_PAST_DATE_HOLD_DAYS} days ago stops holding.")
    b2 = ee.watchlist_banner(BULL, CLEAR_MAP, recs)
    assert "2 names would otherwise be Ready: AAA, CCC. Their next earnings date" in (
        b2["earnings_unknown"]["body"])


def test_banner_is_none_when_nothing_is_held_by_it():
    assert ee.watchlist_banner(BULL, CLEAR_MAP, [])["earnings_unknown"] is None
    assert ee.watchlist_banner(
        BULL, CLEAR_MAP, [_held("DDD", "tone"), _held("EEE", "earnings_unknown", "news")]
    )["earnings_unknown"] is None
    assert ee.watchlist_banner(BULL, CLEAR_MAP, None)["earnings_unknown"] is None


# ═══ the Watchlist card, Analysis and the cron ═══════════════════════════════

def _data(**ov):
    d = {"total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0, "entry_lo": 95.0,
         "entry_hi": 100.0, "stop": 90.0, "targets": {"base": 130.0}, "earnings": None}
    d.update(ov)
    return d


def _ctx():
    return {"tone_state": FLAT, "macro_map": CLEAR_MAP, "news": GOOD_NEWS,
            "sector": "Technology"}


def test_enter_now_becomes_entry_held_with_the_earnings_label():
    card = build_watchlist_recommendation(
        "CRWD", _data(earnings_resolved=_crwd()), entry_ctx=_ctx())
    assert card["action"] == "ENTRY_HELD" and card["hold_kind"] == "earnings_unknown"
    assert card["suppression_kind"] is None                   # no ledger row
    assert "earnings date on file (Mon Nov 23) has passed" in card["hold_reason"]
    assert ee.held_label(card) == "Held: earnings date unconfirmed"
    assert card["clears_when"].startswith("the next earnings date is confirmed, or on ")


def test_a_confirmed_or_legacy_bundle_is_still_enter_now_control():
    ok = build_watchlist_recommendation(
        "CRWD", _data(earnings=FAR, earnings_resolved=_res(yahoo=[FAR])), entry_ctx=_ctx())
    assert ok["action"] == "ENTER_NOW"
    legacy = build_watchlist_recommendation("CRWD", _data(), entry_ctx=_ctx())
    assert legacy["action"] == "ENTER_NOW"
    assert "No imminent earnings risk" in legacy["conditions_met"]


def test_enter_now_card_names_a_pass_note_instead_of_no_imminent_risk():
    fresh = _res(yahoo=[], status="no_date")
    card = build_watchlist_recommendation(
        "NEWCO", _data(earnings_resolved=fresh), entry_ctx=_ctx())
    assert card["action"] == "ENTER_NOW"
    assert "Earnings: no earnings date announced yet" in card["conditions_met"]
    assert "No imminent earnings risk" not in card["conditions_met"]
    assert {"check": "earnings", "detail": "no earnings date announced yet"} in card["entry_passed"]


def test_analysis_gives_no_size_to_an_unconfirmed_buy():
    dec = ee.analysis_entry_hold(
        basis="equity", label="Buy", held=False, entry=_chk(_b(_crwd())))
    assert dec["offer_size"] is False
    assert dec["hold"]["check"] == "earnings_unknown"
    assert "earnings date on file" in dec["reason_if_no_size"]
    banner = ee.analysis_hold_banner(dec, FLAT)
    assert banner["title"].startswith("Not an entry today — held: the earnings date on file")
    assert ee.analysis_shares_label(dec) == "— (held)"


def test_cron_watchlist_counts_the_name_in_checks_held_and_holds_it():
    r, save, spy, _g, _l = _wle(
        entry_context=FLAT_CTX, bundle=_cron_bundle(earnings_resolved=_crwd()))
    assert r["entries"] == []
    assert spy[0][3]["action"] == "ENTRY_HELD" and spy[0][3]["hold_kind"] == "earnings_unknown"
    assert r["checks_held"]["earnings_unknown"] == [SEMIS]
    assert r["checks_held"]["tone_unread"] == []
    assert r["gate_ledger_rows"] == []
    save.assert_not_called()


def test_cron_control_a_confirmed_date_is_not_counted():
    r, *_ = _wle(entry_context=FLAT_CTX,
                 bundle=_cron_bundle(earnings=FAR, earnings_resolved=_res(yahoo=[FAR])))
    assert r["checks_held"]["earnings_unknown"] == []


def test_cron_a_held_in_portfolio_name_is_not_counted():
    r, *_ = _wle(entry_context=FLAT_CTX, held=(SEMIS,),
                 bundle=_cron_bundle(earnings_resolved=_crwd()))
    assert r["checks_held"]["earnings_unknown"] == []


def test_cron_scan_log_line_names_it():
    line = cr._watchlist_checks_held_line(
        {"checks_held": {"fit_unknown": [], "earnings_unknown": ["CRWD", "SNOW"]}})
    assert line == ("watchlist: 2 held, a check couldn't run "
                    "(earnings_unknown: CRWD, SNOW)")
    assert cr._watchlist_checks_held_line({"checks_held": {"earnings_unknown": []}}) is None


def test_cron_lane_is_not_made_red_by_it():
    src = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    seg = src[src.index("def _watchlist_checks_held_line"):src.index("def _log_watchlist_checks_held")]
    assert "failures" not in seg and "_notify" not in seg          # log only (Q8)
    assert '"earnings_unknown"' in seg


# ═══ Grow Today ═══════════════════════════════════════════════════════════════

def _failed(today=_TODAY, status="date"):
    return _res(yahoo=[(today - timedelta(days=4)).isoformat()], today=today, status=status)


def _picks_scn(**extra):
    s = _pick_scn(1.0)
    s["composites"] = {"ZNEWX": _bundle(80.0, earnings_resolved=_failed(), **extra),
                       "ZCTRL": _bundle(80.0)}
    return s


def test_a_pick_with_an_unconfirmed_date_is_held_unsized_and_unledgered():
    grow = _run(_picks_scn(), None)
    assert "ZNEWX" not in _tickers(grow["new_picks"]) and "ZCTRL" in _tickers(grow["new_picks"])
    item = next(i for i in grow["earnings_unknown_picks"] if i["ticker"] == "ZNEWX")
    assert item["gate_id"] is None and item["counterfactual"] is True
    assert item["kind"] == "earnings_unknown"
    assert "earnings date on file" in item["reason"] and item["clears_when"]
    assert item["gate_value"] is None and item["gate_threshold"] is None
    assert not any(k in item for k in ("sizing", "shares", "stop", "entry_lo"))
    assert "ZNEWX" not in _tickers(grow["overflow_picks"])
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert [r for r in rows if r["ticker"] == "ZNEWX"] == []
    assert "earnings_unknown_picks" not in gate_ledger._BUCKET_LANES


def test_a_mover_is_held_the_same_way():
    s = _scn(_PCT["bull"], rows=[], composites={"ZMOVE": _bundle(
        80.0, earnings_resolved=_failed())},
        movers=[{"ticker": "ZMOVE", "price": 100.0, "sector": "Energy", "score": 50.0,
                 "composite_score": 85.0, "day_change": 6.0}])
    grow = _run(s, None)
    assert _tickers(grow["earnings_unknown_picks"]) == ["ZMOVE"]
    assert grow["earnings_unknown_picks"][0]["is_mover"] is True
    assert grow["new_picks"] == []


def test_g06_binds_before_it_and_keeps_its_ledger_row():
    s = _pick_scn(HIGH_TICK)
    s["composites"]["ZNEWX"] = _with_beta(
        _bundle(80.0, earnings_resolved=_failed()), HIGH_TICK)
    grow = _run(s, _fit(HIGH_BOOK))
    assert _tickers(grow["beta_blocked_picks"]) == ["ZNEWX"]
    assert grow["earnings_unknown_picks"] == []
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert any(r["ticker"] == "ZNEWX" and r["gate_id"] == "G-06" for r in rows)


def test_an_imminent_known_date_is_earnings_waiting_not_earnings_unknown():
    soon = _res(yahoo=[_iso(3)], today=_TODAY)
    s = _pick_scn(1.0, earnings={"ZNEWX": _iso(3)})
    s["composites"]["ZNEWX"] = _bundle(80.0, earnings_resolved=soon, earnings=_iso(3))
    grow = _run(s, None)
    assert "ZNEWX" in _tickers(grow["earnings_waiting"])
    assert grow["earnings_unknown_picks"] == []


def test_negative_news_binds_before_it_g30_kept():
    s = _picks_scn()
    s["news"] = _news("ZNEWX", C.NEWS_SENTIMENT_NEGATIVE - 0.2, n=2)
    grow = _run(s, None)
    assert "ZNEWX" in _tickers(grow["news_skipped"])
    assert "ZNEWX" not in _tickers(grow["earnings_unknown_picks"])


def test_it_sits_after_the_beta_and_unpriced_blocks_in_the_source_order():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    body = src[src.index("def _grow_today"):src.index("# ── Act Today")]
    i_beta = body.index("beta_unknown_picks.append(_bd_item)")
    i_fit = body.index("fit_unpriced_picks.append({")
    i_earn = body.index("earnings_unknown_picks.append({")
    i_tier = body.index("# Conviction tier: drives the label shown on the card.")
    assert i_beta < i_fit < i_earn < i_tier
    # and it never feeds sizing / the G-32 source map / the picks list
    seg = body[i_earn:i_tier]
    assert "_g32_pick_src" not in seg and "new_picks.append" not in seg


def test_every_return_shape_carries_the_key():
    bear = _run(_scn(_PCT["bear"]), None)
    assert bear["tone"] == "bear" and bear["earnings_unknown_picks"] == []
    port_df = make_port_df([{"ticker": "ZHELD", "weight": 5.0, "sector": "Tech"}])
    unread = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0,
                         {"tone": "flat", "sp500_pct": 0.0, "tone_available": False,
                          "leading_sectors": []})
    assert unread["entry_hold"] is not None and unread["earnings_unknown_picks"] == []
    macro = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0,
                        {"tone": "bull", "sp500_pct": 0.8, "leading_sectors": []},
                        macro_unread=True)
    assert macro["entry_hold"] is not None and macro["earnings_unknown_picks"] == []
    clean = _run(_pick_scn(1.0), None)
    assert clean["earnings_unknown_picks"] == [] and clean["new_picks"]
    assert _run(_picks_scn(), None)["earnings_unknown_picks"] != []


def test_legacy_composites_without_a_stamp_change_nothing():
    base = _run(_pick_scn(1.0), None)
    assert base["earnings_unknown_picks"] == []
    assert {"ZNEWX", "ZCTRL"} <= set(_tickers(base["new_picks"]))


def _held_add_scn(res, **o):
    return _add_scn(1.0, held_data={"ZADDOK": {"risk_metrics": {"beta": 1.0},
                                                 "earnings_resolved": res,
                                                 "earnings": res["date"]}}, **o)


def test_an_add_with_an_unconfirmed_date_rides_entry_held_adds_and_is_paused():
    grow = _run(_held_add_scn(_failed()), None)
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["check"] == "earnings_unknown" and item["gate_id"] is None
    assert [h["check"] for h in item["holds"]] == ["earnings_unknown"]
    v = sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date=_TODAY, today=_TODAY, brief_epoch=1, epoch=1)
    assert v["state"] == "paused" and v["reason"] == item["reason"]
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert [r for r in rows if r["ticker"] == "ZADDOK"] == []


def test_an_add_with_negative_news_keeps_g30_and_lists_both_holds():
    s = _held_add_scn(_failed(), news=_news("ZADDOK", -0.40))
    grow = _run(s, None)
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["check"] == "news" and item["gate_id"] == "G-30"
    assert [h["check"] for h in item["holds"]] == ["news", "earnings_unknown"]


def test_an_add_control_a_confirmed_date_still_adds():
    grow = _run(_held_add_scn(_res(yahoo=[_d(60, _TODAY)], today=_TODAY)), None)
    assert "ZADDOK" in _tickers(grow["add_positions"])


def test_a_high_beta_add_held_only_by_the_stamp_is_g06_not_entry():
    # G-06 must not be hidden behind the earnings abstention (Opus FIX-FIRST)
    s = _add_scn(HIGH_TICK, held_data={"ZADDOK": {
        "risk_metrics": {"beta": HIGH_TICK}, "earnings_resolved": _failed(),
        "earnings": None}})
    grow = _run(s, _fit(HIGH_BOOK))
    assert "ZADDOK" in _tickers(grow["beta_blocked_adds"])
    assert "ZADDOK" not in _tickers(grow["entry_held_adds"])
    assert "ZADDOK" not in _tickers(grow["add_positions"])
    rows = gate_ledger.build_suppression_rows(
        grow, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert any(r["ticker"] == "ZADDOK" and r["gate_id"] == "G-06" for r in rows)
    v = sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date=_TODAY, today=_TODAY, brief_epoch=1, epoch=1)
    assert v["state"] == "paused"


def test_a_low_beta_add_with_only_the_stamp_stays_an_entry_hold():
    s = _add_scn(1.0, held_data={"ZADDOK": {
        "risk_metrics": {"beta": 1.0}, "earnings_resolved": _failed(), "earnings": None}})
    for fit in (None, _fit(HIGH_BOOK)):
        grow = _run(s, fit)
        item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
        assert item["check"] == "earnings_unknown" and item["gate_id"] is None
        assert "ZADDOK" not in _tickers(grow["beta_blocked_adds"])


def test_a_policy_hold_beside_the_stamp_keeps_entry_even_on_a_high_beta_book():
    s = _add_scn(HIGH_TICK, news=_news("ZADDOK", -0.40), held_data={"ZADDOK": {
        "risk_metrics": {"beta": HIGH_TICK}, "earnings_resolved": _failed(),
        "earnings": None}})
    grow = _run(s, _fit(HIGH_BOOK))
    item = next(i for i in grow["entry_held_adds"] if i["ticker"] == "ZADDOK")
    assert item["check"] == "news" and item["gate_id"] == "G-30"
    assert "ZADDOK" not in _tickers(grow["beta_blocked_adds"])


def test_adds_paused_agrees_with_the_bull_loop_on_a_non_bull_day():
    held = {"ZADDOK": {"risk_metrics": {"beta": HIGH_TICK}, "earnings_resolved": _failed(),
                       "earnings": None}}
    s = _adds_scn_flat(held)
    paused = _run(s, _fit(HIGH_BOOK))
    clear = _run(_adds_scn_flat({"ZADDOK": {"risk_metrics": {"beta": HIGH_TICK}}}),
                 _fit(HIGH_BOOK - 5))
    assert "ZADDOK" not in _tickers(paused["adds_paused"])
    assert "ZADDOK" in _tickers(clear["adds_paused"])


def _adds_scn_flat(held):
    from tests.test_grow_today_golden import _adds_scn
    return _adds_scn(_PCT["flat"], news=[], earnings={}, act_today=None,
                     review_list=None, held_data=held)


# ═══ readers of the bucket ═══════════════════════════════════════════════════

def test_grow_dropoff_attributes_it_after_the_unpriced_hold_and_before_pacing():
    surfaced = [{"ticker": t, "first_seen_at": "2026-10-15T14:00:00Z",
                 "composite_at_surface": 80.0} for t in ("AAA", "BBB", "CCC")]
    buckets = {
        "earnings_unknown_picks": [{"ticker": "AAA", "composite_score": 77.0,
                                    "reason": "R-earn"}],
        "fit_unpriced_picks": [{"ticker": "BBB", "reason": "R-fit"}],
        "overflow_picks": [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}],
    }
    out = {d["ticker"]: d for d in grow_dropoff.derive_dropoffs(
        surfaced, set(), buckets, None, set())}
    assert out["AAA"]["reason_code"] == "earnings_unknown" and out["AAA"]["reason_text"] == "R-earn"
    assert out["AAA"]["has_confident_reason"] and out["AAA"]["current_composite"] == 77.0
    assert out["BBB"]["reason_code"] == "fit_unpriced"          # the earlier hold wins
    assert out["CCC"]["reason_code"] == "pacing"
    # absent key (a bucket dict built before C3b): nothing attributed to it
    old = {d["ticker"]: d for d in grow_dropoff.derive_dropoffs(
        surfaced, set(), {"overflow_picks": [{"ticker": "AAA"}]}, None, set())}
    assert old["AAA"]["reason_code"] == "pacing"


def test_the_evening_debrief_counts_the_hold():
    assert ("earnings_unknown_picks", "earnings date unconfirmed") in (
        evening_debrief._HELD_BACK_OTHER)


def test_the_headless_diag_counts_it():
    src = (ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")
    assert '"earnings_unknown": len(grow.get("earnings_unknown_picks", []))' in src
    cron = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert "earnings date unconfirmed" in cron


# ═══ the redeploy headline (Q9) ══════════════════════════════════════════════

_HL = {"state": "entry", "ticker": "CRWD", "sector": "Tech", "score": 80.0, "rr": 2.5}
_BULL_POSTURE = ep.new_entry_posture(BULL)


def _call(bundle, today=TODAY, **kw):
    return ep.redeploy_call(dict(_HL), _BULL_POSTURE, bundle=bundle, today=today, **kw)


def test_an_unconfirmed_date_is_not_an_entry_call():
    out = _call(_b(_crwd()))
    assert out["entry_called"] is False and out["icon"] == "⚠️"
    assert "its next earnings date couldn't be confirmed, so it isn't called as an entry here." in out["text"]
    assert out["text"].startswith("**CRWD** (Tech) clears the Buy gate")
    assert "$" not in out["text"] and "genuine redeploy entry" not in out["text"]


@pytest.mark.parametrize("days,label", [(0, "today"), (3, "in 3d"),
                                        (C.EARNINGS_IMMINENT_DAYS, f"in {C.EARNINGS_IMMINENT_DAYS}d")])
def test_an_imminent_date_is_not_an_entry_call(days, label):
    b = _b(_res(yahoo=[_d(days)]))
    out = _call(b)
    assert out["entry_called"] is False
    assert f"earnings are {label}" in out["text"]
    assert "so it isn't called as an entry here." in out["text"]


def test_beyond_the_window_the_call_is_unchanged():
    b = _b(_res(yahoo=[_d(C.EARNINGS_IMMINENT_DAYS + 1)]))
    out = _call(b)
    assert out["entry_called"] is True and out["level"] == "success"
    assert "genuine redeploy entry" in out["text"]


def test_without_today_or_a_stamp_the_headline_is_unchanged():
    assert _call(_b(_crwd()), today=None)["entry_called"] is True        # skipped leg
    assert _call({})["entry_called"] is True                              # legacy bundle
    assert _call(None)["entry_called"] is True
    nodate = _b(_res(yahoo=[], status="no_date"))
    assert _call(nodate)["entry_called"] is True


def test_a_grow_hold_still_leads_the_redeploy_text():
    hold = {"reason": "the market move can't be read", "clears_when": "when it can"}
    out = _call(_b(_crwd()), grow_hold=hold)
    assert "🏠 Grow Today holds new entries" in out["text"]


def test_a_flat_posture_keeps_its_own_text():
    flat = ep.new_entry_posture({**BULL, "tone": "flat"})
    out = ep.redeploy_call(dict(_HL), flat, bundle=_b(_crwd()), today=TODAY)
    assert "isn't called as an entry here today" in out["text"]


# ═══ the sell side (Q6): one Review caption, no trade ════════════════════════

def _port(weight, n=10, ticker="CRWD", shares=100):
    rows = [{"ticker": ticker, "weight": weight, "shares": shares, "price": 100.0}]
    rows += [{"ticker": f"OT{i}", "weight": 5.0} for i in range(n - 1)]
    return make_port_df(rows)


def _over_floor(n=10):
    return trim_sizing.earnings_overweight_floor(n) + 3.0


def test_a_held_name_over_the_line_with_an_unconfirmed_date_gets_one_caption_item():
    w = _over_floor()
    held = {"CRWD": {"earnings_resolved": _crwd()}}
    items = _earnings_unconfirmed_items(_port(w), held, [])
    assert [i["ticker"] for i in items] == ["CRWD"]
    it = items[0]
    assert it["on_file"] == CRWD_YAHOO and it["reason"] == "past_unconfirmed"
    assert it["weight"] == pytest.approx(w)
    assert it["floor_pct"] == pytest.approx(trim_sizing.earnings_overweight_floor(10))
    assert it["line"].startswith("Earnings date unconfirmed for CRWD: the date on file "
                                 "(Mon Nov 23) has passed and no next date could be confirmed.")
    assert f"its {w:.1f}% is above your {it['floor_pct']:.1f}% earnings-week line" in it["line"]
    assert "$" not in it["line"] and "**" not in it["line"]


def test_at_or_under_the_line_there_is_no_caption():
    floor = trim_sizing.earnings_overweight_floor(10)
    held = {"CRWD": {"earnings_resolved": _crwd()}}
    assert _earnings_unconfirmed_items(_port(floor), held, []) == []          # equal: not above
    assert _earnings_unconfirmed_items(_port(floor - 1.0), held, []) == []
    assert _earnings_unconfirmed_items(_port(1.0), held, []) == []


def test_a_sub_share_trim_gets_no_caption():
    # over the line, but a 0.1-share holding floors to a 0-share trim
    held = {"CRWD": {"earnings_resolved": _crwd()}}
    assert _earnings_unconfirmed_items(_port(_over_floor(), shares=0.1), held, []) == []


@pytest.mark.parametrize("kind", ["stop_breach", "deterioration_exit"])
def test_a_fully_exited_name_is_skipped(kind):
    held = {"CRWD": {"earnings_resolved": _crwd()}}
    act = [{"ticker": "crwd", "kind": kind}]
    assert _earnings_unconfirmed_items(_port(_over_floor()), held, act) == []
    # a PARTIAL reduce is not a full exit: the caption still shows
    assert len(_earnings_unconfirmed_items(
        _port(_over_floor()), held, [{"ticker": "CRWD", "kind": "deterioration_trim"}])) == 1


def test_a_deterioration_exit_tier_is_a_full_exit_too():
    held = {"CRWD": {"earnings_resolved": _crwd()}}
    det = [{"ticker": "CRWD", "tier": "EXIT"}]
    assert _earnings_unconfirmed_items(_port(_over_floor()), held, [], det) == []
    assert len(_earnings_unconfirmed_items(
        _port(_over_floor()), held, [], [{"ticker": "CRWD", "tier": "WATCH"}])) == 1


def test_a_confirmed_date_a_legacy_bundle_and_odd_inputs_give_nothing():
    w = _over_floor()
    assert _earnings_unconfirmed_items(
        _port(w), {"CRWD": {"earnings_resolved": _res(yahoo=[_d(5)])}}, []) == []
    assert _earnings_unconfirmed_items(_port(w), {"CRWD": {"earnings": _d(5)}}, []) == []
    assert _earnings_unconfirmed_items(_port(w), {"CRWD": None}, []) == []
    assert _earnings_unconfirmed_items(None, {"CRWD": {}}, []) == []
    assert _earnings_unconfirmed_items(pd.DataFrame(), {"CRWD": {}}, []) == []
    assert _earnings_unconfirmed_items(_port(w), None, []) == []


def test_the_line_itself_says_nothing_at_or_under_the_floor():
    r = _crwd()
    assert ed.held_unconfirmed_line("CRWD", r, 8.0, 8.0) is None
    assert ed.held_unconfirmed_line("CRWD", r, 7.9, 8.0) is None
    assert ed.held_unconfirmed_line("CRWD", r, 8.1, 8.0) is not None


def test_no_trade_and_no_act_card_come_from_it():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    seg = src[src.index("def _earnings_unconfirmed_items"):src.index("# ── Act Today")]
    assert "items.append" not in seg and "act.append" not in seg and "TRIM" not in seg
    assert re.search(r"_earnings_unconfirmed_items\(\s*port_df, held_data, act, deterioration\)",
                     src)
    assert "risk_off_pool.full_exit_names(act_today, deterioration)" in seg


# ═══ the time-context matrix (today injected) ════════════════════════════════

@pytest.mark.parametrize("today", [date(2026, 11, 26),      # Thanksgiving (market closed)
                                   date(2026, 11, 27),      # Friday, the live case
                                   date(2026, 11, 28),      # Saturday
                                   date(2026, 11, 29)])     # Sunday
def test_crwd_stays_held_on_a_holiday_and_over_a_weekend(today):
    res = _crwd(today=today)
    assert res["state"] == "failed" and res["reason"] == "past_unconfirmed"
    assert bcs.earnings_unknown_hold(_b(res), None) is not None


def test_the_fourteen_day_boundary_day_14_holds_and_day_15_passes():
    n = C.EARNINGS_PAST_DATE_HOLD_DAYS
    held = _b(_res(yahoo=[_d(-n)]))
    assert held["earnings_resolved"]["state"] == "failed"
    assert bcs.earnings_unknown_hold(held, None) is not None
    passes = _b(_res(yahoo=[_d(-(n + 1))]))
    assert passes["earnings_resolved"]["state"] == "reported_unconfirmed"
    assert bcs.earnings_unknown_hold(passes, None) is None
    assert ee.check_earnings({"bundle": passes, "earnings_days": None})["kind"] == "passed"


@pytest.mark.parametrize("today", [date(2026, 11, 1), date(2027, 3, 14)])   # DST change days
def test_the_boundary_is_calendar_days_across_a_dst_change(today):
    n = C.EARNINGS_PAST_DATE_HOLD_DAYS
    on = _res(yahoo=[(today - timedelta(days=n)).isoformat()], today=today)
    off = _res(yahoo=[(today - timedelta(days=n + 1)).isoformat()], today=today)
    assert on["state"] == "failed" and off["state"] == "reported_unconfirmed"


def test_the_report_day_itself_is_the_imminent_hold_all_day():
    rd = date(2026, 12, 1)                                         # CRWD, after the close
    res = _res(yahoo=["2026-12-01"], today=rd)
    assert res["state"] == "date"
    r = ee.check_earnings({"bundle": _b(res), "earnings_days": 0})
    assert r["check"] == "earnings" and r["reason"] == "earnings today"


def test_the_day_after_a_report_passes_with_an_actual_and_holds_without():
    nxt = date(2026, 12, 2)
    with_actual = _res(yahoo=["2026-12-01"], today=nxt, xc=_xc(reported="2026-12-01"))
    assert with_actual["state"] == "reported"
    assert ee.check_earnings({"bundle": _b(with_actual), "earnings_days": None})["kind"] == "passed"
    without = _res(yahoo=["2026-12-01"], today=nxt)
    assert without["state"] == "failed"
    r = ee.check_earnings({"bundle": _b(without), "earnings_days": None})
    assert r["kind"] == "hold" and r["check"] == "earnings_unknown"


def test_a_date_resolved_late_and_read_after_midnight_holds_until_reload():
    # resolved at 23:50 for a report on that same day (days 0); the page reads at 00:10 with
    # the NEW day's count (-1): the stale stamp must hold, not pass
    d0 = date(2026, 12, 1)
    stamp = _b(_res(yahoo=[d0.isoformat()], today=d0))
    assert stamp["earnings_resolved"]["state"] == "date"
    h = bcs.earnings_unknown_hold(stamp, -1)
    assert h is not None and "has passed since the data loaded" in h["reason"]
    assert ee.check_earnings({"bundle": stamp, "earnings_days": -1})["check"] == "earnings_unknown"


def test_a_late_logged_trade_has_no_bearing():
    # the hold reads only the resolved stamp and the day count: no trade / journal input
    import inspect
    assert "trade" not in inspect.getsource(bcs.earnings_unknown_hold).lower().replace("trade-", "")


# ═══ app.py wiring pins (no test imports app.py) ═════════════════════════════

def test_the_schema_version_is_45_and_names_the_hold():
    m = re.search(r"_SYNTH_SCHEMA_VER = 45  # bumped: ([^;]*); was 44:", APP)
    assert m and "earnings_unknown_picks" in m.group(1)


def test_app_renders_the_bucket_the_banner_and_the_caption():
    assert '"fit_unpriced_picks", "earnings_unknown_picks"):' in APP        # grow_shown set
    assert 'earnings_unknown_picks = list(grow.get("earnings_unknown_picks", []))' in APP
    assert '("⏸ Earnings date unconfirmed", earnings_unknown_picks),' in APP
    assert '("⏸ earnings date unconfirmed", grow.get("earnings_unknown_picks", [])),' in APP
    assert '"earnings_unknown_picks": grow.get("earnings_unknown_picks", []),' in APP
    assert '_wl_banner["earnings_unknown"]' in APP
    assert '_daily_brief.get("earnings_unconfirmed")' in APP
    assert "buy_check_state.CHECK_EARNINGS_UNKNOWN" in APP


def test_both_redeploy_callers_pass_today():
    calls = [m.start() for m in re.finditer(r"_entry_posture\.redeploy_call\(", APP)]
    assert len(calls) == 2
    for i in calls:
        seg = APP[i:i + 1400]
        assert "today=_today_et()" in seg.split("_own_pick", 1)[0]
