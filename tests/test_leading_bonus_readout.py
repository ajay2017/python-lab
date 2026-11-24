"""Tests for the TrendVsLeading readout (stock_analyzer/leading_bonus_readout.py and
scripts/leading_bonus_readout.py): the reconstructed top-3 with the IGV alias rule,
the as-of session choice, bonus states, episodes, splits, the floors and the
read-only guarantee. Synthetic data only; the script's I/O is not exercised (no
Supabase in tests).
"""
from __future__ import annotations

import ast
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import enter_now_readout as eno
from stock_analyzer import leading_bonus_readout as lbr
from stock_analyzer import market_trend as mt
from stock_analyzer.constants import (
    GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_HORIZON_TRADING_DAYS,
    GATE_LEDGER_MIN_TICKERS,
)
from stock_analyzer.data import is_trading_day, previous_trading_day
from stock_analyzer.portfolio import SECTOR_ETF

pytestmark = pytest.mark.fast

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "leading_bonus_readout.py"
_MODULE = _REPO / "stock_analyzer" / "leading_bonus_readout.py"

FUNDS = list(dict.fromkeys(SECTOR_ETF.values()))
PRIOR = date(2026, 11, 20)        # Friday
CALL = date(2026, 11, 23)         # Monday


def _sessions(end: date, n: int) -> list[date]:
    out = [end]
    while len(out) < n:
        out.append(previous_trading_day(out[-1]))
    return sorted(out)


def _closes(last: dict[str, float], end: date = PRIOR, n: int = 12, extra_days=()):
    """Every fund flat at 100 except the last bar, set from `last` (so 1W = last-100)."""
    days = _sessions(end, n) + list(extra_days)
    data = {}
    for f in FUNDS:
        vals = [100.0] * len(days)
        vals[len(_sessions(end, n)) - 1] = last.get(f, 100.0)
        data[f] = vals
    return pd.DataFrame(data, index=days)


def _row(**kw):
    base = {"ticker": "AAA", "rec_date": CALL, "rec_type": "new_pick", "sector": "Semiconductors",
            "thesis": "Strong momentum.", "t_score": 70.0, "bq_score": 60.0, "val_score": 50.0}
    base.update(kw)
    return base


# -- reconstruction -----------------------------------------------------------

def test_one_week_returns_mirrors_the_live_formula_and_rounds_to_2dp():
    cl = _closes({"SOXX": 103.456, "XLU": 98.0})
    rets, missing = lbr.one_week_returns(cl, PRIOR)
    assert missing == []
    assert rets["SOXX"] == round((103.456 / 100 - 1) * 100, 2) == 3.46
    assert rets["XLU"] == -2.0
    assert list(rets) == FUNDS          # SECTOR_ETF value order, SPY included


def test_one_week_returns_uses_five_bars_back():
    days = _sessions(PRIOR, 8)
    cl = pd.DataFrame({f: [100.0] * 8 for f in FUNDS}, index=days)
    cl.loc[days[-6], "XLE"] = 50.0     # the close 5 bars before the last
    cl.loc[days[-7], "XLE"] = 10.0     # older: must not matter
    rets, _ = lbr.one_week_returns(cl, PRIOR)
    assert rets["XLE"] == 100.0


def test_one_week_returns_ignores_bars_after_as_of():
    later = [CALL]
    cl = _closes({"SOXX": 101.0}, extra_days=later)
    cl.loc[CALL, "SOXX"] = 500.0
    rets, _ = lbr.one_week_returns(cl, PRIOR)
    assert rets["SOXX"] == 1.0


def test_fund_without_a_bar_on_as_of_is_named_and_blocks_the_state():
    cl = _closes({"SOXX": 101.0}).drop(columns=["XLU"])
    rets, missing = lbr.one_week_returns(cl, PRIOR)
    assert missing == ["XLU"] and "XLU" not in rets
    assert lbr.session_state(cl, PRIOR, None) is None      # never ranks an incomplete set
    stale = _closes({}).copy()
    stale.loc[PRIOR, "XLE"] = float("nan")
    assert lbr.one_week_returns(stale, PRIOR)[1] == ["XLE"]


def test_top_three_with_the_igv_alias_rule():
    cl = _closes({"IGV": 105.0, "SOXX": 103.0, "XLU": 101.5, "XLE": 101.0})
    rets, _ = lbr.one_week_returns(cl, PRIOR)
    lead = lbr.leading_funds(rets)
    assert [x["etf"] for x in lead] == ["IGV", "SOXX", "XLU"]
    igv = lead[0]
    assert igv["aliases"] == ["AI & Cloud", "AI & Data", "Enterprise Tech"]
    assert igv["sector"] == "AI & Cloud" and igv["return_1w"] == 5.0
    assert len(lead) == lbr.LIVE_LEADING_TOP_N == 3
    # every IGV bucket is reachable when IGV leads; a non-leading bucket is not
    for s in ("AI & Cloud", "AI & Data", "Enterprise Tech", "Semiconductors", "Utilities"):
        assert lbr.gets_leading_bonus(s, lead)
    assert not lbr.gets_leading_bonus("Healthcare", lead)
    assert not lbr.gets_leading_bonus("", lead)


def test_spy_can_lead_and_matches_other():
    cl = _closes({"SPY": 104.0, "SOXX": 103.0, "XLU": 101.5})
    lead = lbr.leading_funds(lbr.one_week_returns(cl, PRIOR)[0])
    assert lead[0]["etf"] == "SPY" and lead[0]["aliases"] == ["Other"]
    assert lbr.gets_leading_bonus("Other", lead)


def test_substring_match_is_the_live_behaviour():
    # "Energy" (XLE's key) is a substring of "Clean Energy": the live expression
    # gives the bonus; the readout must reproduce it, not "fix" it.
    lead = [{"etf": "XLE", "sector": "Energy", "aliases": ["Energy"], "return_1w": 3.0}]
    assert lbr.gets_leading_bonus("Clean Energy", lead)
    assert lbr.matched_fund("Clean Energy", lead)["etf"] == "XLE"
    # an entry cached without aliases falls back to its sector name
    old = [{"etf": "XLE", "sector": "Energy", "return_1w": 3.0}]
    assert lbr.gets_leading_bonus("Energy", old)


def test_ties_keep_sector_etf_order():
    rets = {"AAA": 1.0, "BBB": 1.0, "CCC": 1.0, "DDD": 1.0}
    assert [x["etf"] for x in lbr.leading_funds(rets)] == ["AAA", "BBB", "CCC"]


# -- pins to the live source ----------------------------------------------------

def test_live_expressions_are_pinned_to_the_source():
    briefing = (_REPO / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    assert "alias in str(sector)" in briefing
    assert 'for alias in (ls.get("aliases") or [ls.get("sector", "")])' in briefing
    assert "for ls in lead_secs" in briefing
    assert lbr.MOVER_THESIS_MARKER in briefing
    assert "is_mover and day_change is not None" in briefing
    app = (_REPO / "app.py").read_text(encoding="utf-8")
    assert f'"1W": {lbr.LIVE_ONE_WEEK_BARS}' in app
    assert f"_sect_sorted.head({lbr.LIVE_LEADING_TOP_N})" in app
    assert 'sort_values("1W", ascending=False)' in app
    assert lbr.LIVE_ONE_WEEK_BARS == mt._ONE_WEEK_SESSIONS


def test_orders_match_the_enter_now_readout():
    assert lbr.TREND_ORDER == eno._TREND_ORDER
    assert lbr.NOT_CHECKED == eno.NOT_CHECKED


def test_floors_are_the_enter_now_go_live_constants():
    assert lbr.MIN_EPISODES == GATE_LEDGER_FIRM_CALLS
    assert lbr.MIN_TICKERS == GATE_LEDGER_MIN_TICKERS
    assert lbr.HEADLINE_HORIZON == GATE_LEDGER_HORIZON_TRADING_DAYS


# -- bonus states ---------------------------------------------------------------

def _state(last=None, as_of=PRIOR, trend=None):
    last = last or {"SOXX": 103.0, "XLU": 101.5, "IGV": 105.0}
    return lbr.session_state(_closes(last, end=as_of), as_of, trend)


def test_bonus_state_matrix():
    st = _state()
    assert lbr.bonus_state(_row(sector="Semiconductors"), st)[0] == "yes"
    assert lbr.bonus_state(_row(sector="Healthcare"), st)[0] == "no"
    assert lbr.bonus_state(_row(sector="Healthcare", t_score=None, bq_score=None, val_score=None), st)[0] == "no"
    # a mover is ranked on composite: never the bonus, even in a leading sector
    mover = _row(thesis="Up +3.0% today — breakout outside your core universe. x")
    assert lbr.bonus_state(mover, st)[0] == "no"
    # cron / unknown writer in a leading sector: not compared
    cron = _row(t_score=None, bq_score=None, val_score=None)
    assert lbr.bonus_state(cron, st)[0] == "unknown"
    # no thesis to read mover status from
    assert lbr.bonus_state(_row(thesis=None), st)[0] == "unknown"
    # no reconstructable state
    assert lbr.bonus_state(_row(), None)[0] == "unknown"
    # a non-string sector is treated as no sector, not a crash
    assert lbr.bonus_state(_row(sector=float("nan")), st)[0] == "no"


def test_overflow_style_rows_in_a_leading_sector_are_unknown_not_guessed():
    st = _state()
    overflow = {"ticker": "ZZZ", "rec_date": CALL, "rec_type": "overflow_pick",
                "sector": "Semiconductors", "composite_score": 70}
    assert lbr.bonus_state(overflow, st)[0] == "unknown"
    assert lbr.bonus_state({**overflow, "sector": "Healthcare"}, st)[0] == "no"


def test_has_pillar_scores_and_mover_helpers():
    assert lbr.has_pillar_scores({"val_score": 1})
    assert not lbr.has_pillar_scores({"t_score": float("nan"), "bq_score": None})
    assert lbr.is_mover_pick("Up +1.0% today — breakout outside your core universe.") is True
    assert lbr.is_mover_pick("Steady uptrend.") is False
    assert lbr.is_mover_pick("") is None and lbr.is_mover_pick(None) is None


def test_week_direction():
    assert lbr.week_direction(0.01) == "up" and lbr.week_direction(-0.01) == "down"
    assert lbr.week_direction(0.0) == "flat" and lbr.week_direction(None) == "unknown"


# -- the as-of session choice -----------------------------------------------------

def test_monday_call_reads_fridays_close_primary_and_the_call_day_as_sensitivity():
    seen = []

    def state_fn(d):
        seen.append(d)
        return None
    lbr.classify_pick(_row(rec_date=CALL), state_fn)
    assert seen == [PRIOR, CALL] and PRIOR == previous_trading_day(CALL)
    assert eno.prior_session(CALL) == PRIOR


def test_call_after_a_holiday_reads_the_session_before_it():
    thanksgiving_fri = date(2026, 11, 27)
    assert is_trading_day(thanksgiving_fri) and not is_trading_day(date(2026, 11, 26))
    seen = []
    lbr.classify_pick(_row(rec_date=thanksgiving_fri), lambda d: seen.append(d))
    assert seen[0] == date(2026, 11, 25)


def test_classify_pick_for_a_bonus_pick_carries_the_fund_label_and_direction():
    trend = {"sectors": [{"symbol": "SOXX", "label": "falling", "app_sectors": ["Semiconductors"]},
                         {"symbol": "IGV", "label": "rising", "app_sectors": []}]}
    st = _state(trend=trend)
    c = lbr.classify_pick(_row(sector="Semiconductors"), lambda d: st)
    assert c["bonus"] == "yes" and c["bonus_alt"] == "yes"
    assert c["bonus_fund"] == "SOXX" and c["fund_label"] == "falling"
    assert c["fund_ret_1w"] == 3.0 and c["fund_dir"] == "up"
    assert c["leading_funds"] == "IGV,SOXX,XLU"
    # a fund the trend did not label reads 'not checked'; a down-week fund reads down
    down = {f: 90.0 for f in FUNDS}
    down.update({"SOXX": 97.0, "XLU": 96.0, "XLE": 95.0})
    st2 = _state(last=down, trend={"sectors": []})
    assert [x["etf"] for x in st2["leading"]] == ["SOXX", "XLU", "XLE"]
    c2 = lbr.classify_pick(_row(sector="Semiconductors"), lambda d: st2)
    assert c2["fund_label"] == "not checked" and c2["fund_dir"] == "down"
    # a non-bonus pick has no fund fields
    c3 = lbr.classify_pick(_row(sector="Healthcare"), lambda d: st)
    assert c3["bonus"] == "no" and c3["fund_label"] is None and c3["fund_dir"] is None


def test_sensitivity_flag_when_the_two_reads_disagree():
    a = _state(last={"SOXX": 103.0})
    b = _state(last={"XLE": 103.0, "SOXX": 90.0})
    c = lbr.classify_pick(_row(sector="Semiconductors"), lambda d: a if d == PRIOR else b)
    assert c["bonus"] == "yes" and c["bonus_alt"] == "no"
    cnt = lbr.bonus_counts([c])
    assert cnt["differ_alt"] == 1 and cnt["by_state"]["yes"] == 1


# -- episodes and splits -----------------------------------------------------------

def _graded(tk, d, bonus, a30, a10=None, **kw):
    r = {"ticker": tk, "rec_date": d, "bonus": bonus, "alpha_30": a30, "alpha_10": a10,
         "fund_label": kw.get("fund_label"), "fund_dir": kw.get("fund_dir")}
    return r


def test_episode_takes_the_first_calls_bonus_state():
    d0 = date(2026, 10, 5)
    calls = [_graded("AAA", d0, "yes", 1.0), _graded("AAA", d0 + timedelta(days=1), "no", -3.0),
             _graded("BBB", d0, "no", 2.0)]
    eps = eno.group_episodes(sorted(calls, key=lambda c: (c["rec_date"], c["ticker"])), 3)
    assert len(eps) == 2
    aaa = next(e for e in eps if e["ticker"] == "AAA")
    assert aaa["n_calls"] == 2 and aaa["first"]["bonus"] == "yes"


def test_cell_summary_floors_withhold_figures():
    rows = [{"ticker": f"T{i}", "alpha_30": 1.0 if i % 3 else -1.0} for i in range(GATE_LEDGER_FIRM_CALLS)]
    ok = lbr.cell_summary(rows, "alpha_30")
    assert ok["verdict"] == "measured" and ok["n"] == GATE_LEDGER_FIRM_CALLS
    assert ok["beat_share"] is not None and ok["median_alpha"] == 1.0
    short = lbr.cell_summary(rows[:-1], "alpha_30")
    assert short["verdict"] == "not enough data" and short["beat_share"] is None
    # enough episodes but too few distinct tickers
    few = [{"ticker": f"T{i % (GATE_LEDGER_MIN_TICKERS - 1)}", "alpha_30": 1.0}
           for i in range(GATE_LEDGER_FIRM_CALLS + 5)]
    f = lbr.cell_summary(few, "alpha_30")
    assert f["verdict"] == "not enough data" and f["tickers"] == GATE_LEDGER_MIN_TICKERS - 1
    # ungraded rows (no alpha) are not counted
    assert lbr.cell_summary([{"ticker": "X", "alpha_30": None}], "alpha_30")["n"] == 0


def _population(n_yes, n_no, fund_label="falling"):
    rows = []
    for i in range(n_yes):
        rows.append(_graded(f"Y{i}", date(2026, 10, 1), "yes", 2.0 if i % 2 else -2.0,
                            fund_label=fund_label, fund_dir="up" if i % 2 else "down"))
    for i in range(n_no):
        rows.append(_graded(f"N{i}", date(2026, 10, 1), "no", 1.0))
    return rows


def test_split_a_per_episode_with_per_call_beside_it_and_withheld_when_thin():
    n = GATE_LEDGER_FIRM_CALLS
    eps = _population(n, 3)
    calls = eps + [dict(r, rec_date=date(2026, 10, 2)) for r in eps]     # clustered duplicates
    items = lbr.split_cells(calls, eps, "bonus", lbr.BONUS_ORDER, 30)
    by = {i["cell"]: i for i in items}
    assert by["yes"]["episodes"]["verdict"] == "measured" and by["yes"]["episodes"]["n"] == n
    assert by["yes"]["calls"]["n"] == 2 * n                               # per call is clustered
    assert by["no"]["episodes"]["verdict"] == "not enough data"
    assert by["no"]["calls"]["beat_share"] is None                        # withheld with the cell
    assert by["unknown"]["episodes"]["n"] == 0
    assert list(by) == list(lbr.BONUS_ORDER)


def test_splits_b_and_c_only_look_at_bonus_picks():
    n = GATE_LEDGER_FIRM_CALLS
    eps = _population(n, n)
    titles = {t: (f, o, w) for t, f, o, _n, w in lbr.split_definitions()}
    (fb, ob, wb) = next(v for k, v in titles.items() if k.startswith("(b)"))
    items = lbr.split_cells(eps, eps, fb, ob, 30, wb)
    by = {i["cell"]: i for i in items}
    assert by["falling"]["episodes"]["n"] == n            # the N* rows are not in any (b) cell
    assert by["falling"]["episodes"]["verdict"] == "measured"
    assert sum(i["episodes"]["n"] for i in items) == n
    assert by["rising"]["episodes"]["n"] == 0
    (fc, oc, wc) = next(v for k, v in titles.items() if k.startswith("(c)"))
    items_c = {i["cell"]: i for i in lbr.split_cells(eps, eps, fc, oc, 30, wc)}
    assert items_c["up"]["episodes"]["n"] + items_c["down"]["episodes"]["n"] == n
    assert [i["cell"] for i in lbr.split_cells(eps, eps, fc, oc, 30, wc)][:3] == ["up", "down", "flat"]


def test_format_lines_print_not_enough_data_and_no_verdict_words():
    thin = lbr.split_cells(_population(2, 2), _population(2, 2), "bonus", lbr.BONUS_ORDER, 30)
    text = "\n".join(lbr.format_cell_line(i, lbr.BONUS_NAMES) for i in thin)
    assert "not enough data" in text and "beat SPY" not in text
    assert f"needs {GATE_LEDGER_FIRM_CALLS} episodes and {GATE_LEDGER_MIN_TICKERS} tickers" in text
    full = lbr.split_cells(_population(GATE_LEDGER_FIRM_CALLS, 0),
                           _population(GATE_LEDGER_FIRM_CALLS, 0), "bonus", lbr.BONUS_ORDER, 30)
    line = lbr.format_cell_line(full[0], lbr.BONUS_NAMES)
    assert "beat SPY" in line and "median alpha" in line and "per call (clustered)" in line
    for word in ("recommend", "should", "remove the bonus", "verdict:"):
        assert word not in line.lower()


# -- definition, caveat and the read-only guarantee ------------------------------------

def test_definition_states_the_pre_registered_pieces():
    d = lbr.PRE_REGISTERED_DEFINITION
    assert d.startswith("PRE-REGISTERED DEFINITION")
    for piece in ("one episode per ticker", "(a)", "(b)", "(c)", "not enough data",
                  "GATE_LEDGER_FIRM_CALLS", "primary", "No verdict"):
        assert piece in d
    assert f"{GATE_LEDGER_FIRM_CALLS} episodes or {GATE_LEDGER_MIN_TICKERS} tickers" in d
    assert str(GATE_LEDGER_HORIZON_TRADING_DAYS) in d
    assert "one market cycle, clustered calls" in lbr.CAVEAT_LINE.lower()


def test_script_prints_the_definition_first_and_the_caveat_last():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert src.index("print(lbr.PRE_REGISTERED_DEFINITION)") < src.index("\n    db.set_readonly(True)")
    assert "print(f\"\\n{lbr.CAVEAT_LINE}\")" in src
    assert '"--rec-types"' in src and 'default="new_pick"' in src
    assert "by_rec_type=True" in src and "group_episodes" in src


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def test_module_imports_no_db_and_no_streamlit():
    imps = _imports(_MODULE)
    assert not any(n == "stock_analyzer.db" or n.startswith("stock_analyzer.db.") for n in imps)
    assert "stock_analyzer.db" not in imps and "streamlit" not in imps
    assert not any("providers" in n for n in imps)


def test_script_has_no_write_path_and_forces_readonly():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "db.set_readonly(True)" in src
    tree = ast.parse(src)
    # No supabase-style write or query call anywhere: .table(...) is banned outright,
    # and .insert/.upsert/.update/.delete/.execute/.rpc are banned except the harmless
    # `sys.path.insert(...)`. Matched on the AST, not on substrings.
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            attr, recv = n.func.attr, ast.unparse(n.func.value)
            assert attr != "table", ast.unparse(n)
            if attr in {"insert", "upsert", "update", "delete", "execute", "rpc"}:
                assert recv == "sys.path" and attr == "insert", ast.unparse(n)
    # and no db.save_ / add_ / delete_ / clear_ / write style call (allowlist below is stricter)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "db":
            assert not n.attr.startswith(("save_", "add_", "delete_", "clear_", "write_", "upsert_")), n.attr
    assert "_client()" not in src
    # the only db calls are the guard, the credential check and the read
    db_calls = {n.attr for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "db"}
    assert db_calls == {"set_readonly", "has_db", "load_recommendations_or_none"}
    assert "ANTHROPIC_API_KEY" in src


def test_script_module_loads_and_exposes_main():
    spec = importlib.util.spec_from_file_location("leading_bonus_readout_t", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.main)
    assert "alpha_30" in mod.CSV_COLUMNS and "bonus" in mod.CSV_COLUMNS
