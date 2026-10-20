"""Chunk 3 of docs/plans/daily-new-position-budget.md -- the cron/email wiring
of the daily new-position budget (the pure decision lives in
tests/test_daily_budget.py; the DB layer in tests/test_db_budget_allocations.py).

What is pinned here, against the REAL notify renderers and the REAL
`_run_scan` / `_run_intraday` lanes with only I/O mocked:
  * a binding day: the second candidate is sized from what is LEFT, and that
    smaller size is what the email AND the rec-log row show;
  * joint composite-first allocation over the #1 pick and the watchlist entries;
  * Watchlist failure isolation (picks still allocated and emailed);
  * allocation-write failure: emails still go, footer shown, lane fails;
  * strict trades read: a failed read withholds every consuming size but the
    BUY still goes out;
  * an existing allocation is reused (reserved) and never re-written;
  * intraday: a new name is allocated from what is left and written;
  * SIZING_FORMULA_VERSION pin, rec_budget_state key hygiene.
Every "withheld" test has a firing control.
"""
import datetime
import html as _html

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import daily_budget, hold_reasons, notify
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.daily_briefing import SIZING_FORMULA_VERSION

NOW = cr._ET.localize(datetime.datetime(2026, 10, 16, 9, 50))   # a Friday
TODAY = NOW.date()
_TRADE_COLS = ["traded_at", "action", "ticker", "shares", "price"]

# net capital 10,000 -> budget 40% = 4,000; one name at 24 sh x $100 = 24%.
BUDGET_INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0,
                 "basis_state": "levered", "cash_balance": -4000.0,
                 "cash_as_of": "2026-10-15T20:00:00+00:00"}


# Cash record stamped AFTER today's logged trades (09:45 ET), so no lag correction.
FRESH_CASH = dict(BUDGET_INPUTS, cash_as_of="2026-10-16T13:45:00+00:00")


def _sizing(price=100.0, shares=24):
    return {"shares": shares, "total_cost": round(shares * price, 2),
            "stop": 94.0, "stop_pct": 6.0, "port_pct": 17.1, "risk_budget": 100.0,
            "entry_lo": 99.0, "entry_hi": 101.0, "ceiling_capped": False,
            "uncapped_shares": shares, "portfolio_value": 14000.0,
            "sizing_version": 3, "capital_capped": False, "capital_pct": 24.0}


def _pick(tk, comp, shares=24, price=100.0):
    return {"ticker": tk, "composite_score": comp, "score": 70.0, "price": price,
            "sector": "Tech", "conviction": "High", "thesis": "t",
            "asset_type": "stock",
            "xref": {"verdict": "go", "verdict_reconciled": {"verdict": "go"}},
            "sizing": _sizing(price, shares)}


def _entry(tk, comp, shares=24, price=100.0):
    return {"ticker": tk, "score": comp, "price": price, "rr": 2.5, "stop": 94.0,
            "entry_lo": 99.0, "entry_hi": 101.0, "summary": "s",
            "sizing": _sizing(price, shares), "economics": {},
            "net_capital": 10000.0, "capital_basis": "levered"}


def _ok_save(rows):
    return {"attempted": len(rows), "saved": len(rows), "error": None,
            "dropped_invalid": 0, "skipped_non_trading": 0, "inserted": len(rows)}


_NO_ROWS = object()


class Scan:
    """Harness for cron_runner._run_scan with only I/O mocked. `allocations`:
    a dict of today's existing allocations (default none), or None for an
    UNREADABLE allocations table. `trades`: "empty", a frame, or None
    (unreadable)."""

    def __init__(self, monkeypatch, picks, entries=None, *, allocations=_NO_ROWS,
                 trades="empty", save=_ok_save, wl_raises=False,
                 budget_inputs=BUDGET_INPUTS, wl_unavailable=False):
        from datetime import date as _date
        from stock_analyzer import reference_data as rd
        from stock_analyzer import scanner as real_scanner
        from stock_analyzer import gate_ledger
        self.emails = {}
        self.rec_rows = []
        self.saved_alloc = []
        self.notified = []
        entries = entries or []
        allocations = {} if allocations is _NO_ROWS else allocations
        mp = monkeypatch
        mp.setattr(cr, "is_trading_day", lambda _d: True)
        mp.setattr(rd, "resolve_universe_or_none",
                   lambda name: ({"Tech": ["AAA"]}, _date(2026, 9, 1), None))
        mp.setattr(real_scanner, "scan_sectors",
                   lambda *a, **k: pd.DataFrame({"Ticker": ["AAA"], "Score": [70.0]}))
        mp.setattr(cr, "_build_context", lambda _d: {"ok": True, "errors": []})
        mp.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: True)
        payload = {"picks": picks, "errors": [], "built_at": "2026-10-16T09:45:00",
                   "diag": {"tone": "bull"}, "book_drift": None,
                   "macro_coverage": None, "grow": {},
                   "budget_inputs": budget_inputs}
        mp.setattr(cr, "compute_morning_picks", lambda **_k: payload)

        def _wl(**_k):
            if wl_raises:
                raise RuntimeError("watchlist boom")
            if wl_unavailable:
                return {"entries": None, "built_at": "x", "errors": ["wl down"],
                        "reason": "db_unavailable", "gate_degraded": False}
            return {"entries": entries, "built_at": "2026-10-16T09:45:00",
                    "errors": [], "reason": None, "gate_degraded": False}
        mp.setattr(cr, "compute_watchlist_entries", _wl)
        mp.setattr(cr, "_handle_db_unavailable", lambda *a, **k: 1)

        def _save_recs(rows):
            self.rec_rows.append(list(rows))
            return {"attempted": len(rows), "saved": len(rows), "error": None}
        mp.setattr(cr.db, "save_recommendations", _save_recs)
        mp.setattr(gate_ledger, "build_suppression_rows", lambda *_a, **_k: [])
        mp.setattr(cr.db, "save_gate_suppressions",
                   lambda _r: {"attempted": 0, "saved": 0, "error": None})
        mp.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: pd.DataFrame())
        mp.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
        mp.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
        mp.setattr(cr, "_send_email",
                   lambda label, subject, html: self.emails.__setitem__(label, html) or True)
        mp.setattr(cr, "_notify_failure",
                   lambda lane, detail: self.notified.append((lane, detail)))
        # -- budget I/O
        trades_df = (pd.DataFrame(columns=_TRADE_COLS)
                     if isinstance(trades, str) else trades)
        mp.setattr(cr.db, "load_trades_or_none", lambda: trades_df)
        # A readable LENIENT loader: if the lane ever used it instead of the
        # strict one, a None strict read would silently become "nothing bought".
        mp.setattr(cr.db, "load_trades", lambda: pd.DataFrame(columns=_TRADE_COLS))
        mp.setattr(cr.db, "load_budget_allocations_or_none",
                   lambda day: (None if allocations is None else dict(allocations)))

        def _save_alloc(rows):
            self.saved_alloc.append([dict(r) for r in rows])
            return save(rows)
        mp.setattr(cr.db, "save_budget_allocations", _save_alloc)
        self.rc = cr._run_scan(NOW, force=True)

    @property
    def written(self):
        return [r for batch in self.saved_alloc for r in batch]

    def rec_row(self, tk):
        for batch in self.rec_rows:
            for r in batch:
                if r.get("ticker") == tk and r.get("rec_type") == "new_pick":
                    return r
        raise AssertionError(f"no new_pick rec row for {tk}")


def _esc(t):
    return _html.escape(t)


# ── 1. binding day ────────────────────────────────────────────────────────────

def test_binding_day_watchlist_entry_is_sized_from_what_is_left_in_the_email(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)])
    assert s.rc == 0
    wl = s.emails["watchlist-entries"]
    assert "Suggested size:</b> 16 shares" in wl                # 4,000 - 2,400 = 1,600
    assert "Suggested size:</b> 24 shares" not in wl
    assert _esc(hold_reasons.budget_note(
        hold_reasons.BUDGET_PARTIAL, {"n": 16, "m": 24})) in wl
    assert "24 shares" in s.emails["morning-action"]            # #1 pick keeps its size
    assert {r["ticker"]: r["shares"] for r in s.written} == {"AAA": 24, "WLB": 16}


def test_non_binding_day_leaves_both_sizes_untouched(monkeypatch):
    """Control for the binding test: 10% + 10% fits the 40% budget."""
    s = Scan(monkeypatch, [_pick("AAA", 80, shares=10)], [_entry("WLB", 70, shares=10)])
    assert "10 shares" in s.emails["watchlist-entries"]
    assert "Sized from what" not in s.emails["watchlist-entries"]
    assert {r["ticker"]: r["shares"] for r in s.written} == {"AAA": 10, "WLB": 10}


def test_binding_day_non_emailed_pick_rec_row_carries_the_budget_size(monkeypatch):
    """Two Go picks at 24% each. Only the #1 pick's size is emailed; the other
    is sized from what's left for the rec-log -- and NOT written as an allocation."""
    s = Scan(monkeypatch, [_pick("AAA", 80), _pick("BBB", 70)])
    a, b = s.rec_row("AAA"), s.rec_row("BBB")
    assert a["rec_shares"] == 24 and a["rec_sizing_version"] == daily_budget.BUDGET_SIZING_VERSION
    assert "rec_budget_state" not in a                  # untouched by the budget
    assert b["rec_shares"] == 16
    assert b["rec_budget_state"] == hold_reasons.BUDGET_PARTIAL
    assert b["rec_sizing_version"] == daily_budget.BUDGET_SIZING_VERSION
    assert [r["ticker"] for r in s.written] == ["AAA"]   # BBB's size was never pushed


def test_exhausted_pick_keeps_its_buy_call_and_gets_null_rec_shares(monkeypatch):
    """Budget already spent by logged buys: the call stands, the size is withheld."""
    spent = pd.DataFrame([{"traded_at": "2026-10-16T13:30:00+00:00", "action": "BUY",
                           "ticker": "ZZZ", "shares": 40, "price": 100.0}])
    s = Scan(monkeypatch, [_pick("AAA", 80)], trades=spent, budget_inputs=FRESH_CASH)
    html = s.emails["morning-action"]
    assert "AAA" in html and "Buy $99.00" in html          # the BUY still goes out
    assert "24 shares" not in html
    assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_EXHAUSTED)) in html
    r = s.rec_row("AAA")
    assert r.get("rec_shares") is None and r["rec_budget_state"] == hold_reasons.BUDGET_EXHAUSTED
    assert r["rec_sizing_version"] == daily_budget.BUDGET_SIZING_VERSION
    assert s.written == []                                   # nothing pushed, nothing recorded


# ── 2. joint, composite-first allocation ─────────────────────────────────────

def test_joint_allocation_is_composite_first_across_pick_and_watchlist(monkeypatch):
    hi_entry = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 90)])
    got = {r["ticker"]: r["shares"] for r in hi_entry.written}
    assert got == {"WLB": 24, "AAA": 16}                     # entry outranks the pick


def test_joint_allocation_control_pick_first_when_pick_outranks(monkeypatch):
    lo_entry = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 60)])
    got = {r["ticker"]: r["shares"] for r in lo_entry.written}
    assert got == {"AAA": 24, "WLB": 16}


def test_written_allocation_rows_carry_source_basis_and_frozen_pct(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 60)])
    rows = {r["ticker"]: r for r in s.written}
    assert rows["AAA"]["source"] == "scan:morning"
    assert rows["WLB"]["source"] == "scan:watchlist"
    from stock_analyzer.constants import DAILY_NEW_POSITION_BUDGET_PCT
    for r in rows.values():
        assert r["budget_pct"] == DAILY_NEW_POSITION_BUDGET_PCT
        assert r["basis_dollars"] == pytest.approx(10000.0)
        assert r["budget_date"] == TODAY and type(r["budget_date"]).__name__ == "date"
    assert rows["AAA"]["dollars"] == pytest.approx(2400.0)


# ── 3. Watchlist failure isolation ───────────────────────────────────────────

def test_watchlist_failure_still_allocates_and_emails_the_picks(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)], wl_raises=True)
    assert s.rc == 0
    assert "watchlist-entries" not in s.emails
    assert "24 shares" in s.emails["morning-action"]
    assert [r["ticker"] for r in s.written] == ["AAA"]


def test_watchlist_failure_control_healthy_watchlist_is_emailed(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)])
    assert "watchlist-entries" in s.emails


def test_watchlist_outage_does_not_block_the_picks_email(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], wl_unavailable=True)
    assert "24 shares" in s.emails["morning-action"]
    assert s.rc == 1                       # outage still routed through the handler


# ── 4. allocation-write failure ──────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"attempted": 2, "saved": 0, "error": "boom", "dropped_invalid": 0},
    {"attempted": 2, "saved": 0, "error": None, "dropped_invalid": 0},     # no-DB path
    {"attempted": 2, "saved": 2, "error": None, "dropped_invalid": 1},     # a dropped row
    {"attempted": 2, "saved": 1, "error": None, "dropped_invalid": 0},     # short count
])
def test_failed_allocation_save_still_emails_adds_footer_and_fails_the_lane(monkeypatch, bad):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)], save=lambda rows: bad)
    assert "24 shares" in s.emails["morning-action"]          # emails still go with sizes
    assert "16 shares" in s.emails["watchlist-entries"]
    for label in ("morning-action", "watchlist-entries"):
        assert "budget record" in s.emails[label].replace("&#39;", "'")
        assert "later suggestions may not account for these" in s.emails[label].replace("&#39;", "'")
    assert s.rc == 1
    assert "budget_allocations" in cr._LAST_LANE_FAILURE_DETAIL
    assert s.notified == [("scan", cr._LAST_LANE_FAILURE_DETAIL)]


def test_successful_allocation_save_has_no_footer_and_a_green_lane(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)])
    assert s.rc == 0 and s.notified == []
    for html in s.emails.values():
        assert "budget record" not in html


# ── 5. budget can't be checked ───────────────────────────────────────────────

def test_unreadable_trades_withhold_every_consuming_size_but_the_buy_still_goes(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80), _pick("BBB", 70)], [_entry("WLB", 60)],
             trades=None)
    why = "the trade log couldn't be read"
    mo, wl = s.emails["morning-action"], s.emails["watchlist-entries"]
    assert "AAA" in mo and "Buy $99.00" in mo and "24 shares" not in mo
    assert "WLB" in wl and "Suggested size" not in wl
    for html in (mo, wl):
        assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_UNKNOWN, {"why": why})) in html
    for tk in ("AAA", "BBB"):
        r = s.rec_row(tk)
        assert r.get("rec_shares") is None
        assert r["rec_budget_state"] == hold_reasons.BUDGET_UNKNOWN
        assert r["rec_sizing_version"] == daily_budget.BUDGET_SIZING_VERSION
    assert s.written == []


def test_unreadable_trades_control_readable_trades_size_normally(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 60)])
    assert "24 shares" in s.emails["morning-action"]
    assert "Size withheld" not in s.emails["morning-action"]
    assert "Size withheld" not in s.emails["watchlist-entries"]


def test_unreadable_allocations_withhold_sizes(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], allocations=None)
    assert "24 shares" not in s.emails["morning-action"]
    assert _esc("today's emailed sizes couldn't be read") in s.emails["morning-action"]


def test_stale_cash_record_withholds_sizes_with_a_visible_reason(monkeypatch):
    bi = dict(BUDGET_INPUTS, basis_state="stale")
    s = Scan(monkeypatch, [_pick("AAA", 80)], budget_inputs=bi)
    assert "24 shares" not in s.emails["morning-action"]
    assert _esc("the cash record is out of date") in s.emails["morning-action"]


def test_missing_budget_inputs_withhold_sizes(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], budget_inputs=None)
    html = s.emails["morning-action"]
    assert "24 shares" not in html and "Size withheld" in html
    # The exact reason: a basis forced when inputs are missing would instead
    # read "no cash record".
    assert _esc("net capital couldn't be determined") in html
    assert "no cash record" not in html
    assert s.written == []


def test_called_account_sets_budget_zero_and_f255_markers_pass_through(monkeypatch):
    bi = dict(BUDGET_INPUTS, basis_state="called", net_capital=-500.0)
    marker = {"capital_infeasible": True, "one_share_capital_pct": None,
              "net_capital": -500.0, "portfolio_value": 14000.0, "sizing_version": 3}
    p = _pick("AAA", 80)
    p["sizing"] = marker
    s = Scan(monkeypatch, [p], budget_inputs=bi)
    mo = s.emails["morning-action"]
    assert _esc(hold_reasons.budget_note(
        hold_reasons.BUDGET_EXHAUSTED, {"called": True})) in mo          # banner
    assert "No size suggested" in mo                                      # F-255's own note
    assert s.rec_row("AAA").get("rec_shares") is None


# ── 6. existing allocation reused, never re-written ─────────────────────────

def test_existing_allocation_is_reserved_and_not_rewritten(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)],
             allocations={"AAA": {"shares": 24.0, "dollars": 2400.0}})
    mo = s.emails["morning-action"]
    assert "24 shares" in mo
    assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_RESERVED)) in mo
    assert [r["ticker"] for r in s.written] == ["WLB"]       # AAA is NOT re-written
    assert "16 shares" in s.emails["watchlist-entries"]       # 4,000 - 2,400 left
    assert s.rec_row("AAA")["rec_budget_state"] == hold_reasons.BUDGET_RESERVED


def test_reuse_control_a_new_ticker_is_written(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)],
             allocations={"OLD": {"shares": 10.0, "dollars": 1000.0}})
    assert sorted(r["ticker"] for r in s.written) == ["AAA", "WLB"]


# ── 7. status line on all three emails ───────────────────────────────────────

def test_status_line_is_on_the_morning_and_watchlist_emails(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)])
    line = _esc(hold_reasons.budget_status_line(
        40.0, 0, 0, 4000, 0, list_label="this morning's emails"))
    assert line in s.emails["morning-action"]
    assert line in s.emails["watchlist-entries"]


def test_logged_buys_alone_over_budget_say_so(monkeypatch):
    big = pd.DataFrame([{"traded_at": "2026-10-16T13:30:00+00:00", "action": "BUY",
                         "ticker": "ZZZ", "shares": 50, "price": 100.0}])   # 50% > 40%
    s = Scan(monkeypatch, [_pick("AAA", 80)], trades=big, budget_inputs=FRESH_CASH)
    assert _esc(hold_reasons.budget_over_line(50.0)) in s.emails["morning-action"]


def test_status_line_absent_when_no_budget_is_passed():
    """Control: pre-budget callers (budget=None) render byte-identically."""
    _, html = notify.render_daily_action_email(_pick("AAA", 80), [], [], "2026-10-16T09:45:00")
    assert "New positions today" not in html and "budget" not in html.lower()


# ── 8. intraday ──────────────────────────────────────────────────────────────

def _run_intraday(monkeypatch, picks, *, allocations, save=_ok_save, trades="empty"):
    import stock_analyzer.intraday_entry as ie
    emails, saved, notified = {}, [], []
    now = cr._ET.localize(datetime.datetime(2026, 10, 16, 11, 35))
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(cr.db, "load_scanner_cache",
                        lambda: {"df": pd.DataFrame({"Ticker": ["AAA"]}),
                                 "scan_date": "2026-10-16"})
    payload = {"picks": picks, "errors": [], "built_at": "x", "book_drift": None,
               "macro_coverage": None, "budget_inputs": BUDGET_INPUTS}
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: payload)
    monkeypatch.setattr(ie, "fetch_intraday_prices",
                        lambda t: {x: {"current": 98.0, "open": 100.0} for x in t})
    monkeypatch.setattr(
        ie, "compute_intraday_entries",
        lambda ps, *_a: [{**p, "intraday_drop_pct": -2.0, "current_price": 98.0,
                          "open_price": 100.0} for p in ps])
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, subject, html: emails.__setitem__(label, html) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: notified.append((lane, detail)))
    tdf = pd.DataFrame(columns=_TRADE_COLS) if isinstance(trades, str) else trades
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: tdf)
    monkeypatch.setattr(cr.db, "load_budget_allocations_or_none", lambda day: dict(allocations))

    def _sv(rows):
        saved.append([dict(r) for r in rows])
        return save(rows)
    monkeypatch.setattr(cr.db, "save_budget_allocations", _sv)
    rc = cr._run_intraday(now, force=False)
    return rc, emails, [r for b in saved for r in b], notified


def test_intraday_reuses_existing_allocation_and_allocates_a_new_name_from_what_is_left(monkeypatch):
    rc, emails, written, _ = _run_intraday(
        monkeypatch, [_pick("AAA", 80), _pick("BBB", 70)],
        allocations={"AAA": {"shares": 24.0, "dollars": 2400.0}})
    html = emails["intraday-entry"]
    assert rc == 0
    assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_RESERVED)) in html     # AAA
    assert _esc(hold_reasons.budget_note(
        hold_reasons.BUDGET_PARTIAL, {"n": 16, "m": 24})) in html                   # BBB
    assert [(r["ticker"], r["source"], r["shares"]) for r in written] == [("BBB", "intraday", 16)]
    assert "New positions today" in html                                             # banner
    # Every allocation means "a size was shown": the written name AND the
    # reserved one show their share counts.
    assert "→ SIZE:</b> ~<b style='color:#e5e7eb'>24 shares</b>" in html            # AAA reserved
    assert "→ SIZE:</b> ~<b style='color:#e5e7eb'>16 shares</b>" in html            # BBB new


def test_intraday_control_no_prior_allocations_writes_both(monkeypatch):
    _, _, written, _ = _run_intraday(
        monkeypatch, [_pick("AAA", 80, shares=10), _pick("BBB", 70, shares=10)],
        allocations={})
    assert sorted((r["ticker"], r["source"]) for r in written) == [
        ("AAA", "intraday"), ("BBB", "intraday")]


def test_intraday_unreadable_trades_withhold_but_still_email(monkeypatch):
    rc, emails, written, _ = _run_intraday(
        monkeypatch, [_pick("AAA", 80)], allocations={}, trades=None)
    assert "AAA" in emails["intraday-entry"]
    assert "Size withheld" in emails["intraday-entry"].replace("&#39;", "'")
    assert written == [] and rc == 0


def test_intraday_failed_allocation_save_fails_the_lane_after_the_email(monkeypatch):
    rc, emails, _, notified = _run_intraday(
        monkeypatch, [_pick("AAA", 80)], allocations={},
        save=lambda rows: {"attempted": 1, "saved": 0, "error": "boom", "dropped_invalid": 0})
    assert "intraday-entry" in emails
    assert "later suggestions may not account for these" in emails["intraday-entry"].replace("&#39;", "'")
    assert rc == 1 and "budget_allocations" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == [("intraday", cr._LAST_LANE_FAILURE_DETAIL)]


# ── 9. renderers: every marker rendered, every email carries the banner ─────

_BUDGET_OK = {"state": "ok", "reason": None, "pct_used": 40.0, "used_logged": 0.0,
              "used_allocated": 0.0, "used_list": 4000.0, "left": 0.0,
              "over_line_pct": None, "save_failed": False}


@pytest.mark.parametrize("sz, expected", [
    ({"budget_capped": True, "shares": 16, "budget_uncapped_shares": 24},
     hold_reasons.budget_note(hold_reasons.BUDGET_PARTIAL, {"n": 16, "m": 24})),
    ({"budget_exhausted": True}, hold_reasons.budget_note(hold_reasons.BUDGET_EXHAUSTED)),
    ({"budget_unknown": True, "budget_reason": "no cash record"},
     hold_reasons.budget_note(hold_reasons.BUDGET_UNKNOWN, {"why": "no cash record"})),
    ({"budget_reserved": True}, hold_reasons.budget_note(hold_reasons.BUDGET_RESERVED)),
])
def test_sizing_cap_note_renders_every_budget_marker(sz, expected):
    assert _esc(expected) in notify._sizing_cap_note(sz, _BUDGET_OK)
    assert notify._sizing_cap_note({}, _BUDGET_OK) == ""        # control: nothing fires


def test_sizing_cap_note_called_wording_only_in_a_called_state():
    called = notify._sizing_cap_note({"budget_exhausted": True}, dict(_BUDGET_OK, state="called"))
    assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_EXHAUSTED, {"called": True})) in called
    ok = notify._sizing_cap_note({"budget_exhausted": True}, _BUDGET_OK)
    assert "at or below zero" not in ok


def test_sizing_cap_note_without_budget_is_the_pre_budget_note():
    sz = {"capital_capped": True, "capital_pct": 24.0}
    assert notify._sizing_cap_note(sz) == notify._cap_note_core(sz) != ""


def test_every_email_renders_the_status_line_and_the_save_footer():
    line = _esc(hold_reasons.budget_status_line(
        40.0, 0, 0, 4000, 0, list_label="this morning's emails"))
    bad = dict(_BUDGET_OK, save_failed=True)
    renders = [
        notify.render_daily_action_email(_pick("AAA", 80), [], [], "t", budget=bad)[1],
        notify.render_intraday_entry_email(
            [dict(_pick("AAA", 80), intraday_drop_pct=-2.0)], -0.5, "t", budget=bad)[1],
        notify.render_watchlist_entries_email([_entry("WLB", 70)], "t", budget=bad)[1],
    ]
    intraday_line = _esc(hold_reasons.budget_status_line(
        40.0, 0, 0, 4000, 0, list_label="this email"))
    for i, html in enumerate(renders):
        assert (intraday_line if i == 1 else line) in html
        assert (line in html) is (i != 1)
        assert "later suggestions may not account for these" in html.replace("&#39;", "'")
    ok = notify.render_watchlist_entries_email([_entry("WLB", 70)], "t", budget=_BUDGET_OK)[1]
    assert line in ok and "later suggestions" not in ok          # control


def test_watchlist_exhausted_entry_shows_the_note_not_a_blank():
    e = _entry("WLB", 70)
    e["sizing"] = daily_budget.apply_to_sizing(e["sizing"], {"status": "budget_exhausted"})
    html = notify.render_watchlist_entries_email([e], "t", budget=_BUDGET_OK)[1]
    assert "Suggested size" not in html
    # A Ready-to-Enter name is not "one of today's picks".
    assert _esc(hold_reasons.budget_note(
        hold_reasons.BUDGET_EXHAUSTED, {"is_pick": False})) in html
    assert "One of today" not in html


def test_morning_card_exhausted_keeps_the_pick_wording():
    """Control: the #1 pick card IS one of today's picks."""
    sz = daily_budget.apply_to_sizing(_sizing(), {"status": "budget_exhausted"})
    note = notify._sizing_cap_note(sz, _BUDGET_OK)
    assert _esc(hold_reasons.budget_note(hold_reasons.BUDGET_EXHAUSTED)) in note
    assert "One of today" in note


# ── 10. versioning + row hygiene + crash fallback ───────────────────────────

def test_sizing_formula_version_matches_the_budget_version():
    assert SIZING_FORMULA_VERSION == daily_budget.BUDGET_SIZING_VERSION == 5


def test_non_budget_row_keeps_its_pre_budget_key_set():
    rows = cr._build_new_pick_rows([_pick("AAA", 80)], TODAY)
    assert "rec_budget_state" not in rows[0]
    adj = _pick("BBB", 70)
    adj["sizing"] = daily_budget.apply_to_sizing(
        adj["sizing"], {"status": "budget_partial", "shares": 16, "dollars": 1600.0,
                        "uncapped_shares": 24})
    rows = cr._build_new_pick_rows([adj], TODAY)
    assert rows[0]["rec_budget_state"] == "budget_partial" and rows[0]["rec_shares"] == 16


def test_budget_state_codes_are_hold_reasons_constants():
    for flag, code in (("budget_unknown", hold_reasons.BUDGET_UNKNOWN),
                       ("budget_exhausted", hold_reasons.BUDGET_EXHAUSTED),
                       ("budget_capped", hold_reasons.BUDGET_PARTIAL),
                       ("budget_reserved", hold_reasons.BUDGET_RESERVED)):
        assert cr._rec_budget_state({flag: True}) == code
    assert cr._rec_budget_state({"shares": 5}) is None and cr._rec_budget_state(None) is None


def test_a_crashed_budget_pass_withholds_sizes_and_reports(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("alloc boom")
    monkeypatch.setattr(hae._daily_budget, "allocate", _boom)
    monkeypatch.setattr(hae.db, "load_trades_or_none", lambda: pd.DataFrame(columns=_TRADE_COLS))
    monkeypatch.setattr(hae.db, "load_budget_allocations_or_none", lambda d: {})
    p = _pick("AAA", 80)
    out = hae.run_daily_budget(TODAY, NOW, BUDGET_INPUTS, [("pick", p)], [])
    assert out["crashed"].startswith("RuntimeError") and out["budget"]["state"] == "unknown"
    assert "shares" not in p["sizing"] and p["sizing"]["budget_unknown"] is True


def test_a_crashed_budget_pass_fails_the_scan_lane(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("alloc boom")
    monkeypatch.setattr(hae._daily_budget, "allocate", _boom)
    s = Scan(monkeypatch, [_pick("AAA", 80)])
    assert "24 shares" not in s.emails["morning-action"]
    assert s.rc == 1 and "budget:" in cr._LAST_LANE_FAILURE_DETAIL


# ── 11. review follow-ups ────────────────────────────────────────────────────

def test_non_go_pick_that_is_also_a_watchlist_entry_gets_the_budget_size(monkeypatch):
    """Reviewer probe: the WLB new_pick rec row used to record the UN-budgeted
    24 (first writer wins, permanently) while the Watchlist email showed 16."""
    wlb_pick = _pick("WLB", 70)
    wlb_pick["xref"] = {"verdict": "wait", "verdict_reconciled": {"verdict": "wait"}}
    s = Scan(monkeypatch, [_pick("AAA", 80), wlb_pick], [_entry("WLB", 70)])
    assert "Suggested size:</b> 16 shares" in s.emails["watchlist-entries"]
    assert [r["shares"] for r in s.written if r["ticker"] == "WLB"] == [16]
    r = s.rec_row("WLB")
    assert r["rec_shares"] == 16
    assert r["rec_budget_state"] == hold_reasons.BUDGET_PARTIAL
    assert r["rec_sizing_version"] == daily_budget.BUDGET_SIZING_VERSION


def test_all_non_trading_allocation_save_is_a_quiet_noop_not_a_failure(monkeypatch):
    """A forced (ALERT_FORCE) weekend run: write_guards drops every row."""
    def weekend(rows):
        return {"attempted": 0, "saved": 0, "error": None, "dropped_invalid": 0,
                "skipped_non_trading": len(rows), "inserted": None}
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)], save=weekend)
    assert s.rc == 0 and s.notified == []
    for html in s.emails.values():
        assert "budget record" not in html.replace("&#39;", "'")


def test_partly_non_trading_save_still_counts_as_failed(monkeypatch):
    """Control: skipped < intended is a real shortfall."""
    def part(rows):
        return {"attempted": 1, "saved": 0, "error": None, "dropped_invalid": 0,
                "skipped_non_trading": 1, "inserted": None}
    s = Scan(monkeypatch, [_pick("AAA", 80)], [_entry("WLB", 70)], save=part)
    assert s.rc == 1


def test_banner_keeps_the_real_reason_when_every_item_is_a_no_size_marker(monkeypatch):
    marker = {"capital_infeasible": True, "one_share_capital_pct": 30.0,
              "net_capital": 10000.0, "portfolio_value": 14000.0, "sizing_version": 3}
    p = _pick("AAA", 80)
    p["sizing"] = marker
    s = Scan(monkeypatch, [p], allocations=None)
    html = s.emails["morning-action"]
    assert _esc("today's emailed sizes couldn't be read") in html
    assert "a required input was unavailable" not in html


def test_status_line_label_default_and_email_label():
    assert "this list $4,000" in hold_reasons.budget_status_line(40.0, 0, 0, 4000, 0)
    assert "this morning's emails $4,000" in hold_reasons.budget_status_line(
        40.0, 0, 0, 4000, 0, list_label="this morning's emails")


class TestMorningPicksBudgetInputs:
    """The scan tests mock compute_morning_picks whole, so a dropped or
    mis-keyed budget_inputs field (which would withhold every emailed size
    forever) is only catchable against the real producer."""

    @staticmethod
    def _run(acct):
        from unittest.mock import patch
        ctx = {"ok": True, "errors": [],
               "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [14000.0]}),
               "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None,
               "spy_1y": None, "vix": None,
               "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]})}
        brief = {"grow_today": {"tone": "bull", "new_picks": [], "sp500_pct": 1.0}}
        load = acct if callable(acct) else (lambda: acct)
        with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
             patch("stock_analyzer.data.fetch_market_indices",
                   return_value=[{"short": "S&P 500", "change_pct": 1.0}]), \
             patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
             patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
             patch("stock_analyzer.headless_alert_engine.db.load_account_cash", load), \
             patch("stock_analyzer.headless_alert_engine.build_daily_briefing", return_value=brief):
            return hae.compute_morning_picks(
                TODAY, scanner_results=pd.DataFrame({"Ticker": ["NVDA"]}))

    def test_all_five_fields_when_the_cash_record_is_readable(self):
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        out = self._run({"cash_balance": -4000.0, "note": None, "updated_at": stamp})
        assert out["budget_inputs"] == {
            "gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
            "cash_balance": -4000.0, "cash_as_of": stamp}

    def test_no_cash_record_is_unlevered_with_null_cash(self):
        bi = self._run(None)["budget_inputs"]
        assert bi["basis_state"] == "unlevered" and bi["gross_book"] == 14000.0
        assert bi["cash_balance"] is None and bi["cash_as_of"] is None

    def test_a_raising_cash_read_is_unknown_not_unlevered(self):
        def _boom():
            raise RuntimeError("db down")
        bi = self._run(_boom)["budget_inputs"]
        assert bi["basis_state"] == "unknown" and bi["net_capital"] is None


def test_allocate_unknown_result_carries_its_top_level_reason():
    ok = {"state": "ok", "basis": 10000.0, "reason": None}
    res = daily_budget.allocate(ok, {}, None, [])
    assert res["state"] == "unknown" and res["reason"] == "today's emailed sizes couldn't be read"
    res = daily_budget.allocate(ok, None, {}, [])
    assert res["reason"] == "the trade log couldn't be read"
    assert daily_budget.allocate(ok, {}, {}, [])["state"] == "ok"      # control


# ── 12. intraday shows the size of every name it allocates (owner decision 2026-10-16) ──

def _shares_visible(html, n):
    return f"<b style='color:#e5e7eb'>{n} shares</b>" in html


def test_every_intraday_written_allocation_has_its_share_count_in_the_email(monkeypatch):
    """Invariant: an intraday allocation always means a size was shown to you."""
    rc, emails, written, _ = _run_intraday(
        monkeypatch, [_pick("AAA", 80), _pick("BBB", 70), _pick("CCC", 60)],
        allocations={"AAA": {"shares": 24.0, "dollars": 2400.0}})
    html = emails["intraday-entry"]
    assert written, "firing control: something must have been written"
    for r in written:
        assert r["source"] == "intraday"
        assert _shares_visible(html, int(r["shares"])), r
    assert _shares_visible(html, 24)            # reserved shows the morning size
    assert "→ SIZE:" in html and html.count("→ SIZE:") == 2   # CCC: budget used, no size


def test_intraday_no_size_entries_show_no_share_count(monkeypatch):
    rc, emails, written, _ = _run_intraday(
        monkeypatch, [_pick("AAA", 80)], allocations={}, trades=None)
    html = emails["intraday-entry"]
    assert written == []
    assert "→ SIZE:" not in html and "shares</b>" not in html
    assert "Size withheld" in html


def test_intraday_f255_no_size_marker_keeps_its_note_and_shows_no_count(monkeypatch):
    p = _pick("AAA", 80)
    p["sizing"] = {"capital_infeasible": True, "one_share_capital_pct": 30.0,
                   "net_capital": 10000.0, "portfolio_value": 14000.0, "sizing_version": 3}
    rc, emails, written, _ = _run_intraday(monkeypatch, [p], allocations={})
    html = emails["intraday-entry"]
    assert written == [] and "→ SIZE:" not in html and "No size suggested" in html


def test_morning_email_control_shows_the_top_pick_size_with_the_same_invariant(monkeypatch):
    s = Scan(monkeypatch, [_pick("AAA", 80), _pick("BBB", 70)])
    assert [r["ticker"] for r in s.written] == ["AAA"]
    assert "24 shares" in s.emails["morning-action"]


def test_intraday_banner_label_is_this_email_and_morning_keeps_its_own(monkeypatch):
    rc, emails, _, _ = _run_intraday(monkeypatch, [_pick("AAA", 80, shares=10)], allocations={})
    assert "this email $1,000" in emails["intraday-entry"]
    assert "this morning's emails" not in _html.unescape(emails["intraday-entry"])
    s = Scan(monkeypatch, [_pick("AAA", 80, shares=10)])
    assert _esc("this morning's emails $1,000") in s.emails["morning-action"]
