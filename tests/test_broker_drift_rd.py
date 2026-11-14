"""RecTime C4, rule R-D (`docs/plans/fill-time-typed.md`): which journal rows may
excuse a broker-drift gap.

The defect: `broker_sync.tickers_traded_since` excused drift on any ticker with
a raw `traded_at > captured_at`. A blank-time hand log stores its Record time
(e.g. 14:30) and an import sits at 16:00 ET, so a trade the sync ALREADY held
excused a real, unrelated gap on that ticker (live 2026-11-13, #494).

R-D, per ticker in the diff:
  * excused outright: a CERTAIN-after row, or any SPLIT row that is after or
    undetermined;
  * only no-fill-time rows (unknown): excused ONLY if the drift (app minus broker)
    equals their net signed shares within `BROKER_DRIFT_SHARE_TOL`;
  * an unreadable `traded_at` row, or an unreadable frame / sync time, excuses
    nothing.
"""
import ast
import inspect
import pathlib

import pandas as pd
import pytest

from stock_analyzer import broker_sync as bs
from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL
from stock_analyzer.trade_time import normalize_traded_at

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent

SYNC = "2026-11-13T11:19:14-05:00"      # the 2026-11-13 broker sync, ET
NOW = "2026-11-13T17:00:00+00:00"       # "now" for the staleness check (fresh)
EMPTY = {"certain": [], "untimed": {}}


def _trades(rows):
    """rows: (ticker, action, shares, traded_at, kind)."""
    return pd.DataFrame([
        {"ticker": t, "action": a, "shares": float(s), "price": 100.0,
         "traded_at": ts, "traded_at_time_kind": k}
        for t, a, s, ts, k in rows
    ])


def _hold(pairs):
    return pd.DataFrame([{"Ticker": t, "Shares": s} for t, s in pairs])


def _snap(positions, captured_at=SYNC):
    return {"positions": positions, "captured_at": captured_at,
            "all_accounts_ok": True}


def _verdict(trades, positions, holdings, captured_at=SYNC):
    ex = bs.drift_excuses(trades, captured_at)
    return bs.decide_drift_banner(
        _snap(positions, captured_at), _hold(holdings), NOW, 25,
        recent_trade_tickers=ex["certain"], untimed_trade_shares=ex["untimed"])


def _qty(ticker, rh, app):
    return {"ticker": ticker, "rh_shares": rh, "app_shares": app, "diff": rh - app}


# --- the #494 replay ---------------------------------------------------------

def _rows_494():
    return pd.DataFrame([
        {"id": 493, "ticker": "MRVL", "action": "SELL", "shares": 3.0,
         "price": 160.0, "traded_at": "2026-11-13T09:56:00-05:00",
         "notes": None, "broker_txn_id": None, "fill_time_typed": True},
        {"id": 494, "ticker": "MRVL", "action": "BUY", "shares": 5.0,
         "price": 150.0, "traded_at": "2026-11-13T14:30:50.554958-05:00",
         "notes": None, "broker_txn_id": None, "fill_time_typed": False},
    ])


def test_494_replay_through_normalize_traded_at():
    """Recorded 14:30 after an 11:19 sync: unknown, not certain. App holds 5
    more than the broker -> the drift IS that buy, so it is excused (untimed)."""
    df = normalize_traded_at(_rows_494())
    ex = bs.drift_excuses(df, SYNC)
    assert ex == {"certain": [], "untimed": {"MRVL": 5.0}}
    out = bs.decide_drift_banner(
        _snap({"MRVL": 10.0}), _hold([("MRVL", 15.0)]), NOW, 25,
        recent_trade_tickers=ex["certain"], untimed_trade_shares=ex["untimed"])
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_sync"] == ["MRVL"]
    assert out["awaiting_untimed"] == ["MRVL"]


def test_494_replay_app_three_more_is_real_drift():
    df = normalize_traded_at(_rows_494())
    ex = bs.drift_excuses(df, SYNC)
    out = bs.decide_drift_banner(
        _snap({"MRVL": 10.0}), _hold([("MRVL", 13.0)]), NOW, 25,
        recent_trade_tickers=ex["certain"], untimed_trade_shares=ex["untimed"])
    assert out["state"] == "drift"
    assert [r["ticker"] for r in out["diff"]["qty_mismatch"]] == ["MRVL"]
    assert out["awaiting_sync"] == [] and out["awaiting_untimed"] == []


def test_the_old_raw_rule_would_have_excused_it():
    """Pin the premise: the stored Record time IS later than the sync."""
    ts = pd.to_datetime("2026-11-13T14:30:50.123456-05:00", utc=True)
    assert ts > pd.to_datetime(SYNC, utc=True)
    df = _trades([("MRVL", "BUY", 5, "2026-11-13T14:30:50.123456-05:00", "recorded")])
    assert bs.tickers_traded_since(df, SYNC) == []


def test_recorded_buy_5_app_5_more_excused_3_more_not():
    df = _trades([("MRVL", "BUY", 5, "2026-11-13T14:30:50.123456-05:00", "recorded")])
    ok = _verdict(df, {"MRVL": 10.0}, [("MRVL", 15.0)])
    assert ok["state"] == "awaiting_sync" and ok["awaiting_untimed"] == ["MRVL"]
    bad = _verdict(df, {"MRVL": 10.0}, [("MRVL", 13.0)])
    assert bad["state"] == "drift"
    assert "MRVL" in [r["ticker"] for r in bad["diff"]["qty_mismatch"]]


def test_unknown_sell_matches_a_negative_residual():
    df = _trades([("AAA", "SELL", 4, "2026-11-13T14:00:00-05:00", "recorded")])
    # app sold 4 the broker still holds -> app is 4 BELOW the broker
    assert _verdict(df, {"AAA": 10.0}, [("AAA", 6.0)])["state"] == "awaiting_sync"
    # app 4 ABOVE the broker is the wrong direction for a SELL
    assert _verdict(df, {"AAA": 10.0}, [("AAA", 14.0)])["state"] == "drift"


# --- tolerance boundary ------------------------------------------------------

def _diff_qty(app_minus_rh):
    return {"rh_only": [], "app_only": [],
            "qty_mismatch": [{"ticker": "AAA", "rh_shares": 10.0,
                              "app_shares": 10.0 + app_minus_rh,
                              "diff": -app_minus_rh}]}


def test_residual_exactly_at_the_tolerance_is_excused():
    real, awaiting, un = bs._split_awaiting(
        _diff_qty(BROKER_DRIFT_SHARE_TOL), set(), {"AAA": 0.0})
    assert awaiting == ["AAA"] and un == ["AAA"]
    assert real["qty_mismatch"] == []


def test_residual_just_over_the_tolerance_is_not_excused():
    real, awaiting, un = bs._split_awaiting(
        _diff_qty(BROKER_DRIFT_SHARE_TOL * 2), set(), {"AAA": 0.0})
    assert awaiting == [] and un == []
    assert len(real["qty_mismatch"]) == 1


def test_residual_inside_the_tolerance_of_a_nonzero_net_is_excused():
    _, awaiting, _ = bs._split_awaiting(
        _diff_qty(5.0 + BROKER_DRIFT_SHARE_TOL / 2), set(), {"AAA": 5.0})
    assert awaiting == ["AAA"]


# --- app_only / rh_only ------------------------------------------------------

def test_app_only_unknown_buy_opening_a_position():
    df = _trades([("NEWCO", "BUY", 5, "2026-11-13T14:30:50-05:00", "recorded")])
    hit = _verdict(df, {"AAA": 10.0}, [("AAA", 10.0), ("NEWCO", 5.0)])
    assert hit["state"] == "awaiting_sync" and hit["awaiting_untimed"] == ["NEWCO"]
    miss = _verdict(df, {"AAA": 10.0}, [("AAA", 10.0), ("NEWCO", 8.0)])
    assert miss["state"] == "drift"
    assert [r["ticker"] for r in miss["diff"]["app_only"]] == ["NEWCO"]


def test_rh_only_unknown_sell_of_the_whole_position():
    df = _trades([("OLD", "SELL", 5, "2026-11-13T14:30:50-05:00", "recorded")])
    hit = _verdict(df, {"AAA": 10.0, "OLD": 5.0}, [("AAA", 10.0)])
    assert hit["state"] == "awaiting_sync" and hit["awaiting_untimed"] == ["OLD"]
    miss = _verdict(
        _trades([("OLD", "SELL", 4, "2026-11-13T14:30:50-05:00", "recorded")]),
        {"AAA": 10.0, "OLD": 5.0}, [("AAA", 10.0)])
    assert miss["state"] == "drift"
    assert [r["ticker"] for r in miss["diff"]["rh_only"]] == ["OLD"]


# --- certain rows ------------------------------------------------------------

def test_certain_after_fill_row_excuses_any_drift_as_before():
    df = _trades([("AAA", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill")])
    ex = bs.drift_excuses(df, SYNC)
    assert ex == {"certain": ["AAA"], "untimed": {}}
    out = _verdict(df, {"AAA": 10.0}, [("AAA", 40.0)])      # nothing like 1 share
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_sync"] == ["AAA"]
    assert out["awaiting_untimed"] == []


def test_certain_wins_over_an_unknown_row_on_the_same_ticker():
    df = _trades([
        ("AAA", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill"),
        ("AAA", "BUY", 5, "2026-11-13T14:30:50.5-05:00", "recorded"),
    ])
    ex = bs.drift_excuses(df, SYNC)
    assert ex == {"certain": ["AAA"], "untimed": {}}
    out = _verdict(df, {"AAA": 10.0}, [("AAA", 40.0)])
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_untimed"] == []


def test_tickers_traded_since_returns_only_the_certain_set():
    df = _trades([
        ("AAA", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill"),
        ("BBB", "BUY", 5, "2026-11-13T14:30:50.5-05:00", "recorded"),
        ("CCC", "BUY", 2, "2026-11-13T16:00:00-05:00", "placeholder"),
        ("DDD", "BUY", 2, "2026-11-13T09:00:00-05:00", "fill"),
    ])
    assert bs.tickers_traded_since(df, SYNC) == ["AAA"]
    assert bs.drift_excuses(df, SYNC)["untimed"] == {"BBB": 5.0, "CCC": 2.0}


def test_tickers_are_upper_cased_and_stripped():
    df = _trades([(" aaa ", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill"),
                  (" bbb ", "BUY", 5, "2026-11-13T14:30:50.5-05:00", "recorded")])
    assert bs.drift_excuses(df, SYNC) == {"certain": ["AAA"], "untimed": {"BBB": 5.0}}


def test_a_missing_kind_column_reads_as_fill_the_status_quo():
    df = pd.DataFrame([{"ticker": "AAA", "action": "BUY", "shares": 1.0,
                        "price": 1.0, "traded_at": "2026-11-13T12:30:00-05:00"}])
    assert bs.tickers_traded_since(df, SYNC) == ["AAA"]


# --- SPLIT rows --------------------------------------------------------------

def test_same_day_recorded_split_after_the_sync_still_excuses_drift():
    """A same-day SPLIT is "recorded", so it lands in unknown, never after;
    the rule looks in both, keeping today's behaviour of a split excusing."""
    df = _trades([("AAA", "SPLIT", 40, "2026-11-13T14:00:00.5-05:00", "recorded")])
    ex = bs.drift_excuses(df, SYNC)
    assert ex == {"certain": ["AAA"], "untimed": {}}
    out = _verdict(df, {"AAA": 10.0}, [("AAA", 40.0)])
    assert out["state"] == "awaiting_sync" and out["awaiting_untimed"] == []


def test_split_decorated_action_matches_case_insensitively():
    df = _trades([("AAA", "split 4:1", 40, "2026-11-13T14:00:00.5-05:00", "recorded")])
    assert bs.drift_excuses(df, SYNC)["certain"] == ["AAA"]


def test_a_split_before_the_sync_excuses_nothing():
    df = _trades([("AAA", "SPLIT", 40, "2026-11-13T09:00:00-05:00", "fill")])
    assert bs.drift_excuses(df, SYNC) == EMPTY
    assert _verdict(df, {"AAA": 10.0}, [("AAA", 40.0)])["state"] == "drift"


def test_a_split_row_adds_no_untimed_shares():
    df = _trades([
        ("AAA", "SPLIT", 40, "2026-11-13T14:00:00.5-05:00", "recorded"),
        ("BBB", "BUY", 5, "2026-11-13T14:30:50.5-05:00", "recorded"),
    ])
    assert bs.drift_excuses(df, SYNC) == {"certain": ["AAA"], "untimed": {"BBB": 5.0}}


# --- placeholder rows --------------------------------------------------------

def test_placeholder_on_the_sync_date_needs_an_exact_match():
    df = _trades([("CSCO", "BUY", 10, "2026-11-13T16:00:00-05:00", "placeholder")])
    assert bs.drift_excuses(df, SYNC) == {"certain": [], "untimed": {"CSCO": 10.0}}
    assert _verdict(df, {"CSCO": 5.0}, [("CSCO", 15.0)])["state"] == "awaiting_sync"
    assert _verdict(df, {"CSCO": 5.0}, [("CSCO", 40.0)])["state"] == "drift"


def test_placeholder_on_a_later_et_date_is_certain():
    df = _trades([("CSCO", "BUY", 10, "2026-11-16T16:00:00-05:00", "placeholder")])
    assert bs.drift_excuses(df, SYNC) == {"certain": ["CSCO"], "untimed": {}}
    assert _verdict(df, {"CSCO": 5.0}, [("CSCO", 40.0)])["state"] == "awaiting_sync"


# --- unreadable / offline ----------------------------------------------------

def test_an_unreadable_traded_at_row_excuses_nothing():
    df = _trades([("AAA", "BUY", 5, "garbage", "recorded"),
                  ("BBB", "BUY", 5, None, "fill")])
    assert bs.drift_excuses(df, SYNC) == EMPTY
    out = _verdict(df, {"AAA": 10.0}, [("AAA", 15.0)])
    assert out["state"] == "drift"


def test_unreadable_inputs_explain_nothing_away():
    good = _trades([("AAA", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill")])
    assert bs.drift_excuses(None, SYNC) == EMPTY
    assert bs.drift_excuses(good, None) == EMPTY
    assert bs.drift_excuses(good, "garbage") == EMPTY
    assert bs.drift_excuses(good.drop(columns=["traded_at"]), SYNC) == EMPTY
    assert bs.drift_excuses(pd.DataFrame(), SYNC) == EMPTY
    assert bs.tickers_traded_since(None, SYNC) == []
    assert bs.tickers_traded_since(good, "garbage") == []


# --- time-context matrix -----------------------------------------------------

def test_weekend_visit_friday_rows_against_a_friday_close_sync():
    fri_sync = "2026-11-13T16:15:00-05:00"
    # recorded 14:00, before the 16:15 sync: certainly before -> nothing excused
    before = _trades([("AAA", "BUY", 5, "2026-11-13T14:00:00.5-05:00", "recorded")])
    assert bs.drift_excuses(before, fri_sync) == EMPTY
    # recorded 16:30 Friday, after the sync, same ET date -> unknown
    after_hrs = _trades([("AAA", "BUY", 5, "2026-11-13T16:30:00.5-05:00", "recorded")])
    assert bs.drift_excuses(after_hrs, fri_sync) == {
        "certain": [], "untimed": {"AAA": 5.0}}
    # recorded the next trading day (Monday) -> certain
    monday = _trades([("AAA", "BUY", 5, "2026-11-16T10:00:00.5-05:00", "recorded")])
    assert bs.drift_excuses(monday, fri_sync) == {"certain": ["AAA"], "untimed": {}}


def test_dst_fall_back_placeholder_dates_use_the_et_calendar_day():
    # 2026-11-01 is the fall-back day. Sync at 23:30 ET on 11-01 is already
    # 11-02 in UTC (04:30Z); a UTC-date reading would call the 11-02
    # placeholder "same date" (unknown). By ET date it is certain.
    sync = "2026-11-01T23:30:00-05:00"
    nxt = _trades([("AAA", "BUY", 5, "2026-11-02T16:00:00-05:00", "placeholder")])
    assert bs.drift_excuses(nxt, sync) == {"certain": ["AAA"], "untimed": {}}
    same = _trades([("AAA", "BUY", 5, "2026-11-01T16:00:00-05:00", "placeholder")])
    # 11-01 16:00 ET is the sync's own ET date: a placeholder carries only
    # its date, so same date -> can't tell (S3), needs the exact-shares match.
    assert bs.drift_excuses(same, sync) == {"certain": [], "untimed": {"AAA": 5.0}}
    # And the UTC form of a post-DST anchor against a pre-DST (EDT) sync.
    edt_sync = "2026-10-31T20:00:00-04:00"
    utc_row = _trades([("AAA", "BUY", 5, "2026-11-01T21:00:00+00:00", "placeholder")])
    assert bs.drift_excuses(utc_row, edt_sync) == {"certain": ["AAA"], "untimed": {}}


def test_dst_day_placeholder_on_the_sync_date_is_unknown():
    sync = "2026-11-01T12:00:00-05:00"
    df = _trades([("AAA", "BUY", 5, "2026-11-01T16:00:00-05:00", "placeholder")])
    assert bs.drift_excuses(df, sync) == {"certain": [], "untimed": {"AAA": 5.0}}


# --- back-compat of the public helpers ---------------------------------------

def test_split_awaiting_sync_stays_a_two_tuple():
    diff = {"rh_only": [], "app_only": [],
            "qty_mismatch": [_qty("AAA", 10.0, 15.0)]}
    out = bs.split_awaiting_sync(diff, [], {"AAA": 5.0})
    assert len(out) == 2
    real, awaiting = out
    assert awaiting == ["AAA"] and real["qty_mismatch"] == []
    # no untimed map: unchanged behaviour
    real2, awaiting2 = bs.split_awaiting_sync(diff, [])
    assert awaiting2 == [] and real2 is diff


def test_awaiting_untimed_is_additive_and_empty_on_a_normal_verdict():
    out = bs.decide_drift_banner(
        _snap({"NVDA": 10.0}), _hold([("NVDA", 40.0)]), NOW, 25,
        recent_trade_tickers=["NVDA"])
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_sync"] == ["NVDA"] and out["awaiting_untimed"] == []
    out2 = bs.decide_drift_banner(
        _snap({"NVDA": 10.0}), _hold([("NVDA", 40.0)]), NOW, 25)
    assert out2["state"] == "drift" and out2["awaiting_untimed"] == []


def test_awaiting_untimed_is_a_subset_of_awaiting_sync():
    """One ticker excused by a certain row, one only by the exact match."""
    df = _trades([
        ("AAA", "BUY", 1, "2026-11-13T12:30:00-05:00", "fill"),
        ("BBB", "BUY", 5, "2026-11-13T14:30:50.5-05:00", "recorded"),
    ])
    out = _verdict(df, {"AAA": 10.0, "BBB": 10.0}, [("AAA", 40.0), ("BBB", 15.0)])
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_sync"] == ["AAA", "BBB"]
    assert out["awaiting_untimed"] == ["BBB"]


# --- wiring (app.py has no tests: pin BOTH ends of each call) ----------------

def _calls(tree, attr):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def _kw(call):
    return {k.arg for k in call.keywords}


def test_decide_drift_banner_takes_the_untimed_kwarg():
    params = inspect.signature(bs.decide_drift_banner).parameters
    assert "untimed_trade_shares" in params and "recent_trade_tickers" in params


def test_headless_engine_passes_both_kwargs_from_one_drift_excuses_call():
    src = (_ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    decides = _calls(tree, "decide_drift_banner")
    assert decides, "headless engine no longer calls decide_drift_banner"
    for c in decides:
        assert {"recent_trade_tickers", "untimed_trade_shares"} <= _kw(c)
    assert len(_calls(tree, "drift_excuses")) == len(decides)
    assert not _calls(tree, "tickers_traded_since")


def test_app_home_passes_the_untimed_kwarg_and_the_renderer_reads_it():
    from tests._ast_cache import parse_file_cached
    tree = parse_file_cached(_ROOT / "app.py")
    decides = _calls(tree, "decide_drift_banner")
    assert decides, "app.py no longer calls decide_drift_banner"
    for c in decides:
        assert {"recent_trade_tickers", "untimed_trade_shares"} <= _kw(c)
    assert len(_calls(tree, "drift_excuses")) == len(decides)
    assert not _calls(tree, "tickers_traded_since")
    renderer = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_render_broker_drift")
    consts = {n.value for n in ast.walk(renderer)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "awaiting_untimed" in consts and "awaiting_sync" in consts


def test_split_lookup_is_by_position_not_by_duplicate_index_label():
    # Two rows share index label 0: a SPLIT on AAA BEFORE the sync and a typed
    # BUY on BBB AFTER it. A label look-up would pull the old split in through
    # BBB's label and excuse AAA's drift; the positional look-up must not.
    df = _trades([
        ("AAA", "SPLIT", 20, "2026-11-13T09:00:00-05:00", "fill"),
        ("BBB", "BUY", 5, "2026-11-13T14:00:00-05:00", "fill"),
    ])
    df.index = [0, 0]
    assert bs.drift_excuses(df, SYNC) == {"certain": ["BBB"], "untimed": {}}
