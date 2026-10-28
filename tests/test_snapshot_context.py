from datetime import date

import pandas as pd

from stock_analyzer.act_today_view import act_today_view, offline_brief_stub
from stock_analyzer.snapshot_context import (
    HEADER, NO_TRADES, OFFLINE_NOTE, PAUSED_REASON, RESOLVED_CALL,
    brief_calls_lines, brief_calls_view, calls_hash, generate_allowed, section_html,
)


def _fmt(a):
    if a.get("type") == "WATCH":
        return ("c", "Watch", "no action today — see Trigger below for what would escalate.")
    return ("c", "ACT", "Trim 5 shares")


def _view(active=(), aware=(), resolved=(), state=None):
    return {"state": state or ("act" if active else "clear"),
            "active": list(active), "aware": list(aware),
            "resolved": list(resolved), "n_active": len(active)}


def _unesc(html):
    return html.replace("&#x27;", "'").replace("&#39;", "'")


# ── generate_allowed: one per act_view state ─────────────────────────────
def test_generate_allowed_offline():
    ok, why = generate_allowed(_view(state="offline"))
    assert ok is False and why == PAUSED_REASON


def test_generate_allowed_clear_and_act():
    assert generate_allowed(_view()) == (True, "")
    assert generate_allowed(_view(active=[{"ticker": "A"}])) == (True, "")


def test_generate_allowed_bad_input_is_paused():
    assert generate_allowed(None)[0] is False


# ── view: one per state ──────────────────────────────────────────────────
def test_view_offline_has_no_rows_and_section_is_not_an_all_clear():
    v = brief_calls_view(_view(state="offline"), {}, _fmt)
    assert v == {"state": "offline", "rows": []}
    html = _unesc(section_html(v))
    assert OFFLINE_NOTE in html
    assert NO_TRADES not in html
    assert "could not be built" in "\n".join(brief_calls_lines(_view(state="offline"), {}, _fmt))


def test_view_clear_with_aware_watch():
    v = brief_calls_view(_view(aware=[{"ticker": "aapl", "_source": "review",
                                       "action": {"type": "WATCH"},
                                       "trigger": "close below 90", "reason": "within tolerance"}]),
                         {"grow_today": {}}, _fmt)
    r = [x for x in v["rows"] if x["section"] == "aware"][0]
    assert r["subject"] == "AAPL"
    assert r["call"] == "Watch: no action today. Trigger: close below 90"
    assert "see Trigger" not in r["call"]
    assert r["why"] == "within tolerance"
    assert NO_TRADES in _unesc(section_html(v))
    lines = brief_calls_lines(_view(aware=[{"ticker": "aapl"}]), {}, _fmt)
    assert "- none (no trades today)" in lines and lines[0] == HEADER


def test_view_act_directive_dict_and_why_ellipsis():
    v = brief_calls_view(_view(active=[
        {"ticker": "TSLA", "directive": "Reduce or **exit**", "why": "x" * 300},
        {"ticker": "MU", "_source": "review", "headline": "earnings in 5d",
         "action": {"type": "TRIM_TO_TARGET"}}]), {}, _fmt)
    a = [x for x in v["rows"] if x["section"] == "act"]
    assert a[0]["call"] == "Reduce or exit" and a[0]["why"] == "x" * 160 + "…"
    assert a[1]["call"] == "earnings in 5d — ACT: Trim 5 shares"
    short = brief_calls_view(_view(active=[{"ticker": "A", "directive": "d", "why": "y" * 160}]), {}, _fmt)
    assert short["rows"][0]["why"] == "y" * 160  # exactly at the cap: no ellipsis


def test_ticker_none_subjects():
    v = brief_calls_view(_view(aware=[
        {"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "nvda"}},
        {"ticker": None, "event": "CPI", "headline": "CPI in 2d", "action": {"type": "WATCH"}},
        {"ticker": None, "directive": "Raise cash"}]), {}, _fmt)
    subs = [r["subject"] for r in v["rows"] if r["section"] == "aware"]
    assert subs == ["NVDA", "CPI", "portfolio"]


def test_risk_item_uses_flag_recommendation_not_ticker():
    item = {"priority": "high", "icon": "⚠️", "ticker": "NVDA", "kind": "risk",
            "action": "RISK — Sector concentration",
            "risk_flags": [{"title": "Tech 62% of book",
                            "recommendation": "Trim tech by 8pp " + "z" * 400, "problem": "p"}]}
    r = brief_calls_view(_view(active=[item]), {}, _fmt)["rows"][0]
    assert r["subject"] == "portfolio — Tech 62% of book (largest holding NVDA)"
    assert r["call"].startswith("Trim tech by 8pp") and len(r["call"]) > 400


def test_resolved_breach_goes_to_monitoring_never_sell_all():
    v = brief_calls_view(_view(resolved=[{"ticker": "AAA", "directive": "Sell all 10 shares"}]), {}, _fmt)
    r = [x for x in v["rows"] if x["section"] == "aware"][0]
    assert r["call"] == RESOLVED_CALL and "Sell" not in r["call"]


def test_aware_truncation():
    aw = [{"ticker": f"T{i}", "directive": "d"} for i in range(15)]
    out = brief_calls_lines(_view(aware=aw), {}, _fmt)
    assert "- T11: d" in out and "- T12: d" not in out and "- … and 3 more" in out
    assert "… and 3 more" in section_html(brief_calls_view(_view(aware=aw), {}, _fmt))


def test_grow_rows_shares_signal_message_and_none():
    g = {"grow_today": {"new_picks": [{"ticker": "abc", "signal": "Strong Buy",
                                       "sizing": {"shares": 12}}, {"x": 1}],
                        "add_positions": [{"ticker": "xyz"}]}}
    rows = [r for r in brief_calls_view(_view(), g, _fmt)["rows"] if r["section"] == "grow"]
    assert rows[0] == {"section": "grow", "subject": "ABC",
                       "call": "new pick — Strong Buy, 12 shares", "why": ""}
    assert rows[1]["subject"] == "XYZ"
    msg = brief_calls_view(_view(), {"grow_today": {"message": "risk-off **mode**"}}, _fmt)["rows"][-1]
    assert msg["call"] == "risk-off mode"
    assert brief_calls_view(_view(), {}, _fmt)["rows"][-1]["call"] == "no new picks or adds today"


def test_hash_uses_subject_and_call_only():
    a = brief_calls_view(_view(active=[{"ticker": "A", "directive": "d", "why": "one"}]), {}, _fmt)
    b = brief_calls_view(_view(active=[{"ticker": "A", "directive": "d", "why": "two"}]), {}, _fmt)
    c = brief_calls_view(_view(active=[{"ticker": "A", "directive": "e"}]), {}, _fmt)
    assert calls_hash(a) == calls_hash(b) != calls_hash(c)


def test_section_html_escapes():
    v = brief_calls_view(_view(active=[{"ticker": "A", "directive": "<script>x</script> $5 and $6"}]), {}, _fmt)
    html = section_html(v)
    assert "<script>" not in html and "$" not in html


def test_malformed_never_raises():
    def boom(a):
        raise RuntimeError
    for av, db in [(None, None), ("x", 3), ({"state": "act", "active": "bad", "aware": None}, []),
                   (_view(active=[None, 5, {"action": 7}, {"action": {"type": "T"}}]), {"grow_today": []}),
                   (_view(active=[{"ticker": "A", "action": {"type": "T"}}]), {"grow_today": {"new_picks": 4}})]:
        assert isinstance(brief_calls_lines(av, db, boom), list)
        assert isinstance(section_html(brief_calls_view(av, db, boom)), str)
    assert isinstance(brief_calls_lines(_view(), {}, None), list)


# ── integration: real act_today_view over real daily_briefing shapes ─────
def test_integration_real_brief_shapes():
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    from stock_analyzer.daily_briefing import build_daily_briefing
    from tests.conftest import make_port_df

    port_df = make_port_df([
        {"ticker": "AAA", "weight": 30.0, "gap_to_stop": -1.0, "price": 95.0, "stop": 96.0},
        {"ticker": "BBB", "weight": 10.0},
    ])
    risk_recs = [{"priority": "HIGH", "type": "sector_concentration", "title": "Tech over cap",
                  "recommendation": "Trim the weakest tech name by 5pp.", "problem": "p",
                  "root_tickers": [{"ticker": "BBB"}]}]
    brief = build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=risk_recs, news_items=[],
        macro_events=[], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=date(2026, 10, 28), market_context={"tone": "bull"})
    # live recovery: AAA's breach is now well above its stop -> resolved
    pe = pd.DataFrame({"Ticker": ["AAA"], "Gap to Stop (%)": [STOP_RECOVERY_MARGIN_PCT + 5.0],
                       "Stop": [96.0]})
    brief["review_list"] = list(brief.get("review_list") or []) + [{
        "priority": "low", "icon": "🌐", "ticker": None, "event": "CPI",
        "headline": "CPI in 2d (2026-10-30) · affected: BBB", "action": {"type": "WATCH"},
        "why": "within tolerance", "trigger": "Hawkish surprise → stops protect", "weight": None}]
    av = act_today_view(brief, False, pe, {}, STOP_RECOVERY_MARGIN_PCT)
    rows = brief_calls_view(av, brief, _fmt)["rows"]
    risk = [r for r in rows if r["subject"].startswith("portfolio — Tech over cap")]
    assert risk and "Trim the weakest tech name by 5pp." in risk[0]["call"] and "(largest holding BBB)" in risk[0]["subject"]
    assert any(r["call"] == RESOLVED_CALL and r["subject"] == "AAA" for r in rows)
    macro = [r for r in rows if r["subject"] == "CPI"][0]
    assert "CPI in 2d" in macro["call"] and "see Trigger" not in macro["call"]
    assert "Hawkish surprise" in macro["call"]
    assert not any("Sell all" in r["call"] for r in rows)

    off = act_today_view(offline_brief_stub(), False, None, {}, STOP_RECOVERY_MARGIN_PCT)
    assert generate_allowed(off)[0] is False
    assert brief_calls_view(off, offline_brief_stub(), _fmt)["rows"] == []
