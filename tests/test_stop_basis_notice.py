"""SlidingStop C4: the "Stop basis" notice (docs/plans/sliding-stop-c4-spec.md).

The pure module (`stop_basis_notice`: rows / drop_rows / overall_tier / home_markdown /
email_lines / lane_failure), the two additive wiring fields (`episode_reason`,
`carry_row_seen`), and the render-only wiring pins (app.py Home, cron_runner premarket,
home_notices). No network, no DB, no app import (app.py is read as AST / text)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import constants as C
from stock_analyzer import held_stop_carry as hc
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import home_notices as hn
from stock_analyzer import stop_basis_notice as sbn
from tests._ast_cache import parse_file_cached
from tests.test_held_stop_carry import AVG
from tests.test_held_stop_carry_wiring import (
    HOLD, MISMATCH_JOURNAL, _scenarios, build, falling, mrow)
from tests.test_held_stop_floor import Scenario, T
from tests.test_held_stop_lanes import GO_LIVE_DAY, _run_pm

_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _ROOT / "app.py"
_CRON_PATH = _ROOT / "cron_runner.py"


@pytest.fixture(autouse=True)
def early_trail(monkeypatch):
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2025-01-01")


@pytest.fixture(scope="module")
def s():
    return Scenario()


MISMATCH_REASON = "journal nets 5 shares but holdings show 10"


def res(basis="prior_session", status="mismatch", state=hc.NONE, reason=MISMATCH_REASON,
        value=None, set_on=None, **extra):
    r = {"basis": basis, "episode_status": status, "episode_reason": reason,
         "carry": {"state": state, "value": value, "set_on": set_on}}
    r.update(extra)
    return r


# ── rows ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state,tier", [
    (hc.APPLIED, "warning"), (hc.NOT_BINDING, "warning"),
    (hc.NONE, "error"), (hc.LAPSED, "error"), (hc.UNREADABLE, "error"), (hc.FAILED, "error"),
])
def test_rows_tier_follows_the_carry_state(state, tier):
    got = sbn.rows({"AAA": res(state=state, value=101.5, set_on="2026-11-20")})
    assert len(got) == 1 and got[0]["tier"] == tier and got[0]["carried"] is (tier == "warning")
    assert got[0]["ticker"] == "AAA" and got[0]["status"] == "mismatch"
    assert got[0]["reason"] == MISMATCH_REASON
    if state in (hc.APPLIED, hc.NOT_BINDING):
        assert got[0]["carried_value"] == 101.5 and got[0]["carried_on"] == "2026-11-20"
    else:
        assert got[0]["carried_value"] is None and got[0]["carried_on"] is None


def test_rows_skips_everything_notice_tier_skips_and_sorts_by_ticker():
    held = {
        "ZZZ": res(),
        "AAA": res(status="no_history", reason="no trade history for this ticker"),
        "YNG": res(status="ok", reason=""),                      # the young-listing case: F-320's
        "FUL": res(basis="full"), "PAR": res(basis="partial"),
        "BAD": "not a dict",
    }
    assert [r["ticker"] for r in sbn.rows(held)] == ["AAA", "ZZZ"]


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_non_dict_input_is_empty_everywhere(bad):
    assert sbn.rows(bad) == [] and sbn.drop_rows(bad) == []


# ── drop_rows (U3) ───────────────────────────────────────────────────────────
def full(res_stop=90.0, **seen):
    row = {"stop": 95.0, "source_basis": "prior_session", "as_of": "2026-11-25",
           "stop_set_on": "2026-11-20"}
    row.update(seen)
    return {"basis": "full", "stop": res_stop, "carry_row_seen": row}


def test_drop_rows_full_below_a_carried_row():
    got = sbn.drop_rows({"AAA": full(90.0), "BBB": full(90.0, source_basis="partial",
                                                       stop_set_on=None)})
    assert got == [
        {"ticker": "AAA", "stop": 90.0, "carried_value": 95.0, "carried_on": "2026-11-20"},
        {"ticker": "BBB", "stop": 90.0, "carried_value": 95.0, "carried_on": "2026-11-25"},
    ]


def test_drop_rows_none_for_a_full_row_equal_at_the_cent_higher_stop_or_missing_row():
    assert sbn.drop_rows({"A": full(90.0, source_basis="full")}) == []      # a full day's own row
    assert sbn.drop_rows({"A": full(95.0, stop=95.004)}) == []             # equal at the cent
    assert sbn.drop_rows({"A": full(96.0)}) == []                             # full stop is higher
    assert sbn.drop_rows({"A": {"basis": "full", "stop": 90.0}}) == []        # no carry_row_seen
    assert sbn.drop_rows({"A": {**full(90.0), "basis": "partial"}}) == []     # not a full result
    assert sbn.drop_rows({"A": full(float("nan"))}) == []
    assert sbn.drop_rows({"A": full(90.0, stop=None)}) == []


# ── overall_tier ─────────────────────────────────────────────────────────────
def test_overall_tier_precedence():
    err, warn = {"tier": "error"}, {"tier": "warning"}
    drop = [{"ticker": "C"}]
    assert sbn.overall_tier([warn, err], drop) == "error"
    assert sbn.overall_tier([warn], drop) == "warning"
    assert sbn.overall_tier([], drop) == "info"
    assert sbn.overall_tier([], []) is None and sbn.overall_tier(None, None) is None


# ── home_markdown ────────────────────────────────────────────────────────────
def _md(held):
    return sbn.home_markdown(sbn.rows(held), sbn.drop_rows(held))


def test_nothing_to_say_is_none():
    assert _md({}) is None and _md({"A": res(basis="full")}) is None


def test_error_header_cause_and_fix_line():
    mult = max(float(C.ATR_STOP_MULT), float(C.HELD_TRAIL_ATR_MULT))
    md = _md({"AAA": res()})
    lines = md.split("\n")
    assert lines[0] == "⚠️ **Stop basis: the stop for AAA isn't protected against a slow fall.**"
    assert (f"- **AAA**: {MISMATCH_REASON}; its stop is the last close minus {mult:g} x ATR and "
            "isn't carried, so it moves down as the price falls and a slow decline won't "
            "trigger it.") in lines
    assert lines[-1] == ("Fix the journal in 📒 Trade Journal → ⚠️ Fix drift between trades and "
                         "holdings; the full stop returns once the shares agree.")


def test_warning_header_and_carried_bullet_escape_the_dollar_once():
    md = _md({"AAA": res(state=hc.APPLIED, value=101.5, set_on="2026-11-20")})
    assert md.split("\n")[0] == ("⚠️ **Stop basis: AAA is on a carried stop until the trade "
                                 "journal is fixed.**")
    assert ("its stop is held at \\$101.50, carried from 2026-11-20, while your trades in this "
            "position don't change.") in md


def test_not_binding_never_claims_the_stop_is_held_at_the_saved_value():
    md = _md({"AAA": res(state=hc.NOT_BINDING, value=90.0, set_on="2026-11-20")})
    assert "held at" not in md
    assert "a carried stop of \\$90.00 from 2026-11-20 backs its stop" in md


def test_cause_per_status():
    held = {"A": res(), "B": res(status="no_history", reason="no trade history for this ticker"),
            "C": res(status="unreadable", reason="trade journal could not be read")}
    md = _md(held)
    assert f"- **A**: {MISMATCH_REASON}; " in md
    assert "- **B**: no trade history for this ticker; " in md
    assert "- **C**: the trade journal couldn't be read; " in md


def test_unreadable_only_closing_line_replaces_the_fix_line():
    md = _md({"C": res(status="unreadable", reason="trade journal could not be read")})
    assert md.endswith("This usually clears on the next refresh; if it doesn't, see 🩺 System Trust.")
    assert "Fix the journal" not in md
    assert "Fix the journal in" in _md({"C": res(status="unreadable"), "A": res()})


@pytest.mark.parametrize("names,joined", [(["A"], "A"), (["A", "B"], "A and B"),
                                          (["A", "B", "C"], "A, B and C")])
def test_error_header_name_joins(names, joined):
    md = _md({n: res() for n in names})
    assert md.split("\n")[0] == f"⚠️ **Stop basis: the stop for {joined} isn't protected against a slow fall.**"


def test_error_header_names_only_error_rows_and_keeps_the_warning_bullet():
    md = _md({"A": res(), "B": res(state=hc.APPLIED, value=50.0, set_on="2026-11-20")})
    assert md.split("\n")[0].startswith("⚠️ **Stop basis: the stop for A isn't")
    assert "- **B**:" in md


def test_drops_alone_are_info_with_their_own_header_and_no_closing_line():
    md = _md({"C": full(90.0)})
    lines = md.split("\n")
    assert lines[0] == "ℹ️ **Stop basis: a stop moved back onto your full trade history.**"
    assert lines[-1] == ("- **C**: its stop is back on your full trade history at \\$90.00, below "
                         "the carried \\$95.00 from 2026-11-20; the full-history figure is lower, "
                         "so the carried stop no longer applies.")
    assert "Fix the journal" not in md


def test_every_dollar_is_escaped_and_no_stray_bold():
    held = {"A": res(state=hc.APPLIED, value=101.5, set_on="2026-11-20"), "B": res(),
            "C": full(90.0), "D": res(state=hc.NOT_BINDING, value=70.25, set_on="2026-11-19")}
    md = _md(held)
    assert md.count("$") == md.count("\\$") > 0
    assert not re.search(r"(?<!\\)\$", md)
    # bold appears only in the header and as "**TICKER**"
    for line in md.split("\n")[1:]:
        assert line.count("**") in (0, 2)
        if line.count("**") == 2:
            assert re.match(r"- \*\*[A-Z]+\*\*: ", line)
    assert md.split("\n")[0].count("**") == 2


# ── email_lines ──────────────────────────────────────────────────────────────
def test_email_lines_plain_text_and_one_fix_line_for_several_names():
    held = {"A": res(), "B": res(status="no_history", reason="no trade history for this ticker"),
            "C": res(state=hc.APPLIED, value=101.5, set_on="2026-11-20"), "D": full(90.0)}
    lines = sbn.email_lines(sbn.rows(held), sbn.drop_rows(held))
    fix = [ln for ln in lines if ln.startswith("Stop basis: fix the trade journal for")]
    assert fix == [("Stop basis: fix the trade journal for A, B, C (📒 Trade Journal → ⚠️ Fix "
                    "drift between trades and holdings); until then their stops are not on the "
                    "full history.")]
    assert "A: stop is not carried; a slow decline won't trigger it." in lines
    assert "B: stop is not carried; a slow decline won't trigger it." in lines
    assert not any(ln.startswith("C: stop is not carried") for ln in lines)   # warning: fix line only
    assert ("D: its stop is back on your full trade history at $90.00, below the carried "
            "$95.00 from 2026-11-20; the full-history figure is lower, so the carried stop "
            "no longer applies.") in lines
    for ln in lines:
        assert "\\" not in ln and "**" not in ln


def test_email_single_name_wording_and_unreadable_only_has_no_fix_line():
    one = sbn.email_lines(sbn.rows({"A": res()}), [])
    assert one[0].endswith("until then its stop is not on the full history.")
    assert sbn.email_lines(sbn.rows({"C": res(status="unreadable")}), []) == [
        "C: stop is not carried; a slow decline won't trigger it."]
    assert sbn.email_lines([], []) == []


# ── lane_failure ─────────────────────────────────────────────────────────────
def test_lane_failure_none_with_warning_only_rows_and_text_with_error_rows():
    assert sbn.lane_failure([]) is None and sbn.lane_failure(None) is None
    assert sbn.lane_failure(sbn.rows({"A": res(state=hc.APPLIED, value=1.0, set_on="2026-11-20")})) is None
    one = sbn.lane_failure(sbn.rows({"A": res()}))
    assert one == (f"1 holding(s) on an uncarried prior-close stop because of a trade-journal fault "
                   f"(A: {MISMATCH_REASON}); a slow decline on it won't trigger a stop")
    two = sbn.lane_failure(sbn.rows({"A": res(), "B": res(status="unreadable")}))
    assert two.startswith("2 holding(s)") and "B: the trade journal couldn't be read" in two
    assert two.endswith("a slow decline on them won't trigger a stop")


# ── wiring: episode_reason, carry_row_seen ───────────────────────────────────
def test_episode_reason_is_on_every_path(s, monkeypatch):
    days, _, fall = falling()
    sc = _scenarios(s)
    assert sc["mismatch"]({})["episode_reason"] == MISMATCH_REASON
    assert sc["prior_session"]({})["episode_reason"] == "trade journal could not be read"
    for name in ("full", "partial", "buy_day", "unavailable"):
        assert sc[name]({})["episode_reason"] == ""                    # an ok episode's reason
    no_hist = pd.DataFrame([{"id": 1, "ticker": "ZZZ", "action": "BUY", "shares": 5.0,
                             "price": AVG, "traded_at": "2026-01-02T15:00:00+00:00"}])
    assert build(fall, days[60], {}, trades=no_hist)["episode_reason"] == "no trade history for this ticker"
    gone = build(fall, days[60], {}, fetch=lambda t, p: 1 / 0)
    assert gone["basis"] == "unavailable" and gone["episode_reason"] == MISMATCH_REASON
    assert hw.unavailable_result(T, "x")["episode_reason"] is None
    monkeypatch.setattr(hw, "_one", lambda *a, **k: 1 / 0)
    boom = hw.build_held_stops(HOLD, {}, MISMATCH_JOURNAL, days[60], fetch_history=lambda t, p: fall,
                               floors={}, carry={})[T]
    assert boom["episode_reason"] is None


def test_carry_row_seen_only_when_a_row_exists_and_never_changes_the_stop(s):
    days, _, fall = falling()
    i = 60
    assert "carry_row_seen" not in build(fall, days[i], {})
    assert "carry_row_seen" not in build(fall, days[i], None)
    assert "carry_row_seen" not in build(fall, days[i], {"OTHER": {"stop": 1.0}})
    raw = build(fall, days[i], {})
    row = mrow(fall, days, i, round(raw["stop"] + 5.0, 2))
    eff = build(fall, days[i], {T: row})
    assert eff["carry_row_seen"] == {"stop": row["stop"], "source_basis": row.get("source_basis"),
                                     "as_of": row.get("as_of"), "stop_set_on": row.get("stop_set_on")}
    assert eff["stop"] == row["stop"]                                  # the carry still decides it
    # a lapsed row is still "seen", and today's stop is untouched
    lapsed = build(fall, days[i], {T: mrow(fall, days, i, raw["stop"] + 5.0, avg_cost=AVG + 0.01)})
    assert lapsed["carry"]["state"] == hc.LAPSED and lapsed["stop"] == raw["stop"]
    assert lapsed["carry_row_seen"]["stop"] == pytest.approx(raw["stop"] + 5.0)
    # the failed shape keeps it too
    from unittest.mock import patch
    with patch.object(hw.hsc, "journal_buy_sig", side_effect=ValueError("boom")):
        failed = build(fall, days[i], {T: row})
    assert failed["carry"]["state"] == hc.FAILED and failed["carry_row_seen"]["stop"] == row["stop"]


def test_carry_row_seen_rides_a_full_result_and_never_moves_its_stop(s):
    run = _scenarios(s)["full"]
    plain = run({})
    seen = {"stop": 9999.0, "source_basis": "prior_session", "as_of": s.days[117].isoformat(),
            "stop_set_on": s.days[110].isoformat()}
    got = run({T: dict(seen)})
    assert got["basis"] == "full" and got["carry"]["state"] == hc.NA
    assert got["stop"] == plain["stop"] and got["carry_row_seen"] == seen
    # ... and that is exactly what the U3 note reads
    drops = sbn.drop_rows({T: got})
    assert drops == [{"ticker": T, "stop": plain["stop"], "carried_value": 9999.0,
                      "carried_on": s.days[110].isoformat()}]
    assert "carry_row_seen" not in plain


def test_a_buy_day_result_has_no_carry_row_seen(s):
    assert "carry_row_seen" not in _scenarios(s)["buy_day"]({T: {"stop": 9999.0}})


def test_end_to_end_a_real_mismatch_result_reads_error_then_warning():
    days, _, fall = falling()
    i = 60
    raw = build(fall, days[i], {})
    err = sbn.rows({T: raw})
    assert err and err[0]["tier"] == "error" and err[0]["reason"] == MISMATCH_REASON
    eff = build(fall, days[i], {T: mrow(fall, days, i, round(raw["stop"] + 5.0, 2))})
    warn = sbn.rows({T: eff})
    assert warn and warn[0]["tier"] == "warning" and warn[0]["carried_value"] == eff["stop"]


# ── app.py / cron_runner / home_notices pins ─────────────────────────────────
def test_fix_pointer_is_the_real_page_and_expander_title_in_app_py():
    src = _APP_PATH.read_text(encoding="utf-8")
    page, expander = [p.strip() for p in sbn.FIX_POINTER.split("→")]
    assert f'"{page}"' in src                                             # the nav page name
    tree = parse_file_cached(_APP_PATH)
    titles = [n.args[0].value for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "expander" and n.args and isinstance(n.args[0], ast.Constant)]
    assert expander in titles


def _is_assign_to(node, name):
    return (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))


def test_app_py_declares_and_fills_the_stopbasis_placeholder():
    tree = parse_file_cached(_APP_PATH)
    decl = [n for n in ast.walk(tree) if _is_assign_to(n, "_alert_ph_stopbasis")]
    assert len(decl) == 1
    # right after _alert_ph_journalmatch in the dock's Notices tab
    parent = next(n for n in ast.walk(tree) if hasattr(n, "body") and isinstance(n.body, list)
                  and decl[0] in n.body)
    i = parent.body.index(decl[0])
    assert _is_assign_to(parent.body[i - 1], "_alert_ph_journalmatch")
    withs = [n for n in ast.walk(tree) if isinstance(n, ast.With) and any(
        isinstance(it.context_expr, ast.Call) and isinstance(it.context_expr.func, ast.Attribute)
        and isinstance(it.context_expr.func.value, ast.Name)
        and it.context_expr.func.value.id == "_alert_ph_stopbasis" for it in n.items)]
    assert len(withs) == 1
    notice_calls = [c for c in ast.walk(withs[0]) if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name) and c.func.id == "_home_notice"
                    and c.args and isinstance(c.args[0], ast.Constant)
                    and c.args[0].value == "stopbasis"]
    assert len(notice_calls) == 1
    # the fill sits BEFORE Home's _render_held_stop_notices call, inside a try
    src = _APP_PATH.read_text(encoding="utf-8")
    assert src.index("with _alert_ph_stopbasis.container():") < src.index(
        "_render_held_stop_notices(_held_stops,")
    # never a comparison against a constant, never Act Today
    assert "_sb_rows = stop_basis_notice.rows(_held_stops)" in src


def test_cron_premarket_calls_stop_basis_lane_failure():
    tree = parse_file_cached(_CRON_PATH)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_premarket")
    called = {c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call)
              and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
              and c.func.value.id == "_stop_basis_notice"}
    assert {"rows", "drop_rows", "lane_failure", "email_lines"} <= called
    for other in ("_run_eod", "_run_intraday"):
        f = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == other), None)
        if f is not None:
            assert "_stop_basis_notice" not in ast.unparse(f)


def test_premarket_uncarried_name_goes_red_and_the_footer_gets_the_lines(monkeypatch):
    payload = {"alerts": [{"kind": "stop_breach", "ticker": "AAPL"}], "built_at": "x", "errors": [],
               "held_stops_failed": None, "held_stop_notes": ["AAPL (Prior-close ATR): note"],
               "held_stop_floors_readable": True, "held_stop_carry_readable": True,
               "held_stops": {"AAPL": res(state=hc.NONE)}}
    rc, sent, saved, renders, notified = _run_pm(monkeypatch, payload, GO_LIVE_DAY)
    assert rc == 1 and len(sent) == 1 and len(saved) == 1            # red, but alerts still sent
    assert "held_stop_basis: 1 holding(s) on an uncarried prior-close stop" in cr._LAST_LANE_FAILURE_DETAIL
    lines = renders[0]["footer_lines"]
    i = lines.index("AAPL (Prior-close ATR): note")                   # per-name notes first
    assert lines[i + 1].startswith("Stop basis: fix the trade journal for AAPL")
    assert lines[i + 2] == "AAPL: stop is not carried; a slow decline won't trigger it."


def test_premarket_carried_name_stays_green_with_only_the_footer_line(monkeypatch):
    payload = {"alerts": [{"kind": "stop_breach", "ticker": "AAPL"}], "built_at": "x", "errors": [],
               "held_stops_failed": None, "held_stop_notes": [],
               "held_stop_floors_readable": True, "held_stop_carry_readable": True,
               "held_stops": {"AAPL": res(state=hc.APPLIED, value=100.0, set_on="2026-11-20")}}
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, payload, __import__("datetime").datetime(2026, 12, 1, 8, 30))
    assert rc == 0 and len(sent) == 1
    assert renders[0]["footer_lines"] == [
        "Stop basis: fix the trade journal for AAPL (📒 Trade Journal → ⚠️ Fix drift between "
        "trades and holdings); until then its stop is not on the full history."]


def test_home_notices_has_stopbasis_right_after_journalmatch():
    order = list(hn.SOURCE_ORDER)
    assert order[order.index("journalmatch") + 1] == "stopbasis"
    assert hn.SOURCE_LABELS["stopbasis"] == "Stop basis"
    assert len(order) == 18 and set(order) == set(hn.SOURCE_LABELS)


def test_the_new_module_is_a_gate_file():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ptc", _ROOT / ".claude" / "hooks" / "pre_tool_checks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "stock_analyzer/stop_basis_notice.py" in mod._GATE_FILES
    # (its docs/review-policy.md line isn't asserted here: that doc is not a suite
    # input, so a test reading it trips test_git_hook_adapter's suite-input check)


def test_warning_header_is_plural_for_several_carried_names():
    held = {"A": res(state=hc.APPLIED, value=101.5, set_on="2026-11-20"),
            "B": res(state=hc.NOT_BINDING, value=70.25, set_on="2026-11-19")}
    md = _md(held)
    assert md.split("\n")[0] == ("⚠️ **Stop basis: A and B are on carried stops until the "
                                  "trade journal is fixed.**")
