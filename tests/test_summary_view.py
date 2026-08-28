"""
Tests for stock_analyzer/summary_view.py — the pure decision layer behind the
redesigned 🧾 Summary cockpit (F-204).

The invariants pinned here are the ones whose failure mode is a CONFIDENT WRONG
ANSWER rather than a crash, so each is tested at its boundary rather than argued
to be safe:

  - the Book Safety strip is NEVER green on unmeasured/stale leverage
    (memory: project_leverage_cache_false_green)
  - "broker drift not checked" NEVER reads as "drift"
  - a ticker with no quote is NEVER rendered as a 0% (flat) mover
  - the concentration badge fires AT the ceiling, not just past it
"""
import pandas as pd
import pytest

from stock_analyzer import summary_view
from stock_analyzer.constants import (
    MARGIN_MAINTENANCE_RATE,
    FRAGILITY_PULLBACK_PCT,
    SINGLE_NAME_CEILING,
    COMPOSITE_BUY,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _lev(*, debit, equity, net_capital, stale=False, cash_seen=True, ratio=None):
    """Build a _leverage_cache-shaped dict (see app.py ~4787)."""
    return {
        "levered":      debit > 0,
        "margin_debit": debit,
        "equity":       equity,
        "net_capital":  net_capital,
        "ratio":        ratio if ratio is not None else (equity / net_capital),
        "stale":        stale,
        "cash_seen":    cash_seen,
    }


def _safety(leverage, drift=None):
    return summary_view.book_safety(
        leverage, drift,
        maintenance_rate=MARGIN_MAINTENANCE_RATE,
        fragility_pullback_pct=FRAGILITY_PULLBACK_PCT,
    )


# ─── book_safety: the never-green-on-no-evidence contract ─────────────────────

def test_leverage_cache_absent_is_unknown_never_green():
    out = _safety(None)
    assert out["level"] == "unknown"
    assert out["level"] != "green"


def test_stale_cash_is_unknown_never_green():
    # A stale debit figure could be describing a book that has since levered up.
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, stale=True))
    assert out["level"] == "unknown"


def test_zero_debit_without_cash_seen_is_unknown_not_green():
    # THE defect this contract exists for: a missing account_cash row and a
    # thrown DB read both fail-soft to margin_debit=0.0 / stale=False, which is
    # byte-identical to a genuinely unlevered book. Green here would be an
    # affirmative safety claim on zero evidence.
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, cash_seen=False))
    assert out["level"] == "unknown"
    assert out["reasons"], "must say WHY it cannot verify, not fail silently"


def test_zero_debit_with_cash_seen_is_green():
    # The one world that genuinely warrants green: we read the balance and it
    # showed no debit.
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, cash_seen=True))
    assert out["level"] == "green"


def test_unresolvable_negative_balance_is_unknown_not_green():
    """BLOCKING review finding 2026-08-28. The publisher only fills margin_debit
    when `cash_bal < 0 AND not stale AND net_capital > 0`. When the loan exceeds
    the book (net_capital <= 0) that update never fires, leaving margin_debit at
    0.0 — so an early `cash_seen = True` would have painted GREEN "no margin
    debt" on the single worst state a levered book can reach. The publisher now
    withholds cash_seen on any negative balance it could not resolve; this pins
    the consumer side of that contract."""
    unresolved = {"levered": False, "margin_debit": 0.0, "equity": 10000.0,
                  "net_capital": 10000.0, "ratio": 1.0, "stale": False,
                  "cash_seen": False}
    assert _safety(unresolved)["level"] == "unknown"


def test_drift_forces_red_even_on_an_unlevered_book():
    """BLOCKING review finding 2026-08-28. The drift leg used to be evaluated
    only AFTER the unlevered early-return, so the same drift fact rendered red
    on a levered book and green on an unlevered one — and the green strip
    contained its own contradicting "⚠ Drift" tile."""
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, cash_seen=True),
                  drift={"state": "drift"})
    assert out["drift_state"] == "drift"
    assert out["level"] == "red", "drift is an independent red leg, not a levered-only one"


def test_unlevered_without_drift_is_still_green():
    """Guard the fix above from over-reaching into a false alarm."""
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, cash_seen=True),
                  drift={"state": "unknown"})
    assert out["level"] == "green"


def test_headline_never_says_margin_on_a_book_with_no_margin_loan():
    """`red` has three causes, so the headline cannot be keyed on the level
    alone. An unlevered book flagged for broker drift used to render
    "🔴 Margin risk" directly above "Leverage 1.00×" / "Margin cushion —"."""
    out = _safety(_lev(debit=0, equity=24503, net_capital=24503, cash_seen=True),
                  drift={"state": "drift"})
    assert out["level"] == "red"
    assert "margin" not in out["headline"].lower()
    assert "drift" in out["headline"].lower()


def test_headline_names_the_dominant_cause_worst_first():
    in_call = _safety(_lev(debit=8000, equity=10000, net_capital=2000))
    assert "margin call" in in_call["headline"].lower()
    near = _safety(_lev(debit=6800, equity=10000, net_capital=3200))
    assert "margin call" in near["headline"].lower()
    amber = _safety(_lev(debit=6000, equity=10000, net_capital=4000))
    assert amber["level"] == "amber" and "levered" in amber["headline"].lower()


def test_every_level_carries_a_headline():
    for cache, drift in (
        (None, None),
        (_lev(debit=0, equity=1, net_capital=1, stale=True), None),
        (_lev(debit=0, equity=1, net_capital=1, cash_seen=False), None),
        (_lev(debit=0, equity=24503, net_capital=24503), None),
        (_lev(debit=6000, equity=10000, net_capital=4000), None),
        (_lev(debit=8000, equity=10000, net_capital=2000), {"state": "drift"}),
    ):
        out = _safety(cache, drift)
        assert out["headline"], f"no headline for level={out['level']}"


def test_legacy_cache_without_cash_seen_key_is_unknown():
    # A cache written before cash_seen existed (session predating the deploy).
    # .get() returns None -> falsy -> unknown. Safe direction.
    legacy = {"levered": False, "margin_debit": 0.0, "equity": 24503.0,
              "net_capital": 24503.0, "ratio": 1.0, "stale": False}
    assert _safety(legacy)["level"] == "unknown"


# ─── book_safety: levered classification ─────────────────────────────────────

def test_in_call_is_red():
    # Cushion <= 0: owner equity below the maintenance requirement.
    # equity 10000 * 0.25 = 2500 maintenance; net capital 2000 -> cushion -500.
    out = _safety(_lev(debit=8000, equity=10000, net_capital=2000))
    assert out["in_call"] is True
    assert out["level"] == "red"
    assert any("call" in r.lower() for r in out["reasons"])


def test_red_when_a_routine_pullback_would_trigger_a_call():
    # Construct a book whose call distance sits INSIDE the fragility yardstick.
    # Solve: |call_distance_pct| <= |FRAGILITY_PULLBACK_PCT| (10%).
    # cushion = net_capital - equity*rate ; denom = equity*(1-rate)
    # equity=10000, rate=.25 -> maint=2500, denom=7500.
    # Want cushion/7500*100 <= 10  ->  cushion <= 750  ->  net_capital <= 3250.
    out = _safety(_lev(debit=6800, equity=10000, net_capital=3200))
    assert out["in_call"] is False
    assert abs(out["call_distance_pct"]) <= abs(FRAGILITY_PULLBACK_PCT)
    assert out["level"] == "red"


def test_amber_when_cushion_sits_just_beyond_the_yardstick():
    # net_capital 4000 -> cushion 1500 -> 1500/7500*100 = 20% distance > 10%.
    out = _safety(_lev(debit=6000, equity=10000, net_capital=4000))
    assert abs(out["call_distance_pct"]) > abs(FRAGILITY_PULLBACK_PCT)
    assert out["level"] == "amber"


def test_the_boundary_itself_is_red_not_amber():
    # Exactly AT the yardstick. cushion=750 -> 750/7500*100 = 10.0% == 10.0.
    # The comparison is <=, so at-boundary is a breach.
    out = _safety(_lev(debit=6750, equity=10000, net_capital=3250))
    assert abs(out["call_distance_pct"]) == pytest.approx(abs(FRAGILITY_PULLBACK_PCT))
    assert out["level"] == "red"


# ─── book_safety: broker drift must not be over- or under-read ───────────────

def test_drift_detected_forces_red_even_on_a_comfortable_cushion():
    out = _safety(_lev(debit=6000, equity=10000, net_capital=4000),
                  drift={"state": "drift"})
    assert out["drift_state"] == "drift"
    assert out["level"] == "red"


def test_drift_unknown_does_not_read_as_drift():
    # "not checked" is not "clean" AND not "drift" — it must never manufacture
    # a red, nor be laundered into an in_sync claim.
    out = _safety(_lev(debit=6000, equity=10000, net_capital=4000),
                  drift={"state": "unknown"})
    assert out["drift_state"] == "not_checked"
    assert out["level"] == "amber"


def test_drift_cache_absent_is_not_checked_not_in_sync():
    out = _safety(_lev(debit=6000, equity=10000, net_capital=4000), drift=None)
    assert out["drift_state"] == "not_checked"


def test_drift_in_sync_is_reported_as_in_sync():
    out = _safety(_lev(debit=6000, equity=10000, net_capital=4000),
                  drift={"state": "in_sync"})
    assert out["drift_state"] == "in_sync"
    assert out["level"] == "amber"   # levered but clean


# ─── quote_change_pct: the single quote reader ───────────────────────────────
# These exist because the PREFERENCE ORDER is the whole point of the function
# and was otherwise unasserted — every other test here supplies prev_close, so
# the suite would have passed identically with the order reversed. That is the
# same feedback_validation_reads_detector_source trap as the deleted
# protective_signal_count tests, one level up: a fix nobody can observe failing.

def test_change_pct_is_preferred_over_recompute():
    """Finnhub (`dp`) and FMP (`changesPercentage`) can supply change_pct with a
    None prev_close. A recompute-only reader counts those tickers missing, while
    the Zone 6 table — which reads change_pct — shows real moves for them: one
    screen, two contradicting claims."""
    assert summary_view.quote_change_pct(
        {"price": 110.0, "prev_close": None, "change_pct": 3.2}
    ) == pytest.approx(3.2)


def test_recompute_is_used_when_change_pct_absent():
    assert summary_view.quote_change_pct(
        {"price": 110.0, "prev_close": 100.0}
    ) == pytest.approx(10.0)


def test_unusable_change_pct_falls_back_rather_than_crashing():
    assert summary_view.quote_change_pct(
        {"price": 110.0, "prev_close": 100.0, "change_pct": "n/a"}
    ) == pytest.approx(10.0)


def test_neither_field_usable_is_none_never_zero():
    for q in ({"price": 110.0, "prev_close": None},
              {"price": 110.0, "prev_close": 0.0},
              {}, None, "not-a-dict"):
        assert summary_view.quote_change_pct(q) is None, q


def test_a_genuine_zero_change_is_zero_not_none():
    # Distinguishing "unchanged" from "unknown" is the entire contract.
    assert summary_view.quote_change_pct(
        {"price": 100.0, "prev_close": 100.0}
    ) == pytest.approx(0.0)


# ─── top_movers: a missing quote is never a fabricated 0% ────────────────────

def _pdf(tickers, **cols):
    data = {"Ticker": list(tickers)}
    data.update(cols)
    return pd.DataFrame(data)


def test_missing_ticker_is_counted_missing_not_flat():
    df = _pdf(["AAA", "BBB"])
    prices = {"AAA": {"price": 110.0, "prev_close": 100.0}}
    out = summary_view.top_movers(df, prices)
    assert out["n_priced"] == 1
    assert out["n_missing"] == 1
    moved = [m["ticker"] for m in out["up"] + out["down"]]
    assert "BBB" not in moved, "an unpriced ticker must never appear as a mover"


def test_no_usable_change_is_missing_not_zero():
    """A quote with NEITHER a usable change_pct NOR a prev_close is missing.

    NB the contract is narrower than data.fetch_live_prices' docstring suggests:
    it says prev_close/change_pct go None together and are "never fabricated as
    0", but Finnhub and FMP can in fact supply change_pct with prev_close None
    (see test_change_pct_is_preferred_over_recompute). "Both unusable" is the
    real missing condition — an earlier version of this docstring stated the
    stronger invariant and would have taught the next reader the wrong rule."""
    df = _pdf(["AAA"])
    out = summary_view.top_movers(df, {"AAA": {"price": 110.0, "prev_close": None}})
    assert out["n_missing"] == 1
    assert out["up"] == [] and out["down"] == []


def test_a_flat_name_is_neither_up_nor_down():
    """0.00% is not a direction. It must also agree with day_direction_counts,
    which counts it `flat` — the two render one above the other."""
    df = _pdf(["FLAT"])
    prices = {"FLAT": {"price": 100.0, "prev_close": 100.0}}
    out = summary_view.top_movers(df, prices)
    assert out["up"] == [] and out["down"] == []
    assert out["n_priced"] == 1 and out["n_missing"] == 0
    assert summary_view.day_direction_counts(df, prices)["flat"] == 1


def test_live_prices_none_means_everything_missing():
    df = _pdf(["AAA", "BBB", "CCC"])
    out = summary_view.top_movers(df, None)
    assert out["n_priced"] == 0
    assert out["n_missing"] == 3
    assert out["up"] == [] and out["down"] == []


def test_up_and_down_are_split_and_ordered_by_magnitude():
    df = _pdf(["UP1", "UP2", "DN1", "DN2"])
    prices = {
        "UP1": {"price": 104.0, "prev_close": 100.0},   # +4%
        "UP2": {"price": 102.0, "prev_close": 100.0},   # +2%
        "DN1": {"price": 97.0,  "prev_close": 100.0},   # -3%
        "DN2": {"price": 99.0,  "prev_close": 100.0},   # -1%
    }
    out = summary_view.top_movers(df, prices, n=2)
    assert [m["ticker"] for m in out["up"]] == ["UP1", "UP2"]
    assert [m["ticker"] for m in out["down"]] == ["DN1", "DN2"]


def test_combined_ranks_by_magnitude_across_both_directions():
    """The KPI tile renders `combined`, not up/down. A biggest-loser must
    outrank a smaller gainer — direction is carried by the sign."""
    df = _pdf(["WDAY", "UBER", "MRVL", "NVDA"])
    prices = {
        "WDAY": {"price": 105.8, "prev_close": 100.0},   # +5.8
        "UBER": {"price": 102.4, "prev_close": 100.0},   # +2.4
        "MRVL": {"price": 89.7,  "prev_close": 100.0},   # -10.3
        "NVDA": {"price": 95.4,  "prev_close": 100.0},   # -4.6
    }
    out = summary_view.top_movers(df, prices, n=3)
    assert [m["ticker"] for m in out["combined"]] == ["MRVL", "WDAY", "NVDA"]


def test_combined_is_capped_so_the_tile_height_is_fixed():
    """The whole reason `combined` exists: an up/down split yields 0..2n rows,
    which made this tile taller than the st.metric tiles beside it."""
    df = _pdf([f"T{i}" for i in range(10)])
    prices = {f"T{i}": {"price": 100.0 + i + 1, "prev_close": 100.0} for i in range(10)}
    assert len(summary_view.top_movers(df, prices, n=3)["combined"]) == 3


def test_combined_excludes_flat_and_unpriced_names():
    df = _pdf(["MOVER", "FLAT", "NOQUOTE"])
    prices = {"MOVER": {"price": 103.0, "prev_close": 100.0},
              "FLAT":  {"price": 100.0, "prev_close": 100.0}}
    out = summary_view.top_movers(df, prices, n=3)
    assert [m["ticker"] for m in out["combined"]] == ["MOVER"]
    assert out["n_missing"] == 1


def test_combined_is_empty_when_nothing_is_priced():
    out = summary_view.top_movers(_pdf(["A", "B"]), None, n=3)
    assert out["combined"] == []


def test_n_caps_each_direction():
    df = _pdf(["A", "B", "C", "D"])
    prices = {t: {"price": 100.0 + i, "prev_close": 100.0}
              for i, t in enumerate(["A", "B", "C", "D"], start=1)}
    out = summary_view.top_movers(df, prices, n=2)
    assert len(out["up"]) == 2


def test_zero_prev_close_is_missing_not_infinite():
    df = _pdf(["AAA"])
    out = summary_view.top_movers(df, {"AAA": {"price": 10.0, "prev_close": 0.0}})
    assert out["n_missing"] == 1


def test_empty_portfolio_is_safe():
    out = summary_view.top_movers(pd.DataFrame({"Ticker": []}), {})
    assert out == {"up": [], "down": [], "n_priced": 0, "n_missing": 0}


def test_none_portfolio_is_safe():
    out = summary_view.top_movers(None, {})
    assert out["n_priced"] == 0


# ─── position_status_badge ────────────────────────────────────────────────────

def test_reduce_call_outranks_concentration_cap():
    # Already told to EXIT: the CAP badge adds nothing and would dilute it.
    badge = summary_view.position_status_badge(
        reduce_call={"_source": "act", "kind": "stop_breach"},
        weight_pct=99.0,
        single_name_ceiling=SINGLE_NAME_CEILING,
    )
    assert badge["label"] == "EXIT"


def test_exit_kinds_render_exit():
    for kind in ("stop_breach", "sell_signal", "deterioration_exit"):
        badge = summary_view.position_status_badge(
            reduce_call={"_source": "act", "kind": kind},
            weight_pct=1.0, single_name_ceiling=SINGLE_NAME_CEILING,
        )
        assert badge["label"] == "EXIT", kind


def test_trim_kinds_render_trim():
    for kind in ("deterioration_trim", "risk", "risk_off_derisk"):
        badge = summary_view.position_status_badge(
            reduce_call={"_source": "act", "kind": kind},
            weight_pct=1.0, single_name_ceiling=SINGLE_NAME_CEILING,
        )
        assert badge["label"] == "TRIM", kind


def test_review_origin_reduce_renders_trim():
    badge = summary_view.position_status_badge(
        reduce_call={"_source": "review", "action": {"type": "TRIM_TO_TARGET"}},
        weight_pct=1.0, single_name_ceiling=SINGLE_NAME_CEILING,
    )
    assert badge["label"] == "TRIM"


def test_cap_badge_fires_exactly_at_the_ceiling():
    # >= boundary, matching the sizing-cap convention elsewhere in the app.
    badge = summary_view.position_status_badge(
        reduce_call=None,
        weight_pct=SINGLE_NAME_CEILING,
        single_name_ceiling=SINGLE_NAME_CEILING,
    )
    assert badge is not None and badge["label"] == "CAP⚠"


def test_just_under_the_ceiling_is_clean():
    badge = summary_view.position_status_badge(
        reduce_call=None,
        weight_pct=SINGLE_NAME_CEILING - 0.01,
        single_name_ceiling=SINGLE_NAME_CEILING,
    )
    assert badge is None


# ─── day_direction_counts ─────────────────────────────────────────────────────

def test_day_direction_counts_splits_up_down_flat_missing():
    df = _pdf(["U", "D", "F", "M"])
    prices = {
        "U": {"price": 101.0, "prev_close": 100.0},
        "D": {"price": 99.0,  "prev_close": 100.0},
        "F": {"price": 100.0, "prev_close": 100.0},
    }
    out = summary_view.day_direction_counts(df, prices)
    assert out == {"up": 1, "down": 1, "flat": 1, "missing": 1}


def test_day_direction_unpriced_is_missing_never_flat():
    # The whole point: "no quote" and "unchanged" are different facts.
    df = _pdf(["M"])
    out = summary_view.day_direction_counts(df, None)
    assert out["missing"] == 1
    assert out["flat"] == 0


# ─── protective_signal_count: REMOVED, deliberately ──────────────────────────
# The Risk Posture fallback used to count EXIT/TRIM/WATCH out of
# port_df["Signal"]. That column is f"{icon} {label}" from build_portfolio_df,
# whose labels are Strong Buy / Buy / Hold / Sell / Strong Sell — a composite
# BAND, not a protective vocabulary. The matcher could only ever return 0, and
# the caller painted that 0 green as "no protective signals".
#
# The tests that lived here passed because they INVENTED the input
# ({"Signal": ["EXIT","TRIM","WATCH",...]}) rather than reading the producer.
# That is the failure mode feedback_validation_reads_detector_source names: a
# validator must read the same source the detector reads. Keeping this note
# where the tests were, so the next person to want a port_df-derived protective
# count checks scoring.py's label set first.
#
# The fallback now uses decision_bucket.bucket_act_by_type() on the Brief's own
# act bucket (covered in tests/test_decision_bucket.py).


# ─── avg_score_label ─────────────────────────────────────────────────────────

def test_avg_score_label_at_threshold_reads_above():
    assert summary_view.avg_score_label(COMPOSITE_BUY, COMPOSITE_BUY) == "above buy threshold"


def test_avg_score_label_below_threshold():
    assert summary_view.avg_score_label(COMPOSITE_BUY - 1, COMPOSITE_BUY) == "below buy threshold"
