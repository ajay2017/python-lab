"""Sell-check-unread C4 (docs/plans/sell-check-unread-3a-3b.md): the premarket
protective lane wires `protective_coverage` / `regime_read`.

Invariant: a held name or a check the scan did not really run never reads as a clean
scan (red heartbeat + failure email, a footer line, a per-alert note), while every
protective alert that WAS computed is still sent (U-H, never hide a sell).  A total
price outage keeps its own single `price_outage` line, and a payload without the new
keys (older shape in other tests) keeps its old outcome.
"""
import ast
import datetime
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import cron_runner as cr
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import protective_coverage as pc
from stock_analyzer import regime_read as rr
from tests._ast_cache import parse_file_cached

_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 12, 1, 8, 30)       # after the go-live footer window
ENGINE_TODAY = date(2026, 11, 30)                  # Monday: expected session = Fri 11-27

_CALM = {"state": "calm", "trend": "clear", "vix": "clear", "reasons": [],
         "unread": [], "trend_unread_why": None, "vix_value": 16.0}


def _coverage(unpriced=(), cached=(), behind=(), not_dated=()):
    n_gap = len(unpriced) + len(cached) + len(behind)
    return {
        "state": "gaps" if n_gap else "full", "expected_session": "2026-11-27",
        "held_count": 4, "fresh_count": 4 - n_gap,
        "unpriced": [{"ticker": t, "reason": "bundle_failed"} for t in unpriced],
        "cached": [{"ticker": t, "last_bar": "2026-11-25", "sessions_behind": 1}
                   for t in cached],
        "behind": [{"ticker": t, "last_bar": "2026-11-25", "sessions_behind": 1}
                   for t in behind],
        "not_dated": list(not_dated),
    }


def _payload(alerts=(), coverage="full", basis="full", det_err=None, ro_err=None, **extra):
    p = {"alerts": list(alerts), "built_at": "x", "errors": [],
         "held_stops_failed": None, "held_stop_notes": [],
         "manual_stops_unreadable": False,
         "coverage": _coverage() if coverage == "full" else coverage,
         "deterioration_basis": basis, "deterioration_error": det_err,
         "risk_off_error": ro_err, "regime_read": _CALM}
    p.update(extra)
    return p


def _run_pm(monkeypatch, payload):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    saved, sent, renders, notified = [], [], [], []
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *a, **_k: saved.append(a) or True)

    def _render(*a, **k):
        renders.append((a, k))
        return ("subj", "<p/>")
    monkeypatch.setattr(cr, "render_alert_email", _render)
    monkeypatch.setattr(cr, "_send_email", lambda *a, **_k: sent.append(a) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: notified.append((lane, detail)))
    rc = cr._run_premarket(NOW, force=True)
    return rc, sent, saved, renders, notified


_BREACH = {"kind": "stop_breach", "ticker": "AAPL", "why": "w"}
_NOTHING = "nothing to act on — no email."


# ── lane: held names ─────────────────────────────────────────────────────────

def test_one_cached_name_and_no_alerts_is_red_not_a_clean_scan(monkeypatch, capsys):
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, _payload(coverage=_coverage(cached=["ABC"])))
    out = capsys.readouterr().out
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("held_cached --")
    assert "ABC (Wed Nov 25, 1 session old)" in cr._LAST_LANE_FAILURE_DETAIL
    assert len(notified) == 1 and sent == []
    assert _NOTHING not in out
    assert "no protective alert — a check didn't run, see the failure above." in out
    assert "held coverage: 3 of 4 not flagged; cached=ABC(last 2026-11-25, 1 behind)" in out


def test_all_cached_book_lists_every_name_and_still_sends_the_computed_alert(monkeypatch):
    names = ["AAPL", "MSFT", "NVDA", "TGT"]
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, _payload(alerts=[_BREACH], coverage=_coverage(cached=names)))
    assert rc == 1
    detail = cr._LAST_LANE_FAILURE_DETAIL
    assert detail.startswith("held_cached --")
    assert all(n in detail for n in names)
    assert len(sent) == 1, "a computed alert is still emailed (U-H)"
    (args, kwargs) = renders[0]
    assert args[0] == [_BREACH]
    footer = kwargs["footer_lines"]
    for n in names:
        assert any(ln.startswith(f"{n} was checked against a saved copy") for ln in footer)


def test_dropped_bundle_name_is_a_held_unchecked_line(monkeypatch):
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, _payload(alerts=[_BREACH], coverage=_coverage(unpriced=["ZZZ"])))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("held_unchecked --")
    assert "ZZZ (no price from any provider)" in cr._LAST_LANE_FAILURE_DETAIL
    assert len(sent) == 1
    assert any(ln.startswith("Not checked this morning: ZZZ.") for ln in renders[0][1]["footer_lines"])


def test_a_behind_name_goes_red(monkeypatch):
    rc, *_rest = _run_pm(monkeypatch, _payload(coverage=_coverage(behind=["GHI"])))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("held_price_behind --")


def test_coverage_none_in_a_real_payload_is_unverified_red(monkeypatch):
    rc, *_rest = _run_pm(monkeypatch, _payload(coverage=None))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("held_coverage_unverified --")


# ── lane: checks that did not run ────────────────────────────────────────────

def test_spy_6mo_unreadable_is_a_red_depth_only_line_and_alerts_still_go(monkeypatch):
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, _payload(alerts=[_BREACH], basis="depth_only"))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("deterioration_depth_only --")
    assert len(sent) == 1
    assert any(ln.startswith("Deterioration exits were checked on drawdown depth only")
               for ln in renders[0][1]["footer_lines"])


def test_raised_deterioration_engine_is_red(monkeypatch):
    rc, *_rest = _run_pm(monkeypatch, _payload(det_err="boom " * 40))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("deterioration_failed --")
    assert "boom" in cr._LAST_LANE_FAILURE_DETAIL


def test_raised_risk_off_engine_is_red(monkeypatch):
    rc, *_rest = _run_pm(monkeypatch, _payload(ro_err="kaboom"))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("risk_off_failed --")
    assert "kaboom" in cr._LAST_LANE_FAILURE_DETAIL


def test_empty_message_error_still_counts_as_raised(monkeypatch):
    rc, *_rest = _run_pm(monkeypatch, _payload(det_err=""))
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("deterioration_failed --")


# ── lane: regressions (clean day, outage, older payload shape) ───────────────

def test_clean_day_adds_no_failure_and_keeps_the_nothing_to_act_on_log(monkeypatch, capsys):
    rc, sent, saved, renders, notified = _run_pm(monkeypatch, _payload())
    out = capsys.readouterr().out
    assert rc == 0 and notified == [] and sent == []
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert _NOTHING in out
    assert "a check didn't run" not in out
    assert "held coverage: 4 of 4 not flagged" in out
    assert "risk-off regime: state=calm trend=clear vix=clear (book unmeasured)" in out


def test_regime_log_line_names_the_book_severity(monkeypatch, capsys):
    _run_pm(monkeypatch, _payload(fragility_severity="caution"))
    assert "(book caution)" in capsys.readouterr().out


def test_total_outage_keeps_only_the_old_price_outage_line(monkeypatch, capsys):
    payload = {"alerts": [], "built_at": "x", "errors": ["no holdings could be loaded"],
               "reason": "no_bundles"}
    rc, *_rest = _run_pm(monkeypatch, payload)
    out = capsys.readouterr().out
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("price_outage:")
    assert "held_" not in cr._LAST_LANE_FAILURE_DETAIL
    assert "deterioration_" not in cr._LAST_LANE_FAILURE_DETAIL
    assert "held coverage" not in out and "risk-off regime" not in out


def test_total_outage_ignores_coverage_keys_even_if_present(monkeypatch):
    payload = _payload(coverage=None, basis="depth_only", reason="no_bundles")
    rc, *_rest = _run_pm(monkeypatch, payload)
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("price_outage:")
    assert "held_coverage_unverified" not in cr._LAST_LANE_FAILURE_DETAIL
    assert "deterioration_depth_only" not in cr._LAST_LANE_FAILURE_DETAIL


def test_payload_without_the_new_keys_keeps_its_old_outcome(monkeypatch, capsys):
    legacy = {"alerts": [], "built_at": "x", "errors": [], "manual_stops_unreadable": False}
    rc, sent, saved, renders, notified = _run_pm(monkeypatch, legacy)
    out = capsys.readouterr().out
    assert rc == 0 and notified == []
    assert _NOTHING in out
    assert "held coverage" not in out and "risk-off regime" not in out
    # and an alert-carrying older payload: footer untouched, fingerprint = the alert only
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, {"alerts": [_BREACH], "built_at": "x", "errors": []})
    assert rc == 0 and len(sent) == 1
    assert renders[0][1]["footer_lines"] == []
    assert saved[0][1] == cr._fingerprint([_BREACH])


# ── lane: dedup fingerprint ──────────────────────────────────────────────────

def test_fingerprint_includes_coverage_gap_so_a_later_slot_that_reprices_resends(monkeypatch):
    rc, sent, saved, *_rest = _run_pm(
        monkeypatch, _payload(alerts=[_BREACH], coverage=_coverage(cached=["ABC"])))
    gap_fp = cr._fingerprint([_BREACH, {"kind": "coverage_gap", "ticker": "ABC"}])
    assert saved[0][1] == gap_fp
    assert gap_fp != cr._fingerprint([_BREACH]), "a re-priced name must change the fingerprint"
    # the same alert with the name now fresh is a different set, so it would send again
    rc, sent, saved, *_rest = _run_pm(monkeypatch, _payload(alerts=[_BREACH]))
    assert saved[0][1] == cr._fingerprint([_BREACH])


def test_gap_only_run_saves_the_gap_fingerprint(monkeypatch):
    rc, sent, saved, *_rest = _run_pm(
        monkeypatch, _payload(coverage=_coverage(behind=["GHI"])))
    assert sent == []
    assert saved[0][1] == cr._fingerprint([{"kind": "coverage_gap", "ticker": "GHI"}])


# ── engine ───────────────────────────────────────────────────────────────────

def _frame(last, rows=5):
    idx = pd.DatetimeIndex(pd.bdate_range(end=last, periods=rows))
    return pd.DataFrame({"Close": [10.0] * rows}, index=idx)


def _row(ticker, gap):
    return {"Ticker": ticker, "Gap to Stop (%)": gap, "Price": 10.0, "Shares": 5, "Stop": 11.0,
            "Weight (%)": 10.0, "P&L (%)": 1.0, "Stop Type": "Trailing", "Score": 60.0}


def _ctx(spy_6mo=None, spy_1y=None, vix=None, fragility=None):
    return {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame([_row("AAA", -2.0), _row("BBB", 5.0)]),
        "held_data": {
            "AAA": {"df": _frame("2026-11-25"), "stale_as_of": "2026-11-25", "financials": {}},
            "BBB": {"df": _frame("2026-11-27"), "stale_as_of": None, "financials": {}},
        },
        "fragility": fragility, "spy_6mo": spy_6mo, "spy_1y": spy_1y, "vix": vix,
        "held_tickers": ["AAA", "BBB"],
    }


def _engine(ctx, det=None, risk_off=None, det_exc=None, ro_exc=None):
    det_kw = {"side_effect": det_exc} if det_exc else {"return_value": det or []}
    ro_kw = {"side_effect": ro_exc} if ro_exc else {"return_value": risk_off or []}
    with patch.object(hae, "_build_context", return_value=ctx), \
         patch.object(hae, "deterioration_signals", **det_kw), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk", **ro_kw):
        return hae.compute_protective_alerts(ENGINE_TODAY)


def test_engine_returns_coverage_basis_regime_and_error_fields():
    r = _engine(_ctx(fragility={"severity": "caution"}))
    assert r["coverage"]["state"] == "gaps"
    assert [c["ticker"] for c in r["coverage"]["cached"]] == ["AAA"]
    assert r["deterioration_basis"] == "depth_only"          # spy_6mo None
    assert r["deterioration_error"] is None and r["risk_off_error"] is None
    assert r["regime_read"]["state"] == "unread"             # spy_1y and vix both None
    assert r["fragility_severity"] == "caution"


def test_engine_fragility_severity_none_when_fragility_is_not_a_dict():
    assert _engine(_ctx(fragility=None))["fragility_severity"] is None


def test_engine_deterioration_basis_full_with_a_long_spy_history():
    spy = pd.DataFrame({"Close": [100.0 + i for i in range(400)]},
                       index=pd.bdate_range(end="2026-11-27", periods=400))
    assert _engine(_ctx(spy_6mo=spy))["deterioration_basis"] == "full"


def test_engine_cached_breach_keeps_its_alert_and_gains_the_note():
    r = _engine(_ctx())
    breach = [a for a in r["alerts"] if a["kind"] == "stop_breach"]
    assert [a["ticker"] for a in breach] == ["AAA"], "the alert is never hidden (U-H)"
    note = pc.alert_note(r["coverage"], "AAA")
    assert note and note.startswith("Priced from a saved copy")
    assert breach[0]["why"].endswith(" " + note)
    assert breach[0]["why"].startswith("Price $10.00 is below the Trailing stop")


def test_engine_note_reaches_deterioration_exit_and_risk_off_cards_too():
    det = [{"tier": "EXIT", "ticker": "AAA", "dd_from_peak_pct": 30, "trend_ma": 50,
            "exit_floor": 25, "pnl_pct": -5, "weight_pct": 10}]
    ctx = _ctx()
    ctx["port_df"] = pd.DataFrame([_row("AAA", 4.0), _row("BBB", 5.0)])   # no breach
    r = _engine(ctx, det=det)
    exit_alert = next(a for a in r["alerts"] if a["kind"] == "deterioration_exit")
    assert exit_alert["why"].endswith(pc.alert_note(r["coverage"], "AAA"))
    card = {"ticker": "AAA", "action": "TRIM — Risk-Off", "directive": "d", "why": "beta",
            "trigger": "t", "weight": 10, "pnl_pct": 1}
    r2 = _engine(ctx, risk_off=[card])
    ro_alert = next(a for a in r2["alerts"] if a["kind"] == "risk_off_derisk")
    assert ro_alert["why"].startswith("beta ") and "Priced from a saved copy" in ro_alert["why"]
    # a fresh name's alert carries no note
    card_b = dict(card, ticker="BBB")
    r3 = _engine(ctx, risk_off=[card_b])
    assert next(a for a in r3["alerts"] if a["ticker"] == "BBB")["why"] == "beta"


def test_engine_keeps_the_raised_messages_and_the_engine_notes():
    r = _engine(_ctx(), det_exc=RuntimeError("det boom"))
    assert r["deterioration_error"] == "det boom"
    assert "deterioration_signals failed: det boom" in r["errors"]
    assert r["risk_off_error"] is None
    r = _engine(_ctx(), ro_exc=RuntimeError("ro boom"))
    assert r["risk_off_error"] == "ro boom"
    assert "assess_risk_off_derisk failed: ro boom" in r["errors"]
    assert r["deterioration_error"] is None
    assert [a["kind"] for a in r["alerts"]] == ["stop_breach"], "computed alerts survive"


def test_engine_helpers_never_raise_the_engine():
    with patch.object(hae.protective_coverage, "price_coverage", side_effect=RuntimeError("x")), \
         patch.object(hae.protective_coverage, "deterioration_basis", side_effect=RuntimeError("x")), \
         patch.object(hae.regime_read, "read_regime",
                      side_effect=[RuntimeError("x"), rr.read_regime(None, None)]):
        r = _engine(_ctx())
    assert r["coverage"] is None
    assert r["deterioration_basis"] == "depth_only"
    assert r["regime_read"]["state"] == "unread"
    assert [a["ticker"] for a in r["alerts"]] == ["AAA"]


def test_engine_ctx_not_ok_early_return_is_unchanged():
    r = _engine({"ok": False, "errors": ["no holdings could be loaded"], "reason": "no_bundles"})
    assert r["reason"] == "no_bundles"
    for k in ("coverage", "deterioration_basis", "regime_read"):
        assert k not in r


def test_engine_payload_through_the_lane_is_red_and_still_sends_the_noted_alert(monkeypatch):
    payload = _engine(_ctx())
    rc, sent, saved, renders, notified = _run_pm(monkeypatch, payload)
    assert rc == 1 and len(sent) == 1
    assert "held_cached --" in cr._LAST_LANE_FAILURE_DETAIL
    assert "deterioration_depth_only --" in cr._LAST_LANE_FAILURE_DETAIL
    sent_alerts = renders[0][0][0]
    assert "Priced from a saved copy" in sent_alerts[0]["why"]


def test_build_context_logs_a_failed_spy_1y_fetch():
    from tests.test_headless_alert_engine import _patch_context_deps, _run_build_context
    cfg = _patch_context_deps()
    cfg["fetch_spy_side_effect"] = [cfg["fetch_spy_6mo"], RuntimeError("spy 1y down")]
    result = _run_build_context(cfg)
    assert result["ok"] is True
    assert "SPY 1y fetch failed" in result["errors"]
    assert "SPY 6mo fetch failed" not in result["errors"]
    assert result["spy_1y"] is None


# ── AST pins (both ends) ─────────────────────────────────────────────────────

def _func(path, name):
    tree = parse_file_cached(path)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _attr_calls(fn, module, attr):
    return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute) and c.func.attr == attr
            and isinstance(c.func.value, ast.Name) and c.func.value.id == module]


def test_engine_builds_coverage_from_price_coverage_and_returns_it():
    fn = _func(_ROOT / "stock_analyzer" / "headless_alert_engine.py", "compute_protective_alerts")
    assert len(_attr_calls(fn, "protective_coverage", "price_coverage")) == 1
    assert len(_attr_calls(fn, "protective_coverage", "deterioration_basis")) == 1
    assert len(_attr_calls(fn, "regime_read", "read_regime")) >= 1
    assert len(_attr_calls(fn, "protective_coverage", "alert_note")) == 1
    keys = {k.value for r in ast.walk(fn) if isinstance(r, ast.Return)
            and isinstance(r.value, ast.Dict)
            for k in r.value.keys if isinstance(k, ast.Constant)}
    assert {"coverage", "deterioration_basis", "deterioration_error", "risk_off_error",
            "regime_read", "fragility_severity"} <= keys


def test_premarket_lane_extends_failures_with_lane_failures_and_footer_with_email_lines():
    fn = _func(_ROOT / "cron_runner.py", "_run_premarket")
    lf = _attr_calls(fn, "protective_coverage", "lane_failures")
    assert len(lf) == 1
    assert any(kw.arg == "lane" and isinstance(kw.value, ast.Constant) and kw.value.value == "premarket"
               for kw in lf[0].keywords)
    # the lane_failures result is what failures.extend receives
    assigned = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and n.value is lf[0]]
    assert assigned and isinstance(assigned[0].targets[0], ast.Name)
    var = assigned[0].targets[0].id
    extends = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
               and isinstance(c.func, ast.Attribute) and c.func.attr == "extend"
               and isinstance(c.func.value, ast.Name) and c.func.value.id == "failures"
               and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == var]
    assert len(extends) == 1
    ee = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
          and isinstance(c.func, ast.Attribute) and c.func.attr == "extend"
          and isinstance(c.func.value, ast.Name) and c.func.value.id == "_footer_lines"
          and c.args and isinstance(c.args[0], ast.Call)
          and isinstance(c.args[0].func, ast.Attribute)
          and c.args[0].func.attr == "email_lines"
          and isinstance(c.args[0].func.value, ast.Name)
          and c.args[0].func.value.id == "protective_coverage"]
    assert len(ee) == 1
    assert len(_attr_calls(fn, "protective_coverage", "log_lines")) == 1
    assert len(_attr_calls(fn, "regime_read", "log_text")) == 1
    assert len(_attr_calls(fn, "protective_coverage", "gap_tickers")) == 1


def test_eod_lane_is_not_wired_in_c4():
    """C5 owns the EOD leg; C4 must not touch it."""
    fn = _func(_ROOT / "cron_runner.py", "_run_eod")
    assert "protective_coverage" not in ast.unparse(fn)
    eod = _func(_ROOT / "stock_analyzer" / "headless_alert_engine.py", "compute_eod")
    assert "protective_coverage" not in ast.unparse(eod)
