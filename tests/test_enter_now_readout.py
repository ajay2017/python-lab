"""ENTER_NOW base-rate readout (read-only diagnostic): pure functions, synthetic data.

Time-context matrix: a Monday call reads Friday's state, the day after a holiday
reads the session before it, and episodes fold across a weekend and a holiday.
"""
from __future__ import annotations

import ast
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import enter_now_readout as eno
from stock_analyzer import market_trend
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL
from stock_analyzer.data import is_trading_day

pytestmark = pytest.mark.fast

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "enter_now_regime_readout.py"


def _sessions(n: int, end: date = date(2026, 11, 20)) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _ser(days: list[date], values) -> pd.Series:
    return pd.Series([float(v) for v in values], index=pd.DatetimeIndex(days))


# -- dedupe_calls -------------------------------------------------------------

def test_dedupe_earliest_surfaced_row_wins_regardless_of_input_order():
    rows = [  # the DB returns newest first
        {"id": 9, "ticker": "nvda", "rec_date": "2026-11-02", "surfaced_at": "2026-11-02T15:30:00+00:00",
         "price_at_surface": 120.0},
        {"id": 3, "ticker": "NVDA", "rec_date": "2026-11-02", "surfaced_at": "2026-11-02T14:45:00+00:00",
         "price_at_surface": 100.0},
    ]
    out = eno.dedupe_calls(rows)
    assert len(out) == 1
    assert out[0]["price_at_surface"] == 100.0
    assert out[0]["ticker"] == "NVDA" and out[0]["rec_date"] == date(2026, 11, 2)


def test_dedupe_keeps_distinct_days_and_tickers_sorted_and_does_not_mutate_input():
    rows = [
        {"ticker": "MU", "rec_date": date(2026, 11, 3)},
        {"ticker": "NVDA", "rec_date": "2026-11-02"},
        {"ticker": "MU", "rec_date": pd.Timestamp("2026-11-02")},
    ]
    out = eno.dedupe_calls(rows)
    assert [(c["ticker"], c["rec_date"]) for c in out] == [
        ("MU", date(2026, 11, 2)), ("NVDA", date(2026, 11, 2)), ("MU", date(2026, 11, 3))]
    assert rows[1]["rec_date"] == "2026-11-02" and rows[1]["ticker"] == "NVDA"


def test_dedupe_tie_breaks_on_id_then_input_order():
    same = "2026-11-02T14:45:00+00:00"
    by_id = eno.dedupe_calls([
        {"id": 7, "ticker": "A", "rec_date": "2026-11-02", "surfaced_at": same, "k": "late"},
        {"id": 4, "ticker": "A", "rec_date": "2026-11-02", "surfaced_at": same, "k": "early"}])
    assert by_id[0]["k"] == "early"
    keyless = eno.dedupe_calls([
        {"ticker": "A", "rec_date": "2026-11-02", "k": "first"},
        {"ticker": "A", "rec_date": "2026-11-02", "k": "second"}])
    assert keyless[0]["k"] == "first"


def test_dedupe_drops_rows_without_ticker_or_date():
    rows = [{"ticker": "", "rec_date": "2026-11-02"}, {"ticker": "A", "rec_date": None},
            {"ticker": "B", "rec_date": float("nan")}, {"ticker": "C", "rec_date": "not a date"},
            {"ticker": None, "rec_date": "2026-11-02"}, {"ticker": "D", "rec_date": "2026-11-02"}]
    assert [c["ticker"] for c in eno.dedupe_calls(rows)] == ["D"]


# -- prior_session ------------------------------------------------------------

@pytest.mark.parametrize("call_day, expected", [
    (date(2026, 11, 23), date(2026, 11, 20)),   # Monday -> Friday
    (date(2026, 11, 21), date(2026, 11, 20)),   # Saturday call -> Friday
    (date(2026, 11, 22), date(2026, 11, 20)),   # Sunday call -> Friday
    (date(2026, 11, 24), date(2026, 11, 23)),   # ordinary Tuesday
    (date(2026, 11, 26), date(2026, 11, 25)),   # Thanksgiving itself -> Wednesday
    (date(2026, 11, 27), date(2026, 11, 25)),   # day after Thanksgiving -> Wednesday
    (date(2026, 1, 2), date(2025, 12, 31)),     # day after New Year's holiday
    (date(2026, 4, 6), date(2026, 4, 2)),       # Monday after Good Friday -> Thursday
])
def test_prior_session_skips_weekends_and_holidays(call_day, expected):
    assert eno.prior_session(call_day) == expected


# -- group_episodes -----------------------------------------------------------

def _c(tk, d):
    return {"ticker": tk, "rec_date": d}


def test_episode_folds_across_a_weekend():
    calls = [_c("A", date(2026, 11, 20)), _c("A", date(2026, 11, 23))]   # Fri, Mon = 1 session
    assert len(eno.group_episodes(calls, 1)) == 1
    assert len(eno.group_episodes(calls, 0)) == 2


def test_episode_folds_across_a_holiday_and_counts_sessions_not_days():
    wed_fri = [_c("A", date(2026, 11, 25)), _c("A", date(2026, 11, 27))]   # Thanksgiving between: 1 session
    tue_fri = [_c("A", date(2026, 11, 24)), _c("A", date(2026, 11, 27))]   # Wed + Fri = 2 sessions
    assert len(eno.group_episodes(wed_fri, 1)) == 1
    assert len(eno.group_episodes(tue_fri, 1)) == 2
    assert len(eno.group_episodes(tue_fri, 2)) == 1


def test_episode_gap_is_measured_from_the_previous_call_not_the_first():
    calls = [_c("A", date(2026, 11, 2)), _c("A", date(2026, 11, 5)), _c("A", date(2026, 11, 10))]
    one = eno.group_episodes(calls, 3)    # Mon->Thu 3 sessions, Thu->Tue 3 sessions
    assert len(one) == 1 and one[0]["n_calls"] == 3
    assert one[0]["first_date"] == date(2026, 11, 2) and one[0]["last_date"] == date(2026, 11, 10)
    assert len(eno.group_episodes(calls, 2)) == 3


def test_episodes_are_per_ticker_graded_from_first_call_and_default_gap_is_module_level():
    a1, a2, b1 = _c("A", date(2026, 11, 3)), _c("A", date(2026, 11, 2)), _c("B", date(2026, 11, 2))
    eps = eno.group_episodes([a1, a2, b1])      # default gap, unsorted input
    assert [e["episode_id"] for e in eps] == ["A@2026-11-02", "B@2026-11-02"]
    assert eps[0]["first"] is a2 and eps[0]["n_calls"] == 2
    assert eno.EPISODE_GAP_SESSIONS == 3


# -- summarise / cells_by / grade_call ----------------------------------------

def test_summarise_below_the_floor_is_building_with_no_figures():
    s = eno.summarise({"x": [1.0, -2.0, 3.0]}, min_calls=4)[0]
    assert s["verdict"] == "building" and s["n"] == 3
    assert s["beat_share"] is None and s["mean_alpha"] is None and s["median_alpha"] is None


def test_summarise_at_the_floor_is_measured():
    s = eno.summarise({"x": [1.0, -2.0, 3.0, 0.0]}, min_calls=4)[0]
    assert s["verdict"] == "measured" and s["n"] == 4
    assert s["beat_share"] == pytest.approx(0.5)      # alpha 0.0 does not beat SPY (strict >)
    assert s["mean_alpha"] == pytest.approx(0.5)
    assert s["median_alpha"] == pytest.approx(0.5)


def test_summarise_ignores_nan_keeps_order_and_reports_empty_cells():
    out = eno.summarise({"b": [1.0, float("nan"), None], "a": []}, min_calls=1)
    assert [s["cell"] for s in out] == ["b", "a"]
    assert out[0]["n"] == 1 and out[0]["verdict"] == "measured"
    assert out[1]["n"] == 0 and out[1]["verdict"] == "building"


def test_summarise_row_keys_never_mention_composite():
    keys = set(eno.summarise({"x": [1.0]}, 1)[0]) | set(eno.summarise({"x": []}, 1)[0])
    assert not [k for k in keys if "composite" in k.lower() or "score" in k.lower()]


def test_cells_by_skips_unmatured_orders_cells_and_labels_none():
    rows = [{"f": "yes", "a": 1.0}, {"f": "no", "a": None}, {"f": None, "a": 2.0},
            {"f": "odd", "a": -1.0}, {"f": "yes", "a": float("nan")}]
    cells = eno.cells_by(rows, "f", "a", ("no", "yes"))
    assert list(cells) == ["no", "yes", "not checked", "odd"]   # `order` first, the rest sorted
    assert cells["no"] == [] and cells["yes"] == [1.0]
    assert cells["not checked"] == [2.0] and cells["odd"] == [-1.0]


def test_grade_call_not_matured_never_calls_the_price_function():
    def boom(*a):
        raise AssertionError("must not price an unmatured call")
    call = {"ticker": "A", "rec_date": date(2026, 11, 20), "price_at_surface": 10.0}
    assert eno.grade_call(call, 10, date(2026, 11, 22), boom) == ("not_matured", None)


def test_grade_call_matured_graded_and_unpriced():
    call = {"ticker": "A", "rec_date": date(2026, 9, 1), "price_at_surface": "10.5"}
    seen = {}

    def ok(t, d, p, h):
        seen.update(t=t, d=d, p=p, h=h)
        return 2.5
    assert eno.grade_call(call, 10, date(2026, 11, 22), ok) == ("graded", 2.5)
    assert seen == {"t": "A", "d": date(2026, 9, 1), "p": 10.5, "h": 10}
    assert eno.grade_call(call, 10, date(2026, 11, 22), lambda *a: None) == ("unpriced", None)
    assert eno.grade_call(call, 10, date(2026, 11, 22), lambda *a: float("nan")) == ("unpriced", None)


def test_is_matured_boundary_is_the_target_session_itself():
    d = date(2026, 11, 2)
    target = eno.advance_trading_days(d, 10)
    assert eno.is_matured(d, 10, target)
    assert not eno.is_matured(d, 10, target - timedelta(days=1))


# -- regime_of ----------------------------------------------------------------

_DAYS = _sessions(260)
_AS_OF = _DAYS[-1]


def _spy_up():
    return _ser(_DAYS, np.linspace(300, 500, len(_DAYS)))     # last close well above its mean


def _spy_broken():
    vals = [100.0] * (len(_DAYS) - 1) + [50.0]                # last close far below its mean
    return _ser(_DAYS, vals)


def test_regime_armed_by_trend_leg_only():
    r = eno.regime_of(_spy_broken(), _ser([_AS_OF], [RISK_OFF_VIX_LEVEL - 5]), _AS_OF)
    assert r["armed"] is True and r["state"] == "measured"
    assert len(r["legs"]) == 1 and f"{RISK_OFF_TREND_MA}-day" in r["legs"][0]


def test_regime_armed_by_vix_leg_only():
    r = eno.regime_of(_spy_up(), _ser([_AS_OF], [RISK_OFF_VIX_LEVEL]), _AS_OF)
    assert r["armed"] is True
    assert len(r["legs"]) == 1 and "VIX" in r["legs"][0]


def test_regime_not_armed_when_neither_leg_trips():
    r = eno.regime_of(_spy_up(), _ser([_AS_OF], [RISK_OFF_VIX_LEVEL - 0.5]), _AS_OF)
    assert r["armed"] is False and r["legs"] == [] and r["state"] == "measured"


def test_regime_unmeasurable_is_none_not_false():
    none_at_all = eno.regime_of(pd.Series(dtype=float), None, _AS_OF)
    assert none_at_all["armed"] is None and none_at_all["state"] == "not_measured"
    short = eno.regime_of(_ser(_DAYS[-30:], [100.0] * 30), None, _AS_OF)   # < RISK_OFF_TREND_MA, no VIX
    assert short["armed"] is None and short["state"] == "unusable"


def test_regime_uses_only_closes_on_or_before_as_of():
    earlier = _DAYS[-10]
    vix = pd.Series([10.0, 40.0], index=pd.DatetimeIndex([earlier, _AS_OF]))
    r = eno.regime_of(_spy_broken(), vix, earlier)    # the crash and the VIX spike are both after
    assert r["armed"] is False
    assert eno.regime_of(_spy_broken(), vix, _AS_OF)["armed"] is True


# -- trend labels / sector resolution -----------------------------------------

def _fake_trend(sector_label="weakening"):
    return {
        "indices": [
            {"symbol": "^GSPC", "label": "falling", "drawdown_word": "correction"},
            {"symbol": "^IXIC", "label": "steady", "drawdown_word": None},
        ],
        "sectors": [
            {"symbol": "SOXX", "app_sectors": ["Semiconductors"], "label": sector_label},
            {"symbol": "XLE", "app_sectors": ["Energy"], "label": "rising"},
        ],
    }


def test_trend_labels_reads_the_sp500_row_and_the_sector_fund_row():
    t = eno.trend_labels(_fake_trend(), "NVDA", "")
    assert t["spx_label"] == "falling" and t["spx_drawdown"] == "correction"
    assert t["sector"] == "Semiconductors" and t["sector_fund"] == "SOXX"
    assert t["sector_label"] == "weakening"


def test_trend_labels_sector_with_no_fund_row_is_none():
    t = eno.trend_labels(_fake_trend(), "ZZZZ", "")          # unmapped ticker -> "Other"
    assert t["sector_fund"] is None and t["sector_label"] is None
    assert t["spx_label"] == "falling"
    assert eno.label_key(t["sector_label"]) == "not checked"


def test_trend_labels_fund_not_checked_and_unreadable_trend_are_none():
    assert eno.trend_labels(_fake_trend(None), "NVDA", "")["sector_label"] is None
    t = eno.trend_labels(None, "NVDA", "")
    assert t["spx_label"] is None and t["sector_label"] is None and t["sector_fund"] is None


def test_trend_labels_tolerates_a_nan_row_sector():
    assert eno.trend_labels(_fake_trend(), "NVDA", float("nan"))["sector_fund"] == "SOXX"


def test_spx_symbol_is_pinned_to_market_trend_and_real_compute_trend_feeds_trend_labels():
    assert market_trend.trend_symbols()[0] == eno._SPX_SYMBOL
    days = _sessions(300)
    closes = pd.DataFrame(
        {s: np.linspace(100, 200, len(days)) for s in market_trend.trend_symbols()},
        index=pd.DatetimeIndex(days))
    res = market_trend.compute_trend(closes, as_of=days[-1])
    assert res is not None
    t = eno.trend_labels(res, "NVDA", "")
    assert t["spx_label"] == "rising" and t["sector_fund"] == "SOXX" and t["sector_label"] == "rising"


# -- day-move tone (approximation) ---------------------------------------------

def _spx(**kw):
    days = [date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)]
    return _ser(days, [100.0, kw.get("fri", 99.0), kw.get("mon", 99.1)])


def test_day_move_monday_compares_with_friday():
    assert eno.day_move_pct(_spx(), date(2026, 11, 23)) == pytest.approx(0.1010101, abs=1e-4)
    assert eno.day_tone_approx(_spx(), date(2026, 11, 20)) == {"pct": pytest.approx(-1.0), "tone": "bear"}


def test_day_tone_bull_flat_and_unknown_cases():
    assert eno.day_tone_approx(_spx(fri=101.0), date(2026, 11, 20))["tone"] == "bull"
    assert eno.day_tone_approx(_spx(fri=100.1), date(2026, 11, 20))["tone"] == "flat"
    assert eno.day_tone_approx(_spx(), date(2026, 11, 21)) == {"pct": None, "tone": "unknown"}   # Saturday
    assert eno.day_tone_approx(_spx(), date(2026, 11, 19)) == {"pct": None, "tone": "unknown"}   # no prior bar
    thanksgiving = _ser([date(2026, 11, 25), date(2026, 11, 27)], [100.0, 99.0])
    assert eno.day_move_pct(thanksgiving, date(2026, 11, 26)) is None                             # holiday


def test_day_move_after_a_holiday_uses_the_session_before_it():
    s = _ser([date(2026, 11, 25), date(2026, 11, 27)], [100.0, 99.0])
    assert eno.day_move_pct(s, date(2026, 11, 27)) == pytest.approx(-1.0)


# -- classify_call ------------------------------------------------------------

def test_classify_call_adds_market_state_and_labels():
    call = {"ticker": "NVDA", "rec_date": _AS_OF + timedelta(days=3), "sector": "Semiconductors",
            "already_held": np.bool_(True), "composite_score": 70.0}
    out = eno.classify_call(
        call, spy_close=_spy_broken(), spx_close=_spy_up(),
        vix_close=_ser([_AS_OF], [RISK_OFF_VIX_LEVEL - 5]), trend_fn=lambda as_of: _fake_trend())
    assert out["prior_session"] == _AS_OF           # Mon 11-23 call -> Fri 11-20
    assert out["regime_armed"] == "yes" and out["regime_state"] == "measured"
    assert out["spx_label"] == "falling" and out["spx_drawdown"] == "correction"
    assert out["sector_label"] == "weakening" and out["sector_fund"] == "SOXX"
    assert out["already_held"] == "yes"
    assert out["day_tone_approx"] == "unknown"       # no call-day bar downloaded
    assert out["composite_score"] == 70.0 and "prior_session" not in call


def test_classify_call_unmeasured_state_reads_unknown_and_not_checked():
    call = {"ticker": "ZZZZ", "rec_date": date(2026, 11, 23), "already_held": None}
    out = eno.classify_call(call, spy_close=pd.Series(dtype=float), spx_close=pd.Series(dtype=float),
                            vix_close=None, trend_fn=lambda as_of: None)
    assert out["regime_armed"] == "unknown"
    assert out["spx_label"] == "not checked" and out["sector_label"] == "not checked"
    assert out["already_held"] == "unknown" and out["day_tone_approx"] == "unknown"
    for held, label in ((True, "yes"), (False, "no"), (float("nan"), "unknown")):
        c = eno.classify_call({"ticker": "A", "rec_date": date(2026, 11, 23), "already_held": held},
                              spy_close=pd.Series(dtype=float), spx_close=pd.Series(dtype=float),
                              vix_close=None, trend_fn=lambda as_of: None)
        assert c["already_held"] == label


# -- no composite-band split (pre-registered) ---------------------------------

def _non_docstring_strings(tree: ast.AST) -> list[str]:
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                doc_nodes.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes]


def test_splits_are_market_state_only_no_composite_or_score_band():
    assert len(eno.SPLITS) == 9  # 5 market-state splits + the entry regime rules A / B / C + the Nasdaq label (C2)
    for title, field, order in eno.SPLITS:
        text = " ".join([title, field, *order]).lower()
        assert "composite" not in text and "score" not in text and "band" not in text


def test_script_has_no_composite_split_outside_the_csv_only_column():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    strings = _non_docstring_strings(tree)
    offenders = [s for s in strings if "composite" in s.lower() and s != "composite_score"]
    assert offenders == []
    # composite_score may appear only inside the CSV_COLUMNS tuple
    csv_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "CSV_COLUMNS" for t in n.targets)]
    assert len(csv_nodes) == 1
    in_csv = [c.value for c in ast.walk(csv_nodes[0].value) if isinstance(c, ast.Constant)]
    assert in_csv.count("composite_score") == 1
    assert strings.count("composite_score") == 1


def test_script_is_read_only_pops_the_llm_key_and_never_imports_app():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'os.environ.pop("ANTHROPIC_API_KEY", None)' in src
    assert "db.set_readonly(True)" in src and "db.has_db()" in src
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "app" not in imported and "streamlit" not in imported
    # nothing persists: no db.save_*/upsert/insert calls
    assert not re.findall(r"db\.(save_\w+|upsert\w*|insert\w*|delete\w*)", src)


def test_script_csv_columns_carry_the_requested_fields():
    ns = {}
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CSV_COLUMNS" for t in n.targets):
            ns["cols"] = ast.literal_eval(n.value)
    for need in ("ticker", "rec_date", "prior_session", "alpha_10", "alpha_30", "regime_armed",
                 "regime_legs", "spx_label", "spx_drawdown", "sector_label", "day_tone_approx",
                 "already_held", "episode_id"):
        assert need in ns["cols"]
