"""Signals headline decision (bug register Bal / Reb)."""
import ast
import math
from pathlib import Path

import pytest

from stock_analyzer import held_stop_wiring as hsw
from stock_analyzer import portfolio as pf
from stock_analyzer import signals_headline as sh
from stock_analyzer import stop_breach_text as sbt
from tests._ast_cache import parse_file_cached, source_segment
from tests.test_held_stop_switch import _alert_df

REAL = {"type": "trim", "trim_shares": 3, "ticker": "X"}
ZERO = {"type": "trim", "trim_shares": 0, "ticker": "Y"}


def BREACH(t):
    return {"level": "danger", "category": "stop", "ticker": t, "breached": True,
            "msg": sbt.alert_breach_msg(t, -1.0, 10.0)}


DANGER = {"level": "danger", "category": "earnings", "msg": "m"}
WARN = {"level": "warning", "category": "stop", "msg": "m"}
INFO = {"level": "info", "category": "signal_change", "msg": "m"}
SELL = "Today's Brief says sell at the next open"
RULE = "the stop rule says sell at the next open"


def d(actions, alerts, brief="ok", active=(), resolved=(), offline=False):
    return sh.decide(actions, alerts, offline, brief_state=brief,
                     active_breach=frozenset(active), resolved=frozenset(resolved))


@pytest.mark.parametrize("brief", ["ok", "offline", "not_built"])
def test_offline_wins_over_everything(brief):
    assert d([REAL], [BREACH("A")], brief, offline=True) == {
        "kind": "offline", "text": None, "note": None}


def test_breached_with_brief_active_credits_brief_plural():
    r = d([], [BREACH("XOM"), BREACH("OKTA"), BREACH("XOM"), DANGER], active={"OKTA", "XOM"})
    assert r == {"kind": "act", "note": None,
                 "text": f"⛔ Act first: OKTA, XOM — stops breached; {SELL}."}


def test_breached_singular_brief_active():
    r = d([REAL], [BREACH("OKTA")], active={"okta"})
    assert r["text"] == f"⛔ Act first: OKTA — stop breached; {SELL}."


def test_breached_not_in_brief_active_uses_rule_wording():
    r = d([], [BREACH("OKTA"), BREACH("XOM")], active={"OKTA"})
    assert r["kind"] == "act"
    assert r["text"] == f"⛔ Act first: OKTA, XOM — stops breached; {RULE}."


def test_breached_brief_offline_and_not_built():
    off = d([], [BREACH("OKTA")], "offline")
    assert off["text"] == (f"⛔ Act first: OKTA — stop breached; {RULE}. "
                           "Today's Brief couldn't be built this run — open 🏠 Home.")
    nb = d([], [BREACH("OKTA")], "not_built", active={"OKTA"})
    assert nb["text"] == (f"⛔ Act first: OKTA — stop breached; {RULE}. "
                          "Open 🏠 Home to build today's Brief.")
    assert SELL not in off["text"] + nb["text"]


def test_breached_without_ticker_still_triggers():
    r = d([], [{"level": "danger", "breached": True}])
    assert r["kind"] == "act" and "Act first: —" in r["text"] and RULE in r["text"]


def test_resolved_only_is_never_clear():
    r = d([], [BREACH("OKTA")], resolved={"OKTA"})
    assert r["kind"] == "resolved" and r["text"] is None
    assert r["note"] == ("ℹ️ OKTA: stop breach resolved — live price recovered above "
                         "the stop; see 🏠 Home.")


def test_mixed_act_and_resolved():
    r = d([], [BREACH("OKTA"), BREACH("XOM")], active={"XOM"}, resolved={"OKTA"})
    assert r["kind"] == "act" and r["text"] == f"⛔ Act first: XOM — stop breached; {SELL}."
    assert "OKTA: stop breach resolved" in r["note"]


def test_resolved_ignored_when_brief_not_ok():
    r = d([], [BREACH("OKTA")], "offline", resolved={"OKTA"})
    assert r["kind"] == "act" and r["note"] is None


def test_resolved_note_under_attention_and_none():
    r = d([], [BREACH("OKTA"), DANGER], resolved={"OKTA"})
    assert r["kind"] == "attention" and r["note"]
    r = d([REAL], [BREACH("OKTA")], resolved={"OKTA"})
    assert r["kind"] == "none" and r["note"]


@pytest.mark.parametrize("brief", ["ok", "offline", "not_built"])
def test_attention_wording_is_neutral_for_every_brief_state(brief):
    assert d([], [DANGER], brief)["text"] == "🔴 1 danger alert below — review it."
    assert d([], [DANGER, DANGER], brief)["text"] == "🔴 2 danger alerts below — review each."


def test_clear_only_without_warnings():
    r = d([], [INFO])
    assert r == {"kind": "clear", "note": None, "text": sh.CLEAR_TEXT}
    assert sh.CLEAR_TEXT == "✅ Portfolio is well-balanced — no signal-driven actions needed at this time."
    assert d([], [WARN])["text"] == "✅ No signal-driven actions needed — 1 warning below to monitor."
    assert d([ZERO], [WARN, WARN])["text"] == \
        "✅ No signal-driven actions needed — 2 warnings below to monitor."


def test_zero_share_trim_alone_is_clear_and_real_action_is_none():
    assert d([ZERO], [])["kind"] == "clear"
    assert d([ZERO, REAL], [WARN])["kind"] == "none"
    assert d([{"type": "add"}], [])["kind"] == "none"


# ── alert_line ───────────────────────────────────────────────────────────────
def test_alert_line_variants():
    a = BREACH("OKTA")
    assert sh.alert_line(a, brief_state="ok", resolved=frozenset(), active_breach={"OKTA"}) == a["msg"]
    assert SELL in a["msg"]
    g = sh.alert_line(a, brief_state="ok", resolved={"OKTA"})
    assert g == ("🟢 **OKTA** breached its stop earlier, but the live price has "
                 "recovered above it — see 🏠 Home.")
    for st_ in ("offline", "not_built"):
        o = sh.alert_line(a, brief_state=st_, resolved={"OKTA"})
        assert SELL not in o and f"{RULE} (Today's Brief isn't available this run)" in o
    # ok, but the Brief has no stop_breach card for this name (locked snapshot)
    k = sh.alert_line(a, brief_state="ok", resolved=frozenset(), active_breach={"XOM"})
    assert SELL not in k and RULE in k and "isn't available" not in k
    assert sh.alert_line(a, brief_state="ok", active_breach={"okta"}) == a["msg"]
    assert sh.alert_line(WARN, brief_state="offline") == "m"
    assert sh.alert_line(DANGER, brief_state="offline") == "m"


def test_split_alert_levels_and_effective_level():
    b = BREACH("OKTA")
    lv = [b, BREACH("XOM"), DANGER, WARN, INFO]
    dg, wr, inf = sh.split_alert_levels(lv, brief_state="ok", resolved={"OKTA"})
    assert dg == [lv[1], DANGER] and wr == [WARN] and inf == [b, INFO]
    dg, wr, inf = sh.split_alert_levels(lv, brief_state="offline", resolved={"OKTA"})
    assert b in dg and len(dg) == 3
    assert sh.effective_level(b, brief_state="ok", resolved={"OKTA"}) == "info"
    assert sh.effective_level(b, brief_state="ok") == "danger"
    assert sh.split_alert_levels(None, brief_state="ok") == ([], [], [])


def test_brief_context():
    view = {"state": "act",
            "active": [{"ticker": " xom ", "kind": "stop_breach"}, {"ticker": "A", "kind": "x"}],
            "resolved": [{"ticker": "okta"}]}
    assert sh.brief_context({"b": 1}, False, view) == ("ok", frozenset({"XOM"}), frozenset({"OKTA"}))
    assert sh.brief_context(None, False, view)[0] == "not_built"
    assert sh.brief_context({"b": 1}, True, view)[0] == "offline"
    assert sh.brief_context({"b": 1}, False, {"state": "offline"})[0] == "offline"


# ── portfolio.alerts keys + rule agreement ───────────────────────────────────
def test_get_alerts_marks_breach_only_at_gap_zero_or_below():
    df, _ = _alert_df(0.0)
    out = [a for a in pf.alerts(df, None) if a["category"] == "stop"]
    assert out[0]["breached"] is True and out[0]["ticker"] == "AAA"
    df, _ = _alert_df(0.1)
    out = [a for a in pf.alerts(df, None) if a["category"] == "stop"]
    assert not out[0].get("breached") and out[0]["ticker"] == "AAA"


@pytest.mark.parametrize("gap", [-0.1, 0.0, 0.04, 0.06, float("nan")])
def test_alerts_breached_matches_brief_breached_set(gap):
    # daily_briefing's stop_breach list needs a full Brief build (news, macro,
    # deterioration inputs); impractical here, so pin against the shared rule
    # helper held_stop_wiring.brief_breached_set (act_today_precedence).
    df, _ = _alert_df(0.0 if math.isnan(gap) else gap)
    if math.isnan(gap):
        df["Gap to Stop (%)"] = float("nan")
    marked = {a["ticker"] for a in pf.alerts(df, None) if a.get("breached")}
    assert marked == hsw.brief_breached_set(df)


# ── app wiring ───────────────────────────────────────────────────────────────
def test_app_signals_page_wiring():
    path = Path(__file__).resolve().parent.parent / "app.py"
    src = path.read_text(encoding="utf-8")
    tree = parse_file_cached(path)

    def is_sh(n, name):
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == name and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "signals_headline")
    decides = [n for n in ast.walk(tree) if is_sh(n, "decide")]
    assert len(decides) == 1
    assert [ast.unparse(a) for a in decides[0].args][:2] == ["actions", "alert_list"]
    assert {k.arg for k in decides[0].keywords} >= {"brief_state", "active_breach", "resolved"}
    lines = [n for n in ast.walk(tree) if is_sh(n, "alert_line")]
    assert lines and {k.arg for k in lines[0].keywords} >= {"brief_state", "resolved", "active_breach"}
    assert any(is_sh(n, "split_alert_levels") for n in ast.walk(tree))
    for kind, fn in (("act", "error"), ("attention", "warning"), ("clear", "success")):
        hit = [n for n in ast.walk(tree) if isinstance(n, ast.If)
               and f"['kind'] == '{kind}'" in ast.unparse(n.test)
               and f"st.{fn}(" in ast.unparse(n.body[0])]
        assert hit, kind
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) \
                and isinstance(n.test.op, ast.Not) and isinstance(n.test.operand, ast.Name) \
                and n.test.operand.id == "actions":
            assert "well-balanced" not in source_segment(src, n)
