"""N1 C6: fail-closed readers for stale account_daily_snapshots rows, and the
structural-refusal alarm dedup. Covers `stale_details`, refusal `kind`s,
`account_row_trust`, its wiring (period core, Reports, Debrief, email) and
`cron_runner._repair_failure_lines`. docs/plans/account-snapshot-repair.md."""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import account_row_trust as ART
from stock_analyzer import account_snapshot_autorepair as AR
from stock_analyzer import account_snapshot_repair as R
from stock_analyzer import debrief_advisor as da
from stock_analyzer import notify
from stock_analyzer import performance_review as pr
from stock_analyzer import period_inputs as pi
from stock_analyzer.account import account_money_fields
from stock_analyzer.market_time import ET
from tests._ast_cache import parse_file_cached
from tests.test_account_snapshot_repair import (
    ANCHOR_30, BASE, CASH_AS_OF_30, CLOSES, NOW, late_bbb, row, trades)

ROOT = Path(__file__).resolve().parent.parent


# -- stale_details / stale_rows ----------------------------------------------------

def test_stale_details_names_the_stale_tickers_and_agrees_with_stale_rows():
    t = trades(BASE, late_bbb(), (6, "CCC", "BUY", 1, ANCHOR_30))
    acct = [row(d="2026-10-29", max_id=4, cash_as_of="2026-10-29T21:00:00+00:00"), row()]
    det = R.stale_details(acct, t)
    assert det == [{"snapshot_date": "2026-10-30", "tickers": ["BBB", "CCC"]}]
    assert R.stale_rows(acct, t) == [d["snapshot_date"] for d in det]


def test_stale_details_none_when_journal_unreadable_like_stale_rows():
    assert R.stale_details([row()], None) is None
    assert R.stale_rows([row()], None) is None
    bad = trades(BASE).drop(columns=["id"])
    assert R.stale_details([row()], bad) is None


def test_stale_details_oldest_first_and_legacy_never_flagged():
    t = trades(BASE, (5, "BBB", "BUY", 2, "2026-10-28T20:00:00+00:00"), late_bbb(id_=6))
    acct = [row(), row(d="2026-10-28", cash_as_of="2026-10-28T21:00:00+00:00"),
            row(d="2026-10-27", max_id=None)]
    assert [d["snapshot_date"] for d in R.stale_details(acct, t)] == ["2026-10-28", "2026-10-30"]


# -- refusal kinds -------------------------------------------------------------------

def _kinds(acct, t, closes=CLOSES):
    reps, refs = R.plan_repairs(acct, t, closes, NOW)
    return reps, [x["kind"] for x in refs], refs


def test_kind_journal_unreadable():
    _, kinds, refs = _kinds([row()], None)
    assert kinds == ["journal_unreadable"] and refs[0]["snapshot_date"] is None


def test_kind_no_book():
    r = row()
    r["book_positions"] = None
    assert _kinds([r], trades(BASE, late_bbb()))[1] == ["no_book"]


def test_kind_split_both_reasons():
    assert _kinds([row()], trades(BASE, (5, "AAA", "SPLIT", 20, ANCHOR_30)), {})[1] == ["split"]
    t = trades(BASE, late_bbb(), (6, "BBB", "SPLIT", 4, "2026-11-05T21:00:00+00:00"))
    assert _kinds([row()], t)[1] == ["split"]


def test_kind_oversell():
    assert _kinds([row()], trades(BASE, (5, "AAA", "SELL", 20, ANCHOR_30)), {})[1] == ["oversell"]


def test_kind_unexplained():
    t = trades(BASE, (2, "AAA", "BUY", 7, "2026-10-02T20:00:00+00:00"),
               (5, "AAA", "BUY", 3, ANCHOR_30))
    assert _kinds([row()], t, {})[1] == ["unexplained"]


def test_kind_no_close():
    other = {("BBB", date(2026, 10, 29)): 49.0}
    assert _kinds([row()], trades(BASE, late_bbb()), other)[1] == ["no_close"]


def test_kind_no_rate():
    r = row()
    r["maintenance_rate"] = None
    assert _kinds([r], trades(BASE, late_bbb()))[1] == ["no_rate"]


def test_structural_kinds_exclude_the_transient_ones():
    assert R.STRUCTURAL_REFUSAL_KINDS == {"no_book", "split", "oversell", "unexplained", "no_rate"}
    assert "no_close" not in R.STRUCTURAL_REFUSAL_KINDS
    assert "journal_unreadable" not in R.STRUCTURAL_REFUSAL_KINDS


# -- exclude_stale_rows --------------------------------------------------------------

def _frame(*rows):
    return pd.DataFrame(list(rows))


def _stale_world():
    t = trades(BASE, late_bbb())
    legacy = row(d="2026-10-23", max_id=None, cash_as_of="2026-10-23T21:00:00+00:00")
    clean = row(d="2026-10-29", cash_as_of="2026-10-29T21:00:00+00:00")
    return t, _frame(legacy, clean, row())


def test_none_input():
    assert ART.exclude_stale_rows(None, trades(BASE)) == (None, [])


def test_legacy_only_frame_unchanged_and_copied():
    df = _frame(row(max_id=None))
    df = df.drop(columns=["journal_max_trade_id"])
    out, ex = ART.exclude_stale_rows(df, trades(BASE, late_bbb()))
    assert ex == [] and out is not df and out.equals(df)
    empty, ex2 = ART.exclude_stale_rows(pd.DataFrame(), trades(BASE))
    assert ex2 == [] and empty.empty


def test_stale_row_masked_others_untouched_input_not_mutated():
    t, df = _stale_world()
    before = df.copy(deep=True)
    out, ex = ART.exclude_stale_rows(df, t)
    assert df.equals(before)                        # input never mutated
    assert [x["snapshot_date"] for x in ex] == ["2026-10-30"]
    assert "BBB" in ex[0]["reason"]
    i = out.index[out["snapshot_date"] == "2026-10-30"][0]
    for c in ART.MONEY_FIELDS:
        assert out.loc[i, c] is None
    others = out.drop(index=i)
    pd.testing.assert_frame_equal(others, before.drop(index=i), check_dtype=False)
    # cash / date / provenance stay: only the money fields are nulled
    assert out.loc[i, "cash_balance"] == before.loc[i, "cash_balance"]


def test_idempotent():
    t, df = _stale_world()
    once, ex1 = ART.exclude_stale_rows(df, t)
    twice, ex2 = ART.exclude_stale_rows(once, t)
    assert ex1 == ex2
    assert twice.equals(once)


def test_trades_none_masks_provenance_rows_only():
    _, df = _stale_world()
    out, ex = ART.exclude_stale_rows(df, None)
    assert [x["snapshot_date"] for x in ex] == ["2026-10-29", "2026-10-30"]
    assert all("couldn't be checked" in x["reason"] for x in ex)
    leg = out[out["snapshot_date"] == "2026-10-23"].iloc[0]
    assert leg["net_equity"] == df[df["snapshot_date"] == "2026-10-23"].iloc[0]["net_equity"]
    assert out[out["snapshot_date"] != "2026-10-23"]["net_equity"].isna().all()


def test_unusable_trades_frame_fails_closed():
    _, df = _stale_world()
    out, ex = ART.exclude_stale_rows(df, trades(BASE).drop(columns=["traded_at"]))
    assert len(ex) == 2 and out["net_equity"].isna().sum() == 2


def test_masked_row_is_rejected_as_a_valid_account_row():
    t, df = _stale_world()
    assert pr.valid_account_snapshot_row(df.iloc[2])
    out, _ = ART.exclude_stale_rows(df, t)
    assert not pr.valid_account_snapshot_row(out.iloc[2])
    assert pr.valid_account_snapshot_row(out.iloc[0])


def test_date_matching_str_and_date_objects():
    t, df = _stale_world()
    df_d = df.copy()
    df_d["snapshot_date"] = [date.fromisoformat(x) for x in df["snapshot_date"]]
    for frame in (df, df_d):
        out, ex = ART.exclude_stale_rows(frame, t)
        assert [x["snapshot_date"] for x in ex] == ["2026-10-30"]
        assert out["net_equity"].isna().sum() == 1


def test_masked_frame_reads_missing_not_nan_text():
    t, df = _stale_world()
    out, _ = ART.exclude_stale_rows(df, t)
    again = pd.DataFrame(out.to_dict("records"))
    assert pr._opt(again.iloc[2]["leverage"]) is None


# -- excluded_line -------------------------------------------------------------------

def _ex(d):
    return {"snapshot_date": d, "reason": "its holdings don't match the trade journal (X changed)"}


def test_excluded_line_includes_d0_session_and_period_bounds():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)      # Monday start: d0 = Fri 10-30
    line = ART.excluded_line([_ex("2026-10-30")], ps, pe)
    assert line.startswith("Left out of this period's account figures: 2026-10-30 — ")
    assert line.endswith(".")
    assert ART.excluded_line([_ex("2026-11-06")], ps, pe) is not None
    assert ART.excluded_line([_ex("2026-11-08")], ps, pe) is not None


def test_excluded_line_start_after_a_holiday_uses_the_prior_session():
    ps, pe = date(2026, 11, 27), date(2026, 11, 29)    # Thanksgiving 11-26: d0 = Wed 11-25
    assert ART.excluded_line([_ex("2026-11-25")], ps, pe) is not None
    assert ART.excluded_line([_ex("2026-11-24")], ps, pe) is None


def test_excluded_line_outside_and_empty_are_none():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    assert ART.excluded_line([_ex("2026-10-29")], ps, pe) is None   # before d0
    assert ART.excluded_line([_ex("2026-11-09")], ps, pe) is None   # after the end
    assert ART.excluded_line([], ps, pe) is None
    assert ART.excluded_line(None, ps, pe) is None


def test_excluded_line_multiple_joined_and_plain_text():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    line = ART.excluded_line([_ex("2026-10-30"), _ex("2026-11-03")], ps, pe)
    assert line.count("; ") == 1 and "2026-10-30" in line and "2026-11-03" in line
    assert "**" not in line and "$" not in line


def test_real_reasons_carry_no_markup():
    t, df = _stale_world()
    _, ex = ART.exclude_stale_rows(df, t)
    line = ART.excluded_line(ex, date(2026, 10, 26), date(2026, 11, 1))
    assert "**" not in line and "$" not in line and "BBB" in line
    _, ex2 = ART.exclude_stale_rows(df, None)
    assert "$" not in ART.excluded_line(ex2, date(2026, 10, 26), date(2026, 11, 1))


# -- compute_period_core: the 11-01 regression shape --------------------------------

PS, PE = date(2026, 10, 26), date(2026, 11, 1)


def _reg_rows():
    def acct(d, gross, cash, **kw):
        m = account_money_fields(gross, cash, 0.25)
        return {"snapshot_date": d, "gross_book": gross, "cash_balance": cash,
                "maintenance_rate": 0.25, "cash_as_of": f"{d}T21:00:00+00:00", **m, **kw}
    d0 = acct("2026-10-23", 20951.66, -13286.14)        # legacy: NULL provenance
    d1 = acct("2026-10-30", 15272.55, -12762.22, journal_max_trade_id=4,
              book_positions={"AAA": {"shares": 10, "close": 1527.255}})
    return pd.DataFrame([d0, d1])


def _reg_inputs():
    daily = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])
    return {"trades_df": trades(BASE, late_bbb()), "account_snapshots_df": _reg_rows(),
            "daily_snapshots_df": daily, "flows": [], "income_events": []}


def test_core_stale_end_row_is_withheld_like_an_invalid_row():
    inp = _reg_inputs()
    raw = pr.account_return(inp["account_snapshots_df"], [], PS, PE, {})
    assert raw["status"] == "ok" and raw["return_pct"] == pytest.approx(-67.25, abs=0.01)
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    assert core["account_return"]["status"] == "pre_coverage"
    assert [x["snapshot_date"] for x in core["account_rows_excluded"]] == ["2026-10-30"]
    assert core["leverage"]["status"] == "empty"                    # the stale row is skipped
    assert core["leverage"]["n_null_rows"] == 1
    # the cached input frame was not mutated
    assert inp["account_snapshots_df"].loc[1, "net_equity"] == pytest.approx(2510.33)


def test_core_without_stale_rows_lists_nothing_and_none_input_is_empty():
    inp = _reg_inputs()
    inp["trades_df"] = trades(BASE)
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    assert core["account_rows_excluded"] == []
    assert core["account_return"]["status"] == "ok"
    assert pi.compute_period_core(None, PS, PE, spy_prices_by_date=None,
                                  reference_leverage=2.0)["account_rows_excluded"] == []


def test_core_unreadable_journal_withholds_provenance_rows():
    inp = _reg_inputs()
    inp["trades_df"] = None
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    assert [x["snapshot_date"] for x in core["account_rows_excluded"]] == ["2026-10-30"]
    assert core["account_return"]["status"] == "offline" or core["account_return"]["status"] != "ok"


# -- debrief package + email ---------------------------------------------------------

def test_debrief_package_carries_the_excluded_line():
    inp = _reg_inputs()
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    pkg = da.build_debrief_package(PE, core, None, inp["trades_df"])
    assert pkg["excluded_account_line"].startswith("Left out of this period's account figures: 2026-10-30")
    clean = da.build_debrief_package(PE, None, None, inp["trades_df"])
    assert clean["excluded_account_line"] is None


def test_email_shows_the_line_escaped_and_omits_it_when_none():
    pkg = {"excluded_account_line": "Left out: 2026-10-30 — <b>x</b> & y."}
    html = notify.render_debrief_email({"metrics_version": 2, "performance_pct": None}, period=pkg)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y." in html and "<b>x</b>" not in html
    assert "Left out" in notify.render_debrief_email(
        {"metrics_version": 2, "performance_pct": 1.0}, period=pkg)
    for none_pkg in ({"excluded_account_line": None}, {}):
        assert "Left out" not in notify.render_debrief_email(
            {"metrics_version": 2, "performance_pct": None}, period=none_pkg)


# -- refusal alarm dedup -------------------------------------------------------------

def _res(*refusals, ok=True, failed=()):
    return {"ok": ok, "since": "2026-09-27", "checked": 3, "applied": [], "failed": list(failed),
            "refusals": list(refusals), "error": None if ok else "boom", "details": []}


def _ref(d, kind, reason="why"):
    return {"snapshot_date": d, "kind": kind, "reason": reason}


def test_structural_refusal_keys_only_structural_sorted_and_only_when_ok():
    r = _res(_ref("2026-10-30", "split"), _ref("2026-10-29", "no_close"),
             _ref("2026-10-28", "oversell"), _ref(None, "journal_unreadable"))
    assert AR.structural_refusal_keys(r) == ["2026-10-28|oversell", "2026-10-30|split"]
    assert AR.structural_refusal_keys(_res(_ref("2026-10-30", "split"), ok=False)) == []


def test_failure_lines_suppress_structural_only():
    r = _res(_ref("2026-10-30", "split", "S"), _ref("2026-10-29", "no_close", "C"),
             failed=["2026-10-27"])
    keys = frozenset(AR.structural_refusal_keys(r) + ["2026-10-29|no_close"])
    lines = AR.failure_lines(r, suppress_keys=keys)
    assert any("refused 2026-10-29: C" in x for x in lines)        # transient never suppressed
    assert any("write failed for 2026-10-27" in x for x in lines)  # failed write never suppressed
    assert not any("2026-10-30" in x for x in lines)
    assert len(AR.failure_lines(r)) == 3                           # default: nothing suppressed
    err = _res(ok=False)
    assert AR.failure_lines(err, suppress_keys=frozenset({"x"})) == ["account_snapshot_repair: boom"]


def test_failure_lines_cap_applies_after_suppression():
    refs = [_ref(f"2026-10-{d:02d}", "split") for d in range(1, 9)]
    r = _res(*refs)
    suppress = frozenset(AR.structural_refusal_keys(r)[:6])
    assert len(AR.failure_lines(r, suppress_keys=suppress)) == 2


def test_standing_lines_name_only_the_suppressed():
    r = _res(_ref("2026-10-30", "split", "S"), _ref("2026-10-29", "no_close", "C"))
    out = AR.standing_lines(r, frozenset({"2026-10-30|split", "2026-10-29|no_close"}))
    assert len(out) == 1
    assert out[0] == ("account_snapshot_repair: refused 2026-10-30 still standing (S) — alarmed "
                      "when first seen; Reports and the Debrief leave that day out")


class _FakeDb:
    def __init__(self, state="unset"):
        self.state, self.saved = state, []

    def load_alert_state(self, row_id=1):
        assert row_id == cr._ACCT_REPAIR_REFUSAL_ROW
        return None if self.state == "unset" else self.state

    def save_alert_state(self, emailed_date, fingerprint, row_id=1):
        assert row_id == cr._ACCT_REPAIR_REFUSAL_ROW
        self.saved.append((emailed_date, fingerprint))
        return True


NOW_ET = ET.localize(datetime(2026, 11, 3, 16, 30))


def test_repair_failure_lines_first_run_alarms_and_saves_second_run_is_quiet(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    logs = []
    monkeypatch.setattr(cr, "_log", logs.append)
    r = _res(_ref("2026-10-30", "split", "S"))
    first = cr._repair_failure_lines(r, NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in first)
    assert fake.saved == [("2026-11-03", "2026-10-30|split")]
    fake.state = {"last_emailed_date": "2026-11-03", "last_fingerprint": "2026-10-30|split"}
    logs.clear()
    second = cr._repair_failure_lines(r, NOW_ET)
    assert second == []
    assert any("still standing" in x for x in logs)


def test_repair_failure_lines_new_date_kind_alarms_and_transient_always_does(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    r = _res(_ref("2026-10-30", "split", "S"), _ref("2026-10-30", "oversell", "O"),
             _ref("2026-10-29", "no_close", "C"))
    lines = cr._repair_failure_lines(r, NOW_ET)
    assert not any("refused 2026-10-30: S" in x for x in lines)
    assert any("refused 2026-10-30: O" in x for x in lines)        # new (date, kind)
    assert any("refused 2026-10-29: C" in x for x in lines)        # transient
    assert fake.saved[-1][1] == "2026-10-30|oversell;2026-10-30|split"


def test_repair_failure_lines_cleared_refusal_is_forgotten_so_a_recurrence_alarms(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    assert cr._repair_failure_lines(_res(), NOW_ET) == []
    assert fake.saved[-1][1] == ""                                  # current set stored


def test_repair_failure_lines_offline_state_fails_open(monkeypatch):
    fake = _FakeDb(state="unset")                                   # load_alert_state -> None
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    lines = cr._repair_failure_lines(_res(_ref("2026-10-30", "split", "S")), NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in lines)


def test_repair_failure_lines_not_ok_does_not_save_and_never_raises(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    assert cr._repair_failure_lines(_res(ok=False), NOW_ET) == ["account_snapshot_repair: boom"]
    assert fake.saved == []

    class Boom(_FakeDb):
        def load_alert_state(self, row_id=1):
            raise RuntimeError("down")

        def save_alert_state(self, *a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr(cr, "db", Boom())
    out = cr._repair_failure_lines(_res(_ref("2026-10-30", "split", "S")), NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in out)
    assert cr._repair_failure_lines(None, NOW_ET)[0].startswith("account_snapshot_repair:")


# -- AST pins: both ends --------------------------------------------------------------

def _fn(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, attr):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == attr)
                 or (isinstance(n.func, ast.Name) and n.func.id == attr))]


def test_compute_period_core_masks_before_all_three_readers():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "period_inputs.py")
    fn = _fn(tree, "compute_period_core")
    assert _calls(fn, "exclude_stale_rows")
    for reader in ("reconcile_period", "leverage_section", "account_return"):
        (c,) = _calls(fn, reader)
        names = [a.id for a in c.args if isinstance(a, ast.Name)] + [
            k.value.id for k in c.keywords if isinstance(k.value, ast.Name)]
        assert "acct" in names, reader


def test_app_reports_passes_masked_frames_to_build_review_and_engine_inputs():
    tree = parse_file_cached(ROOT / "app.py")
    masked = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and _calls(n.value, "exclude_stale_rows")
              for tgt in n.targets if isinstance(tgt, ast.Tuple)
              for t in tgt.elts if isinstance(t, ast.Name)}
    assert {"_perf_acct_snap_full_m", "_perf_acct_snap_m"} <= masked
    (br,) = [c for c in _calls(tree, "build_review")
             if any(k.arg == "account_return_snapshots_df" for k in c.keywords)]
    kw = {k.arg: k.value for k in br.keywords}
    assert kw["account_snapshots_df"].id == "_perf_acct_snap_m"
    assert kw["account_return_snapshots_df"].id == "_perf_acct_snap_full_m"
    eng = [c for c in _calls(tree, "load_engine_inputs")
           if any(k.arg == "account_snapshots_df" and isinstance(k.value, ast.Name)
                  and k.value.id == "_perf_acct_snap_full_m" for k in c.keywords)]
    assert eng, "Reports' load_engine_inputs must take the masked full frame"


@pytest.mark.parametrize("fn", ["_run_eod", "_run_debrief"])
def test_cron_lanes_use_the_deduping_helper_not_failure_lines(fn):
    tree = ast.parse((ROOT / "cron_runner.py").read_text(encoding="utf-8"))
    node = _fn(tree, fn)
    assert _calls(node, "_repair_failure_lines")
    assert not _calls(node, "failure_lines")
