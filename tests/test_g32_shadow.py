"""G-32 C2 shadow capture: pure module, the _grow_today attach (measure-only,
can't route or crash), db.save_g32_shadow and the scan-lane capture block."""
from __future__ import annotations

import math
from datetime import date

import pytest

import cron_runner as cr
from stock_analyzer import daily_briefing, db, g32_shadow
from stock_analyzer.constants import RR_ENTRY_MIN
from tests.test_cron_lane_write_failure_rc import _run_scan
from tests.test_grow_today_owns_buys import (
    _ADDS_HELD_DATA, _TODAY, _adds_book, _adds_kwargs, _bundle, _cap_fixture,
    _run, _tk,
)
from tests.test_daily_briefing import _scanner_df
from stock_analyzer.daily_briefing import _grow_today

pytestmark = pytest.mark.fast

FRI, SAT = date(2026, 10, 9), date(2026, 10, 10)


def _bnd(price=100.0, stop=94.0, base=112.0, src="analyst"):
    return {"current_price": price, "stop": stop,
            "targets": {"base": base, "base_source": src}}


# ── shadow_measure ───────────────────────────────────────────────────────────

def test_shadow_measure_bundle_and_lane_basis():
    m = g32_shadow.shadow_measure(_bnd(), lane_entry=101.0, lane_stop=95.0)
    assert m["state"] == "ok" and m["rr"] == pytest.approx(2.0)
    assert (m["entry"], m["stop"], m["base"], m["base_source"]) == (100.0, 94.0, 112.0, "analyst")
    assert (m["lane_entry"], m["lane_stop"]) == (101.0, 95.0)
    assert m["rr_lane"] == pytest.approx((112 - 101) / 6)


def test_shadow_measure_no_bundle_is_unknown():
    m = g32_shadow.shadow_measure(None)
    assert m["state"] == "unknown" and m["why"] == "no_bundle" and m["rr"] is None
    assert m["entry"] is None and m["base"] is None and m["rr_lane"] is None


def test_shadow_measure_without_lane_inputs_has_no_lane_rr():
    m = g32_shadow.shadow_measure(_bnd())
    assert m["rr"] is not None and m["lane_entry"] is None and m["rr_lane"] is None


def test_shadow_measure_non_finite_inputs_become_none():
    m = g32_shadow.shadow_measure(_bnd(price=float("nan")), lane_entry=float("inf"))
    assert m["entry"] is None and m["lane_entry"] is None and m["state"] == "unknown"


# ── build_shadow_rows ────────────────────────────────────────────────────────

def _item(t, **kw):
    d = {"ticker": t, "entry_rr": g32_shadow.shadow_measure(_bnd(), 100.0, 94.0)}
    d.update(kw)
    return d


def test_rows_lane_mapping_and_composite_field_per_lane():
    grow = {
        "new_picks": [_item("aa", composite_score=71.0, is_mover=True)],
        "add_positions": [_item("BB", score=80.0)],
        "overflow_picks": [_item("CC", composite_score=66.0, is_mover=False, asset_type="etf")],
    }
    rows = g32_shadow.build_shadow_rows(grow, FRI, "cron")
    by = {r["lane"]: r for r in rows}
    assert [r["lane"] for r in rows] == ["pick", "add", "overflow"]
    assert by["pick"]["ticker"] == "AA" and by["pick"]["composite"] == 71.0
    assert by["pick"]["is_mover"] is True
    assert by["add"]["composite"] == 80.0 and by["add"]["is_mover"] is None
    assert by["overflow"]["composite"] == 66.0 and by["overflow"]["asset_type"] == "etf"
    assert by["pick"]["asset_type"] == "stock"
    assert all(r["source"] == "cron" and r["rec_date"] == FRI for r in rows)
    assert all(r["rr_min"] == RR_ENTRY_MIN for r in rows)
    assert by["pick"]["rr"] == pytest.approx(2.0) and by["pick"]["rr_state"] == "ok"
    assert by["pick"]["base_source"] == "analyst"


def test_item_without_the_field_is_an_unknown_row():
    rows = g32_shadow.build_shadow_rows({"new_picks": [{"ticker": "X", "composite_score": 70}]},
                                        FRI, "cron")
    assert len(rows) == 1 and rows[0]["rr"] is None and rows[0]["rr_state"] == "unknown"


@pytest.mark.parametrize("grow", [None, [], "x", 3, {}, {"new_picks": None},
                                  {"new_picks": "nope"}])
def test_non_dict_or_missing_buckets_yield_no_rows(grow):
    assert g32_shadow.build_shadow_rows(grow, FRI, "cron") == []


def test_dedupe_keeps_first_per_date_lane_ticker():
    grow = {"new_picks": [_item("A", composite_score=1.0), _item("a", composite_score=2.0)],
            "overflow_picks": [_item("A", composite_score=3.0)]}
    rows = g32_shadow.build_shadow_rows(grow, FRI, "cron")
    assert [(r["lane"], r["composite"]) for r in rows] == [("pick", 1.0), ("overflow", 3.0)]


# ── _grow_today attach: present, and unable to route or crash ────────────────

def _strip(g):
    out = {}
    for k, v in g.items():
        if isinstance(v, list):
            out[k] = [{kk: vv for kk, vv in i.items() if kk != "entry_rr"} if isinstance(i, dict) else i
                      for i in v]
        else:
            out[k] = v
    return out


def _fixture_with_everything():
    rows, comps = _cap_fixture(n_extra=2)
    for b in comps.values():
        b.update(current_price=100.0, targets={"base": 112.0, "base_source": "analyst"})
    held = dict(_ADDS_HELD_DATA)
    held["CLEAN"] = dict(held["CLEAN"], current_price=100.0,
                         targets={"base": 120.0, "base_source": "analyst"})

    def run():
        return _grow_today(_adds_book(), _scanner_df(rows), [], held, _TODAY, 100_000.0,
                           {"tone": "bull"}, composites=comps, **_adds_kwargs())
    return run


def test_field_attached_to_picks_adds_and_overflow():
    g = _fixture_with_everything()()
    assert g["new_picks"] and g["add_positions"] and g["overflow_picks"]
    for key in ("new_picks", "add_positions", "overflow_picks"):
        for it in g[key]:
            m = it["entry_rr"]
            assert m["state"] == "ok", (key, it["ticker"])
            assert m["rr"] == pytest.approx((112.0 - 100.0) / (100.0 - _bundle()["stop"]))
    add = g["add_positions"][0]["entry_rr"]
    assert add["lane_stop"] == 90.0 and add["base"] == 120.0   # held bundle stop + target
    assert add["lane_entry"] is not None
    # overflow carries the same measurement the pick lane would have produced
    assert g["overflow_picks"][0]["entry_rr"]["lane_stop"] is not None


def test_item_with_no_bundle_still_gets_the_field():
    rows, comps = _cap_fixture(n_extra=0)
    g = _run(rows, comps)
    assert g["new_picks"]
    assert all(i["entry_rr"]["state"] == "unknown" for i in g["new_picks"])  # fixture has no current_price/targets


def test_shadow_failure_cannot_change_or_crash_the_lists(monkeypatch):
    run = _fixture_with_everything()
    baseline = _strip(run())

    def boom(*_a, **_k):
        raise RuntimeError("shadow boom")
    monkeypatch.setattr(g32_shadow, "shadow_measure", boom)
    broken = run()
    assert _strip(broken) == baseline                       # tickers, order, sizing identical
    assert _tk(broken["new_picks"]) == _tk(baseline["new_picks"])
    for key in ("new_picks", "add_positions", "overflow_picks"):
        assert all(i["entry_rr"]["state"] == "unknown" for i in broken[key])


def test_field_is_measure_only_in_source():
    import inspect
    src = inspect.getsource(daily_briefing._grow_today)
    assert src.count('"entry_rr"') == 2        # the attach + its fallback; nothing reads it
    # the attach sits after every routing decision: last thing before the return
    assert src.index("G-32 C2 shadow: measure-only") > src.index("add_positions.sort(")


# ── db.save_g32_shadow ───────────────────────────────────────────────────────

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
    r = {"rec_date": d, "lane": "pick", "ticker": t, "source": "cron", "rr": 2.5,
         "entry": 100.0, "rr_state": "ok", "rr_min": RR_ENTRY_MIN, "is_mover": False}
    r.update(kw)
    return r


def test_save_read_only(monkeypatch):
    c = _db(monkeypatch, readonly=True)
    assert db.save_g32_shadow([_row()]) == {"attempted": 0, "saved": 0, "error": "read-only"}
    assert c.log == []


def test_save_empty_is_a_noop(monkeypatch):
    c = _db(monkeypatch)
    assert db.save_g32_shadow([]) == {"attempted": 0, "saved": 0, "error": None}
    assert c.log == []


def test_save_drops_weekend_rows(monkeypatch):
    c = _db(monkeypatch)
    assert db.save_g32_shadow([_row(SAT)]) == {
        "attempted": 0, "saved": 0, "error": None, "skipped_non_trading": 1}
    assert c.log == []
    res = db.save_g32_shadow([_row(FRI), _row(SAT, "MU")])
    assert res == {"attempted": 1, "saved": 1, "error": None}


def test_save_success_payload_and_conflict_key(monkeypatch):
    c = _db(monkeypatch)
    res = db.save_g32_shadow([_row(rr=float("nan"), entry=float("inf"), ticker=" aapl ")])
    assert res == {"attempted": 1, "saved": 1, "error": None}
    table, rows, kw = c.log[0]
    assert table == "g32_shadow"
    assert kw == {"on_conflict": "rec_date,lane,ticker,source", "ignore_duplicates": True}
    r = rows[0]
    assert r["ticker"] == "AAPL" and r["rec_date"] == "2026-10-09"
    assert r["rr"] is None and r["entry"] is None and r["rr_min"] == RR_ENTRY_MIN
    assert all(not isinstance(v, float) or math.isfinite(v) for v in r.values())


def test_save_missing_table_is_a_skip_not_an_error(monkeypatch):
    _db(monkeypatch, exc=Exception('relation "public.g32_shadow" does not exist'))
    res = db.save_g32_shadow([_row()])
    assert res == {"attempted": 1, "saved": 0, "error": None, "missing_table": True}
    _db(monkeypatch, exc=Exception("PGRST205 Could not find the table 'public.g32_shadow'"))
    assert db.save_g32_shadow([_row()]).get("missing_table") is True


def test_save_real_error_is_reported(monkeypatch):
    _db(monkeypatch, exc=Exception("connection reset"))
    res = db.save_g32_shadow([_row()])
    assert res["saved"] == 0 and "connection reset" in res["error"]
    assert "missing_table" not in res


# ── cron scan lane ───────────────────────────────────────────────────────────

def test_scan_missing_table_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(cr.db, "save_g32_shadow",
                        lambda _r: {"attempted": 2, "saved": 0, "error": None, "missing_table": True})
    rc, notified = _run_scan(monkeypatch)
    assert rc == 0 and notified == []


def test_scan_g32_saved_is_clean(monkeypatch):
    monkeypatch.setattr(cr.db, "save_g32_shadow",
                        lambda _r: {"attempted": 2, "saved": 2, "error": None})
    rc, _ = _run_scan(monkeypatch)
    assert rc == 0


@pytest.mark.parametrize("fn", [
    lambda _r: {"attempted": 1, "saved": 0, "error": "offline"},
    lambda _r: (_ for _ in ()).throw(RuntimeError("boom")),
])
def test_scan_g32_real_failure_fails_the_lane_only_via_its_own_key(monkeypatch, fn):
    monkeypatch.setattr(cr.db, "save_g32_shadow", fn)
    rc, notified = _run_scan(monkeypatch)
    assert rc == 1 and "g32 shadow" in cr._LAST_LANE_FAILURE_DETAIL
    assert "recommendations" not in cr._LAST_LANE_FAILURE_DETAIL
    assert "gate_suppressions" not in cr._LAST_LANE_FAILURE_DETAIL


# ── review follow-ups ────────────────────────────────────────────────────────

def test_asset_type_falls_back_to_the_measured_bundle_then_stock():
    etf_b = dict(_bnd(), asset_type="etf")
    grow = {"add_positions": [{"ticker": "A", "score": 70,
                               "entry_rr": g32_shadow.shadow_measure(etf_b, 100.0, 94.0)}],
            "new_picks": [{"ticker": "B", "asset_type": "etf", "composite_score": 70}],
            "overflow_picks": [{"ticker": "C", "composite_score": 70}]}
    by = {r["ticker"]: r["asset_type"] for r in g32_shadow.build_shadow_rows(grow, FRI, "cron")}
    assert by == {"A": "etf", "B": "etf", "C": "stock"}


def test_missing_table_check_failure_is_a_real_error_not_a_skip(monkeypatch):
    import stock_analyzer.system_health as sh
    monkeypatch.setattr(sh, "_is_missing_table",
                        lambda _e: (_ for _ in ()).throw(RuntimeError("x")))
    _db(monkeypatch, exc=Exception('relation "g32_shadow" does not exist'))
    res = db.save_g32_shadow([_row()])
    assert res["saved"] == 0 and res["error"] and "missing_table" not in res


def test_etf_lane_does_not_overwrite_the_stock_pick_basis_in_source():
    import inspect
    src = inspect.getsource(daily_briefing._grow_today)
    assert "_g32_pick_src.setdefault(" in src
