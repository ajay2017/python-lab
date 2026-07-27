"""Regression tests for stock_analyzer/daily_briefing.py's Buy Candidates
suppression funnel and the Review Before Close weak-large-position flag.

_buy_candidates()'s add-to-winner block is the "mirror image" of risk_advisor's
single_name_concentration rec: risk_advisor flags an overweight STRONG
position (score >= WEAK_CONVICTION_SCORE) as a structural risk-limit issue,
while _review_list's weak-large flag catches an overweight WEAK position
(score < WEAK_CONVICTION_SCORE) as a conviction issue -- the two surfaces
partition cleanly on the WEAK_CONVICTION_SCORE boundary and must never
double-fire on the same ticker. These tests pin each suppression path in the
add-to-winner funnel (act-today, cooldown, risk-trim conflict, single-name
ceiling, drift-trim, deterioration WATCH) individually, since a refactor that
accidentally drops just one of six independent `continue` guards would be
invisible without a test targeting that exact guard.
"""
from datetime import date

from stock_analyzer.constants import (
    ADD_WINNER_COOLDOWN_DAYS,
    ADD_WINNER_MIN_GAP_PCT,
    COMPOSITE_BUY,
    LARGE_POSITION_WEIGHT_PCT,
    SINGLE_NAME_CEILING,
    WEAK_CONVICTION_SCORE,
)
from stock_analyzer.daily_briefing import (
    _buy_candidates,
    _cross_reference,
    _recently_added,
    _review_list,
    _trim_targets,
)
from tests.conftest import find_item, make_port_df

_TODAY = date(2026, 7, 27)


# ── _trim_targets ─────────────────────────────────────────────────────────────

def test_trim_targets_empty_or_none_returns_empty():
    assert _trim_targets(None) == {}
    assert _trim_targets([]) == {}


def test_trim_targets_only_beta_and_sharpe_types_qualify():
    recs = [
        {"priority": "HIGH", "type": "beta", "title": "Beta rec", "root_tickers": [{"ticker": "AAA"}]},
        {"priority": "HIGH", "type": "volatility", "title": "Vol rec", "root_tickers": [{"ticker": "BBB"}]},
        {"priority": "HIGH", "type": "drawdown", "title": "DD rec", "root_tickers": [{"ticker": "CCC"}]},
    ]
    targets = _trim_targets(recs)
    assert set(targets.keys()) == {"AAA"}


def test_trim_targets_excludes_low_and_ok_priority():
    recs = [
        {"priority": "OK", "type": "beta", "title": "x", "root_tickers": [{"ticker": "AAA"}]},
        {"priority": "LOW", "type": "sharpe", "title": "x", "root_tickers": [{"ticker": "BBB"}]},
    ]
    assert _trim_targets(recs) == {}


def test_trim_targets_keys_are_uppercased():
    recs = [{"priority": "MEDIUM", "type": "sharpe", "title": "x", "root_tickers": [{"ticker": "aaa"}]}]
    targets = _trim_targets(recs)
    assert "AAA" in targets
    assert targets["AAA"]["reason"] == "sharpe"


# ── _recently_added ───────────────────────────────────────────────────────────

def test_recently_added_false_with_no_held_data_entry():
    assert _recently_added("AAA", {}) is False


def test_recently_added_false_when_days_since_last_buy_missing():
    assert _recently_added("AAA", {"AAA": {}}) is False


def test_recently_added_true_within_cooldown():
    assert _recently_added("AAA", {"AAA": {"days_since_last_buy": 3}}) is True


def test_recently_added_false_at_or_past_cooldown():
    assert _recently_added("AAA", {"AAA": {"days_since_last_buy": ADD_WINNER_COOLDOWN_DAYS}}) is False


# ── _buy_candidates: scanner picks ────────────────────────────────────────────

def _scanner_df(rows):
    import pandas as pd
    defaults = dict(score=70.0, price=50.0, signal="Buy", sector="Healthcare", rsi=60.0, mom_1m=5.0, trend="Up")
    filled = [{**defaults, **r} for r in rows]
    return pd.DataFrame([
        {
            "Ticker": r["ticker"], "Score": r["score"], "Price": r["price"],
            "Signal": r["signal"], "Sector": r["sector"], "RSI": r["rsi"],
            "1M Momentum": r["mom_1m"], "Trend": r["trend"],
        }
        for r in filled
    ])


def test_scanner_pick_included_with_price_captured():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    scanner = _scanner_df([{"ticker": "NEW", "score": 80.0, "price": 42.5}])
    items = _buy_candidates(port_df, scanner, [], {}, _TODAY)
    item = find_item(items, "NEW")
    assert item is not None
    assert item["type"] == "new_pick"
    assert item["price"] == 42.5  # regression: price_at_surface must never be blank


def test_scanner_pick_excluded_when_already_held():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    scanner = _scanner_df([{"ticker": "HELD", "score": 80.0}])
    items = _buy_candidates(port_df, scanner, [], {}, _TODAY)
    assert find_item(items, "HELD") is None


def test_scanner_pick_excluded_when_act_blocked():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    scanner = _scanner_df([{"ticker": "NEW", "score": 80.0}])
    items = _buy_candidates(port_df, scanner, [], {}, _TODAY, act_today=[{"ticker": "NEW"}])
    assert find_item(items, "NEW") is None


def test_scanner_pick_excluded_below_composite_buy():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    scanner = _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY - 1}])
    items = _buy_candidates(port_df, scanner, [], {}, _TODAY)
    assert find_item(items, "NEW") is None


# ── _buy_candidates: add-to-winner ────────────────────────────────────────────

def _winner_row(**overrides):
    row = dict(
        ticker="AAA", weight=10.0, score=COMPOSITE_BUY + 5, signal="Strong Buy",
        gap_to_stop=ADD_WINNER_MIN_GAP_PCT + 2,
    )
    row.update(overrides)
    return row


def test_add_to_winner_included_when_all_conditions_met():
    port_df = make_port_df([_winner_row()])
    items = _buy_candidates(port_df, None, [], {}, _TODAY)
    item = find_item(items, "AAA")
    assert item is not None
    assert item["type"] == "add_winner"


def test_add_to_winner_suppressed_when_gap_too_small():
    port_df = make_port_df([_winner_row(gap_to_stop=ADD_WINNER_MIN_GAP_PCT - 1)])
    items = _buy_candidates(port_df, None, [], {}, _TODAY)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_when_signal_not_strong_buy():
    port_df = make_port_df([_winner_row(signal="Buy")])
    items = _buy_candidates(port_df, None, [], {}, _TODAY)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_when_act_blocked():
    port_df = make_port_df([_winner_row()])
    items = _buy_candidates(port_df, None, [], {}, _TODAY, act_today=[{"ticker": "AAA"}])
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_by_recent_add_cooldown():
    port_df = make_port_df([_winner_row()])
    held_data = {"AAA": {"days_since_last_buy": 2}}
    items = _buy_candidates(port_df, None, [], held_data, _TODAY)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_by_risk_advisor_trim_conflict():
    port_df = make_port_df([_winner_row()])
    risk_recs = [{
        "priority": "HIGH", "type": "beta", "title": "Beta rec",
        "root_tickers": [{"ticker": "AAA"}],
    }]
    items = _buy_candidates(port_df, None, [], {}, _TODAY, risk_recs=risk_recs)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_at_single_name_ceiling():
    port_df = make_port_df([_winner_row(weight=SINGLE_NAME_CEILING)])
    items = _buy_candidates(port_df, None, [], {}, _TODAY)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_by_deterioration_watch():
    port_df = make_port_df([_winner_row()])
    deterioration = [{"ticker": "AAA", "tier": "WATCH"}]
    items = _buy_candidates(port_df, None, [], {}, _TODAY, deterioration=deterioration)
    assert find_item(items, "AAA") is None


def test_add_to_winner_suppressed_by_drift_trim_overweight():
    # Single held position -> equal-weight target is 100%, so the drift-trim
    # floor (100% + 5%) can never trip with one row; use two positions so the
    # single overweight name (60%) clears its 55% floor (50% eq-target + 5%).
    port_df = make_port_df([
        _winner_row(ticker="AAA", weight=60.0),
        {"ticker": "BBB", "weight": 40.0},
    ])
    items = _buy_candidates(port_df, None, [], {}, _TODAY)
    assert find_item(items, "AAA") is None


# ── Review Before Close: weak-large position flag ────────────────────────────

def test_weak_large_flag_fires_on_overweight_plus_weak_score():
    port_df = make_port_df([{
        "ticker": "AAA", "weight": LARGE_POSITION_WEIGHT_PCT + 2,
        "score": WEAK_CONVICTION_SCORE - 10,
    }])
    items = _review_list(port_df, [], [], {}, _TODAY, portfolio_value=100_000.0)
    item = find_item(items, "AAA")
    assert item is not None
    assert item["action"]["reason_key"] == "weak_large"


def test_weak_large_flag_silent_below_weight_threshold():
    port_df = make_port_df([{
        "ticker": "AAA", "weight": LARGE_POSITION_WEIGHT_PCT - 2,
        "score": WEAK_CONVICTION_SCORE - 10,
    }])
    items = _review_list(port_df, [], [], {}, _TODAY, portfolio_value=100_000.0)
    assert find_item(items, "AAA") is None


def test_weak_large_flag_silent_when_conviction_is_strong():
    # Overweight + STRONG is risk_advisor's single_name_concentration rec's
    # job, not this one's -- the two surfaces partition on WEAK_CONVICTION_SCORE
    # so they never double-fire on the same ticker.
    port_df = make_port_df([{
        "ticker": "AAA", "weight": LARGE_POSITION_WEIGHT_PCT + 2,
        "score": WEAK_CONVICTION_SCORE + 10,
    }])
    items = _review_list(port_df, [], [], {}, _TODAY, portfolio_value=100_000.0)
    assert find_item(items, "AAA") is None


# ── _cross_reference ──────────────────────────────────────────────────────────
#
# _cross_reference() computes its OWN legacy verdict ('confirmed'/'mixed'/
# 'conflicted'/'caution'/'unverified') via a dedicated if/elif chain, AND
# separately calls signal_reconciliation.reconcile_signals() for a second,
# independently-computed verdict ('go'/'verify'/'caution'/'skip') stored under
# verdict_reconciled. app.py uses BOTH live in the UI -- the legacy `verdict`
# drives sorting/coloring/grouping in most surfaces (e.g. the "confirmed"
# bucket split at app.py ~22648), while verdict_reconciled['one_liner'] is
# rendered as the prominent explanation text (app.py ~5897, ~7479). These two
# verdicts are NOT guaranteed to agree, because reconcile_signals() has no
# concept of the analyst-revisions layer (Layer 5) that only the legacy chain
# considers -- see test_cross_reference_legacy_and_reconciled_verdicts_can_diverge
# below, which pins a concrete case where they disagree.

def _scanner_row(**overrides):
    row = dict(Signal="Buy", Score=80.0, RSI=60.0, Trend="Up")
    row.update(overrides)
    return row


def _held_df(ticker="AAA", signal=None, score=None):
    import pandas as pd
    return pd.DataFrame([{"Ticker": ticker, "Signal": signal, "Score": score}])


def test_cross_reference_confirmed_when_composite_agrees_no_conflicts():
    composites = {"AAA": {"rec": {"label": "Strong Buy"}, "total": 85.0}}
    result = _cross_reference("AAA", _scanner_row(), None, [], {}, _TODAY, composites=composites)
    assert result["verdict"] == "confirmed"


def test_cross_reference_unverified_not_held_no_composite():
    result = _cross_reference("AAA", _scanner_row(), None, [], {}, _TODAY)
    assert result["verdict"] == "unverified"
    assert "Run Analysis First" in result["verdict_label"]


def test_cross_reference_unverified_held_no_composite_uses_different_label():
    port_df = _held_df("AAA", signal=None, score=None)
    result = _cross_reference("AAA", _scanner_row(), port_df, [], {}, _TODAY)
    assert result["verdict"] == "unverified"
    assert result["is_held"] is True
    assert "Composite Signal Missing" in result["verdict_label"]


def test_cross_reference_composite_conflict_alone_gives_conflicted():
    port_df = _held_df("AAA", signal="Hold", score=50.0)
    result = _cross_reference("AAA", _scanner_row(), port_df, [], {}, _TODAY)
    assert result["verdict"] == "conflicted"
    assert "Composite vs Technical" in result["verdict_label"]


def test_cross_reference_sentiment_conflict_alone_gives_mixed():
    news = [{"ticker": "AAA", "compound": -0.5, "headline": "bad news"}]
    result = _cross_reference("AAA", _scanner_row(), None, news, {}, _TODAY)
    assert result["verdict"] == "mixed"
    assert "Negative News" in result["verdict_label"]


def test_cross_reference_earnings_conflict_alone_gives_caution():
    earnings_lookup = {"AAA": "2026-07-29"}  # 2 days out from _TODAY
    result = _cross_reference("AAA", _scanner_row(), None, [], {}, _TODAY, earnings_lookup=earnings_lookup)
    assert result["verdict"] == "caution"
    assert "Earnings Within" in result["verdict_label"]


def test_cross_reference_earnings_plus_composite_conflict_escalates_to_conflicted():
    # The docstring/comment above the verdict chain explicitly requires this:
    # earnings + another conflict must ESCALATE to "conflicted", not settle
    # for the lower "caution" tier earnings would give alone.
    port_df = _held_df("AAA", signal="Sell", score=30.0)
    earnings_lookup = {"AAA": "2026-07-29"}
    result = _cross_reference("AAA", _scanner_row(), port_df, [], {}, _TODAY, earnings_lookup=earnings_lookup)
    assert result["verdict"] == "conflicted"
    assert "Earnings + Signal Conflict" in result["verdict_label"]


def test_cross_reference_composite_and_sentiment_conflict_gives_multiple_conflicts():
    port_df = _held_df("AAA", signal="Sell", score=30.0)
    news = [{"ticker": "AAA", "compound": -0.5, "headline": "bad news"}]
    result = _cross_reference("AAA", _scanner_row(), port_df, news, {}, _TODAY)
    assert result["verdict"] == "conflicted"
    assert "Multiple Conflicts" in result["verdict_label"]


def test_cross_reference_analyst_downgrade_alone_gives_generic_mixed():
    # A revisions-only conflict (no composite/sentiment/earnings conflict)
    # still downgrades the verdict via the generic `elif conflicts:` branch,
    # with the plain "Mixed" label (not the specific Negative-News one).
    held_data = {"AAA": {"revisions": {"net": -3}}}
    result = _cross_reference("AAA", _scanner_row(), None, [], held_data, _TODAY)
    assert result["verdict"] == "mixed"
    assert result["verdict_label"] == "⚠️ Mixed"


def test_cross_reference_layers_checked_matches_agreed_plus_conflicts():
    result = _cross_reference("AAA", _scanner_row(), None, [], {}, _TODAY)
    assert result["layers_checked"] == len(result["agreed"]) + len(result["conflicts"])


def test_cross_reference_legacy_and_reconciled_verdicts_can_diverge():
    # Composite agrees with technical (Strong Buy, no conflict) and there's no
    # earnings/news conflict -- but an analyst-revisions downgrade (Layer 5,
    # legacy-only) still knocks the LEGACY verdict down to "mixed", while
    # reconcile_signals() -- which has no revisions input at all -- sees a
    # clean composite/momentum agreement and returns "go". Both fields ship
    # live in the same dict and are both rendered in the UI (see module note
    # above), so this is a real, currently-shipping inconsistency, not a test
    # artifact -- pinned here as documented current behaviour, not asserting
    # it's correct.
    port_df = _held_df("AAA", signal="Strong Buy", score=90.0)
    held_data = {"AAA": {"revisions": {"net": -3}}}
    result = _cross_reference("AAA", _scanner_row(), port_df, [], held_data, _TODAY)
    assert result["verdict"] == "mixed"
    assert result["verdict_reconciled"]["verdict"] == "go"
