"""TJDup (D0): the duplicate-entry guard on the Trade Journal confirm card.

Regression fixture: 2026-11-06, CSCO BUY 3 @ $102.00 logged at
2026-11-05T00:14:00+00:00 (19:14 ET on Nov 4, RECOMMENDATION) and re-logged as a
MANUAL row dated Nov 4 (the 16:00 ET date-only anchor). Robinhood had one fill.

Layers pinned:
  1. `trade_confirm.find_duplicate_entries` (ET dates, session distance, share /
     price tolerances, broker identity).
  2. `trade_confirm.confirm_gate` (what the card renders; hard / tick rules).
  3. app.py (no test imports it, so source / AST pins): the Confirm buttons
     consume the gate, the ack key carries the idempotency key, the staging read
     is the STRICT one, and the findings never reach `db.save_trade`.
"""
import ast
import re
from pathlib import Path

import pandas as pd

from stock_analyzer import trade_confirm as tc
from stock_analyzer import trade_time
from stock_analyzer.constants import (
    BROKER_DRIFT_SHARE_TOL,
    JOURNAL_MATCH_PRICE_TOL_USD,
    TRADE_LOG_DUP_WINDOW_SESSIONS,
)
from tests._ast_cache import parse_file_cached

_APP = Path(__file__).resolve().parents[1] / "app.py"


def _row(id_, traded_at, *, ticker="CSCO", action="BUY", shares=3, price=102.0,
         trigger="MANUAL", txn=None, notes=None):
    return {"id": id_, "ticker": ticker, "action": action, "shares": shares,
            "price": price, "traded_at": traded_at, "trigger_type": trigger,
            "broker_txn_id": txn, "notes": notes}


def _journal(*rows):
    return pd.DataFrame(list(rows))


def _new(traded_at, *, ticker="CSCO", action="BUY", shares=3, price=102.0, txn=None):
    d = {"ticker": ticker, "action": action, "shares": shares, "price": price,
         "traded_at": traded_at}
    if txn is not None:
        d["broker_txn_id"] = txn
    return d


def _kinds(dup):
    return [m["kind"] for m in dup["matches"]]


# anchors: 16:00 ET date-only placeholder (EST = 21:00Z, EDT = 20:00Z)
NOV02 = "2026-11-02T21:00:00+00:00"
NOV03 = "2026-11-03T21:00:00+00:00"
NOV04 = "2026-11-04T21:00:00+00:00"
NOV05 = "2026-11-05T21:00:00+00:00"
NOV06 = "2026-11-06T21:00:00+00:00"
NOV07_SAT = "2026-11-07T21:00:00+00:00"
NOV09_MON = "2026-11-09T21:00:00+00:00"
NOV25 = "2026-11-25T21:00:00+00:00"
NOV26_HOLIDAY = "2026-11-26T21:00:00+00:00"
NOV27 = "2026-11-27T21:00:00+00:00"


# ── the incident ─────────────────────────────────────────────────────────────

def test_csco_incident_is_a_same_price_match():
    j = _journal(_row(13901, "2026-11-05T00:14:00+00:00", trigger="RECOMMENDATION"))
    # The UTC date of the stored row (Nov 5) is NOT the new row's (Nov 4): a
    # UTC comparison would have missed it. The ET date is Nov 4.
    assert str(j.loc[0, "traded_at"])[:10] == "2026-11-05"
    assert str(trade_time.et_trade_date(j.loc[0, "traded_at"])) == "2026-11-04"
    d = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    assert d["state"] == "checked" and d["hard"] == []
    assert _kinds(d) == ["same_price"]
    m = d["matches"][0]
    assert m["id"] == 13901 and m["trigger_type"] == "RECOMMENDATION"
    assert m["linked"] is False and m["et_display"] == "2026-11-04 19:14"


def test_csco_gate_text():
    j = _journal(_row(13901, "2026-11-05T00:14:00+00:00", trigger="RECOMMENDATION"))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    assert g["hard"] is False and g["needs_tick"] is True
    assert g["tick_label"] == "This is a separate fill, not #13901"
    (b,) = g["blocks"]
    assert b["level"] == "warning"
    assert b["title"] == ("Possible duplicate — this looks like a trade already "
                          "in your journal")
    assert b["lines"][0] == ("#13901 · Wed Nov 4, 19:14 ET · BUY 3 @ $102.00 · "
                             "Recommendation · same price, same day")


def test_date_only_placeholder_row_reads_no_time():
    j = _journal(_row(7, NOV04))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    assert "#7 · Wed Nov 4 (no time) · BUY 3 @ $102.00 · Manual" in g["blocks"][0]["lines"][0]


def test_linked_row_suffix_only_when_linked():
    j = _journal(_row(1, NOV04, txn="abc"), _row(2, NOV04))
    d = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    lines = tc.confirm_gate(d)["blocks"][0]["lines"]
    linked = [ln for ln in lines if ln.startswith("#1 ")]
    unlinked = [ln for ln in lines if ln.startswith("#2 ")]
    assert "linked to Robinhood" in linked[0]
    assert "linked to Robinhood" not in unlinked[0]


# ── time-context matrix ──────────────────────────────────────────────────────

def test_dst_same_instant_two_spellings_match():
    # Fri 2026-10-30 20:30 EDT == 2026-10-31T00:30Z: same ET day (10-30).
    j = _journal(_row(1, "2026-10-31T00:30:00+00:00"))
    d = tc.find_duplicate_entries(_new("2026-10-30T20:30:00-04:00"), j, broker_path=False)
    assert _kinds(d) == ["same_price"]


def test_est_evening_fill_dates_to_the_et_day():
    # Mon 11-02 19:30 EST == 2026-11-03T00:30Z -> ET 11-02, so the 11-02 anchor matches.
    j = _journal(_row(1, "2026-11-03T00:30:00+00:00"))
    assert str(trade_time.et_trade_date("2026-11-03T00:30:00+00:00")) == "2026-11-02"
    d = tc.find_duplicate_entries(_new(NOV02), j, broker_path=False)
    assert _kinds(d) == ["same_price"]


def test_march_dst_change_week():
    # Mar 9 2026 (the day after spring-forward): 20:30 EDT == 2026-03-10T00:30Z.
    # The EDT date-only anchor for Mar 9 is 20:00Z.
    j = _journal(_row(1, "2026-03-10T00:30:00+00:00"))
    d = tc.find_duplicate_entries(_new("2026-03-09T20:00:00+00:00"), j, broker_path=False)
    assert _kinds(d) == ["same_price"]
    # Fri Mar 6 -> Mon Mar 9 is one session apart across the change.
    j2 = _journal(_row(2, "2026-03-06T21:00:00+00:00"))
    d2 = tc.find_duplicate_entries(_new("2026-03-09T20:00:00+00:00"), j2, broker_path=False)
    assert _kinds(d2) == ["adjacent_session_same_price"]


def test_weekend_dated_entry_is_same_session_as_friday():
    # Fri 11-06 row vs a Saturday-dated entry: 0 sessions apart -> always a match.
    j = _journal(_row(1, NOV06))
    d = tc.find_duplicate_entries(_new(NOV07_SAT), j, broker_path=False)
    assert _kinds(d) == ["same_price"]
    d2 = tc.find_duplicate_entries(_new(NOV07_SAT, price=105.0), j, broker_path=False)
    assert _kinds(d2) == ["same_size_diff_price"]


def test_friday_to_monday_is_one_session():
    j = _journal(_row(1, NOV06))
    assert _kinds(tc.find_duplicate_entries(_new(NOV09_MON), j, broker_path=False)) \
        == ["adjacent_session_same_price"]
    assert tc.find_duplicate_entries(_new(NOV09_MON, price=105.0), j,
                                     broker_path=False)["matches"] == []


def test_thanksgiving():
    j = _journal(_row(1, NOV25))
    # Wed -> Fri skips the holiday: 1 session, same price only.
    assert _kinds(tc.find_duplicate_entries(_new(NOV27), j, broker_path=False)) \
        == ["adjacent_session_same_price"]
    assert tc.find_duplicate_entries(_new(NOV27, price=110.0), j,
                                     broker_path=False)["matches"] == []
    # Wed -> Thu (closed): 0 sessions, so even a different price matches.
    assert _kinds(tc.find_duplicate_entries(_new(NOV26_HOLIDAY, price=110.0), j,
                                            broker_path=False)) == ["same_size_diff_price"]


def test_logged_a_day_late_and_a_day_early_buy_and_sell():
    for action in ("BUY", "SELL"):
        j = _journal(_row(1, NOV04, action=action))
        # new entry dated the day AFTER the existing row (logged a day late)
        d_late = tc.find_duplicate_entries(_new(NOV05, action=action), j, broker_path=False)
        assert _kinds(d_late) == ["adjacent_session_same_price"]
        assert d_late["matches"][0]["row_after_new"] is False
        assert "previous trading day" in tc.confirm_gate(d_late)["blocks"][0]["lines"][0]
        # new entry dated the day BEFORE the existing row
        d_early = tc.find_duplicate_entries(_new(NOV03, action=action), j, broker_path=False)
        assert _kinds(d_early) == ["adjacent_session_same_price"]
        assert d_early["matches"][0]["row_after_new"] is True
        assert "next trading day" in tc.confirm_gate(d_early)["blocks"][0]["lines"][0]


def test_manual_entry_without_traded_at_is_stamped_now_et(monkeypatch):
    import stock_analyzer.market_time as mt
    monkeypatch.setattr(mt, "now_et",
                        lambda: pd.Timestamp("2026-11-04 19:30", tz="America/New_York").to_pydatetime())
    j = _journal(_row(1, "2026-11-05T00:14:00+00:00"))   # 19:14 ET Nov 4
    new = _new(NOV04)
    new.pop("traded_at")
    assert _kinds(tc.find_duplicate_entries(new, j, broker_path=False)) == ["same_price"]


# ── boundaries ───────────────────────────────────────────────────────────────

def test_share_tolerance_boundary():
    assert BROKER_DRIFT_SHARE_TOL == 0.001
    j = _journal(_row(1, NOV04, shares=3.0))
    assert _kinds(tc.find_duplicate_entries(_new(NOV04, shares=3.001), j,
                                            broker_path=False)) == ["same_price"]
    assert tc.find_duplicate_entries(_new(NOV04, shares=3.0011), j,
                                     broker_path=False)["matches"] == []


def test_price_tolerance_boundary_is_float_safe():
    assert JOURNAL_MATCH_PRICE_TOL_USD == 0.01
    j = _journal(_row(1, NOV04, price=102.0))
    assert _kinds(tc.find_duplicate_entries(_new(NOV04, price=102.01), j,
                                            broker_path=False)) == ["same_price"]
    assert _kinds(tc.find_duplicate_entries(_new(NOV04, price=102.0101), j,
                                            broker_path=False)) == ["same_size_diff_price"]
    # next session: the price tolerance is the whole test
    j2 = _journal(_row(1, NOV03, price=102.0))
    assert _kinds(tc.find_duplicate_entries(_new(NOV04, price=102.01), j2,
                                            broker_path=False)) == ["adjacent_session_same_price"]
    assert tc.find_duplicate_entries(_new(NOV04, price=102.0101), j2,
                                     broker_path=False)["matches"] == []


def test_window_is_one_session():
    assert TRADE_LOG_DUP_WINDOW_SESSIONS == 1
    j = _journal(_row(1, NOV02))            # Mon; new on Wed = 2 sessions
    assert tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)["matches"] == []


def test_buy_never_matches_sell_and_other_tickers_never_match():
    j = _journal(_row(1, NOV04, action="SELL"), _row(2, NOV04, ticker="AAPL"))
    assert tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)["matches"] == []


def test_ticker_and_action_case_and_whitespace_are_normalised():
    j = _journal(_row(1, NOV04, ticker=" csco ", action="buy "))
    d = tc.find_duplicate_entries(_new(NOV04, ticker="CSCO", action=" Buy"), j,
                                  broker_path=False)
    assert _kinds(d) == ["same_price"]


def test_matches_sorted_closest_first():
    j = _journal(_row(1, NOV03), _row(2, NOV04, price=104.0), _row(3, NOV04))
    d = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    assert _kinds(d) == ["same_price", "same_size_diff_price",
                         "adjacent_session_same_price"]
    g = tc.confirm_gate(d)
    assert g["tick_label"] == "This is a separate fill, not #3, #2 or #1"


def test_missing_numeric_values_never_match_or_raise():
    j = _journal(_row(1, NOV04, shares=None), _row(2, NOV04, price=None),
                 _row(3, None))
    d = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    assert d["state"] == "checked" and d["matches"] == []


# ── unchecked / empty ────────────────────────────────────────────────────────

def test_none_journal_is_unchecked_info_only():
    d = tc.find_duplicate_entries(_new(NOV04), None, broker_path=False)
    assert d == {"state": "unchecked", "hard": [], "matches": []}
    g = tc.confirm_gate(d)
    assert g["hard"] is False and g["needs_tick"] is False and g["tick_label"] == ""
    assert g["blocks"] == [{"level": "info",
                            "title": "Couldn't check for a duplicate: the trade journal couldn't be read.",
                            "lines": []}]


def test_gate_of_none_is_the_same_unchecked_block():
    assert tc.confirm_gate(None) == tc.confirm_gate(
        tc.find_duplicate_entries(_new(NOV04), None, broker_path=False))


def test_empty_frame_is_checked_and_clean():
    d = tc.find_duplicate_entries(_new(NOV04), pd.DataFrame(), broker_path=False)
    assert d == {"state": "checked", "hard": [], "matches": []}
    g = tc.confirm_gate(d)
    assert g == {"hard": False, "needs_tick": False, "tick_label": "", "blocks": []}


def test_no_match_means_no_tick():
    j = _journal(_row(1, NOV04, ticker="AAPL"))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    assert g["blocks"] == [] and g["needs_tick"] is False and g["hard"] is False


# ── broker path ──────────────────────────────────────────────────────────────

def test_broker_txn_already_in_journal_is_hard():
    j = _journal(_row(42, NOV04, txn="TX-1", trigger="MANUAL"))
    d = tc.find_duplicate_entries(_new(NOV04, txn="TX-1"), j, broker_path=True)
    assert [h["kind"] for h in d["hard"]] == ["broker_txn_present"]
    assert d["hard"][0]["row"]["id"] == 42
    g = tc.confirm_gate(d)
    assert g["hard"] is True and g["needs_tick"] is False
    (b,) = g["blocks"]
    assert b["level"] == "error" and b["title"] == "Robinhood fill already in the journal"
    assert b["lines"][0] == "#42 · Wed Nov 4 (no time) · BUY 3 @ $102.00 · Manual"
    assert b["lines"][1] == ("Use 💰 Account → Broker Sync → Pending Imports → "
                             "Already logged instead.")


def test_linked_row_with_other_txn_is_excluded_on_broker_path_only():
    j = _journal(_row(1, NOV04, txn="OTHER"))
    on_broker = tc.find_duplicate_entries(_new(NOV04, txn="TX-9"), j, broker_path=True)
    assert on_broker["hard"] == [] and on_broker["matches"] == []
    on_manual = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    assert _kinds(on_manual) == ["same_price"] and on_manual["matches"][0]["linked"] is True


def test_broker_path_still_matches_unlinked_rows():
    # the CSCO shape: the fill about to be imported vs a hand-logged unlinked row
    j = _journal(_row(1, "2026-11-05T00:14:00+00:00", trigger="RECOMMENDATION"))
    d = tc.find_duplicate_entries(_new(NOV04, txn="TX-1"), j, broker_path=True)
    assert d["hard"] == [] and _kinds(d) == ["same_price"]


def test_txn_on_manual_path_is_not_compared():
    j = _journal(_row(1, NOV04, txn="TX-1", ticker="AAPL"))
    d = tc.find_duplicate_entries(_new(NOV04, txn="TX-1"), j, broker_path=False)
    assert d["hard"] == []


# ── gate text ────────────────────────────────────────────────────────────────

def test_gate_text_is_plain_text_no_html():
    j = _journal(_row(1, NOV04, txn="x"), _row(2, NOV03, trigger="TAX_HARVEST"))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    texts = [g["tick_label"]]
    for b in g["blocks"]:
        texts += [b["title"], *b["lines"]]
    assert texts and all("<" not in t and ">" not in t for t in texts)
    assert any("Tax Harvest" in t for t in texts)


def test_missing_trigger_type_is_stated_not_invented():
    j = _journal(_row(1, NOV04, trigger=None))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    assert "reason not recorded" in g["blocks"][0]["lines"][0]


def test_row_without_id_has_a_readable_reference():
    j = _journal(_row(None, NOV04))
    g = tc.confirm_gate(tc.find_duplicate_entries(_new(NOV04), j, broker_path=False))
    assert "#None" not in g["tick_label"]
    assert "#None" not in g["blocks"][0]["lines"][0]


def test_float_ids_from_a_nullable_column_render_as_ints():
    j = _journal(_row(13901.0, NOV04), _row(None, NOV03))
    d = tc.find_duplicate_entries(_new(NOV04), j, broker_path=False)
    assert d["matches"][0]["id"] == 13901 and isinstance(d["matches"][0]["id"], int)


# ── normalize_traded_at idempotence ──────────────────────────────────────────

def test_already_normalised_frame_gives_the_same_result():
    # An imported row at midnight UTC (the 11-04 bare-date import, provenance via
    # broker_txn_id) is re-anchored to 16:00 ET by the loader.
    raw = _journal(_row(1, "2026-11-04T00:00:00+00:00", txn="TX-1"))
    once = trade_time.normalize_traded_at(raw)
    twice = trade_time.normalize_traded_at(once)
    new = _new(NOV04)
    d_raw = tc.find_duplicate_entries(new, raw, broker_path=False)
    d_once = tc.find_duplicate_entries(new, once, broker_path=False)
    d_twice = tc.find_duplicate_entries(new, twice, broker_path=False)
    # A frame that already carries `traded_at_time_known` (the strict loader's
    # output) is not re-normalised, so its flag survives: raw -> loader -> same.
    assert d_raw == d_once
    # normalize_traded_at itself is NOT flag-idempotent (a second pass stamps
    # time_known True on the already-anchored row); the dates, and so the
    # finding, still agree.
    assert [m["id"] for m in d_twice["matches"]] == [m["id"] for m in d_once["matches"]]
    assert _kinds(d_twice) == _kinds(d_once)
    assert _kinds(d_once) == ["same_price"]       # re-anchored to Nov 4, 16:00 ET


def test_normalisation_keeps_the_time_known_flag():
    raw = _journal(_row(1, "2026-11-04T00:00:00+00:00", txn="TX-1"))
    once = trade_time.normalize_traded_at(raw)
    d = tc.find_duplicate_entries(_new(NOV04), once, broker_path=False)
    assert d["matches"][0]["time_known"] is False
    assert "(no time)" in tc.confirm_gate(d)["blocks"][0]["lines"][0]


# ── C2a: entry findings on the same card ─────────────────────────────────────

_HARD_F = {"kind": "FUTURE", "severity": "hard", "text": "Fri Nov 13, 3:00 PM ET is in the future"}
_HARD_R = {"kind": "SELL_BEFORE_BUY", "severity": "hard", "text": "At Thu Nov 5, 2:00 PM ET your journal holds only 0 OKTA"}
_TICK_F = {"kind": "OUTSIDE_RTH", "severity": "tick", "text": "4:06 PM ET is outside regular hours (9:30 AM-4:00 PM)"}
_TICK_G = {"kind": "NON_TRADING_DATE", "severity": "tick", "text": "Sat Nov 7 is not a trading day"}
_INFO_F = {"kind": "MATCH_SAME_DAY", "severity": "info", "text": "Robinhood shows this fill"}


def _dups():
    """A spread of dup findings: unchecked, clean, soft match, D0 hard."""
    j = _journal(_row(1, NOV04))
    return {
        "none": None,
        "clean": tc.find_duplicate_entries(_new(NOV04), _journal(), broker_path=False),
        "soft": tc.find_duplicate_entries(_new(NOV04), j, broker_path=False),
        "hard": tc.find_duplicate_entries(
            _new(NOV04, txn="TX-1"), _journal(_row(1, NOV04, txn="TX-1")), broker_path=True),
    }


def test_no_entry_findings_is_byte_identical_to_the_dup_only_gate():
    for name, d in _dups().items():
        base = tc.confirm_gate(d)
        assert tc.confirm_gate(d, entry_findings=None) == base, name
        assert tc.confirm_gate(d, entry_findings=[]) == base, name
        assert tc.confirm_gate(d, None) == base, name
        assert list(base) == ["hard", "needs_tick", "tick_label", "blocks"], name


def test_hard_entry_finding_only_is_one_error_block_and_no_tick():
    d = _dups()["clean"]
    g = tc.confirm_gate(d, entry_findings=[_HARD_F, _HARD_R])
    assert g["hard"] is True and g["needs_tick"] is False and g["tick_label"] == ""
    assert g["blocks"] == [{"level": "error", "title": "This entry can't be recorded as dated",
                            "lines": [_HARD_F["text"], _HARD_R["text"]]}]


def test_tick_entry_finding_only_is_a_warning_block_with_the_single_tick():
    d = _dups()["clean"]
    g = tc.confirm_gate(d, entry_findings=[_TICK_F])
    assert g["hard"] is False and g["needs_tick"] is True
    assert g["tick_label"] == "The date and time are right"
    assert g["blocks"] == [{"level": "warning", "title": "Check the date and time",
                            "lines": [_TICK_F["text"],
                                      "Tick the box below if the date and time are right "
                                      "(for example an extended-hours fill)."]}]


def test_two_tick_findings_share_one_block_and_one_box():
    g = tc.confirm_gate(_dups()["none"], entry_findings=[_TICK_G, _TICK_F])
    assert g["needs_tick"] is True and g["tick_label"] == "The date and time are right"
    warn = [b for b in g["blocks"] if b["level"] == "warning"]
    assert len(warn) == 1 and warn[0]["lines"][:2] == [_TICK_G["text"], _TICK_F["text"]]


def test_dup_tick_only_label_is_unchanged_and_both_ticks_merge_into_one_label():
    soft = _dups()["soft"]
    dup_only = tc.confirm_gate(soft)
    assert dup_only["needs_tick"] is True
    both = tc.confirm_gate(soft, entry_findings=[_TICK_F])
    assert both["needs_tick"] is True
    assert both["tick_label"] == f"{dup_only['tick_label']}, and the date and time are right"
    # one box only: the dup block is first, the entry block follows
    assert [b["level"] for b in both["blocks"]] == ["warning", "warning"]
    assert both["blocks"][0] == dup_only["blocks"][0]
    assert both["blocks"][1]["title"] == "Check the date and time"
    assert both["hard"] is False


def test_dup_tick_with_a_hard_entry_finding_is_hard_and_keeps_the_dup_box():
    soft = _dups()["soft"]
    dup_only = tc.confirm_gate(soft)
    g = tc.confirm_gate(soft, entry_findings=[_HARD_F])
    assert g["hard"] is True
    assert g["needs_tick"] is True and g["tick_label"] == dup_only["tick_label"]
    assert [b["level"] for b in g["blocks"]] == ["warning", "error"]


def test_d0_hard_identity_still_has_no_tick_but_entry_findings_render_after_it():
    hard = _dups()["hard"]
    base = tc.confirm_gate(hard)
    assert base["hard"] is True and base["needs_tick"] is False
    for extra in ([_HARD_F], [_TICK_F], [_HARD_F, _TICK_F]):
        g = tc.confirm_gate(hard, entry_findings=extra)
        assert g["hard"] is True
        assert g["needs_tick"] is False and g["tick_label"] == ""
        assert g["blocks"][0] == base["blocks"][0]            # D0 block first, unchanged
        assert len(g["blocks"]) == 1 + (1 if any(f["severity"] == "hard" for f in extra) else 0)             + (1 if any(f["severity"] == "tick" for f in extra) else 0)
    # no dangling "tick the box below" hint when there is no box
    g = tc.confirm_gate(hard, entry_findings=[_TICK_F])
    assert g["blocks"][1]["lines"] == [_TICK_F["text"]]


def test_unchecked_dup_plus_tick_entry_finding_reads_time_only_label():
    g = tc.confirm_gate(None, entry_findings=[_TICK_F])
    assert g["blocks"][0]["level"] == "info"                   # "couldn't check" block first
    assert g["needs_tick"] is True and g["tick_label"] == "The date and time are right"
    assert g["hard"] is False


def test_info_entry_findings_never_block_or_need_a_tick():
    g = tc.confirm_gate(_dups()["clean"], entry_findings=[_INFO_F])
    assert g["hard"] is False and g["needs_tick"] is False and g["tick_label"] == ""
    assert g["blocks"] == [{"level": "info", "title": _INFO_F["text"], "lines": []}]


def test_unknown_severity_is_treated_as_a_tick_not_ignored():
    g = tc.confirm_gate(_dups()["clean"], entry_findings=[{"kind": "X", "severity": "weird", "text": "odd"}])
    assert g["needs_tick"] is True and g["hard"] is False


def test_entry_finding_text_is_plain_text():
    g = tc.confirm_gate(_dups()["soft"], entry_findings=[_HARD_R, _TICK_F])
    texts = [g["tick_label"]]
    for b in g["blocks"]:
        texts += [b["title"], *b["lines"]]
    assert all("<" not in x and ">" not in x for x in texts)


# ── app.py wiring (source / AST pins) ────────────────────────────────────────

def _app_text() -> str:
    return _APP.read_text(encoding="utf-8")


def _slice(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def test_both_confirm_buttons_are_disabled_by_the_gate():
    t = _app_text()
    for btn, gate, ack in (("Confirm SELL", "_ps_gate", "_ps_ack"),
                           ("Confirm BUY", "_pb_gate", "_pb_ack")):
        i = t.index(f'"✅ {btn}"')
        expr = t[i:i + 700]
        m = re.search(r"disabled=\((.*?)\)\):", expr, re.S)
        assert m, btn
        d = m.group(1)
        assert f'{gate}["hard"]' in d and f'{gate}["needs_tick"] and not {ack}' in d, btn


def test_gate_is_built_from_confirm_gate_and_ack_key_carries_the_idempotency_key():
    t = _app_text()
    assert ('trade_confirm.confirm_gate(\n            st.session_state.get("_tj_pending_dup_sell"), '
            'entry_findings=_ps_ef)') in t
    assert ('trade_confirm.confirm_gate(\n            st.session_state.get("_tj_pending_dup_buy"), '
            'entry_findings=_pb_ef)') in t
    assert 'key=f"_tj_ack_{_pending_sell[\'idempotency_key\']}"' in t
    assert 'key=f"_tj_ack_{_pending_buy[\'idempotency_key\']}"' in t
    # exactly one tick box per card, labelled by the gate
    assert t.count('st.checkbox(_ps_gate["tick_label"]') == 1
    assert t.count('st.checkbox(_pb_gate["tick_label"]') == 1


def test_staging_uses_the_strict_journal_read():
    t = _app_text()
    seg = _slice(t, "TJDup: ONE strict journal read at staging",
                 '_tj_pending_dup_buy"] = _dup_check')
    assert "db.load_trades_or_none()" in seg
    assert "load_trades()" not in seg
    assert "trade_confirm.find_duplicate_entries(" in seg
    assert "broker_path=_is_broker_trade" in seg


def test_findings_never_reach_save_trade():
    tree = parse_file_cached(_APP)
    saves = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "save_trade"]
    assert saves
    for call in saves:
        assert "dup" not in ast.unparse(call).lower(), ast.unparse(call)
    t = _app_text()
    assert 'record["_dup_check"]' not in t
    assert "_dup_check" not in _slice(t, "record = {", "# SnapTrade dedup key")


def test_dup_keys_are_popped_with_their_pending_record():
    t = _app_text()
    assert t.count('st.session_state.pop("_tj_pending_dup_sell", None)') == 2
    assert t.count('st.session_state.pop("_tj_pending_dup_buy", None)') == 2


# ── C2a wiring pins (app.py has no importable surface) ───────────────────────

def test_entry_keys_are_staged_beside_the_record_and_popped_with_it():
    t = _app_text()
    assert t.count('st.session_state["_tj_pending_entry_sell"] = _entry_stage') == 1
    assert t.count('st.session_state["_tj_pending_entry_buy"] = _entry_stage') == 1
    assert t.count('st.session_state.pop("_tj_pending_entry_sell", None)') == 2
    assert t.count('st.session_state.pop("_tj_pending_entry_buy", None)') == 2
    # the entry stage never goes into the record that db.save_trade receives
    assert "_entry_stage" not in _slice(t, "record = {", "# SnapTrade dedup key")
    assert "_entry" not in _slice(t, "_ps_saved = db.save_trade(_pending_sell)",
                                  "if _ps_saved or not db.has_db()")


def test_manual_traded_at_comes_from_resolve_traded_at_and_the_old_branches_are_gone():
    t = _app_text()
    seg = _slice(t, "TJDup: ONE strict journal read at staging", 'st.session_state["_tj_pending_buy"] = record')
    assert "trade_entry_checks.resolve_traded_at(" in seg
    assert "trade_entry_checks.time_findings(" in seg and "time_supplied=" in seg
    assert "trade_entry_checks.sell_replay_finding(" in seg
    assert "not _override_sell" in seg
    assert "trade_entry_checks.same_ticker_same_day(" in seg
    # resolution reads the strict journal read made just above it
    assert seg.index("_dup_journal =") < seg.index("same_ticker_same_day(")
    assert "elif _is_manual_backdated:" not in t
    assert "_et_anchor_iso(_tj_picked_date)" not in t


def test_date_input_defaults_through_default_trade_date_and_handles_none():
    t = _app_text()
    assert "trade_entry_checks.default_trade_date(_tj_now)" in t
    assert "value=_tj_date_value," not in t
    assert 'value=st.session_state.get("_tj_trade_date", _today_et())' not in t
    assert "(_tj_picked_date is not None)" in t
    assert 'st.error("Pick the trade date.")' in t
    assert 'key="_tj_fill_time"' in t
    # only the two successful saves pop it; the stale-day reset ASSIGNS
    assert t.count('st.session_state.pop("_tj_fill_time", None)') == 2


def test_no_db_rows_keep_the_records_own_traded_at():
    t = _app_text()
    assert '"traded_at": _pending_sell.get("traded_at") or _now_et().isoformat()' in t
    assert '"traded_at": _pending_buy.get("traded_at") or _now_et().isoformat()' in t
    assert '"id": None, "traded_at": _now_et().isoformat()' not in t


def test_sell_replay_unchecked_info_finding_neither_blocks_nor_needs_a_tick():
    from stock_analyzer import trade_entry_checks as tec
    info = tec.sell_replay_unchecked_finding()
    for dup in (_dups()["clean"], _dups()["none"]):
        base = tc.confirm_gate(dup)
        g = tc.confirm_gate(dup, entry_findings=[info])
        assert g["hard"] is base["hard"] is False
        assert g["needs_tick"] is base["needs_tick"] is False
        assert g["tick_label"] == ""
        assert g["blocks"][-1] == {"level": "info", "title": info["text"], "lines": []}
        assert g["blocks"][:-1] == base["blocks"]
    # beside a tick finding it adds no second box and no tick of its own
    g = tc.confirm_gate(_dups()["clean"], entry_findings=[_TICK_F, info])
    assert g["needs_tick"] is True and g["tick_label"] == "The date and time are right"
    assert [b["level"] for b in g["blocks"]] == ["warning", "info"]


def test_c2a_review_fixes_are_wired_in_app():
    t = _app_text()
    assert "trade_entry_checks.trade_date_widget_reset(" in t
    assert 'st.session_state["_tj_trade_date_seeded_on"] = _today_et()' in t
    assert 'st.session_state.pop("_tj_fill_time", None)' in t
    assert "trade_entry_checks.pick_date_caption(_now_et())" in t
    assert "trade_entry_checks.fill_time_missing_text(" in t
    assert "_dup_journal is None" in t
    seg = _slice(t, "trade_entry_checks.time_findings(", "if _entry_ok:\n                    try:")
    # the replay check has its own try / except that keeps the time findings
    assert seg.count("try:") == 2 and seg.count("except Exception:") == 2
    assert "trade_entry_checks.sell_replay_unchecked_finding()" in seg
    assert seg.index("trade_entry_checks.logging_as_text(") < seg.index("sell_replay_finding(")


def test_trade_date_reset_assigns_never_pops_and_the_widget_value_is_none():
    t = _app_text()
    blk = _slice(t, "_tj_now = _now_et()", 'st.session_state["_tj_trade_date_seeded_on"] = _today_et()')
    # Streamlit pushes a value to the browser only when session_state is assigned
    assert 'st.session_state["_tj_trade_date"] = trade_entry_checks.default_trade_date(_tj_now)' in blk
    assert 'st.session_state["_tj_fill_time"] = None' in blk
    assert 'if _tj_date_reset_this_run or "_tj_trade_date" not in st.session_state:' in blk
    assert ".pop(" not in blk
    # a non-None default beside an assignment draws Streamlit's duplicate-value warning
    di = _slice(blk, "st.date_input(", 'key="_tj_trade_date"')
    assert "value=None," in di
    # after a save the fill time is assigned too, so the browser clears (pop + rerun)
    assert ('if "_tj_fill_time" not in st.session_state:\n'
            '                    st.session_state["_tj_fill_time"] = None') in t


def test_a_reset_this_run_refuses_to_stage_and_says_why():
    t = _app_text()
    assert "_tj_date_reset_this_run = False" in t          # defined on the broker path too
    i = t.index("elif not _is_broker_trade and _tj_date_reset_this_run:")
    seg = t[i:i + 500]
    assert "st.warning(trade_entry_checks.DATE_RESET_NOTICE)" in seg
    assert '_tj_prefill"] = _stash_failed_entry' in seg
    # it sits before the pick-the-date check and before staging
    assert i < t.index('st.error("Pick the trade date.")')
    assert i < t.index("trade_entry_checks.resolve_traded_at(")


def test_replay_still_runs_when_the_time_checks_fail():
    t = _app_text()
    seg = _slice(t, "trade_entry_checks.time_findings(", "if _entry_ok:\n                    try:")
    assert '"time_unchecked": True' in seg
    assert "if _entry_f is not None and action" not in seg
    assert 'if action == "SELL" and not _override_sell:' in seg
    # the card still says the date / time weren't checked
    assert 'bool(_e.get("time_unchecked", False))' in t
