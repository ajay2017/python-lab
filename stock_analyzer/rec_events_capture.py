"""
rec_events — capture half (Recommendation-Outcomes-Measurement Phase 1b,
docs/plans/recommendation-outcomes-measurement.md §10/§11).

Pure. No Streamlit, no DB, no network I/O — every price this module needs is
dependency-injected via the optional `price_fn` callback, mirroring the
convention `gate_ledger_readout.py`'s docstring documents for its own
price/SPY dependencies. Called once per EOD cron run (`cron_runner.py`,
immediately after step 1c's `build_portfolio_risk_snapshot` call) with that
SAME run's already-computed `port_df` / `port_risk` / `held_data`, plus
`db.load_trades()` and the already-resolved `sector_candidates` /
`discovery_universe` reference payloads — no second fetch of any kind.

Three independent generators, one row (or list of rows) per rec_type, EACH
wrapped in its own try/except in `build_rec_event_rows` so one generator's
failure can never blank the others (mirrors
`risk_metric_history.build_portfolio_risk_snapshot`'s isolation pattern):

  - `rebal_trim`   — from `portfolio.rebalance_actions()`'s "trim" branch
                     (oversized + profitable position). One row per
                     over-threshold ticker that day.
  - `beta_trim`    — reproduces (does NOT import) `risk_advisor.py`'s beta-
                     card top-contributor trim pick, reading `port_df`/
                     `port_risk`/`held_data` directly, so `risk_advisor.py`
                     itself stays untouched (a `_GATE_FILES` member — no
                     reason to pull it into this review). At most one row per
                     day (the beta card names exactly one trim candidate).
  - `diversify_add`— from `portfolio.diversification_recommendations()`'s
                     "ADD" entries. One row per underweight diversifying
                     sector that day, `ticker` = the FIRST (highest-priority,
                     roster-before-discovery-bucket) candidate in that
                     sector's surfaced pool.

NULL-preserving contract (this project has shipped and fixed the fabricated-
neutral bug class twice — feedback_sentinel_is_present /
feedback_overloaded_producer_state): every numeric field is `None` when its
input is unavailable, NEVER a fabricated 0/neutral. `diversification_recommendations`
is called with a dummy `corr_df`/`div_result` — both parameters are unused by
its ADD-generation code path (confirmed against HEAD; `corr_df` isn't
referenced anywhere in the function body, and `div_result` is only consulted
by the REDUCE/PAIR_RISK branches, which this module never reads) — so no
second correlation-matrix computation is needed here. `avg_pairwise_corr` /
`corr_coverage_n` for the diversify_add rows are read from the SAME EOD run's
already-computed `portfolio_risk_snapshots`-shaped row (risk_metric_history.
build_portfolio_risk_snapshot's return value) rather than recomputed a second
time.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from stock_analyzer.beta_repair import expected_beta_after_trim
from stock_analyzer.constants import PORTFOLIO_BETA_ELEVATED, SINGLE_NAME_CEILING
from stock_analyzer.portfolio import diversification_recommendations, rebalance_actions

_SOURCE = "cron"


def _safe_float(x) -> "float | None":
    """NaN/None/non-numeric-safe float coerce — never a fabricated 0."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # NaN check


def _fired_date_str(fired_date) -> str:
    return fired_date.isoformat() if hasattr(fired_date, "isoformat") else str(fired_date)[:10]


# ── rebal_trim ───────────────────────────────────────────────────────────────

def _build_rebal_trim_rows(port_df, fired_date_str: str) -> "list[dict]":
    """One row per `rebalance_actions()` "trim" action that day.

    `metric_before` = the ticker's current portfolio weight; `metric_predicted_
    after` = `SINGLE_NAME_CEILING` (the target the trim rec is sized against);
    `rec_dollars` = the recommended trim amount (`trim_val`).
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    try:
        actions = rebalance_actions(port_df)
    except Exception:
        return []

    rows: "list[dict]" = []
    for act in actions or []:
        if act.get("type") != "trim":
            continue
        ticker = str(act.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        rows.append({
            "rec_type":               "rebal_trim",
            "ticker":                 ticker,
            "sector":                 None,
            "fired_date":             fired_date_str,
            "source":                 _SOURCE,
            "metric_name":            "single_name_pct",
            "metric_before":          _safe_float(act.get("weight")),
            "metric_predicted_after": _safe_float(SINGLE_NAME_CEILING),
            "rec_dollars":            _safe_float(act.get("trim_val")),
            "price_at_rec":           None,
            "candidates":             None,
            "corr_coverage_n":        None,
        })
    return rows


# ── beta_trim ────────────────────────────────────────────────────────────────

def _bought_today_tickers(trades_df, today: date) -> "set[str]":
    """Same-day BUY tickers, ET-dated — reproduces risk_advisor.py's
    `_bought_today` construction verbatim (not imported, per this module's
    own no-risk_advisor-import discipline)."""
    out: "set[str]" = set()
    if trades_df is None or getattr(trades_df, "empty", True):
        return out
    for _, tr in trades_df.iterrows():
        if str(tr.get("action", "") or "").strip().upper() != "BUY":
            continue
        ta = tr.get("traded_at")
        if ta is None:
            continue
        # format="ISO8601" — trades.traded_at genuinely mixes microsecond vs
        # second precision; utc=True ALONE does not fix that (silent NaT on
        # the non-matching rows). See feedback_pandas_mixed_tz_parsing.
        ta_ts = pd.to_datetime(ta, utc=True, format="ISO8601", errors="coerce")
        if pd.isna(ta_ts):
            continue
        ta_date = ta_ts.tz_convert("America/New_York").date()
        if ta_date == today:
            out.add(str(tr.get("ticker", "") or "").strip().upper())
    return out


def _beta_tr_map(port_df, held_data: dict) -> "dict[str, dict]":
    """Per-ticker {beta, weight, market_value} — reproduces risk_advisor.py's
    `tr_map` construction (beta/weight/market-value legs only; this module
    doesn't need the other risk_advisor fields)."""
    m: "dict[str, dict]" = {}
    if port_df is None or getattr(port_df, "empty", True):
        return m
    held_data = held_data or {}
    for _, row in port_df.iterrows():
        t = row.get("Ticker")
        if not t:
            continue
        bundle = held_data.get(t)
        rm = bundle.get("risk_metrics") if isinstance(bundle, dict) else None
        rm = rm if isinstance(rm, dict) else {}
        m[t] = {
            "beta":         _safe_float(rm.get("beta")),
            "weight":       _safe_float(row.get("Weight (%)")) or 0.0,
            "market_value": _safe_float(row.get("Market Value")) or 0.0,
        }
    return m


def _build_beta_trim_row(
    port_df, port_risk, held_data, trades_df, fired_date: date,
) -> "dict | None":
    """Reproduces risk_advisor.py's beta-card top-contributor trim pick
    (contribution-ranked top 3, same-day-BUY exclusion, 50%-of-top-contributor
    trim, `expected_beta_after_trim` for the predicted new beta) — WITHOUT
    importing risk_advisor.py.

    Only fires when the live card itself would fire (beta > PORTFOLIO_
    BETA_ELEVATED, i.e. `beta_priority` HIGH/MEDIUM) and at least one
    contributor survives the same-day-BUY exclusion. Returns None otherwise —
    a day with no qualifying beta trim writes no beta_trim row, not a
    fabricated one.
    """
    if not port_risk or port_df is None or getattr(port_df, "empty", True):
        return None
    beta = _safe_float(port_risk.get("beta"))
    if beta is None or beta <= PORTFOLIO_BETA_ELEVATED:
        return None

    tr_map = _beta_tr_map(port_df, held_data or {})
    contribs = []
    for t, tr in tr_map.items():
        b, w = tr["beta"], tr["weight"]
        if b is not None and b > 0 and w > 0:
            contribs.append({
                "ticker": t, "beta": b, "weight": w,
                "market_value": tr["market_value"], "_contrib": b * w / 100,
            })
    if not contribs:
        return None
    contribs.sort(key=lambda x: -x["_contrib"])
    top_beta = contribs[:3]

    bought_today = _bought_today_tickers(trades_df, fired_date)
    trim_row = None
    for c in top_beta:
        if c["ticker"].upper() in bought_today:
            continue
        trim_row = c
        break
    if trim_row is None:
        return None

    tw = trim_row["weight"] / 100
    tb = trim_row["beta"]
    tf = 0.50
    if tw * tf > 0.999:
        new_beta = beta
    else:
        new_beta = expected_beta_after_trim(
            current_beta=beta, book_fraction_sold=tw * tf, position_beta=tb,
        )
        if new_beta is None:
            new_beta = beta
    new_beta = round(max(float(new_beta), 0.3), 2)
    trim_dollar = round(trim_row["market_value"] * tf)

    return {
        "rec_type":               "beta_trim",
        "ticker":                 trim_row["ticker"],
        "sector":                 None,
        "fired_date":             _fired_date_str(fired_date),
        "source":                 _SOURCE,
        "metric_name":            "portfolio_beta",
        "metric_before":          beta,
        "metric_predicted_after": new_beta,
        "rec_dollars":            _safe_float(trim_dollar),
        "price_at_rec":           None,
        "candidates":             None,
        "corr_coverage_n":        None,
    }


# ── diversify_add ────────────────────────────────────────────────────────────

def _build_diversify_add_rows(
    port_df,
    sector_candidates: "dict | None",
    discovery_universe: "dict | None",
    portfolio_value: "float | None",
    avg_pairwise_corr: "float | None",
    corr_coverage_n: "int | None",
    fired_date_str: str,
    price_fn: "Callable[[str], float | None] | None" = None,
) -> "list[dict]":
    """One row per underweight diversifying sector's ADD rec that day.

    `ticker` = the FIRST candidate in that sector's surfaced pool (roster
    names first, by `diversifying_candidate_pool`'s own ordering). `metric_
    before` = the book's current avg_pairwise_corr (from the SAME EOD run's
    `portfolio_risk_snapshots` row — not recomputed); `metric_predicted_after`
    is always None — no "expected correlation after add" formula exists yet
    (confirmed against HEAD; `portfolio.expected_beta_after_add` covers beta
    only). `price_at_rec` is resolved via the caller-injected `price_fn` (a
    non-held ticker has no price in `port_df`/`held_data` — this module makes
    no fetch of its own).
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    try:
        recs = diversification_recommendations(
            port_df, pd.DataFrame(), {},
            sector_candidates=sector_candidates or {},
            discovery_universe=discovery_universe or {},
            portfolio_value=portfolio_value if portfolio_value else 50_000.0,
        )
    except Exception:
        return []

    rows: "list[dict]" = []
    for rec in recs or []:
        if rec.get("type") != "ADD":
            continue
        _raw_candidates = rec.get("candidates")
        candidates = [
            str(c).strip().upper()
            for c in (_raw_candidates if isinstance(_raw_candidates, list) else [])
            if c
        ]
        if not candidates:
            continue
        ticker = candidates[0]

        price_at_rec = None
        if price_fn is not None:
            try:
                price_at_rec = _safe_float(price_fn(ticker))
            except Exception:
                price_at_rec = None

        rows.append({
            "rec_type":               "diversify_add",
            "ticker":                 ticker,
            "sector":                 rec.get("sector"),
            "fired_date":             fired_date_str,
            "source":                 _SOURCE,
            "metric_name":            "avg_pairwise_corr",
            "metric_before":          _safe_float(avg_pairwise_corr),
            "metric_predicted_after": None,
            "rec_dollars":            None,
            "price_at_rec":           price_at_rec,
            "candidates":             candidates,
            "corr_coverage_n":        corr_coverage_n,
        })
    return rows


# ── orchestrator ─────────────────────────────────────────────────────────────

def build_rec_event_rows(
    fired_date: date,
    port_df,
    port_risk,
    held_data: "dict | None",
    trades_df,
    risk_snapshot: "dict | None",
    sector_candidates: "dict | None",
    discovery_universe: "dict | None",
    portfolio_value: "float | None",
    *,
    price_fn: "Callable[[str], float | None] | None" = None,
) -> "list[dict]":
    """Build every rec_events row for one EOD cron run.

    `risk_snapshot` is the SAME run's already-computed `portfolio_risk_
    snapshots`-shaped row (risk_metric_history.build_portfolio_risk_snapshot's
    return value) — reused for the diversify_add rows' `avg_pairwise_corr`/
    `corr_coverage_n`, never recomputed. Each of the three generators is
    independently try/excepted so one's failure can never blank the others
    (mirrors build_portfolio_risk_snapshot's own per-metric isolation).
    Returns [] (never None) when nothing qualifies today — an empty capture
    day is a real, complete answer, not a failure.
    """
    fired_date_str = _fired_date_str(fired_date)
    risk_snapshot = risk_snapshot or {}
    rows: "list[dict]" = []

    try:
        rows.extend(_build_rebal_trim_rows(port_df, fired_date_str))
    except Exception:
        pass

    try:
        beta_row = _build_beta_trim_row(port_df, port_risk, held_data, trades_df, fired_date)
        if beta_row is not None:
            rows.append(beta_row)
    except Exception:
        pass

    try:
        rows.extend(_build_diversify_add_rows(
            port_df, sector_candidates, discovery_universe, portfolio_value,
            risk_snapshot.get("avg_pairwise_corr"), risk_snapshot.get("corr_coverage_n"),
            fired_date_str, price_fn=price_fn,
        ))
    except Exception:
        pass

    return rows
