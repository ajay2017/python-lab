"""
Tests for stock_analyzer/intelligence_report.py — F-4 Monthly Intelligence
Report: percent/band-line formatting helpers, the data-package builder, prompt
formatting, and response section parsing.
C4b (docs/plans/acted-rule-c4-spec.md): the package builder tests run on the REAL
pipeline (`monthly_entry_calls.build_month` on the shared entry-call rule, then
`recommendations_history.compute_outcomes` / rollups); where a test must see the
rows `compute_outcomes` was given, it wraps `rh.compute_outcomes` and asserts on the
judged call (the investor's view, call 0) or the engine call (every call at its
call-day price, call 1) explicitly.
`generate_report`'s real Anthropic call is exercised via a fake
`sys.modules["anthropic"]` module for a full-header round trip; its guard
clauses return before `import anthropic` runs and need no mocking.
"""
import json
import sys
import types
from datetime import date

import pandas as pd
import pytest

import stock_analyzer.recommendations_history as rh
from stock_analyzer import intelligence_report as ir

pytestmark = pytest.mark.fast


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    type = "text"
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    def create(self, **kwargs):
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text, **kwargs):
        self.messages = _FakeMessages(response_text)


def _install_fake_anthropic(response_text):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── _pct ──────────────────────────────────────────────────────────────────────

def test_pct_none_returns_placeholder():
    assert ir._pct(None) == "—"


def test_pct_nan_returns_placeholder():
    assert ir._pct(float("nan")) == "—"


def test_pct_normal_float_signed_one_decimal():
    assert ir._pct(3.14159) == "+3.1%"
    assert ir._pct(-2.5) == "-2.5%"


def test_pct_non_numeric_returns_placeholder():
    assert ir._pct("not-a-number") == "—"


# ─── _band_line ────────────────────────────────────────────────────────────────

def test_band_line_full_row_joins_with_dot():
    row = {"band": "Strong Buy", "n_total": 5, "action_rate": 80.0, "n_priced": 4, "avg_alpha": 2.5}
    line = ir._band_line(row)
    assert "Strong Buy: 5 rec(s)" in line
    assert "acted 80%" in line
    assert "4 matured" in line
    assert "engine alpha +2.5%" in line
    assert " · " in line


def test_band_line_action_rate_and_avg_alpha_omitted_when_none():
    row = {"band": "Hold zone", "n_total": 2, "action_rate": None, "n_priced": 0, "avg_alpha": None}
    line = ir._band_line(row)
    assert "acted" not in line
    assert "matured" not in line
    assert "engine alpha" not in line


def test_band_line_avg_alpha_omitted_when_n_priced_falsy_even_if_present():
    row = {"band": "Buy", "n_total": 3, "action_rate": 50.0, "n_priced": 0, "avg_alpha": 9.9}
    line = ir._band_line(row)
    assert "matured" not in line
    assert "engine alpha" not in line


# ─── build_report_package ─────────────────────────────────────────────────────
# C4b (docs/plans/acted-rule-c4-spec.md): the package is built on the REAL
# pipeline (monthly_entry_calls.build_month -> compute_outcomes -> rollups), no
# longer a fake around a string "matched". Nov 2026: Thu 11-26 is Thanksgiving,
# Fri 11-27 a half day; N = ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS.

D = date.fromisoformat
NOV_S, NOV_E = D("2026-11-01"), D("2026-11-30")
RUN = D("2026-12-06")
SPY = {D("2026-11-01"): 100.0, D("2026-12-06"): 102.0}      # SPY +2% over the window
_TRADE_COLS = ["id", "ticker", "action", "traded_at", "trigger_type", "notes",
               "broker_txn_id", "shares", "price"]


def _rec(id_, ticker, rec_date, rec_type="new_pick", comp=70, price=100.0,
         already_held=None):
    return {"id": id_, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
            "surfaced_at": f"{rec_date}T14:00:00", "price_at_surface": price,
            "composite_score": comp, "verdict": "Engine-Confirmed",
            "already_held": already_held}


def _recs(*rows):
    return pd.DataFrame(list(rows))


def _trade(id_, ticker, action, traded_at, price=100.0):
    return {"id": id_, "ticker": ticker, "action": action, "traded_at": traded_at,
            "trigger_type": "MANUAL", "notes": "", "broker_txn_id": None,
            "shares": 10, "price": price}


def _trades(*rows):
    return pd.DataFrame(list(rows), columns=_TRADE_COLS)


def _pkg(recs, trades, **kw):
    kw.setdefault("current_prices", {})
    kw.setdefault("spy_close_by_date", SPY)
    kw.setdefault("as_of", RUN)
    return ir.build_report_package(NOV_S, NOV_E, recs, trades, **kw)


def _rich():
    """AAA acted (bought at 90, called at 100), BBB skipped, CCC still open (Mon
    11-30, window ends Wed 12-02), XYZ not judged (a buy with an unreadable date)."""
    recs = _recs(_rec(1, "AAA", "2026-11-03", comp=70), _rec(2, "BBB", "2026-11-03", comp=50),
                 _rec(3, "CCC", "2026-11-30"), _rec(4, "XYZ", "2026-11-09"))
    trades = _trades(_trade(1, "AAA", "BUY", "2026-11-04T15:00:00+00:00", price=90.0),
                     _trade(2, "XYZ", "BUY", "garbage"))
    return _pkg(recs, trades, current_prices={"AAA": 110.0, "BBB": 90.0}, min_graded=2)


def test_build_report_package_none_recs_df_refuses_not_nothing_to_report():
    result = ir.build_report_package(NOV_S, NOV_E, None, pd.DataFrame())
    assert result["has_data"] is False
    assert result["judge_state"] == "recs_unavailable"
    assert result["n_total"] == 0 and result["viz"] is None


def test_build_report_package_empty_recs_df_is_ok_with_no_data():
    result = ir.build_report_package(NOV_S, NOV_E, pd.DataFrame(), pd.DataFrame())
    assert result["has_data"] is False
    assert result["judge_state"] == "ok"
    assert result["n_surfaced"] == 0 and result["action_rate"] is None


def test_build_report_package_trades_none_refuses_and_fills_nothing_else():
    result = _pkg(_recs(_rec(1, "AAA", "2026-11-03")), None)
    assert result["judge_state"] == "trades_unavailable"
    assert result["has_data"] is False
    assert result["viz"] is None and result["n_surfaced"] == 0
    assert result["n_missed"] == 0     # never "every name skipped"
    assert result["band_rows"] == [] and result["verdict_rows"] == []


def test_build_report_package_unreadable_trades_and_recs_refuse():
    r = _pkg(_recs(_rec(1, "AAA", "2026-11-03")), pd.DataFrame({"foo": [1]}))
    assert r["judge_state"] == "trades_unreadable" and r["has_data"] is False
    r = _pkg(pd.DataFrame([{"ticker": "AAA"}]), _trades())
    assert r["judge_state"] == "recs_unreadable" and r["has_data"] is False


def test_build_report_package_has_no_rec_types_parameter_and_scopes_to_entry_calls():
    import inspect
    assert "rec_types" not in inspect.signature(ir.build_report_package).parameters
    assert _pkg(_recs(_rec(1, "AAA", "2026-11-03")), _trades())["rec_scope"] == \
        "new_pick, add_winner, enter_now"


def test_build_report_package_has_data_follows_names_surfaced_not_names_judged():
    # one still-open call: nothing is judged yet, but a name surfaced
    result = _pkg(_recs(_rec(1, "NVDA", "2026-11-27", "enter_now", already_held=False)),
                  _trades())
    assert result["judge_state"] == "ok"
    assert result["has_data"] is True
    assert (result["n_surfaced"], result["n_total"], result["n_open"]) == (1, 0, 1)
    assert result["action_rate"] is None
    assert result["open_names"][0]["ticker"] == "NVDA"
    assert result["open_names"][0]["window_end"] == "2026-12-01"


def test_build_report_package_only_awareness_calls_is_no_data():
    result = _pkg(_recs(_rec(1, "AAA", "2026-11-03", rec_type="buy_candidate"),
                        _rec(2, "BBB", "2026-11-03", rec_type="overflow_pick")), _trades())
    assert result["judge_state"] == "ok" and result["has_data"] is False


def test_build_report_package_fully_populated_pipeline():
    weekly_rows = [{"week_ending": "2026-11-22", "performance_pct": 2.0, "spy_pct": 1.0,
                    "alpha_pct": 1.0, "metrics_version": 2}]
    recs = _recs(_rec(1, "AAA", "2026-11-03", comp=70), _rec(2, "BBB", "2026-11-03", comp=50),
                 _rec(3, "CCC", "2026-11-30"), _rec(4, "XYZ", "2026-11-09"))
    trades = _trades(_trade(1, "AAA", "BUY", "2026-11-04T15:00:00+00:00", price=90.0),
                     _trade(2, "XYZ", "BUY", "garbage"))
    result = _pkg(recs, trades, current_prices={"AAA": 110.0, "BBB": 90.0},
                  weekly_rows=weekly_rows, min_graded=2)
    assert result["judge_state"] == "ok" and result["has_data"] is True
    assert (result["n_surfaced"], result["n_total"], result["n_acted"], result["n_missed"],
            result["n_open"], result["n_unknown"]) == (4, 2, 1, 1, 1, 1)
    assert result["action_rate"] == 50.0                  # acted / (acted + not acted)
    assert result["unknown_names"] == [{
        "ticker": "XYZ", "rec_types": ["new_pick"], "n_calls": 1,
        "last_call_date": "2026-11-09", "reason": "trade_date_unreadable"}]
    assert result["window_trading_days"] == 2
    # the Bought average is the investor's view: priced from the 90 FILL (+22.2%), SPY +2%
    assert result["engine_alpha_pct"] == pytest.approx(20.22, abs=0.01)
    assert result["avg_acted_alpha"] == result["engine_alpha_pct"]
    assert result["q0_ready"] is True                       # 2 graded >= min_graded 2
    assert result["n_graded"] == 2
    assert len(result["weekly_trajectory"]) == 1
    assert result["weekly_trajectory"][0]["week_ending"] == "2026-11-22"


def test_build_report_package_band_rows_are_the_engine_view_at_the_call_day_price():
    result = _rich()
    by = {b["band"]: b for b in result["band_rows"]}
    buy = next(v for k, v in by.items() if k.startswith("Buy"))
    # AAA's band alpha uses its CALL-DAY price (100 -> 110 = +10, SPY +2 = 8.0), never the 90 fill
    assert buy["avg_alpha"] == pytest.approx(8.0, abs=0.01)
    assert buy["n_total"] == 3                  # AAA + CCC (open) + XYZ (not judged): engine view
    assert buy["action_rate"] == 100.0          # judged: AAA only; the open / unknown calls are out
    assert result["viz"]["bands"][0]["avg_alpha"] == pytest.approx(8.0, abs=0.01)
    assert [v["verdict"] for v in result["verdict_rows"]] == ["Engine-Confirmed"]
    assert result["verdict_rows"][0]["n_total"] == 4
    assert result["verdict_rows"][0]["action_rate"] == 50.0


def test_build_report_package_n_graded_counts_the_engine_view_surfacings():
    # one name, surfaced as two types on one day: two engine surfacings, one name
    recs = _recs(_rec(1, "AAA", "2026-11-03", "new_pick"),
                 _rec(2, "AAA", "2026-11-03", "enter_now", already_held=False))
    result = _pkg(recs, _trades(), current_prices={"AAA": 110.0}, min_graded=2)
    assert result["n_surfaced"] == 1 and result["n_total"] == 1
    assert result["n_graded"] == 2 and result["q0_ready"] is True


def test_build_report_package_freezes_skipped_names_avg_alpha_into_viz():
    result = _rich()
    # BBB (skipped): 100 -> 90 = -10 vs SPY +2 = -12.0, over the names the tile counts
    assert result["viz"]["avg_skipped_names_alpha"] == pytest.approx(-12.0, abs=0.01)
    # the package-level per-surfacing average keeps its meaning (it feeds the LLM prompt)
    assert result["avg_missed_alpha"] == pytest.approx(-12.0, abs=0.01)
    assert "avg_missed_alpha" not in result["viz"]
    # JSON-safe: the snapshot is written to a jsonb column
    assert json.loads(json.dumps(result["viz"])) == result["viz"]


def test_build_report_package_freezes_none_skipped_avg_as_present_none():
    recs = _recs(_rec(1, "AAA", "2026-11-03"))
    trades = _trades(_trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00"))
    result = _pkg(recs, trades, current_prices={"AAA": 110.0})
    assert "avg_skipped_names_alpha" in result["viz"]
    assert result["viz"]["avg_skipped_names_alpha"] is None


def test_build_report_package_viz_carries_the_current_rule_version():
    result = _rich()
    assert result["viz"]["acted_rule_version"] == 2
    assert result["viz"]["entry"]["price_basis"] == "call_day"
    assert result["viz"]["as_of"] == "2026-12-06"


def test_build_report_package_q0_ready_boundary():
    recs = _recs(*[_rec(i, f"T{i}", "2026-11-03") for i in range(1, 5)])
    prices = {f"T{i}": 105.0 for i in range(1, 5)}
    assert _pkg(recs, _trades(), current_prices=prices, min_graded=4)["q0_ready"] is True
    r = _pkg(recs, _trades(), current_prices=prices, min_graded=5)
    assert r["n_graded"] == 4 and r["q0_ready"] is False


def test_build_report_package_judges_at_the_period_end_not_as_of():
    # Fri 11-27 call (window ends Tue 12-01), BUY on 12-01, run on 12-06: still open
    recs = _recs(_rec(1, "NVDA", "2026-11-27", "enter_now", already_held=False))
    trades = _trades(_trade(1, "NVDA", "BUY", "2026-12-01T15:00:00+00:00"))
    result = _pkg(recs, trades, as_of=D("2026-12-06"))
    assert (result["n_open"], result["n_acted"], result["n_missed"]) == (1, 0, 0)
    # a different run date gives the same counts
    again = _pkg(recs, trades, as_of=D("2026-12-20"))
    assert (again["n_open"], again["n_acted"], again["n_missed"]) == (1, 0, 0)


def test_build_report_package_as_of_before_period_end_is_a_caller_bug():
    with pytest.raises(ValueError):
        _pkg(_recs(_rec(1, "AAA", "2026-11-03")), _trades(), as_of=D("2026-11-29"))


# ─── judge_state_error ────────────────────────────────────────────────────────

def test_judge_state_error_is_none_only_for_ok():
    assert ir.judge_state_error({"judge_state": "ok"}) is None
    assert ir.judge_state_error({"judge_state": "ok"}, rebuilding=True) is None


@pytest.mark.parametrize("state", ["recs_unavailable", "recs_unreadable",
                                   "trades_unavailable", "trades_unreadable", None, "weird"])
def test_judge_state_error_text_for_every_refusal(state):
    for rebuilding in (False, True):
        text = ir.judge_state_error({"judge_state": state}, rebuilding=rebuilding)
        assert isinstance(text, str) and text


def test_judge_state_error_a_missing_key_reads_as_not_ok():
    assert ir.judge_state_error({}) is not None


def test_judge_state_error_exact_texts():
    assert ir.judge_state_error({"judge_state": "trades_unavailable"}) == (
        "Could not read your trade journal — the database may be unreachable, so no "
        "report was generated. Try again in a moment.")
    assert ir.judge_state_error({"judge_state": "recs_unreadable"}, rebuilding=True) == (
        "Could not read recommendation history, so this report's visuals can't be "
        "rebuilt right now.")
    assert ir.judge_state_error({"judge_state": "trades_unreadable"}, rebuilding=True) == (
        "Could not read your trade journal, so this report's signal-flow visuals can't "
        "be rebuilt right now. Try again in a moment.")


# ─── _format_prompt ────────────────────────────────────────────────────────────

def _base_package(**overrides):
    package = {
        "period_start": "2026-11-01", "period_end": "2026-11-30",
        "n_surfaced": 10, "n_total": 10, "n_acted": 6, "n_missed": 4,
        "n_open": 0, "n_unknown": 0, "open_names": [], "unknown_names": [],
        "action_rate": 60.0,
        "q0_ready": True, "min_graded": 5,
        "avg_acted_pct": 5.0, "avg_missed_pct": -1.0,
        "avg_acted_alpha": 3.0, "avg_missed_alpha": -2.0,
        "missed_alpha": -5.0, "best": None, "worst": None,
        "band_rows": [], "verdict_rows": [], "weekly_trajectory": [],
    }
    package.update(overrides)
    return package


def test_format_prompt_count_discipline_and_field_lines():
    text = ir._format_prompt(_base_package())
    assert "COUNT DISCIPLINE" in text
    assert ("surfaced = 10, acted on = 6, not acted on = 4, still open = 0, "
            "not judged = 0.") in text


def test_format_prompt_counts_paragraph_names_open_and_not_judged():
    text = ir._format_prompt(_base_package(
        n_surfaced=12, n_total=10, n_open=1, n_unknown=1,
        open_names=[{"ticker": "NVDA", "window_end": "2026-12-01"}],
        unknown_names=[{"ticker": "XYZ", "reason": "trade_date_unreadable"}]))
    assert ("Distinct entry-call names surfaced this period: 12. You acted on 6 and did "
            "not act on 4; 1 still open at the period end (decision window runs past "
            "2026-11-30); 1 not judged.") in text
    assert "NEVER state a count of names, recommendations, or outcomes greater than 12" in text
    assert "Action rate = acted ÷ (acted + not acted)" in text
    assert ("Still open at the period end (already listed by the app under Signal "
            "discipline; never call them skipped or acted on): NVDA (window ends "
            "2026-12-01)") in text
    assert ("Not judged (already listed by the app under Signal discipline; never call "
            "them skipped or acted on): XYZ (a buy of this name has a date that couldn't "
            "be read)") in text


def test_format_prompt_open_names_are_never_called_missed_or_skipped():
    text = ir._format_prompt(_base_package(
        n_open=1, open_names=[{"ticker": "NVDA", "window_end": "2026-12-01"}]))
    line = next(ln for ln in text.splitlines() if ln.startswith("Still open at the period end"))
    assert "NVDA" in line and "never call them skipped or acted on" in line
    assert "missed" not in line.replace("never call them skipped or acted on", "")


def test_format_prompt_without_open_or_unknown_names_has_no_such_lines():
    text = ir._format_prompt(_base_package())
    assert "Still open at the period end" not in text.replace("still open at the period end", "")
    assert "Not judged (already listed" not in text


def test_format_prompt_scope_names_the_three_call_types_and_the_exclusion():
    text = ir._format_prompt(_base_package())
    assert "SCOPE:" in text
    for phrase in ("New Positions to Initiate", "Add to Winner", "Enter Now",
                   "already held is left out", "EXCLUDED"):
        assert phrase in text


def test_format_prompt_states_the_price_bases_with_n_from_the_constant(monkeypatch):
    from stock_analyzer import constants
    text = ir._format_prompt(_base_package())
    assert ("Band and verdict engine alpha price every call from its call-day price, "
            "whether or not you bought; the acted averages use your fill price, up to "
            "2 trading days after the call.") in text
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 3)
    assert "up to 3 trading days after the call." in ir._format_prompt(_base_package())


def test_format_prompt_q0_not_ready_shows_note_line_in_surfacings():
    text = ir._format_prompt(_base_package(q0_ready=False))
    assert "too few" in text
    assert "matured surfacings" in text


def test_format_prompt_q0_ready_omits_note_line():
    text = ir._format_prompt(_base_package(q0_ready=True))
    assert "too few" not in text


def test_format_prompt_as_of_line_only_when_it_differs_from_the_period_end():
    assert "Outcomes are measured to" not in ir._format_prompt(_base_package())
    assert "Outcomes are measured to" not in ir._format_prompt(
        _base_package(as_of="2026-11-30"))
    text = ir._format_prompt(_base_package(as_of="2026-12-06"))
    assert "Outcomes are measured to 2026-12-06, not to the period end." in text
    assert "(the run date)" not in text


def test_format_prompt_best_worst_only_when_truthy():
    text = ir._format_prompt(_base_package())
    assert "Best outcome" not in text
    assert "Worst outcome" not in text

    package = _base_package(
        best={"ticker": "AAA", "outcome_pct": 10.0, "alpha_pct": 5.0, "acted_on": True},
        worst={"ticker": "BBB", "outcome_pct": -5.0, "alpha_pct": -3.0, "acted_on": False},
    )
    text2 = ir._format_prompt(package)
    assert "Best outcome: AAA" in text2
    assert "Worst outcome: BBB" in text2


def test_format_prompt_band_verdict_weekly_sections_only_when_nonempty():
    text = ir._format_prompt(_base_package())
    assert "By composite band" not in text
    assert "By cross-check verdict" not in text
    assert "Weekly performance trajectory" not in text

    package = _base_package(
        band_rows=[{"band": "Buy", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0}],
        verdict_rows=[{"verdict": "Confirmed", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0}],
        weekly_trajectory=[{"week_ending": "2026-11-22", "performance_pct": 1.0, "spy_pct": 0.5, "alpha_pct": 0.5}],
    )
    text2 = ir._format_prompt(package)
    assert "By composite band" in text2
    assert "By cross-check verdict" in text2
    assert "Weekly performance trajectory" in text2


def test_format_prompt_reads_a_package_without_the_new_keys():
    # a package built before C4b (no n_surfaced / n_open / ...) still formats: every
    # surfaced name was judged
    old = {"period_start": "2026-01-01", "period_end": "2026-01-31", "n_total": 3,
           "n_acted": 2, "n_missed": 1, "action_rate": 66.7, "q0_ready": True,
           "min_graded": 5, "band_rows": [], "verdict_rows": [], "weekly_trajectory": []}
    text = ir._format_prompt(old)
    assert "surfaced = 3, acted on = 2, not acted on = 1, still open = 0, not judged = 0." in text


# ─── _system_prompt ───────────────────────────────────────────────────────────

def test_system_prompt_defines_acted_on_with_n_from_the_constant(monkeypatch):
    from stock_analyzer import constants
    text = ir._system_prompt()
    assert ('"Acted on" means the investor BOUGHT the name on the call day or within '
            '2 trading days after it (a sell never counts); "not acted on" (skipped) '
            'means no buy in that window.') in text
    assert ('A call whose window runs past the period end is "still open"; a call the '
            'app couldn\'t judge is "not judged". Neither is ever counted as acted on '
            'or skipped, and you must never call them either.') in text
    assert "actually traded the name" not in text
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 3)
    assert "within 3 trading days after it" in ir._system_prompt()
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 1)
    assert "within 1 trading day after it" in ir._system_prompt()


def test_system_prompt_keeps_composite_buy_scoped_to_new_position_calls():
    from stock_analyzer.constants import COMPOSITE_BUY
    text = ir._system_prompt()
    assert f"composite ≥ {COMPOSITE_BUY}" in text
    assert "A \"New Position to Initiate\" call" in text
    assert "New Position, Add to Winner and Enter Now calls" in text


# ─── _parse_response ──────────────────────────────────────────────────────────

def test_parse_response_canonical_headers_split_correctly():
    # Body text deliberately avoids starting with the word "pattern" -- the
    # bare "pattern" alternate-header spelling below would otherwise match a
    # body line starting with that word and wrongly restart a new section.
    text = (
        "**Entry quality**\nEntry text here.\n\n"
        "**Signal discipline**\nSignal text here.\n\n"
        "**Pattern & focus**\nFocus text here."
    )
    sections = ir._parse_response(text)
    assert sections["section_entry_quality"] == "Entry text here."
    assert sections["section_signal_discipline"] == "Signal text here."
    assert sections["section_patterns"] == "Focus text here."


def test_parse_response_alternate_pattern_header_spellings():
    text1 = "**Pattern and focus**\nAlt text 1."
    text2 = "**Pattern**\nAlt text 2."
    assert ir._parse_response(text1)["section_patterns"] == "Alt text 1."
    assert ir._parse_response(text2)["section_patterns"] == "Alt text 2."


def test_parse_response_discards_text_before_first_header():
    text = "Preamble that should be discarded.\n**Entry quality**\nReal text."
    sections = ir._parse_response(text)
    assert "Preamble" not in sections["section_entry_quality"]
    assert sections["section_entry_quality"] == "Real text."


def test_parse_response_missing_section_stays_empty():
    text = "**Entry quality**\nOnly this section."
    sections = ir._parse_response(text)
    assert sections["section_signal_discipline"] == ""
    assert sections["section_patterns"] == ""


# ─── generate_report ───────────────────────────────────────────────────────────

def test_generate_report_no_api_key_returns_none():
    assert ir.generate_report({"has_data": True}, api_key="") is None


def test_generate_report_has_data_false_returns_none():
    assert ir.generate_report({"has_data": False}, api_key="fake-key") is None


def test_generate_report_valid_response_round_trip():
    package = _base_package()
    package["has_data"] = True
    package["viz"] = {"flow": {}}
    package["n_acted"] = 6
    package["n_missed"] = 4
    package["engine_alpha_pct"] = 3.0
    raw = (
        "**Entry quality**\nEntry text.\n\n"
        "**Signal discipline**\nSignal text.\n\n"
        "**Pattern & focus**\nFocus text."
    )
    _install_fake_anthropic(raw)
    result = ir.generate_report(package, api_key="fake-key")
    assert result["section_entry_quality"] == "Entry text."
    assert result["section_signal_discipline"] == "Signal text."
    assert result["section_patterns"] == "Focus text."
    assert result["section_thesis"] is None
    assert result["email_sent"] is False
    assert result["viz_json"] == package["viz"]
    assert result["period_start"] == package["period_start"]
    assert result["period_end"] == package["period_end"]
    assert "generated_at" in result


# ─── classify_recommendations_read ───────────────────────────────────────────
# 2026-08-30: extracted from app.py's "Generate Monthly Report" button so the
# outage-vs-empty-vs-ready decision is unit-tested rather than only reachable
# via a screenshot (app.py has no test coverage of its own). Also closes a
# latent app.py bug: the old inline check was `if _mr_recs is None or
# _mr_recs.empty`, but load_recommendations() never actually returns None,
# so that branch was dead code and a DB outage rendered identically to a
# genuine "nothing recorded yet" quiet period.

def test_classify_recommendations_read_outage_when_read_is_none():
    assert ir.classify_recommendations_read(None) == "outage"


def test_classify_recommendations_read_empty_when_genuinely_zero_rows():
    assert ir.classify_recommendations_read(pd.DataFrame()) == "empty"


def test_classify_recommendations_read_ready_when_rows_present():
    df = pd.DataFrame({"ticker": ["AAPL"], "rec_date": ["2026-08-24"]})
    assert ir.classify_recommendations_read(df) == "ready"


def test_classify_recommendations_read_outage_never_confused_with_empty():
    """The failure mode this function exists to close: None and a genuinely
    empty DataFrame must classify DIFFERENTLY."""
    assert ir.classify_recommendations_read(None) != ir.classify_recommendations_read(pd.DataFrame())


# ─── C7: only current-method weekly rows feed the trajectory ─────────────────

def test_build_report_package_weekly_trajectory_keeps_only_current_method_rows():
    recs = _recs(_rec(1, "AAA", "2026-11-03"))
    weekly_rows = [
        {"week_ending": "2026-11-22", "performance_pct": 2.0, "spy_pct": 1.0, "alpha_pct": 1.0, "metrics_version": 2},
        {"week_ending": "2026-11-15", "performance_pct": 9.0, "spy_pct": 1.0, "alpha_pct": 8.0, "metrics_version": 1},
        {"week_ending": "2026-11-08", "performance_pct": 7.0, "spy_pct": 1.0, "alpha_pct": 6.0},
        {"week_ending": "2026-11-01", "performance_pct": 5.0, "spy_pct": 1.0, "alpha_pct": 4.0,
         "metrics_version": float("nan")},
    ]
    result = _pkg(recs, _trades(), weekly_rows=weekly_rows, min_graded=5)
    assert [w["week_ending"] for w in result["weekly_trajectory"]] == ["2026-11-22"]
    assert result["weekly_excluded_old_method"] == 3


def test_format_prompt_notes_earlier_weeks_on_an_older_method():
    package = _base_package(
        weekly_trajectory=[{"week_ending": "2026-01-25", "performance_pct": 1.0, "spy_pct": 0.5, "alpha_pct": 0.5}],
        weekly_excluded_old_method=3,
    )
    text = ir._format_prompt(package)
    assert "(3 earlier weeks used an older method — not shown)" in text
    clean = ir._format_prompt(_base_package(
        weekly_trajectory=[{"week_ending": "2026-01-25", "performance_pct": 1.0, "spy_pct": 0.5, "alpha_pct": 0.5}]))
    assert "older method" not in clean


def _install_fake_anthropic_no_text_block():
    """A reply with no text block at all (e.g. stop_reason end_turn, empty content)."""
    class _M:
        def create(self, **kwargs):
            return types.SimpleNamespace(content=[], stop_reason="end_turn", usage=None)

    class _C:
        def __init__(self, **kwargs):
            self.messages = _M()

    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = _C
    sys.modules["anthropic"] = fake_mod


def test_generate_report_no_text_block_returns_none_not_an_empty_report():
    # P1-c: an empty reply used to return a report dict with empty sections,
    # which the monthly cron then saved.
    package = _base_package()
    package["has_data"] = True
    _install_fake_anthropic_no_text_block()
    assert ir.generate_report(package, api_key="fake-key") is None


# ─── as_of: outcomes measured to the run date, stock and SPY alike ────────────

def _real_pipeline_package(monkeypatch, *, as_of, period_end=None):
    """The REAL match / compute_outcomes / viz pipeline (only compute_outcomes is
    wrapped, to capture each call) for one new_pick surfaced 2 days before the
    period end, with live prices and a SPY series that runs past the period end.

    Under the shared rule that call's window (N = 2 sessions) runs past the period
    end, so it is "still open": the JUDGED view (call 0) is empty and the ENGINE view
    (call 1, every call at its call-day price) carries the row. The assertions below
    are on the engine call explicitly (spec 4)."""
    import datetime as dt
    captured = {"calls": []}
    real = rh.compute_outcomes

    def _wrap(*a, **k):
        out = real(*a, **k)
        captured["calls"].append({"rows": out, "today": k.get("today")})
        return out

    monkeypatch.setattr(rh, "compute_outcomes", _wrap)
    recs_df = pd.DataFrame([{
        "id": 1, "ticker": "AAA", "rec_date": "2026-01-29", "rec_type": "new_pick",
        "surfaced_at": "2026-01-29T14:00:00+00:00", "price_at_surface": 50.0,
        "composite_score": 70.0, "verdict": "Confirmed",
    }])
    spy = {dt.date(2026, 1, 29): 100.0, dt.date(2026, 1, 30): 110.0,
           dt.date(2026, 2, 10): 120.0}
    pkg = ir.build_report_package(
        dt.date(2026, 1, 1), period_end or dt.date(2026, 1, 31), recs_df, _trades(),
        current_prices={"AAA": 55.0}, spy_close_by_date=spy, as_of=as_of)
    captured["judged"], captured["engine"] = captured["calls"][0], captured["calls"][1]
    return pkg, captured


def test_the_pipeline_runs_a_judged_and_an_engine_call_both_dated_to_as_of(monkeypatch):
    import datetime as dt
    pkg, cap = _real_pipeline_package(monkeypatch, as_of=dt.date(2026, 2, 10))
    assert len(cap["calls"]) == 2
    assert cap["judged"]["rows"] == []                  # still open: not in your view
    assert [r["acted_on"] for r in cap["engine"]["rows"]] == [False]
    assert cap["judged"]["today"] == cap["engine"]["today"] == dt.date(2026, 2, 10)
    assert pkg["n_open"] == 1 and pkg["has_data"] is True


def test_as_of_measures_the_stock_and_spy_to_the_same_date(monkeypatch):
    import datetime as dt
    pkg, cap = _real_pipeline_package(monkeypatch, as_of=dt.date(2026, 2, 10))
    row = cap["engine"]["rows"][0]
    assert cap["engine"]["today"] == dt.date(2026, 2, 10)
    # SPY runs 100 -> 120 to the run date (not 100 -> 110 to the period end), so the
    # stock's live +10% reads as -10.0 alpha against the SAME window
    assert row["spy_return_pct"] == 20.0
    assert row["outcome_pct"] == pytest.approx(10.0)
    assert row["alpha_pct"] == pytest.approx(-10.0)
    assert row["days_since"] == 12
    assert pkg["as_of"] == "2026-02-10" and pkg["viz"]["as_of"] == "2026-02-10"


def test_a_rec_young_at_period_end_but_old_at_as_of_is_not_maturing(monkeypatch):
    import datetime as dt
    _pkg_, cap = _real_pipeline_package(monkeypatch, as_of=dt.date(2026, 2, 10))
    assert cap["engine"]["rows"][0]["outcome_maturing"] is False       # 12 days at as_of
    _pkg_, cap = _real_pipeline_package(monkeypatch, as_of=None)
    assert cap["engine"]["rows"][0]["days_since"] == 2                 # measured to period_end
    assert cap["engine"]["rows"][0]["outcome_maturing"] is True


def test_as_of_defaults_to_the_period_end(monkeypatch):
    import datetime as dt
    pkg, cap = _real_pipeline_package(monkeypatch, as_of=None)
    assert cap["engine"]["today"] == dt.date(2026, 1, 31)
    assert pkg["as_of"] == "2026-01-31"
    assert cap["engine"]["rows"][0]["spy_return_pct"] == 10.0           # 100 -> 110 (Jan 30 close)


# ─── generate_report: the app-written lines at the end of Signal discipline ──

def _gen(package, raw):
    _install_fake_anthropic(raw)
    return ir.generate_report(package, api_key="fake-key")


_RAW = ("**Entry quality**\nEntry text.\n\n"
        "**Signal discipline**\nSignal text.\n\n"
        "**Pattern & focus**\nFocus text.")


def test_generate_report_appends_the_app_lines_after_the_ai_text():
    pkg = _rich()
    result = _gen(pkg, _RAW)
    sd = result["section_signal_discipline"]
    assert sd.startswith("Signal text.\n\n")
    lines = sd.split("\n\n", 1)[1].split("\n")
    assert lines == [
        "Still open at the month's end (not counted as bought or skipped):",
        "• CCC: New Position call on Mon Nov 30. Decision window ends Wed Dec 2.",
        "Not judged (not counted as bought or skipped):",
        "• XYZ: New Position call on Mon Nov 9. Not judged: a buy of this name has a "
        "date that couldn't be read.",
    ]
    # the model's other sections are untouched
    assert result["section_entry_quality"] == "Entry text."
    assert result["section_patterns"] == "Focus text."


def test_generate_report_lines_alone_when_the_ai_section_is_empty():
    pkg = _rich()
    result = _gen(pkg, "**Entry quality**\nEntry text.\n\n**Pattern & focus**\nFocus text.")
    sd = result["section_signal_discipline"]
    assert sd.startswith("Still open at the month's end")
    assert not sd.startswith("\n")


def test_generate_report_adds_nothing_when_there_is_nothing_to_list():
    recs = _recs(_rec(1, "AAA", "2026-11-03"))
    trades = _trades(_trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00"))
    pkg = _pkg(recs, trades, current_prices={"AAA": 110.0})
    assert _gen(pkg, _RAW)["section_signal_discipline"] == "Signal text."


def test_generate_report_never_adds_lines_for_an_older_viz():
    pkg = _base_package()
    pkg["has_data"] = True
    pkg["viz"] = {"flow": {}, "entry": {"open_names": [{"ticker": "NVDA"}]}}
    assert _gen(pkg, _RAW)["section_signal_discipline"] == "Signal text."


def test_generate_report_freezes_the_current_rule_version_in_viz_json():
    result = _gen(_rich(), _RAW)
    assert result["viz_json"]["acted_rule_version"] == 2
    assert result["acted_count"] == 1 and result["missed_count"] == 1
    assert result["engine_alpha_pct"] == pytest.approx(20.22, abs=0.01)
    json.dumps(result["viz_json"])


# ─── the monthly branch wiring (app.py has no tests; grep the source) ────────

def _monthly_branch_source():
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(
        encoding="utf-8")
    a = src.index('elif _rpt_kind == "🧭 Monthly Decision Review (AI)":')
    b = src.index('elif _rpt_kind == "🧾 Tax Report":', a)
    return src[a:b]


def test_the_live_fallback_judges_through_build_month_not_the_same_day_matcher():
    branch = _monthly_branch_source()
    live = branch[branch.index("if _mr_viz is None:"):branch.index("_mr_flow = ")]
    assert "_mr_mec.build_month(" in live
    for banned in ("match_recs_to_trades", "report_viz_snapshot", "compute_outcomes",
                   "load_recommendations(", "rec_types=(\"new_pick\",)"):
        assert banned not in live, banned
    # strict reads, with the visible errors
    assert "load_recommendations_or_none(" in live and "load_trades_or_none()" in live
    assert "judge_state_error(" in live
    # the live fallback never writes into the stored narrative text
    assert "signal_lines" not in live and "section_signal_discipline" not in live


def test_the_generate_button_refuses_on_a_non_ok_judge_state_before_calling_the_llm():
    branch = _monthly_branch_source()
    a = branch.index("_ir.build_report_package(")
    gen = branch[a:branch.index("_rpt_emailed_preview(", a)]
    assert gen.index("judge_state_error(") < gen.index("generate_report(")
    assert "st.error(_mr_refusal)" in gen


def test_the_monthly_branch_has_no_new_policy_comparison_and_no_signal_lines_call():
    branch = _monthly_branch_source()
    assert "signal_lines" not in branch      # only Generate (generate_report) freezes them
    for banned in ("match_recs_to_trades", "report_viz_snapshot"):
        assert banned not in branch, banned
    assert "📊 Recommendations History" not in branch
    assert "📜 Recommendations History" in branch
