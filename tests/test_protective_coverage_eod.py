"""Sell-check-unread C5 (docs/plans/sell-check-unread-3a-3b.md, owner decision U-E):
the EOD lane and the premarket `exit_signals` capture never treat a saved-copy
(bundle-cache) price as today's.

Invariant: at the EOD a cache-served held name counts as UNPRICED (the lane goes red
through the existing `partial_price_outage` failure, the account row is skipped, the
risk row's book fields are blanked, the name is kept out of `snapshot_rows` /
`book_positions` / `gross_book` and is NOT swept as sold); a fresh read whose last bar
is before the session is LOG ONLY (shadow, never a failure); a payload without the new
keys keeps its old outcome.  A saved-copy name's premarket exit signal is STILL
recorded (the 09:45 email's exit block reads those rows) and named in the log.
"""
import ast
import datetime
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import protective_coverage as pc
from tests._ast_cache import parse_file_cached
from tests.test_cron_eod_write_failure_propagation import (
    _patch_defaults, _payload, _spy_book_writes)
from tests.test_held_stop_carry_wiring import _scene, dbc, logs  # noqa: F401  (fixtures)
from tests.test_protective_coverage_wiring import (
    _BREACH, _coverage as _pm_coverage, _payload as _pm_payload, _run_pm)

_ROOT = Path(__file__).resolve().parent.parent
WED = date(2026, 11, 25)          # a trading day
SAT = date(2026, 11, 28)          # forced weekend run: the expected session is Fri 11-27
EOD_NOW = datetime.datetime(2026, 11, 25, 17, 30)


# ── engine: compute_eod ──────────────────────────────────────────────────────

def _frame(last, rows=5):
    idx = pd.DatetimeIndex(pd.bdate_range(end=last, periods=rows))
    return pd.DataFrame({"Close": [10.0] * rows}, index=idx)


def _bundle(last, cached=False):
    return {"df": _frame(last),
            "stale_as_of": "2026-11-24T21:00:00+00:00" if cached else None}


def _port(*names):
    price = {"ABC": 50.0, "DEF": 10.0, "GHI": 20.0}
    shares = {"ABC": 4, "DEF": 10, "GHI": 5}
    return pd.DataFrame([
        {"Ticker": t, "Price": price[t], "Shares": shares[t],
         "Shares Exact": float(shares[t]), "Market Value": price[t] * shares[t]}
        for t in names])


def _eod(port_df, held_data, held_tickers, today=WED):
    ctx = {"ok": True, "errors": [], "port_df": port_df, "held_data": held_data,
           "fragility": None, "spy_6mo": None, "held_tickers": held_tickers}
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=1), \
         patch.object(hae, "_build_context", return_value=ctx):
        return hae.compute_eod(today)


def test_cached_name_is_kept_out_of_rows_book_and_gross():
    r = _eod(_port("ABC", "DEF"),
             {"ABC": _bundle("2026-11-24", cached=True), "DEF": _bundle("2026-11-25")},
             ["ABC", "DEF"])
    assert r["eod_withheld"] == ["ABC"]
    assert [c["ticker"] for c in r["coverage"]["cached"]] == ["ABC"]
    assert r["snapshot_rows"] == [{"ticker": "DEF", "shares": 10.0, "close_price": 10.0}]
    assert r["book_positions"] == {"DEF": {"shares": 10.0, "close": 10.0}}
    assert r["gross_book"] == 100.0, "ABC's saved-copy 200.0 is not in the gross"


def test_every_name_cached_is_a_failed_book_never_an_empty_one():
    r = _eod(_port("ABC", "DEF"),
             {"ABC": _bundle("2026-11-24", cached=True), "DEF": _bundle("2026-11-24", cached=True)},
             ["ABC", "DEF"])
    assert r["eod_withheld"] == ["ABC", "DEF"]
    assert r["snapshot_rows"] == []
    assert r["book_positions"] is None, "couldn't build, never {} with gross 0.0"
    assert r["gross_book"] is None, "the Market Value fallback must not record saved-copy prices"


def test_market_value_fallback_still_runs_when_nothing_is_withheld():
    # (the all-cached test above pins the other side: a withheld name skips it)
    df = _port("ABC", "DEF")
    df["Price"] = float("nan")          # nothing priced -> book None; coverage: unpriced
    r = _eod(df, {"ABC": _bundle("2026-11-24", cached=True), "DEF": _bundle("2026-11-25")},
             ["ABC", "DEF"])
    assert r["book_positions"] is None
    assert r["eod_withheld"] == [], "unpriced names are the existing guard's job, not cached"
    assert r["gross_book"] == 300.0, "legacy fallback unchanged when nothing is withheld"


def test_fresh_but_behind_name_is_priced_and_logged_only():
    r = _eod(_port("DEF", "GHI"),
             {"DEF": _bundle("2026-11-25"), "GHI": _bundle("2026-11-24")},
             ["DEF", "GHI"])
    assert r["eod_withheld"] == []
    assert [b["ticker"] for b in r["coverage"]["behind"]] == ["GHI"]
    assert [x["ticker"] for x in r["snapshot_rows"]] == ["DEF", "GHI"]
    assert r["gross_book"] == 200.0


def test_clean_book_is_unchanged():
    r = _eod(_port("DEF", "GHI"),
             {"DEF": _bundle("2026-11-25"), "GHI": _bundle("2026-11-25")},
             ["DEF", "GHI"])
    assert r["coverage"]["state"] == "full" and r["eod_withheld"] == []
    assert len(r["snapshot_rows"]) == 2 and r["gross_book"] == 200.0


def test_saturday_forced_run_expects_friday_not_saturday():
    fresh = {"DEF": _bundle("2026-11-27"), "GHI": _bundle("2026-11-27")}
    r = _eod(_port("DEF", "GHI"), fresh, ["DEF", "GHI"], today=SAT)
    assert r["coverage"]["expected_session"] == "2026-11-27"
    assert r["coverage"]["state"] == "full" and r["eod_withheld"] == []
    cached = {"DEF": _bundle("2026-11-27", cached=True), "GHI": _bundle("2026-11-27")}
    r = _eod(_port("DEF", "GHI"), cached, ["DEF", "GHI"], today=SAT)
    assert r["eod_withheld"] == ["DEF"]
    assert [x["ticker"] for x in r["snapshot_rows"]] == ["GHI"]


def test_coverage_that_raises_withholds_nothing_and_never_raises_the_eod():
    with patch.object(hae.protective_coverage, "price_coverage", side_effect=RuntimeError("x")):
        r = _eod(_port("ABC", "DEF"),
                 {"ABC": _bundle("2026-11-24", cached=True), "DEF": _bundle("2026-11-25")},
                 ["ABC", "DEF"])
    assert r["coverage"] is None and r["eod_withheld"] == []
    assert len(r["snapshot_rows"]) == 2


def test_early_return_is_unchanged_and_has_no_new_keys():
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=1), \
         patch.object(hae, "_build_context",
                      return_value={"ok": False, "reason": "no_bundles", "errors": ["x"]}):
        r = hae.compute_eod(WED)
    assert r["reason"] == "no_bundles"
    assert "coverage" not in r and "eod_withheld" not in r


# ── cron: _run_eod ───────────────────────────────────────────────────────────

def _cov(cached=(), behind=()):
    n = len(cached) + len(behind)
    return {
        "state": "gaps" if n else "full", "expected_session": "2026-11-25",
        "held_count": 2, "fresh_count": 2 - n, "unpriced": [],
        "cached": [{"ticker": t, "last_bar": "2026-11-24", "sessions_behind": 1} for t in cached],
        "behind": [{"ticker": t, "last_bar": "2026-11-24", "sessions_behind": 1} for t in behind],
        "not_dated": [],
    }


def _eod_payload(withheld=(), cov="auto", names=("ABC", "DEF"), **extra):
    rows = [{"ticker": t, "shares": 10, "close_price": 10.0} for t in names if t not in withheld]
    kw = dict(extra)
    if "port_df" not in kw:
        kw["port_df"] = _port(*names)
    p = _payload(snapshot_rows=rows, held_tickers=list(names), **kw)
    p["eod_withheld"] = list(withheld)
    p["coverage"] = _cov(cached=withheld) if cov == "auto" else cov
    return p


def test_cached_name_makes_the_lane_red_withholds_the_records_and_is_not_swept(monkeypatch, capsys):
    saved_risk, snap = [], []
    _patch_defaults(
        monkeypatch,
        compute_eod=lambda **_k: _eod_payload(withheld=["ABC"]),
    )
    calls = _spy_book_writes(monkeypatch)
    monkeypatch.setattr(cr, "build_portfolio_risk_snapshot",
                        lambda *_a, **_k: {"portfolio_beta": 1.4, "top_sector": "Tech",
                                           "top_sector_pct": 80.0, "max_single_name_pct": 60.0,
                                           "corr_coverage_n": 120})
    monkeypatch.setattr(cr.db, "save_portfolio_risk_snapshot",
                        lambda row: saved_risk.append(dict(row)) or True)
    monkeypatch.setattr(cr.db, "save_daily_snapshot",
                        lambda d, rows, held_tickers=None: snap.append((rows, held_tickers)) or True)
    rc = cr._run_eod(EOD_NOW, force=True)
    out = capsys.readouterr().out
    assert rc == 1
    detail = cr._LAST_LANE_FAILURE_DETAIL
    assert "partial_price_outage: 1 holding(s) unpriced: ABC (saved copy only)" in detail
    assert "re-run the eod lane the same evening with ALERT_FORCE=1" in detail
    assert calls["account"] == 0, "no account row is even built"
    row = saved_risk[0]
    for k in ("portfolio_beta", "top_sector", "top_sector_pct", "max_single_name_pct"):
        assert row[k] is None, k
    assert row["corr_coverage_n"] == 120
    rows, held = snap[0]
    assert [r["ticker"] for r in rows] == ["DEF"]
    assert "ABC" in held, "held_tickers keeps ABC, so the snapshot sweep never deletes it"
    assert "held coverage: 1 of 2 not flagged; cached=ABC(last 2026-11-24, 1 behind)" in out


def test_plain_unpriced_names_print_unchanged_beside_a_saved_copy_name(monkeypatch):
    def _payload_zzz_unpriced(**_k):
        # ZZZ is held but has no price anywhere: not in port_df and not in
        # snapshot_rows (a snapshot row would count it as priced).
        p = _eod_payload(withheld=["ABC"], names=("ABC", "DEF", "ZZZ"),
                         port_df=_port("ABC", "DEF"))
        p["snapshot_rows"] = [r for r in p["snapshot_rows"] if r["ticker"] != "ZZZ"]
        return p
    _patch_defaults(monkeypatch, compute_eod=_payload_zzz_unpriced)
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert ("2 holding(s) unpriced: ABC (saved copy only), ZZZ"
            in cr._LAST_LANE_FAILURE_DETAIL)


def test_fresh_behind_name_is_a_log_line_only_account_row_still_written(monkeypatch, capsys):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _eod_payload(
        cov=_cov(behind=["GHI"]), names=("DEF", "GHI")))
    calls = _spy_book_writes(monkeypatch)
    rc = cr._run_eod(EOD_NOW, force=True)
    out = capsys.readouterr().out
    assert rc == 0 and cr._LAST_LANE_FAILURE_DETAIL is None
    assert calls["account"] == 1
    assert ("eod price basis: GHI last bar 2026-11-24, expected 2026-11-25 "
            "(shadow, not a failure)") in out
    assert "held coverage: 1 of 2 not flagged; behind=GHI(last 2026-11-24, 1 behind)" in out


def test_clean_eod_adds_no_failure_and_no_shadow_line(monkeypatch, capsys):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _eod_payload(cov=_cov()))
    calls = _spy_book_writes(monkeypatch)
    assert cr._run_eod(EOD_NOW, force=True) == 0
    out = capsys.readouterr().out
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert calls == {"account": 1, "risk": 1, "rec_events": 1}
    assert "held coverage: 2 of 2 not flagged" in out
    assert "eod price basis" not in out


def test_unverified_coverage_with_a_real_held_list_turns_the_eod_red(monkeypatch, capsys):
    # C5 review: compute_eod couldn't build coverage (internal fault) though the
    # held list is real, so nothing was withheld: red, never silent.
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _eod_payload(cov=None))
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert "held coverage: unverified" in capsys.readouterr().out
    assert "held_coverage_unverified -- the EOD couldn't check" in cr._LAST_LANE_FAILURE_DETAIL


def test_unverified_coverage_on_an_empty_held_list_is_not_a_failure(monkeypatch):
    p = _eod_payload(cov=None, names=())
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: p)
    cr._run_eod(EOD_NOW, force=True)
    assert "held_coverage_unverified" not in (cr._LAST_LANE_FAILURE_DETAIL or "")


def test_older_payload_without_the_new_keys_keeps_its_old_outcome(monkeypatch, capsys):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[{"ticker": "DEF", "shares": 10, "close_price": 10.0}],
        held_tickers=["DEF"]))
    assert cr._run_eod(EOD_NOW, force=True) == 0
    out = capsys.readouterr().out
    assert "held coverage" not in out and "eod price basis" not in out
    # and an unpriced name on an older payload keeps its plain wording
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[{"ticker": "DEF", "shares": 10, "close_price": 10.0}],
        held_tickers=["DEF", "MSFT"]))
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert "1 holding(s) unpriced: MSFT --" in cr._LAST_LANE_FAILURE_DETAIL
    assert "saved copy" not in cr._LAST_LANE_FAILURE_DETAIL


@pytest.mark.parametrize("bad", [None, "ABC", ("ABC",), {"ABC"}, 7])
def test_a_non_list_eod_withheld_is_ignored(monkeypatch, bad):
    p = _eod_payload(cov=_cov(), names=("ABC", "DEF"))
    p["eod_withheld"] = bad
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: p)
    assert cr._run_eod(EOD_NOW, force=True) == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None


def test_unverified_held_list_stays_unverified_with_a_withheld_name(monkeypatch):
    p = _eod_payload(withheld=["ABC"])
    p["held_tickers"] = None
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: p)
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert "held-ticker list unavailable, coverage unverified" in cr._LAST_LANE_FAILURE_DETAIL
    assert "saved copy" not in cr._LAST_LANE_FAILURE_DETAIL


def test_revision_shadow_does_not_record_a_saved_copy_price(monkeypatch):
    seen = {}

    def _build(port_df, held, ctx, today, now, source):
        seen["port"] = list(port_df["Ticker"])
        seen["held"] = sorted(held)
        return []
    p = _eod_payload(withheld=["ABC"], held_data={"ABC": {"earnings": None}, "DEF": {"earnings": None}})
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: p)
    monkeypatch.setattr("stock_analyzer.revision_flag.build_shadow_rows", _build)
    monkeypatch.setattr(cr.db, "load_earnings_context_batch", lambda tickers: {})
    monkeypatch.setattr(cr.db, "save_earnings_revision_shadow",
                        lambda rows: {"saved": 0, "attempted": 0})
    cr._run_eod(EOD_NOW, force=True)
    assert seen == {"port": ["DEF"], "held": ["DEF"]}
    # a clean book still passes every name
    seen.clear()
    p2 = _eod_payload(cov=_cov(), held_data={"ABC": {"earnings": None}, "DEF": {"earnings": None}})
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: p2)
    monkeypatch.setattr("stock_analyzer.revision_flag.build_shadow_rows", _build)
    cr._run_eod(EOD_NOW, force=True)
    assert seen == {"port": ["ABC", "DEF"], "held": ["ABC", "DEF"]}


# ── cron: the carried-stop writer ────────────────────────────────────────────

def test_carry_writer_writes_nothing_for_a_saved_copy_name_and_does_not_sweep_it(logs, dbc):  # noqa: F811
    payload, now, *_ = _scene()
    T = next(iter(payload["held_stops"]))
    payload["eod_withheld"] = [T]
    assert cr._eod_held_stop_carry(payload, now) == []
    assert dbc["save"] == [], "a stop built from a cached frame is never saved"
    assert any(f"not written for saved-copy name(s): {T}" in m for m in logs)
    assert dbc["delete"] == [], "held_tickers keeps it: saved - held is empty"
    # the same scene without the key still writes (regression)
    payload.pop("eod_withheld")
    logs.clear()
    cr._eod_held_stop_carry(payload, now)
    assert len(dbc["save"]) == 1


def test_carry_writer_filter_keeps_full_basis_and_fresh_names(monkeypatch):
    seen = {}

    def _rows(held_stops, as_of, **kw):
        seen["names"] = sorted(held_stops)
        return []
    monkeypatch.setattr(cr._held_stop_carry, "rows_to_write", _rows)
    monkeypatch.setattr(cr.db, "load_held_stop_carry_tickers_or_none", lambda: set())
    monkeypatch.setattr(cr, "_log", lambda *a, **k: None)
    now = datetime.datetime(2026, 11, 25, 17, 30)
    payload = {"held_stops": {"AAA": {"basis": "partial"}, "BBB": {"basis": "full"},
                              "CCC": {"basis": "partial"}, "DDD": {"basis": "prior_session"}},
               "held_stop_carry_readable": True, "held_stop_journal_readable": True,
               "held_stop_journal_sigs": {}, "held_stop_session_closes": {},
               "held_stop_carry": {}, "held_tickers": ["AAA", "BBB", "CCC", "DDD"],
               "eod_withheld": ["aaa", "BBB", "DDD"]}
    cr._eod_held_stop_carry(payload, now)
    assert seen["names"] == ["BBB", "CCC"], "full basis came from the fresh long history"


# ── premarket: exit_signals capture ──────────────────────────────────────────

_DET = [{"tier": "TRIM", "ticker": "ABC", "composite_score": 40, "price": 10.0},
        {"tier": "EXIT", "ticker": "DEF", "composite_score": 30, "price": 11.0}]
_RISK_OFF = [{"ticker": "ABC", "composite_score": 40, "price": 10.0}]


def _capture(monkeypatch):
    saved = []
    monkeypatch.setattr(cr.db, "save_exit_signals_batch",
                        lambda rows: saved.append([dict(r) for r in rows]) or True)
    return saved


def test_a_saved_copy_names_exit_signals_are_still_recorded_and_named_in_the_log(monkeypatch, capsys):
    saved = _capture(monkeypatch)
    abc_breach = {"kind": "stop_breach", "ticker": "ABC", "why": "w"}
    rc, sent, *_rest = _run_pm(monkeypatch, _pm_payload(
        alerts=[abc_breach], coverage=_pm_coverage(cached=["ABC"]),
        all_deterioration_signals=_DET, risk_off_signals=_RISK_OFF))
    out = capsys.readouterr().out
    # C5 review (blocking): the rows feed the 09:45 email's exit block, so they stay.
    assert sorted((r["ticker"], r["signal_type"]) for r in saved[0]) == [
        ("ABC", "RISK_OFF"), ("ABC", "TRIM"), ("DEF", "EXIT")]
    assert "exit_signals: 1 name(s) recorded from a saved price copy: ABC" in out
    assert len(sent) == 1
    assert rc == 1


def test_fresh_names_signals_are_recorded_and_nothing_is_logged(monkeypatch, capsys):
    saved = _capture(monkeypatch)
    _run_pm(monkeypatch, _pm_payload(all_deterioration_signals=_DET, risk_off_signals=_RISK_OFF))
    out = capsys.readouterr().out
    assert sorted((r["ticker"], r["signal_type"]) for r in saved[0]) == [
        ("ABC", "RISK_OFF"), ("ABC", "TRIM"), ("DEF", "EXIT")]
    assert "recorded from a saved price copy" not in out


def test_unverified_coverage_still_records_the_signals(monkeypatch):
    saved = _capture(monkeypatch)
    _run_pm(monkeypatch, _pm_payload(coverage=None, all_deterioration_signals=_DET))
    assert sorted(r["ticker"] for r in saved[0]) == ["ABC", "DEF"]


def test_a_payload_without_coverage_records_as_before(monkeypatch):
    saved = _capture(monkeypatch)
    legacy = {"alerts": [], "built_at": "x", "errors": [], "manual_stops_unreadable": False,
              "all_deterioration_signals": _DET}
    _run_pm(monkeypatch, legacy)
    assert sorted(r["ticker"] for r in saved[0]) == ["ABC", "DEF"]


def test_only_cached_signals_are_all_still_written(monkeypatch, capsys):
    saved = _capture(monkeypatch)
    _run_pm(monkeypatch, _pm_payload(coverage=_pm_coverage(cached=["ABC", "DEF"]),
                                     all_deterioration_signals=_DET))
    assert sorted(r["ticker"] for r in saved[0]) == ["ABC", "DEF"]
    assert "exit_signals: 2 name(s) recorded from a saved price copy: ABC, DEF" \
        in capsys.readouterr().out


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_eod_shadow_lines_use_iso_dates_and_the_exact_wording():
    cov = _cov(behind=["GHI", "JKL"])
    cov["behind"][1]["last_bar"] = "2026-11-21"
    assert pc.eod_shadow_lines(cov) == [
        "eod price basis: GHI last bar 2026-11-24, expected 2026-11-25 (shadow, not a failure)",
        "eod price basis: JKL last bar 2026-11-21, expected 2026-11-25 (shadow, not a failure)"]


@pytest.mark.parametrize("cov", [None, {}, {"behind": []}, _cov(), _cov(cached=["ABC"]),
                                 "garbage", 7, [], {"behind": "x"}, {"behind": [None]},
                                 {"behind": [{"ticker": "A"}]}])
def test_eod_shadow_lines_are_empty_and_never_raise_when_nothing_or_garbage(cov):
    assert pc.eod_shadow_lines(cov) == []


def test_eod_shadow_lines_skip_one_malformed_entry_and_keep_the_rest():
    cov = _cov(behind=["GHI"])
    cov["behind"].insert(0, {"ticker": "BAD"})
    assert pc.eod_shadow_lines(cov) == [
        "eod price basis: GHI last bar 2026-11-24, expected 2026-11-25 (shadow, not a failure)"]


def test_cached_tickers_sorted_and_empty_when_unknown():
    assert pc.cached_tickers(_cov(cached=["ZZZ", "ABC"])) == ["ABC", "ZZZ"]
    for bad in (None, "x", 7, {}, {"cached": [{"nope": 1}]}):
        assert pc.cached_tickers(bad) == []


# ── AST pins (both ends) ─────────────────────────────────────────────────────

def _func(path, name):
    tree = parse_file_cached(path)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _attr_calls(fn, module, attr):
    return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute) and c.func.attr == attr
            and isinstance(c.func.value, ast.Name) and c.func.value.id == module]


def test_compute_eod_builds_the_eod_coverage_and_returns_the_new_keys():
    fn = _func(_ROOT / "stock_analyzer" / "headless_alert_engine.py", "compute_eod")
    assert len(_attr_calls(fn, "protective_coverage", "price_coverage")) == 1
    assert len(_attr_calls(fn, "protective_coverage", "eod_withheld")) == 1
    es = _attr_calls(fn, "protective_coverage", "expected_session")
    assert len(es) == 1 and any(kw.arg == "lane" and isinstance(kw.value, ast.Constant)
                                and kw.value.value == "eod" for kw in es[0].keywords)
    keys = {k.value for r in ast.walk(fn) if isinstance(r, ast.Return)
            and isinstance(r.value, ast.Dict)
            for k in r.value.keys if isinstance(k, ast.Constant)}
    assert {"coverage", "eod_withheld"} <= keys


def test_run_eod_reads_eod_withheld_into_unpriced_and_logs_the_coverage():
    fn = _func(_ROOT / "cron_runner.py", "_run_eod")
    src = ast.unparse(fn)
    assert "payload.get('eod_withheld')" in src
    assert "(saved copy only)" in src
    assert len(_attr_calls(fn, "protective_coverage", "log_lines")) == 1
    assert len(_attr_calls(fn, "protective_coverage", "eod_shadow_lines")) == 1
    # the withheld names join `_unpriced` (the existing records-withhold rule reads it)
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_unpriced" for t in n.targets)
               and "_withheld" in ast.unparse(n.value)]
    assert assigns


def test_premarket_capture_names_cached_names_via_cached_tickers():
    fn = _func(_ROOT / "cron_runner.py", "_run_premarket")
    assert len(_attr_calls(fn, "protective_coverage", "cached_tickers")) == 1
