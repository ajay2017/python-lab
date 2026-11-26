"""RiskListwiseYoung C4 (RiskOffUnchecked): stock_analyzer/risk_off_check.derisk_unchecked,
its wiring into the Brief / headless engine / premarket lane / Home, and the
risk_window outage wording. Spec: docs/plans/risk-off-unchecked-c4-spec.md.
Clock-free: every input is a frame or a number.
"""
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import risk_off_check
from stock_analyzer.constants import (
    BETA_MIN_RETURNS,
    PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
    RISK_OFF_TREND_MA,
    RISK_OFF_VIX_LEVEL,
)
from stock_analyzer.risk_off_check import derisk_unchecked

pytestmark = pytest.mark.fast

_ROOT = Path(__file__).resolve().parent.parent


def _spy(last_below_average: bool) -> pd.DataFrame:
    """RISK_OFF_TREND_MA bars; the last close sits below (True) or above (False)
    the window's average."""
    closes = [100.0] * (RISK_OFF_TREND_MA - 1) + [50.0 if last_below_average else 150.0]
    return pd.DataFrame({"Close": closes})


_CALM_VIX = RISK_OFF_VIX_LEVEL - 5.0
_HIGH_VIX = RISK_OFF_VIX_LEVEL + 5.0


def _cov(state="unmeasured", unmeasured_pct=15.0, reason="short_history"):
    return {"state": state,
            "left_out": [{"ticker": "ANT", "reason": reason, "n_returns": 3,
                          "weight_pct": unmeasured_pct}],
            "window_returns": 0, "min_returns": BETA_MIN_RETURNS,
            "max_unmeasured_pct": PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
            "unmeasured_pct": unmeasured_pct, "late_start": []}


# ── the decision ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sev", ["ok", "caution", "fragile", None])
def test_a_fragility_dict_of_any_severity_is_checked_so_none(sev):
    assert derisk_unchecked({"severity": sev}, _spy(True), _HIGH_VIX) is None


def test_an_empty_fragility_dict_is_still_a_dict_so_none():
    assert derisk_unchecked({}, _spy(True), _HIGH_VIX) is None


def test_none_fragility_armed_by_the_trend_leg():
    r = derisk_unchecked(None, _spy(True), _CALM_VIX)
    assert r is not None and len(r["reasons"]) == 1
    assert "downtrend" in r["reasons"][0]
    assert r["text"].startswith("Risk-off de-risk check couldn't run: the market is in a risk-off regime (")
    assert r["reasons"][0] in r["text"]
    assert "no de-risk trim was considered today" in r["text"]


def test_none_fragility_armed_by_the_vix_leg():
    r = derisk_unchecked(None, _spy(False), _HIGH_VIX)
    assert r is not None and len(r["reasons"]) == 1 and "VIX" in r["reasons"][0]


def test_none_fragility_both_legs_join_with_semicolon():
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX)
    assert len(r["reasons"]) == 2
    assert "; ".join(r["reasons"]) in r["text"]


def test_regime_not_armed_is_none():
    assert derisk_unchecked(None, _spy(False), _CALM_VIX) is None


def test_vix_exactly_at_the_threshold_arms_it():
    # the regime's own boundary (>=), not re-decided here
    assert derisk_unchecked(None, _spy(False), RISK_OFF_VIX_LEVEL) is not None


def test_missing_regime_inputs_degrade_to_not_armed_so_none():
    assert derisk_unchecked(None, None, None) is None


def test_short_spy_history_and_no_vix_is_none():
    short = pd.DataFrame({"Close": [100.0] * (RISK_OFF_TREND_MA - 1)})
    assert derisk_unchecked(None, short, None) is None


def test_text_is_plain_no_dollar_no_markdown():
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX, coverage=_cov())
    assert "$" not in r["text"] and "**" not in r["text"] and "<" not in r["text"]


# ── coverage clause ───────────────────────────────────────────────────────────
def test_unmeasured_coverage_appends_its_note():
    from stock_analyzer import risk_window
    cov = _cov("unmeasured")
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)
    note = risk_window.display_note(cov)
    assert note and r["text"].endswith(" " + note)
    assert "ANT" in r["text"]


def test_partial_coverage_appends_no_note():
    # "measured without X" beside "beta couldn't be measured" would mislead.
    from stock_analyzer import risk_window
    cov = _cov("partial", unmeasured_pct=4.0)
    assert risk_window.display_note(cov)        # the note exists ...
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)
    assert r["text"].endswith("before adding risk.")   # ... but is not appended
    assert "measured without" not in r["text"]


@pytest.mark.parametrize("cov", [None, {}, "junk", {"state": "full", "left_out": []},
                                 {"left_out": []}])
def test_full_or_absent_coverage_appends_nothing(cov):
    bare = derisk_unchecked(None, _spy(True), _CALM_VIX)
    assert derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=cov)["text"] == bare["text"]
    assert bare["text"].endswith("before adding risk.")


def test_a_raising_display_note_does_not_raise(monkeypatch):
    def _boom(_c):
        raise RuntimeError("x")
    monkeypatch.setattr(risk_off_check.risk_window, "display_note", _boom)
    r = derisk_unchecked(None, _spy(True), _CALM_VIX, coverage=_cov())
    assert r is not None and r["text"].endswith("before adding risk.")


# ── fail visible ──────────────────────────────────────────────────────────────
def test_a_raising_regime_read_returns_the_generic_dict(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("regime exploded")
    monkeypatch.setattr(risk_off_check.exit_advisor, "risk_off_regime", _boom)
    r = derisk_unchecked(None, _spy(True), _HIGH_VIX)
    assert r == {"reasons": [], "text": (
        "Risk-off de-risk check couldn't run: your portfolio beta couldn't be "
        "measured, and the market regime couldn't be read either. "
        "Review your highest-beta holdings yourself before adding risk.")}


def test_a_raising_regime_read_is_moot_when_fragility_is_a_dict(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("never called")
    monkeypatch.setattr(risk_off_check.exit_advisor, "risk_off_regime", _boom)
    assert derisk_unchecked({"severity": "ok"}, _spy(True), _HIGH_VIX) is None


def test_defaults_are_the_constants():
    import inspect
    p = inspect.signature(derisk_unchecked).parameters
    assert p["trend_ma"].default == RISK_OFF_TREND_MA
    assert p["vix_threshold"].default == RISK_OFF_VIX_LEVEL


# ── risk_window outage wording (spec E) ───────────────────────────────────────
def test_all_no_prices_unmeasured_reads_as_an_outage_not_a_limit():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured", reason="no_prices")
    cov["left_out"][0]["n_returns"] = 0
    note = coverage_note(cov)
    assert note.startswith("Portfolio risk can't be measured: no price history could be loaded for ANT")
    assert note.endswith("(market data offline?).")
    assert "limit" not in note and "$" not in note


def test_outage_wording_lists_tickers_only_for_two_and_three_names():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured", reason="no_prices")
    cov["left_out"] = [{"ticker": t, "reason": "no_prices", "n_returns": 0,
                        "weight_pct": 8.0} for t in ("AAA", "BBB")]
    assert coverage_note(cov) == (
        "Portfolio risk can't be measured: no price history could be loaded for "
        "AAA and BBB (market data offline?).")
    cov["left_out"].append({"ticker": "CCC", "reason": "no_prices", "n_returns": 0,
                            "weight_pct": 8.0})
    note = coverage_note(cov)
    assert "for AAA, BBB and CCC (market data offline?)." in note
    assert "%" not in note and "no price history)" not in note


def test_mixed_no_prices_and_short_history_keeps_the_above_cap_text():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("unmeasured")
    cov["left_out"].append({"ticker": "GONE", "reason": "no_prices", "n_returns": 0,
                            "weight_pct": 5.0})
    cov["unmeasured_pct"] = 20.0
    note = coverage_note(cov)
    assert f"more than the {PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}% limit" in note


def test_short_history_above_cap_text_is_unchanged():
    from stock_analyzer.risk_window import coverage_note
    note = coverage_note(_cov("unmeasured"))
    assert note == (
        f"Portfolio risk can't be measured: ANT (15.0% of the book: 3 trading days "
        f"since its first close, {BETA_MIN_RETURNS} needed) leaves out 15.0% of the "
        f"book, more than the {PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}% limit.")


def test_no_prices_left_out_with_a_partial_state_is_not_the_outage_text():
    from stock_analyzer.risk_window import coverage_note
    cov = _cov("partial", unmeasured_pct=4.0, reason="no_prices")
    assert "market data offline" not in coverage_note(cov)


# ── Brief wiring ──────────────────────────────────────────────────────────────
def _brief(**kw):
    from stock_analyzer.daily_briefing import build_daily_briefing
    from tests.conftest import make_port_df
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Tech"}])
    return build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[],
        macro_events=[], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=date(2026, 11, 25),
        market_context={"tone": "bull"}, **kw)


def test_brief_carries_the_disclosure_on_a_risk_off_day_with_no_fragility():
    b = _brief(fragility=None, spy_trend_df=_spy(True), vix_level=_CALM_VIX,
               risk_coverage=_cov())
    assert isinstance(b["risk_off_unchecked"], dict)
    assert "ANT" in b["risk_off_unchecked"]["text"]
    # the de-risk trim itself was never considered: no card of that kind anywhere
    assert all(a.get("kind") != "risk_off_derisk" for a in b["act_today"])
    assert all(a.get("kind") != "risk_off_derisk" for a in b["review_list"])


def test_brief_disclosure_is_none_when_fragility_is_checked_or_regime_calm():
    assert _brief(fragility={"severity": "ok"}, spy_trend_df=_spy(True),
                  vix_level=_HIGH_VIX)["risk_off_unchecked"] is None
    assert _brief(fragility=None, spy_trend_df=_spy(False),
                  vix_level=_CALM_VIX)["risk_off_unchecked"] is None
    assert _brief()["risk_off_unchecked"] is None


def test_brief_without_risk_coverage_still_discloses_without_the_note():
    b = _brief(fragility=None, spy_trend_df=_spy(True), vix_level=_CALM_VIX)
    assert b["risk_off_unchecked"]["text"].endswith("before adding risk.")


# ── source-pinned wiring (app.py has no importable tests) ─────────────────────
_APP = (_ROOT / "app.py").read_text(encoding="utf-8")
_CRON = (_ROOT / "cron_runner.py").read_text(encoding="utf-8")
_HAE = (_ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")


def test_every_app_build_daily_briefing_call_passes_the_risk_coverage():
    calls = [m.start() for m in re.finditer(r"\bbuild_daily_briefing\(", _APP)]
    # the import line has no "(", so every hit is a call
    assert len(calls) == 2
    for pos in calls:
        assert "risk_coverage" in _APP[pos:pos + 4000], \
            "a build_daily_briefing call lacks risk_coverage="


def test_home_renders_the_warning_from_the_brief_key_without_a_constants_comparison():
    i = _APP.index('_ro_unchk = (_daily_brief or {}).get("risk_off_unchecked")')
    block = _APP[i:i + 4000]
    # The header never says "you're set" on an unchecked day ...
    j_label = block.index('''_act_label  = "⚠️ Act Today — de-risk check couldn't run"''')
    j_set = block.index('''_act_label  = "✅ Act Today — you're set"''')
    assert j_label < j_set
    # ... the warning renders as plain text (no raw HTML) ...
    assert 'st.warning("⚠️ " + str(_ro_unchk["text"]))' in block
    # ... and the empty-Act case shows the not-an-all-clear caption before the calm box.
    j_cap = block.index("No trade calls today, but this is not an all-clear")
    j_calm = block.index("Nothing to act on — you're set for today.")
    assert j_cap < j_calm


def test_engine_and_cron_wiring():
    assert "_risk_off_check.derisk_unchecked(" in _HAE
    assert '"risk_off_unchecked": risk_off_unchecked' in _HAE
    assert 'payload.get("risk_off_unchecked")' in _CRON


def test_the_module_is_a_gate_file():
    import sys
    sys.path.insert(0, str(_ROOT / ".claude" / "hooks"))
    import pre_tool_checks as p
    assert "stock_analyzer/risk_off_check.py" in p._GATE_FILES
    # (Its review-policy.md reason line is a doc convention; a test reading that
    # doc would make it a push-suite input, see test_git_hook_adapter.)


# ── fix round: Summary / dock / Home-memo wiring (source pins) ────────────────
def test_summary_replaces_the_green_clear_and_the_zone4_no_protective_calls():
    i = _APP.index('_sm_ro_unchk = (_sm_daily_brief or {}).get("risk_off_unchecked")')
    block = _APP[i:i + 3200]
    assert 'No trade calls today, but this is not an all-clear: ' in block
    assert 'st.warning(' in block
    # the warning branch comes before the green "you're clear" container
    assert block.index("pass   # the warning above replaces") < block.index('key="sm_act_clear"')
    # Zone 4's evidence-based green is guarded by the same flag
    j = _APP.index("# RiskOffUnchecked (C4): the Brief was built but its risk-off")
    assert j < _APP.index("No protective calls</div>")


def test_dock_digest_publish_passes_the_brief_value():
    assert "risk_off_unchecked=risk_off_unchecked," in _APP
    assert re.search(
        r'_publish_home_dock_digest\(\s*_act_view,\s*'
        r'risk_off_unchecked=\(_daily_brief or \{\}\)\.get\("risk_off_unchecked"\),', _APP)


def test_user_guide_names_the_unchecked_header_and_every_surface():
    assert "Act Today — de-risk check couldn't run" in _APP
    assert "🧾 Summary and the dock line say the same instead of an all-clear" in _APP
