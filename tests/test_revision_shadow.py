"""Earnings rating-change flag shadow (finding #22): the pure row builder,
db.save_earnings_revision_shadow, the EOD-lane capture block, the System Trust
inventory row and the data-maintenance protected-table pin."""
from __future__ import annotations

import datetime
import math
from datetime import date, timedelta

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import db, revision_flag, system_health
from stock_analyzer import data_maintenance
from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS, EARNINGS_PARTIAL_TRIM_PCT, EARNINGS_REVISION_WATCH_NET,
)
from stock_analyzer.trim_sizing import fraction_trim_shares
from tests.test_cron_eod_write_failure_propagation import NOW, _patch_defaults, _payload

pytestmark = pytest.mark.fast

TODAY = date(2026, 9, 21)          # a Monday (trading day)
FRI, SAT = date(2026, 10, 9), date(2026, 10, 10)


def _iso(days):
    return (TODAY + timedelta(days=days)).isoformat()


def _pdf(*rows):
    return pd.DataFrame(list(rows))


def _prow(t, shares=10, weight=12.0, price=100.0, score=60.0, avail=True, exact=None):
    r = {"Ticker": t, "Shares": shares, "Weight (%)": weight, "Price": price,
         "Score": score, "Score Available": avail}
    if exact is not None:
        r["Shares Exact"] = exact
    return r


def _held(days, net=None, ups=0, downs=0):
    d = {"earnings": _iso(days)}
    if net is not None:
        d["revisions"] = {"net": net, "upgrades_90d": ups, "downgrades_90d": downs}
    return d


def _build(port, held, ctx=None, today=TODAY):
    return revision_flag.build_shadow_rows(port, held, ctx or {}, today, None, "cron")


# ── build_shadow_rows ────────────────────────────────────────────────────────

def test_window_edges():
    up = EARNINGS_IMMINENT_DAYS
    tks = {"M1": -1, "Z": 0, "U": up, "U1": up + 1}
    rows = _build(_pdf(*[_prow(t) for t in tks]),
                  {t: _held(d) for t, d in tks.items()})
    assert sorted(r["ticker"] for r in rows) == ["U", "Z"]
    by = {r["ticker"]: r for r in rows}
    assert by["Z"]["days_until"] == 0 and by["U"]["days_until"] == up


def test_flagged_unflagged_and_unknown_all_get_rows():
    held = {"F": _held(2, net=EARNINGS_REVISION_WATCH_NET, ups=0, downs=2),
            "C": _held(2, net=EARNINGS_REVISION_WATCH_NET + 3, ups=3, downs=0),
            "U": _held(2)}
    rows = _build(_pdf(_prow("F"), _prow("C"), _prow("U")), held)
    by = {r["ticker"]: r for r in rows}
    assert set(by) == {"F", "C", "U"}
    assert by["F"]["flagged"] is True and by["F"]["rev_state"] == "ok"
    assert by["F"]["net_rev"] == EARNINGS_REVISION_WATCH_NET
    assert (by["F"]["ups_90"], by["F"]["downs_90"], by["F"]["window_days"]) == (0, 2, 90)
    assert by["C"]["flagged"] is False and by["C"]["rev_state"] == "ok"
    assert by["U"]["flagged"] is False and by["U"]["rev_state"] == "unknown"
    assert by["U"]["net_rev"] is None
    assert all(r["flag_net"] == EARNINGS_REVISION_WATCH_NET for r in rows)
    assert all(r["rule_version"] == "rev-watch-v1" and r["source"] == "cron" for r in rows)
    assert all(r["capture_date"] == TODAY.isoformat() for r in rows)


def test_composite_none_when_score_withheld_and_fields_carried():
    rows = _build(_pdf(_prow("A", score=71.0, avail=True, weight=9.5, price=50.0),
                       _prow("B", score=50.0, avail=False)),
                  {"A": _held(1), "B": _held(1)})
    by = {r["ticker"]: r for r in rows}
    assert by["A"]["composite"] == 71.0 and by["A"]["score_available"] is True
    assert by["A"]["weight_pct"] == 9.5 and by["A"]["price"] == 50.0
    assert by["A"]["earnings_date"] == _iso(1)
    assert by["B"]["composite"] is None and by["B"]["score_available"] is False


def test_ctx_state_both_ways():
    ctx = {"A": {"beat_rate_pct": 40.0, "recent_reaction_direction": "bearish"}}
    rows = _build(_pdf(_prow("A"), _prow("B")), {"A": _held(1), "B": _held(1)}, ctx)
    by = {r["ticker"]: r for r in rows}
    assert by["A"]["ctx_state"] == "present"
    assert by["A"]["beat_rate_pct"] == 40.0 and by["A"]["reaction"] == "bearish"
    assert by["B"]["ctx_state"] == "none_or_unavailable"
    assert by["B"]["beat_rate_pct"] is None and by["B"]["reaction"] is None


def test_would_trim_shares_uses_exact_shares_and_the_constant():
    rows = _build(_pdf(_prow("A", shares=2, exact=10.5), _prow("B", shares=1, exact=1.0)),
                  {"A": _held(1), "B": _held(1)})
    by = {r["ticker"]: r for r in rows}
    assert by["A"]["shares"] == 10.5
    assert by["A"]["would_trim_shares"] == fraction_trim_shares(10.5, EARNINGS_PARTIAL_TRIM_PCT)
    assert by["A"]["partial_trim_pct"] == EARNINGS_PARTIAL_TRIM_PCT
    assert by["B"]["would_trim_shares"] == fraction_trim_shares(1.0, EARNINGS_PARTIAL_TRIM_PCT)


def test_malformed_rows_skipped_without_raising():
    port = _pdf(_prow("A"), {"Ticker": "BAD"}, _prow("C", weight=float("nan")))
    held = {"A": _held(1), "BAD": _held(1), "C": {"earnings": "not-a-date"}}
    rows = _build(port, held)
    by = {r["ticker"]: r for r in rows}
    assert set(by) == {"A", "BAD"}                 # C: unparseable date -> no window
    assert by["BAD"]["weight_pct"] is None and by["BAD"]["price"] is None
    assert by["BAD"]["composite"] is None and by["BAD"]["score_available"] is False
    assert by["A"]["composite"] is not None and by["A"]["score_available"] is True


def test_nan_flag_is_not_available():
    rows = _build(_pdf(_prow("A", avail=float("nan"), score=60.0)), {"A": _held(1)})
    assert rows[0]["score_available"] is False and rows[0]["composite"] is None


def test_nan_score_is_not_available():
    rows = _build(_pdf(_prow("A", avail=True, score=float("nan"))), {"A": _held(1)})
    assert rows[0]["score_available"] is False and rows[0]["composite"] is None


def test_missing_columns_keep_the_row_with_none_fields():
    port = pd.DataFrame([{"Ticker": "A", "Shares": 4}])
    rows = _build(port, {"A": _held(1)})
    assert len(rows) == 1
    r = rows[0]
    assert r["weight_pct"] is None and r["price"] is None and r["composite"] is None
    assert r["score_available"] is False
    # missing flag column with a finite Score -> available
    rows = _build(pd.DataFrame([{"Ticker": "A", "Shares": 4, "Score": 55.0}]), {"A": _held(1)})
    assert rows[0]["score_available"] is True and rows[0]["composite"] == 55.0


@pytest.mark.parametrize("port", [None, pd.DataFrame(), "x", 3])
def test_empty_or_bad_frame_yields_no_rows(port):
    assert _build(port, {"A": _held(1)}) == []


def test_bad_held_and_ctx_types_do_not_raise():
    assert revision_flag.build_shadow_rows(_pdf(_prow("A")), None, None, TODAY, None) == []
    rows = revision_flag.build_shadow_rows(_pdf(_prow("A")), {"A": _held(1)}, "junk", TODAY, None)
    assert rows[0]["ctx_state"] == "none_or_unavailable"


def test_duplicate_ticker_rows_deduped():
    rows = _build(_pdf(_prow("A"), _prow("A")), {"A": _held(1)})
    assert len(rows) == 1


# ── db.save_earnings_revision_shadow ─────────────────────────────────────────

class _Q:
    def __init__(self, log, table, exc):
        self._log, self._table, self._exc = log, table, exc

    def upsert(self, rows, **kw):
        self._rows, self._kw = list(rows), kw
        return self

    def execute(self):
        if self._exc is not None:
            raise self._exc
        self._log.append((self._table, self._rows, self._kw))


class _Client:
    def __init__(self, exc=None):
        self.log, self.exc = [], exc

    def table(self, name):
        return _Q(self.log, name, self.exc)


def _db(monkeypatch, exc=None, readonly=False):
    c = _Client(exc)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: readonly)
    monkeypatch.setattr(db, "_client", lambda: c)
    return c


def _row(d=FRI, t="AAPL", **kw):
    r = {"capture_date": d.isoformat(), "ticker": t, "source": "cron",
         "earnings_date": (d + timedelta(days=2)).isoformat(), "days_until": 2,
         "rev_state": "ok", "net_rev": -2, "flagged": True, "flag_net": -2,
         "composite": 61.0, "would_trim_shares": 3, "score_available": True}
    r.update(kw)
    return r


def test_save_read_only(monkeypatch):
    c = _db(monkeypatch, readonly=True)
    assert db.save_earnings_revision_shadow([_row()]) == {
        "attempted": 0, "saved": 0, "error": "read-only"}
    assert c.log == []


def test_save_empty_is_a_noop(monkeypatch):
    c = _db(monkeypatch)
    assert db.save_earnings_revision_shadow([]) == {"attempted": 0, "saved": 0, "error": None}
    assert c.log == []


def test_save_drops_non_trading_dates(monkeypatch):
    c = _db(monkeypatch)
    assert db.save_earnings_revision_shadow([_row(SAT)]) == {
        "attempted": 0, "saved": 0, "error": None, "skipped_non_trading": 1}
    assert c.log == []
    assert db.save_earnings_revision_shadow([_row(FRI), _row(SAT, "MU")]) == {
        "attempted": 1, "saved": 1, "error": None}


def test_save_payload_nan_to_none_ints_and_conflict_key(monkeypatch):
    c = _db(monkeypatch)
    res = db.save_earnings_revision_shadow(
        [_row(ticker=" aapl ", composite=float("nan"), net_rev=-3.0,
              weight_pct=float("inf"), beat_rate_pct="junk")])
    assert res == {"attempted": 1, "saved": 1, "error": None}
    table, rows, kw = c.log[0]
    assert table == "earnings_revision_shadow"
    assert kw == {"on_conflict": "capture_date,ticker,source", "ignore_duplicates": True}
    r = rows[0]
    assert r["ticker"] == "AAPL" and r["capture_date"] == FRI.isoformat()
    assert r["composite"] is None and r["weight_pct"] is None and r["beat_rate_pct"] is None
    assert r["net_rev"] == -3 and isinstance(r["net_rev"], int)
    assert r["flagged"] is True and r["score_available"] is True
    assert all(not isinstance(v, float) or math.isfinite(v) for v in r.values())


def test_save_skips_rows_missing_required_fields(monkeypatch):
    c = _db(monkeypatch)
    res = db.save_earnings_revision_shadow([_row(rev_state=None), _row(earnings_date=None),
                                            _row(t="")])
    assert res == {"attempted": 0, "saved": 0, "error": None}
    assert c.log == []


def test_save_missing_table_is_a_skip_not_an_error(monkeypatch):
    _db(monkeypatch, exc=Exception('relation "public.earnings_revision_shadow" does not exist'))
    assert db.save_earnings_revision_shadow([_row()]) == {
        "attempted": 1, "saved": 0, "error": None, "missing_table": True}
    _db(monkeypatch, exc=Exception("PGRST205 Could not find the table "
                                   "'public.earnings_revision_shadow'"))
    assert db.save_earnings_revision_shadow([_row()]).get("missing_table") is True


def test_save_real_error_is_reported(monkeypatch):
    _db(monkeypatch, exc=Exception("connection reset"))
    res = db.save_earnings_revision_shadow([_row()])
    assert res["saved"] == 0 and "connection reset" in res["error"]
    assert "missing_table" not in res


# ── EOD lane block ───────────────────────────────────────────────────────────

def _eod_payload():
    return _payload(port_df=_pdf(_prow("AAPL")),
                    held_data={"AAPL": {"earnings": (NOW.date() + timedelta(days=2)).isoformat()}})


def _eod(monkeypatch, save):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _eod_payload())
    monkeypatch.setattr(cr.db, "load_earnings_context_batch", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_earnings_revision_shadow", save)
    return cr._run_eod(NOW, force=True)


def test_eod_saved_is_clean_and_receives_rows(monkeypatch):
    seen = []
    rc = _eod(monkeypatch, lambda rows: seen.append(rows) or
              {"attempted": len(rows), "saved": len(rows), "error": None})
    assert rc == 0 and cr._LAST_LANE_FAILURE_DETAIL is None
    assert [r["ticker"] for r in seen[0]] == ["AAPL"]


def test_eod_missing_table_is_not_a_failure(monkeypatch):
    rc = _eod(monkeypatch, lambda _r: {"attempted": 1, "saved": 0, "error": None,
                                       "missing_table": True})
    assert rc == 0 and cr._LAST_LANE_FAILURE_DETAIL is None


@pytest.mark.parametrize("fn", [
    lambda _r: {"attempted": 1, "saved": 0, "error": "offline"},
    lambda _r: (_ for _ in ()).throw(RuntimeError("boom")),
])
def test_eod_real_failure_lands_in_failures_without_aborting(monkeypatch, fn):
    rc = _eod(monkeypatch, fn)
    assert rc == 1 and "revision shadow" in cr._LAST_LANE_FAILURE_DETAIL


def test_eod_builder_exception_does_not_abort_the_lane(monkeypatch):
    monkeypatch.setattr(revision_flag, "build_shadow_rows",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    rc = _eod(monkeypatch, lambda _r: {"attempted": 0, "saved": 0, "error": None})
    assert rc == 1 and "revision shadow" in cr._LAST_LANE_FAILURE_DETAIL


def test_eod_no_port_df_skips_quietly(monkeypatch):
    called = []
    _patch_defaults(monkeypatch)       # default payload: port_df None
    monkeypatch.setattr(cr.db, "save_earnings_revision_shadow",
                        lambda r: called.append(r) or {"attempted": 0, "saved": 0, "error": None})
    assert cr._run_eod(NOW, force=True) == 0
    assert called == []


def test_compat_note_is_not_a_failure(monkeypatch):
    rc = _eod(monkeypatch, lambda _r: {"attempted": 1, "saved": 1,
                                       "error": "compat: ignore_duplicates unsupported"})
    assert rc == 0


# ── inventory + protected-table pin ──────────────────────────────────────────

def test_system_health_inventory_has_the_store():
    st = [s for s in system_health._INVENTORY if s.table == "earnings_revision_shadow"]
    assert len(st) == 1
    assert st[0].lane == "eod" and st[0].cadence == "daily" and st[0].unconditional is False


def test_protected_tables_include_it():
    assert "earnings_revision_shadow" in data_maintenance._PROTECTED_TABLES
