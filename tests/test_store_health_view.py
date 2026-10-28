"""store_health_view.stores_summary + system_health `age_checked` flag."""
import pytest

from stock_analyzer import db, market_time
from stock_analyzer import system_health as sh
from stock_analyzer import store_health_view as v

pytestmark = pytest.mark.fast


def _ok(age=False, n=0):
    return {"severity": "ok", "state": "present", "label": f"s{n}", "age_checked": age}


def _unk(label, state):
    return {"severity": "unknown", "state": state, "label": label, "age_checked": False}


def test_live_case_sentence():
    rows = [_ok(True, i) for i in range(6)] + [_ok(False, i) for i in range(17)]
    g32 = next(x.label for x in sh._INVENTORY if x.table == "g32_shadow")
    rows.append(_unk(g32, "empty"))
    assert v.stores_summary(rows) == (
        "No store found missing or stale — 6 daily stores are fresh; 17 others exist "
        "(existence-only check, so their age isn't graded here); "
        f"not confirmed: {g32}: exists, no rows yet."
    )


def test_daily_unchecked_bucket_changes_lead():
    rows = [_ok(True, 0), {"severity": "ok", "state": "present", "label": "d",
                           "age_checked": False, "unconditional": True}, _ok(False, 1)]
    s = v.stores_summary(rows)
    assert s.startswith("No store found missing —")
    assert "stale" not in s
    assert "1 daily store exists but its freshness couldn't be checked this run" in s
    assert "1 other exists" in s


def test_empty_rows():
    assert "No data stores" in v.stores_summary([])


def test_present_reason():
    assert "latest date unreadable" in v.stores_summary([_unk("Z", "present")])


def test_all_fresh():
    s = v.stores_summary([_ok(True, i) for i in range(3)])
    assert s == "No store found missing or stale — 3 daily stores are fresh."


def test_all_exist_only():
    s = v.stores_summary([_ok(False, i) for i in range(2)])
    assert "fresh" not in s and "2 others exist" in s and "not confirmed" not in s


def test_zero_exist_only_omits_clause():
    s = v.stores_summary([_ok(True), _unk("X", "empty")])
    assert "exist (" not in s and "exists (" not in s.replace("exists, no rows", "")
    assert s.startswith("No store found missing or stale — 1 daily store is fresh; not confirmed: X")


def test_reasons():
    s = v.stores_summary([_unk("A", "error"), _unk("B", "offline"), _unk("C", "weird")])
    assert "A: couldn't be read" in s
    assert "B: database unreachable" in s
    assert "C: not confirmed" in s


def test_truncation_over_five():
    s = v.stores_summary([_unk(f"U{i}", "empty") for i in range(8)])
    assert "U4: exists, no rows yet" in s and "U5" not in s
    assert s.endswith("and 3 more.")


def test_exactly_five_no_more():
    s = v.stores_summary([_unk(f"U{i}", "empty") for i in range(5)])
    assert "more" not in s


def test_singular_exist_only():
    s = v.stores_summary([_ok(False)])
    assert "1 other exists" in s and "its age isn't graded here" in s


# ── _probe_store age_checked ──
class _Q:
    def __init__(self, rows):
        self._r = rows

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self):
        class R: pass
        r = R(); r.data = self._r
        return r


class _C:
    def __init__(self, rows): self._r = rows
    def table(self, _n): return _Q(self._r)


def test_age_checked_only_on_unconditional_age_branch(monkeypatch):
    today = market_time.today_et().isoformat()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(sh, "_last_expected_daily_date", lambda h: market_time.today_et())
    u = next(s for s in sh._INVENTORY if s.unconditional and s.date_col and s.expected_hour_et is not None)
    monkeypatch.setattr(db, "_client", lambda: _C([{u.date_col: today}]))
    assert sh._probe_store(u)["age_checked"] is True
    monkeypatch.setattr(db, "_client", lambda: _C([{u.date_col: "2000-01-01"}]))
    r = sh._probe_store(u)
    assert r["severity"] == "warn" and r["age_checked"] is True
    # expected None -> ok but not age-checked
    monkeypatch.setattr(sh, "_last_expected_daily_date", lambda h: None)
    monkeypatch.setattr(db, "_client", lambda: _C([{u.date_col: today}]))
    r = sh._probe_store(u)
    assert r["age_checked"] is False and r["unconditional"] is True and r["severity"] == "ok"
    # conditional store with a row: exists only
    c = next(s for s in sh._INVENTORY if not s.unconditional)
    monkeypatch.setattr(db, "_client", lambda: _C([{c.date_col or "x": today}]))
    r = sh._probe_store(c)
    assert r["severity"] == "ok" and r["age_checked"] is False


def test_age_checked_false_on_offline_empty_and_error(monkeypatch):
    s = sh._INVENTORY[0]
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert sh._probe_store(s)["age_checked"] is False
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _C([]))
    assert sh._probe_store(s)["age_checked"] is False
