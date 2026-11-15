"""stock_analyzer.effective_capital -- the one builder of the sizing capital inputs
(U7 C3 / C4; docs/plans/net-capital-broker-frame-c3-spec.md).

F-255's net capital and the F-306 budget basis are each the LOWER of the journal
figure and Robinhood's own positions. The budget must be fed the RAW journal net
(never F-255's reconciled one: a BUY logged after the sync is already subtracted
by `budget_basis`'s lag correction, so feeding it the reconciled net would
subtract it twice). The scenarios below run the REAL `margin.resolve_net_capital`,
`daily_budget.budget_basis` and `broker_frame` end to end through both the app's
path (`budget_view.postpass`) and the cron's (`headless_alert_engine.run_daily_budget`).

Scenario world: tickers AAA / BBB priced $100, Robinhood sync = the pair of a
positions snapshot and a broker-note cash record.
"""
import ast
import copy
import datetime
import html as _html
import itertools
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import broker_frame as bf
from stock_analyzer import budget_view as bv
from stock_analyzer import daily_budget
from stock_analyzer import effective_capital as ec
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import margin
from stock_analyzer import notify
from stock_analyzer.constants import (
    ACCOUNT_CASH_STALE_DAYS,
    NET_CAPITAL_BROKER_GAP_NOTE_PCT,
)

pytestmark = pytest.mark.fast

PX = {"AAA": 100.0, "BBB": 100.0}
_TRADE_COLS = ["traded_at", "action", "ticker", "shares", "price", "traded_at_time_kind"]
MON = datetime.date(2026, 11, 16)           # a Monday; EST (UTC-5)
S1_TEXT = ("net capital taken from Robinhood's positions: ~$1,000 below your "
           "journal's figure (see 💰 Account → Net capital check)")


def _utc(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _trades(rows):
    return pd.DataFrame(
        [{"traded_at": t, "action": a, "ticker": tk, "shares": sh, "price": px,
          "traded_at_time_kind": k} for t, a, tk, sh, px, k in rows],
        columns=_TRADE_COLS)


def _brief():
    return {"act_today": [], "review_list": [],
            "grow_today": {"tone": "bull", "new_picks": [], "add_positions": [],
                           "overflow_picks": []}}


def _world(holdings, cash, cash_at, snap, snap_at, rows, now_iso, today=MON,
           px=None, note=bf.BROKER_CASH_NOTE, brief=None):
    """-> (eff, postpass output, trades_df, now)."""
    px = px or PX
    hold = pd.DataFrame(holdings, columns=["Ticker", "Shares"])
    gross = sum(sh * px[t] for t, sh in holdings)
    cash_rec = {"cash_balance": cash, "note": note, "updated_at": cash_at}
    snapshot = {"positions": dict(snap), "captured_at": snap_at,
                "account_ids": ["x"], "all_accounts_ok": True, "kinds": {}}
    now = _utc(now_iso)
    jnet, jstate = margin.resolve_net_capital(gross, cash_rec, ACCOUNT_CASH_STALE_DAYS, now)
    trades = _trades(rows)
    eff = ec.resolve(gross, cash_rec, jnet, jstate, snapshot, hold, px, now,
                     trades_df=trades)
    out = bv.postpass(brief if brief is not None else _brief(),
                      eff["budget_inputs"], trades, {}, today, now)
    return eff, out, trades, now


def _no_suffix(text):
    assert text is not None
    assert "Robinhood" not in text and "cross-checked" not in text


# ---------------------------------------------------------------- T1
def test_t1_post_sync_logged_buy_is_not_subtracted_twice():
    # AAA 20 / BBB 80 @ $100 (G = 10,000); cash -4,000 at 16:15Z; the snapshot says
    # AAA 10 / BBB 80; a BUY of AAA 10 @ $100 filled 17:00Z (after the sync) is logged.
    eff, out, _t, _n = _world(
        [("AAA", 20), ("BBB", 80)], -4000.0, "2026-11-16T16:15:00Z",
        {"AAA": 10, "BBB": 80}, "2026-11-16T16:14:00Z",
        [("2026-11-16T17:00:00Z", "BUY", "AAA", 10, 100.0, "fill")],
        "2026-11-16T18:00:00Z")
    assert eff["frame"]["state"] == "ok" and eff["frame"]["adjust"] == pytest.approx(1000.0)
    assert eff["net"] == pytest.approx(5000.0) and eff["state"] == "levered"
    # the budget is fed the RAW journal pair (6,000), not the reconciled 5,000
    assert eff["budget_inputs"]["net_capital"] == pytest.approx(6000.0)
    c = out["cache"]
    assert c["basis"] == pytest.approx(5000.0)          # NOT 4,000 (a double subtraction)
    assert c["basis_source"] == "journal" and c["broker_gap"] == 0.0
    assert c["lag_adjustment"] == pytest.approx(-1000.0)
    _no_suffix(bv.status_line_text(c))


# ---------------------------------------------------------------- T2
def test_t2_log_time_sell_inside_the_sync_binds_the_budget_not_f255():
    # The sell actually filled BEFORE the sync (broker already has BBB 70, cash
    # -3,000) but was logged with a fill time after it.
    args = ([("AAA", 10), ("BBB", 70)], -3000.0, "2026-11-16T16:15:00Z",
            {"AAA": 10, "BBB": 70}, "2026-11-16T16:14:00Z")
    eff, out, _t, _n = _world(
        *args, [("2026-11-16T17:00:00Z", "SELL", "BBB", 10, 100.0, "fill")],
        "2026-11-16T18:00:00Z")
    assert eff["net"] == pytest.approx(5000.0)          # F-255 unchanged
    assert eff["rec"]["source"] == "journal"
    c = out["cache"]
    assert c["basis"] == pytest.approx(5000.0)          # raw 6,000 lowered to the frame's
    assert c["basis_source"] == "broker" and c["broker_gap"] == pytest.approx(1000.0)
    txt = bv.status_line_text(c)
    assert ("~$1,000 below your journal's figure, because a sell logged as after "
            "Robinhood's last sync is already in it (check that trade's fill time)") in txt


def test_t2_same_sell_recorded_same_day_is_uncertain_not_bound():
    eff, out, _t, _n = _world(
        [("AAA", 10), ("BBB", 70)], -3000.0, "2026-11-16T16:15:00Z",
        {"AAA": 10, "BBB": 70}, "2026-11-16T16:14:00Z",
        [("2026-11-16T17:00:00Z", "SELL", "BBB", 10, 100.0, "recorded")],
        "2026-11-16T18:00:00Z")
    c = out["cache"]
    assert c["basis"] == pytest.approx(5000.0) and c["basis_source"] == "journal"
    assert c["broker_gap"] == 0.0
    assert c["lag_uncertain"] == pytest.approx(1000.0) and c["lag_uncertain_tickers"] == ["BBB"]
    _no_suffix(bv.status_line_text(c))


# ---------------------------------------------------------------- T3
def test_t3_log_time_buy_inside_the_sync_stays_failsafe_journal():
    args = ([("AAA", 20), ("BBB", 80)], -5000.0, "2026-11-16T16:15:00Z",
            {"AAA": 20, "BBB": 80}, "2026-11-16T16:14:00Z")
    eff, out, _t, _n = _world(
        *args, [("2026-11-16T17:00:00Z", "BUY", "AAA", 10, 100.0, "fill")],
        "2026-11-16T18:00:00Z")
    assert eff["net"] == pytest.approx(5000.0)
    c = out["cache"]
    assert c["basis"] == pytest.approx(4000.0) and c["basis_source"] == "journal"
    assert c["broker_gap"] == 0.0
    _no_suffix(bv.status_line_text(c))
    # unknown-timed BUY: still subtracted (fail-safe), and flagged uncertain
    eff, out, _t, _n = _world(
        *args, [("2026-11-16T17:00:00Z", "BUY", "AAA", 10, 100.0, "recorded")],
        "2026-11-16T18:00:00Z")
    c = out["cache"]
    assert c["basis"] == pytest.approx(4000.0) and c["basis_source"] == "journal"
    assert c["lag_uncertain"] == pytest.approx(1000.0)


# ---------------------------------------------------------------- T4 (time context)
def test_t4_monday_open_sunday_pair_friday_unlogged_sell_monday_logged_buy():
    # Sunday 11-15 16:15 ET = 21:15Z. Friday 15:30 ET BBB sell never logged; Monday
    # 09:35 ET (14:35Z) BUY AAA 10 logged. Journal AAA 20 / BBB 80, broker AAA 10 /
    # BBB 70, cash -3,000 at the sync.
    eff, out, _t, _n = _world(
        [("AAA", 20), ("BBB", 80)], -3000.0, "2026-11-15T21:15:00Z",
        {"AAA": 10, "BBB": 70}, "2026-11-15T21:14:00Z",
        [("2026-11-16T14:35:00Z", "BUY", "AAA", 10, 100.0, "fill")],
        "2026-11-16T14:45:00Z")
    assert eff["net"] == pytest.approx(5000.0)
    c = out["cache"]
    assert c["basis"] == pytest.approx(5000.0) and c["basis_source"] == "broker"
    assert c["broker_gap"] == pytest.approx(1000.0)     # the sell only, not the buy
    assert S1_TEXT in bv.status_line_text(c)
    assert c["used_logged"] == pytest.approx(1000.0)    # Monday's buy counts as used


def test_t4_holiday_pair_with_an_unlogged_wednesday_sell_binds_on_the_holiday_and_friday():
    # Thanksgiving 2026-11-26; pair 11:15 ET = 16:15Z; the sell was Wed 15:30 ET.
    for now_iso, day in (("2026-11-26T15:00:00Z", datetime.date(2026, 11, 26)),
                         ("2026-11-27T14:45:00Z", datetime.date(2026, 11, 27))):
        eff, out, _t, _n = _world(
            [("AAA", 10), ("BBB", 80)], -3000.0, "2026-11-26T16:15:00Z",
            {"AAA": 10, "BBB": 70}, "2026-11-26T16:14:00Z", [], now_iso, today=day)
        assert eff["frame"]["state"] == "ok", now_iso
        c = out["cache"]
        assert c["basis_source"] == "broker" and c["broker_gap"] == pytest.approx(1000.0)


def test_t4_unlogged_sell_after_the_sync_binds_only_after_the_next_pair():
    # Sync 16:15 ET Monday = 21:15Z; an unlogged 16:30 ET sell of BBB 10.
    same = _world([("AAA", 20), ("BBB", 80)], -4000.0, "2026-11-16T21:15:00Z",
                  {"AAA": 20, "BBB": 80}, "2026-11-16T21:14:00Z", [],
                  "2026-11-16T21:45:00Z")           # 16:45 ET
    assert same[0]["frame"]["adjust"] == 0.0
    assert same[1]["cache"]["basis_source"] == "journal"
    nxt = _world([("AAA", 20), ("BBB", 80)], -3000.0, "2026-11-17T16:15:00Z",
                 {"AAA": 20, "BBB": 70}, "2026-11-17T16:14:00Z", [],
                 "2026-11-17T17:00:00Z", today=datetime.date(2026, 11, 17))
    assert nxt[1]["cache"]["basis_source"] == "broker"
    assert nxt[1]["cache"]["broker_gap"] == pytest.approx(1000.0)


def test_t4_logged_16_30_sell_never_binds_before_or_after():
    # The same sell, logged with its fill time (16:30 ET = 21:30Z Monday).
    row = [("2026-11-16T21:30:00Z", "SELL", "BBB", 10, 100.0, "fill")]
    before = _world([("AAA", 20), ("BBB", 70)], -4000.0, "2026-11-16T21:15:00Z",
                    {"AAA": 20, "BBB": 80}, "2026-11-16T21:14:00Z", row,
                    "2026-11-16T21:45:00Z")
    assert before[1]["cache"]["basis_source"] == "journal"
    assert before[1]["cache"]["broker_gap"] == 0.0
    after = _world([("AAA", 20), ("BBB", 70)], -3000.0, "2026-11-17T16:15:00Z",
                   {"AAA": 20, "BBB": 70}, "2026-11-17T16:14:00Z", row,
                   "2026-11-17T17:00:00Z", today=datetime.date(2026, 11, 17))
    assert after[1]["cache"]["basis_source"] == "journal"
    assert after[1]["cache"]["broker_gap"] == 0.0


@pytest.mark.parametrize("action, holdings, snap, cash", [
    # BUY filled Mon 10:00 ET, already in the 11:15 ET sync, logged Tuesday
    ("BUY", [("AAA", 20), ("BBB", 80)], {"AAA": 20, "BBB": 80}, -5000.0),
    # SELL filled Mon 10:00 ET, already in the sync, logged Tuesday
    ("SELL", [("AAA", 20), ("BBB", 70)], {"AAA": 20, "BBB": 70}, -3000.0),
])
def test_t4_day_late_log_with_a_real_fill_time_before_the_sync_never_binds(
        action, holdings, snap, cash):
    row = [("2026-11-16T15:00:00Z", action, "BBB" if action == "SELL" else "AAA",
            10, 100.0, "fill")]
    eff, out, _t, _n = _world(
        holdings, cash, "2026-11-16T16:15:00Z", snap, "2026-11-16T16:14:00Z", row,
        "2026-11-17T14:45:00Z", today=datetime.date(2026, 11, 17))
    c = out["cache"]
    assert c["basis_source"] == "journal" and c["broker_gap"] == 0.0
    assert c["lag_adjustment"] == 0.0 and c["used_logged"] == 0.0
    _no_suffix(bv.status_line_text(c))


# ---------------------------------------------------------------- split guard
def _split_world(kind, when):
    # NVDA split 4:1 logged: the journal holds the post-split 40, the pre-split
    # snapshot still says 10; AAA agrees.
    rows = [(when, "SPLIT 4:1", "NVDA", 40, 0.0, kind)]
    hold = pd.DataFrame([("AAA", 10), ("NVDA", 40)], columns=["Ticker", "Shares"])
    snap = {"positions": {"AAA": 10, "NVDA": 10}, "captured_at": "2026-11-16T16:14:00Z"}
    cash = {"cash_balance": -500.0, "note": bf.BROKER_CASH_NOTE,
            "updated_at": "2026-11-16T16:15:00Z"}
    return ec.resolve(5000.0, cash, 4500.0, "levered", snap, hold,
                      {"AAA": 100.0, "NVDA": 100.0}, _utc("2026-11-16T18:00:00Z"),
                      trades_df=_trades(rows))


@pytest.mark.parametrize("kind, when, excluded", [
    ("fill", "2026-11-16T17:00:00Z", True),          # AFTER the sync
    ("placeholder", "2026-11-16T21:00:00Z", True),   # same ET date: UNKNOWN
    ("fill", "2026-11-16T15:00:00Z", False),         # BEFORE the sync
])
def test_split_after_or_unknown_is_excluded_not_before(kind, when, excluded):
    f = _split_world(kind, when)["frame"]
    assert f["state"] == "ok"
    if excluded:
        assert [e["ticker"] for e in f["excluded"]] == ["NVDA"]
        assert f["adjust"] == 0.0 and f["rows"] == []
    else:
        assert f["excluded"] == []
        assert f["adjust"] == pytest.approx(3000.0)


def test_split_tickers_since_edges():
    snap_at = "2026-11-16T16:14:00Z"
    df = _trades([
        ("2026-11-16T17:00:00Z", "SPLIT 4:1", "nvda", 40, 0.0, "fill"),
        ("2026-11-16T17:00:00Z", "BUY", "AAA", 1, 100.0, "fill"),
        ("2026-11-16T15:00:00Z", "SPLIT", "OLD", 5, 0.0, "fill"),
    ])
    assert ec.split_tickers_since(df, snap_at) == frozenset({"NVDA"})
    assert ec.split_tickers_since(None, snap_at) == frozenset()
    assert ec.split_tickers_since(df, None) == frozenset()
    assert ec.split_tickers_since(df, "not a time") == frozenset()
    assert ec.split_tickers_since(pd.DataFrame({"x": [1]}), snap_at) == frozenset()
    assert ec.split_tickers_since(pd.DataFrame(columns=_TRADE_COLS), snap_at) == frozenset()
    assert isinstance(ec.split_tickers_since(df, snap_at), frozenset)


def test_split_lookup_is_positional_with_duplicate_index_labels():
    df = _trades([
        ("2026-11-16T15:00:00Z", "SPLIT", "OLD", 5, 0.0, "fill"),
        ("2026-11-16T17:00:00Z", "BUY", "AAA", 1, 100.0, "fill"),
    ])
    df.index = [0, 0]
    assert ec.split_tickers_since(df, "2026-11-16T16:14:00Z") == frozenset()


# ---------------------------------------------------------------- resolve contract
def test_resolve_never_raises_on_garbage():
    for args in (("x", 5, "y", None, 7, "z", None, None),
                 (None, None, None, None, None, None, None, None),
                 (1.0, {}, 1.0, "levered", {"positions": 3}, 4, {}, "now")):
        eff = ec.resolve(*args, trades_df="not a frame")
        assert set(eff) == {"net", "state", "rec", "frame", "acct", "cash_as_of",
                            "captured_at", "budget_inputs"}


def test_resolve_none_inputs_keep_the_journal_figure_with_an_unchecked_rec():
    eff = ec.resolve(10000.0, None, 6000.0, "levered", None, None, {}, None)
    assert eff["net"] == 6000.0 and eff["state"] == "levered"
    assert eff["frame"]["state"] == "no_snapshot"
    assert eff["rec"]["checked"] is False
    assert eff["cash_as_of"] is None and eff["captured_at"] is None
    assert eff["budget_inputs"]["cash_balance"] is None


def test_resolve_exposes_the_two_freshness_stamps():
    eff, _o, _t, _n = _world(
        [("AAA", 10)], -100.0, "2026-11-16T16:15:00Z", {"AAA": 10},
        "2026-11-16T16:14:00Z", [], "2026-11-16T18:00:00Z")
    assert eff["cash_as_of"] == "2026-11-16T16:15:00Z"
    assert eff["captured_at"] == "2026-11-16T16:14:00Z"
    assert eff["acct"]["cash_balance"] == -100.0


def test_resolve_failed_reconcile_falls_back_to_the_unchanged_journal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ec.broker_frame, "reconcile_net_capital", boom)
    eff = ec.resolve(10000.0, {"cash_balance": -4000.0, "updated_at": "x"}, 6000.0,
                     "levered", None, None, {}, None)
    assert eff["net"] == 6000.0 and eff["rec"]["source"] == "journal"
    assert eff["rec"]["checked"] is False


def test_resolve_failed_frame_is_none_and_unchecked(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ec.broker_frame, "broker_frame", boom)
    eff = ec.resolve(10000.0, None, 6000.0, "levered", None, None, {}, None)
    assert eff["frame"] is None and eff["net"] == 6000.0
    assert eff["budget_inputs"]["broker_frame"] is None


# ---------------------------------------------------------------- budget_inputs
def test_budget_inputs_keys_and_none_safety():
    bi = ec.budget_inputs(14000.0, 10000.0, "levered",
                          {"cash_balance": -4000.0, "updated_at": "t"})
    assert bi == {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
                  "cash_balance": -4000.0, "cash_as_of": "t", "broker_frame": None}
    bi = ec.budget_inputs(1.0, None, None, "not a dict", frame={"state": "ok"})
    assert bi["basis_state"] == "unknown" and bi["cash_balance"] is None
    assert bi["cash_as_of"] is None and bi["broker_frame"] == {"state": "ok"}


def test_budget_view_alias_keeps_the_old_four_argument_call():
    assert bv.budget_inputs is ec.budget_inputs
    bi = bv.budget_inputs(14000.0, 10000.0, "levered", {"cash_balance": -4000.0,
                                                        "updated_at": "t"})
    assert bi["broker_frame"] is None and bi["net_capital"] == 10000.0


def test_resolve_builds_the_budget_inputs_from_the_raw_journal_pair():
    tree = ast.parse(Path(ec.__file__).read_text(encoding="utf-8-sig"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "resolve")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "budget_inputs"]
    assert len(calls) == 1
    assert [a.id for a in calls[0].args[1:3]] == ["journal_net", "journal_state"]


# ---------------------------------------------------------------- reconciled_basis
def test_reconciled_basis_equals_budget_basis_when_there_is_no_frame():
    inputs = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
              "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
    day, now = datetime.date(2026, 10, 16), _utc("2026-10-16T13:50:00Z")
    trades = pd.DataFrame(columns=_TRADE_COLS)
    raw = daily_budget.budget_basis(14000.0, 10000.0, "levered", -4000.0,
                                    inputs["cash_as_of"], trades, day, now)
    rb = ec.reconciled_basis(inputs, trades, day, now)
    for k, v in raw.items():
        assert rb[k] == v
    assert rb["checked"] is False and rb["source"] == "journal" and rb["gap"] is None


def test_reconciled_basis_tolerates_a_non_dict_inputs():
    rb = ec.reconciled_basis(None, None, MON, _utc("2026-11-16T18:00:00Z"))
    assert rb["state"] == "unknown" and rb["checked"] is False


# ---------------------------------------------------------------- tightening-only grid
def test_tightening_only_end_to_end_over_a_grid():
    shares = (0, 10, 100)
    pxs = (50.0, 200.0)
    cashes = (-3000.0, 0.0, -20000.0)
    now = _utc("2026-11-16T18:00:00Z")
    day = MON
    seen_bind = 0
    for ja, jb, ba, bb, p, cash in itertools.product(shares, shares, shares, shares,
                                                     pxs, cashes):
        holdings = [(t, s) for t, s in (("AAA", ja), ("BBB", jb)) if s]
        px = {"AAA": p, "BBB": p}
        gross = sum(s * p for _t, s in holdings)
        cash_rec = {"cash_balance": cash, "note": bf.BROKER_CASH_NOTE,
                    "updated_at": "2026-11-16T16:15:00Z"}
        snapshot = {"positions": {k: v for k, v in (("AAA", ba), ("BBB", bb)) if v},
                    "captured_at": "2026-11-16T16:14:00Z"}
        jnet, jstate = margin.resolve_net_capital(gross, cash_rec, ACCOUNT_CASH_STALE_DAYS, now)
        hold = pd.DataFrame(holdings, columns=["Ticker", "Shares"])
        trades = pd.DataFrame(columns=_TRADE_COLS)
        eff = ec.resolve(gross, cash_rec, jnet, jstate, snapshot, hold, px, now,
                         trades_df=trades)
        if jnet is not None:
            assert eff["net"] <= jnet + 1e-9
        else:
            assert eff["net"] is None
        raw_inputs = ec.budget_inputs(gross, jnet, jstate, cash_rec, frame=None)
        raw = ec.reconciled_basis(raw_inputs, trades, day, now)
        new = ec.reconciled_basis(eff["budget_inputs"], trades, day, now)
        if raw["basis"] is not None:
            assert new["basis"] <= raw["basis"] + 1e-9
            seen_bind += new["source"] == "broker"
        else:
            assert new["basis"] is None
    assert seen_bind > 0


# ---------------------------------------------------------------- formats
def test_broker_fields_is_json_safe_primitives():
    basis = {"state": "ok", "basis": 5000.0, "source": "broker", "gap": 1000.0,
             "checked": True, "journal_net": 6000.0, "frame_state": "ok",
             "frame_adjust": 2000.0, "lag_uncertain_tickers": ["X"]}
    f = ec.broker_fields(basis)
    assert f["basis_source"] == "broker" and f["broker_gap"] == 1000.0
    assert f["broker_rec"] == {"state": "ok", "source": "broker", "gap": 1000.0,
                               "checked": True, "journal_net": 6000.0,
                               "frame_state": "ok", "frame_adjust": 2000.0}
    json.dumps(f)
    empty = ec.broker_fields(None)
    assert empty["basis_source"] == "journal" and empty["broker_gap"] is None
    assert set(empty["broker_rec"]) == set(f["broker_rec"])
    json.dumps(empty)


def test_log_line_formats():
    eff, _o, _t, _n = _world(
        [("AAA", 10)], -100.0, "2026-11-16T16:15:00Z", {"AAA": 10},
        "2026-11-16T16:14:00Z", [], "2026-11-16T18:00:00Z")
    assert ec.log_line(eff) == "net capital source=journal gap=0.00 frame=ok state=levered"
    eff = ec.resolve(10000.0, None, 6000.0, "levered", None, None, {}, None)
    assert ec.log_line(eff) == ("net capital source=journal gap=n/a "
                                "frame=no_snapshot state=levered")
    eff = ec.resolve(1.0, None, None, "unlevered", None, None, {}, None)
    assert ec.log_line(eff) == "net capital source=journal gap=n/a frame=no_snapshot state=unlevered"
    assert ec.log_line(None) == "net capital source=journal gap=n/a frame=none state=None"
    binding = {"rec": {"source": "broker", "gap": 1234.567}, "frame": {"state": "ok"},
               "state": "levered"}
    assert ec.log_line(binding) == "net capital source=broker gap=1234.57 frame=ok state=levered"


def test_budget_log_suffix_formats():
    assert ec.budget_log_suffix({"basis_source": "journal", "broker_gap": 0.0}) == (
        " · basis_source=journal broker_gap=0.00")
    assert ec.budget_log_suffix({"basis_source": "broker", "broker_gap": 1000}) == (
        " · basis_source=broker broker_gap=1000.00")
    assert ec.budget_log_suffix({}) == " · basis_source=n/a broker_gap=n/a"
    assert ec.budget_log_suffix(None) == " · basis_source=n/a broker_gap=n/a"
    assert ec.budget_log_suffix({"basis_source": None, "broker_gap": float("nan")}) == (
        " · basis_source=n/a broker_gap=n/a")


# ---------------------------------------------------------------- hygiene
def test_effective_capital_imports_no_margin_streamlit_db_or_budget_view():
    tree = ast.parse(Path(ec.__file__).read_text(encoding="utf-8-sig"))
    banned = ("stock_analyzer.margin", "streamlit", "stock_analyzer.db",
              "stock_analyzer.budget_view")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in banned, mod
            if mod == "stock_analyzer":
                assert all(a.name not in ("margin", "db", "budget_view")
                           for a in node.names)
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name not in banned, a.name


def test_effective_capital_is_a_gate_file():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".claude" / "hooks"))
    import pre_tool_checks as p
    assert "stock_analyzer/effective_capital.py" in p._GATE_FILES


# ---------------------------------------------------------------- budget_view / notify
def _pick(tk, comp, shares=24, price=100.0):
    return {"ticker": tk, "composite_score": comp, "score": 70.0, "price": price,
            "sector": "Tech", "thesis": "t", "asset_type": "stock",
            "xref": {"verdict": "go", "verdict_reconciled": {"verdict": "go"}},
            "sizing": {"shares": shares, "total_cost": round(shares * price, 2),
                       "stop": 94.0, "stop_pct": 6.0, "port_pct": 17.1,
                       "risk_budget": 100.0, "entry_lo": 99.0, "entry_hi": 101.0,
                       "ceiling_capped": False, "uncapped_shares": shares,
                       "portfolio_value": 10000.0, "sizing_version": 3,
                       "capital_capped": False, "capital_pct": float(shares)}}


T4_ARGS = dict(
    holdings=[("AAA", 20), ("BBB", 80)], cash=-3000.0, cash_at="2026-11-15T21:15:00Z",
    snap={"AAA": 10, "BBB": 70}, snap_at="2026-11-15T21:14:00Z",
    rows=[("2026-11-16T14:35:00Z", "BUY", "AAA", 10, 100.0, "fill")],
    now_iso="2026-11-16T14:45:00Z")


def test_cache_and_plan_summary_carry_the_three_fields():
    eff, out, trades, now = _world(**T4_ARGS)
    c = out["cache"]
    assert c["basis_source"] == "broker" and c["broker_gap"] == pytest.approx(1000.0)
    assert c["broker_rec"]["checked"] is True and c["broker_rec"]["source"] == "broker"
    assert c["broker_rec"]["frame_adjust"] == pytest.approx(2000.0)
    json.dumps({k: v for k, v in c.items()})
    plan = bv.plan_downstream(eff["budget_inputs"], trades, {}, c, MON, now, [])
    s = plan["summary"]
    assert s["basis_source"] == "broker" and s["broker_gap"] == pytest.approx(1000.0)
    assert s["broker_rec"] == c["broker_rec"]
    assert S1_TEXT in bv.status_line_text(s)


def test_buy_preview_uses_the_tightened_basis_and_says_nothing_extra():
    eff, _out, trades, now = _world(**T4_ARGS)
    bp = bv.buy_preview(eff["budget_inputs"], trades, 500.0, MON, now)
    assert bp["state"] == "ok"
    # (1,000 logged + 500 pending) / frame basis 5,000
    assert bp["pct"] == pytest.approx(30.0)
    assert "Robinhood" not in bp["text"]


def test_books_agree_is_byte_identical_to_no_frame_except_the_suffix():
    brief = {"grow_today": {"tone": "bull", "new_picks": [_pick("CCC", 80)],
                            "add_positions": [], "overflow_picks": []}}
    common = dict(holdings=[("AAA", 20), ("BBB", 80)], cash=-4000.0,
                  cash_at="2026-11-16T16:15:00Z", snap={"AAA": 20, "BBB": 80},
                  snap_at="2026-11-16T16:14:00Z", rows=[], now_iso="2026-11-16T18:00:00Z")
    eff, out, trades, now = _world(brief=copy.deepcopy(brief), **common)
    assert eff["frame"]["state"] == "ok" and eff["frame"]["adjust"] == 0.0
    no_frame_inputs = dict(eff["budget_inputs"], broker_frame=None)
    out0 = bv.postpass(copy.deepcopy(brief), no_frame_inputs, trades, {}, MON, now)
    for k in ("basis", "left", "per_ticker", "used_logged", "used_list", "budget", "state"):
        assert out["cache"][k] == out0["cache"][k], k
    assert out["brief"] == out0["brief"]
    assert bv.status_line_text(out["cache"]).count("cross-checked") == 0
    assert "sizes not cross-checked against Robinhood's positions" in bv.status_line_text(
        out0["cache"])


def test_status_line_one_percent_boundary_and_masking():
    base = {"state": "ok", "pct_used": 10.0, "used_logged": 0.0, "used_allocated": 0.0,
            "used_list": 1000.0, "left": 3000.0}
    gap1 = 10000.0 * NET_CAPITAL_BROKER_GAP_NOTE_PCT / 100.0
    rec = lambda gap: {"state": "ok", "checked": True, "gap": gap, "journal_net": 10000.0,
                       "source": "broker", "frame_state": "ok", "frame_adjust": gap}
    shown = bv.status_line_text(dict(base, broker_rec=rec(gap1)))
    assert "below your journal's figure" in shown
    assert "below your journal's figure" not in bv.status_line_text(
        dict(base, broker_rec=rec(gap1 - 0.99)))
    masked = bv.status_line_text(dict(base, broker_rec=rec(gap1)), mask=True)
    assert "$" not in masked and "below your journal's figure" in masked
    # a cache with no broker_rec (older producer) says nothing extra
    assert "Robinhood" not in bv.status_line_text(base)


def test_over_line_text_docstring_names_logged_buys_alone():
    assert "Logged buys alone are X% of net capital" in bv.over_line_text.__doc__


def test_email_banner_shows_the_disclosure_escaped():
    budget = {"state": "ok", "pct_used": 10.0, "used_logged": 0.0, "used_allocated": 0.0,
              "used_list": 1000.0, "left": 1000.0, "over_line_pct": None,
              "broker_rec": {"state": "ok", "checked": True, "gap": 1000.0,
                             "journal_net": 6000.0, "source": "broker",
                             "frame_state": "ok", "frame_adjust": 2000.0}}
    html = notify._budget_banner(budget, "this email")
    assert _html.escape(" · " + S1_TEXT) in html
    assert "Robinhood's" not in html            # the apostrophe is escaped
    # no broker_rec -> nothing extra; unchecked -> the not-cross-checked suffix
    assert "Robinhood" not in notify._budget_banner(
        {k: v for k, v in budget.items() if k != "broker_rec"}, "this email")
    unchecked = dict(budget, broker_rec={"state": "ok", "checked": False,
                                         "frame_state": "no_snapshot"})
    assert "not cross-checked" in notify._budget_banner(unchecked, "this email")


# ---------------------------------------------------------------- cron <-> app parity
def _parity_pick():
    return _pick("CCC", 80, shares=24)


def _run_cron(monkeypatch, eff, trades, now, saved):
    monkeypatch.setattr(hae.db, "load_trades_or_none", lambda: trades)
    monkeypatch.setattr(hae.db, "load_budget_allocations_or_none", lambda d: {})

    def _save(rows):
        saved.extend(rows)
        return {"attempted": len(rows), "saved": len(rows), "error": None,
                "dropped_invalid": 0, "skipped_non_trading": 0}
    monkeypatch.setattr(hae.db, "save_budget_allocations", _save)
    item = _parity_pick()
    out = hae.run_daily_budget(MON, now, eff["budget_inputs"], [("pick", item)], [])
    return out, item


@pytest.mark.parametrize("scenario", [
    dict(T4_ARGS),                                                   # a binding frame
    dict(holdings=[("AAA", 20), ("BBB", 80)], cash=-4000.0,          # books agree
         cash_at="2026-11-16T16:15:00Z", snap={"AAA": 20, "BBB": 80},
         snap_at="2026-11-16T16:14:00Z", rows=[], now_iso="2026-11-16T18:00:00Z"),
    dict(holdings=[("AAA", 20), ("BBB", 80)], cash=-4000.0,          # no snapshot paired
         cash_at="2026-11-16T16:15:00Z", snap={"AAA": 20, "BBB": 80},
         snap_at="2026-11-16T10:00:00Z", rows=[], now_iso="2026-11-16T18:00:00Z"),
])
def test_cron_and_app_size_identically(monkeypatch, scenario):
    brief = {"grow_today": {"tone": "bull", "new_picks": [_parity_pick()],
                            "add_positions": [], "overflow_picks": []}}
    eff, app_out, trades, now = _world(brief=brief, **scenario)
    saved: list = []
    cron_out, cron_item = _run_cron(monkeypatch, eff, trades, now, saved)
    cache, b = app_out["cache"], cron_out["budget"]
    app_sz = app_out["brief"]["grow_today"]["new_picks"][0]["sizing"]
    assert cache["state"] == b["state"] == "ok"
    assert cache["basis_source"] == b["basis_source"]
    assert cache["broker_gap"] == b["broker_gap"]
    assert cache["broker_rec"] == b["broker_rec"]
    assert cache["left"] == pytest.approx(b["left"])
    assert cache["used_logged"] == pytest.approx(b["used_logged"])
    assert app_sz.get("shares") == cron_item["sizing"].get("shares")
    assert cache["per_ticker"]["CCC"]["shares"] == cron_out["per_ticker"]["CCC"]["shares"]
    assert saved, "the sized pick must have been written"
    assert saved[0]["basis_dollars"] == pytest.approx(cache["basis"])
    json.dumps(b)                                   # persisted / emailed: primitives only


def test_cron_binding_budget_shows_s1_in_the_email(monkeypatch):
    brief = {"grow_today": {"tone": "bull", "new_picks": [_parity_pick()],
                            "add_positions": [], "overflow_picks": []}}
    eff, app_out, trades, now = _world(brief=brief, **T4_ARGS)
    cron_out, _item = _run_cron(monkeypatch, eff, trades, now, [])
    b = cron_out["budget"]
    assert b["basis_source"] == "broker" and b["broker_gap"] == pytest.approx(1000.0)
    html = notify._budget_banner(b, "this morning's emails")
    assert _html.escape(S1_TEXT) in html
    assert bv.status_line_text(app_out["cache"]).endswith(S1_TEXT)


def test_run_daily_budget_crash_dict_carries_the_three_null_fields(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(hae, "_run_daily_budget", boom)
    out = hae.run_daily_budget(MON, _utc("2026-11-16T18:00:00Z"), {}, [], [])
    b = out["budget"]
    assert b["basis_source"] is None and b["broker_gap"] is None and b["broker_rec"] is None
    assert out["crashed"]


def test_run_daily_budget_basis_failure_falls_back_to_unknown(monkeypatch):
    monkeypatch.setattr(hae.db, "load_trades_or_none", lambda: pd.DataFrame(columns=_TRADE_COLS))
    monkeypatch.setattr(hae.db, "load_budget_allocations_or_none", lambda d: {})

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(hae._effective_capital, "reconciled_basis", boom)
    out = hae.run_daily_budget(MON, _utc("2026-11-16T18:00:00Z"), {}, [], [])
    assert out["budget"]["state"] == "unknown"
    assert out["budget"]["basis_source"] == "journal" and out["budget"]["broker_gap"] is None


# ---------------------------------------------------------------- headless wiring
def _fn_src(module_path, name):
    src = Path(module_path).read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name)
    return fn, ast.get_source_segment(src, fn)


def test_headless_uses_the_helper_in_all_three_places():
    path = hae.__file__
    for name, needle in (("compute_morning_picks", "_effective_capital.resolve("),
                         ("compute_watchlist_entries", "_effective_capital.resolve("),
                         ("_run_daily_budget", "_effective_capital.reconciled_basis(")):
        _fn, seg = _fn_src(path, name)
        assert needle in seg, name
    _fn, seg = _fn_src(path, "compute_morning_picks")
    assert '"net_capital":  _f255_net_cap' not in seg   # the hand-built dict is gone
    assert '"net_capital_check"' in seg
    _fn, seg = _fn_src(path, "_run_daily_budget")
    assert "_daily_budget.budget_basis(" not in seg


def test_budget_view_has_one_basis_entry_point():
    src = Path(bv.__file__).read_text(encoding="utf-8-sig")
    assert src.count("effective_capital.reconciled_basis(") == 3
    assert "daily_budget.budget_basis(" not in src


def test_cron_runner_logs_the_net_capital_check_in_both_lanes():
    src = (Path(__file__).resolve().parent.parent / "cron_runner.py").read_text(
        encoding="utf-8-sig")
    assert src.count('payload.get("net_capital_check")') == 2
    assert src.count("_effective_capital.budget_log_suffix(") == 2
