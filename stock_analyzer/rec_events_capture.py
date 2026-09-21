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

Five independent generators, one row (or list of rows) per rec_type, EACH
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
  - `single_name_concentration` (2026-09-21 app-review Part 2 #1) —
                     reproduces (does NOT import) `risk_advisor.py`'s
                     conviction-INDEPENDENT single-name-overweight branch
                     (`risk_advisor.py:883-931`): a ticker over
                     `SINGLE_NAME_CEILING` with score >= `WEAK_CONVICTION_
                     SCORE`. DEDUPED against `rebal_trim`'s same-run output
                     (see `build_rec_event_rows`) — a ticker that is BOTH
                     oversized+profitable (rebal_trim's own trigger) AND
                     oversized+strong-conviction (this trigger) would
                     otherwise get two rows crediting one real SELL, the
                     exact `rebal_trim`/`beta_trim` conflation class §11
                     already ratified against. This type measures only its
                     genuine non-overlapping gap: oversized names rebal_trim
                     structurally misses (not yet profitable). At most one
                     row per ticker per day; a day can have several.
  - `sector_concentration` (2026-09-21 app-review Part 2 #1) — reproduces
                     (does NOT import) `risk_advisor.py`'s sector-
                     concentration branch (`risk_advisor.py:692-881`). At
                     most ONE row per day — like the live card, this names
                     only the single WORST (highest-weight) real sector,
                     never every sector over threshold. `ticker` = the
                     lowest-conviction trim candidate in that sector (same
                     ranking the live card uses); the FULL trim-candidate
                     ticker list rides in `candidates` so attribution can
                     credit a SELL of ANY of them, not just the first.

Both new types are EQUITY-basis only (no `gate_denom` scaling) — under
current policy (`risk_advisor.py`'s own `_acct_f` comment, 2026-07-09) the
live cards' basis-scaling factor is always 1.0, so `portfolio_value` is
derived directly from `port_df["Market Value"].sum()` here rather than
threading a second policy-basis parameter through `cron_runner.py` for a
value that never actually changes today.

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
from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    SECTOR_ELEVATED,
    SINGLE_NAME_CEILING,
    UNCLASSIFIED_SECTOR,
    WEAK_CONVICTION_SCORE,
)
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


# ── single_name_concentration ───────────────────────────────────────────────

def _build_single_name_conc_rows(
    port_df, trades_df, fired_date: date, fired_date_str: str,
    rebal_trim_tickers: "set[str] | None" = None,
) -> "list[dict]":
    """One row per ticker over SINGLE_NAME_CEILING with score >=
    WEAK_CONVICTION_SCORE — reproduces (does NOT import) risk_advisor.py's
    conviction-independent single-name-overweight branch (risk_advisor.py:
    883-931), EQUITY-basis only (no gate_denom scaling — see module docstring).

    `rebal_trim_tickers`: tickers `_build_rebal_trim_rows` already emitted
    THIS SAME RUN — skipped here to avoid crediting one real SELL with two
    rows (this type and rebal_trim can both fire on an oversized+profitable+
    strong-conviction name). Same-day-BUY exclusion mirrors
    `_bought_today_tickers`.
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    rebal_trim_tickers = rebal_trim_tickers or set()
    bought_today = _bought_today_tickers(trades_df, fired_date)
    try:
        pv = float(port_df["Market Value"].sum())
    except Exception:
        pv = 0.0
    if not pv or pv <= 0:
        return []

    rows: "list[dict]" = []
    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "") or "").strip().upper()
        if not ticker or ticker in rebal_trim_tickers or ticker in bought_today:
            continue
        w = _safe_float(row.get("Weight (%)")) or 0.0
        # Mirrors risk_advisor.py's own coercion here: a missing score
        # defaults to 0.0, which only ever EXCLUDES a name from firing
        # (never fabricates a false "high conviction" reading that would
        # otherwise reach the user).
        score = _safe_float(row.get("Score")) or 0.0
        if w < SINGLE_NAME_CEILING or score < WEAK_CONVICTION_SCORE:
            continue
        excess_pp = w - SINGLE_NAME_CEILING
        rows.append({
            "rec_type":               "single_name_concentration",
            "ticker":                 ticker,
            "sector":                 None,
            "fired_date":             fired_date_str,
            "source":                 _SOURCE,
            "metric_name":            "single_name_pct",
            "metric_before":          _safe_float(w),
            "metric_predicted_after": _safe_float(SINGLE_NAME_CEILING),
            "rec_dollars":            _safe_float(round(excess_pp / 100.0 * pv)),
            "price_at_rec":           None,
            "candidates":             None,
            "corr_coverage_n":        None,
        })
    return rows


# ── sector_concentration ─────────────────────────────────────────────────────

def _build_sector_conc_rows(port_df, trades_df, fired_date: date, fired_date_str: str) -> "list[dict]":
    """At most ONE row per day — reproduces (does NOT import) risk_advisor.py's
    sector-concentration branch (risk_advisor.py:692-881), EQUITY-basis only.
    Like the live card, this names only the single WORST (highest-weight)
    real sector (UNCLASSIFIED_SECTOR excluded, same as the live card — a
    concentration "breach" on the unclassified catch-all is a data-hygiene
    artifact, not a real correlated-sector risk), never every sector over
    threshold. `ticker` = the lowest-conviction trim candidate (score_raw
    ascending, same-day-BUY excluded); the FULL trim-candidate ticker list
    rides in `candidates` so attribution can credit a SELL of ANY of them.
    Fires at SECTOR_ELEVATED (the live card's own MEDIUM-or-HIGH threshold —
    matches when the card is shown at all, not just its HIGH escalation).
    No row when there are zero score-eligible, not-same-day-bought
    candidates in the worst sector (nothing to attribute).
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    bought_today = _bought_today_tickers(trades_df, fired_date)

    sector_weights: "dict[str, float]" = {}
    sector_holdings: "dict[str, list[dict]]" = {}
    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "") or "").strip().upper()
        if not ticker:
            continue
        sec = str(row.get("Sector", "") or "").strip() or UNCLASSIFIED_SECTOR
        w = _safe_float(row.get("Weight (%)")) or 0.0
        sector_weights[sec] = sector_weights.get(sec, 0.0) + w
        sector_holdings.setdefault(sec, []).append({
            "ticker":    ticker,
            "weight":    w,
            "score_raw": _safe_float(row.get("Score")),
        })

    real_sector_weights = {s: w for s, w in sector_weights.items() if s != UNCLASSIFIED_SECTOR}
    if not real_sector_weights:
        return []
    top_sec, top_wt = max(real_sector_weights.items(), key=lambda x: x[1])
    if top_wt < SECTOR_ELEVATED:
        return []

    try:
        pv = float(port_df["Market Value"].sum())
    except Exception:
        pv = 0.0
    if not pv or pv <= 0:
        return []

    trim_eligible = sorted(
        (h for h in sector_holdings.get(top_sec, []) if h["score_raw"] is not None),
        key=lambda h: h["score_raw"],
    )
    candidates = [h["ticker"] for h in trim_eligible if h["ticker"] not in bought_today]
    if not candidates:
        return []

    excess_pp = top_wt - SECTOR_ELEVATED
    return [{
        "rec_type":               "sector_concentration",
        "ticker":                 candidates[0],
        "sector":                 top_sec,
        "fired_date":             fired_date_str,
        "source":                 _SOURCE,
        "metric_name":            "top_sector_pct",
        "metric_before":          _safe_float(top_wt),
        "metric_predicted_after": _safe_float(SECTOR_ELEVATED),
        "rec_dollars":            _safe_float(round(excess_pp / 100.0 * pv)),
        "price_at_rec":           None,
        "candidates":             candidates,
        "corr_coverage_n":        None,
    }]


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
    `corr_coverage_n`, never recomputed. Each of the five generators is
    independently try/excepted so one's failure can never blank the others
    (mirrors build_portfolio_risk_snapshot's own per-metric isolation).
    Returns [] (never None) when nothing qualifies today — an empty capture
    day is a real, complete answer, not a failure.

    `single_name_concentration` runs AFTER `rebal_trim` specifically so its
    dedup set (the tickers rebal_trim already emitted this run) is available
    — see `_build_single_name_conc_rows`'s own docstring.
    """
    fired_date_str = _fired_date_str(fired_date)
    risk_snapshot = risk_snapshot or {}
    rows: "list[dict]" = []
    rebal_trim_tickers: "set[str]" = set()

    try:
        rebal_rows = _build_rebal_trim_rows(port_df, fired_date_str)
        rebal_trim_tickers = {r["ticker"] for r in rebal_rows}
        rows.extend(rebal_rows)
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

    try:
        rows.extend(_build_single_name_conc_rows(
            port_df, trades_df, fired_date, fired_date_str,
            rebal_trim_tickers=rebal_trim_tickers,
        ))
    except Exception:
        pass

    try:
        rows.extend(_build_sector_conc_rows(port_df, trades_df, fired_date, fired_date_str))
    except Exception:
        pass

    return rows
