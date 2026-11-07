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
    ex = [_ex("2026-10-30"), {"snapshot_date": "2026-11-03", "reason": "a different reason"}]
    line = ART.excluded_line(ex, ps, pe)
    assert line.count("; ") == 1 and "2026-10-30" in line and "2026-11-03" in line
    assert "**" not in line and "$" not in line
    # the same reason on both days collapses into one group
    same = ART.excluded_line([_ex("2026-10-30"), _ex("2026-11-03")], ps, pe)
    assert same.count("; ") == 0 and "2026-10-30, 2026-11-03 — " in same


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
    """alert_state fake that remembers what was saved (state follows the saves)."""
    def __init__(self, state="unset"):
        self.state, self.saved = state, []

    def load_alert_state(self, row_id=1):
        assert row_id == cr._ACCT_REPAIR_REFUSAL_ROW
        return None if self.state == "unset" else self.state

    def save_alert_state(self, emailed_date, fingerprint, row_id=1):
        assert row_id == cr._ACCT_REPAIR_REFUSAL_ROW
        self.saved.append((emailed_date, fingerprint))
        self.state = {"last_emailed_date": emailed_date, "last_fingerprint": fingerprint}
        return True


NOW_ET = ET.localize(datetime(2026, 11, 3, 16, 30))


def _lane_run(result, *, email_ok):
    """What a lane does: take the lines + pending fingerprint, send the failure
    email if there is anything to say, save the fingerprint only after delivery."""
    lines, pending = cr._repair_failure_lines(result, NOW_ET)
    sent = None
    if lines:
        sent = email_ok
        if sent:
            cr._commit_repair_fingerprint(pending, NOW_ET)
    return lines, pending, sent


def test_repair_failure_lines_first_run_alarms_saves_only_after_delivery_second_run_is_quiet(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    logs = []
    monkeypatch.setattr(cr, "_log", logs.append)
    r = _res(_ref("2026-10-30", "split", "S"))
    first, pending = cr._repair_failure_lines(r, NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in first)
    assert pending == "2026-10-30|split"
    assert fake.saved == []                                        # nothing saved before delivery
    cr._commit_repair_fingerprint(pending, NOW_ET)
    assert fake.saved == [("2026-11-03", "2026-10-30|split")]
    logs.clear()
    second, pending2 = cr._repair_failure_lines(r, NOW_ET)
    assert second == [] and pending2 is None
    assert any("still standing" in x for x in logs)


def test_email_fails_fingerprint_not_saved_next_run_alarms_again_then_delivery_saves(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    r = _res(_ref("2026-10-30", "split", "S"))
    lines1, _, sent1 = _lane_run(r, email_ok=False)
    assert lines1 and sent1 is False and fake.saved == []
    lines2, _, _ = _lane_run(r, email_ok=False)                    # still alarms: never marked seen
    assert any("refused 2026-10-30: S" in x for x in lines2) and fake.saved == []
    lines3, _, sent3 = _lane_run(r, email_ok=True)
    assert lines3 and sent3 is True
    assert fake.saved == [("2026-11-03", "2026-10-30|split")]
    lines4, pending4, sent4 = _lane_run(r, email_ok=True)          # delivered once: quiet now
    assert lines4 == [] and pending4 is None and sent4 is None


def test_nothing_new_saves_immediately_without_an_email(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    lines, pending, sent = _lane_run(_res(_ref("2026-10-30", "split", "S")), email_ok=False)
    assert lines == [] and pending is None and sent is None        # no email needed
    assert fake.saved == [("2026-11-03", "2026-10-30|split")]      # saved right away


def test_new_key_defers_but_a_transient_line_alone_does_not_defer(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    # a transient refusal alarms every run but needs no deferral: no structural key is new
    lines, pending = cr._repair_failure_lines(
        _res(_ref("2026-10-30", "split", "S"), _ref("2026-10-29", "no_close", "C")), NOW_ET)
    assert any("refused 2026-10-29: C" in x for x in lines) and pending is None
    assert fake.saved[-1][1] == "2026-10-30|split"


def test_repair_failure_lines_new_date_kind_alarms_and_transient_always_does(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    r = _res(_ref("2026-10-30", "split", "S"), _ref("2026-10-30", "oversell", "O"),
             _ref("2026-10-29", "no_close", "C"))
    lines, pending = cr._repair_failure_lines(r, NOW_ET)
    assert not any("refused 2026-10-30: S" in x for x in lines)
    assert any("refused 2026-10-30: O" in x for x in lines)        # new (date, kind)
    assert any("refused 2026-10-29: C" in x for x in lines)        # transient
    assert pending == "2026-10-30|oversell;2026-10-30|split" and fake.saved == []
    cr._commit_repair_fingerprint(pending, NOW_ET)
    assert fake.saved[-1][1] == "2026-10-30|oversell;2026-10-30|split"


def test_repair_failure_lines_cleared_refusal_is_forgotten_so_a_recurrence_alarms(monkeypatch):
    fake = _FakeDb({"last_emailed_date": "x", "last_fingerprint": "2026-10-30|split"})
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    assert cr._repair_failure_lines(_res(), NOW_ET) == ([], None)
    assert fake.saved[-1][1] == ""                                  # current set stored
    lines, pending = cr._repair_failure_lines(_res(_ref("2026-10-30", "split", "S")), NOW_ET)
    assert lines and pending == "2026-10-30|split"                  # recurrence alarms


def test_repair_failure_lines_offline_state_fails_open(monkeypatch):
    fake = _FakeDb(state="unset")                                   # load_alert_state -> None
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    lines, pending = cr._repair_failure_lines(_res(_ref("2026-10-30", "split", "S")), NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in lines)
    assert pending == "2026-10-30|split"


def test_repair_failure_lines_not_ok_does_not_save_and_never_raises(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    assert cr._repair_failure_lines(_res(ok=False), NOW_ET) == (["account_snapshot_repair: boom"], None)
    assert fake.saved == []

    class Boom(_FakeDb):
        def load_alert_state(self, row_id=1):
            raise RuntimeError("down")

        def save_alert_state(self, *a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr(cr, "db", Boom())
    out, pending = cr._repair_failure_lines(_res(_ref("2026-10-30", "split", "S")), NOW_ET)
    assert any("refused 2026-10-30: S" in x for x in out) and pending == "2026-10-30|split"
    cr._commit_repair_fingerprint(pending, NOW_ET)                  # a failed save never raises
    cr._commit_repair_fingerprint(None, NOW_ET)
    bad_lines, bad_pending = cr._repair_failure_lines(None, NOW_ET)
    assert bad_lines[0].startswith("account_snapshot_repair:") and bad_pending is None


def test_commit_none_is_a_noop(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setattr(cr, "db", fake)
    cr._commit_repair_fingerprint(None, NOW_ET)
    assert fake.saved == []


@pytest.mark.parametrize("sent,raises,expected", [(True, False, True), (False, False, False),
                                                  (None, False, False), (True, True, False)])
def test_notify_failure_reports_whether_the_email_went_out(monkeypatch, sent, raises, expected):
    monkeypatch.setattr(cr, "_log", lambda *_: None)

    def fake_send(label, subject, html):
        if raises:
            raise RuntimeError("resend down")
        return sent
    monkeypatch.setattr(cr, "_send_email", fake_send)
    assert cr._notify_failure("eod", "x") is expected


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


def _inside_if_testing(node, test_call, target_call):
    return any(target in list(ast.walk(i))
               for i in ast.walk(node) if isinstance(i, ast.If) and _calls(i.test, test_call)
               for target in _calls(node, target_call))


def test_eod_saves_the_fingerprint_only_after_a_delivered_failure_email():
    tree = ast.parse((ROOT / "cron_runner.py").read_text(encoding="utf-8"))
    node = _fn(tree, "_run_eod")
    assert len(_calls(node, "_commit_repair_fingerprint")) == 1
    assert _inside_if_testing(node, "_notify_failure", "_commit_repair_fingerprint")


def test_debrief_routes_every_failure_email_through_the_commit_after_delivery_closure():
    tree = ast.parse((ROOT / "cron_runner.py").read_text(encoding="utf-8"))
    node = _fn(tree, "_run_debrief")
    assert len(_calls(node, "_notify_failure")) == 1               # only inside the closure
    closure = _fn(node, "_notify_debrief_failure")
    assert _calls(closure, "_notify_failure")
    assert _inside_if_testing(closure, "_notify_failure", "_commit_repair_fingerprint")
    assert len(_calls(node, "_notify_debrief_failure")) == 6


@pytest.mark.parametrize("fn", ["_run_eod", "_run_debrief"])
def test_cron_lanes_use_the_deduping_helper_not_failure_lines(fn):
    tree = ast.parse((ROOT / "cron_runner.py").read_text(encoding="utf-8"))
    node = _fn(tree, fn)
    assert _calls(node, "_repair_failure_lines")
    assert not _calls(node, "failure_lines")


# ======================= C6b: wording, reasons, collapse, fingerprint ====================

# -- separate fail-closed reasons (unreadable journal vs a check that raised) ----------

def test_unreadable_journal_and_raised_check_have_distinct_reasons_both_fail_closed(monkeypatch):
    _, df = _stale_world()
    out, ex = ART.exclude_stale_rows(df, None)
    assert {x["reason"] for x in ex} == {"the trade journal couldn't be read, so this day couldn't be checked"}
    assert out["net_equity"].isna().sum() == 2

    def boom(*_a, **_k):
        raise RuntimeError("bad frame")
    monkeypatch.setattr(ART, "stale_details", boom)
    out2, ex2 = ART.exclude_stale_rows(df, trades(BASE))
    assert [x["snapshot_date"] for x in ex2] == ["2026-10-29", "2026-10-30"]
    assert {x["reason"] for x in ex2} == {"the check failed with an error, so this day couldn't be checked"}
    assert out2["net_equity"].isna().sum() == 2                    # still masked
    leg = out2[out2["snapshot_date"] == "2026-10-23"].iloc[0]
    assert leg["net_equity"] == df[df["snapshot_date"] == "2026-10-23"].iloc[0]["net_equity"]


def test_unusable_journal_frame_reads_as_unreadable_not_as_an_error():
    _, df = _stale_world()
    _, ex = ART.exclude_stale_rows(df, trades(BASE).drop(columns=["traded_at"]))
    assert all("couldn't be read" in x["reason"] for x in ex)


# -- excluded_line: collapse identical reasons ------------------------------------------

def _same(d):
    return {"snapshot_date": d, "reason": "R-same"}


def test_excluded_line_collapses_one_reason_and_caps_the_date_list():
    ps, pe = date(2026, 11, 2), date(2026, 11, 20)
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    line = ART.excluded_line([_same(d) for d in days], ps, pe)
    assert line == ("Left out of this period's account figures: "
                    "2026-11-02, 2026-11-03, 2026-11-04, 2026-11-05, 2026-11-06, 2026-11-09 — R-same.")
    assert line.count("R-same") == 1
    more = ART.excluded_line([_same(d) for d in days + ["2026-11-10", "2026-11-11"]], ps, pe)
    assert more.endswith("2026-11-09 and 2 more — R-same.")
    assert "2026-11-10" not in more and more.count("R-same") == 1
    assert "**" not in more and "$" not in more


def test_excluded_line_groups_by_reason_in_date_order_and_window_scopes_the_group():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    ex = [{"snapshot_date": "2026-11-05", "reason": "B"}, {"snapshot_date": "2026-11-03", "reason": "A"},
          {"snapshot_date": "2026-11-04", "reason": "B"}, {"snapshot_date": "2026-11-20", "reason": "A"}]
    assert ART.excluded_line(ex, ps, pe) == (
        "Left out of this period's account figures: 2026-11-03 — A; 2026-11-04, 2026-11-05 — B.")


def test_excluded_line_period_window_boundary_masked_d0_counts_the_day_before_does_not():
    # Monday start 11-02 -> d0 = Fri 10-30. Sat 10-31 sits between d0 and the start: inside.
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    assert ART.excluded_line([_same("2026-10-30")], ps, pe) is not None       # d0 counts
    assert ART.excluded_line([_same("2026-10-31")], ps, pe) is not None
    assert ART.excluded_line([_same("2026-10-29")], ps, pe) is None           # one session before d0
    # a Saturday start still resolves d0 to the Friday
    assert ART.excluded_line([_same("2026-10-30")], date(2026, 10, 31), date(2026, 11, 8)) is not None
    assert ART.excluded_line([_same("2026-10-29")], date(2026, 10, 31), date(2026, 11, 8)) is None


# -- legacy rows are never masked ----------------------------------------------------------

def test_legacy_null_provenance_row_is_never_masked_even_when_the_journal_is_unreadable_or_late():
    legacy = row(d="2026-10-30", max_id=None)          # would be stale if it had provenance
    nan_legacy = row(d="2026-10-29", max_id=float("nan"), cash_as_of="2026-10-29T21:00:00+00:00")
    df = _frame(legacy, nan_legacy)
    for jr in (trades(BASE, late_bbb()), None, trades(BASE).drop(columns=["traded_at"])):
        out, ex = ART.exclude_stale_rows(df, jr)
        assert ex == []
        assert out["net_equity"].notna().all() and out["leverage"].notna().all()
    # and next to a stale row the legacy neighbour stays whole
    out, ex = ART.exclude_stale_rows(_frame(legacy, row(d="2026-10-28", cash_as_of="2026-10-28T21:00:00+00:00",
                                                        max_id=3)), trades(BASE, (5, "BBB", "BUY", 2, "2026-10-28T20:00:00+00:00")))
    assert [x["snapshot_date"] for x in ex] == ["2026-10-28"]
    assert out[out["snapshot_date"] == "2026-10-30"].iloc[0]["net_equity"] is not None


# -- masked row through compute_period_core: d0 withheld / d1 searched back ----------------

def _core_frame(*rows_):
    return pd.DataFrame(list(rows_))


def _core_inputs(df):
    daily = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])
    return {"trades_df": trades(BASE, late_bbb()), "account_snapshots_df": df,
            "daily_snapshots_df": daily, "flows": [], "income_events": []}


def test_core_masked_d1_row_searches_back_to_the_earlier_valid_row():
    legacy = row(d="2026-10-23", max_id=None, cash_as_of="2026-10-23T21:00:00+00:00")
    clean = row(d="2026-10-29", cash_as_of="2026-10-29T21:00:00+00:00")
    inp = _core_inputs(_core_frame(legacy, clean, row()))          # row() = stale 10-30
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    assert [x["snapshot_date"] for x in core["account_rows_excluded"]] == ["2026-10-30"]
    assert core["recon"]["status"] in ("ok", "partial")
    assert core["recon"]["d1"] == date(2026, 10, 29)              # the masked 10-30 was skipped
    assert core["account_return"]["status"] == "ok"
    assert core["account_return"]["d1"] == date(2026, 10, 29)
    assert core["leverage"]["n_null_rows"] == 1 and core["leverage"]["n_days_recorded"] == 1
    note = ART.leverage_gap_text(core["leverage"], core["account_rows_excluded"], PS, PE)
    assert note.startswith("1 day(s) left out (") and "no leverage recorded" not in note


def test_core_masked_d0_row_withholds_the_account_result():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)                  # d0 = Fri 10-30 (stale)
    later = row(d="2026-11-03", max_id=5, cash_as_of="2026-11-03T21:00:00+00:00")
    inp = _core_inputs(_core_frame(row(), later))
    core = pi.compute_period_core(inp, ps, pe, spy_prices_by_date={}, reference_leverage=2.0)
    assert core["recon"]["status"] == "pre_coverage"
    assert core["recon"]["pre_coverage_reason"] == "no_start_record"
    assert core["account_return"]["status"] == "pre_coverage"
    note = ART.pre_coverage_left_out_note(core["recon"], core["account_rows_excluded"])
    assert note.startswith("The account record for 2026-10-30 was left out (")
    assert "see the note above" in note
    quiet = ART.pre_coverage_left_out_note(core["recon"], core["account_rows_excluded"], see_note=False)
    assert "was left out" in quiet and "see the note above" not in quiet


# -- wording decisions ------------------------------------------------------------------

def _lev(n_null, n_call=0):
    return {"status": "ok", "n_null_rows": n_null, "n_null_call_rows": n_call}


def test_leverage_gap_text_counts_left_out_apart_from_genuinely_missing():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    ex = [_same("2026-11-03"), _same("2026-11-04")]
    assert ART.leverage_gap_text(_lev(0), ex, ps, pe) is None
    assert ART.leverage_gap_text(None, ex, ps, pe) is None
    both = ART.leverage_gap_text(_lev(3, 1), ex, ps, pe)
    assert both.startswith("2 day(s) left out (their holdings didn't match the trade journal")
    assert "see the note above" in both
    assert "1 day(s) had no leverage recorded and 1 had no margin-call distance" in both
    only_left = ART.leverage_gap_text(_lev(2), ex, ps, pe)
    assert "no leverage recorded" not in only_left and only_left.endswith(").")
    none_left = ART.leverage_gap_text(_lev(2, 1), [], ps, pe)
    assert none_left == ("2 day(s) had no leverage recorded and 1 had no margin-call distance "
                         "— left out of the figures above.")
    only_call = ART.leverage_gap_text(_lev(0, 2), ex, ps, pe)
    assert only_call == "2 day(s) had no margin-call distance — left out of the figures above."
    quiet = ART.leverage_gap_text(_lev(2), ex, ps, pe, see_note=False)
    assert "see the note above" not in quiet
    for txt in (both, only_left, none_left, quiet):
        assert "**" not in txt and "$" not in txt


def test_leverage_gap_text_only_counts_rows_inside_the_leverage_window_and_never_over_counts():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    # the d0 session (10-30) is in excluded_line's window but NOT leverage_section's
    assert ART.leverage_gap_text(_lev(1), [_same("2026-10-30")], ps, pe) == (
        "1 day(s) had no leverage recorded — left out of the figures above.")
    assert ART.leverage_gap_text(_lev(1), [_same("2026-11-03"), _same("2026-11-04")], ps, pe).startswith(
        "1 day(s) left out (")                                     # capped at n_null_rows


def test_pre_coverage_left_out_note_only_when_masking_can_explain_the_reason():
    ex = [_same("2026-11-03")]
    def rec(reason, **kw):
        return {"status": "pre_coverage", "pre_coverage_reason": reason,
                "d0": date(2026, 10, 30), "d1": date(2026, 11, 6), **kw}
    assert "2026-11-03" in ART.pre_coverage_left_out_note(rec("no_valid_end_record"), ex)
    assert ART.pre_coverage_left_out_note(rec("no_valid_end_record"), [_same("2026-10-30")]) is None
    assert ART.pre_coverage_left_out_note(rec("no_end_record_yet"), ex) is None
    assert ART.pre_coverage_left_out_note(rec("no_sessions"), ex) is None
    assert ART.pre_coverage_left_out_note(rec("no_start_record"), ex) is None       # not d0
    assert ART.pre_coverage_left_out_note(rec("no_start_record"), [_same("2026-10-30")]) is not None
    assert ART.pre_coverage_left_out_note({"status": "ok"}, ex) is None
    assert ART.pre_coverage_left_out_note(None, ex) is None
    many = [_same(f"2026-11-{d:02d}") for d in range(2, 10)]
    assert " and 2 more " in ART.pre_coverage_left_out_note(
        rec("no_valid_end_record", d1=date(2026, 11, 30)), many)


def test_left_out_reading_names_a_masked_end_row_and_is_none_otherwise():
    ex = [_same("2026-11-06")]
    assert ART.left_out_reading(date(2026, 11, 6), ex).startswith("left out (")
    assert ART.left_out_reading("2026-11-06", ex) is not None
    assert ART.left_out_reading(date(2026, 11, 5), ex) is None
    assert ART.left_out_reading(None, ex) is None
    assert ART.left_out_reading(date(2026, 11, 6), None) is None


def test_period_left_out_note_spans_d0_to_period_end():
    recon = {"d0": date(2026, 10, 30), "period_end": date(2026, 11, 8)}
    note = ART.period_left_out_note(recon, [_same("2026-10-30"), _same("2026-11-03")], see_note=False)
    assert "2026-10-30, 2026-11-03" in note and "see the note above" not in note
    assert ART.period_left_out_note(recon, [_same("2026-11-09")]) is None
    assert ART.period_left_out_note({}, [_same("2026-11-03")]) is None


def test_unknown_result_reason_says_a_record_was_left_out_without_a_pointer():
    recon = {"status": "pre_coverage", "pre_coverage_reason": "no_start_record",
             "d0": date(2026, 10, 30), "d1": date(2026, 11, 6), "period_end": date(2026, 11, 8)}
    plain = da.unknown_result_reason(recon, {"status": "pre_coverage"})
    assert "left out" not in plain                                  # default arg: unchanged
    txt = da.unknown_result_reason(recon, {"status": "pre_coverage"}, [_same("2026-10-30")])
    assert txt.startswith(plain) and "2026-10-30 was left out" in txt
    assert "see the note above" not in txt
    ar_only = da.unknown_result_reason({"status": "ok", "d0": date(2026, 10, 30),
                                        "period_end": date(2026, 11, 8)},
                                       {"status": "pre_coverage"}, [_same("2026-11-03")])
    assert "no usable account records at both ends" in ar_only and "2026-11-03 was left out" in ar_only


# -- debrief prompt carries the excluded line -----------------------------------------------

def test_debrief_prompt_receives_the_excluded_line_and_the_left_out_reason():
    inp = _reg_inputs()
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    pkg = da.build_debrief_package(PE, core, None, inp["trades_df"])
    prompt = da._format_prompt(pkg)
    assert pkg["excluded_account_line"] in prompt
    assert ("The account change above already leaves these days out; do not state an "
            "account value for those dates.") in prompt
    assert "State no account figure" not in prompt
    assert "2026-10-30" in pkg["result_reason"] and "left out" in pkg["result_reason"]
    clean = da.build_debrief_package(PE, None, None, inp["trades_df"])
    assert "Left out of this period's account figures" not in da._format_prompt(clean)


# -- period report markdown ------------------------------------------------------------------

def _md_report(excluded, lev, recon):
    return {"period_start": date(2026, 11, 2), "period_end": date(2026, 11, 8),
            "recon": recon, "leverage": lev, "trust": [], "account_rows_excluded": excluded}


def test_period_markdown_says_left_out_not_just_no_leverage_records():
    from stock_analyzer import period_attribution as pa
    recon = {"status": "pre_coverage", "pre_coverage_reason": "no_start_record",
             "d0": date(2026, 10, 30), "d1": date(2026, 11, 6)}
    lev = {"status": "empty", "n_null_rows": 2, "n_null_call_rows": 0}
    ex = [_same("2026-10-30"), _same("2026-11-03"), _same("2026-11-04")]
    md = pa.format_period_markdown(_md_report(ex, lev, recon))
    assert "Left out of this period's account figures:" in md
    assert "_No usable leverage readings in this period._" in md
    assert "No account snapshots recorded" not in md
    assert "2 day(s) left out (" in md
    assert "The account record for 2026-10-30 was left out" in md
    assert "**" not in md.replace("**Informational only -- not investment advice.**", "")
    bare = pa.format_period_markdown({k: v for k, v in _md_report(ex, lev, recon).items()
                                      if k != "account_rows_excluded"})
    assert "left out" not in bare.replace("left out of the figures", "")


# ======================= C6b review fixes (round 2) ======================================

def test_leverage_empty_text_never_says_no_snapshots_when_null_rows_exist():
    assert ART.leverage_empty_text({"status": "empty", "n_null_rows": 2}) == \
        "No usable leverage readings in this period."
    assert ART.leverage_empty_text({"status": "empty", "n_null_rows": 0}) == \
        "No account snapshots recorded in this period."
    assert ART.leverage_empty_text({"status": "empty"}) == \
        "No account snapshots recorded in this period."
    assert ART.leverage_empty_text(None) == "No account snapshots recorded in this period."


def test_every_row_masked_core_reads_usable_readings_not_no_snapshots():
    # the all-masked window: leverage_section is "empty" yet rows exist
    inp = _reg_inputs()
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date={}, reference_leverage=2.0)
    assert core["leverage"]["status"] == "empty" and core["leverage"]["n_null_rows"] == 1
    assert ART.leverage_empty_text(core["leverage"]) == "No usable leverage readings in this period."
    gap = ART.leverage_gap_text(core["leverage"], core["account_rows_excluded"], PS, PE)
    assert gap.startswith("1 day(s) left out (")


def test_screen_and_markdown_share_the_empty_state_helper():
    tree = parse_file_cached(ROOT / "app.py")
    assert _calls(tree, "leverage_empty_text")
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "No account snapshots recorded in this period" not in src   # lives only in the helper
    pa_tree = parse_file_cached(ROOT / "stock_analyzer" / "period_attribution.py")
    assert _calls(_fn(pa_tree, "format_period_markdown"), "leverage_empty_text")


def test_markdown_empty_window_with_no_rows_keeps_the_old_text_and_no_gap_line():
    from stock_analyzer import period_attribution as pa
    lev = {"status": "empty", "n_null_rows": 0, "n_null_call_rows": 0}
    md = pa.format_period_markdown(_md_report([], lev, {"status": "ok"}))
    assert "_No account snapshots recorded in this period._" in md


def test_start_left_out_suffix_names_a_masked_start_row():
    ex = [_same("2026-11-02")]
    s = ART.start_left_out_suffix(date(2026, 11, 2), ex)
    assert s.startswith(" · change since 2026-11-02 not shown (") and "see the note above" in s
    assert "$" not in s and "**" not in s
    assert ART.start_left_out_suffix("2026-11-02", ex) == s
    assert ART.start_left_out_suffix(date(2026, 11, 3), ex) == ""
    assert ART.start_left_out_suffix(None, ex) == ""
    assert ART.start_left_out_suffix(date(2026, 11, 2), None) == ""


def test_app_appends_the_start_suffix_to_the_period_end_caption():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_art_mod.start_left_out_suffix(" in src


def test_excluded_line_group_with_no_reason_reads_sensibly():
    ps, pe = date(2026, 11, 2), date(2026, 11, 8)
    for reason in (None, ""):
        line = ART.excluded_line([{"snapshot_date": "2026-11-03", "reason": reason}], ps, pe)
        assert "None" not in line and line.endswith("— the reason wasn't recorded.")


def test_no_valid_end_record_text_is_true_for_masking_and_for_a_failed_cash_check():
    from stock_analyzer import period_attribution as pa
    recon = {"status": "pre_coverage", "pre_coverage_reason": "no_valid_end_record",
             "d0": date(2026, 10, 30), "d1": date(2026, 11, 6)}
    for txt in (pa.pre_coverage_reason_text(recon),
                {c["key"]: c for c in pa.trust_strip(recon, None)}["coverage"]["label"],
                pa.format_period_markdown({"recon": recon, "leverage": {}, "period_start": None,
                                           "period_end": None})):
        assert "none was usable" in txt and "left out" in txt and "after-close cash check" in txt
        assert "none passed the after-close cash check" not in txt


# -- sibling dedup sites: saved only when the email was delivered ---------------------------

@pytest.mark.parametrize("delivered,saved", [(True, True), (False, False)])
def test_broker_failure_dedup_saved_only_after_a_delivered_email(monkeypatch, delivered, saved):
    writes = []
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: delivered)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **kw: writes.append(kw) or True)
    cr._notify_broker_failure(NOW_ET, "SnapTrade unreachable")
    assert bool(writes) is saved
    if saved:
        assert writes[0]["row_id"] == cr._BROKER_FAILURE_ROW


def test_broker_failure_undelivered_alarms_again_next_run(monkeypatch):
    state, sent = {"v": None}, []
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append(mode) or False)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: state["v"])
    monkeypatch.setattr(cr.db, "save_alert_state",
                        lambda **kw: state.update(v={"last_emailed_date": kw["emailed_date"]}) or True)
    cr._notify_broker_failure(NOW_ET, "x")
    cr._notify_broker_failure(NOW_ET, "x")
    assert sent == ["broker", "broker"] and state["v"] is None       # never marked as sent


@pytest.mark.parametrize("delivered,saved", [(True, True), (False, False)])
def test_news_only_failure_dedup_saved_only_after_a_delivered_email(monkeypatch, delivered, saved):
    writes = []
    monkeypatch.setattr(cr, "_log", lambda *_: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: delivered)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **kw: writes.append(kw) or True)
    cr._notify_lane_failure("premarket", NOW_ET, ["news feed: 0 headlines"])
    assert bool(writes) is saved
    if saved:
        assert writes[0]["row_id"] == cr._NEWS_FAILURE_ROW


def test_non_news_lane_failure_still_just_notifies(monkeypatch):
    calls, writes = [], []
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: calls.append(lane) or False)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **kw: writes.append(kw) or True)
    cr._notify_lane_failure("premarket", NOW_ET, ["exit_signals"])
    assert calls == ["premarket"] and writes == []
