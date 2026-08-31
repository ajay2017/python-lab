"""
Gate Suppression Ledger — READOUT half (F-259 Phase 2).

The capture half (`gate_ledger.py` + `db.save_gate_suppressions`) already
ships and writes one row per gate-suppressed pick/add. This module answers
the question the capture half exists to eventually answer: **did the app's
own restraint help or hurt**, measured per-gate as forward alpha vs SPY over
the pre-registered `GATE_LEDGER_HORIZON_TRADING_DAYS` window
(`docs/plans/gate-suppression-ledger.md` §5).

Pure — no Streamlit, no DB, no network I/O. All price/SPY history is
dependency-injected by the caller (app.py's `_cached_spy` /
`_cached_historical_close`), mirroring
`predictive_analytics.forward_alpha_at_horizon`'s own pattern exactly — this
module reuses that function rather than writing a second alpha calculation
(§5: "do not write a second alpha").

**Redline (constants.py's GATE_LEDGER_* block): this is a RETROSPECTIVE
MEASUREMENT of the app's own past restraint. It feeds NO gate, NO
recommendation, NO composite, NO sizing path — awareness only. Every
threshold comparison in this feature lives HERE, never in app.py:
check_antipatterns.py's POLICY_DECISION_IN_RENDER rule fails a NEW
constants.py comparison landing in app.py/cron_runner.py.**

Two functions do the work:
  - `enrich_and_grade()` — per-row maturity + scope classification + alpha.
  - `grade_by_gate()`    — per-gate aggregation into a headline verdict,
                           mirroring `protective_track_record.protective_
                           headline`'s exact banding structure (with an
                           added distinct-ticker floor, §5's "K").
Plus `readout_footnotes()` — the §5a per-gate caveats a screen showing a
gate's number must disclose alongside it, so a bare count can't be
misread.

Scope filters applied per row (§5 / §5a — see each check's comment for the
citation):
  - `counterfactual != True`            → context, not evidence (§5, F3)
  - `gate_id == "G-01" and source != "app"` → a verdict over an empty set (§5, F1)
  - `ticker == "__MARKET__"` (G-23)      → no per-ticker instrument to price (§5a-3)
  - `lane == "new_pick"` rows additionally require `composite_score >= composite_buy`
    (§5a-1) — a NULL composite is excluded but counted SEPARATELY
    (`excluded_null_composite`), never silently folded either way. Add-lane
    rows (`lane == "add_winner"`) get NO composite filter: reaching an
    add-lane gate site already proves the name would have been an add.

No row is ever dropped from `enrich_and_grade`'s output — every row gets a
`status` tag, even an excluded one, so the counts a screen shows always sum
back to the input population.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

from stock_analyzer import gate_registry
from stock_analyzer.predictive_analytics import _advance_trading_days, forward_alpha_at_horizon

# ── row status tags ─────────────────────────────────────────────────────────
STATUS_NOT_MATURED = "not_matured"
STATUS_MATURED_UNPRICEABLE = "matured_unpriceable"
STATUS_MATURED_EVALUABLE = "matured_evaluable"
STATUS_EXCLUDED_COUNTERFACTUAL_FALSE = "excluded_counterfactual_false"
STATUS_EXCLUDED_SOURCE_MISMATCH = "excluded_source_mismatch"
STATUS_EXCLUDED_LOW_COMPOSITE = "excluded_low_composite"
STATUS_EXCLUDED_NULL_COMPOSITE = "excluded_null_composite"
STATUS_MARKET_WIDE = "market_wide"

_MARKET_TICKER = "__MARKET__"
_G01 = "G-01"
_G23 = "G-23"
_NEW_PICK_LANE = "new_pick"


def _to_date(v) -> "date | None":
    """Best-effort date coerce — mirrors recommendations_history._to_date."""
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _safe_float(x) -> "float | None":
    """NaN/None/non-numeric-safe float coerce. Used for composite_score (a
    filter input) and price_at_suppress (fed to forward_alpha_at_horizon,
    which already no-ops on a falsy/non-positive price, but a raw
    Decimal/str from the DB client must not raise before it gets there)."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:   # NaN
        return None
    return v


def enrich_and_grade(
    rows: "list[dict]",
    *,
    today: date,
    spy_close_by_date: dict,
    historical_close_fn: "Callable[[str, date, date], float | None]",
    horizon_trading_days: int,
    composite_buy: float,
) -> "list[dict]":
    """
    Classify + grade every gate_suppressions row, one at a time.

    Order of checks (each returns immediately on match — a row gets exactly
    one status):
      1. Market-wide (`ticker == "__MARKET__"`, i.e. G-23) → `market_wide`.
         Never reaches `forward_alpha_at_horizon` — there is no instrument.
      2. Non-binding (`counterfactual is not True`) → `excluded_counterfactual_false`.
      3. G-01 over a non-`app` source → `excluded_source_mismatch`.
      4. New-pick lane (`lane == "new_pick"`) below/without a composite:
         `excluded_null_composite` (composite_score is None/non-numeric) or
         `excluded_low_composite` (composite_score < composite_buy).
      5. Maturity — `rec_date + horizon_trading_days` trading sessions vs
         `today`, checked BEFORE any fetch (mirrors
         `judgment_grading.grade_ticker_opinion`'s exact pattern) so "not
         matured yet" is never confused with a fetch failure below.
         → `not_matured` when still in the future (or `rec_date` is
         unparseable — treated as never-yet-matured rather than a fabricated
         evaluable state).
      6. Priced via `forward_alpha_at_horizon` (reused verbatim, not
         reimplemented). `None` → `matured_unpriceable` (a genuine data gap:
         missing/non-positive `price_at_suppress`, or the forward
         close/SPY window couldn't be found) — counted separately from
         "not yet matured". Otherwise → `matured_evaluable`, with `alpha_pct`
         set to the (already dependency-injected, already rounded) result.

    Returns one dict per input row — the original fields plus `status` (one
    of the tags above) and `alpha_pct` (`None` for every status except
    `matured_evaluable`). No row is ever dropped.
    """
    out: "list[dict]" = []
    for row in rows or []:
        r = dict(row)
        r["alpha_pct"] = None

        ticker = str(r.get("ticker", "") or "").strip().upper()
        gate_id = r.get("gate_id")
        lane = r.get("lane")
        source = r.get("source")
        counterfactual = r.get("counterfactual")

        # 1. Market-wide — no per-ticker instrument to price, ever (§5a-3).
        if ticker == _MARKET_TICKER or gate_id == _G23:
            r["status"] = STATUS_MARKET_WIDE
            out.append(r)
            continue

        # 2. Non-binding rows are context, not evidence (§5, F3).
        if counterfactual is not True:
            r["status"] = STATUS_EXCLUDED_COUNTERFACTUAL_FALSE
            out.append(r)
            continue

        # 3. G-01 is only meaningful over source='app' rows (§5, F1) — a
        #    cron row means risk_advisor recs were never even computed.
        if gate_id == _G01 and source != "app":
            r["status"] = STATUS_EXCLUDED_SOURCE_MISMATCH
            out.append(r)
            continue

        # 4. New-pick-lane composite filter (§5a-1). Add-lane rows
        #    (lane == "add_winner") and the never-reached tone lane get no
        #    such filter — reaching an add-lane site already proves the
        #    name would have been an add regardless of composite.
        if lane == _NEW_PICK_LANE:
            _cs = _safe_float(r.get("composite_score"))
            if _cs is None:
                r["status"] = STATUS_EXCLUDED_NULL_COMPOSITE
                out.append(r)
                continue
            if _cs < composite_buy:
                r["status"] = STATUS_EXCLUDED_LOW_COMPOSITE
                out.append(r)
                continue

        # 5. Maturity — checked before any fetch.
        rec_date = _to_date(r.get("rec_date"))
        if rec_date is None:
            r["status"] = STATUS_NOT_MATURED
            out.append(r)
            continue
        target_date = _advance_trading_days(rec_date, horizon_trading_days)
        if target_date > today:
            r["status"] = STATUS_NOT_MATURED
            out.append(r)
            continue

        # 6. Matured — price it. Reuses forward_alpha_at_horizon verbatim;
        #    this module never computes its own return/alpha arithmetic.
        alpha = forward_alpha_at_horizon(
            ticker, rec_date, _safe_float(r.get("price_at_suppress")),
            horizon_trading_days, spy_close_by_date, historical_close_fn,
        )
        if alpha is None:
            r["status"] = STATUS_MATURED_UNPRICEABLE
            out.append(r)
            continue

        r["status"] = STATUS_MATURED_EVALUABLE
        r["alpha_pct"] = alpha
        out.append(r)

    return out


def grade_by_gate(
    enriched: "list[dict]",
    *,
    gate_ids: "tuple[str, ...]",
    min_calls: int,
    firm_calls: int,
    min_tickers: int,
) -> "list[dict]":
    """
    Aggregate `enrich_and_grade`'s output into one summary dict per gate id,
    in the SAME order as `gate_ids` (never re-sorted — a stable, predictable
    render order matters more than any ranking here).

    Banding mirrors `protective_track_record.protective_headline`'s exact
    if/elif/else structure, with one addition: TWO floors gate the
    building → early transition (§5's "N_min" and "K"), not just one — a
    gate needs both `min_calls` matured-evaluable rows AND `min_tickers`
    distinct tickers to leave "building". Only `min_calls`/`firm_calls`
    gate the early → firm transition; `min_tickers` never re-enters there
    (once a gate has cleared both floors once, later concentration in one
    ticker doesn't demote it back to "building").

    G-23 (market-wide, no per-ticker instrument — §5a-3) is handled as a
    special minimal case: no banding, no mean alpha, never counted toward
    the "no gate produced a verdict" retirement tally.
    """
    out: "list[dict]" = []
    for gid in gate_ids:
        gate_rows = [r for r in enriched if r.get("gate_id") == gid]
        gate_description = gate_registry.GATE_IDS.get(gid, "")

        if gid == _G23:
            out.append({
                "gate_id": gid,
                "gate_description": gate_description,
                "market_wide": True,
            })
            continue

        evaluable = [r for r in gate_rows if r.get("status") == STATUS_MATURED_EVALUABLE]
        n_matured_evaluable = len(evaluable)
        n_distinct_tickers_evaluable = len({
            r.get("ticker") for r in evaluable if r.get("ticker")
        })
        n_not_matured = sum(1 for r in gate_rows if r.get("status") == STATUS_NOT_MATURED)
        n_matured_unpriceable = sum(
            1 for r in gate_rows if r.get("status") == STATUS_MATURED_UNPRICEABLE
        )
        n_excluded_null_composite = sum(
            1 for r in gate_rows if r.get("status") == STATUS_EXCLUDED_NULL_COMPOSITE
        )
        n_excluded_low_composite = sum(
            1 for r in gate_rows if r.get("status") == STATUS_EXCLUDED_LOW_COMPOSITE
        )
        n_excluded_counterfactual_false = sum(
            1 for r in gate_rows if r.get("status") == STATUS_EXCLUDED_COUNTERFACTUAL_FALSE
        )
        n_excluded_source_mismatch = sum(
            1 for r in gate_rows if r.get("status") == STATUS_EXCLUDED_SOURCE_MISMATCH
        )

        # Two floors, both required to leave "building" (§5's N_min and K).
        if n_matured_evaluable < min_calls or n_distinct_tickers_evaluable < min_tickers:
            band = "building"
        elif n_matured_evaluable < firm_calls:
            band = "early"
        else:
            band = "firm"

        mean_alpha_pct = (
            round(sum(r["alpha_pct"] for r in evaluable) / n_matured_evaluable, 2)
            if n_matured_evaluable else None
        )

        since_dates = [d for d in (_to_date(r.get("rec_date")) for r in gate_rows) if d is not None]
        since_date = min(since_dates) if since_dates else None

        out.append({
            "gate_id": gid,
            "gate_description": gate_description,
            "market_wide": False,
            "n_matured_evaluable": n_matured_evaluable,
            "n_distinct_tickers_evaluable": n_distinct_tickers_evaluable,
            "n_not_matured": n_not_matured,
            "n_matured_unpriceable": n_matured_unpriceable,
            "n_excluded_null_composite": n_excluded_null_composite,
            "n_excluded_low_composite": n_excluded_low_composite,
            "n_excluded_counterfactual_false": n_excluded_counterfactual_false,
            "n_excluded_source_mismatch": n_excluded_source_mismatch,
            "band": band,
            "mean_alpha_pct": mean_alpha_pct,
            "since_date": since_date,
        })

    return out


def readout_footnotes(gate_id: str) -> "list[str]":
    """0-2 short caveats a screen showing `gate_id`'s number must disclose
    alongside it (§5a) — found by the Opus reviewer BEFORE any readout data
    existed, specifically because a naive reading of the raw count would
    draw a confident wrong conclusion. Every other gate id returns []."""
    if gate_id == "G-20":
        return [
            "This gate's binding (counterfactual=true) count is a bull-day-only "
            "undercount: the in-loop upgrade only fires when tone == 'bull', and "
            "the table is first-writer-wins, so an intraday flat→bull tone flip "
            "keeps the morning's non-binding row and discards the later binding "
            "one. A low count here must not be read as ‘the gate rarely binds.’",
        ]
    if gate_id == "G-23":
        return [
            "Market-wide restraint (ticker=‘__MARKET__’) — there is no single "
            "instrument to price, so this gate is excluded from the evaluable "
            "tally by design, not because it hasn't produced enough data.",
        ]
    return []
