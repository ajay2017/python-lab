"""
Tests for stock_analyzer/debrief_advisor.py — Portfolio Debrief Advisor (F-3
AI Intelligence Layer): the weekly retrospective data-package builder (from
raw snapshot/rec/trade DataFrames), prompt formatting, response parsing, and
the LLM call itself. Zero coverage before this batch.
`build_debrief_package`'s two enrichment blocks (behavioral fingerprint,
decision quality) locally import their helpers at call time, so they are
monkeypatched directly on `stock_analyzer.behavioral_fingerprint` /
`stock_analyzer.decision_quality`. `generate_debrief`'s real Anthropic call
is exercised via a fake `sys.modules["anthropic"]` module for a full 4-header
round trip; its guard clauses return before `import anthropic` runs and need
no mocking.
"""
import sys
import types
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import debrief_advisor as da


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.messages = _FakeMessages(response_text, raise_exc)


def _install_fake_anthropic(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text, raise_exc)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── _pct ──────────────────────────────────────────────────────────────────────

def test_pct_none_returns_na():
    assert da._pct(None) == "N/A"


def test_pct_nan_returns_na():
    assert da._pct(float("nan")) == "N/A"


def test_pct_normal_float_signed_one_decimal():
    assert da._pct(3.14159) == "+3.1%"
    assert da._pct(-2.5) == "-2.5%"


# ─── build_debrief_package: guards / early returns ────────────────────────────

WEEK_ENDING = date(2026, 7, 26)  # week_start = 2026-07-20


def test_build_debrief_package_nan_spy_week_pct_coerced_to_none():
    package = da.build_debrief_package(
        WEEK_ENDING, None, None, None, spy_week_pct=float("nan"),
    )
    assert package["spy_pct"] is None


def test_build_debrief_package_none_snapshots_df_returns_default():
    package = da.build_debrief_package(WEEK_ENDING, None, None, None)
    assert package["has_snapshots"] is False
    assert package["days_available"] == 0
    assert package["performance_pct"] is None
    assert package["contributors"] == []
    assert package["detractors"] == []


def test_build_debrief_package_empty_snapshots_df_returns_default():
    package = da.build_debrief_package(WEEK_ENDING, pd.DataFrame(), None, None)
    assert package["has_snapshots"] is False


def test_build_debrief_package_fewer_than_5_days_returns_early():
    snap = pd.DataFrame([
        {"snapshot_date": "2026-07-20", "ticker": "AAA", "shares": 10.0, "close_price": 100.0},
        {"snapshot_date": "2026-07-21", "ticker": "AAA", "shares": 10.0, "close_price": 101.0},
    ])
    package = da.build_debrief_package(WEEK_ENDING, snap, None, None)
    assert package["days_available"] == 2
    assert package["has_snapshots"] is False


# ─── build_debrief_package: full 5-day happy path ─────────────────────────────

_DATES = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]


def _snap_rows():
    """AAA present on all 5 dates (drives days_available=5); other tickers only
    need start/end rows since only those two are read for P&L."""
    rows = [
        {"snapshot_date": d, "ticker": "AAA", "shares": 10.0, "close_price": 100.0 + i}
        for i, d in enumerate(_DATES)
    ]  # AAA: 100 -> 104, pnl = +40
    # Additional contributors (only start/end rows needed)
    rows += [
        {"snapshot_date": _DATES[0], "ticker": "DDD", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "DDD", "shares": 10.0, "close_price": 55.0},  # +50
        {"snapshot_date": _DATES[0], "ticker": "FFF", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "FFF", "shares": 10.0, "close_price": 52.0},  # +20
        {"snapshot_date": _DATES[0], "ticker": "GGG", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "GGG", "shares": 10.0, "close_price": 50.5},  # +5
    ]
    # Detractors
    rows += [
        {"snapshot_date": _DATES[0], "ticker": "BBB", "shares": 5.0, "close_price": 200.0},
        {"snapshot_date": _DATES[-1], "ticker": "BBB", "shares": 5.0, "close_price": 180.0},  # -100
        {"snapshot_date": _DATES[0], "ticker": "EEE", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "EEE", "shares": 10.0, "close_price": 47.0},  # -30
        {"snapshot_date": _DATES[0], "ticker": "HHH", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "HHH", "shares": 10.0, "close_price": 49.0},  # -10
        {"snapshot_date": _DATES[0], "ticker": "III", "shares": 10.0, "close_price": 50.0},
        {"snapshot_date": _DATES[-1], "ticker": "III", "shares": 10.0, "close_price": 49.5},  # -5
    ]
    # CCC sold this week -- present at start only, absent at end
    rows += [
        {"snapshot_date": _DATES[0], "ticker": "CCC", "shares": 8.0, "close_price": 30.0},
    ]
    return pd.DataFrame(rows)


def _trades_df(extra_rows=None):
    rows = [
        {"ticker": "CCC", "action": "SELL", "traded_at": "2026-07-22T10:00:00"},
    ]
    if extra_rows:
        rows += extra_rows
    return pd.DataFrame(rows)


def test_build_debrief_package_happy_path_performance_and_alpha():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(), spy_week_pct=1.0)
    assert package["has_snapshots"] is True
    assert package["days_available"] == 5
    # start_val / end_val computed from AAA(100->104)+DDD+FFF+GGG+BBB+EEE+HHH+III+CCC(start only)
    start_val = 10 * 100 + 10 * 50 + 10 * 50 + 10 * 50 + 5 * 200 + 10 * 50 + 10 * 50 + 10 * 50 + 8 * 30
    end_val = 10 * 104 + 10 * 55 + 10 * 52 + 10 * 50.5 + 5 * 180 + 10 * 47 + 10 * 49 + 10 * 49.5
    expected_perf = round((end_val - start_val) / start_val * 100, 2)
    assert package["performance_pct"] == expected_perf
    assert package["alpha_pct"] == round(expected_perf - 1.0, 2)
    assert package["week_start_value"] == start_val
    assert package["week_end_value"] == end_val


def test_build_debrief_package_week_had_trades_true_when_trade_inside_window():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    assert package["week_had_trades"] is True  # CCC sell on 07-22 is inside [07-20, 07-24]


def test_build_debrief_package_week_had_trades_false_when_trade_outside_window():
    trades = pd.DataFrame([
        {"ticker": "ZZZ", "action": "BUY", "traded_at": "2026-06-01T10:00:00"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, trades)
    assert package["week_had_trades"] is False


def test_build_debrief_package_contributors_sorted_descending_capped_at_3():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    tickers = [c["ticker"] for c in package["contributors"]]
    assert tickers == ["DDD", "AAA", "FFF"]  # +50, +40, +20 -> sorted desc; GGG(+5) excluded (capped 3)


def test_build_debrief_package_detractors_sorted_ascending_most_negative_first_capped_at_3():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    tickers = [d["ticker"] for d in package["detractors"]]
    assert tickers == ["BBB", "EEE", "HHH"]  # -100, -30, -10 -- III(-5) excluded (capped 3)


def test_build_debrief_package_closed_position_excluded_from_contributors_detractors():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    assert "CCC" not in [c["ticker"] for c in package["contributors"]]
    assert "CCC" not in [d["ticker"] for d in package["detractors"]]
    assert package["closed_positions"] == ["CCC"]


def test_build_debrief_package_recs_surfaced_dedup_times_surfaced_and_dominant_verdict():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "AAA", "rec_date": "2026-07-22", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "AAA", "rec_date": "2026-07-23", "rec_type": "new_pick", "verdict": "Weakening"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, _trades_df())
    entry = next(r for r in package["recs_surfaced"] if r["ticker"] == "AAA")
    assert entry["times_surfaced"] == 3
    assert entry["verdict"] == "Confirmed"  # 2 vs 1 -- dominant by count


def test_build_debrief_package_recs_surfaced_acted_flag_from_trades():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "DDD", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
    ])
    trades = _trades_df(extra_rows=[
        {"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T10:00:00"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, trades)
    aaa = next(r for r in package["recs_surfaced"] if r["ticker"] == "AAA")
    ddd = next(r for r in package["recs_surfaced"] if r["ticker"] == "DDD")
    assert aaa["acted"] is True
    assert ddd["acted"] is False


def test_build_debrief_package_end_week_pct_none_for_closed_position():
    recs_df = pd.DataFrame([
        {"ticker": "CCC", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, _trades_df())
    ccc = next(r for r in package["recs_surfaced"] if r["ticker"] == "CCC")
    assert ccc["end_week_pct"] is None  # never renders the -100% closure artifact


def test_build_debrief_package_rec_type_filtered_to_new_pick_and_add_winner():
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_date": "2026-07-20", "rec_type": "new_pick", "verdict": "Confirmed"},
        {"ticker": "DDD", "rec_date": "2026-07-20", "rec_type": "buy_candidate", "verdict": "Confirmed"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, _trades_df())
    tickers = [r["ticker"] for r in package["recs_surfaced"]]
    assert "AAA" in tickers
    assert "DDD" not in tickers


# ─── build_debrief_package: protective_signals (WATCH/TRIM/EXIT) ─────────────
# 2026-08-24 review finding: this block (added for the symmetric
# WATCH/TRIM/EXIT debrief narration) had zero test coverage. Its tier-
# escalation merge logic is exactly the kind of stateful accumulation that
# regresses silently, so these tests target that logic directly rather than
# the block's existence.

def _exit_sig(ticker, signal_date, signal_type, pnl_pct=-5.0, dd_from_peak_pct=-8.0):
    return {"ticker": ticker, "signal_date": signal_date, "signal_type": signal_type,
            "pnl_pct": pnl_pct, "dd_from_peak_pct": dd_from_peak_pct}


def test_build_debrief_package_protective_signals_escalates_to_most_severe_tier_seen():
    """Rows arrive WATCH-then-EXIT (ascending severity) for the same ticker
    across two different days — the final tier must be the most severe seen
    all week (EXIT), not just whichever row happened to be seen last if the
    rows had instead arrived in descending order."""
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-20", "WATCH"),
        _exit_sig("AAA", "2026-07-22", "EXIT"),
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(),
                                        exit_signals_df=sig_df)
    entry = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    assert entry["tier"] == "EXIT"
    assert entry["times_surfaced"] == 2


def test_build_debrief_package_protective_signals_does_not_downgrade_from_a_later_lower_tier():
    """Rows arrive EXIT-then-WATCH (descending severity) — the lower-severity
    later row must NOT downgrade the tier back down. Proves the merge compares
    against the running max, not against whichever row is seen last."""
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-20", "EXIT"),
        _exit_sig("AAA", "2026-07-22", "WATCH"),
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(),
                                        exit_signals_df=sig_df)
    entry = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    assert entry["tier"] == "EXIT"


def test_build_debrief_package_protective_signals_excludes_a_closed_position():
    """CCC is sold and fully closed this week per _snap_rows()/_trades_df()
    (present at start, absent at end, with a matching SELL trade) — it must
    be excluded from protective_signals even though it carries a real EXIT
    signal, because closed_positions already narrates it and a second bullet
    from this block would conflict with that wording."""
    sig_df = pd.DataFrame([_exit_sig("CCC", "2026-07-21", "EXIT")])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(),
                                        exit_signals_df=sig_df)
    assert "CCC" in package["closed_positions"]  # the fixture's own precondition
    assert "CCC" not in [s["ticker"] for s in package["protective_signals"]]


def test_build_debrief_package_protective_signals_sold_flag_true_when_sell_trade_coincides():
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-21", "TRIM"),
        _exit_sig("DDD", "2026-07-21", "TRIM"),
    ])
    trades = _trades_df(extra_rows=[
        {"ticker": "AAA", "action": "SELL", "traded_at": "2026-07-21T10:00:00"},
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, trades,
                                        exit_signals_df=sig_df)
    aaa = next(s for s in package["protective_signals"] if s["ticker"] == "AAA")
    ddd = next(s for s in package["protective_signals"] if s["ticker"] == "DDD")
    assert aaa["sold"] is True
    assert ddd["sold"] is False


def test_build_debrief_package_protective_signals_none_when_exit_signals_df_is_none():
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(),
                                        exit_signals_df=None)
    assert package["protective_signals"] == []


def test_build_debrief_package_protective_signals_empty_when_no_watch_trim_exit_in_window():
    """A signal_type outside {WATCH, TRIM, EXIT} (or outside the week window)
    must not surface."""
    sig_df = pd.DataFrame([
        _exit_sig("AAA", "2026-07-21", "HOLD"),          # not a protective tier
        _exit_sig("DDD", "2026-06-01", "EXIT"),           # outside the week window
    ])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df(),
                                        exit_signals_df=sig_df)
    assert package["protective_signals"] == []


# ─── build_debrief_package: behavioral / decision-quality enrichment ─────────

def test_build_debrief_package_behavioral_enrichment_populates_package(monkeypatch):
    fake_momentum = {"high": {"n": 10, "n_acted": 5, "action_rate": 50.0},
                      "low": {"n": 10, "n_acted": 2, "action_rate": 20.0},
                      "delta_pp": 30.0, "direction": "chases"}
    fake_conviction = {"strong_buy": {"n": 10, "n_acted": 8, "action_rate": 80.0},
                        "buy": {"n": 10, "n_acted": 3, "action_rate": 30.0},
                        "delta_pp": 50.0}
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.momentum_recency_pattern",
                        lambda matched, min_n: fake_momentum)
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.conviction_tier_pattern",
                        lambda matched, strong_buy_floor, min_n: fake_conviction)
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_type": "new_pick", "momentum_score": 80.0, "composite_score": 78.0},
    ])
    trades = _trades_df(extra_rows=[{"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T10:00:00"}])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, trades, all_recs_df=recs_df)
    assert package["behavioral"]["momentum"] == fake_momentum
    assert package["behavioral"]["conviction"] == fake_conviction


def test_build_debrief_package_behavioral_enrichment_exception_caught_stays_default(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("stock_analyzer.behavioral_fingerprint.momentum_recency_pattern", _boom)
    recs_df = pd.DataFrame([
        {"ticker": "AAA", "rec_type": "new_pick", "momentum_score": 80.0, "composite_score": 78.0},
    ])
    trades = _trades_df(extra_rows=[{"ticker": "AAA", "action": "BUY", "traded_at": "2026-07-21T10:00:00"}])
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), recs_df, trades, all_recs_df=recs_df)
    assert package["behavioral"] == {}


def test_build_debrief_package_decision_quality_populates_current_month(monkeypatch):
    cur_month = str(WEEK_ENDING)[:7]
    fake_grades = [{
        "month_str": cur_month, "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    }]
    monkeypatch.setattr("stock_analyzer.decision_quality.build_monthly_grades",
                        lambda trades_df, spy_monthly_returns=None: fake_grades)
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    dq = package["decision_quality_month"]
    assert dq == {
        "month_str": cur_month, "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    }


def test_build_debrief_package_decision_quality_exception_caught_stays_none(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("stock_analyzer.decision_quality.build_monthly_grades", _boom)
    package = da.build_debrief_package(WEEK_ENDING, _snap_rows(), None, _trades_df())
    assert package["decision_quality_month"] is None


# ─── _format_prompt ────────────────────────────────────────────────────────────

def _base_package(**overrides):
    package = {
        "week_start": "2026-07-20", "week_ending": "2026-07-26",
        "performance_pct": None, "spy_pct": None, "alpha_pct": None,
        "week_start_value": None, "week_end_value": None,
        "week_had_trades": False,
        "contributors": [], "detractors": [],
        "recs_surfaced": [], "closed_positions": [], "broken_theses": [],
        "behavioral": {}, "decision_quality_month": None,
    }
    package.update(overrides)
    return package


def test_format_prompt_insufficient_data_fallback_line():
    text = da._format_prompt(_base_package())
    assert "insufficient snapshot data" in text


def test_format_prompt_week_had_trades_caveat_only_when_true():
    package = _base_package(performance_pct=2.0, week_start_value=1000.0, week_end_value=1020.0)
    text_no_trades = da._format_prompt(package)
    assert "may include position-size effects" not in text_no_trades

    package["week_had_trades"] = True
    text_trades = da._format_prompt(package)
    assert "may include position-size effects" in text_trades


def test_format_prompt_contributors_detractors_format_dollars_and_pct():
    package = _base_package(
        contributors=[{"ticker": "AAA", "pnl": 100.0, "pct": 5.0}],
        detractors=[{"ticker": "BBB", "pnl": -50.0, "pct": -2.5}],
    )
    text = da._format_prompt(package)
    assert "AAA: +$100" in text
    assert "(+5.0%)" in text
    assert "BBB: -$50" in text
    assert "(-2.5%)" in text


def test_format_prompt_recs_surfaced_times_suffix_only_when_greater_than_1():
    package = _base_package(recs_surfaced=[
        {"ticker": "AAA", "rec_type": "new_pick", "acted": True, "verdict": "Confirmed",
         "times_surfaced": 1, "end_week_pct": None},
        {"ticker": "BBB", "rec_type": "new_pick", "acted": False, "verdict": "Confirmed",
         "times_surfaced": 3, "end_week_pct": None},
    ])
    text = da._format_prompt(package)
    assert "AAA" in text and "surfaced" not in text.split("BBB")[0].split("AAA")[-1]
    assert "(surfaced 3× this week)" in text


def test_format_prompt_closed_positions_note_only_when_nonempty():
    text_empty = da._format_prompt(_base_package())
    assert "fully closed" not in text_empty

    text_present = da._format_prompt(_base_package(closed_positions=["CCC"]))
    assert "Positions fully closed (sold) this week: CCC" in text_present


def test_format_prompt_broken_theses_line_only_when_nonempty():
    text_empty = da._format_prompt(_base_package())
    assert "BROKEN thesis" not in text_empty

    text_present = da._format_prompt(_base_package(broken_theses=["XYZ"]))
    assert "Positions with BROKEN thesis: XYZ" in text_present


def test_format_prompt_momentum_block_only_when_truthy():
    text_empty = da._format_prompt(_base_package())
    assert "momentum tendency" not in text_empty

    package = _base_package(behavioral={"momentum": {
        "high": {"n": 10, "n_acted": 5, "action_rate": 50.0},
        "low": {"n": 10, "n_acted": 2, "action_rate": 20.0},
        "delta_pp": 30.0, "direction": "chases",
    }})
    text_present = da._format_prompt(package)
    assert "momentum tendency" in text_present
    assert "High-momentum signals: 10 surfaced, 5 acted on (50% rate)" in text_present


def test_format_prompt_conviction_block_only_when_truthy():
    text_empty = da._format_prompt(_base_package())
    assert "conviction tier" not in text_empty

    package = _base_package(behavioral={"conviction": {
        "strong_buy": {"n": 10, "n_acted": 8, "action_rate": 80.0},
        "buy": {"n": 10, "n_acted": 3, "action_rate": 30.0},
        "delta_pp": 50.0,
    }})
    text_present = da._format_prompt(package)
    assert "conviction tier" in text_present
    assert "Strong Buy signals: 10 surfaced, 8 acted on (80% rate)" in text_present


def test_format_prompt_decision_quality_block_only_when_truthy():
    text_empty = da._format_prompt(_base_package())
    assert "Decision quality" not in text_empty

    package = _base_package(decision_quality_month={
        "month_str": "2026-07", "grade_letter": "B", "composite_score": 72.0,
        "trade_count": 8, "win_rate": 0.6, "profit_factor": 1.8,
    })
    text_present = da._format_prompt(package)
    assert "Decision quality — 2026-07 month to date (8 trades):" in text_present
    assert "Grade: B (composite 72/100)" in text_present
    assert "Win rate: 60%" in text_present
    assert "Profit factor: 1.80" in text_present


# ─── _parse_response ──────────────────────────────────────────────────────────

def test_parse_response_all_4_sections_split_correctly():
    text = (
        "**What happened**\nFacts text.\n\n"
        "**Decisions you made**\nDecisions text.\n\n"
        "**Patterns this week**\nPatterns text.\n\n"
        "**One thing to watch**\nWatch text."
    )
    sections = da._parse_response(text)
    assert sections["section_facts"] == "Facts text."
    assert sections["section_decisions"] == "Decisions text."
    assert sections["section_patterns"] == "Patterns text."
    assert sections["section_watchnext"] == "Watch text."


def test_parse_response_missing_section_stays_empty():
    text = "**What happened**\nOnly this section."
    sections = da._parse_response(text)
    assert sections["section_facts"] == "Only this section."
    assert sections["section_decisions"] == ""
    assert sections["section_patterns"] == ""
    assert sections["section_watchnext"] == ""


def test_parse_response_discards_text_before_first_header():
    text = "Preamble that should be discarded.\n**What happened**\nReal text."
    sections = da._parse_response(text)
    assert "Preamble" not in sections["section_facts"]
    assert sections["section_facts"] == "Real text."


# ─── generate_debrief ──────────────────────────────────────────────────────────

def test_generate_debrief_no_api_key_returns_none():
    assert da.generate_debrief({"has_snapshots": True}, api_key="") is None


def test_generate_debrief_has_snapshots_false_returns_none():
    assert da.generate_debrief({"has_snapshots": False}, api_key="fake-key") is None


def test_generate_debrief_valid_response_round_trip():
    package = _base_package(performance_pct=2.0, spy_pct=1.0, alpha_pct=1.0)
    package["has_snapshots"] = True
    raw = (
        "**What happened**\nFacts text.\n\n"
        "**Decisions you made**\nDecisions text.\n\n"
        "**Patterns this week**\nPatterns text.\n\n"
        "**One thing to watch**\nWatch text."
    )
    _install_fake_anthropic(raw)
    result = da.generate_debrief(package, api_key="fake-key")
    assert result["week_ending"] == package["week_ending"]
    assert result["performance_pct"] == 2.0
    assert result["spy_pct"] == 1.0
    assert result["alpha_pct"] == 1.0
    assert result["section_facts"] == "Facts text."
    assert result["section_decisions"] == "Decisions text."
    assert result["section_patterns"] == "Patterns text."
    assert result["section_watchnext"] == "Watch text."
    assert result["email_sent"] is False
    assert "generated_at" in result


# ─── classify_snapshot_read ──────────────────────────────────────────────────
# 2026-08-30: extracted from app.py's "Generate Now" button so the outage-vs-
# insufficient-vs-ready decision is unit-tested rather than only reachable
# via a screenshot (app.py has no test coverage of its own).

def test_classify_snapshot_read_outage_when_read_is_none():
    status, days = da.classify_snapshot_read(None, min_days=5)
    assert status == "outage"
    assert days == 0


def test_classify_snapshot_read_insufficient_when_genuinely_few_days():
    df = pd.DataFrame({"snapshot_date": ["2026-08-24", "2026-08-25", "2026-08-26"]})
    status, days = da.classify_snapshot_read(df, min_days=5)
    assert status == "insufficient"
    assert days == 3


def test_classify_snapshot_read_insufficient_when_genuinely_empty():
    status, days = da.classify_snapshot_read(pd.DataFrame(columns=["snapshot_date"]), min_days=5)
    assert status == "insufficient"
    assert days == 0


def test_classify_snapshot_read_ready_when_enough_distinct_days():
    df = pd.DataFrame({"snapshot_date": [
        "2026-08-24", "2026-08-24", "2026-08-25", "2026-08-26",
        "2026-08-27", "2026-08-28",
    ]})
    status, days = da.classify_snapshot_read(df, min_days=5)
    assert status == "ready"
    assert days == 5  # distinct dates, not row count


def test_classify_snapshot_read_boundary_exactly_min_days_is_ready():
    df = pd.DataFrame({"snapshot_date": [f"2026-08-{d:02d}" for d in range(24, 29)]})
    status, days = da.classify_snapshot_read(df, min_days=5)
    assert status == "ready"
    assert days == 5


def test_classify_snapshot_read_outage_never_confused_with_insufficient():
    """The failure mode this function exists to close: None and a genuinely
    empty DataFrame must classify DIFFERENTLY."""
    outage_status, _ = da.classify_snapshot_read(None, min_days=5)
    empty_status, _ = da.classify_snapshot_read(pd.DataFrame(columns=["snapshot_date"]), min_days=5)
    assert outage_status != empty_status
