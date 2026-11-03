"""SecTrig: the Diversification Advisor's sector REDUCE card is an early
awareness signal at SECTOR_REDUCE_TRIGGER, sized back UNDER it."""
import re

import pandas as pd

from stock_analyzer import sector_trigger_text as stt
from stock_analyzer.constants import (
    POSITION_AT_RISK_GAP_PCT, SECTOR_CEILING, SECTOR_ELEVATED, SECTOR_REDUCE_TRIGGER,
)
from stock_analyzer.portfolio import diversification_recommendations


def _recs(sector_wt, gap=2.0, weak_weight=8.0, value=100_000.0, weak_score=57.0):
    other = 100.0 - sector_wt
    df = pd.DataFrame({
        "Ticker": ["ISRG", "ABBV", "AAPL"],
        "Sector": ["Healthcare", "Healthcare", "Technology"],
        "Score": [weak_score, 70.0, 80.0],
        "Score Available": [True, True, True],
        "Signal": ["🟡 Hold", "🟢 Buy", "🟢 Buy"],
        "P&L (%)": [1.0, 2.0, 3.0],
        "Weight (%)": [weak_weight, sector_wt - weak_weight, other],
        "Gap to Stop (%)": [gap, 9.0, 9.0],
        "Market Value": [weak_weight * 1000, (sector_wt - weak_weight) * 1000, other * 1000],
    })
    out = diversification_recommendations(
        df, pd.DataFrame(), {"risk_pairs": []},
        sector_candidates={}, discovery_universe={}, portfolio_value=value,
        corr_coverage=None,
    )
    return [r for r in out if r["type"] == "REDUCE" and r["sector"] == "Healthcare"]


def test_reduce_sizes_back_under_trigger_not_to_single_name_ceiling():
    (r,) = _recs(23.5)
    assert r["target_pct"] == SECTOR_REDUCE_TRIGGER
    frac = (23.5 - SECTOR_REDUCE_TRIGGER) / (1 - SECTOR_REDUCE_TRIGGER / 100)
    assert r["reduce_pct"] == round(frac, 1) == 4.4
    assert r["reduce_dollars"] == 4375  # unrounded fraction, not 4.4%


def test_trim_lands_the_sector_at_the_trigger_not_above_it():
    # Selling shrinks the book too: (S - t) / (1 - t) must equal T.
    S, T = 23.5, SECTOR_REDUCE_TRIGGER
    t = (S - T) / (1 - T / 100)
    assert abs((S - t) / (1 - t / 100) - T) < 1e-9
    # the old S - T sizing left the sector above the trigger
    assert (S - (S - T)) / (1 - (S - T) / 100) > T


def test_weakest_score_is_not_pre_rounded_across_a_band_edge():
    (r,) = _recs(23.5, weak_score=64.6)
    assert r["weakest_tickers"][0]["score"] == 64.6
    assert "64.6" in stt.body(r, EARLY) and "Hold 65" not in stt.body(r, EARLY)


def test_boundary_unchanged_fires_just_above_not_at_trigger():
    assert _recs(SECTOR_REDUCE_TRIGGER) == []
    assert len(_recs(SECTOR_REDUCE_TRIGGER + 0.1)) == 1


EARLY = {"band": "early", "show_size": True, "pointer": None}


def _body(gap, weak_weight=8.0):
    (r,) = _recs(23.5, gap=gap, weak_weight=weak_weight)
    return r, stt.body(r, EARLY)


def test_near_stop_clause_only_strictly_between_zero_and_threshold():
    for gap, expect in [(1.1, True), (0.01, True), (POSITION_AT_RISK_GAP_PCT - 0.01, True),
                        (0.0, False), (-1.0, False), (POSITION_AT_RISK_GAP_PCT, False),
                        (9.0, False), (None, False), (float("nan"), False)]:
        r, b = _body(gap)
        assert r["weakest_tickers"][0]["near_stop"] is expect, gap
        assert ("stop exit" in b) is expect, gap


def test_stop_exit_resolves_only_when_weight_covers_excess():
    _, big = _body(1.1, weak_weight=8.0)
    assert "would roughly resolve this" in big and "1.1% away" in big
    _, small = _body(1.1, weak_weight=2.0)
    assert "would also cut this" in small and "resolve" not in small


def test_text_makes_no_sector_cap_claim_and_no_dollar_pair():
    (r,) = _recs(23.5)
    texts = [stt.title(r), stt.body(r, EARLY), r["reason"], stt.size_label()]
    for t in texts:
        assert "sector cap" not in t.lower()
        assert "reduce by" not in t.lower()
        assert "→" not in t
        assert "**" not in t
    assert "20% diversification trigger" in stt.title(r)
    b = stt.body(r, EARLY)
    assert f"From {SECTOR_ELEVATED:.0f}%, 🔗 Risk Analysis lists your largest sector as elevated" in b
    assert "Grow Today warns before adding to it" in b
    assert f"at {SECTOR_CEILING:.0f}% Today's Brief makes it an Act Today trim and new buys in it are blocked" in b
    assert "Today's Brief flags a sector" not in b
    assert "Names in one sector often move together on the same news." in b
    # every "$" in markdown prose is escaped; the metric value is plain
    assert not re.search(r"(?<!\\)\$", stt.body(r, EARLY))
    assert stt.size_value(r) == "≈$4,375"
    assert "ISRG is the weakest name" in stt.body(r, EARLY)


def test_missing_gap_column_does_not_crash():
    df = pd.DataFrame({
        "Ticker": ["A"], "Sector": ["Healthcare"], "Score": [50.0],
        "Signal": ["🟡 Hold"], "P&L (%)": [0.0], "Weight (%)": [23.5],
        "Market Value": [1.0],
    })
    out = diversification_recommendations(
        df, pd.DataFrame(), {"risk_pairs": []}, sector_candidates={},
        discovery_universe={}, corr_coverage=None)
    (r,) = [x for x in out if x["type"] == "REDUCE"]
    assert r["weakest_tickers"][0]["gap_to_stop"] is None


def _risk(priority, sector="Healthcare"):
    return {"type": "sector_concentration", "priority": priority, "sector": sector}


def test_band_early_when_no_matching_risk_rec():
    (r,) = _recs(23.5)
    for recs in ([], [{"type": "beta", "priority": "HIGH"}], [_risk("HIGH", "Technology")]):
        st = stt.card_state(r, recs)
        assert st == {"band": "early", "show_size": True, "pointer": None}


def test_band_elevated_medium_rec_no_size():
    (r,) = _recs(23.5)
    st = stt.card_state(r, [_risk("MEDIUM")])
    assert st["band"] == "elevated" and st["show_size"] is False
    b = stt.body(r, st)
    assert "Risk Analysis lists this sector as elevated" in b and "$" not in b
    assert "awareness only" not in b.lower()


def test_band_hard_cap_high_rec_no_size_no_awareness_claim():
    (r,) = _recs(23.5)
    st = stt.card_state(r, [_risk("HIGH")])
    assert st["band"] == "hard_cap" and st["show_size"] is False
    b = stt.body(r, st)
    assert "Act Today item" in b and "takes precedence" in b and "$" not in b
    assert "awareness only" not in b.lower() and "no action required" not in b.lower()


def test_high_rec_defers_while_card_pct_under_ceiling():
    # a HIGH rec while the card's pct is under the ceiling (only reachable if
    # gate_denom ever moves off equity)
    (r,) = _recs(23.5)
    assert r["current_pct"] < SECTOR_CEILING
    assert stt.card_state(r, [_risk("HIGH")])["band"] == "hard_cap"


def test_band_hard_cap_at_and_above_ceiling():
    (r,) = _recs(SECTOR_CEILING + 1)
    assert stt.card_state(r, [_risk("HIGH")])["show_size"] is False


def test_band_unchecked_when_risk_recs_offline_or_rec_unnamed():
    (r,) = _recs(23.5)
    for recs in (None, [{"type": "sector_concentration", "priority": "HIGH"}]):
        st = stt.card_state(r, recs)
        assert st["band"] == "unchecked" and st["show_size"] is False
    b = stt.body(r, stt.card_state(r, None))
    assert "couldn't be read this session" in b and "can't say how urgent" in b
    assert "awareness only" not in b.lower() and "$" not in b


def test_empty_checked_risk_recs_is_early_not_unchecked():
    (r,) = _recs(23.5)
    assert stt.card_state(r, [])["band"] == "early"


def test_one_name_sector_makes_no_plural_claim():
    df = pd.DataFrame({
        "Ticker": ["A", "B"], "Sector": ["Healthcare", "Technology"],
        "Score": [50.0, 80.0], "Signal": ["🟡 Hold"] * 2, "P&L (%)": [0.0] * 2,
        "Weight (%)": [23.5, 76.5], "Market Value": [1.0, 1.0],
    })
    out = diversification_recommendations(
        df, pd.DataFrame(), {"risk_pairs": []}, sector_candidates={},
        discovery_universe={}, corr_coverage=None)
    (r,) = [x for x in out if x["type"] == "REDUCE" and x["sector"] == "Healthcare"]
    assert r["n_holdings"] == 1
    assert "move together" not in stt.body(r, EARLY)


def test_risk_advisor_rec_names_its_sector():
    import inspect
    from stock_analyzer import risk_advisor
    src = inspect.getsource(risk_advisor)
    assert '"sector":   top_sec' in src
