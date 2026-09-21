"""
rec_events — readout half (Recommendation-Outcomes-Measurement Phase 1b,
docs/plans/recommendation-outcomes-measurement.md §10/§11).

Answers the question the capture half (`rec_events_capture.py` +
`db.save_rec_events`) exists to eventually answer: did the Rebalancer's
concentration/beta trims and the Diversification Advisor's ADD calls actually
move the portfolio metric they were computed against, and did the owner act
on them?

Pure — no Streamlit, no DB, no network I/O. All price/SPY-history/portfolio-
risk-snapshot dependencies are injected by the caller, mirroring
`gate_ledger_readout.py`'s own dependency-injection docstring.

**Redline (constants.py's REC_OUTCOME_* block): this is a RETROSPECTIVE
MEASUREMENT of the app's own past rebalancer/diversification calls. It feeds
NO gate, NO recommendation, NO composite, NO sizing path — awareness only.
Every threshold comparison in this feature lives HERE, never in app.py.**

**Hard redline (ratified 2026-09-15, §11): never port the BUY-side alpha-vs-
SPY shape onto rebal_trim/beta_trim.** Those two types are graded as a
portfolio-level predicted-vs-realized metric delta (beta / single-name-or-
sector weight), NOT a ticker-vs-SPY alpha. diversify_add gets TWO separate
legs — a candidate-return-vs-SPY leg captioned precisely as "the added name's
own performance" (never "diversification worked"), and a portfolio-level
correlation leg with `corr_coverage_n` disclosed at both ends.

Pipeline:
  - `collapse_by_rec_ticker()`   — one row per (rec_type, ticker) ever,
                                   earliest-fired_date-anchored.
  - `match_attribution()`        — did a trade act on this specific rec.
  - `enrich_and_grade()`         — maturity gate, attribution, cross-system
                                   overlap disclosure, per-type outcome legs.
  - `grade_by_rec_type()`        — banded (building/early/firm) verdict per
                                   rec_type x acted/skipped arm.
  - `readout_footnotes()`        — the per-type caveats a screen showing a
                                   rec_type's numbers must disclose alongside
                                   them.
  - `schedule_disclosure()`      — the §11-ratified 3-month sanity-check /
                                   12-month retirement dates, disclosure only
                                   (never auto-retires anything in code).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import pandas as pd

from stock_analyzer.predictive_analytics import _advance_trading_days, forward_alpha_at_horizon

STATUS_NOT_MATURED = "not_matured"
STATUS_MATURED = "matured"

_TRIM_TYPES = ("rebal_trim", "beta_trim", "single_name_concentration", "sector_concentration")
_DIRECTION_FOR_TYPE = {
    "rebal_trim":                  "SELL",
    "beta_trim":                   "SELL",
    "diversify_add":               "BUY",
    # 2026-09-21 app-review Part 2 #1 additions — both trims, same shape.
    "single_name_concentration":   "SELL",
    "sector_concentration":        "SELL",
}
# Confirming trigger_type per rec_type — a BOOST only, never required for
# `acted`. beta_trim has no dedicated quick-log trigger_type of its own (only
# the Rebalancer's own trim quick-log writes REBAL_TRIM, app.py:13721) — a
# manually-tagged REBAL_TRIM SELL matching a beta_trim episode is still
# treated as confirming, since it's the same underlying discretionary action
# (a sell reducing beta/concentration), not a re-derivation of a different
# rule. single_name_concentration / sector_concentration (2026-09-21) reuse
# REBAL_TRIM for the same reason — owner decision, no new quick-log tag.
_CONFIRMING_TRIGGER = {
    "rebal_trim":                  "REBAL_TRIM",
    "beta_trim":                   "REBAL_TRIM",
    "diversify_add":               "DIVERSIFY_ADD",
    "single_name_concentration":   "REBAL_TRIM",
    "sector_concentration":        "REBAL_TRIM",
}
# rec_types where a matching SELL may be of ANY ticker in the row's own
# `candidates` list, not just its own `ticker` field (which is always the
# FIRST/lowest-conviction candidate by construction). diversify_add does NOT
# need this: its own `ticker` IS candidates[0] and match_attribution's
# existing candidate-set check there is defense-in-depth only, never the
# primary match. sector_concentration DOES need it — the owner may
# reasonably sell the 2nd- or 3rd-ranked trim candidate instead of the
# lowest-conviction one and that should still credit the call.
_MATCH_ANY_CANDIDATE = frozenset({"sector_concentration"})


def _safe_float(x) -> "float | None":
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _to_date(v) -> "date | None":
    """Best-effort date coerce — mirrors gate_ledger_readout._to_date /
    recommendations_history._to_date. Unparseable input -> None (never a
    fabricated evaluable date)."""
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _traded_date_et(v) -> "date | None":
    """A `trades.traded_at` timestamptz (UTC) -> its America/New_York
    calendar date. Mirrors risk_advisor.py's `_bought_today` conversion — a
    trade filled after ~8pm ET must not roll into "tomorrow" by staying in
    UTC. Unparseable -> None."""
    if v is None:
        return None
    try:
        # format="ISO8601" — trades.traded_at genuinely mixes microsecond vs
        # second precision (raw-SQL rows vs Postgres now()); utc=True ALONE
        # does not fix that (pandas infers a strict format from the first
        # non-null value and silently NaTs the rest). See
        # feedback_pandas_mixed_tz_parsing.
        ts = pd.to_datetime(v, utc=True, format="ISO8601", errors="coerce")
        if pd.isna(ts):
            return None
        return ts.tz_convert("America/New_York").date()
    except Exception:
        return None


# ── Collapse ─────────────────────────────────────────────────────────────────

def collapse_by_rec_ticker(rows: "list[dict]") -> "list[dict]":
    """One row per distinct (rec_type, ticker) ever recorded — extends
    `protective_track_record.collapse_by_ticker`'s earliest-anchored pattern,
    keyed on the pair instead of just ticker (§11 ratified decision 4 / the
    plan's "Collapse rule"). No episode-gap-reopening mechanism: a
    (rec_type, ticker) that fires on 10 consecutive days collapses to ONE
    row, anchored at the earliest `fired_date` among them.

    Rows missing `rec_type` or `ticker` are dropped (nothing to key on).
    Order of the returned list is not guaranteed.
    """
    groups: "dict[tuple, list[dict]]" = {}
    for r in rows or []:
        rt = r.get("rec_type")
        tk = r.get("ticker")
        if not rt or not tk:
            continue
        groups.setdefault((rt, tk), []).append(r)

    out: "list[dict]" = []
    for (_rt, _tk), grp in groups.items():
        dated = [r for r in grp if _to_date(r.get("fired_date")) is not None]
        rep = min(dated, key=lambda r: _to_date(r["fired_date"])) if dated else grp[0]
        out.append(dict(rep))
    return out


# ── Attribution ──────────────────────────────────────────────────────────────

def match_attribution(
    rec: dict,
    trades: "list[dict] | None",
    action_window_trading_days: int,
) -> dict:
    """Did a logged trade act on this specific rec_events row.

    A trade counts as "acted on" this rec iff ALL of:
      (a) trade ticker == rec['ticker'] EXACTLY (a SELL of a DIFFERENT
          high-beta/oversized name never credits this rec —§11 ratified
          decision 3, re-applied here for rebal_trim/beta_trim, and the
          natural reading of "the named ticker" for diversify_add too) —
          EXCEPT for `rec_type`s in `_MATCH_ANY_CANDIDATE` (currently just
          `sector_concentration`), where a SELL of ANY ticker in the row's
          own `candidates` list credits the call, not just `ticker` itself
          (which is only the lowest-conviction candidate by construction —
          the owner may reasonably sell a different one of the named
          trim candidates instead).
      (b) trade action matches the rec_type's expected direction (SELL for
          rebal_trim/beta_trim/single_name_concentration/
          sector_concentration, BUY for diversify_add).
      (c) traded_date (ET) within [fired_date, fired_date +
          action_window_trading_days] trading days, INCLUSIVE both ends.
    For diversify_add, an additional defense-in-depth check: the bought
    ticker must also be present in the rec's own `candidates` jsonb (the
    row's own `ticker` is always its own candidates[0] by construction, so
    this only ever bites a corrupted/malformed row) — a no-op for
    sector_concentration since (a) above already requires candidate
    membership there.

    `trigger_type` is a CONFIRMING BOOST only, never required — a manual/
    untagged trade matching the above still counts as acted
    (`attribution_confirmed=False` in that case).

    Returns {"acted": bool, "attribution_confirmed": bool,
    "matched_trade": dict | None} — the EARLIEST qualifying trade wins when
    more than one matches.
    """
    _none = {"acted": False, "attribution_confirmed": False, "matched_trade": None}

    rec_type = rec.get("rec_type")
    direction = _DIRECTION_FOR_TYPE.get(rec_type)
    ticker = str(rec.get("ticker", "") or "").strip().upper()
    fired_date = _to_date(rec.get("fired_date"))
    if not ticker or direction is None or fired_date is None:
        return _none

    match_any = rec_type in _MATCH_ANY_CANDIDATE
    candidate_set = None
    if rec_type == "diversify_add" or match_any:
        _raw_candidates = rec.get("candidates")
        candidate_set = {
            str(c).strip().upper()
            for c in (_raw_candidates if isinstance(_raw_candidates, list) else [])
            if c
        }

    window_end = _advance_trading_days(fired_date, action_window_trading_days)

    best: "tuple[date, dict] | None" = None
    for tr in trades or []:
        tk = str(tr.get("ticker", "") or "").strip().upper()
        if match_any:
            if not candidate_set or tk not in candidate_set:
                continue
        elif tk != ticker:
            continue
        action = str(tr.get("action", "") or "").strip().upper()
        if action != direction:
            continue
        traded_date = _traded_date_et(tr.get("traded_at"))
        if traded_date is None:
            continue
        if traded_date < fired_date or traded_date > window_end:
            continue
        if candidate_set is not None and tk not in candidate_set:
            continue
        if best is None or traded_date < best[0]:
            best = (traded_date, tr)

    if best is None:
        return _none

    _, matched = best
    trigger = str(matched.get("trigger_type", "") or "").strip().upper()
    confirmed = trigger == _CONFIRMING_TRIGGER.get(rec_type)
    return {"acted": True, "attribution_confirmed": confirmed, "matched_trade": matched}


# ── Outcome legs ─────────────────────────────────────────────────────────────

def _compute_outcome(
    r: dict,
    fired_date: date,
    horizon_trading_days: int,
    risk_snapshot_by_date: dict,
    spy_close_by_date: "dict | None",
    historical_close_fn: "Callable[[str, date, date], float | None] | None",
) -> dict:
    rec_type = r.get("rec_type")
    target_date = _advance_trading_days(fired_date, horizon_trading_days)
    _snap_map = risk_snapshot_by_date or {}
    target_row = _snap_map.get(target_date.isoformat())
    target_row = target_row if isinstance(target_row, dict) else {}

    if rec_type == "beta_trim":
        return {
            "predicted": _safe_float(r.get("metric_predicted_after")),
            "realized_portfolio_beta": _safe_float(target_row.get("portfolio_beta")),
            "caption": (
                "Portfolio-level proxy — other trades and price moves also "
                "affect portfolio beta, not just this trim."
            ),
        }

    if rec_type in ("rebal_trim", "single_name_concentration"):
        # single_name_concentration (2026-09-21) folds into this exact branch
        # rather than getting its own — both measure the SAME realized
        # substrate (max_single_name_pct), just from a different trigger
        # condition on the capture side (profitable+oversized vs strong-
        # conviction+oversized). Deduped at capture time (see
        # rec_events_capture._build_single_name_conc_rows) so a ticker never
        # produces both a rebal_trim and a single_name_concentration row.
        return {
            "predicted": _safe_float(r.get("metric_predicted_after")),
            "realized_max_single_name_pct": _safe_float(target_row.get("max_single_name_pct")),
            "realized_top_sector_pct": _safe_float(target_row.get("top_sector_pct")),
            "caption": (
                "Portfolio-level proxy — the daily snapshot stores only the "
                "book's MAX single-name/sector weight, not this specific "
                "ticker's own weight history."
            ),
        }

    if rec_type == "sector_concentration":
        realized_top_sector = target_row.get("top_sector")
        return {
            "predicted": _safe_float(r.get("metric_predicted_after")),
            "realized_top_sector_pct": _safe_float(target_row.get("top_sector_pct")),
            "realized_top_sector": realized_top_sector,
            # None (not True/False) when the horizon snapshot itself is
            # missing — "did the top sector change" is unanswerable then,
            # never silently read as "no, unchanged".
            "top_sector_changed": (
                (realized_top_sector != r.get("sector")) if realized_top_sector is not None else None
            ),
            "caption": (
                "Portfolio-level proxy — the daily snapshot stores only the "
                "book's MAX single-name/sector weight, not this specific "
                "sector's own weight history. If the book's top sector "
                "rotated by the horizon date, the realized figure describes "
                "a DIFFERENT sector, not this one's improvement."
            ),
        }

    if rec_type == "diversify_add":
        price_entry = _safe_float(r.get("price_at_rec"))
        leg_a_alpha = None
        if price_entry:
            try:
                leg_a_alpha = forward_alpha_at_horizon(
                    r.get("ticker"), fired_date, price_entry, horizon_trading_days,
                    spy_close_by_date, historical_close_fn,
                )
            except Exception:
                leg_a_alpha = None
        return {
            "leg_a_candidate_alpha_pct": leg_a_alpha,
            "leg_a_caption": (
                "The added name's own performance vs SPY — never read as "
                "'diversification worked.'"
            ),
            "leg_b_avg_corr_before": _safe_float(r.get("metric_before")),
            "leg_b_corr_coverage_n_before": r.get("corr_coverage_n"),
            "leg_b_avg_corr_after": _safe_float(target_row.get("avg_pairwise_corr")),
            "leg_b_corr_coverage_n_after": target_row.get("corr_coverage_n"),
            "leg_b_caption": (
                "Portfolio-level proxy; sample size (corr_coverage_n) "
                "disclosed at both the rec-time and horizon-time snapshot — "
                "a listwise sample-size shift must never be misread as a "
                "real diversification change."
            ),
        }

    return {}


# ── Main enrichment pass ─────────────────────────────────────────────────────

def enrich_and_grade(
    rows: "list[dict]",
    *,
    today: date,
    trades: "list[dict] | None",
    horizon_trading_days: int,
    action_window_trading_days: int,
    protective_call_tickers: "set[str] | None" = None,
    risk_snapshot_by_date: "dict | None" = None,
    spy_close_by_date: "dict | None" = None,
    historical_close_fn: "Callable[[str, date, date], float | None] | None" = None,
) -> "list[dict]":
    """Classify + grade every (already-collapsed) rec_events row.

    Order (maturity checked FIRST, before any other computation — matches
    `gate_ledger_readout.enrich_and_grade`'s own ordering discipline):
      1. Maturity: unparseable `fired_date`, or `fired_date +
         horizon_trading_days` trading sessions still in the future ->
         `status="not_matured"`. Every other field on a not-yet-matured row
         is left at its neutral default (acted=False, outcome=None) — never
         a fabricated evaluable state.
      2. Attribution (`match_attribution`) -> `acted` / `attribution_confirmed`.
      3. Cross-system overlap: if `acted` and this ticker also carries an
         active Exit-Advisor protective call (`protective_call_tickers`),
         `overlap_with_exit_advisor=True` — BOTH systems are credited; this
         flag is disclosure, never a reason to withhold credit from either.
      4. Outcome legs (`_compute_outcome`), per rec_type — computed
         regardless of `acted`, since the portfolio-level metric moves (or
         doesn't) independent of whether THIS specific call was the one
         acted on.

    Returns one dict per input row plus `status`/`acted`/
    `attribution_confirmed`/`overlap_with_exit_advisor`/`outcome`. No row is
    ever dropped.
    """
    out: "list[dict]" = []
    trades = trades or []
    protective_call_tickers = {str(t).strip().upper() for t in (protective_call_tickers or set())}
    risk_snapshot_by_date = risk_snapshot_by_date or {}

    for row in rows or []:
        r = dict(row)
        r["ticker"] = str(r.get("ticker", "") or "").strip().upper()
        r["acted"] = False
        r["attribution_confirmed"] = False
        r["overlap_with_exit_advisor"] = False
        r["outcome"] = None

        fired_date = _to_date(r.get("fired_date"))
        if fired_date is None:
            r["status"] = STATUS_NOT_MATURED
            out.append(r)
            continue

        target_date = _advance_trading_days(fired_date, horizon_trading_days)
        if target_date > today:
            r["status"] = STATUS_NOT_MATURED
            out.append(r)
            continue

        r["status"] = STATUS_MATURED

        attribution = match_attribution(r, trades, action_window_trading_days)
        r["acted"] = attribution["acted"]
        r["attribution_confirmed"] = attribution["attribution_confirmed"]
        if attribution["acted"] and r["ticker"] in protective_call_tickers:
            r["overlap_with_exit_advisor"] = True

        r["outcome"] = _compute_outcome(
            r, fired_date, horizon_trading_days, risk_snapshot_by_date,
            spy_close_by_date, historical_close_fn,
        )

        out.append(r)

    return out


# ── Banding ──────────────────────────────────────────────────────────────────

def grade_by_rec_type(
    enriched: "list[dict]",
    *,
    rec_type: str,
    arm: str,
    min_calls: int,
    firm_calls: int,
    min_tickers: int,
) -> dict:
    """Banded (building/early/firm) verdict for one rec_type x acted/skipped
    arm. Two floors, both required to leave "building" — mirrors
    `gate_ledger_readout.grade_by_gate`'s "N_min"/"K" pattern: `min_calls`
    matured rows AND `min_tickers` distinct tickers.

    `arm` must be "acted" or "skipped" — any other value scopes to nothing
    (an empty, "building" result) rather than silently matching everything.
    """
    want_acted = arm == "acted"
    if arm not in ("acted", "skipped"):
        rows: "list[dict]" = []
    else:
        rows = [
            r for r in enriched
            if r.get("rec_type") == rec_type
            and r.get("status") == STATUS_MATURED
            and bool(r.get("acted")) == want_acted
        ]

    n_calls = len(rows)
    n_tickers = len({r.get("ticker") for r in rows if r.get("ticker")})

    if n_calls < min_calls or n_tickers < min_tickers:
        band = "building"
    elif n_calls < firm_calls:
        band = "early"
    else:
        band = "firm"

    return {
        "rec_type": rec_type,
        "arm": arm,
        "band": band,
        "n_calls": n_calls,
        "n_distinct_tickers": n_tickers,
    }


# ── Footnotes / schedule disclosure ──────────────────────────────────────────

def readout_footnotes(rec_type: str) -> "list[str]":
    """0-2 short caveats a screen showing `rec_type`'s numbers must disclose
    alongside them (§11 / the plan's outcome-leg captions)."""
    if rec_type == "beta_trim":
        return [
            "Portfolio-level proxy — other trades and price moves also "
            "affect portfolio beta, not just this trim.",
        ]
    if rec_type == "rebal_trim":
        return [
            "Portfolio-level proxy — the daily snapshot stores only the "
            "book's MAX single-name/sector weight, not this specific "
            "ticker's own weight history.",
        ]
    if rec_type == "single_name_concentration":
        return [
            "Portfolio-level proxy — the daily snapshot stores only the "
            "book's MAX single-name/sector weight, not this specific "
            "ticker's own weight history.",
            "This population is specifically the oversized names Rebalancer "
            "Trim does NOT catch — not yet profitable enough to trigger that "
            "rec, but flagged here on size alone regardless of conviction.",
        ]
    if rec_type == "sector_concentration":
        return [
            "Portfolio-level proxy — the daily snapshot stores only the "
            "book's MAX single-name/sector weight, not this specific "
            "sector's own weight history.",
            "If the book's top sector at the horizon date is a DIFFERENT "
            "sector than the one this call was about, the realized figure "
            "describes that different sector, not this one's improvement — "
            "check the disclosed top-sector-changed flag before reading it "
            "as this sector cooling off.",
        ]
    if rec_type == "diversify_add":
        return [
            "The candidate-return leg is the added name's own performance "
            "vs SPY — never read as 'diversification worked.'",
            "The correlation leg discloses sample size (corr_coverage_n) at "
            "both the rec-time and horizon-time snapshot — a listwise "
            "sample-size shift must never be misread as a real "
            "diversification change.",
        ]
    return []


def earliest_fired_date(rows: "list[dict]") -> "date | None":
    """Earliest `fired_date` across ALL rows (typically the collapsed set) —
    the anchor `schedule_disclosure` needs. `None` when no row carries a
    parseable date."""
    dates = [d for d in (_to_date(r.get("fired_date")) for r in (rows or [])) if d is not None]
    return min(dates) if dates else None


def schedule_disclosure(min_fired_date: "date | None", today: date) -> dict:
    """The §11-ratified 3-month sanity-check / 12-month retirement dates,
    anchored on the EARLIEST `fired_date` across ALL rec_events ever
    captured (not per rec_type) — disclosure only, this function never
    retires anything; a caller reads `retirement_due` and writes the actual
    retirement copy by hand.

    `min_fired_date=None` (no rec_events captured yet) -> both dates None,
    both `_due` flags False — never a fabricated schedule.
    """
    if min_fired_date is None:
        return {
            "sanity_check_date": None, "retirement_date": None,
            "sanity_check_due": False, "retirement_due": False,
        }
    sanity_date = min_fired_date + timedelta(days=90)
    retirement_date = min_fired_date + timedelta(days=365)
    return {
        "sanity_check_date": sanity_date,
        "retirement_date": retirement_date,
        "sanity_check_due": today >= sanity_date,
        "retirement_due": today >= retirement_date,
    }
