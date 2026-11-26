"""CorrYoung C3 + C3b (docs/plans/corr-young-holding.md section 7): the gates, the Home
producer / banner and the Grow Today add hold, for a correlation read on a thin sample or
with a young holding left out. Also the three C2-review leftovers (corr_window wording,
malformed short_history item, the n_obs 0 record note).

No test here reads a docs/ file.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import (
    cluster_add_gate, corr_window, gate_ledger, home_risk_synthesis as hrs,
    pair_add_gate, portfolio, risk_metric_history as rmh, util,
)
from stock_analyzer.constants import (
    ADD_WINNER_MIN_GAP_PCT, COMPOSITE_BUY, CORR_MIN_OBS_TRUSTED,
    DIVERSIFY_MODERATE_PCT, DIVERSIFY_WELL_PCT,
)
from stock_analyzer.daily_briefing import _grow_today
from tests.conftest import make_port_df

FLOOR = CORR_MIN_OBS_TRUSTED
_TODAY = date(2026, 7, 27)

THIN_COV = {"n_obs": FLOOR - 1, "min_returns": FLOOR, "left_out": []}
OK_COV = {"n_obs": 125, "min_returns": FLOOR, "left_out": []}


def _df(tickers, pairs):
    df = pd.DataFrame(0.0, index=tickers, columns=tickers)
    for t in tickers:
        df.loc[t, t] = 1.0
    for (a, b), c in pairs.items():
        df.loc[a, b] = c
        df.loc[b, a] = c
    return df


def _danger(a="AAA", b="BBB", corr=0.9):
    return {"t1": a, "t2": b, "corr": corr, "level": "danger"}


def _cluster(tickers, new_pairs):
    return [{"tickers": tickers, "size": len(tickers), "avg_internal_corr": 0.9,
             "combined_weight_pct": 20.0, "tier": "warning", "new_pairs": new_pairs}]


# ── item 1: the sample tag ────────────────────────────────────────────────────
def test_pair_block_map_entries_carry_the_book_wide_sample_tag():
    df = _df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    held = ["AAA", "BBB"]
    thin = pair_add_gate.add_block_map(df, [_danger()], [], held, corr_coverage=THIN_COV)
    assert set(thin) == {"AAA", "BBB"}
    assert all(e["sample"] == "thin" and e["n_obs"] == FLOOR - 1 for e in thin.values())
    trusted = pair_add_gate.add_block_map(df, [_danger()], [], held, corr_coverage=OK_COV)
    assert all(e["sample"] == "trusted" and "n_obs" not in e for e in trusted.values())
    unknown = pair_add_gate.add_block_map(df, [_danger()], [], held)
    assert all(e["sample"] == "unknown" and "n_obs" not in e for e in unknown.values())
    # the tag never changes who is paused or the partner detail
    strip = lambda m: {t: {k: v for k, v in e.items() if k not in ("sample", "n_obs")}
                       for t, e in m.items()}
    assert strip(thin) == strip(trusted) == strip(unknown)


def test_pair_block_map_empty_thin_is_still_none_and_empty_trusted_still_empty():
    df = _df(["AAA", "BBB"], {("AAA", "BBB"): 0.7})
    warning = [dict(_danger(corr=0.7), level="warning")]
    assert pair_add_gate.add_block_map(df, warning, [], ["AAA", "BBB"], corr_coverage=THIN_COV) is None
    assert pair_add_gate.add_block_map(df, warning, [], ["AAA", "BBB"], corr_coverage=OK_COV) == {}


def test_cluster_block_map_entries_carry_the_book_wide_sample_tag():
    df = _df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    clusters = _cluster(["AAA", "BBB"], [["AAA", "BBB"]])
    held = ["AAA", "BBB"]
    thin = cluster_add_gate.add_block_map(clusters, df, held, "2026-10-01", corr_coverage=THIN_COV)
    assert set(thin) == {"AAA", "BBB"}
    assert all(e["sample"] == "thin" and e["n_obs"] == FLOOR - 1 for e in thin.values())
    trusted = cluster_add_gate.add_block_map(clusters, df, held, "2026-10-01", corr_coverage=OK_COV)
    assert all(e["sample"] == "trusted" and "n_obs" not in e for e in trusted.values())
    unknown = cluster_add_gate.add_block_map(clusters, df, held, "2026-10-01")
    assert all(e["sample"] == "unknown" and "n_obs" not in e for e in unknown.values())
    # empty thin -> None, as before
    assert cluster_add_gate.add_block_map(
        clusters, df, ["ZZZ"], "2026-10-01", corr_coverage=THIN_COV) is None


# ── item 2: the thin reason text ──────────────────────────────────────────────
def test_describe_reason_thin_text():
    df = _df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    entry = pair_add_gate.add_block_map(
        df, [_danger()], [], ["AAA", "BBB"], corr_coverage=THIN_COV)["AAA"]
    text = pair_add_gate.describe_reason(entry)
    assert text == (
        f"Correlation with BBB couldn't be measured: only {FLOOR - 1} shared daily returns "
        f"({FLOOR} needed). Add paused until it can be; this is not a finding that they "
        "move together, and no trim is recommended.")
    assert "couldn't be measured" in text
    assert "this is not a finding that they move together" in text
    assert len(text) <= 300
    # the old positive wording is absent
    assert "This pause adds no recommendation of its own" not in text
    assert "paused on the correlation alone" not in text
    # a trusted entry keeps the old wording
    trusted = pair_add_gate.add_block_map(
        df, [_danger()], [], ["AAA", "BBB"], corr_coverage=OK_COV)["AAA"]
    assert "This pause adds no recommendation of its own." in pair_add_gate.describe_reason(trusted)
    assert "couldn't be measured" not in pair_add_gate.describe_reason(trusted)


def test_describe_reason_thin_never_raises_on_a_bad_entry():
    assert "couldn't be measured" in pair_add_gate.describe_reason(
        {"sample": "thin", "partners": "junk", "n_obs": "x"})
    assert len(pair_add_gate.describe_reason(
        {"sample": "thin", "n_obs": 3,
         "partners": [{"partner": f"T{i}"} for i in range(60)]})) <= 300


# ── item 3: the Home producer on a thin sample ────────────────────────────────
def _thin_book():
    """A and B are counted (each 29 returns) but share only 14 returns, all identical:
    a danger pair on a THIN sample."""
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2026-01-05", periods=45)
    rets = pd.Series(rng.normal(0.0, 0.02, len(days)), index=days)

    def closes(lo, hi):
        idx = days[lo:hi]
        return {"df": pd.DataFrame({"Close": 100.0 * (1 + rets[idx]).cumprod()}, index=idx)}

    held = {"AAA": closes(0, 30), "BBB": closes(15, 45)}
    port_df = make_port_df([dict(ticker="AAA", weight=10.0, score=60.0),
                            dict(ticker="BBB", weight=10.0, score=60.0)])
    return port_df, held


def test_build_correlation_bundle_thin_withholds_the_score_and_keeps_the_pairs():
    port_df, held = _thin_book()
    out = hrs.build_correlation_bundle(
        port_df, held, 20_000.0, sector_candidates={}, discovery_universe={},
        held_tickers=["AAA", "BBB"])
    cov = out["corr_coverage"]
    assert portfolio.correlation_sample_state(cov) == portfolio.CORR_SAMPLE_THIN
    assert out["div_score"] is None and out["avg_corr"] is None
    assert out["div_label"] == "Unavailable"
    assert out["div"]["score"] is None and out["div"]["avg_correlation"] is None
    # the pairs survive: the gates and PAIR_RISK_WITHHELD still need them
    assert [p["level"] for p in out["risk_pairs"]] == ["danger"]
    assert out["div"]["risk_pairs"] == out["risk_pairs"]
    types = [r.get("type") for r in (out["div_recs"] or [])]
    assert "PAIR_RISK_WITHHELD" in types and "PAIR_RISK" not in types


def test_build_correlation_bundle_trusted_is_unchanged():
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2026-01-05", periods=60)
    held = {t: {"df": pd.DataFrame(
        {"Close": 100.0 * (1 + pd.Series(rng.normal(0, 0.02, 60), index=days)).cumprod()},
        index=days)} for t in ("AAA", "BBB")}
    port_df, _ = _thin_book()
    out = hrs.build_correlation_bundle(
        port_df, held, 20_000.0, sector_candidates={}, discovery_universe={},
        held_tickers=["AAA", "BBB"])
    assert portfolio.correlation_sample_state(out["corr_coverage"]) == portfolio.CORR_SAMPLE_TRUSTED
    assert out["div_score"] is not None and out["avg_corr"] is not None
    assert out["div_label"] != "Unavailable"
    assert out["div"]["score"] == out["div_score"]


def test_diversification_recommendations_reads_only_risk_pairs_from_div():
    # Pins the item-3 claim: the producer hands the recommender a copy with a
    # None score, which is only safe while it reads nothing but risk_pairs.
    port_df, held = _thin_book()
    corr_df = portfolio.correlation_matrix(held)
    div = portfolio.diversification_score(corr_df)
    assert div["score"] is not None
    only_pairs = {"risk_pairs": div["risk_pairs"]}
    nulled = dict(div, score=None, avg_correlation=None)
    kw = dict(sector_candidates={}, discovery_universe={}, portfolio_value=20_000.0,
              corr_coverage=OK_COV)
    full = portfolio.diversification_recommendations(port_df, corr_df, div, **kw)
    assert full == portfolio.diversification_recommendations(port_df, corr_df, nulled, **kw)
    assert full == portfolio.diversification_recommendations(port_df, corr_df, only_pairs, **kw)
    assert any(r.get("type") == "PAIR_RISK" for r in full)   # not a vacuous equality


# ── item 4: the banner (Q4) ───────────────────────────────────────────────────
def _cls(n_d, n_w, score, unchecked, cov):
    return hrs.classify_risk_banner(n_d, n_w, score, corr_unchecked=unchecked, corr_coverage=cov)[0]


def test_banner_q4_table():
    thin = {"n_obs": 5}
    unknown = {"n_obs": None}
    trusted = {"n_obs": 125}
    assert _cls(0, 0, 36.0, [], thin) == hrs.RISK_BANNER_INCOMPLETE_LABEL
    assert _cls(0, 0, 25.0, [], unknown) == hrs.RISK_BANNER_INCOMPLETE_LABEL
    assert _cls(0, 0, 36.0, ["ANT"], trusted) == "Monitor"
    assert _cls(0, 0, 25.0, [], trusted) == "Action Required"
    assert _cls(1, 0, 99.0, [], thin) == "Action Required"
    assert _cls(0, 1, 99.0, [], thin) == "Monitor"
    assert _cls(0, 0, 50.0, [], trusted) == "All Clear"
    # boundaries of the two score legs on a trusted sample
    assert _cls(0, 0, DIVERSIFY_MODERATE_PCT - 0.01, [], trusted) == "Action Required"
    assert _cls(0, 0, DIVERSIFY_MODERATE_PCT, [], trusted) == "Monitor"
    assert _cls(0, 0, DIVERSIFY_WELL_PCT, [], trusted) == "All Clear"


def test_banner_record_label_unchanged_by_q4():
    ver = {"corr_unchecked": [], "corr_coverage": {"n_obs": 125}}
    thin = {"corr_unchecked": [], "corr_coverage": {"n_obs": 5}}
    assert hrs.risk_banner_record_label(0, 0, 50.0, **ver) == "All Clear"
    assert hrs.risk_banner_record_label(0, 1, 99.0, **ver) == "Monitor"
    assert hrs.risk_banner_record_label(1, 0, 99.0, **thin) == "Action Required"
    assert hrs.risk_banner_record_label(0, 1, 99.0, **thin) is None
    assert hrs.risk_banner_record_label(0, 0, 25.0, **thin) is None


def test_banner_docstring_states_the_trusted_sample_rule():
    doc = hrs.classify_risk_banner.__doc__
    assert "TRUSTED" in doc and "CorrYoung" in doc
    assert "ORDERING IS LOAD-BEARING" in doc
    assert "escalation is honest evidence even from a partial" not in doc


# ── item 5: risk_metric_history diagnostics ───────────────────────────────────
def _hist(n, first="2026-01-05", step=1.0):
    return {"df": pd.DataFrame({"Close": [100.0 + step * i + (i % 3) for i in range(n)]},
                               index=pd.bdate_range(first, periods=n))}


def test_snapshot_diagnostics_carry_corr_left_out_and_the_row_is_still_withheld():
    held = {"AAA": _hist(60), "BBB": _hist(60, step=-0.3), "ANT": _hist(5)}
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB", "ANT"], "Sector": ["T", "T", "T"],
                            "Gate Weight (%)": [10.0, 10.0, 5.0]})
    diag: dict = {}
    row = rmh.build_portfolio_risk_snapshot(
        "2026-11-25", port_df, {"beta": 1.0}, held,
        held_tickers=["AAA", "BBB", "ANT"], diagnostics=diag)
    assert diag["corr_left_out"] == ["ANT"]
    # Q1 = B: ANT is unchecked, so the correlation reading is still withheld
    assert row["avg_pairwise_corr"] is None and row["diversification_score"] is None
    assert diag["corr_withheld"] is True
    # nothing left out -> [] ; coverage unavailable -> None
    diag2: dict = {}
    rmh.build_portfolio_risk_snapshot(
        "2026-11-25", port_df.iloc[:2], {"beta": 1.0},
        {"AAA": held["AAA"], "BBB": held["BBB"]}, held_tickers=["AAA", "BBB"], diagnostics=diag2)
    assert diag2["corr_left_out"] == []
    diag3: dict = {}
    rmh.build_portfolio_risk_snapshot("2026-11-25", port_df, None, {}, diagnostics=diag3)
    assert diag3["corr_left_out"] is None


def test_snapshot_corr_left_out_lists_a_name_with_an_unreadable_count(monkeypatch):
    cov = {"n_obs": 125, "n_tickers": 3, "min_returns": FLOOR, "left_out": [
        {"ticker": "ANT", "reason": "short_history"},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}
    monkeypatch.setattr(portfolio, "correlation_coverage", lambda held: cov)
    diag: dict = {}
    rmh.build_portfolio_risk_snapshot(
        "2026-11-25", pd.DataFrame({"Ticker": ["AAA"], "Sector": ["T"], "Gate Weight (%)": [10.0]}),
        {"beta": 1.0}, {"AAA": _hist(60)}, held_tickers=["AAA"], diagnostics=diag)
    assert diag["corr_left_out"] == ["ANT", "XYZ"]


# ── items 11 / 12 / 13: the C2 leftovers ──────────────────────────────────────
def test_unconfirmed_reasons_puts_the_floor_after_the_young_clause():
    assert corr_window.unconfirmed_reasons(
        [{"ticker": "ANT", "reason": "short_history", "n_returns": 4},
         {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}], need=FLOOR) == (
        f"ANT has only 4 daily returns of price history ({FLOOR} needed) and XYZ has no price history")
    assert corr_window.unconfirmed_reasons(
        [{"ticker": "ANT", "reason": "short_history", "n_returns": 4}], need=FLOOR) == (
        f"it has only 4 daily returns of price history ({FLOOR} needed)")
    assert "needed" not in corr_window.unconfirmed_reasons(
        [{"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}], need=FLOOR)


_MALFORMED = {"n_obs": 0, "min_returns": FLOOR, "left_out": [
    {"ticker": "ANT", "reason": "short_history"},                       # no n_returns
    {"ticker": "BAD", "reason": "short_history", "n_returns": "four"},
    {"ticker": "INF", "reason": "short_history", "n_returns": float("inf")},
    {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}


def test_left_out_items_drops_a_short_history_item_without_a_numeric_count():
    assert [x["ticker"] for x in corr_window.left_out_items(_MALFORMED)] == ["XYZ"]


def test_malformed_left_out_item_never_raises_in_the_wording():
    # the surviving no_prices item still words; the malformed ones fall to the generic text
    text = pair_add_gate.unchecked_disclosure(None, True, True, _MALFORMED)
    assert isinstance(text, str) and "XYZ" in text
    only_bad = {"n_obs": 0, "left_out": [{"ticker": "ANT", "reason": "short_history"}]}
    generic = pair_add_gate.unchecked_disclosure(None, True, False, only_bad)
    assert generic == pair_add_gate.unchecked_disclosure(None, True, False)
    assert isinstance(util.correlation_unchecked_note(None, only_bad), str)
    assert isinstance(util.correlation_unchecked_note(["ANT"], only_bad), str)
    assert isinstance(pair_add_gate.unchecked_disclosure(["ANT"], True, True, only_bad), str)
    assert isinstance(util.correlation_record_withheld_note(["ANT"], only_bad, subject="S"), str)
    assert corr_window.left_out_note(only_bad) is None


def test_record_withheld_note_with_fewer_than_two_counted():
    cov = {"n_obs": 0, "n_tickers": 2, "min_returns": FLOOR, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4}]}
    text = util.correlation_record_withheld_note(None, cov, subject="This scan can't be saved")
    assert "fewer than 2 holdings have enough price history to pair" in text
    assert "only 0 shared daily returns" not in text
    assert "only overlap on those days" not in text
    # n_obs 0 with two COUNTED names that share no sessions keeps the overlap wording
    cov2 = {"n_obs": 0, "n_tickers": 3, "min_returns": FLOOR, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4}]}
    text2 = util.correlation_record_withheld_note([], cov2, subject="S")
    assert "only 0 shared daily returns" in text2
    # n_tickers unreadable and only no_prices names left out: the count is unknown,
    # so the old overlap wording stays (never "fewer than 2" unverified)
    cov4 = {"n_obs": 0, "min_returns": FLOOR, "left_out": [
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}
    text4 = util.correlation_record_withheld_note([], cov4, subject="S")
    assert "fewer than 2" not in text4 and "only 0 shared daily returns" in text4
    cov5 = dict(cov4, n_tickers="two")
    assert "fewer than 2" not in util.correlation_record_withheld_note([], cov5, subject="S")
    # a thin sample with nothing left out keeps its wording
    text3 = util.correlation_record_withheld_note(
        [], {"n_obs": 5, "min_returns": FLOOR, "left_out": []}, subject="S")
    assert "only 5 shared daily returns" in text3 and "fewer than 2" not in text3


# ── C3b: Grow Today ───────────────────────────────────────────────────────────
def _winner(**kw):
    row = dict(ticker="AAA", weight=10.0, score=COMPOSITE_BUY + 5, signal="Strong Buy",
               gap_to_stop=ADD_WINNER_MIN_GAP_PCT + 2)
    row.update(kw)
    return row


def _grow(rows, **kw):
    return _grow_today(make_port_df(rows), None, [], {}, _TODAY, 100_000.0,
                       {"tone": "bull"}, **kw)


def _blocks(coverage):
    df = _df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    pair = pair_add_gate.add_block_map(df, [_danger()], [], ["AAA", "BBB"], corr_coverage=coverage)
    cluster = cluster_add_gate.add_block_map(
        _cluster(["AAA", "BBB"], [["AAA", "BBB"]]), df, ["AAA", "BBB"], "2026-10-01",
        corr_coverage=coverage)
    return pair, cluster


def test_thin_pair_entry_holds_as_corr_unmeasured_not_as_g26():
    pair, _ = _blocks(THIN_COV)
    g = _grow([_winner()], pair_add_blocks=pair)
    assert g["add_positions"] == [] and g["pair_blocked_adds"] == []
    (held,) = g["entry_held_adds"]
    assert held["ticker"] == "AAA" and held["check"] == "corr_unmeasured"
    assert held["gate_id"] is None and held["counterfactual"] is True
    assert "couldn't be measured" in held["reason"]
    assert "this is not a finding that they move together" in held["reason"]
    assert held["clears_when"] == (
        f"when the correlation sample reaches {FLOOR} shared daily returns")
    # no ledger row for it
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.5)
    assert rows == []
    assert g["adds_paused"] == []


def test_thin_cluster_entry_holds_as_corr_unmeasured_not_as_g25():
    _, cluster = _blocks(THIN_COV)
    g = _grow([_winner()], cluster_add_blocks=cluster)
    assert g["add_positions"] == [] and g["cluster_blocked_adds"] == []
    (held,) = g["entry_held_adds"]
    assert held["check"] == "corr_unmeasured" and held["gate_id"] is None
    assert held["reason"] == (
        f"New-cluster check with BBB couldn't be measured: only {FLOOR - 1} shared daily "
        f"returns ({FLOOR} needed). Add paused until it can be (a Structural Scan can't be "
        "saved on this sample); this is not a finding that they move together.")
    assert gate_ledger.build_suppression_rows(
        g, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.5) == []


def test_a_trusted_fired_map_still_binds_as_g26_and_g25_with_a_ledger_row():
    pair, cluster = _blocks(OK_COV)
    g = _grow([_winner()], pair_add_blocks=pair)
    assert [b["gate_id"] for b in g["pair_blocked_adds"]] == ["G-26"]
    assert g["entry_held_adds"] == []
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.5)
    assert [r["gate_id"] for r in rows] == ["G-26"]
    g2 = _grow([_winner()], cluster_add_blocks=cluster)
    assert [b["gate_id"] for b in g2["cluster_blocked_adds"]] == ["G-25"]
    assert g2["entry_held_adds"] == []
    # an UNKNOWN-tagged map (coverage not passed) also still binds
    pair_u, _ = _blocks(None)
    g3 = _grow([_winner()], pair_add_blocks=pair_u)
    assert [b["gate_id"] for b in g3["pair_blocked_adds"]] == ["G-26"]


def test_an_entry_held_ticker_stays_entry_when_its_pair_is_thin(monkeypatch):
    pair, _ = _blocks(THIN_COV)

    def _hold(**kw):
        h = {"check": "stale", "gate_id": None, "reason": "data is stale",
             "clears_when": "fresh data", "gate_value": None, "gate_threshold": None}
        return {"eligible": False, "holds": [h], "binding": h, "unchecked": [], "passed": []}

    monkeypatch.setattr("stock_analyzer.entry_eligibility.entry_check", _hold)
    g = _grow([_winner()], pair_add_blocks=pair)
    (held,) = g["entry_held_adds"]
    assert held["check"] == "stale"   # ENTRY first; CORR_UNMEASURED comes after it


def _left_out_cov(*items):
    return {"n_obs": 125, "min_returns": FLOOR, "left_out": list(items)}


def test_q2_short_history_left_out_ticker_is_held():
    cov = _left_out_cov({"ticker": "ANT", "reason": "short_history", "n_returns": 4})
    g = _grow([_winner(ticker="ANT")], corr_coverage=cov)
    assert g["add_positions"] == []
    (held,) = g["entry_held_adds"]
    assert held["check"] == "corr_unmeasured" and held["gate_id"] is None
    assert held["reason"] == (
        f"ANT's correlation with your other holdings can't be measured: only 4 daily "
        f"returns of price history ({FLOOR} needed). Add held until it can be.")
    assert held["clears_when"] == f"when ANT has {FLOOR} daily returns of price history"
    assert gate_ledger.build_suppression_rows(
        g, rec_date=_TODAY, source="app", tone="bull", sp500_pct=0.5) == []


def test_q2_no_prices_left_out_ticker_is_held():
    cov = _left_out_cov({"ticker": "ANT", "reason": "no_prices", "n_returns": 0})
    g = _grow([_winner(ticker="ANT")], corr_coverage=cov)
    (held,) = g["entry_held_adds"]
    assert held["reason"] == (
        "ANT's correlation with your other holdings can't be measured: no usable price "
        "history this run. Add held until it can be.")
    assert held["clears_when"] == "when ANT's price history loads"


def test_q2_only_the_left_out_name_is_held_and_a_missing_or_malformed_key_is_tolerated():
    cov = _left_out_cov({"ticker": "ANT", "reason": "short_history", "n_returns": 4})
    g = _grow([_winner(ticker="ANT"), _winner(ticker="AAA", score=COMPOSITE_BUY + 6)],
              corr_coverage=cov)
    assert [h["ticker"] for h in g["entry_held_adds"]] == ["ANT"]
    assert [a["ticker"] for a in g["add_positions"]] == ["AAA"]
    for cov_ in ({"n_obs": 125}, {"left_out": None}, {"left_out": "x"}, "junk"):
        g2 = _grow([_winner(ticker="ANT")], corr_coverage=cov_)
        assert [a["ticker"] for a in g2["add_positions"]] == ["ANT"], cov_
        assert g2["entry_held_adds"] == []


def test_q2_short_history_with_an_unreadable_count_is_still_held_with_count_free_text():
    for bad in (None, "four", float("nan"), float("inf"), True):
        item = {"ticker": "ANT", "reason": "short_history"}
        if bad is not None:
            item["n_returns"] = bad
        g = _grow([_winner(ticker="ANT")], corr_coverage=_left_out_cov(item))
        assert g["add_positions"] == [], bad
        (held,) = g["entry_held_adds"]
        assert held["check"] == "corr_unmeasured" and held["gate_id"] is None
        assert held["reason"] == (
            "ANT's correlation with your other holdings can't be measured: too little "
            f"price history ({FLOOR} needed). Add held until it can be.")
        assert held["clears_when"] == f"when ANT has {FLOOR} daily returns of price history"
        assert held["gate_value"] is None and held["gate_threshold"] == FLOOR
    # the wording-site filter still drops it (generic wording there)
    assert corr_window.left_out_items(
        _left_out_cov({"ticker": "ANT", "reason": "short_history"})) == []
    assert [x["ticker"] for x in corr_window.raw_left_out(
        _left_out_cov({"ticker": "ANT", "reason": "short_history"}))] == ["ANT"]


def test_thin_cluster_entry_with_none_partners_never_crashes_the_brief():
    _, cluster = _blocks(THIN_COV)
    cluster["AAA"]["partners"] = None
    g = _grow([_winner()], cluster_add_blocks=cluster)
    (held,) = g["entry_held_adds"]
    assert held["reason"].startswith("New-cluster check with its partner couldn't be measured")


def test_adds_paused_excludes_corr_unmeasured_names_on_a_flat_day():
    cov = _left_out_cov({"ticker": "ANT", "reason": "short_history", "n_returns": 4})
    port_df = make_port_df([_winner(ticker="ANT"), _winner(ticker="AAA")])
    g = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "flat"},
                    corr_coverage=cov)
    assert [p["ticker"] for p in g["adds_paused"]] == ["AAA"]
    g0 = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "flat"})
    assert sorted(p["ticker"] for p in g0["adds_paused"]) == ["AAA", "ANT"]


def test_corr_coverage_none_is_byte_identical_to_not_passing_it():
    pair, cluster = _blocks(OK_COV)
    port_df = make_port_df([_winner(), _winner(ticker="CCC", score=COMPOSITE_BUY + 7)])
    for kw in ({}, {"pair_add_blocks": pair}, {"cluster_add_blocks": cluster},
               {"pair_add_blocks": pair, "cluster_add_blocks": cluster}):
        base = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"}, **kw)
        none = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                           corr_coverage=None, **kw)
        assert base == none


def test_build_daily_briefing_passes_corr_coverage_through():
    import inspect
    from stock_analyzer import daily_briefing as db
    sig = inspect.signature(db.build_daily_briefing)
    assert sig.parameters["corr_coverage"].default is None
    assert inspect.signature(db._grow_today).parameters["corr_coverage"].default is None
    src = inspect.getsource(db.build_daily_briefing)
    assert "corr_coverage=corr_coverage" in src
