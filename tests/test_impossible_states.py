"""Impossible-states audit as maintenance check 6 (2026-10-14).

Pins: the regression decision against the post-cleanup baseline
(stock_analyzer.impossible_states), the read-only rpc wrapper's
offline-sentinel contract (db.run_impossible_states_q0), the wiring into
data_maintenance.run_all_checks, and that the Postgres function file is
exactly what the generator derives from Q0 (no hand-drifted copy).
"""
import importlib.util
import pathlib
import re

import pytest

from stock_analyzer import data_maintenance as dm
from stock_analyzer import db
from stock_analyzer import impossible_states as ist

pytestmark = pytest.mark.fast

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _rows(version=ist.Q0_VERSION, **overrides):
    """A full Q0 run (every check, plus the `_V` version row), every count 0
    unless overridden. version=None omits the version row."""
    rows = [{"id": cid, "chk": f"check {cid}", "n_bad": overrides.get(cid, 0), "n_total": 10}
            for cid in sorted(ist.EXPECTED_IDS)]
    if version is not None:
        rows.append({"id": "_V", "chk": version, "n_bad": None, "n_total": None})
    return rows


def _sev(findings):
    return [f["severity"] for f in findings]


# ── classify ───────────────────────────────────────────────────────────────

def test_did_not_run_is_unknown_never_ok():
    out = ist.classify(None, "function public.impossible_states_q0 does not exist")
    assert _sev(out) == ["unknown"]
    assert "does not exist" in out[0]["detail"]
    assert "impossible-states-function.sql" in out[0]["detail"]


def test_clean_run_is_one_ok_row():
    out = ist.classify(_rows())
    assert _sev(out) == ["ok"]
    assert f"{len(ist.EXPECTED_IDS)} checks" in out[0]["detail"]


def test_the_recorded_baseline_itself_is_clean():
    # The exact post-cleanup Q0 of 2026-10-14 must read ok.
    out = ist.classify(_rows(A3=23, C1=19, C2=6, C3=2, C7=3, C8=2, C9=5, D5=3))
    assert _sev(out) == ["ok"]


@pytest.mark.parametrize("cid", ["A1", "A6", "B1", "D3", "D4", "D10"])
def test_any_row_on_a_zero_check_warns(cid):
    out = ist.classify(_rows(**{cid: 1}))
    assert _sev(out) == ["warn"]
    assert out[0]["key"] == f"impossible_states_{cid}" and out[0]["count"] == 1


@pytest.mark.parametrize("cid,base", [("C3", 2), ("C7", 3), ("C8", 3), ("C9", 5), ("D5", 3)])
def test_frozen_check_warns_only_above_its_baseline(cid, base):
    assert ist.policy_for(cid) == (ist.FROZEN, base)
    assert _sev(ist.classify(_rows(**{cid: base}))) == ["ok"]
    out = ist.classify(_rows(**{cid: base + 1}))
    assert _sev(out) == ["warn"] and out[0]["count"] == 1
    assert f"baseline {base}" in out[0]["detail"]


@pytest.mark.parametrize("cid,base", [("A3", 23), ("C1", 19), ("C2", 6)])
def test_accruing_check_never_warns_but_reports_growth(cid, base):
    assert ist.policy_for(cid) == (ist.ACCRUING, base)
    out = ist.classify(_rows(**{cid: base + 500}))
    assert _sev(out) == ["ok"]
    assert f"{cid} {base + 500} (baseline {base})" in out[0]["detail"]


def test_shape_mismatch_does_not_stop_the_other_checks():
    rows = [r for r in _rows(D4=1) if r["id"] != "A1"]
    keys = {f["key"] for f in ist.classify(rows)}
    assert {"impossible_states_shape", "impossible_states_D4"} <= keys


@pytest.mark.parametrize("version", ["q0:000000000000", None])
def test_stale_or_unversioned_applied_function_warns(version):
    out = ist.classify(_rows(version=version))
    stale = [f for f in out if f["key"] == "impossible_states_version"]
    assert len(stale) == 1 and stale[0]["severity"] == "warn"
    assert "re-apply" in stale[0]["detail"]


def test_version_row_is_not_an_unexpected_check():
    assert [f["key"] for f in ist.classify(_rows())] == ["impossible_states"]


def test_missing_or_unexpected_check_ids_are_reported():
    rows = [r for r in _rows() if r["id"] != "D4"] + [{"id": "E1", "chk": "x", "n_bad": 0}]
    out = ist.classify(rows)
    shape = [f for f in out if f["key"] == "impossible_states_shape"]
    assert len(shape) == 1 and shape[0]["severity"] == "warn"
    assert "D4" in shape[0]["detail"] and "E1" in shape[0]["detail"]


def test_unreadable_count_is_unknown_not_zero():
    out = ist.classify(_rows(D4=None))
    assert [f["severity"] for f in out if f["key"] == "impossible_states_D4"] == ["unknown"]


def test_baseline_ids_are_real_q0_ids():
    assert set(ist._RESIDUAL) <= ist.EXPECTED_IDS


# ── db.run_impossible_states_q0 ────────────────────────────────────────────

class _RpcClient:
    def __init__(self, data=None, exc=None):
        self.calls, self._data, self._exc = [], data, exc

    def rpc(self, name, params):
        self.calls.append((name, params))
        if self._exc:
            raise self._exc

        class _Q:
            def __init__(q, data):
                q._d = data

            def execute(q):
                class _R:
                    data = q._d
                return _R()
        return _Q(self._data)


def test_rpc_read_only_session_is_not_run(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    monkeypatch.setattr(db, "has_db", lambda: True)
    assert db.run_impossible_states_q0([])["rows"] is None


def test_rpc_no_db_is_not_run(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.run_impossible_states_q0(["2026-07-03"])["rows"] is None


def test_rpc_failure_is_not_run_with_the_error(monkeypatch):
    c = _RpcClient(exc=RuntimeError("function does not exist"))
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: c)
    res = db.run_impossible_states_q0(["2026-07-03"])
    assert res["rows"] is None and "does not exist" in res["error"]


def test_rpc_non_list_payload_is_not_run(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: _RpcClient(data={"oops": 1}))
    assert db.run_impossible_states_q0([])["rows"] is None


def test_rpc_success_passes_the_sorted_calendar(monkeypatch):
    c = _RpcClient(data=_rows())
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: c)
    res = db.run_impossible_states_q0({"2026-09-07", "2026-07-03"})
    assert res["error"] is None and len(res["rows"]) == len(ist.EXPECTED_IDS) + 1
    assert c.calls == [("impossible_states_q0", {"p_closed": ["2026-07-03", "2026-09-07"]})]


# ── data_maintenance wiring ────────────────────────────────────────────────

def test_check_passes_the_real_nyse_calendar(monkeypatch):
    from stock_analyzer.constants import NYSE_HOLIDAYS
    seen = {}
    monkeypatch.setattr(db, "run_impossible_states_q0",
                        lambda closed: seen.update(closed=set(closed)) or {"rows": _rows(), "error": None})
    assert _sev(dm.check_impossible_states()) == ["ok"]
    assert seen["closed"] == set(NYSE_HOLIDAYS) and "2026-07-03" in seen["closed"]


def test_run_all_checks_includes_the_audit(monkeypatch):
    monkeypatch.setattr(db, "run_impossible_states_q0",
                        lambda closed: {"rows": _rows(D4=2), "error": None})
    for name in ("check_orphan_cache_rows", "check_account_flows_duplicates",
                 "check_analyst_coverage_duplicates", "check_thesis_reviews_duplicates",
                 "check_pending_import_anomalies"):
        monkeypatch.setattr(dm, name, lambda *_a, **_k: [])
    out = dm.run_all_checks(set(), set(), set())
    assert [f["key"] for f in out] == ["impossible_states_D4"]


# ── the SQL function file is generated, not hand-copied ────────────────────

def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_isf", ROOT / "scripts" / "gen_impossible_states_function.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_function_file_is_current_with_q0():
    gen = _load_generator()
    committed = (ROOT / "docs" / "sql" / "impossible-states-function.sql").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert committed == gen.render(), (
        "docs/sql/impossible-states-function.sql is stale -- run "
        "scripts/gen_impossible_states_function.py, then re-apply it in Supabase")


def test_q0_version_matches_the_generator():
    assert ist.Q0_VERSION == _load_generator().q0_version(), (
        "Q0 changed: set impossible_states.Q0_VERSION to the output of "
        "scripts/gen_impossible_states_function.py --version, regenerate, re-apply")


def test_q0_thresholds_are_the_constants_py_values():
    """Hard Rule #1 (review of this commit, blocking): Q0 runs on a schedule
    now, so its threshold literals must track constants.py. A changed constant
    fails here, forcing a Q0 edit + regenerate + re-apply. Otherwise e.g. a
    raised CORR_MIN_OBS_TRUSTED would leave B1 silently blind to n in
    [20, new)."""
    from stock_analyzer.constants import (
        ACCOUNT_CASH_STALE_DAYS, COMPOSITE_BUY, COMPOSITE_STRONG_BUY,
        CORR_MIN_OBS_TRUSTED, SINGLE_NAME_CEILING,
    )
    body = (ROOT / "docs" / "sql" / "impossible-states-function.sql").read_text(
        encoding="utf-8").split("$fn$")[1]
    expected = {
        "A4 BUY bar": (f"composite >= {COMPOSITE_BUY} AND", 1),
        "D5 BUY bar": (f"composite_score < {COMPOSITE_BUY}", 1),
        "D6 STRONG_BUY": (f"composite_score < {COMPOSITE_STRONG_BUY})", 1),
        "D7 single-name ceiling": (f"* 100 > {SINGLE_NAME_CEILING + 0.05:g})", 1),
        "B1/B3 corr floor": (f"corr_coverage_n < {CORR_MIN_OBS_TRUSTED})", 2),
        "B2 cash staleness": (f"::date > {ACCOUNT_CASH_STALE_DAYS})", 1),
    }
    for name, (frag, n) in expected.items():
        assert body.count(frag) == n, f"{name}: expected {frag!r} x{n} in Q0"


def test_function_checks_match_expected_ids_and_take_the_calendar_as_a_parameter():
    text = (ROOT / "docs" / "sql" / "impossible-states-function.sql").read_text(encoding="utf-8")
    body = text.split("$fn$")[1]
    assert set(re.findall(r"SELECT '([A-D]\d+)'", body)) == set(ist.EXPECTED_IDS)
    assert "= ANY(p_closed)" in body
    assert "DATE '2026-07-03'" not in body, "the calendar must come from constants, not SQL"
    assert re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", body) is None
