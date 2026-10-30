"""#26 C3 chunk (c): the atomic switch of every stop consumer to the held stop.

Pins both ends of every wiring (AST), the portfolio build with `held_stops`,
the #24 alert wording, the manual-stop parity, the D4 notice window, and two
real-shaped fixtures (GOOGL: extended-hours fill, breach, "Sell all 5 shares at
next open"; FSLR: episode 10-12, add 10-16). No network, no DB."""
import ast
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import daily_briefing as db_brief
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_view as hv
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import portfolio as pf
from stock_analyzer import risk
from stock_analyzer import stop_breach_text as sbt
from tests._ast_cache import parse_file_cached
from tests._held_stops import hs_for

_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _ROOT / "app.py"
_APP = _APP_PATH.read_text(encoding="utf-8")
_HEADLESS_PATH = _ROOT / "stock_analyzer" / "headless_alert_engine.py"


def _calls(tree, name):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                    isinstance(f, ast.Attribute) and f.attr == name):
                out.append(n)
    return out


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


# ── AST parity: both ends of every build_portfolio_df call ───────────────────
def test_app_build_portfolio_df_calls_pass_held_stops_from_held_stops_for():
    tree = parse_file_cached(_APP_PATH)
    calls = _calls(tree, "build_portfolio_df")
    assert len(calls) == 2, "the two app.py build sites (post-trade refresh and Home)"
    seen = set()
    for c in calls:
        v = _kw(c, "held_stops")
        assert v is not None, "held_stops= is REQUIRED at every call"
        if isinstance(v, ast.Call):
            assert v.func.id == "_held_stops_for"
            seen.add("inline")
        else:
            assert isinstance(v, ast.Name) and v.id == "_held_stops"
            seen.add("name")
    assert seen == {"inline", "name"}
    # the Home site's variable is assigned from _held_stops_for(...)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_held_stops" for t in n.targets)]
    assert assigns and all(isinstance(a.value, ast.Call) and a.value.func.id == "_held_stops_for"
                           for a in assigns)


def test_held_stops_for_uses_strict_trades_et_today_and_build_held_stops():
    fn = _func(parse_file_cached(_APP_PATH), "_held_stops_for")
    src = ast.unparse(fn)
    assert "_budget_strict_trades()" in src
    assert "_today_et()" in src
    bhs = _calls(fn, "build_held_stops")
    assert bhs and _kw(bhs[0], "fetch_history") is not None
    assert "_unadjusted_history_or_raise" in ast.unparse(_kw(bhs[0], "fetch_history"))


def test_unadjusted_history_is_cached_per_et_day_and_failures_raise():
    tree = parse_file_cached(_APP_PATH)
    fn = _func(tree, "_cached_unadjusted_history")
    dec = ast.unparse(fn.decorator_list[0])
    assert "cache_data" in dec and "86400" in dec          # ttl = 1 day
    assert "et_date" in [a.arg for a in fn.args.args]      # no leading underscore: part of the key
    assert "fetch_or_raise" in ast.unparse(fn)             # a failure raises, so it isn't cached
    assert "neg_cache_active(" in ast.unparse(_func(tree, "_unadjusted_history_or_raise"))


def test_headless_build_context_wiring():
    tree = parse_file_cached(_HEADLESS_PATH)
    ctx = _func(tree, "_build_context")
    src = ast.unparse(ctx)
    assert "db.load_trades_or_none()" in src
    bhs = _calls(ctx, "build_held_stops")
    assert len(bhs) == 1
    assert "trades_strict" in ast.unparse(bhs[0]) and "today" in ast.unparse(bhs[0].args[3])
    assert ast.unparse(_kw(bhs[0], "fetch_history")) == "_held_stop_wiring.fetch_or_raise"
    pdf = _calls(ctx, "build_portfolio_df")
    assert len(pdf) == 1 and ast.unparse(_kw(pdf[0], "held_stops")) == "held_stops"


def test_no_old_stop_engine_left_in_app():
    assert not re.search(r"\bprotective_stop\(", _APP)
    assert not re.search(r"\bstop_ladder\(", _APP)


def test_synth_sig_has_held_sig_and_hit_test_uses_brief_breached_set():
    m = re.search(r"_synth_sig = \((.*?)\n    \)\n", _APP, re.S)
    assert m and "held_sig(" in m.group(1)
    assert "_SYNTH_SCHEMA_VER = 21" in _APP
    hit = re.search(r"_breached_now = .*?_synth_hit = \(.*?\)\n", _APP, re.S)
    assert hit and "brief_breached_set(" in hit.group(0)
    assert "_breached_now - _breached_at_build" in hit.group(0)
    assert '"breached_at_build": frozenset(_breached_now)' in _APP


def test_analysis_merge_uses_analysis_manual_override():
    assert "_held_stop_wiring.analysis_manual_override(" in _APP
    assert "manual_stop_wins(_ms_p" not in _APP


def test_banner_sites_render_held_stop_basis_and_notice():
    assert "_held_stops_banner_text(_held_stops)" in _APP
    assert "_held_stop_view.recalc_notice(_today_et())" in _APP


# ── portfolio build with held_stops ──────────────────────────────────────────
def _row(price=100.0, stop=90.0):
    return {"current_price": price, "stop": stop, "atr": 3.0, "sector": "Technology",
            "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}


def _hs(stop, label="Trailing Stop", basis="full", note=""):
    return {"stop": stop, "label": label, "basis": basis, "binding": "trailing",
            "note": note, "since": date(2026, 9, 1), "atr_last": 2.5, "hw_close": 110.0,
            "components": {"entry": 80.0, "trailing": stop, "ratchet": None}}


H = [{"Ticker": "AAA", "Shares": 10, "Avg Cost ($)": 80.0}]


def test_held_stops_is_a_required_keyword_without_default():
    with pytest.raises(TypeError):
        pf.build_portfolio_df(H, {"AAA": _row()})          # type: ignore[call-arg]
    import inspect
    p = inspect.signature(pf.build_portfolio_df).parameters["held_stops"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty


def test_stop_comes_from_held_stops_and_loaded_data_is_untouched():
    loaded = {"AAA": _row(price=100.0, stop=90.0)}
    before = {"stop": loaded["AAA"]["stop"], "atr": loaded["AAA"]["atr"]}
    df = pf.build_portfolio_df(H, loaded, held_stops={"AAA": _hs(95.5)})
    r = df.iloc[0]
    assert r["Stop"] == 95.5 and r["Stop Type"] == "Trailing Stop" and r["Auto Stop"] == 95.5
    assert r["Stop Basis"] == "full" and r["Stop ATR"] == 2.5 and r["Stop HW Close"] == 110.0
    assert r["Stop Trailing"] == 95.5 and r["Stop Entry"] == 80.0
    assert r["Gap to Stop (%)"] == round((100.0 - 95.5) / 100.0 * 100, C.GAP_TO_STOP_ROUND_DECIMALS)
    assert loaded["AAA"]["stop"] == before["stop"] and loaded["AAA"]["atr"] == before["atr"]


@pytest.mark.parametrize("held_stops", [None, {}, {"OTHER": {"stop": 50.0}},
                                         {"AAA": {"stop": None, "label": "Stop Unavailable"}}])
def test_missing_held_stop_is_stop_unavailable_never_the_old_engine(held_stops):
    df = pf.build_portfolio_df(H, {"AAA": _row(stop=90.0)}, held_stops=held_stops)
    r = df.iloc[0]
    assert r["Stop Type"] == "Stop Unavailable" and r["Stop Basis"] == "unavailable"
    assert pd.isna(r["Stop"]) and pd.isna(r["Gap to Stop (%)"]) and pd.isna(r["Auto Stop"])


def test_manual_parity_between_build_portfolio_df_and_analysis_override():
    for auto, manual in [(95.0, 94.0), (95.0, 95.0), (95.0, 96.2), (None, 70.0), (None, None)]:
        hstops = {"AAA": _hs(auto)} if auto is not None else {}
        ms = {"AAA": {"stop_price": manual, "set_at": "x"}} if manual is not None else {}
        df = pf.build_portfolio_df(H, {"AAA": _row()}, manual_stops=ms, held_stops=hstops)
        r = df.iloc[0]
        want_stop, want_manual = hw.analysis_manual_override(auto, ms.get("AAA"))
        if want_stop is None:
            assert pd.isna(r["Stop"])
        else:
            assert r["Stop"] == want_stop
        assert (r["Stop Type"] == "Manual") == want_manual


def test_manual_stop_applies_when_automatic_stop_unavailable():
    df = pf.build_portfolio_df(H, {"AAA": _row()}, manual_stops={"AAA": {"stop_price": 88.0}},
                               held_stops=None)
    r = df.iloc[0]
    assert r["Stop"] == 88.0 and r["Stop Type"] == "Manual" and pd.isna(r["Auto Stop"])
    assert pd.isna(r["Stop Type Auto"]) or r["Stop Type Auto"] is None


def test_bundle_stop_still_equals_risk_atr_stop_loss():
    # entry (fresh-buy / Grow Today add sizing) stops never change: bundle_loader
    # calls risk.atr_stop_loss with ATR_STOP_MULT and nothing in C3 touches it.
    src = (_ROOT / "stock_analyzer" / "bundle_loader.py").read_text(encoding="utf-8")
    assert "stop, atr_val = atr_stop_loss(df, multiplier=ATR_STOP_MULT)" in src
    df = pd.DataFrame({"High": np.arange(100.0, 130.0) + 1, "Low": np.arange(100.0, 130.0) - 1,
                       "Close": np.arange(100.0, 130.0)})
    stop, atr = risk.atr_stop_loss(df, multiplier=C.ATR_STOP_MULT)
    assert stop == round(129.0 - C.ATR_STOP_MULT * risk._atr_value(df), 2)


# ── #24 alert wording ────────────────────────────────────────────────────────
def test_alert_breach_messages():
    m = sbt.alert_breach_msg("WDAY", -2.5, 200.25)
    assert m == ("⛔ **WDAY** is 2.5% below its stop $200.25 — breached; "
                 "Today's Brief says sell at the next open")
    for g in (0.0, -0.0):
        z = sbt.alert_breach_msg("WDAY", g, 200.25)
        assert "is at or just above its stop $200.25" in z and "below" not in z
    for msg in (m, z):
        assert msg.count("$") == 1


def _alert_df(gap):
    stop = round(100.0 * (1 - gap / 100.0), 2)
    return pf.build_portfolio_df(H, {"AAA": _row(price=100.0)}, held_stops={"AAA": _hs(stop)}), stop


@pytest.mark.parametrize("gap,want", [(-2.5, "is 2.5% below its stop $102.50"),
                                      (0.0, "is at or just above its stop $100.00")])
def test_signals_alert_for_a_breach_is_danger_and_not_within_wording(gap, want):
    df, _ = _alert_df(gap)
    out = [a for a in pf.alerts(df, None) if a["category"] == "stop"]
    assert len(out) == 1 and out[0]["level"] == "danger"
    assert want in out[0]["msg"] and "within" not in out[0]["msg"]
    assert "breached; Today's Brief says sell at the next open" in out[0]["msg"]
    assert out[0]["msg"].count("$") == 1


def test_signals_alert_just_above_the_stop_is_unchanged():
    df, _ = _alert_df(0.1)
    out = [a for a in pf.alerts(df, None) if a["category"] == "stop"]
    assert out and out[0]["msg"] == "🔴 **AAA** is within 0.1% of stop $99.90 — review immediately"


def test_stop_noun_never_doubles_stop():
    assert sbt.stop_noun("Trailing Stop") == "trailing stop"
    assert sbt.stop_noun("Entry Stop") == "entry stop"
    assert sbt.stop_noun("Manual") == "manual stop"
    assert sbt.stop_noun("Prior-close ATR") == "prior-close ATR stop"
    assert sbt.stop_noun("Stop Unavailable") == "stop" and sbt.stop_noun(None) == "stop"
    assert sbt.stop_noun("Protect 25% gain") == "profit-lock stop (Protect 25% gain)"
    for label in ("Trailing Stop", "Entry Stop", "Manual", "Prior-close ATR", "ATR Stop"):
        assert "stop stop" not in sbt.stop_noun(label).lower()
        assert "stop stop" not in sbt.breach_why(100.0, 101.0, label, -1.0).lower()


# ── D4 notice window ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("today,visible", [
    (date(2026, 11, 6), False),    # HELD_TRAIL_START itself: before go-live
    (date(2026, 11, 7), True),     # deploy Saturday: 0 sessions elapsed
    (date(2026, 11, 8), True),
    (date(2026, 11, 9), True),     # session 1
    (date(2026, 11, 13), True),    # session 5 = HELD_STOP_NOTICE_SESSIONS
    (date(2026, 11, 14), True),    # weekend after session 5 (still 5)
    (date(2026, 11, 16), False),   # session 6
    (date(2026, 12, 1), False),
])
def test_notice_window(today, visible):
    assert C.HELD_TRAIL_START == "2026-11-06" and C.HELD_STOP_NOTICE_SESSIONS == 5
    assert hv.notice_visible(today) is visible
    assert (hv.recalc_notice(today) is not None) is visible
    assert (hv.email_notice_line(today) is not None) is visible


def test_notice_text_is_true_and_says_sell_all_possible():
    t = hv.recalc_notice(date(2026, 11, 9))
    assert "recalculated" in t and "only moves up" in t and "sell-all" in t
    assert "$" not in t and "**" not in t
    e = hv.email_notice_line(date(2026, 11, 9))
    assert "recalculated" in e and "sell-all" in e


def test_notice_respects_a_holiday_in_the_window(monkeypatch):
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2026-11-20")
    # 11-26 is Thanksgiving (not a session): 11-23, 24, 25, 27, 30 are the five sessions
    assert hv.sessions_since_go_live(date(2026, 11, 30)) == 5
    assert hv.notice_visible(date(2026, 11, 30)) is True
    assert hv.notice_visible(date(2026, 12, 1)) is False


# ── #28 wording ──────────────────────────────────────────────────────────────
def test_news_trigger_and_directive_do_not_assume_a_stop_you_set():
    src = (_ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    assert "tighten your stop" not in src
    assert "→ Act Today critical news (consider a tighter manual stop)." in src
    assert "consider setting a tighter manual stop" in src


# ── real-shaped fixtures ─────────────────────────────────────────────────────
def _gap_frame():
    """GOOGL-like: prior close 324.89; entry-session range 280.36-288.33."""
    idx = pd.bdate_range("2026-04-01", periods=130)
    close = np.full(len(idx), 320.0)
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                       "Close": close}, index=idx)
    k = 120
    df.iloc[k - 1, df.columns.get_loc("Close")] = 324.89
    df.iloc[k, df.columns.get_loc("Open")] = 286.88
    df.iloc[k, df.columns.get_loc("High")] = 288.33
    df.iloc[k, df.columns.get_loc("Low")] = 280.36
    df.iloc[k, df.columns.get_loc("Close")] = 282.0
    return df, k


def _journal(rows):
    return pd.DataFrame([{"id": i, "ticker": t, "action": a, "shares": sh, "price": px,
                          "traded_at": f"{d}T14:00:00+00:00"}
                         for i, (t, a, sh, px, d) in enumerate(rows, start=1)])


@pytest.fixture
def early_trail(monkeypatch):
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2026-01-01")


def test_googl_extended_hours_fill_breached_sell_all_5_shares(early_trail):
    df, k = _gap_frame()
    E = df.index[k].date()
    as_of = df.index[k + 3].date()
    holdings = [{"Ticker": "GOOGL", "Shares": 5.0, "Shares Exact": 5.0, "Avg Cost ($)": 310.60}]
    trades = _journal([("GOOGL", "BUY", 5.0, 310.60, E.isoformat())])
    stops = hw.build_held_stops(holdings, {}, trades, as_of, fetch_history=lambda t, p: df)
    g = stops["GOOGL"]
    assert g["basis"] == "full" and g["components"]["entry"] is not None   # fill accepted
    assert g["stop"] > 283.82
    loaded = {"GOOGL": {"current_price": 283.82, "stop": 0.0, "sector": "Technology",
                        "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}}
    port = pf.build_portfolio_df(holdings, loaded, held_stops=stops)
    row = port.iloc[0]
    assert row["Gap to Stop (%)"] <= 0
    assert hw.brief_breached_set(port) == {"GOOGL"}
    items = [i for i in db_brief._act_today(port, [], [], [], [], as_of)
             if i.get("kind") == "stop_breach"]
    assert len(items) == 1
    assert items[0]["directive"] == "Sell all 5 shares at next open — mechanical stop rule."
    assert "$283.82" in items[0]["why"] and "stop stop" not in items[0]["why"].lower()
    alert = [a for a in pf.alerts(port, None) if a["category"] == "stop"]
    assert alert and "below its stop" in alert[0]["msg"]


def test_fslr_episode_oct12_add_oct16_ratchet_restarts_at_the_add(early_trail):
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2026-05-01", end="2026-10-29")
    close = 150 * np.exp(np.cumsum(rng.normal(0.001, 0.012, len(idx))))
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                       "Close": close}, index=idx)
    e1, e2 = date(2026, 10, 12), date(2026, 10, 16)
    px1, px2 = float(df.loc[pd.Timestamp(e1), "Close"]), float(df.loc[pd.Timestamp(e2), "Close"])
    trades = _journal([("FSLR", "BUY", 4.0, px1, e1.isoformat()),
                       ("FSLR", "BUY", 2.0, px2, e2.isoformat())])
    avg = (4 * px1 + 2 * px2) / 6
    holdings = [{"Ticker": "FSLR", "Shares": 6.0, "Shares Exact": 6.0, "Avg Cost ($)": avg}]
    out = hw.build_held_stops(holdings, {}, trades, date(2026, 10, 30),
                              fetch_history=lambda t, p: df)["FSLR"]
    assert out["basis"] == "full" and out["since"] == e1
    ep = hs.episode_from_journal("FSLR", trades, 6.0, shares_tol=C.HELD_STOP_SHARES_TOL)
    assert ep["start"] == e1 and ep["last_buy"] == e2 and ep["first_buy_price"] == pytest.approx(px1)
    # the high-water for the ratchet restarts at the add (Oct 16), not Oct 12
    assert out["hw_session"] is None or out["hw_session"] >= e2
    assert out["components"]["entry"] is not None and out["components"]["trailing"] is not None


# ── review fixes ─────────────────────────────────────────────────────────────
def test_exit_plan_for_a_held_sell_uses_the_held_stop_not_the_bundle_stop():
    tree = parse_file_cached(_APP_PATH)
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and "ATR downside level" in n.value]
    assert len(hits) == 1
    # find the innermost If chain that owns the "ATR downside level" caption
    owners = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and any(hits[0] is c for c in ast.walk(ast.Module(body=n.orelse, type_ignores=[])))]
    assert owners, "the ATR caption must live in an else/elif branch"
    held_if = [o for o in owners if ast.unparse(o.test) == "_sa_holding"]
    assert held_if, "the ATR caption must sit in the orelse of `if _sa_holding:`"
    body_src = "\n".join(ast.unparse(b) for b in held_if[-1].body)
    assert "held_exit_stop_caption(_sa_holding, price)" in body_src
    assert "basis_caption(_sa_holding)" in body_src


def test_held_exit_stop_caption_messages():
    row = {"Stop": 102.5, "Stop Type": "Trailing Stop", "Gap to Stop (%)": -2.5}
    t = hv.held_exit_stop_caption(row, 100.0)
    assert "Your trailing stop is breached" in t and "sell all at the next open" in t
    ok = hv.held_exit_stop_caption({"Stop": 95.0, "Stop Type": "Manual", "Gap to Stop (%)": 5.0}, 100.0)
    assert ok.startswith("📉 Your manual stop:") and "breached" not in ok
    assert ok.endswith("if the price breaks below it, Today's Brief calls a full exit at the next open.")
    none = hv.held_exit_stop_caption({"Stop": float("nan"), "Stop Type": "Stop Unavailable"}, 100.0)
    assert "No stop is available" in none
    assert hv.held_exit_stop_caption(row, None) is None


def test_analysis_merge_maps_every_held_row_and_never_skips_one():
    i = _APP.index("_auto_map = {}")
    seg = _APP[i:i + 900]
    assert '_pos_or_zero(_h.get("Auto Stop")) or None' in seg
    assert "except (TypeError, ValueError)" not in seg.split("for _t, _r in")[0]


def test_held_stops_for_keeps_a_ticker_whose_build_raised():
    fn = _func(parse_file_cached(_APP_PATH), "_held_stops_for")
    assert "unavailable_result" in ast.unparse(fn)


def test_alerts_stop_unavailable_fires_for_nan_gap_in_a_mixed_book():
    two = H + [{"Ticker": "BBB", "Shares": 10, "Avg Cost ($)": 80.0}]
    df = pf.build_portfolio_df(two, {"AAA": _row(), "BBB": _row()},
                               held_stops={"AAA": _hs(95.0)})          # BBB has no stop
    assert pd.isna(df.set_index("Ticker").loc["BBB", "Gap to Stop (%)"])
    msgs = [a["msg"] for a in pf.alerts(df, None) if a["category"] == "stop"]
    assert any("BBB" in m and "stop data unavailable" in m for m in msgs)


def test_display_stop_uses_held_stop_for_held_names_only():
    assert hv.display_stop(90.0, None) == 90.0
    assert hv.display_stop(90.0, {"Stop": 97.5}) == 97.5
    assert hv.display_stop(90.0, {"Stop": float("nan")}) is None       # held, no stop: never 90
    assert hv.held_row(pd.DataFrame({"Ticker": ["AAA"], "Stop": [1.0]}), "aaa")["Stop"] == 1.0
    assert hv.held_row(pd.DataFrame({"Ticker": ["AAA"]}), "ZZZ") is None
    for needle in ("_held_stop_view.display_stop(r[\"stop\"], ps_row)",
                   "_held_stop_view.display_stop(r[\"stop\"], _sa_holding)",
                   "_held_stop_view.held_row(_sc_port, ticker)"):
        assert needle in _APP


def test_only_moves_up_claims_carry_the_add_qualifier():
    assert "and until you add shares" in _APP
    for bad in ("It can fall only if you sell out", "add shares at a lower average cost"):
        assert bad not in _APP
    assert "the stop a NEW buy (or an add) is sized with" in _APP
    assert "for a position you do NOT hold yet" not in _APP
