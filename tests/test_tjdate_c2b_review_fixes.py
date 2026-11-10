"""TJDate C2b, Opus review fixes (FIX-FIRST round).

1. A Confirm flips only the pending import of the record it just saved
   (`trade_confirm.pending_import_to_flip`): `_tj_pending_broker_id` is one shared key,
   so confirming one staged card must never mark another card's fill 'logged'.
2. `has_same_day_fill`: a same-day split that reconciles (MRVL 10-30) was compared,
   so no "not compared yet" line.
3. `price_check_history_period` follows `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS`.
4. A full activities page (the client reads one page) withholds the cron's
   completed-sync stamp.
"""
import inspect
from datetime import date

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import constants as C
from stock_analyzer import snaptrade_client as sc
from stock_analyzer import trade_confirm as tc
from stock_analyzer import trade_entry_checks as tec
from tests.test_broker_txn_sync_cron import _run
from tests.test_trade_confirm import _app_text

pytestmark = pytest.mark.fast


# ── 1. pending_import_to_flip ────────────────────────────────────────────────

def test_pending_import_to_flip_boundaries():
    f = tc.pending_import_to_flip
    rec = {"broker_txn_id": "T1"}
    assert f({"pending_id": 7, "txn_id": "T1"}, rec) == 7                       # a match
    assert f({"pending_id": 7, "txn_id": " T1 "}, rec) == 7
    assert f({"pending_id": 7, "txn_id": 55}, {"broker_txn_id": "55"}) == 7     # str compare
    assert f({"pending_id": 7, "txn_id": "T2"}, rec) is None                    # another card's fill
    assert f({"pending_id": 7, "txn_id": None}, rec) is None                    # None txn
    assert f({"pending_id": 7, "txn_id": "T1"}, {"broker_txn_id": None}) is None
    assert f({"pending_id": 7, "txn_id": "T1"}, {}) is None                     # record never linked
    assert f({"pending_id": None, "txn_id": "T1"}, rec) is None
    assert f({"txn_id": "T1"}, rec) is None
    assert f(7, rec) is None                                                    # legacy bare int
    assert f("T1", rec) is None
    assert f(None, rec) is None                                                 # missing key
    assert f({"pending_id": 7, "txn_id": "T1"}, None) is None
    assert f({"pending_id": 0, "txn_id": "T1"}, rec) == 0                       # a falsy id is still an id


def test_two_staged_cards_cannot_flip_each_others_pending_import():
    # a BUY matched fill T, then a SELL matched T': the shared key holds the SELL's stash
    sell_stash = {"pending_id": 22, "txn_id": "T-prime"}
    assert tc.pending_import_to_flip(sell_stash, {"broker_txn_id": "T"}) is None
    assert tc.pending_import_to_flip(sell_stash, {"broker_txn_id": "T-prime"}) == 22
    # a manual card (no link) can never flip a broker prefill's import
    assert tc.pending_import_to_flip(sell_stash, {"ticker": "X"}) is None


def test_both_confirm_handlers_flip_through_the_pure_helper():
    t = _app_text()
    for var, rec in (("_ps_broker_pid", "_pending_sell"), ("_pb_broker_pid", "_pending_buy")):
        needle = (f"{var} = trade_confirm.pending_import_to_flip(\n"
                  f'                    st.session_state.pop("_tj_pending_broker_id", None), {rec})')
        assert needle in t, var
        assert f"mark_snaptrade_pending_import_logged({var})" in t
    # every use of the key is a pop, or one of the two dict-writing assignments
    assert t.count('st.session_state["_tj_pending_broker_id"] = {') == 2
    for line in t.splitlines():
        if "_tj_pending_broker_id" in line and "pop(" not in line:
            assert 'st.session_state["_tj_pending_broker_id"] = {' in line, line
    assert 'session_state.get("_tj_pending_broker_id"' not in t
    # both writers store the {"pending_id", "txn_id"} shape
    assert t.count('"txn_id": _broker_prefill.get("snaptrade_txn_id")') == 1
    assert t.count('"txn_id": _tj_match_link["txn_id"]') == 1


# ── 2. has_same_day_fill ─────────────────────────────────────────────────────

def _fill(txn, tk, act, sh, px, d):
    return {"snaptrade_txn_id": txn, "ticker": tk, "action": act, "shares": sh,
            "price": px, "trade_date": d}


def _entry(tk="MRVL", act="BUY", sh=8, px=196.99, d=date(2026, 10, 30)):
    return {"ticker": tk, "action": act, "shares": sh, "price": px, "trade_date": d}


def test_has_same_day_fill_boundaries():
    e = _entry()
    same = _fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30))
    assert tec.has_same_day_fill(e, [same]) is True
    assert tec.has_same_day_fill(e, [_fill("S", " mrvl ", "buy", 5, 1, "2026-10-30")]) is True
    assert tec.has_same_day_fill(e, [_fill("S", "MRVL", "BUY", 5, 1, date(2026, 10, 29))]) is False
    assert tec.has_same_day_fill(e, [_fill("S", "MRVL", "SELL", 5, 1, date(2026, 10, 30))]) is False
    assert tec.has_same_day_fill(e, [_fill("S", "AAPL", "BUY", 5, 1, date(2026, 10, 30))]) is False
    assert tec.has_same_day_fill(e, []) is False and tec.has_same_day_fill(e, None) is False
    assert tec.has_same_day_fill(None, [same]) is False
    assert tec.has_same_day_fill({"ticker": "MRVL"}, [same]) is False
    junk = ["junk", {"ticker": "MRVL", "action": "BUY", "trade_date": None}, same]
    assert tec.has_same_day_fill(e, junk) is True
    # an ET evening fill stored as the next UTC day is still the entry's day
    assert tec.has_same_day_fill(e, [_fill("S", "MRVL", "BUY", 5, 1, "2026-10-31T01:30:00Z")]) is True


def test_the_mrvl_reconcile_was_compared_so_it_gets_no_not_compared_line():
    e = _entry()
    fills = [_fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             _fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30))]
    assert tec.pending_fill_findings(e, fills) == []
    assert tec.has_same_day_fill(e, fills) is True
    # a different day's fill alone is NOT proof the entry's day was compared
    assert tec.has_same_day_fill(e, [_fill("S1", "MRVL", "BUY", 5, 200.0, date(2026, 10, 29))]) is False


def test_the_app_skips_the_coverage_line_only_for_a_compared_day():
    t = _app_text()
    i = t.index("def _tj_price_broker_stage(")
    body = t[i:t.index("# ── SELL confirmation card", i)]
    assert "if not _bk_f and not trade_entry_checks.has_same_day_fill(" in body
    assert body.index("has_same_day_fill(") < body.index("broker_coverage_finding(")


# ── 3. price_check_history_period ────────────────────────────────────────────

def test_history_period_follows_the_constant(monkeypatch):
    assert tec.price_check_history_period() == "3mo"                 # 35 days today
    for age, want in ((27, "1mo"), (28, "3mo"), (35, "3mo"), (88, "3mo"), (89, "6mo"),
                      (179, "6mo"), (180, "1y"), (364, "1y"), (365, "2y"), (3649, "10y"),
                      (3650, "max")):
        monkeypatch.setattr(tec, "TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS", age)
        got = tec.price_check_history_period()
        assert got == want, (age, got)
        if got != "max":
            assert dict(tec._YF_PERIOD_DAYS)[got] > age              # strictly spans the oldest date


def test_history_period_always_spans_the_real_constant():
    assert dict(tec._YF_PERIOD_DAYS)[tec.price_check_history_period()] > \
        C.TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS


def test_the_app_passes_the_derived_period_not_a_literal():
    t = _app_text()
    i = t.index("def _tj_price_broker_stage(")
    body = t[i:t.index("# ── SELL confirmation card", i)]
    assert "trade_entry_checks.price_check_history_period()" in body
    assert '"3mo"' not in body


# ── 4. truncated activities page ─────────────────────────────────────────────

def test_the_client_has_one_named_page_limit_used_by_its_request():
    assert sc.ACTIVITIES_PAGE_LIMIT == 1000
    src = inspect.getsource(sc.get_account_activities)
    assert '"limit": ACTIVITIES_PAGE_LIMIT' in src and '"limit": 1000' not in src


def test_a_full_activities_page_withholds_the_stamp_and_says_so(monkeypatch, capsys):
    limit = sc.ACTIVITIES_PAGE_LIMIT
    for n, stamped in ((limit - 1, 1), (limit, 0), (limit + 5, 0)):
        rc, rec = _run(monkeypatch, trades=pd.DataFrame(), activities=[{}] * n)
        assert len(rec["stamp"]) == stamped, n
        assert rc == 0 and rec["detail"] is None            # bookkeeping: never a lane failure
    out = capsys.readouterr().out
    assert out.count("page limit") == 2 and "completed-sync stamp withheld" in out


def test_the_cron_reads_the_limit_from_the_client_not_a_literal():
    src = inspect.getsource(cr._run_broker)
    assert "snaptrade_client.ACTIVITIES_PAGE_LIMIT" in src
    assert "1000" not in src
