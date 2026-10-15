"""grow-today-owns-buys P3/P3b: overflow_pick rec rows, the db writer's
optional hold_reason/rec_budget_state columns, and the cron scan lane's
separate, isolated overflow save."""
import copy

import pytest

import cron_runner as cr
from stock_analyzer import db
from stock_analyzer.overflow_rows import build_overflow_rec_rows
from tests.test_cron_lane_write_failure_rc import _OMIT, _run_scan

pytestmark = pytest.mark.fast


def _item(tk="NVDA", reason="pick_cap"):
    return {"ticker": tk, "sector": "Technology", "composite_score": 82.0,
            "momentum_score": 71, "price": 120.5, "reason": reason,
            "verdict": "Buy", "is_mover": False}


# -- builder ------------------------------------------------------------------

def test_row_field_mapping_and_null_sizing():
    r, = build_overflow_rec_rows([_item()], "2026-10-15", surfaced_at="t0")
    assert r == {"ticker": "NVDA", "rec_date": "2026-10-15",
                 "rec_type": "overflow_pick", "surfaced_at": "t0",
                 "price_at_surface": 120.5, "composite_score": 82.0,
                 "momentum_score": 71, "sector": "Technology",
                 "verdict": "Buy", "hold_reason": "pick_cap",
                 "asset_type": "stock"}
    for k in ("rec_shares", "rec_stop", "rec_portfolio_value", "rec_sizing_version"):
        assert r.get(k) is None


@pytest.mark.parametrize("reason", ["pick_cap", "sector_slot", "mover_cap"])
def test_reason_carried(reason):
    assert build_overflow_rec_rows([_item(reason=reason)], "d")[0]["hold_reason"] == reason


@pytest.mark.parametrize("empty", [None, [], ()])
def test_empty_input(empty):
    assert build_overflow_rec_rows(empty, "d") == []


def test_skips_no_ticker_keeps_others():
    rows = build_overflow_rec_rows([{"ticker": ""}, {"reason": "x"}, _item("AMD")], "d")
    assert [r["ticker"] for r in rows] == ["AMD"]


def test_input_not_mutated():
    src = [_item("A"), _item("B")]
    before = copy.deepcopy(src)
    build_overflow_rec_rows(src, "d")
    assert src == before


# -- db writer ----------------------------------------------------------------

class _Builder:
    def __init__(self, calls):
        self._c = calls

    def upsert(self, rows, on_conflict=None, ignore_duplicates=None):
        self._c.append([dict(r) for r in rows])
        return self

    def execute(self):
        return None


class _Client:
    def __init__(self):
        self.calls = []

    def table(self, _n):
        return _Builder(self.calls)


@pytest.fixture
def fake(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_CLIENT", c)
    return c


def _rec(tk="CRM", rec_type="new_pick", **kw):
    d = {"ticker": tk, "rec_date": "2026-10-15", "rec_type": rec_type,
         "price_at_surface": 10.0, "composite_score": 70}
    d.update(kw)
    return d


def test_new_pick_batch_has_no_hold_keys(fake):
    db.save_recommendations([_rec()])
    row = fake.calls[0][0]
    assert "hold_reason" not in row and "rec_budget_state" not in row


def test_overflow_batch_carries_hold_reason(fake):
    rows = build_overflow_rec_rows([_item("A"), _item("B", "sector_slot")], "2026-10-15")
    res = db.save_recommendations(rows)
    assert res["error"] is None
    sent = {r["ticker"]: r for r in fake.calls[0]}
    assert sent["A"]["hold_reason"] == "pick_cap"
    assert sent["B"]["hold_reason"] == "sector_slot"
    assert sent["A"]["rec_type"] == "overflow_pick"
    assert sent["A"]["rec_shares"] is None


def test_mixed_batch_key_set_is_uniform(fake):
    """When any row carries the column, every row in the call gets the key
    (None elsewhere), so a batch never has a ragged key set."""
    db.save_recommendations([_rec("A"), _rec("B", "overflow_pick", hold_reason="pick_cap")])
    a, b = fake.calls[0]
    assert set(a) == set(b)
    assert a["hold_reason"] is None and b["hold_reason"] == "pick_cap"


def test_rec_budget_state_passthrough_only_when_present(fake):
    db.save_recommendations([_rec(rec_budget_state="s")])
    assert fake.calls[0][0]["rec_budget_state"] == "s"
    assert "hold_reason" not in fake.calls[0][0]


def test_missing_hold_column_strips_only_that_generation(monkeypatch):
    class _B(_Builder):
        def execute(self):
            if len(self._c) == 1:
                raise Exception("Could not find the 'hold_reason' column of "
                                "'recommendations' in the schema cache PGRST204")

    class _C(_Client):
        def table(self, _n):
            return _B(self.calls)

    c = _C()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_CLIENT", c)
    res = db.save_recommendations([_rec(hold_reason="pick_cap", t_score=60)])
    assert res["error"] is None
    assert "hold_reason" in c.calls[0][0]
    assert "hold_reason" not in c.calls[1][0]
    assert c.calls[1][0]["t_score"] == 60   # unrelated generation kept


def test_weekend_overflow_row_dropped_by_writer(fake):
    """2026-10-17 is a Saturday: split_non_trading_rows drops it."""
    rows = build_overflow_rec_rows([_item()], "2026-10-17")
    res = db.save_recommendations(rows)
    assert res["attempted"] == 0 and fake.calls == []


# -- cron scan lane -----------------------------------------------------------

def test_cron_overflow_saved_in_separate_call(monkeypatch):
    log = []
    rc, _ = _run_scan(monkeypatch, grow={"overflow_picks": [_item("NVDA")]}, rec_log=log)
    assert rc == 0
    assert len(log) == 2
    assert log[0] == [{"ticker": "AAPL"}]               # new_pick stub, untouched
    assert [r["rec_type"] for r in log[1]] == ["overflow_pick"]
    assert log[1][0]["hold_reason"] == "pick_cap"


@pytest.mark.parametrize("grow", [{}, {"overflow_picks": []}, {"overflow_picks": None}])
def test_cron_no_overflow_no_extra_call(monkeypatch, grow):
    log = []
    rc, _ = _run_scan(monkeypatch, grow=grow, rec_log=log)
    assert rc == 0 and len(log) == 1


def test_cron_overflow_builder_crash_reddens_but_new_pick_saved(monkeypatch):
    import stock_analyzer.overflow_rows as orows

    def _boom(*_a, **_k):
        raise RuntimeError("of boom")
    monkeypatch.setattr(orows, "build_overflow_rec_rows", _boom)
    log = []
    rc, _ = _run_scan(monkeypatch, grow={"overflow_picks": [_item()]}, rec_log=log)
    assert len(log) == 1 and log[0] == [{"ticker": "AAPL"}]   # new_pick still saved
    assert rc == 1
    assert "overflow rec log" in cr._LAST_LANE_FAILURE_DETAIL


def test_cron_overflow_save_error_reddens_new_pick_unaffected(monkeypatch):
    log = []
    rc, _ = _run_scan(monkeypatch, grow={"overflow_picks": [_item()]}, rec_log=log,
                      of_result={"attempted": 1, "saved": 0, "error": "offline"})
    assert len(log) == 2
    assert rc == 1
    assert "overflow rec log" in cr._LAST_LANE_FAILURE_DETAIL
    assert "recommendations:" not in cr._LAST_LANE_FAILURE_DETAIL.replace("overflow rec log", "")


@pytest.mark.parametrize("grow", [_OMIT, None])
def test_cron_build_failed_grow_missing_or_none_is_clean(monkeypatch, grow):
    """compute_morning_picks' build-failed path has no "grow" key (or None):
    the isinstance guard must keep the overflow block inert."""
    log = []
    rc, _ = _run_scan(monkeypatch, grow=grow, rec_log=log)
    assert rc == 0
    assert len(log) == 1 and log[0] == [{"ticker": "AAPL"}]
    assert "overflow rec log" not in (cr._LAST_LANE_FAILURE_DETAIL or "")


def test_none_sector_stored_as_empty_string():
    it = _item()
    it["sector"] = None
    assert build_overflow_rec_rows([it], "d")[0]["sector"] == ""
