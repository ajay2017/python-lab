"""Regression tests for stock_analyzer/comparison.py — the 2-ticker
side-by-side Compare page engine: per-row winner picking, formatting
helpers, the composite-first/sub-factor-tiebreak verdict, and the
portfolio-fit (already-held / sector-concentration) notes. Pure logic
(dict/DataFrame inputs, no I/O). See docs/plans/test-automation.md.
"""
import pandas as pd
import pytest

from stock_analyzer import comparison as cmp
from stock_analyzer.constants import SECTOR_CEILING, SECTOR_ELEVATED


# ── _f ────────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert cmp._f(None) is None
    assert cmp._f(None, default=5) == 5


def test_f_nan_returns_default():
    assert cmp._f(float("nan"), default=-1) == -1


def test_f_unparseable_returns_default():
    assert cmp._f("bad", default=2) == 2


def test_f_parses_valid_value():
    assert cmp._f("3.5") == 3.5


# ── _winner ──────────────────────────────────────────────────────────────

def test_winner_both_none_returns_none():
    assert cmp._winner(None, None) is None


def test_winner_a_none_b_wins():
    assert cmp._winner(None, 5.0) == "b"


def test_winner_b_none_a_wins():
    assert cmp._winner(5.0, None) == "a"


def test_winner_within_tolerance_is_tie():
    assert cmp._winner(10.0, 11.0, tolerance=2.0) == "tie"


def test_winner_beyond_tolerance_higher_better_a_wins():
    assert cmp._winner(20.0, 10.0, higher_better=True, tolerance=0.0) == "a"


def test_winner_beyond_tolerance_higher_better_b_wins():
    assert cmp._winner(10.0, 20.0, higher_better=True, tolerance=0.0) == "b"


def test_winner_lower_better_smaller_value_wins():
    assert cmp._winner(10.0, 20.0, higher_better=False, tolerance=0.0) == "a"


def test_winner_lower_better_beyond_tolerance_b_wins():
    assert cmp._winner(20.0, 10.0, higher_better=False, tolerance=0.0) == "b"


def test_winner_exact_equal_is_tie_even_with_zero_tolerance():
    assert cmp._winner(10.0, 10.0, tolerance=0.0) == "tie"


# ── formatting helpers ──────────────────────────────────────────────────────

def test_fmt_pct_none_is_dash():
    assert cmp._fmt_pct(None) == "—"


def test_fmt_pct_formats_as_percent_with_sign():
    assert cmp._fmt_pct(0.1234) == "+12.3%"
    assert cmp._fmt_pct(-0.05) == "-5.0%"


def test_fmt_money_none_is_dash():
    assert cmp._fmt_money(None) == "—"


@pytest.mark.parametrize("val,expected", [
    (2.5e12, "$2.50T"),
    (3.4e9, "$3.4B"),
    (150e6, "$150M"),
    (5000.0, "$5,000"),
])
def test_fmt_money_tiers(val, expected):
    assert cmp._fmt_money(val) == expected


def test_fmt_num_none_is_dash():
    assert cmp._fmt_num(None) == "—"


def test_fmt_num_default_format():
    assert cmp._fmt_num(3.14159) == "3.14"


def test_fmt_num_custom_format():
    assert cmp._fmt_num(42.0, "{:.0f}") == "42"


# ── _signal_winner ──────────────────────────────────────────────────────────

def test_signal_winner_both_unknown_returns_none():
    assert cmp._signal_winner("Nonsense", "Whatever") is None


def test_signal_winner_higher_rank_wins():
    assert cmp._signal_winner("Strong Buy", "Hold") == "a"
    assert cmp._signal_winner("Hold", "Strong Buy") == "b"


def test_signal_winner_tie_same_rank():
    assert cmp._signal_winner("Buy", "Buy") == "tie"


def test_signal_winner_avoid_and_strong_sell_tie_at_rank_0():
    assert cmp._signal_winner("Avoid", "Strong Sell") == "tie"


def test_signal_winner_known_beats_unknown():
    # Unknown label ranks -1, below even Strong Sell's rank 0.
    assert cmp._signal_winner("Strong Sell", "Nonsense") == "a"


def test_signal_winner_none_label_treated_as_unknown():
    assert cmp._signal_winner(None, "Hold") == "b"


# ── _trend_winner ────────────────────────────────────────────────────────────

def test_trend_winner_both_unknown_returns_none():
    assert cmp._trend_winner("Nonsense", "Whatever") is None


def test_trend_winner_uptrend_beats_downtrend():
    assert cmp._trend_winner("Strong Uptrend", "Strong Downtrend") == "a"


def test_trend_winner_sideways_and_mixed_tie():
    assert cmp._trend_winner("Sideways", "Mixed") == "tie"


def test_trend_winner_known_beats_unknown():
    assert cmp._trend_winner("Weak", "Nonsense") == "a"


# ── build_comparison ─────────────────────────────────────────────────────────

def _bundle(total=60.0, label="Hold", t_score=50.0, s_score=50.0, sector="Tech",
            fcf_yield=None, revenue_growth=None, profit_margins=None,
            debt_to_equity=None, forward_pe=None, beta=None, sharpe=None,
            current_price=None, stop=None, entry_lo=None, entry_hi=None,
            target=None, name=None):
    return {
        "name": name, "total": total, "rec": {"label": label},
        "sector": sector, "industry": "Software", "market_cap": 1e9,
        "current_price": current_price, "t_score": t_score,
        "t_signals": {"RSI": 50.0, "Trend": "Sideways"},
        "bq_score": 60.0, "val_score": 55.0, "s_score": s_score,
        "avg_sent": 0.1,
        "financials": {
            "fcf_yield": fcf_yield, "revenue_growth": revenue_growth,
            "profit_margins": profit_margins, "debt_to_equity": debt_to_equity,
            "forward_pe": forward_pe,
        },
        "revisions": {"net": 0},
        "risk_metrics": {"beta": beta, "sharpe": sharpe, "ann_volatility": 20.0,
                          "max_drawdown": -10.0},
        "stop": stop, "entry_lo": entry_lo, "entry_hi": entry_hi,
        "targets": {"base": target}, "earnings": None,
    }


def test_build_comparison_returns_expected_top_level_keys():
    result = cmp.build_comparison(_bundle(), _bundle(), "aapl", "msft")
    assert result["ticker_a"] == "AAPL"
    assert result["ticker_b"] == "MSFT"
    assert "sections" in result
    assert "verdict" in result
    assert "portfolio_fit" in result


def test_build_comparison_section_names_in_order():
    result = cmp.build_comparison(_bundle(), _bundle(), "A", "B")
    names = [s["name"] for s in result["sections"]]
    assert names == ["Headline", "Overview", "Technicals", "Business Quality",
                      "Valuation", "Sentiment & Analyst", "Risk", "Setup"]


def test_build_comparison_headline_composite_winner():
    result = cmp.build_comparison(_bundle(total=80.0), _bundle(total=50.0), "A", "B")
    headline = result["sections"][0]["rows"]
    composite_row = next(r for r in headline if r["label"] == "Composite Score")
    assert composite_row["winner"] == "a"


def test_build_comparison_uses_name_fallback_to_ticker():
    result = cmp.build_comparison(_bundle(name=None), _bundle(name="Microsoft"), "aapl", "msft")
    assert result["name_a"] == "aapl"
    assert result["name_b"] == "Microsoft"


def test_build_comparison_setup_rr_ratio_winner():
    a = _bundle(current_price=100.0, stop=90.0, target=120.0)   # RR = 20/10 = 2.0
    b = _bundle(current_price=100.0, stop=95.0, target=105.0)   # RR = 5/5 = 1.0
    result = cmp.build_comparison(a, b, "A", "B")
    setup = next(s for s in result["sections"] if s["name"] == "Setup")
    rr_row = next(r for r in setup["rows"] if r["label"] == "R:R Ratio")
    assert rr_row["winner"] == "a"
    assert rr_row["value_a"] == "2.0:1"


def test_build_comparison_rr_none_when_price_below_stop():
    a = _bundle(current_price=80.0, stop=90.0, target=120.0)  # price <= stop -> invalid
    b = _bundle(current_price=100.0, stop=90.0, target=110.0)
    result = cmp.build_comparison(a, b, "A", "B")
    setup = next(s for s in result["sections"] if s["name"] == "Setup")
    rr_row = next(r for r in setup["rows"] if r["label"] == "R:R Ratio")
    assert rr_row["value_a"] == "—"


def test_build_comparison_business_quality_debt_to_equity_lower_is_better():
    a = _bundle(debt_to_equity=20.0)
    b = _bundle(debt_to_equity=80.0)
    result = cmp.build_comparison(a, b, "A", "B")
    bq = next(s for s in result["sections"] if s["name"] == "Business Quality")
    row = next(r for r in bq["rows"] if r["label"] == "Debt/Equity")
    assert row["winner"] == "a"


def test_build_comparison_risk_beta_lower_is_better():
    a = _bundle(beta=0.8)
    b = _bundle(beta=1.5)
    result = cmp.build_comparison(a, b, "A", "B")
    risk = next(s for s in result["sections"] if s["name"] == "Risk")
    row = next(r for r in risk["rows"] if r["label"] == "Beta")
    assert row["winner"] == "a"


def test_build_comparison_passes_port_df_to_portfolio_fit():
    port_df = pd.DataFrame({"Ticker": ["A"], "Weight (%)": [10.0], "Market Value": [1000.0],
                             "Sector": ["Tech"]})
    result = cmp.build_comparison(_bundle(), _bundle(), "A", "B", port_df=port_df)
    assert "Already held" in result["portfolio_fit"]["a"]


# ── _compute_verdict ─────────────────────────────────────────────────────────

def test_compute_verdict_tie_when_gap_small_no_subfactors():
    v = cmp._compute_verdict(
        {"total": 60.0}, {"total": 61.0}, "AAPL", "MSFT", {}, {}, {}, {}, None,
    )
    assert v["preferred"] == "tie"
    assert v["confidence"] == "low"
    assert "portfolio fit" in v["reason"]


def test_compute_verdict_tie_with_subfactor_tiebreakers():
    fin_a = {"fcf_yield": 5.0}
    fin_b = {"fcf_yield": 3.0}
    v = cmp._compute_verdict(
        {"total": 60.0}, {"total": 61.0}, "AAPL", "MSFT", fin_a, fin_b, {}, {}, None,
    )
    assert v["preferred"] == "tie"
    assert "better FCF yield" in v["reason"]
    assert "AAPL" in v["reason"]


def test_compute_verdict_clear_winner_medium_confidence():
    v = cmp._compute_verdict(
        {"total": 70.0}, {"total": 65.0}, "AAPL", "MSFT", {}, {}, {}, {}, None,
    )
    assert v["preferred"] == "a"
    assert v["confidence"] == "medium"
    assert "AAPL" in v["reason"]


def test_compute_verdict_clear_winner_high_confidence_at_gap_10():
    v = cmp._compute_verdict(
        {"total": 75.0}, {"total": 65.0}, "AAPL", "MSFT", {}, {}, {}, {}, None,
    )
    assert v["confidence"] == "high"


def test_compute_verdict_b_preferred_when_higher():
    v = cmp._compute_verdict(
        {"total": 50.0}, {"total": 70.0}, "AAPL", "MSFT", {}, {}, {}, {}, None,
    )
    assert v["preferred"] == "b"
    assert "MSFT" in v["reason"]


def test_compute_verdict_reasons_include_beta_and_sharpe():
    rm_a = {"beta": 0.8, "sharpe": 1.5}
    rm_b = {"beta": 1.2, "sharpe": 1.0}
    v = cmp._compute_verdict(
        {"total": 70.0}, {"total": 55.0}, "AAPL", "MSFT", {}, {}, rm_a, rm_b, None,
    )
    assert "lower beta" in v["reason"]
    assert "stronger Sharpe" in v["reason"]


def test_compute_verdict_small_subfactor_deltas_not_cited():
    fin_a = {"fcf_yield": 5.0}
    fin_b = {"fcf_yield": 5.1}  # delta 0.1, below the 0.5 threshold
    v = cmp._compute_verdict(
        {"total": 70.0}, {"total": 55.0}, "AAPL", "MSFT", fin_a, fin_b, {}, {}, None,
    )
    assert "FCF yield" not in v["reason"]


# ── _portfolio_fit ───────────────────────────────────────────────────────────

def test_portfolio_fit_none_port_df_returns_empty_notes():
    assert cmp._portfolio_fit({}, {}, "A", "B", None) == {"a": "", "b": ""}


def test_portfolio_fit_empty_port_df_returns_empty_notes():
    assert cmp._portfolio_fit({}, {}, "A", "B", pd.DataFrame()) == {"a": "", "b": ""}


def test_portfolio_fit_already_held_note():
    port_df = pd.DataFrame({"Ticker": ["AAPL"], "Weight (%)": [12.5], "Market Value": [1000.0]})
    result = cmp._portfolio_fit({}, {}, "AAPL", "MSFT", port_df)
    assert "12.5%" in result["a"]
    assert result["b"] == ""


def test_portfolio_fit_sector_ceiling_breached():
    port_df = pd.DataFrame({
        "Ticker": ["X", "Y"], "Weight (%)": [20.0, 20.0], "Market Value": [1000.0, 1000.0],
        "Sector": ["Tech", "Tech"], "Gate Weight (%)": [20.0, 20.0],
    })
    result = cmp._portfolio_fit({"sector": "Tech"}, {"sector": "Energy"}, "AAPL", "XOM", port_df)
    assert "hard ceiling" in result["a"]
    assert str(SECTOR_CEILING)[:2] in result["a"]
    assert result["b"] == ""


def test_portfolio_fit_sector_elevated_but_not_breached():
    port_df = pd.DataFrame({
        "Ticker": ["X"], "Weight (%)": [28.0], "Market Value": [1000.0],
        "Sector": ["Tech"], "Gate Weight (%)": [28.0],
    })
    result = cmp._portfolio_fit({"sector": "Tech"}, {}, "AAPL", "MSFT", port_df)
    assert "elevated" in result["a"]
    assert "hard ceiling" not in result["a"]


def test_portfolio_fit_sector_below_elevated_no_note():
    port_df = pd.DataFrame({
        "Ticker": ["X"], "Weight (%)": [10.0], "Market Value": [1000.0],
        "Sector": ["Tech"], "Gate Weight (%)": [10.0],
    })
    result = cmp._portfolio_fit({"sector": "Tech"}, {}, "AAPL", "MSFT", port_df)
    assert result["a"] == ""


def test_portfolio_fit_falls_back_to_weight_column_without_gate_weight():
    port_df = pd.DataFrame({
        "Ticker": ["X"], "Weight (%)": [40.0], "Market Value": [1000.0], "Sector": ["Tech"],
    })
    result = cmp._portfolio_fit({"sector": "Tech"}, {}, "AAPL", "MSFT", port_df)
    assert "hard ceiling" in result["a"]


def test_portfolio_fit_combines_held_and_sector_notes():
    port_df = pd.DataFrame({
        "Ticker": ["AAPL"], "Weight (%)": [40.0], "Market Value": [1000.0], "Sector": ["Tech"],
    })
    result = cmp._portfolio_fit({"sector": "Tech"}, {}, "AAPL", "MSFT", port_df)
    assert "Already held" in result["a"]
    assert "hard ceiling" in result["a"]
    assert " · " in result["a"]
