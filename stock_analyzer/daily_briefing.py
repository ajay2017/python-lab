"""
Daily Briefing module.

Synthesizes all available intelligence into three prioritized action buckets:
  - Act Today      : stop triggers, sell signals, critical news, today's macro, REDUCE flags
  - Buy Candidates : scanner picks + add-to-winner, each with a multi-signal confidence verdict
  - Review Before Close : approaching stops, near-term earnings, weak large positions,
                          warning news, upcoming macro catalysts

Confidence verdict system (Buy Candidates)
------------------------------------------
Each candidate is cross-referenced across every available signal layer:
  Layer 1 — Technical setup   (scanner: RSI, trend, momentum)
  Layer 2 — Composite signal  (port_df Signal/Score — held positions only)
  Layer 3 — News sentiment    (news_items avg compound for ticker)
  Layer 4 — Earnings risk     (held_data earnings date)
  Layer 5 — Analyst revisions (held_data revisions.net — held only)

Verdict tiers:
  Confirmed  — all available layers agree (green)
  Mixed      — 1 soft conflict (amber)
  Conflicted — hard conflict: composite ≠ technical OR strong negative news (red)
  Caution    — earnings within 7 days regardless of other signals (amber)
"""

from datetime import date, datetime as _dt, timedelta

import pandas as pd

from stock_analyzer import trim_sizing as _trim_sizing
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
    COMPOSITE_HIGH_CONVICTION,
    COMPOSITE_BUY_FLAT_DAY,
    SINGLE_NAME_CEILING,
    NET_CAPITAL_POSITION_CAP_PCT,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    UNCLASSIFIED_SECTOR,
    MACRO_IMMINENT_DAYS,
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_CRITICAL_DAYS,
    EARNINGS_MANAGEABLE_DAYS,
    RISK_PCT_PER_TRADE,
    ADD_WINNER_MIN_GAP_PCT,
    ADD_WINNER_COOLDOWN_DAYS,
    APPROACHING_STOP_GAP_PCT,
    LARGE_POSITION_WEIGHT_PCT,
    WEAK_CONVICTION_SCORE,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_SENTIMENT_NEGATIVE,
    NEWS_SENTIMENT_POSITIVE,
    NEWS_CRITICAL_MIN_HEADLINES,
    STOP_PROFIT_LOCK_PNL_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    STOP_TIGHTEN_ATR_MULT,
    STOP_TIGHTEN_MIN_GAIN_PCT,
    EARNINGS_OVERWEIGHT_TRIM_PCT,
    EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT,
    EARNINGS_OVERWEIGHT_TOLERANCE_PP,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    WEAK_LARGE_TRIM_TO_PCT,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
    MACRO_BROAD_EXPOSURE_PCT,
    MOVER_MAX_PICKS,
    GROW_MAX_PICKS_BULL,
    GROW_MAX_PICKS_DEFAULT,
    GROW_CANDIDATE_OVERFETCH,
    ATR_STOP_MULT,
    DETERIORATION_TRIM_SUGGESTED_PCT,
    ETF_MAX_PICKS,
    CORR_HIGH_PAIRS_THRESHOLD,
    CORR_DANGER_PAIRS_THRESHOLD,
)
from stock_analyzer.risk import position_sizing, sizing_unavailable_reason
from stock_analyzer.targets import entry_zone
from stock_analyzer.signal_reconciliation import (
    reconcile_signals,
    lookup_composite,
)
from stock_analyzer.position_lifecycle import classify_position_state
from stock_analyzer.sector_fit import gate_sector
from stock_analyzer import exit_advisor
from stock_analyzer import decision_bucket
from stock_analyzer.predictive_analytics import divergence_at_entry
from stock_analyzer.personalized_discovery import score_candidate_match
from stock_analyzer import asset_type
from stock_analyzer.pillar_availability import unmeasured_pillars, verdict_basis
from stock_analyzer import etf_candidates as etf_candidates_mod
from stock_analyzer import pair_add_gate
from stock_analyzer import entry_eligibility
from stock_analyzer.entry_eligibility import data_trust_hold, macro_block_map, macro_hold
from stock_analyzer.analyst_intel import trustworthy_composite
from stock_analyzer import held_news_check
from stock_analyzer import g32_shadow


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _ledger_composite(composites, ticker):
    """Composite to record on a gate-ledger row, or None when the bundle's
    `total` would not have been trusted for a buy decision (stale, stale
    fundamentals, or an unmeasured bq/val/technical pillar). Reuses the one
    shared `analyst_intel.trustworthy_composite` predicate. A None is later counted
    by the retrospective ledger as a null-composite exclusion, never graded.

    The ETF check is the one leg the Grow Today loop has (its unconditional ETF
    `continue`) that the shared predicate lacks: an ETF's equity `total` is never
    a stock-buy composite, even if its fundamentals flags happen to read True."""
    bundle = (composites or {}).get(ticker)
    if isinstance(bundle, dict) and bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
        return None
    return trustworthy_composite(bundle)


# Provenance marker for the SIZING FORMULA, persisted alongside every suggested
# size (recommendations.rec_sizing_version) so a future formula change can never
# be silently compared against sizes produced under the old one.
#   1 = the retired _suggest_size era (hardcoded trend-bucket stops, NO
#       concentration cap). Never actually persisted — nothing captured sizes
#       before F-249 — so a NULL in the DB means "pre-capture", not "version 1".
#   2 = F-249: ceiling-capped risk.position_sizing() off the ATR stop.
#   3 = F-255: adds the SEPARATE net-capital cap (NET_CAPITAL_POSITION_CAP_PCT)
#       on top of the existing gross-book ceiling — inert (identical to v2's
#       output) whenever the caller doesn't pass net_capital.
#   4 = adds the daily new-position budget
#       (daily_budget.BUDGET_SIZING_VERSION): a stored rec_shares now also
#       depends on the day's other candidates and today's logged buys.
#   5 = S-D: SINGLE_NAME_CEILING / NET_CAPITAL_POSITION_CAP_PCT bind on the whole position after an add (existing holding value counted); add_winner rec_shares change meaning.
# Bump this whenever the formula changes; do NOT reuse a number.
SIZING_FORMULA_VERSION = 5


def _position_size_for_render(
    portfolio_value: float,
    price: float,
    stop: float,
    entry_lo,
    entry_hi,
    net_capital: float | None = None,
    existing_value: float = 0.0,
) -> dict:
    """Wrap position_sizing() into renderer-compatible keys.

    Calls the ceiling-capped position_sizing() from risk.py (the same function
    used by Analysis, Watchlist Advisor, etc.) and maps its output to the dict
    shape the Grow Today renderers (app.py) and cron emails (notify.py) expect.

    Five states, deliberately distinct:
      - ``{}``                  — a required input is MISSING (price <= 0 or
                                  stop <= 0). Nothing to explain; the caller
                                  simply has no data yet.
      - ``portfolio_unknown``   — price and stop are valid but portfolio_value is
                                  non-positive. No honest size exists. The account
                                  may be perfectly adequate; the app just does not
                                  know it (🏠 Home not yet visited this session).
      - ``ceiling_infeasible``  — inputs are fine but one share alone breaches
                                  the single-name cap. Account-size constraint.
      - ``capital_infeasible``  — inputs are fine but one share alone breaches
                                  the SEPARATE net-capital cap (F-255). Only
                                  reachable when `net_capital` is supplied;
                                  every existing caller that omits it can never
                                  hit this branch. An account-size constraint,
                                  just on net capital rather than gross book.
      - ``stop_infeasible``     — inputs are fine but price is at/below the stop.
    S-D (2026-10-19) adds two more, only reachable when `existing_value` is
    not 0 (an add to a held name; the caps bind on the WHOLE position):
      - ``room_infeasible``     — the existing holding already leaves less than
                                  one whole share of room under the ceiling
                                  (``room_binding`` "ceiling") or STRICTLY below
                                  the net-capital cap ("capital"). Carries the
                                  existing / one-more-share percentages.
      - ``holding_unknown``     — `existing_value` is not a finite number >= 0
                                  (never coerced to 0 — that would size a held
                                  name as a fresh one).
    On a sized add, room_capped / room_binding / post_portfolio_pct /
    post_capital_pct / existing_value are copied through. With
    existing_value == 0 the output is byte-identical to before S-D.
    These markers carry NO ``shares`` key, so a renderer gating its size text
    on ``shares`` can never print "0 shares"; they exist so a card that already
    says BUY never shows a blank size with no reason.

    Carries ceiling_capped and uncapped_shares so the render layer can show a
    disclosure when the single-name cap binds. When `net_capital` is supplied,
    also carries capital_capped/capital_pct for the same disclosure on the
    net-capital cap. `net_capital=None` (the default) makes the whole net-
    capital cap inert — output is byte-identical to before F-255.
    """
    if not (price > 0 and stop > 0):
        return {}
    # Why no size, if there is no size. Asks the shared detector rather than
    # re-deriving either predicate here — a second copy drifts the moment
    # position_sizing's own guards change.
    _reason = sizing_unavailable_reason(
        portfolio_value, price, stop, max_position_pct=SINGLE_NAME_CEILING,
        net_capital=net_capital, max_capital_pct=NET_CAPITAL_POSITION_CAP_PCT,
        existing_value=existing_value,
    )
    if _reason == "holding":
        return {
            "holding_unknown": True,
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    if _reason in ("room_ceiling", "room_capital"):
        _ex = float(existing_value)
        _cap_basis = _reason == "room_capital"
        _den = net_capital if _cap_basis else portfolio_value
        return {
            "room_infeasible": True,
            "room_binding": "capital" if _cap_basis else "ceiling",
            "existing_value": round(_ex, 2),
            "existing_gross_pct": round(_ex / portfolio_value * 100, 1),
            "existing_capital_pct": (
                round(_ex / net_capital * 100, 1) if net_capital and net_capital > 0 else None
            ),
            "one_more_pct": round((_ex + price) / _den * 100, 1),
            "cap_pct": NET_CAPITAL_POSITION_CAP_PCT if _cap_basis else SINGLE_NAME_CEILING,
            "portfolio_value": round(portfolio_value, 2),
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    if _reason == "ceiling":
        # One share alone breaches the single-name cap, so no honest size
        # exists. Surface an explicit marker rather than an empty dict so the
        # render layer can say WHY instead of silently dropping the line.
        # Carries no "shares" key on purpose — every renderer gates its size
        # text on shares, so a marker can never print "0 shares".
        return {
            "ceiling_infeasible": True,
            "one_share_pct": round(price / portfolio_value * 100, 1),
            "portfolio_value": round(portfolio_value, 2),
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    if _reason == "capital":
        # One share alone breaches the SEPARATE net-capital cap (or net_capital
        # is already <= 0 / margin-called). Only reachable when net_capital
        # was supplied and positive-or-called — every existing caller that
        # passes net_capital=None never routes here.
        return {
            "capital_infeasible": True,
            "one_share_capital_pct": (
                round(price / net_capital * 100, 1) if net_capital and net_capital > 0 else None
            ),
            "net_capital": round(net_capital, 2) if net_capital is not None else None,
            "portfolio_value": round(portfolio_value, 2),
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    if _reason == "portfolio":
        # Portfolio value is unknown or non-positive — no honest size exists.
        # Carries NO shares key (same convention as the other infeasible markers:
        # every renderer gates its size text on `shares`, so a marker can never
        # print "0 shares"). Also carries NO one_share_pct or portfolio_value —
        # both would require dividing by or reporting the very number we don't have.
        return {
            "portfolio_unknown": True,
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    if _reason == "stop":
        # Today's price is at or below the ATR stop for this name. Same marker
        # pattern: no shares key, but an explicit reason so the card explains
        # itself instead of rendering a buy with a blank size (the silent-filter
        # case the Opus review flagged as blocking).
        return {
            "stop_infeasible": True,
            "stop_at": round(stop, 2),
            "portfolio_value": round(portfolio_value, 2),
            "sizing_version": SIZING_FORMULA_VERSION,
        }
    raw = position_sizing(
        portfolio_value, RISK_PCT_PER_TRADE, price, stop,
        max_position_pct=SINGLE_NAME_CEILING,
        net_capital=net_capital, max_capital_pct=NET_CAPITAL_POSITION_CAP_PCT,
        existing_value=existing_value,
    )
    if raw is None:
        # Unreachable today: position_sizing delegates the identical predicate
        # with identical arguments to sizing_unavailable_reason above, so both
        # of its None causes were already branched on. Kept as defence-in-depth
        # against the two drifting apart — not a live route.
        return {}
    out = {
        "shares":          raw["shares"],
        "total_cost":      raw["total_cost"],
        "stop":            round(stop, 2),
        "stop_pct":        round((price - stop) / price * 100, 1),
        "port_pct":        raw["portfolio_pct"],
        "risk_budget":     raw["risk_budget"],
        "entry_lo":        entry_lo,
        "entry_hi":        entry_hi,
        "ceiling_capped":  raw.get("ceiling_capped", False),
        "uncapped_shares": raw.get("uncapped_shares", raw["shares"]),
        # Provenance for persistence (Phase 2). Travels WITH the size so the
        # stored basis can never disagree with the basis it was computed from.
        "portfolio_value": round(portfolio_value, 2),
        "sizing_version":  SIZING_FORMULA_VERSION,
    }
    if net_capital is not None:
        out["capital_capped"] = raw.get("capital_capped", False)
        out["capital_pct"]    = raw.get("capital_pct")
    if "room_capped" in raw:
        # Only present when existing_value > 0 (S-D): leaves the fresh-pick
        # output byte-identical.
        for _k in ("existing_value", "post_portfolio_pct", "post_capital_pct",
                   "room_capped", "room_binding"):
            if _k in raw:
                out[_k] = raw[_k]
        # Carried so a later budget trim can recompute post_capital_pct.
        out["net_capital"] = round(net_capital, 2) if net_capital is not None else None
    return out


def _gate_wt(row) -> float:
    """Weight a concentration GATE compares against the 15%/35% ceilings.

    Reads "Gate Weight (%)" and falls back to "Weight (%)" when the column is
    absent (non-app callers like the headless cron). As of the 2026-07-09
    equity-basis policy (reqs G-19), app.py sets "Gate Weight (%)" == equity
    "Weight (%)", so both are the same equity weight; the column is kept as the
    seam so callers are unchanged if the basis is ever revisited.
    """
    _gw = row.get("Gate Weight (%)")
    # NaN-safe (`_gw == _gw` is False for NaN): a NaN gate weight falls back to
    # the equity column, matching _f's own NaN handling — strict parity with the
    # equity-basis path when Market Value is missing.
    return _f(_gw if (_gw is not None and _gw == _gw) else row.get("Weight (%)"), 0)


def _gate_wt_col(port_df) -> str:
    """Column name the sector gate sums — gate-basis if present, else equity."""
    return "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"


def _has_positions(port_df) -> bool:
    """True only when port_df is a usable frame with a Ticker column.

    `port_df is not None` is NOT sufficient: build_portfolio_df returns a
    0x0 DataFrame with NO columns for a zero-holdings book, so a bare
    port_df["Ticker"] raises KeyError rather than yielding an empty result.

    The None branch is DEFENSIVE ONLY — build_portfolio_df never returns None,
    and None must never be adopted as an "unreadable book" sentinel here: this
    would collapse it to "nothing held", which is the offline-sentinel fail-open
    CLAUDE.md warns about. Unreadable is handled upstream by outage_gate.decide.
    """
    return port_df is not None and not port_df.empty and "Ticker" in port_df.columns


def _held_tickers(port_df) -> set:
    """Held-ticker set, empty for a zero-holdings book. See _has_positions."""
    return set(port_df["Ticker"].tolist()) if _has_positions(port_df) else set()


def _port_rows(port_df, ticker: str):
    """Rows for one ticker, or an empty frame when there is no book to match
    against. Callers all branch on .empty, so an empty frame is the correct
    "not held" answer — never a fabricated row.
    """
    if not _has_positions(port_df):
        return pd.DataFrame()
    return port_df[port_df["Ticker"] == ticker]


def _days_until(date_str: str, today: date) -> int | None:
    try:
        d = date.fromisoformat(str(date_str)[:10])
        return (d - today).days
    except Exception:
        return None


# Risk-Advisor rec types that are slow-moving statistical metrics (6-month
# Sharpe / beta / volatility / drawdown / tail risk) rather than time-boxed
# decisions. These route to the Brief's "Portfolio Tune-up" awareness lane
# instead of Act Today — §2B: Act Today = decisions to make TODAY; a Sharpe drag
# is a standing portfolio-quality issue, not a same-day call. Concentration
# breaches are NOT here — they stay in Act (structural, user-flagged act-worthy).
_TUNEUP_RISK_TYPES = frozenset({"beta", "sharpe", "volatility", "drawdown", "tail_risk", "single_name_concentration"})

# Of the Tune-up types, only these two name a concrete TRIM target (same scope
# as `_trim_targets`) — so only these can double-surface against an Act Today
# risk-off TRIM on the same high-beta name (both rank by β·weight). vol/drawdown/
# tail/concentration recommend ADDING diversifiers, a different action, so they
# never restate a trim and are left untouched (2026-08-04 audit).
_TUNEUP_TRIM_DRIVER_TYPES = frozenset({"beta", "sharpe"})
# Of the trim-drivers, only the beta card's recommendation prose is anchored to
# a single headline name (`trim_ticker == root_tickers[0]`); sharpe's prose is a
# generic per-position rule that names no anchor. So a beta card whose PRIMARY
# name is already acted must be dropped whole (its "Sell 50% of X" would go
# stale), whereas sharpe just chip-filters and survives on any remaining drag.
_TUNEUP_PRIMARY_ANCHORED_TYPES = frozenset({"beta"})


def _portfolio_tuneup(risk_recs: list | None, acted_tickers: set | None = None) -> list[dict]:
    """Slow-moving risk-metric recommendations surfaced as standing 'Portfolio
    Tune-up' items (awareness), not Act Today decisions. HIGH or MEDIUM only —
    OK/LOW aren't improvements to make. Shape is render-ready for app.py.

    acted_tickers (optional): every ticker already carrying an Act Today / Review
    card this render (same broad basis as the risk-off exclude set — matches the
    sibling block's 2026-07-29 H6 precedent). For the trim-driver types
    (beta/sharpe) this suppresses the redundant restatement of a trim already in
    Act Today (2026-08-04 audit — the beta Tune-up card and a risk-off TRIM both
    rank the same high-beta names, so a fragile+risk-off render otherwise shows
    "trim NVDA" in both lanes). A beta card whose primary trim_ticker is acted is
    dropped whole; a merely SECONDARY overlap is filtered from the chip list only,
    keeping the card's still-valid primary-anchored prose."""
    acted = {str(t).upper() for t in (acted_tickers or set())}
    out: list[dict] = []
    for rec in (risk_recs or []):
        if rec.get("type") not in _TUNEUP_RISK_TYPES:
            continue
        if rec.get("priority") not in ("HIGH", "MEDIUM"):
            continue
        rtype = rec.get("type")
        rt = rec.get("root_tickers", []) or []
        tickers = [r.get("ticker") for r in rt if r.get("ticker")]
        if acted and rtype in _TUNEUP_TRIM_DRIVER_TYPES and tickers:
            # Beta prose is built around root_tickers[0] — if THAT name is being
            # trimmed today, the whole card duplicates the Act Today call.
            if rtype in _TUNEUP_PRIMARY_ANCHORED_TYPES and str(tickers[0]).upper() in acted:
                continue
            tickers = [t for t in tickers if str(t).upper() not in acted]
            if not tickers:
                continue  # every named trim target already acted -> nothing to add
        out.append({
            "type":           rtype,
            "priority":       rec.get("priority"),
            "title":          rec.get("title", "Portfolio metric"),
            "problem":        rec.get("problem", ""),
            "recommendation": rec.get("recommendation", ""),
            "tickers":        tickers,
        })
    return out


def _trim_targets(risk_recs: list | None) -> dict[str, dict]:
    """
    Extract tickers Risk Advisor is recommending the user trim.

    Only beta and sharpe recs concretely identify trim targets (volatility,
    drawdown, and tail-risk recs instead recommend adding diversifiers).
    Returns {ticker: {"reason": rec_type, "title": rec_title, "priority": priority}}.
    """
    if not risk_recs:
        return {}
    targets: dict[str, dict] = {}
    for rec in risk_recs:
        pri = rec.get("priority")
        if pri not in ("HIGH", "MEDIUM"):
            continue
        rec_type = rec.get("type", "")
        if rec_type not in ("beta", "sharpe"):
            continue
        for rt in rec.get("root_tickers", []):
            t = rt.get("ticker")
            if not t or t in targets:
                continue
            targets[str(t).upper()] = {
                "reason":   rec_type,
                "title":    rec.get("title", ""),
                "priority": pri,
            }
    return targets


# ── Cross-reference engine ────────────────────────────────────────────────────

# The reconciled verdict's label for the negative-news skip, exactly as
# signal_reconciliation.reconcile_signals emits it (no structured flag exists
# there). G-30 (chunk 7b / N-B2) keys on it; pinned by a test against the real
# reconcile_signals output so a label edit there cannot silently drop the rows.
_NEGATIVE_NEWS_SKIP_LABEL = "❌ Skip — Negative News"


def _ticker_news_avg(news_items, ticker: str):
    """Mean compound of the Brief's headlines for `ticker`; None when none."""
    _tn = [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == ticker]
    return sum(n.get("compound", 0) for n in _tn) / len(_tn) if _tn else None


def _cross_reference(ticker: str, scanner_row: dict, port_df, news_items: list,
                     held_data: dict, today: date,
                     earnings_lookup: dict | None = None,
                     composites: dict | None = None,
                     is_mover: bool = False,
                     scanner_row_is_synthetic: bool = False) -> dict:
    """
    Cross-check a buy candidate across all available signal layers.

    earnings_lookup (optional): {ticker: earnings_date_str}. Sourced from BOTH
       held_data and pre-fetched composites, so the earnings risk check applies
       to non-held new picks too.
    composites (optional): {ticker: load_all() bundle} for pre-fetched scanner
       picks. When present, lets the composite signal participate in the verdict
       for non-held candidates too — without this, every new pick collapses to
       "Verify — Run Analysis First" even when the composite already exists in
       session_state (the TSLA-shows-as-buy-but-Stock-Analysis-says-sell case).

    Returns a dict with:
      verdict             — 'confirmed' | 'mixed' | 'conflicted' | 'caution' | 'unverified'
      verdict_label       — display string (the legacy tier badge)
      verdict_color       — hex colour
      verdict_one_liner   — explicit resolution sentence (NEW — surfaces should
                            render this prominently; the central reconciliation
                            engine populates it consistently across all callers)
      verdict_reconciled  — full dict from reconcile_signals() — has its own
                            verdict tier ('go'/'verify'/'caution'/'skip')
      agreed              — list of supporting signals
      conflicts           — list of conflicting signals
      layers_checked      — int
      is_held             — bool
      composite_available — bool (now True whenever ANY source has composite)
    """
    agreed:    list[str] = []
    conflicts: list[str] = []

    # ── Determine whether ticker is held (composite signal available) ─────────
    is_held = False
    if port_df is not None and not port_df.empty:
        is_held = not port_df[port_df["Ticker"] == ticker].empty

    # ── Layer 1: Momentum setup (real scanner rows only) ──────────────────────
    # scanner_row_is_synthetic=True means this is a fabricated dict from the
    # add-winner path (port_df["Signal"]/["Score"]), not an actual scanner read.
    # Omit Layer 1 entirely — absence of a momentum reading ≠ confirmation;
    # same precedent as Layer 3's "No news available" comment below.
    scan_sig   = str(scanner_row.get("Signal", ""))
    scan_score = _f(scanner_row.get("Score", 0))     # kept as-is: feeds reconcile_signals
    rsi        = _f(scanner_row.get("RSI"), None)     # None-preserving: RSI 0 ≠ RSI missing
    trend      = str(scanner_row.get("Trend", ""))
    if not scanner_row_is_synthetic:
        if scan_sig or scan_score:
            _l1 = (
                f"Momentum: {scan_sig} ({scan_score:.0f}/100)" if scan_sig
                else f"Momentum: {scan_score:.0f}/100"
            )
            if trend:
                _l1 += f" · {trend}"
            if rsi is not None:
                _l1 += f" · RSI {rsi:.0f}"
            agreed.append(_l1)
        # else: neither signal nor score — omit (data absence ≠ confirmation)

    # ── Layer 2: Composite signal — held OR pre-fetched scanner pick ─────────
    # Previously this only consulted port_df (held positions). That left every
    # non-held candidate falling through to "Verify First" even when composite
    # was pre-fetched into composites dict. Now we look at both sources.
    comp_sig, comp_scr = lookup_composite(ticker, port_df, composites)
    composite_conflict   = False
    composite_available  = comp_sig is not None or comp_scr is not None
    if composite_available:
        buy_words       = ("Strong Buy", "Buy")
        hold_sell_words = ("Hold", "Sell", "Avoid", "Weak")
        _sig_disp = comp_sig or "n/a"
        _scr_disp = f"{comp_scr:.0f}/100" if comp_scr is not None else "—"
        if comp_sig and any(w in comp_sig for w in buy_words):
            agreed.append(f"Composite signal: {_sig_disp} ({_scr_disp})")
        elif comp_sig and any(w in comp_sig for w in hold_sell_words):
            conflicts.append(
                f"Composite signal: {_sig_disp} ({_scr_disp}) — "
                "technicals say Buy but full multi-factor analysis is more cautious"
            )
            composite_conflict = True
        else:
            agreed.append(f"Composite signal: {_sig_disp} ({_scr_disp})")

    # ── Layer 3: News sentiment ───────────────────────────────────────────────
    ticker_news = [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == ticker]
    sentiment_conflict = False
    if ticker_news:
        avg_compound  = sum(n.get("compound", 0) for n in ticker_news) / len(ticker_news)
        best_headline = max(ticker_news, key=lambda n: abs(n.get("compound", 0)))
        if avg_compound <= NEWS_SENTIMENT_NEGATIVE:
            conflicts.append(
                f"News sentiment: Negative (avg {avg_compound:+.2f}) — "
                f"\"{(best_headline.get('title') or best_headline.get('headline') or '')[:60]}\""
            )
            sentiment_conflict = True
        elif avg_compound >= NEWS_SENTIMENT_POSITIVE:
            agreed.append(f"News sentiment: Positive (avg {avg_compound:+.2f})")
        else:
            agreed.append(f"News sentiment: Neutral (avg {avg_compound:+.2f})")
    # No news available — don't add to agreed (absence of data ≠ confirmation)

    # ── Layer 4: Earnings risk ────────────────────────────────────────────────
    # Earnings date is looked up from a UNION of held_data + earnings_lookup so
    # non-held new picks are also screened. Previously the check fired only on
    # held positions, so a brand-new scanner pick with earnings tomorrow could
    # be marked "Confirmed" with no caution.
    earnings_conflict = False
    earn_days = None
    earn_date = None
    if earnings_lookup and ticker in earnings_lookup:
        earn_date = earnings_lookup[ticker]
    elif held_data and ticker in held_data:
        earn_date = (held_data[ticker] or {}).get("earnings")
    if earn_date:
        earn_days = _days_until(earn_date, today)
        if earn_days is not None and 0 <= earn_days <= EARNINGS_IMMINENT_DAYS:
            label = "today" if earn_days == 0 else f"in {earn_days}d"
            conflicts.append(
                f"Earnings {label} ({earn_date}) — binary event risk; "
                "signals may not hold post-release"
            )
            earnings_conflict = True
        elif earn_days is not None and EARNINGS_IMMINENT_DAYS < earn_days <= EARNINGS_MANAGEABLE_DAYS:
            agreed.append(f"Earnings in {earn_days}d — manageable window")

    # ── Layer 5: Analyst revisions (held positions with held_data) ────────────
    if held_data and ticker in held_data:
        revs = ((held_data[ticker] or {}).get("revisions") or {})
        net  = revs.get("net")
        if net is not None:
            if net >= 2:
                agreed.append(f"Analyst revisions: +{net} net upgrades (90d)")
            elif net <= -2:
                conflicts.append(f"Analyst revisions: {net} net downgrades (90d)")
            else:
                agreed.append(f"Analyst revisions: flat ({net:+d} net)")

    # ── Verdict ───────────────────────────────────────────────────────────────
    layers = len(agreed) + len(conflicts)

    # Earnings within 7 days is a binary risk event. When combined with other signal
    # conflicts it must ESCALATE severity, not override to a lower "caution" level.
    if earnings_conflict and (composite_conflict or sentiment_conflict):
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Earnings + Signal Conflict"
        verdict_color = "#ef4444"
    elif composite_conflict and sentiment_conflict:
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Multiple Conflicts"
        verdict_color = "#ef4444"
    elif composite_conflict:
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Composite vs Technical"
        verdict_color = "#ef4444"
    elif earnings_conflict:
        verdict       = "caution"
        verdict_label = f"⚠️ Caution — Earnings Within {EARNINGS_IMMINENT_DAYS} Days"
        verdict_color = "#f59e0b"
    elif sentiment_conflict:
        verdict       = "mixed"
        verdict_label = "⚠️ Mixed — Negative News"
        verdict_color = "#f59e0b"
    elif conflicts:
        verdict       = "mixed"
        verdict_label = "⚠️ Mixed"
        verdict_color = "#f59e0b"
    elif not composite_available:
        # Composite signal isn't loaded for this ticker — can't issue
        # "Confirmed" regardless of held status. The "Verify" amber treatment
        # surfaces the data gap. Held vs non-held only changes the message
        # (the user knows whether they own it or not), not the verdict tier.
        verdict       = "unverified"
        verdict_label = (
            "🔍 Verify — Composite Signal Missing"
            if is_held else "🔍 Verify — Run Analysis First"
        )
        verdict_color = "#f59e0b"   # amber — action required, not a green light
    else:
        # Composite IS available, no conflicts above — full Confirmed regardless
        # of whether the user already holds the position. Previously this branch
        # only fired for is_held=True, leaving non-held picks at "Unverified"
        # even when the pre-fetched composite cleared the gate. That made every
        # new scanner pick look amber on the Brief and stored an "Unverified"
        # verdict in the recommendations log for things that were actually
        # fully confirmed.
        verdict       = "confirmed"
        # Compact form (Consistency #6) — this legacy field only ever renders as a
        # small nowrap badge (app.py Grow Today / scanner-picks pills), never as a
        # card headline; the verbose one-liner for that role is verdict_one_liner.
        # "Signals Agree" not bare "Confirmed" — echoes reconcile_signals()'s own
        # "Go — All Signals Agree" label (which this compact form is a fallback
        # for) and avoids colliding with thesis_cluster.py's unrelated
        # cluster-match "Confirmed" (2026-08-04 UX audit CA4).
        verdict_label = "✅ Signals Agree"
        verdict_color = "#22c55e"

    # ── Central reconciliation — populates the explicit one-liner the UI ─────
    # surfaces render alongside the tier badge. Calling reconcile_signals here
    # keeps the resolution copy consistent everywhere: same wording in Daily
    # Briefing, Grow Today, Market Scanner, and Watchlist.
    _news_compound = _ticker_news_avg(news_items, ticker)
    _reconciled = reconcile_signals(
        ticker=ticker,
        momentum_score=scan_score,
        momentum_signal=scan_sig,
        composite_score=comp_scr,
        composite_signal=comp_sig,
        is_held=is_held,
        is_mover=is_mover,
        earnings_days=earn_days,
        news_sentiment=_news_compound,
        momentum_available=not scanner_row_is_synthetic,
    )

    return {
        "verdict":             verdict,
        "verdict_label":       verdict_label,
        "verdict_color":       verdict_color,
        "verdict_one_liner":   _reconciled["one_liner"],
        "verdict_reconciled":  _reconciled,
        "agreed":              agreed,
        "conflicts":           conflicts,
        "layers_checked":      layers,
        "earn_days":           earn_days,
        "is_held":             is_held,
        "composite_available": composite_available,
    }


# ── Grow Today ───────────────────────────────────────────────────────────────

def _add_entry_ok(row) -> bool:
    """The ONE add-to-winner entry rule: a held "Strong Buy" whose composite
    clears COMPOSITE_BUY and whose gap to stop is at least ADD_WINNER_MIN_GAP_PCT.

    Shared by _grow_today's bull-day add loop AND its adds_paused disclosure
    (non-bull days), so the two can never disagree about who "would qualify".
    """
    return (
        "Strong Buy" in str(row.get("Signal", ""))
        and _f(row.get("Score"), 0) >= COMPOSITE_BUY
        and _f(row.get("Gap to Stop (%)"), 0) >= ADD_WINNER_MIN_GAP_PCT
    )


def _thesis(ticker: str, scanner_row: dict, is_sector_leader: bool) -> str:
    parts = []
    trend  = str(scanner_row.get("Trend", ""))
    mom_1m = _f(scanner_row.get("1M Momentum", 0))
    mom_3m = _f(scanner_row.get("3M Momentum", 0))
    rsi    = _f(scanner_row.get("RSI", 50))

    if "Strong Uptrend" in trend:
        parts.append("price above both 20d and 50d MAs — primary trend intact")
    elif "Uptrend" in trend:
        parts.append("price above 20d MA — trend building")

    if mom_1m > 10:
        parts.append(f"1M momentum +{mom_1m:.1f}% — strong recent acceleration")
    elif mom_1m > 5:
        parts.append(f"1M momentum +{mom_1m:.1f}%")

    if mom_3m > 20:
        parts.append(f"3M momentum +{mom_3m:.1f}% — sustained move")

    if 40 <= rsi <= 60:
        parts.append("RSI in ideal entry zone (not overbought)")
    elif rsi < 40:
        parts.append(f"RSI {rsi:.0f} — oversold, mean-reversion potential")
    elif rsi > 70:
        parts.append(f"RSI {rsi:.0f} — extended, wait for pullback")

    if is_sector_leader:
        parts.append("sector leading the market today — institutional tailwind")

    return (". ".join(parts).capitalize() + ".") if parts else "Momentum and trend aligned."



def _recently_added(ticker, held_data, cooldown: int = ADD_WINNER_COOLDOWN_DAYS) -> bool:
    """True when the user added shares to `ticker` within the cooldown window —
    used to suppress repeat add-to-winner nudges right after they acted on one
    (the PATH case). days_since_last_buy is the age of the newest still-held lot
    (attached in app.py). None (no trade journal) → False (calm, not blind)."""
    d = (held_data or {}).get(ticker) or (held_data or {}).get(str(ticker).upper()) or {}
    dslb = d.get("days_since_last_buy")
    return dslb is not None and dslb < cooldown


def _expired_macro_series(today) -> "list[dict] | None":
    """Thin wrapper around reference_shelf.expired_macro_series for use inside
    _grow_today.

    Return contract (critical — do not collapse):
      ``None``  — could not verify (import error, parse failure, any exception).
                  This is NOT "nothing expired".  A caller that treats None as []
                  silently turns "we don't know" into "we checked and it's fine."
      ``[]``    — verified, nothing expired.
      list      — one or more expired recurring series.

    G-07 never reads this value: the new-pick suppression in _grow_today uses
    macro_calendar directly and runs BEFORE this call.  A failure here can never
    remove a suppression; it can only mean the UI annotation is missing.
    """
    try:
        from stock_analyzer import reference_shelf as _rs
        return _rs.expired_macro_series(today)
    except Exception:
        return None


def _grow_today(port_df, scanner_results, news_items, held_data, today,
                portfolio_value: float, market_context: dict,
                act_today: list | None = None,
                review_list: list | None = None,
                composites: dict | None = None,
                risk_recs: list | None = None,
                earnings_lookup: dict | None = None,
                macro_events: list | None = None,
                movers: list | None = None,
                deterioration: list | None = None,
                winner_profile: dict | None = None,
                net_capital: float | None = None,
                sold_today: set | None = None,
                spy_df=None,
                etf_candidates: list | None = None,
                cluster_add_blocks: dict | None = None,
                pair_add_blocks: dict | None = None,
                corr_unchecked: list | None = None) -> dict:
    """
    Build growth-oriented action list calibrated to today's market tone.

    market_context keys: sp500_pct, nasdaq_pct, tone ('bull'|'bear'|'flat'),
                         leading_sectors [{"sector", "etf", "return_1w"}]
    act_today  : output of _act_today — tickers flagged here are excluded.
    review_list: output of _review_list — tickers flagged here (e.g. earnings-
                 overweight or weak-large-position TRIM_TO_TARGET) are ALSO
                 excluded. Without this, a review-origin trim never suppressed
                 a same-day add-to-winner pick on the same ticker (2026-07-30
                 coordination-gap fix; earnings-overweight-trim is review-origin,
                 not act_today-origin, so it silently slipped past the earlier
                 act_today-only check).
    composites : {ticker: load_all() result} for top scanner picks — used to
                 validate conviction using the full composite score so the label
                 reflects more than just momentum.
    risk_recs  : Risk Advisor recommendations — tickers flagged for trim (beta
                 or sharpe root_tickers) are suppressed from add-to-winner so
                 the briefing never tells the user to trim X and add to X.
    deterioration : output of deterioration_signals() — a held ticker carrying
                 an active WATCH tier is SUPPRESSED from add-to-winner (moved to
                 deterioration_blocked_adds with a visible reason), not annotated:
                 the app must not nudge to ADD to a name showing early
                 deterioration, even on a Strong Buy composite. WATCH is
                 entry-price-agnostic, which is why this is the principled add
                 gate. (Changed 2026-07-21; was annotate-only, which read as
                 "add more to a name I'm telling you is weakening" — the FSLR case.)
    winner_profile : output of personalized_discovery.build_winner_profile() — the
                 composite/momentum band + top sectors of the user's own REALIZED
                 winning entries. None when there isn't enough history yet (never
                 fabricated at low N). Attached to new_picks ONLY (not
                 add_positions — an add-to-winner is a call already made once, not
                 a fresh entry decision) as pick["personalized_match"]. Diagnostic
                 annotation only, same as "divergence" above — never gates,
                 re-scores, or re-ranks; the pick has already cleared every gate.
    net_capital : optional (F-255) — equity after margin debit, threaded straight
                 into every _position_size_for_render call so new-pick and
                 add-to-winner sizing both respect the SEPARATE net-capital cap.
                 None (the default, and every caller before F-255) leaves sizing
                 byte-identical to the pre-F-255 output.
    spy_df      : optional benchmark history, threaded into
                 exit_advisor.candidate_deterioration_flag() so the warn-only
                 pre-purchase deterioration check on new_picks can classify
                 TRIM-tier (which requires rel_strength < 0 vs the benchmark).
                 None (the default) is a fail-safe UNDER-warn, not a crash — RS
                 defaults to 0.0 inside assess_holding, so only WATCH/EXIT can
                 fire, never TRIM.
    etf_candidates : optional (ETF-support Phase 2b) — list of
                 {"ticker", "group", "bundle", ...} dicts resolved by
                 etf_candidates_mod.resolve_etf_candidates() with each
                 not-held candidate's bundle already attached by the caller
                 (app.py / headless_alert_engine.py), or a {"kind":
                 "held_group", "reason": ...} screen-out entry. `None` (the
                 default, and every caller before Phase 2b) or `[]` is a
                 clean no-op — byte-identical new_picks/every other return
                 key to the pre-Phase-2b output, `etf_screened_out == []`.
                 A SEPARATE candidate pool from curated_rows/mover_rows —
                 never truncated by or subjected to the equity fundamentals
                 gate, capped at its own ETF_MAX_PICKS allowance.
    cluster_add_blocks : optional (G-25, docs/plans/cluster-add-gate.md) —
                 cluster_add_gate.add_block_map() output: {TICKER: {...}} for
                 held tickers that are themselves an endpoint of a newly-
                 formed warning-tier correlation pair since the owner's last
                 acknowledged Structural Scan. `None` (the default, and every
                 caller before G-25) leaves add_positions/every other key
                 byte-identical to the pre-G-25 output — this is the
                 regression-safety net. Checked with `is not None`, never
                 `or {}`: a `None` here means "couldn't check" (D4 fail-open
                 — the add proceeds, uncaptioned as blocked) and collapsing
                 it to `{}` would be indistinguishable from "checked, nothing
                 new," the offline-sentinel-collapse bug class this project
                 has a dedicated antipattern gate for.
    pair_add_blocks : optional (G-26, owner decisions 2026-10-08) —
                 pair_add_gate.add_block_map() output: {TICKER: {...}} for
                 held tickers sitting at the danger-tier end of a STANDING
                 correlated pair (CORR_DANGER_PAIRS_THRESHOLD), independent
                 of whether a PAIR_RISK trim card exists for it. Checked
                 BEFORE cluster_add_blocks (G-25) — a pair that is both a
                 new cluster pairing and already at danger tier records
                 under G-26, never G-25. UNLIKE G-25, this has no
                 acknowledge/override path: the pause lasts for as long as
                 the pair stays at danger tier and both names are held.
                 `None` (the default, and every caller before G-26) leaves
                 every existing key byte-identical to the pre-G-26 output —
                 checked `is not None`, never `or {}`.
    corr_unchecked : optional (disclosure-only G-25/G-26 follow-on,
                 docs/plans/pair-add-gate.md's fail-open-blind-spot fix) —
                 portfolio.correlation_unchecked() output: the list of held
                 tickers missing from this render's correlation matrix (an
                 unpriceable holding's PARTNER could still proceed with an
                 add suggestion, uncaptioned, unless this is disclosed).
                 PASSTHROUGH ONLY — this function never reads it in any
                 skip/suppress decision; it is carried unchanged onto
                 `grow_today["corr_unchecked"]` on every return path
                 (including the bear-day early return) purely so the render
                 layer can show a disclosure caption. `None` (the default,
                 and every caller before this follow-on) leaves every
                 existing key byte-identical.
    """
    tone        = market_context.get("tone", "flat")
    sp500_pct   = _f(market_context.get("sp500_pct", 0))
    nasdaq_pct  = _f(market_context.get("nasdaq_pct", 0))
    lead_secs   = market_context.get("leading_sectors", [])
    lead_names  = {ls.get("sector", "") for ls in lead_secs}
    held_tickers = _held_tickers(port_df)

    # ── Cross-reference the Brief to block conflicting picks ──────────────────
    # Any ticker already flagged ANYWHERE in today's Brief (act_today OR
    # review_list — sell, stop, risk reduce, or a review-origin trim) must not
    # appear in Grow Today — contradicting signals in the same briefing is
    # dangerous. (Chunk 7a: held-name ADDS no longer read this set -- they use
    # decision_bucket.add_blocking_cards, news WATCH excluded, owner N-A
    # 2026-10-18; new picks / leads / ETF held-group keep this broad set.)
    # decision_bucket.all_flagged_tickers() is the canonical broad
    # set (2026-07-29 audit H6) and resolves ticker=None/action.trim_ticker
    # macro-card shapes correctly, which a naive `.get("ticker")` loop misses.
    _act_blocked: set = decision_bucket.all_flagged_tickers(act_today, review_list)
    # Tickers sold this session must never immediately re-appear as buys.
    if sold_today:
        _act_blocked |= sold_today
    _act_risk_flags: list[str] = []
    for _ai in (act_today or []):
        _action = str(_ai.get("action", ""))
        if "RISK —" in _action:
            _act_risk_flags.append(_action.replace("RISK — ", ""))
    risk_banner = _act_risk_flags[:3] if _act_risk_flags else None

    # Risk Advisor trim targets — used to suppress add-to-winner conflicts.
    _trim_set = _trim_targets(risk_recs)

    # Drift-trim set: positions overweight enough that the equal-weight
    # rebalancer would flag them for trim. Adding to a position the rebalance
    # logic would trim creates a same-day contradiction. Default target =
    # 100/N (equal weight); trim threshold = target + 5pp.
    _drift_trim_set: set = set()
    if port_df is not None and not port_df.empty:
        _n_pos = len(port_df)
        if _n_pos > 0:
            _eq_target = 100.0 / _n_pos
            _trim_floor = _eq_target + 5.0   # TOLERANCE_WATCH from rebalancer.py
            for _, _row in port_df.iterrows():
                if _f(_row.get("Weight (%)"), 0) > _trim_floor:
                    _drift_trim_set.add(str(_row["Ticker"]).upper())

    # Macro gate — hard-suppress new picks in sectors with imminent HIGH-impact
    # macro events. Opening a fresh position into a known binary catalyst is
    # exactly the institutional anti-pattern this gate prevents.
    # Lifted to entry_eligibility (shared new-entry check); clock read stays here.
    try:
        import pytz as _pytz
        _now_et_dt = _dt.now(_pytz.timezone("America/New_York"))
    except Exception:
        _now_et_dt = None                                  # keep gate on
    _macro_map = macro_block_map(macro_events, today, _now_et_dt)
    # None (macro_events unchecked) reads as "nothing blocked", as it always did.
    _macro_blocked_sectors: set = set(_macro_map["sectors"]) if _macro_map else set()
    _macro_block_reasons:   dict = dict(_macro_map["reasons"]) if _macro_map else {}

    # Sector concentration gate — sectors already AT/ABOVE the hard cap. Opening
    # or adding to a position in such a sector worsens a breach the Risk Advisor
    # is simultaneously telling the user to TRIM (the ESTC case: a Strong-Buy add
    # surfaced while its sector was 44% over the 35% cap). The deploy-capital
    # signal must defer to the protect-capital signal. Mirrors the macro gate.
    # Elevated (not yet hard-capped) sectors — the same SECTOR_ELEVATED band
    # Risk Advisor already renders as its MEDIUM "Elevated Sector Concentration"
    # card. A pick here isn't suppressed (a 25-35% sector isn't yet a breach),
    # but it's warned so the buy side doesn't stay silent about a sector the
    # trim side is already watching (the SHOP case: a buy recommended while
    # under 35% that, once bought, immediately became the trim engine's #1
    # full-exit candidate the same day — see risk_advisor's trim_excluded_recent
    # for the matching same-day-buy backstop on the trim side).
    _breached_sectors: set = set()
    _elevated_sectors: set = set()
    _sector_wt_map: dict = {}
    if port_df is not None and not port_df.empty and "Weight (%)" in port_df.columns:
        _sec_wt = port_df.groupby("Sector")[_gate_wt_col(port_df)].sum()
        # "Other" is a classification artifact (unclassified holdings), not a
        # real correlated sector — exclude it so picks/adds aren't suppressed by
        # a phantom cap breach. Mirrors risk_advisor's UNCLASSIFIED_SECTOR exclusion.
        _sector_wt_map = {str(_s): _f(_w, 0) for _s, _w in _sec_wt.items()
                          if str(_s) != UNCLASSIFIED_SECTOR}
        _breached_sectors = {_s for _s, _w in _sector_wt_map.items()
                             if _w >= SECTOR_CEILING}
        _elevated_sectors = {_s for _s, _w in _sector_wt_map.items()
                             if SECTOR_ELEVATED <= _w < SECTOR_CEILING}

    # Deterioration WATCH map (hoisted above the bear early return: the
    # adds_paused disclosure below reads it, and so does the G-20 pre-pass).
    _watch_by_ticker: dict = {
        str(d["ticker"]).upper(): d for d in (deterioration or []) if d.get("tier") == "WATCH"
    }

    # Add-lane CARD set (chunk 7a, owner decision N-A Option 2, 2026-10-18):
    # any Act Today card (incl. critical news), any Review card EXCEPT a news
    # WATCH, or a sale today. A Review news WATCH is an ENTRY decision (below),
    # not a card block; the add card carries a note instead.
    _add_cards: dict = decision_bucket.add_blocking_cards(act_today, review_list, sold_today)
    _review_news_watch: dict = decision_bucket.review_news_watch_by_ticker(review_list)

    _entry_memo: dict = {}

    def _entry_verdict(_r) -> dict:
        """The shared new-entry check for ONE held name's add -- exactly stale
        data, negative news and imminent earnings (no tone: the bull loop only
        runs on bull days; no macro: owner decision N-C). Memoised per ticker so
        the bull loop and adds_paused read the same verdict. A crash fails
        CLOSED (a hold), never open."""
        _t = str(_r["Ticker"])
        _tu = _t.upper()
        if _tu in _entry_memo:
            return _entry_memo[_tu]
        try:
            _b = (held_data or {}).get(_t)
            if not _b:
                _b = None
            _ed = None
            if earnings_lookup and _t in earnings_lookup:
                _ed = earnings_lookup[_t]
            elif isinstance(_b, dict):
                _ed = _b.get("earnings")
            _edays = _days_until(_ed, today) if _ed else None
            _nitems = [n for n in (news_items or [])
                       if str(n.get("ticker", "")).upper() == _tu]
            _v = entry_eligibility.entry_check(
                bundle=_b,
                news=entry_eligibility.news_ctx_from_items(_nitems),
                earnings_days=_edays,
                ticker=_t,
                checks=(entry_eligibility.check_stale,
                        entry_eligibility.check_news,
                        entry_eligibility.check_earnings),
            )
        except Exception:
            _h = {"check": "check_failed", "gate_id": None,
                  "reason": "entry check couldn't run",
                  "clears_when": "the entry check can run again — reload the page",
                  "gate_value": None, "gate_threshold": None}
            _v = {"eligible": False, "holds": [_h], "binding": _h,
                  "unchecked": [], "passed": []}
        _entry_memo[_tu] = _v
        return _v

    def _add_block_gate(_r) -> "str | None":
        """THE add-gate decision, in the order the bull loop applies it. Returns
        None (nothing blocks), "CARD" (a card in today's Brief or a sale today),
        the gate id that holds the add (G-20 WATCH, G-24 cooldown, G-16 sector
        cap, G-01 risk trim, G-04 single-name ceiling, G-09 drift trim, G-26
        danger pair, G-25 new cluster), or "ENTRY" (the shared entry check:
        stale data / negative news / imminent earnings) -- checked LAST so every
        existing gate's bucket and ledger rows are unchanged. Shared by the bull
        add loop (which records the bucket per gate) and adds_paused, so the two
        cannot diverge."""
        _t = str(_r["Ticker"])
        _tu = _t.upper()
        if _tu in _add_cards:
            return "CARD"
        if _tu in _watch_by_ticker:
            return "G-20"
        if _recently_added(_t, held_data):
            return "G-24"
        if str(_r.get("Sector", "")) in _breached_sectors:
            return "G-16"
        if _tu in _trim_set:
            return "G-01"
        if _gate_wt(_r) >= SINGLE_NAME_CEILING:
            return "G-04"
        if _tu in _drift_trim_set:
            return "G-09"
        if pair_add_blocks is not None and _tu in pair_add_blocks:
            return "G-26"
        if cluster_add_blocks is not None and _tu in cluster_add_blocks:
            return "G-25"
        if not _entry_verdict(_r)["eligible"]:
            return "ENTRY"
        return None

    def _adds_paused_list() -> list[dict]:
        """OP-07 disclosure: on a non-bull day, held names that WOULD qualify
        for an add-to-winner (the shared `_add_entry_ok` rule) and that
        `_add_block_gate` does not hold. Ticker + composite only -- never a
        size."""
        out: list[dict] = []
        if port_df is None or port_df.empty:
            return out
        for _, _r in port_df.iterrows():
            if not _add_entry_ok(_r) or _add_block_gate(_r) is not None:
                continue
            out.append({"ticker": str(_r["Ticker"]), "composite_score": _f(_r.get("Score"), 0)})
        out.sort(key=lambda x: (-x["composite_score"], x["ticker"]))
        return out

    def _unevaluated_leads(evaluated: set) -> list[dict]:
        """Scanner names Grow Today did NOT evaluate today: momentum >=
        COMPOSITE_BUY, not held, not already flagged, and not in `evaluated`
        (the working set actually run through the gates). Ticker + momentum
        only -- they carry NO verdict and are never buy calls."""
        out: list[dict] = []
        if scanner_results is None or scanner_results.empty:
            return out
        _seen: set = set()
        for _, _r in scanner_results.iterrows():
            _t = str(_r["Ticker"])
            _tu = _t.upper()
            _m = _f(_r.get("Score"), 0)
            if (_m < COMPOSITE_BUY or _t in held_tickers or _t in _act_blocked
                    or _tu in evaluated or _tu in _seen):
                continue
            _seen.add(_tu)
            out.append({"ticker": _t, "momentum_score": _m})
        out.sort(key=lambda x: (-x["momentum_score"], x["ticker"]))
        return out

    # On bear days — no new entries, return protection message
    if tone == "bear":
        # macro_coverage_expired is built AFTER all suppression decisions so it
        # is physically impossible for the gate to read it.  On the bear path,
        # new_picks == [] regardless of coverage, so the render condition
        # `macro_expired and new_picks` is provably False.
        _bear_expired = _expired_macro_series(today)
        return {
            "tone":          "bear",
            "message":       (
                f"S&P 500 {sp500_pct:+.2f}% today — market in risk-off mode. "
                "Focus on protecting existing positions. "
                "Defer new entries until conditions stabilise."
            ),
            "new_picks":                  [],
            "add_positions":              [],
            "risk_blocked_adds":          [],
            "concentration_blocked_adds": [],
            "sector_blocked_adds":        [],
            # Chunk 7a: a bear day suppresses every add outright, so neither
            # the CARD nor the ENTRY bucket can have anything to record.
            "card_blocked_adds":          [],
            "entry_held_adds":            [],
            # G-31 (S-D): always a list, never omitted; a bear day has no adds.
            "room_blocked_adds":          [],
            "sector_blocked_picks":       [],
            "sector_unknown_picks":       [],
            "macro_blocked_picks":        [],
            "composite_skipped":          [],
            "composite_unavailable":      [],
            "news_skipped":               [],
            "earnings_waiting":           [],
            "etf_screened_out":           [],
            # Grow Today owns every buy (2026-10-15): on a bear day nothing is
            # picked, so nothing can be pacing-held or flat-day-held; the
            # scanner names are ALL unevaluated and the would-qualify adds are
            # disclosed as paused. Always present, never omitted.
            "overflow_picks":             [],
            "flat_day_held":              [],
            "unevaluated_leads":          _unevaluated_leads(set()),
            "adds_paused":                _adds_paused_list(),
            # G-25: always a list, never omitted, even on this early return —
            # a bear day suppresses every add-to-winner lane outright (the
            # loop below never runs), so there is nothing for the cluster
            # gate to have blocked.
            "cluster_blocked_adds":       [],
            "cluster_gate_checked":       cluster_add_blocks is not None,
            # G-26: same "always present, never omitted" posture as G-25
            # above — a bear day suppresses every add-to-winner lane outright
            # (the loop below never runs), so this function's own
            # pair_blocked_adds bucket is necessarily [].
            "pair_blocked_adds":          [],
            "pair_gate_checked":          pair_add_blocks is not None,
            # Disclosure-only follow-on — passthrough, unchanged, never read
            # in any decision above (the add loop never runs on a bear day
            # anyway, so there is nothing for this to have gated even if it
            # were read).
            "corr_unchecked":             corr_unchecked,
            "risk_banner":                risk_banner,
            # LATE construction: built after every suppression decision so the
            # gate above can never read this value.
            "macro_coverage_expired":     _bear_expired,
        }

    # Score threshold: higher bar on flat days, standard on bull days
    min_score   = COMPOSITE_BUY if tone == "bull" else COMPOSITE_BUY_FLAT_DAY
    max_picks   = GROW_MAX_PICKS_BULL if tone == "bull" else GROW_MAX_PICKS_DEFAULT

    new_picks: list[dict] = []
    _confirmed_picks: list[dict] = []
    _unverified_picks: list[dict] = []
    macro_blocked_picks: list[dict] = []
    sector_blocked_picks: list[dict] = []   # new picks suppressed — sector over hard cap
    # D1 (sector_gate_spec.md, 2026-09-30) — FAIL CLOSED: a pick whose sector
    # couldn't be classified at all (not in TICKER_SECTORS and no usable row/
    # provider label) is held out of new_picks entirely, same posture as a
    # composite fetch failure. No gate_id — this is a data abstention, not a
    # ledgered policy gate, so it is never added to the gate_ledger key map.
    sector_unknown_picks: list[dict] = []
    composite_skipped:  list[dict] = []
    # Picks where the composite fetch FAILED (load_all raised / not in cache).
    # Distinct from composite_skipped (where composite loaded but < BUY).
    # Kept out of new_picks entirely so we never surface a half-validated
    # recommendation; the Brief renders an aggregate banner with a Refresh
    # button so the user can retry the data load.
    composite_unavailable: list[dict] = []
    # Picks whose RECONCILED verdict is "skip" (F-302) -- most often "❌ Skip
    # — Negative News". Held out of new_picks like composite_skipped.
    news_skipped: list[dict] = []
    # Picks whose reconciled verdict is "caution" -- earnings within
    # EARNINGS_IMMINENT_DAYS, "Wait for the post-print setup" (F-303). Held
    # out like news_skipped; they come back once the print has passed.
    earnings_waiting: list[dict] = []

    # Build ONE candidate pool from two sources so the user sees a single
    # "New Positions to Initiate" list regardless of where a ticker came from:
    #   (a) curated scanner picks — qualify on momentum Score ≥ min_score
    #   (b) movers — qualify on today's 1-day gain (applied upstream in
    #       scan_movers); NOT momentum-gated, since a fresh breakout may not
    #       show in 1M/3M momentum yet.
    # Both then face the SAME gates below (act-today block, macro suppression,
    # cross-ref verdict, composite ≥ BUY, sector diversity) and compete for the
    # SAME max_picks cap. This is what stops the flat-day contradiction where
    # the main section said "no new entries" while a separate Movers section
    # listed four. Movers are a SOURCE of candidates, not a parallel pipeline.
    def _sector_bonus(sector: str) -> int:
        # Checks EVERY alias a leading ETF represents, not just its single
        # display "sector" name -- SECTOR_ETF has 3 keys sharing one ETF
        # (IGV: AI & Cloud / AI & Data / Enterprise Tech), so a candidate in
        # any of those SECTOR_UNIVERSE buckets must be reachable when that
        # ETF leads, not just the first-key-wins display name. "aliases"
        # falls back to ["sector"] for a market_context dict cached before
        # this field existed (e.g. across a redeploy boundary).
        return 5 if any(
            alias in str(sector)
            for ls in lead_secs
            for alias in (ls.get("aliases") or [ls.get("sector", "")])
        ) else 0

    # Curated pool — momentum-gated, ranked by momentum + sector bonus, then
    # truncated to a working set for sector-diversity selection.
    curated_rows: list[dict] = []
    if scanner_results is not None and not scanner_results.empty:
        _curated = scanner_results[
            (scanner_results["Score"] >= min_score) &
            (~scanner_results["Ticker"].isin(held_tickers))
        ]
        for _, r in _curated.iterrows():
            d = dict(r)
            # RANKING-ONLY (2026-09-30): this reads the row's raw "Sector"
            # label straight from scanner_results, deliberately NOT the
            # gate_sector-resolved value computed later per-candidate below —
            # a sort-order nudge toward today's leading sector is a different,
            # lower-stakes concern than the hard sector-ceiling/macro gates,
            # which must use the curated/provider-resolved sector instead.
            d["_rank"]       = _f(d.get("Score", 0)) + _sector_bonus(d.get("Sector", ""))
            d["_is_mover"]   = False
            d["_day_change"] = None
            curated_rows.append(d)
    curated_rows.sort(key=lambda d: d.get("_rank", 0), reverse=True)
    curated_rows = curated_rows[: max_picks * GROW_CANDIDATE_OVERFETCH]

    # Mover pool — kept SEPARATE so it isn't truncated out by higher-ranked
    # curated names and isn't subject to the curated momentum bar or the
    # flat-day verdict gate. Ranked by composite (their quality signal).
    mover_rows: list[dict] = []
    _seen_ct = {str(d.get("Ticker", "")).upper() for d in curated_rows}
    for m in (movers or []):
        mt = str(m.get("ticker", "")).upper()
        if not mt or mt in held_tickers or mt in _seen_ct:
            continue
        _sector = str(m.get("sector", ""))
        mover_rows.append({
            "Ticker":       mt,
            "Price":        _f(m.get("price")),
            "Sector":       _sector,
            "Trend":        str(m.get("trend", "")),
            "Signal":       str(m.get("scanner_signal", "")),
            "Score":        _f(m.get("score")),          # momentum (may be modest)
            "RSI":          _f(m.get("rsi", 50)),
            "1M Momentum":  _f(m.get("mom_1m", 0)),
            "3M Momentum":  _f(m.get("mom_3m", 0)),
            "_rank":        _f(m.get("composite_score")),
            "_is_mover":    True,
            "_day_change":  _f(m.get("day_change")),
        })
        _seen_ct.add(mt)
    mover_rows.sort(key=lambda d: d.get("_rank", 0), reverse=True)

    # Scanner names that never reached the gates below (flat day: momentum under
    # the flat-day bar or past the working-set truncation; bull day: only the
    # truncation). Listed by name with momentum only -- no verdict.
    unevaluated_leads = _unevaluated_leads(
        {str(d.get("Ticker", "")).upper() for d in curated_rows}
        | {str(d.get("Ticker", "")).upper() for d in mover_rows}
    )
    # Pacing-only leftovers: cleared EVERY check but lost to the pick cap, the
    # 1-per-sector rule or the mover cap. Unsized by construction.
    overflow_picks: list[dict] = []
    # G-32 C2 shadow inputs (measure-only): ticker -> (bundle, lane price, lane
    # stop) recorded where each lane sizes. Read only by the final shadow
    # attach below; never by a routing decision.
    _g32_pick_src: dict = {}
    _g32_add_src: dict = {}
    # Curated picks the flat-day check holds back (verdict not fully confirmed).
    flat_day_held: list[dict] = []

    _mover_picks: list[dict] = []

    if curated_rows or mover_rows:
        for row in curated_rows + mover_rows:
            ticker   = str(row["Ticker"])
            price    = _f(row.get("Price", 0))
            # Hoisted ahead of sector resolution (2026-09-30, Hole 2 fix) so
            # gate_sector can fall back to the REAL provider sector already
            # sitting in the composite bundle when the row's own "Sector" is
            # a placeholder (scanner.py tags every watchlist extra
            # "Watchlist" — display-only, never a real sector). The later
            # uses of _comp_data below are unchanged.
            _comp_data = (composites or {}).get(ticker, {})
            sector   = gate_sector(ticker, row_label=row.get("Sector", ""),
                                   provider_sector=_comp_data.get("sector"))
            trend    = str(row.get("Trend", ""))
            # Reuses _sector_bonus's own alias-aware match rather than
            # re-implementing the same first-key-wins-only check here --
            # this exact duplicated check (pre-fix) would otherwise have
            # kept disagreeing with the ranking bonus after the alias fix
            # above: a pick could get the +5 rank bonus but still render
            # without its "leading sector" badge, or vice versa.
            is_leader = bool(_sector_bonus(sector))
            is_mover  = bool(row.get("_is_mover"))
            day_change = row.get("_day_change")

            # Skip if Act Today already has an action on this ticker
            if ticker in _act_blocked:
                continue

            # Macro gate — hard-suppress if sector has an imminent HIGH-impact
            # macro event. Opening into a known binary catalyst is the
            # anti-pattern; surface the suppression so the user knows.
            _macro_block = macro_hold(sector, _macro_map)
            if _macro_block:
                macro_blocked_picks.append({
                    "ticker": ticker, "sector": sector,
                    "score":  _f(row.get("Score", 0)),
                    "reason": _macro_block,
                    "gate_id":        "G-07",
                    "counterfactual": True,
                    "gate_value":     None,
                    "gate_threshold": MACRO_IMMINENT_DAYS,
                    "price":          price,
                    "composite_score": _ledger_composite(composites, ticker),
                    "momentum_score":  _f(row.get("Score"), None),
                })
                continue

            # Sector concentration gate — same rationale as macro: don't open a
            # fresh position in a sector already over the hard cap.
            if sector in _breached_sectors:
                sector_blocked_picks.append({
                    "ticker": ticker, "sector": sector,
                    "score":  _f(row.get("Score", 0)),
                    "reason": f"{sector} sector already ≥ {SECTOR_CEILING:.0f}% hard cap",
                    "gate_id":        "G-16",
                    "counterfactual": True,
                    "gate_value":     _sector_wt_map.get(sector),
                    "gate_threshold": SECTOR_CEILING,
                    "price":          price,
                    "composite_score": _ledger_composite(composites, ticker),
                    "momentum_score":  _f(row.get("Score"), None),
                })
                continue

            xref = _cross_reference(ticker, row, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup, composites=composites,
                                    is_mover=is_mover)

            # Hard conflicts are never recommended -- but they are no longer
            # dropped SILENTLY here (F-302 follow-up, 2026-10-14). A legacy
            # "conflicted" pick runs on through the data-quality and composite
            # gates below so it lands in the bucket that names its reason: a
            # Hold/Sell composite is a composite below the Buy bar
            # (composite_skipped), a stale/missing one is composite_unavailable,
            # and anything left is held out into news_skipped after the
            # reconciled-skip check. Since 2026-10-18 negative news (with or
            # without imminent earnings) is a reconciled skip at any momentum,
            # so that last branch is a defensive backstop. It can never reach
            # sizing or new_picks.
            _legacy_conflicted = xref["verdict"] == "conflicted"
            # Flat-day high-conviction suppression — CURATED ONLY. A composite-Buy
            # mover up 5%+ today is itself the "clearer direction" the flat-day
            # caution waits for, so movers are exempt from this gate (they still
            # face the composite gate, macro gate, act-today block below, and are
            # never reached on bear days due to the early return).
            _flat_hold_item = None
            if tone == "flat" and not is_mover and xref["verdict"] not in ("confirmed", "unverified"):
                # No longer silent (OP-07): name it, say why, and -- only where
                # the data supports it -- when it could clear. The earnings date
                # is derivable from xref["earn_days"]; every other verdict has
                # no single clearing event, so clears_when stays None.
                _fd_earn = xref.get("earn_days")
                _fd_comp = _ledger_composite(composites, ticker)
                _fd_earn_date = None
                _fd_clears = None
                # Only a name that would otherwise qualify (trusted composite at
                # the Buy bar) gets a clearing hint; earn_days == 0 still counts
                # as imminent, so the hold lifts the day AFTER the date.
                if (xref["verdict"] == "caution" and _fd_earn is not None
                        and _fd_comp is not None and _fd_comp >= COMPOSITE_BUY):
                    _fd_earn_date = (today + timedelta(days=_fd_earn)).isoformat()
                    _fd_clears = f"after earnings ({_fd_earn_date}), if it still qualifies"
                _fd_why = str(xref.get("verdict_label") or xref["verdict"])
                if xref["conflicts"]:
                    _fd_why += " — " + "; ".join(str(c) for c in xref["conflicts"])
                # The item is only STAGED here. It is committed to
                # flat_day_held after the data-quality and composite gates
                # below, so a name those gates would reject on ANY day (a sub-
                # Buy composite, or an unmeasured/stale one) lands in the
                # bucket that names its real reason instead of being blamed on
                # the flat market (2026-10-15: a 57 sat above a 62 in
                # "composite says no"). Only a name that would otherwise
                # qualify stays held by the flat-day check.
                _flat_hold_item = {
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _fd_comp,
                    "verdict":         xref["verdict"],
                    "reason":          _fd_why,
                    "clears_when":     _fd_clears,
                    "earnings_date":   _fd_earn_date,
                    "earn_days":       _fd_earn,
                }

            _base_thesis = _thesis(ticker, row, is_leader)
            # Movers lead with today's move (their entry trigger), then the
            # standard trend/momentum thesis.
            thesis = (
                f"Up {day_change:+.1f}% today — breakout outside your core universe. {_base_thesis}"
                if is_mover and day_change is not None else _base_thesis
            )

            # Validate conviction using full composite score when available.
            # Scanner score measures momentum only; composite includes fundamentals
            # and sentiment — a pick can score 100 on momentum but 63 composite.
            # _comp_data itself is hoisted above (before sector resolution).
            _composite_score = _f(_comp_data.get("total")) if _comp_data else None
            _composite_label = str((_comp_data.get("rec") or {}).get("label", "")) if _comp_data else ""

            # ETF/fund new-pick exclusion (ETF-support Phase 2, F-279 §11) —
            # belt-and-suspenders: an ETF must never become new-pick eligible
            # this phase ("existing holdings only, no new-pick eligibility"),
            # even if a future data change made fundamentals_available/
            # val_available accidentally True for it. Today those flags are
            # already False for every ETF (the fundamentals-metric-count gate
            # below already catches it), so this is redundant on purpose, not
            # the load-bearing guard — an explicit, deliberate invariant.
            if _comp_data.get("asset_type") == asset_type.ASSET_TYPE_ETF:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Staleness gate: if the bundle was served from the Supabase cache
            # fallback (stale_as_of is not None), the composite could reflect data
            # up to BUNDLE_CACHE_MAX_AGE_DAYS (5) old. That is too stale to back
            # a new-position recommendation — a deteriorating ticker can appear
            # composite-healthy on 5-day-old fundamentals and then score Sell on a
            # fresh Analysis fetch minutes later (the INTC incident, 2026-07-14).
            if data_trust_hold(_comp_data) is not None:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Fundamentals/Valuation gate: if the composite was computed on a
            # fabricated neutral-50 fundamental OR valuation leg (no real data
            # from any source), the verdict isn't trustworthy — hold it OUT of
            # new_picks exactly like a failed fetch. Otherwise the Brief can
            # surface a "new position to initiate" whose Analysis page
            # withholds its verdict (the PINS/HUBS mismatch, and — since
            # 2026-08-04 — the same class for Valuation's own availability flag).
            # Since 2026-10-15 the Technical pillar is held to the same bar
            # (t_available — too little price history reads a neutral-ish
            # technical leg too). All three legs are decided by the ONE shared
            # pillar_availability.verdict_basis (this is the stock new-pick
            # lane, so anything but "equity" is held out); "missing" names
            # which pillar(s) so the render can say why. Default True so legacy
            # bundles without any flag aren't gated.
            if _comp_data and verdict_basis(_comp_data) != "equity":
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                    "missing":        unmeasured_pillars(_comp_data),
                })
                continue

            # Exclude picks where composite is known and below the Buy threshold.
            # Scanner score measures momentum only — composite (technical + fundamental
            # + sentiment) is the authoritative signal. A 100/100 momentum score with
            # a sub-COMPOSITE_BUY composite is not a high-conviction entry.
            #
            # Record the rejection so the Brief can render a visible "Filtered Out"
            # bucket — silent drops leave the user wondering why a hot momentum
            # ticker isn't being recommended. This is the TSLA case: Momentum 90
            # but composite 48.8 Hold.
            if _composite_score is not None and _composite_score < COMPOSITE_BUY:
                composite_skipped.append({
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _composite_score,
                    "composite_label": _composite_label or "Hold",
                    # Display-only: a mover was caught by today's breakout, not
                    # a momentum score, so the render must not say "Momentum".
                    "is_mover":        is_mover,
                    "day_change":      day_change,
                })
                continue

            # Composite fetch failed — pick is held out of new_picks entirely.
            # Surfacing a half-validated card next to high-conviction picks led to
            # user-visible contradictions (e.g. INTC presented as a new position
            # to initiate while Analysis page rated it Sell at 38.3). Per the
            # "decides, not informs" posture: if we can't validate, we don't
            # recommend. The Brief renders an aggregate "Pending Verification"
            # banner with a Refresh button so the user can retry the fetch.
            if _composite_score is None:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Flat-day hold commits here: the name cleared the data-quality
            # and composite gates above; held by the flat-day verdict check.
            if _flat_hold_item is not None:
                flat_day_held.append(_flat_hold_item)
                continue

            # Sector-unknown gate (D1, sector_gate_spec.md, 2026-09-30) — FAIL
            # CLOSED. Deliberately placed AFTER every ETF/stale/fundamentals/
            # composite gate above (ordering invariant: those always win) and
            # BEFORE the conviction tier below, so a pick that already failed
            # on real data quality is attributed to THAT reason, never masked
            # by a sector abstention. A ticker that is neither in TICKER_
            # SECTORS nor carries a usable row/provider label means the
            # sector ceiling and macro-sector checks above could not run
            # against it at all — that is a reason to withhold the
            # recommendation, not a green light. No gate_id: a data
            # abstention, not a ledgered policy gate.
            if sector == UNCLASSIFIED_SECTOR:
                sector_unknown_picks.append({
                    "ticker":          ticker,
                    "sector":          sector,
                    "score":           _f(row.get("Score", 0)),
                    "reason":          "Sector unknown — the sector ceiling and macro sector checks couldn't run",
                    "price":           price,
                    "composite_score": _composite_score,
                    "momentum_score":  _f(row.get("Score"), None),
                })
                continue

            # Reconciled-skip hold-out (F-302, 2026-10-14). The legacy filter
            # above drops only xref["verdict"] == "conflicted", but the card
            # renders reconcile_signals()' verdict -- so a "❌ Skip — Negative
            # News" pick (legacy "mixed") used to reach New Positions WITH a
            # share size and get rec-logged as a new_pick, grading the Engine
            # Track Record on a call the card itself said to skip. The morning
            # email was already go-only (cron_runner), so it never listed them.
            # Placed after every data-quality gate so a pick that failed on
            # real data is still attributed to THAT reason. Held out into a
            # visible bucket, never silently dropped.
            _rv = xref.get("verdict_reconciled")
            if isinstance(_rv, dict) and _rv.get("verdict") == "skip":
                _rv_item = {
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _composite_score,
                    "label":           str(_rv.get("label") or "❌ Skip"),
                    "one_liner":       str(_rv.get("one_liner") or ""),
                    "is_mover":        is_mover,
                }
                # G-30 (chunk 7b / N-B2): only the negative-news skip is ledgered.
                # The "Signals Disagree" skip is a different gate and the
                # legacy-conflicted branch below stays unlabelled.
                if _rv_item["label"] == _NEGATIVE_NEWS_SKIP_LABEL:
                    _nav = _ticker_news_avg(news_items, ticker)
                    _rv_item.update({
                        "gate_id":        "G-30",
                        "counterfactual": True,
                        "gate_value":     _nav,
                        "gate_threshold": NEWS_SENTIMENT_NEGATIVE,
                        "price":          price,
                        "reason":         _rv_item["one_liner"],
                    })
                news_skipped.append(_rv_item)
                continue
            if _legacy_conflicted:
                news_skipped.append({
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _composite_score,
                    "label":           str(xref.get("verdict_label") or "❌ Conflicted"),
                    "one_liner":       "; ".join(str(c) for c in xref["conflicts"]),
                    "is_mover":        is_mover,
                })
                continue
            # Earnings-wait hold-out (F-303, owner decision 2026-10-14). The
            # card says "Wait for the post-print setup", so the app must not
            # list it as a New Position with a share size -- nor log it as a
            # new_pick for the Engine Track Record. The go-only emails already
            # left it out. After this check, every pick left is reconciled "go".
            if isinstance(_rv, dict) and _rv.get("verdict") == "caution":
                earnings_waiting.append({
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _composite_score,
                    "label":           str(_rv.get("label") or "⚠️ Caution"),
                    "one_liner":       str(_rv.get("one_liner") or ""),
                    "earn_days":       xref.get("earn_days"),
                    "is_mover":        is_mover,
                })
                continue

            # Conviction tier: drives the label shown on the card.
            # "high" requires the same bar as Strong Buy (no looser tier here —
            # previously a custom 68 magic number existed that called scores
            # 68-74 "high conviction" even though they don't clear COMPOSITE_
            # STRONG_BUY=75; that asymmetry contradicted scoring.py's label).
            if _composite_score is None:
                conviction = "unverified"
            elif _composite_score >= COMPOSITE_HIGH_CONVICTION:
                conviction = "high"
            elif _composite_score >= COMPOSITE_BUY:
                conviction = "moderate"
            else:
                conviction = "low"

            # Entry Timing (F-220 Phase 2) — momentum vs composite divergence at
            # the moment this pick fires. Diagnostic annotation only, never a
            # gate: the pick has already cleared every gate above (composite,
            # staleness, fundamentals, macro, sector, act-today) by this point.
            # Grow Today's own render (app.py) decides whether/how to caption it.
            _divergence = divergence_at_entry({
                "momentum_score": _f(row.get("Score", 0)), "composite_score": _composite_score,
            })

            # Sizing — computed here, after the composite gate, so _comp_data is
            # guaranteed non-empty. Uses the ATR stop from the bundle (same source
            # as the stop ladder shown on the Analysis page) and the ceiling-capped
            # position_sizing() that every other sizing surface uses.
            # Stop on the SAME price basis as the size. `_comp_data["stop"]` sits
            # ATR_STOP_MULT x ATR below the BUNDLE's last close, but `price` here is
            # the scanner row's quote — for movers (up 5%+ intraday) those diverge.
            # Above the stale stop that only over-states risk_per_share and
            # under-sizes (safe); BELOW it, risk_per_share goes negative and the
            # size vanished with no explanation on the primary buy surface. Deriving
            # the stop from the bundle's ATR against the live price keeps
            # risk_per_share exactly ATR_STOP_MULT x ATR and makes stop_pct honest.
            # Falls back to the bundle's own stop when ATR is unavailable.
            _pick_atr  = _f(_comp_data.get("atr"))
            # Fall back on the RESULT, not the input: a name whose ATR is >= half
            # its price yields a NON-POSITIVE derived stop, which would trip the
            # adapter's missing-input guard and render a BUY with no size and no
            # caption — the same silent shape this fix exists to remove.
            _atr_stop  = round(price - ATR_STOP_MULT * _pick_atr, 2) if _pick_atr > 0 else 0.0
            _pick_stop = _atr_stop if _atr_stop > 0 else _f(_comp_data.get("stop"))
            # Entry zone on the SAME live-price basis as the stop and the size.
            # The bundle's own entry_lo/hi are derived from its last close, so for
            # a mover the zone could sit entirely BELOW the current quote while the
            # card headline said BUY — and the emails' "only act if price is still
            # inside $X-$Y" guard would then contradict their own headline.
            _pick_lo, _pick_hi = (
                entry_zone(price, _pick_atr) if _pick_atr > 0
                else (_comp_data.get("entry_lo"), _comp_data.get("entry_hi"))
            )
            sizing = (
                _position_size_for_render(
                    portfolio_value, price, _pick_stop, _pick_lo, _pick_hi,
                    net_capital=net_capital,
                )
                if price > 0 and portfolio_value > 0 else {}
            )

            # Pre-purchase deterioration warning (2026-09-11 ON incident
            # follow-up) — warn-only, never suppresses or reorders the pick.
            # Describes the CANDIDATE STOCK's own chart, not a position (the
            # user doesn't own it yet), so this is a genuinely different,
            # correct use of the ~3-month fallback window than the age_days=0
            # bug fixed in exit_advisor._peak_window_bars above (a candidate
            # has no entry date at all, vs. that fix's known-age-zero case).
            _cand_det = exit_advisor.candidate_deterioration_flag(
                ticker, _comp_data.get("df"), spy_df, price=price, atr=_pick_atr,
            )
            _cand_det_warning = exit_advisor.candidate_deterioration_caption(
                _cand_det, ticker, verdict_phrase="a buy",
            )

            _g32_pick_src[ticker.upper()] = (_comp_data, price, _pick_stop)
            pick = {
                "ticker":          ticker,
                "score":           _f(row.get("Score", 0)),    # momentum / scanner score
                "composite_score":      _composite_score,           # full composite, or None
                "composite_label":      _composite_label,
                "composite_fetched_at": _comp_data.get("fetched_at"),
                "conviction":           conviction,
                "sector":          sector,
                "price":           price,
                "trend":           trend,
                "scanner_signal":  str(row.get("Signal", "")),
                "is_leader":       is_leader,
                "is_mover":        is_mover,
                "day_change":      day_change,
                "thesis":          thesis,
                "sizing":          sizing,
                "xref":            xref,
                "divergence":      _divergence,
                # Elevated (not hard-capped) sector warning — see _elevated_sectors
                # above. Not a gate: the pick stands, but the user sees the sector
                # is already in the band Risk Advisor is watching for a trim.
                "sector_elevated_warning": (
                    f"{sector} sector already at {_sector_wt_map.get(sector, 0):.1f}% "
                    f"(warn level {SECTOR_ELEVATED:.0f}%) — this buy adds to a sector "
                    "Risk Advisor already has flagged for trim."
                    if sector in _elevated_sectors else None
                ),
                "deterioration_warning": _cand_det_warning,
            }
            if is_mover:
                _mover_picks.append(pick)
            elif xref["verdict"] == "confirmed":
                _confirmed_picks.append(pick)
            else:
                _unverified_picks.append(pick)

        # Curated selection: confirmed before unverified, 1-per-sector so all
        # slots don't land in the same sector, capped at max_picks.
        _seen_sectors: set[str] = set()
        for _pick in _confirmed_picks + _unverified_picks:
            _ps = _pick.get("sector", "")
            if _ps and _ps in _seen_sectors:
                continue
            new_picks.append(_pick)
            if _ps:
                _seen_sectors.add(_ps)
            if len(new_picks) >= max_picks:
                break

        # Mover selection: OWN allowance (MOVER_MAX_PICKS), independent of the
        # curated cap. No sector-diversity constraint — multiple breakouts in
        # one sector is itself a signal (a sector-wide move), and forcing
        # 1-per-sector would hide most of them (the HUBS/ESTC/ADBE/INTU case,
        # all Technology). Ranked by composite (already sorted). Deduped
        # against anything already chosen.
        _already = {p["ticker"] for p in new_picks}
        _movers_added = 0
        for _pick in _mover_picks:
            if _pick["ticker"] in _already:
                continue
            new_picks.append(_pick)
            _movers_added += 1
            if _movers_added >= MOVER_MAX_PICKS:
                break

        # Pacing leftovers (stocks only; the ETF lane has its own cap). Built
        # AFTER both selections from picks that cleared every gate, so they are
        # disjoint from new_picks and from every held-out bucket by
        # construction. SIZING IS DELIBERATELY NOT COPIED -- "not a second buy
        # list". Reason code: sector_slot when an already-chosen pick holds the
        # candidate's sector, else pick_cap; mover_cap for breakouts past
        # MOVER_MAX_PICKS.
        _picked_t = {p["ticker"] for p in new_picks}

        def _overflow_item(_p: dict, _why: str) -> dict:
            return {
                "ticker":          _p["ticker"],
                "sector":          _p.get("sector"),
                "composite_score": _p.get("composite_score"),
                "momentum_score":  _p.get("score"),
                "price":           _p.get("price"),
                "reason":          _why,
                "verdict":         _p["xref"].get("verdict"),
                "is_mover":        bool(_p.get("is_mover")),
            }

        for _pick in _confirmed_picks + _unverified_picks:
            if _pick["ticker"] in _picked_t:
                continue
            _ps = _pick.get("sector", "")
            overflow_picks.append(_overflow_item(
                _pick, "sector_slot" if (_ps and _ps in _seen_sectors) else "pick_cap"))
        for _pick in _mover_picks:
            if _pick["ticker"] in _picked_t:
                continue
            overflow_picks.append(_overflow_item(_pick, "mover_cap"))

        # Personalized Discovery — flag which of TODAY's already-gated new_picks
        # resemble the user's own REALIZED winning entries. new_picks ONLY (not
        # add_positions below — an add-to-winner is a call already made once,
        # not a fresh entry decision). None-safe: score_candidate_match() returns
        # an empty match when winner_profile is None (not enough history yet).
        for _pick in new_picks:
            _pick["personalized_match"] = score_candidate_match(
                _pick.get("composite_score"), _pick.get("score"),
                _pick.get("sector"), winner_profile,
            )

    # ── ETF new-pick candidates (ETF-support Phase 2b) ────────────────────────
    # Deliberately OUTSIDE the `if curated_rows or mover_rows:` wrapper above —
    # a day with zero stock candidates must not silently skip the ETF pool too.
    # A SEPARATE pool from curated_rows/mover_rows: never truncated by, or
    # subject to, the equity fundamentals/staleness/composite gates those two
    # pools go through above (an ETF has no fundamentals leg at all — see
    # etf_scoring.py) — it has its own gate (etf_newpick_eligible) and its own
    # allowance (ETF_MAX_PICKS), decided entirely in docs/plans/
    # etf-multi-asset-support.md's "Phase 2b" section. A clean no-op when
    # `etf_candidates` is falsy (None/[]): etf_screened_out stays [] and
    # new_picks is untouched, so every pre-Phase-2b caller is byte-identical.
    etf_screened_out: list[dict] = []
    if etf_candidates:
        _etf_eligible: list[dict] = []
        # Case-insensitive on BOTH sides -- _act_blocked/sold_today are
        # expected to already be uppercase from their real producers
        # (decision_bucket.all_flagged_tickers / build_daily_briefing's own
        # `.upper()` construction of _sold_today), but a defensive uppercase
        # here means a stray lowercase entry on either side can never slip a
        # just-sold/already-flagged ticker past this check.
        _act_blocked_upper = {str(x).upper() for x in _act_blocked}
        # Sold-today-then-sibling-reopens guard: resolve_etf_candidates() only
        # sees the CURRENT holdings set (computed by the caller before today's
        # actions), so a group member sold/acted-on today already drops out of
        # "held" there -- meaning its untouched siblings would sail through as
        # normal candidates rather than D-G held_group screen-outs, even
        # though the position just closed today (the exact same-day
        # wash-sale-adjacent pattern `sold_today` exists to prevent
        # elsewhere). Build a group -> acted-ticker map from THIS pool so the
        # loop below can screen out the rest of a group the instant any
        # member is act-blocked, without re-deriving group membership.
        _etf_act_blocked_group_ticker: dict = {}
        for _ec0 in etf_candidates:
            _t0 = str(_ec0.get("ticker", "")).strip().upper()
            if not _t0 or _t0 not in _act_blocked_upper or _ec0.get("kind") == "held_group":
                continue
            _g0 = _ec0.get("group")
            if _g0 and _g0 not in _etf_act_blocked_group_ticker:
                _etf_act_blocked_group_ticker[_g0] = _t0
        for _ec in etf_candidates:
            _et = str(_ec.get("ticker", "")).strip().upper()
            if not _et:
                continue

            # [D-G] Already-held-group screen-out — emitted directly by the
            # caller's resolve_etf_candidates(); no further checks apply.
            if _ec.get("kind") == "held_group":
                etf_screened_out.append({
                    "ticker": _et,
                    "group":  _ec.get("group"),
                    "kind":   "held_group",
                    "reason": _ec.get("reason", ""),
                })
                continue

            # Same Act-Today cross-reference block every stock candidate
            # already respects — silent skip, matching the stock path.
            if _et in _act_blocked_upper:
                continue

            # Sold-today sibling screen-out (see map build above) — a
            # not-yet-blocked ticker whose group sibling WAS acted on today.
            _etf_act_group = _ec.get("group")
            if _etf_act_group in _etf_act_blocked_group_ticker:
                etf_screened_out.append({
                    "ticker": _et,
                    "group":  _etf_act_group,
                    "kind":   "held_group",
                    "reason": (
                        f"{_etf_act_blocked_group_ticker[_etf_act_group]} was acted on "
                        f"today (same {_etf_act_group} exposure)"
                    ),
                })
                continue

            _ebundle = _ec.get("bundle")
            _eligible, _why = etf_candidates_mod.etf_newpick_eligible(_ebundle, tone)
            if not _eligible:
                # Suppress the trivial "bull days only" noise on every
                # flat/down day — on those tones EVERY candidate fails check
                # 8 of etf_newpick_eligible trivially, which would spam a
                # useless reason into the UI daily. Other reasons (macro/AUM/
                # composite/staleness) still surface, but only on a bull day
                # (the only tone this feature ever actually fires in).
                if tone == "bull" and _why != "bull days only":
                    etf_screened_out.append({
                        "ticker": _et,
                        "group":  _ec.get("group"),
                        "kind":   "ineligible",
                        "reason": _why,
                    })
                continue

            # [D-M] Macro gate — decided on SET MEMBERSHIP, never on whether
            # the reason text is truthy (fails CLOSED, unlike the stock
            # path's own `if _macro_block:` check above).
            _macro_reason = etf_candidates_mod.etf_macro_block_reason(
                _macro_blocked_sectors, _macro_block_reasons,
            )
            if _macro_reason:
                # Deliberately NOT added to macro_blocked_picks (gate_ledger's
                # G-07 lane): an ETF's own alpha is ~0 by construction against
                # itself and would corrupt that gate's evaluable-verdict math.
                etf_screened_out.append({
                    "ticker": _et,
                    "group":  _ec.get("group"),
                    "kind":   "macro",
                    "reason": _macro_reason,
                })
                continue

            # TRAP B: never let the cross-reference step read the bundle's
            # own `rec`/`total` directly — for an ETF bundle those are the
            # FABRICATED equity composite (same bug class Phase 2 already
            # fixed elsewhere), not the real `etf_rec`/`etf_total`. Build a
            # small local SHADOW dict and pass THAT instead; the original
            # bundle dict is never mutated (Phase 2's established
            # anti-mutation design — tests pin this with a deep-copy
            # comparison).
            _etf_total = _ebundle.get("etf_total")
            # Explicit is-None check (not `... or {}`) -- etf_newpick_eligible
            # already confirmed etf_total is a real finite value above, and
            # bundle_loader only ever produces a None etf_rec when etf_total
            # is itself None, so this is a defensive fallback, not a real
            # sentinel collapse; written this way anyway to read unambiguously
            # and stay clear of the OFFLINE_SENTINEL_COLLAPSE antipattern shape.
            _etf_rec = _ebundle.get("etf_rec")
            if _etf_rec is None:
                _etf_rec = {}
            # The synthetic scanner_row's "Score" feeds reconcile_signals()'s
            # own `momentum_score` parameter, which — unlike the visible
            # "Momentum" line (omitted via scanner_row_is_synthetic=True) —
            # is NOT gated on momentum_available: it still drives the Signals
            # Disagree branch. Using the real etf_total (already confirmed
            # >= COMPOSITE_STRONG_BUY by etf_newpick_eligible above) rather
            # than a literal 0 keeps that branch live without fabricating a
            # visible momentum reading. The negative-news skip no longer
            # depends on momentum_score at all, so it still vetoes this pick
            # on a bad-news day either way.
            _shadow_composites = {_et: {"rec": _etf_rec, "total": _etf_total}}
            _etf_xref = _cross_reference(
                _et, {"Ticker": _et, "Score": _f(_etf_total, 0), "Signal": ""}, port_df, news_items,
                held_data, today, earnings_lookup=earnings_lookup,
                composites=_shadow_composites, is_mover=False,
                scanner_row_is_synthetic=True,
            )
            # Defensive backstop only — should never fire given etf_total
            # already cleared COMPOSITE_STRONG_BUY in etf_newpick_eligible,
            # but left in rather than removed per the stock path's own
            # precedent for this exact check.
            if _etf_xref["verdict"] == "conflicted":
                continue

            # F-302 — same reconciled-skip hold-out as the stock lane, so
            # negative news vetoes an ETF pick; without this the card read
            # "Skip" while the pick was still sized into new_picks. Routed into this lane's own
            # visible screen-out bucket, like its macro/AUM/held-group reasons.
            _etf_rv = _etf_xref.get("verdict_reconciled")
            if isinstance(_etf_rv, dict) and _etf_rv.get("verdict") == "skip":
                etf_screened_out.append({
                    "ticker": _et,
                    "group":  _ec.get("group"),
                    "kind":   "skip_verdict",
                    # Label only: this lane calls reconcile_signals with
                    # momentum_available=False, whose one-liner is add-to-
                    # winner wording ("Hold off adding") -- wrong for a
                    # not-held ETF.
                    "reason": str(_etf_rv.get("label") or "❌ Skip"),
                })
                continue
            # F-303 -- same earnings-wait hold-out as the stock lane.
            if isinstance(_etf_rv, dict) and _etf_rv.get("verdict") == "caution":
                etf_screened_out.append({
                    "ticker": _et,
                    "group":  _ec.get("group"),
                    "kind":   "earnings_wait",
                    "reason": str(_etf_rv.get("label") or "⚠️ Caution"),
                })
                continue

            # reconcile_signals()'s own momentum_available=False "go" wording
            # ("...there is no separate scanner momentum reading for a
            # position you already hold") is correct for the add-to-winner
            # path it was written for, but wrong here: this is a NOT-HELD new
            # ETF pick, not an add to an existing position. Overwrite with
            # ETF-appropriate wording for the branch this pool actually
            # reaches in practice (composite already cleared Strong Buy, no
            # earnings concept for a fund, so "go" is the realistic outcome
            # unless negative news or an imminent macro event intervenes —
            # both already screened above or still correctly worded by
            # reconcile_signals itself for the "skip"/"caution" tiers).
            _etf_reconciled = _etf_xref.get("verdict_reconciled")
            if isinstance(_etf_reconciled, dict) and _etf_reconciled.get("verdict") == "go":
                _etf_reconciled["one_liner"] = (
                    f"ETF composite {_f(_etf_total, 0):.0f} ({_etf_rec.get('label', '')}) "
                    "cleared the Strong Buy bar — "
                    f"{_etf_rec.get('rationale') or 'technical + cost checks confirm this entry'}."
                )

            # Sizing — same ATR-stop/entry-zone/_position_size_for_render
            # sequence the stock new-pick path uses above (price derived from
            # the SAME bundle as atr/stop here, since an ETF candidate has no
            # separate live scanner-row quote to diverge from — but the same
            # ATR-derivation-with-fallback shape is kept for consistency and
            # so an ATR-unavailable ETF still degrades to the bundle's own
            # last-close stop/entry-zone rather than silently sizing nothing).
            _etf_price = _f(_ebundle.get("current_price"))
            _etf_atr   = _f(_ebundle.get("atr"))
            _etf_atr_stop = (
                round(_etf_price - ATR_STOP_MULT * _etf_atr, 2) if _etf_atr > 0 else 0.0
            )
            _etf_stop = _etf_atr_stop if _etf_atr_stop > 0 else _f(_ebundle.get("stop"))
            _etf_lo, _etf_hi = (
                entry_zone(_etf_price, _etf_atr) if _etf_atr > 0
                else (_ebundle.get("entry_lo"), _ebundle.get("entry_hi"))
            )
            _etf_sizing = (
                _position_size_for_render(
                    portfolio_value, _etf_price, _etf_stop, _etf_lo, _etf_hi,
                    net_capital=net_capital,
                )
                if _etf_price > 0 and portfolio_value > 0 else {}
            )
            _etf_det = exit_advisor.candidate_deterioration_flag(
                _et, _ebundle.get("df"), spy_df, price=_etf_price, atr=_etf_atr,
            )
            _etf_det_warning = exit_advisor.candidate_deterioration_caption(
                _etf_det, _et, verdict_phrase="a buy",
            )
            _g32_pick_src.setdefault(str(_et).upper(), (_ebundle, _etf_price, _etf_stop))
            _etf_label = str(_etf_rec.get("label", ""))
            # Derived, not hardcoded -- etf_newpick_eligible's own bar is
            # COMPOSITE_STRONG_BUY, which today equals COMPOSITE_HIGH_
            # CONVICTION, so this is always "high" in practice, but deriving
            # it from the real constant (same comparison the stock path uses
            # just above) prevents silent drift if either threshold ever
            # moves independently of the other.
            _etf_conviction = "high" if _f(_etf_total, 0) >= COMPOSITE_HIGH_CONVICTION else "moderate"
            _etf_eligible.append({
                "ticker":               _et,
                # No independent momentum/scanner score exists for a
                # registry-sourced ETF candidate -- None (not 0.0) so a
                # renderer can tell "no momentum reading" from "scored a
                # literal zero" and skip the Momentum badge rather than
                # printing a fabricated "Momentum 0/100".
                "score":                None,
                "composite_score":      _etf_total,
                "composite_label":      _etf_label,
                "composite_fetched_at": _ebundle.get("fetched_at"),
                "conviction":           _etf_conviction,
                "sector":               UNCLASSIFIED_SECTOR,   # truthfully — never a fabricated sector
                "price":                _etf_price,
                "trend":                "",
                "scanner_signal":       "",
                "is_leader":            False,
                "is_mover":             False,
                "day_change":           None,
                "thesis": (
                    f"ETF composite {_etf_total:.1f} ({_etf_label}) — "
                    f"{_etf_rec.get('rationale', '')}"
                ),
                "sizing":               _etf_sizing,
                "xref":                 _etf_xref,
                "divergence":           None,   # momentum/composite divergence concept doesn't apply here
                "sector_elevated_warning": None,   # "Other" is excluded from the elevated-sector map by construction
                "deterioration_warning": _etf_det_warning,
                "asset_type":           asset_type.ASSET_TYPE_ETF,
                "etf_group":            _ec.get("group"),
                # An ETF has no "winner profile" match concept (the profile is
                # built from realized STOCK entries) — None/None/UNCLASSIFIED_
                # SECTOR makes score_candidate_match() a guaranteed no-op
                # (every comparison branch requires a non-None value or a
                # real, non-UNCLASSIFIED sector) rather than reusing this
                # pick's own real composite/momentum, which could coincidentally
                # land inside a profile band and imply a match that has no
                # actual conceptual basis.
                "personalized_match": score_candidate_match(None, None, UNCLASSIFIED_SECTOR, winner_profile),
            })

        # Deterministic tie-break — highest composite first, ticker
        # alphabetical on exact ties (VOO/IVV routinely score identically).
        _etf_eligible.sort(key=lambda d: (-_f(d.get("composite_score"), 0), d["ticker"]))
        _etf_already = {p["ticker"] for p in new_picks}
        _etf_picks_made = 0
        for _ep in _etf_eligible:
            if _ep["ticker"] in _etf_already:
                continue
            if _etf_picks_made >= ETF_MAX_PICKS:
                break
            new_picks.append(_ep)
            _etf_already.add(_ep["ticker"])
            _etf_picks_made += 1

    # Add-to-winner: held Strong Buy, Score ≥ COMPOSITE_BUY (65), Gap ≥ 8%
    # — only on bull days. Composite bar aligned with new-pick threshold
    # (was 68; raised the bar on adds and lowered the bar on new picks created
    # an asymmetric system where it was easier to start a position than to add
    # to one you'd already vetted — backwards from "press your winners.")
    add_positions:     list[dict] = []
    risk_blocked_adds: list[dict] = []
    concentration_blocked_adds: list[dict] = []
    sector_blocked_adds: list[dict] = []   # adds suppressed — sector over hard cap
    cooldown_adds:     list[dict] = []     # adds suppressed — recently added (post-act cooldown)
    deterioration_blocked_adds: list[dict] = []  # adds suppressed — active early-deterioration WATCH
    cluster_blocked_adds: list[dict] = []  # adds suppressed — new correlation cluster (G-25)
    pair_blocked_adds: list[dict] = []  # adds suppressed — standing danger-tier correlated pair (G-26)
    card_blocked_adds: list[dict] = []  # adds paused by a Brief card / sale today (chunk 7a, no ledger row)
    entry_held_adds:   list[dict] = []  # adds held by the shared entry check (stale / news / earnings)
    room_blocked_adds: list[dict] = []  # adds with no room left under the whole-position caps (G-31, S-D)

    def _review_news_note(_t: str):
        """Note for an add card whose ticker has a Review news WATCH (which no
        longer pauses the add). None when there is no such card."""
        _c = _review_news_watch.get(_t.upper())
        if _c is None:
            return None
        _nc = entry_eligibility.news_ctx_from_items(
            [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == _t.upper()])
        return {
            "headline":       _c.get("headline"),
            "n_negative":     _c.get("news_n_negative"),
            "window_label":   _c.get("news_window_label"),
            "news_headline":  _c.get("news_headline"),
            "worst_compound": _c.get("news_compound"),
            "news_avg":       None if _nc is None else _nc["compound"],
            "news_n":         None if _nc is None else _nc["n"],
        }
    # Deterioration WATCH — SUPPRESS add-to-winner (see docstring; changed
    # 2026-07-21 from annotate-only). `_watch_by_ticker` is built above, ahead
    # of the bear early return, because adds_paused reads it too.
    # Pre-populate deterioration_blocked_adds for ALL held WATCH names on every
    # non-bear day (bear returns above this point and carries no such key).
    # In bull mode the inner loop below also catches them (and continues past add);
    # on a flat day the inner loop is skipped, so this pre-pass is what still
    # writes the flat-day G-20 ledger rows (counterfactual=False) and feeds
    # the app.py dedup. (Before 2026-10-15 it also kept WATCH names out of the
    # retired scanner buy-candidates lane -- the FSLR Protect-mode repro, 2026-07-23.)
    if port_df is not None:
        for _, _wr in port_df.iterrows():
            _wt = str(_wr["Ticker"]).upper()
            _dw_pre = _watch_by_ticker.get(_wt)
            if _dw_pre and not any(x["ticker"].upper() == _wt for x in deterioration_blocked_adds):
                deterioration_blocked_adds.append({
                    "ticker":  str(_wr["Ticker"]),
                    "score":   _f(_wr.get("Score"), 0),
                    "pnl_pct": _f(_wr.get("P&L (%)")),
                    "gap":     _f(_wr.get("Gap to Stop (%)"), 0),
                    "reason":  (
                        f"Down {_dw_pre['dd_from_peak_pct']:.1f}% from its "
                        f"${_dw_pre['peak']:.2f} peak, below SMA{_dw_pre['trend_ma']} — "
                        "early deterioration Watch. Don't add to a weakening name."
                    ),
                    "gate_id":        "G-20",
                    "counterfactual": False,
                    "gate_value":     _dw_pre["dd_from_peak_pct"],
                    "gate_threshold": None,
                    "price":          _f(_wr.get("Price"), None),
                    "composite_score": _f(_wr.get("Score"), None),
                    "momentum_score":  None,
                })
    if tone == "bull" and port_df is not None:
        for _, row in port_df.iterrows():
            sig = str(row.get("Signal", ""))
            gap = _f(row.get("Gap to Stop (%)"), 0)
            scr = _f(row.get("Score"), 0)
            if _add_entry_ok(row):
                ticker  = str(row["Ticker"])
                sector  = str(row.get("Sector", ""))
                _gate = _add_block_gate(row)
                # A card in today's Brief (Act Today / Review, not a news WATCH)
                # or a sale today pauses the add. Recorded -- never silent --
                # but with no gate id and no ledger row (the card itself is the
                # disclosure; chunk 7a).
                if _gate == "CARD":
                    # A name already in the G-20 bucket keeps that render + ledger
                    # row; never list it in two places.
                    if any(x["ticker"].upper() == ticker.upper()
                           for x in deterioration_blocked_adds):
                        continue
                    _cl = _add_cards.get(ticker.upper(), "")
                    card_blocked_adds.append({
                        "ticker":     ticker,
                        "score":      scr,
                        "where":      ("Act Today" if _cl.startswith("Act Today")
                                       else "Monitoring" if _cl.startswith("Monitoring")
                                       else "sold" if _cl == "sold today"
                                       else "Card"),
                        "card_label": _cl,
                    })
                    continue
                # Early-deterioration WATCH — an active WATCH on a held name
                # (below its trend MA, drawing down from its peak) means the app
                # must NOT nudge to ADD, even on a Strong Buy composite. This was
                # previously annotate-only, which read as "add more to a name I'm
                # telling you is weakening" (the FSLR 2026-07-21 case). WATCH is
                # entry-price-agnostic, so this is the principled add gate; the
                # suppression is surfaced (never silent) via deterioration_blocked_adds.
                _dw = _watch_by_ticker.get(ticker.upper())
                if _gate == "G-20":
                    # Pre-pass (above) always inserted this ticker first; in bull
                    # mode upgrade counterfactual to True in-place (A2). Reaching
                    # here proves G-20 is the BINDING constraint — the ticker
                    # cleared _act_blocked + Strong Buy + COMPOSITE_BUY + gap.
                    _dw_existing_idx = next(
                        (i for i, x in enumerate(deterioration_blocked_adds)
                         if x["ticker"].upper() == ticker.upper()), -1
                    )
                    if _dw_existing_idx < 0:
                        # Not yet present (edge: pure bull path, pre-pass skipped).
                        deterioration_blocked_adds.append({
                            "ticker":  ticker,
                            "score":   scr,
                            "pnl_pct": _f(row.get("P&L (%)")),
                            "gap":     gap,
                            "reason":  (
                                f"Down {_dw['dd_from_peak_pct']:.1f}% from its "
                                f"${_dw['peak']:.2f} peak, below SMA{_dw['trend_ma']} — "
                                "early deterioration Watch. Don't add to a weakening name."
                            ),
                            "gate_id":        "G-20",
                            "counterfactual": True,
                            "gate_value":     _dw["dd_from_peak_pct"],
                            "gate_threshold": None,
                            "price":          _f(row.get("Price"), None),
                            "composite_score": _f(row.get("Score"), None),
                            "momentum_score":  None,
                        })
                    else:
                        # Already present from pre-pass — upgrade to binding (A2).
                        deterioration_blocked_adds[_dw_existing_idx]["counterfactual"] = True
                        if deterioration_blocked_adds[_dw_existing_idx].get("gate_value") is None:
                            deterioration_blocked_adds[_dw_existing_idx]["gate_value"] = (
                                _dw["dd_from_peak_pct"]
                            )
                    continue
                # Post-add cooldown — you already acted on this add recently; let
                # the new shares settle before nudging to add more (anti-churn).
                if _gate == "G-24":
                    _dslb = ((held_data or {}).get(ticker) or {}).get("days_since_last_buy")
                    cooldown_adds.append({
                        "ticker":  ticker,
                        "score":   scr,
                        "pnl_pct": _f(row.get("P&L (%)")),
                        "days_since_last_buy": _dslb,
                        "reason":  (
                            f"Added within the last {ADD_WINNER_COOLDOWN_DAYS} days "
                            f"({_dslb}d ago) — letting the new shares settle before "
                            "suggesting more."
                        ),
                        "gate_id":        "G-24",
                        "counterfactual": True,
                        "gate_value":     _dslb,
                        "gate_threshold": ADD_WINNER_COOLDOWN_DAYS,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # Sector concentration gate — don't add to a position whose sector
                # is over the hard cap (Risk Advisor is recommending a trim there).
                if _gate == "G-16":
                    sector_blocked_adds.append({
                        "ticker":  ticker,
                        "sector":  sector,
                        "weight":  _f(row.get("Weight (%)"), 0),
                        "score":   scr,
                        "pnl_pct": _f(row.get("P&L (%)")),
                        "gap":     gap,
                        "reason":  (
                            f"{sector} sector ≥ {SECTOR_CEILING:.0f}% hard cap — "
                            "trim the sector before adding. A Strong Buy here is a "
                            "KEEP, not an add."
                        ),
                        "gate_id":        "G-16",
                        "counterfactual": True,
                        "gate_value":     _sector_wt_map.get(sector),
                        "gate_threshold": SECTOR_CEILING,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # Skip — and record — if Risk Advisor is recommending trim on this ticker
                _trim_info = _trim_set.get(ticker.upper())
                if _gate == "G-01":
                    risk_blocked_adds.append({
                        "ticker":   ticker,
                        "reason":   _trim_info["reason"],
                        "title":    _trim_info["title"],
                        "priority": _trim_info["priority"],
                        "score":    scr,
                        "pnl_pct":  _f(row.get("P&L (%)")),
                        "gap":      gap,
                        "gate_id":        "G-01",
                        "counterfactual": True,
                        "gate_value":     None,
                        "gate_threshold": None,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # Single-name ceiling — hard suppress if current weight ≥ ceiling.
                # No matter how strong the signal, additional concentration here
                # creates asymmetric idiosyncratic risk that the institutional
                # framework caps at SINGLE_NAME_CEILING.
                _cur_wt = _gate_wt(row)
                if _gate == "G-04":
                    concentration_blocked_adds.append({
                        "ticker":    ticker,
                        "weight":    _cur_wt,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "reason":    (
                            f"Position already at {_cur_wt:.1f}% of portfolio "
                            f"(≥ {SINGLE_NAME_CEILING:.0f}% single-name ceiling). "
                            "Trim to target before adding."
                        ),
                        "gate_id":        "G-04",
                        "counterfactual": True,
                        "gate_value":     _cur_wt,
                        "gate_threshold": SINGLE_NAME_CEILING,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # Drift-trim conflict — Rebalancer would trim this position to
                # target; the briefing must not simultaneously recommend adding.
                if _gate == "G-09":
                    concentration_blocked_adds.append({
                        "ticker":    ticker,
                        "weight":    _cur_wt,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "reason":    (
                            f"Position at {_cur_wt:.1f}% — overweight vs equal-weight target "
                            f"(would be flagged for drift-trim by Rebalancer). "
                            "Don't add to a position you'd trim."
                        ),
                        "gate_id":        "G-09",
                        "counterfactual": True,
                        "gate_value":     _cur_wt,
                        "gate_threshold": None,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # G-26 — standing danger-tier correlated pair
                # (CORR_DANGER_PAIRS_THRESHOLD), owner decisions ratified
                # 2026-10-08. Checked BEFORE G-25: a pair that is BOTH a new
                # cluster pairing AND already at danger tier records here,
                # never under G-25. UNLIKE G-25, there is no acknowledge/
                # override — this pause persists for as long as the pair
                # stays at danger tier and both names are held; trimming the
                # weaker name does not lift it on its own. `is not None`,
                # never `or {}` — None means "couldn't check" (fail-open: the
                # add proceeds below, uncaptioned), {} / a miss means
                # "checked, this ticker isn't on a standing danger pair."
                if _gate == "G-26":
                    _pab = pair_add_blocks[ticker.upper()]
                    pair_blocked_adds.append({
                        "ticker":    ticker,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "sector":    sector,
                        "reason":    pair_add_gate.describe_reason(_pab),
                        "gate_id":        "G-26",
                        "counterfactual": True,
                        "gate_value":     _pab.get("max_corr"),
                        "gate_threshold": CORR_DANGER_PAIRS_THRESHOLD,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                        "partners":          _pab.get("partners", []),
                    })
                    continue
                # G-25 — new correlation cluster since the owner's last
                # acknowledged Structural Scan. Checked immediately after G-09,
                # before any other per-candidate computation, per
                # docs/plans/cluster-add-gate.md. `is not None`, never `or {}`
                # — None means "couldn't check" (D4 fail-open: the add
                # proceeds below, uncaptioned), [] / a miss means "checked,
                # this ticker isn't on a new pair's endpoints."
                if _gate == "G-25":
                    _cab = cluster_add_blocks[ticker.upper()]
                    _cab_partners_str = ", ".join(
                        f"{p}({c:+.2f})" for p, c in (_cab.get("partners", []))
                    )
                    cluster_blocked_adds.append({
                        "ticker":    ticker,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "sector":    sector,
                        "reason":    (
                            f"New correlation pairing with {_cab_partners_str} since your last "
                            f"Structural Scan ({_cab.get('baseline_scan_date')}) — add paused "
                            "until you review the cluster. No trim is being recommended."
                        ),
                        "gate_id":        "G-25",
                        "counterfactual": True,
                        "gate_value":     _cab.get("max_new_corr"),
                        "gate_threshold": CORR_HIGH_PAIRS_THRESHOLD,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                        "partners":          _cab.get("partners", []),
                        "baseline_scan_date": _cab.get("baseline_scan_date"),
                    })
                    continue
                # ENTRY (chunk 7a) -- the shared entry check, LAST so every
                # gate above keeps its bucket and ledger rows. gate_id is G-30
                # ONLY when the binding hold (first in holds) is news; a stale /
                # earnings / check_failed binding hold stays unledgered.
                _entry_v = _entry_verdict(row)
                if _gate == "ENTRY":
                    _eb = _entry_v["binding"] or {}
                    entry_held_adds.append({
                        "ticker":    ticker,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "sector":    sector,
                        "check":     _eb.get("check"),
                        "reason":    _eb.get("reason"),
                        "clears_when": _eb.get("clears_when"),
                        "holds":     list(_entry_v["holds"]),
                        "unchecked": list(_entry_v["unchecked"]),
                        "gate_id":        "G-30" if _eb.get("check") == "news" else None,
                        "counterfactual": True,
                        "gate_value":     _eb.get("gate_value"),
                        "gate_threshold": _eb.get("gate_threshold"),
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                price   = _f(row.get("Price", 0))
                # Same alias-aware match as _sector_bonus/is_leader above --
                # this is the add-to-winner lane's own copy of the identical
                # first-key-wins-only bug (pre-fix), reused rather than
                # re-duplicated so the two lanes can't silently diverge again.
                is_lead = bool(_sector_bonus(sector))
                # Sizing — source the ATR stop from held_data (same bundle shape as
                # composites). If no stop is available, degrade to {} — never invent
                # or estimate a stop (spec item 2).
                # NOTE: the add lane deliberately stays on the bundle's
                # last-close ATR stop rather than re-deriving against the live
                # price as the new-pick lane does. Safe because this branch is
                # already gated on gap >= ADD_WINNER_MIN_GAP_PCT measured from
                # the RATCHETED stop (>= the ATR stop), so price > stop holds by
                # construction. Don't "unify the lanes" without re-reading that
                # gate; and per memory project_stop_ladder_and_display the ATR
                # basis here is a policy decision (the ratchet stop would sit
                # higher, suggest MORE shares, and over-buy into strength).
                _held_bundle = (held_data or {}).get(ticker, {})
                _held_stop = _f(_held_bundle.get("stop"))
                # S-D (owner decision D1): the caps bind on the WHOLE position,
                # so the holding's own market value goes in. A missing/NaN value
                # is passed as NaN (-> holding_unknown, no size), NEVER as 0.0,
                # which would size a held name as a fresh one.
                _existing_mv = _f(row.get("Market Value"), None)
                sizing = (
                    _position_size_for_render(
                        portfolio_value, price, _held_stop,
                        _held_bundle.get("entry_lo"), _held_bundle.get("entry_hi"),
                        net_capital=net_capital,
                        existing_value=(float("nan") if _existing_mv is None else _existing_mv),
                    )
                    if price > 0 and portfolio_value > 0 else {}
                )
                # Room check LAST (after ENTRY and sizing) so every earlier gate
                # keeps its own bucket and ledger row.
                if sizing.get("room_infeasible"):
                    _rb = sizing.get("room_binding")
                    if _rb == "capital":
                        _now_p = sizing.get("existing_capital_pct")
                        _basis_txt, _cap_c = "of your net capital", NET_CAPITAL_POSITION_CAP_PCT
                    else:
                        _now_p = sizing.get("existing_gross_pct")
                        _basis_txt, _cap_c = "of your book", SINGLE_NAME_CEILING
                    room_blocked_adds.append({
                        "ticker":    ticker,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "sector":    sector,
                        "weight":    _cur_wt,
                        "reason":    (
                            f"You already hold {_now_p:.1f}% {_basis_txt} "
                            f"(cap {_cap_c:.0f}%) — one more share would make it "
                            f"{sizing['one_more_pct']:.1f}%. "
                            "No add while the whole position is at the cap."
                        ),
                        "room_binding":         _rb,
                        "existing_gross_pct":   sizing.get("existing_gross_pct"),
                        "existing_capital_pct": sizing.get("existing_capital_pct"),
                        "gate_id":        "G-31",
                        "counterfactual": True,
                        "gate_value":     _now_p,
                        "gate_threshold": _cap_c,
                        "price":          _f(row.get("Price"), None),
                        "composite_score": _f(row.get("Score"), None),
                        "momentum_score":  None,
                    })
                    continue
                # Honest P&L framing — the composite is entry-price-agnostic, so a
                # Strong Buy add candidate can be up OR down vs the user's entry.
                # State the real P&L with a single correct sign (the old copy hard-
                # coded "Already profitable (+{:+.1f})", printing a false "+-10.8%"
                # on a losing position). (Change 3, FSLR 2026-07-21.)
                _pnl_v = _f(row.get("P&L (%)"))
                _pnl_phrase = (
                    f"Up {_pnl_v:.1f}% vs entry" if _pnl_v > 0
                    else f"Down {abs(_pnl_v):.1f}% vs entry" if _pnl_v < 0
                    else "At breakeven vs entry"
                )
                _g32_add_src[ticker.upper()] = (
                    (held_data or {}).get(ticker), price,
                    _held_stop if _held_stop > 0 else None)
                add_positions.append({
                    "ticker":    ticker,
                    "score":     scr,
                    "signal":    sig,
                    "pnl_pct":   _pnl_v,
                    "gap":       gap,
                    "sector":    sector,
                    "is_leader": is_lead,
                    "thesis":    (
                        f"{_pnl_phrase}, {gap:.1f}% above stop — Strong Buy, "
                        "adds within existing position."
                        + (" Sector leading today." if is_lead else "")
                    ),
                    "sizing":    sizing,
                    # Elevated (not hard-capped) sector warning — see _elevated_sectors.
                    "sector_elevated_warning": (
                        f"{sector} sector already at {_sector_wt_map.get(sector, 0):.1f}% "
                        f"(warn level {SECTOR_ELEVATED:.0f}%) — this add adds to a sector "
                        "Risk Advisor already has flagged for trim."
                        if sector in _elevated_sectors else None
                    ),
                    # Chunk 7a: a Review news WATCH no longer pauses the add --
                    # the add card says so. {headline, n_negative, window_label,
                    # news_headline, worst_compound, news_avg, news_n} or None (no such card).
                    "review_news_note": _review_news_note(ticker),
                    # What the entry check cleared / couldn't run, so the
                    # "News checked" line can render on every add card.
                    "entry_passed":    list(_entry_v["passed"]),
                    "entry_unchecked": list(_entry_v["unchecked"]),
                })
        add_positions.sort(key=lambda x: (-x["score"], -x["gap"]))

    # G-32 C2 shadow: measure-only, read by cron_runner's g32_shadow capture; never routes.
    # Runs after every routing decision; a failure here can't touch an item or the lists.
    for _g32_items, _g32_src in ((new_picks, _g32_pick_src),
                                 (add_positions, _g32_add_src),
                                 (overflow_picks, _g32_pick_src)):
        for _g32_it in _g32_items:
            try:
                _g32_b, _g32_e, _g32_s = _g32_src.get(
                    str(_g32_it.get("ticker", "")).upper(), (None, None, None))
                _g32_it["entry_rr"] = g32_shadow.shadow_measure(_g32_b, _g32_e, _g32_s)
            except Exception:
                try:
                    _g32_it["entry_rr"] = {"rr": None, "state": "unknown", "why": "no_bundle"}
                except Exception:
                    pass

    return {
        "tone":                       tone,
        "message":                    None,
        "new_picks":                  new_picks,
        "add_positions":              add_positions,
        "risk_blocked_adds":          risk_blocked_adds,
        "concentration_blocked_adds": concentration_blocked_adds,
        "sector_blocked_adds":        sector_blocked_adds,
        "cooldown_adds":              cooldown_adds,
        "deterioration_blocked_adds": deterioration_blocked_adds,
        # Chunk 7a: CARD (Brief card / sold today; no gate id, no ledger row)
        # and ENTRY (shared entry check) add pauses -- always present.
        "card_blocked_adds":          card_blocked_adds,
        "entry_held_adds":            entry_held_adds,
        # G-31 (S-D) -- no room left under the whole-position caps.
        "room_blocked_adds":          room_blocked_adds,
        # G-25 — see cluster_add_blocks docstring above. cluster_gate_checked
        # is True only when cluster_add_blocks was actually resolved this run
        # (not None) — lets the render layer distinguish "checked, nothing
        # blocked" from "couldn't check" (D4 fail-open).
        "cluster_blocked_adds":       cluster_blocked_adds,
        "cluster_gate_checked":       cluster_add_blocks is not None,
        # G-26 — see pair_add_blocks docstring above. pair_gate_checked is
        # True only when pair_add_blocks was actually resolved this run (not
        # None).
        "pair_blocked_adds":         pair_blocked_adds,
        "pair_gate_checked":         pair_add_blocks is not None,
        # Disclosure-only follow-on — passthrough, unchanged, never read in
        # any suppression decision above.
        "corr_unchecked":            corr_unchecked,
        "sector_blocked_picks":       sector_blocked_picks,
        "sector_unknown_picks":       sector_unknown_picks,
        "macro_blocked_picks":        macro_blocked_picks,
        "composite_skipped":          composite_skipped,
        "composite_unavailable":      composite_unavailable,
        "news_skipped":               news_skipped,
        "earnings_waiting":           earnings_waiting,
        "etf_screened_out":           etf_screened_out,
        # Grow Today owns every buy (2026-10-15, OP-07 "gate the call, never
        # the candidate"). None of these four is a gate: overflow_picks are
        # pacing-held (no size), flat_day_held names the flat-day holds,
        # unevaluated_leads carry NO verdict, adds_paused is non-bull only.
        "overflow_picks":             overflow_picks,
        "flat_day_held":              flat_day_held,
        "unevaluated_leads":          unevaluated_leads,
        "adds_paused":                [] if tone == "bull" else _adds_paused_list(),
        "risk_banner":                risk_banner,
        "sp500_pct":                  sp500_pct,
        "nasdaq_pct":                 nasdaq_pct,
        "leading_sectors":            lead_secs,
        # LATE construction: built after every suppression decision so the G-07
        # macro gate above can never read this value.  None means "could not
        # verify"; [] means "verified, nothing expired".  Never gates a pick.
        "macro_coverage_expired":     _expired_macro_series(today),
    }


# ── Act Today ─────────────────────────────────────────────────────────────────

def deterioration_signals(
    port_df, held_data, spy_df=None, *, split_flagged: set[str] | None = None,
) -> list[dict]:
    """Held-position deterioration signals (exit_advisor) for every holding.

    Returns the list of non-None payloads (tier WATCH/TRIM/EXIT) — TRIM/EXIT feed
    Act Today, WATCH feeds the Review awareness lane. Pure pass-through to
    exit_advisor.assess_holding; all inputs come from data the brief already has
    (port_df row + held_data[t]'s df/atr/position_age_days + the SPY benchmark).

    `split_flagged` (finding D1): tickers whose stored `avg_cost` is currently
    unreliable (an unaccounted stock split, per split_detector.detect_split_
    adjustment). classify_deterioration_tier's `escalate` leg reads price vs
    avg_cost directly (`exit_advisor.py`) — a forward split can escalate an
    otherwise-legitimate, price-history-real TRIM into an unwarranted EXIT
    without any actual deterioration. ALL tiers are withheld for a flagged
    ticker, not only EXIT: every dollar figure a directive would render
    (P&L, dollar-risk) is built on the same uncorrected avg_cost, so even a
    split-safe WATCH/TRIM would still print wrong numbers. Owner-confirmed
    tradeoff (2026-09-13) — this can suppress a genuinely real signal on that
    one ticker until the split is corrected on 🏠 Home; the caller MUST
    disclose this (split_detector.split_withheld_message), never drop it
    silently. Default `None` (empty set) reproduces today's behaviour
    exactly, so every other caller is unaffected.
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    flagged = split_flagged or set()
    out: list[dict] = []
    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "")).upper()
        if ticker in flagged:
            continue
        data = (held_data or {}).get(ticker, {}) or {}
        payload = exit_advisor.assess_holding(
            ticker,
            data.get("df"),
            spy_df,
            price=_f(row.get("Price"), None),
            atr=data.get("atr"),
            avg_cost=_f(row.get("Avg Cost"), None),
            shares=_f(row.get("Shares"), 0),
            pnl_pct=_f(row.get("P&L (%)"), None),
            weight_pct=_f(row.get("Weight (%)"), None),
            age_days=data.get("position_age_days"),
            # Material-add re-anchor (Phase 1.1): when the user averaged in
            # materially, clip the peak window to "since the add" so a stale
            # pre-add high can't fabricate a false EXIT. None → spans the whole
            # holding (oldest lot), as before.
            peak_window_days=data.get("material_add_age_days"),
        )
        if payload:
            out.append(payload)
    return out


def _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today,
               deterioration: list | None = None,
               premortem_triggers: list | None = None,
               news_window_start_ts: float | None = None,
               news_window_label: str | None = None) -> list[dict]:
    """
    Build the Act Today list. Each item carries a structured directive so the
    UI renders a concrete ACT/Why/Trigger block (matching Review Before Close)
    rather than a prose one-liner.

    item shape:
      priority  : "critical" | "high"
      icon, ticker, action (headline)
      kind      : "stop_breach" | "sell_signal" | "critical_news" | "macro" | "risk"
      directive : the concrete action line (what to do)
      why       : 1-line rationale
      trigger   : escalation / confirmation condition
      risk_flags: [{title, recommendation, problem}] — populated for kind="risk";
                  multiple flags on one ticker are merged into a single card
      weight, pnl_pct

    After building the flat list, items are CONSOLIDATED per-ticker so a ticker
    never appears in more than one card. Rule (user-chosen): a mechanical exit
    (stop breach / Sell signal) WINS — it suppresses any risk-advisor trim on
    the same ticker, because if you're exiting, a "trim 50% for beta" rec is
    moot. Multiple risk flags on one non-exiting ticker merge into one card.
    """
    items: list[dict] = []

    # 1 — Stop-loss breaches (Gap to Stop ≤ 0)
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if gap <= 0:
            _shares = int(_f(row.get("Shares")))
            items.append({
                "priority": "critical",
                "icon":     "🛑",
                "ticker":   row["Ticker"],
                "kind":     "stop_breach",
                "action":   "SELL — Stop Breached",
                "directive": (
                    f"Sell all {_shares} shares at next open — mechanical stop rule."
                ),
                "why": (
                    f"Price ${_f(row.get('Price')):.2f} closed below the "
                    f"{row.get('Stop Type','')} stop ${_f(row.get('Stop')):.2f} "
                    f"(gap {gap:+.1f}%)."
                ),
                "trigger": "Already breached — this is the exit signal, not a watch.",
                "weight":  _f(row.get("Weight (%)")),
                "pnl_pct": _f(row.get("P&L (%)")),
                "shares":  _shares,
            })

    # 2 — Sell / Avoid signals on held positions
    sell_words = ("Sell", "Avoid", "Weak Hold")
    for _, row in port_df.iterrows():
        sig = str(row.get("Signal", ""))
        if any(w in sig for w in sell_words) and row["Ticker"] not in {i["ticker"] for i in items}:
            items.append({
                "priority": "high",
                "icon":     "📉",
                "ticker":   row["Ticker"],
                "kind":     "sell_signal",
                "action":   f"REVIEW — Signal: {sig}",
                "directive": (
                    f"Reduce or exit — composite signal has shifted to {sig}."
                ),
                "why": (
                    f"Composite score {_f(row.get('Score')):.0f}/100 in the Sell zone. "
                    f"P&L {_f(row.get('P&L (%)')):+.1f}%, weight {_f(row.get('Weight (%)')):.1f}%."
                ),
                "trigger": "Confirm before close; if price is also below stop, exit fully.",
                "weight":  _f(row.get("Weight (%)")),
                "pnl_pct": _f(row.get("P&L (%)")),
            })

    # 2.5 — Held-position deterioration (TRIM / EXIT). Fills the gap between
    # "Hold" and a score-collapse "Sell (<30)": a name down from its peak and
    # below trend, before the composite ever reacts. WATCH tier is awareness-only
    # → it goes to the Review lane, not here. Dedup: a stop_breach or sell_signal
    # already added above wins (single-surface), so skip a ticker already present.
    _existing = {i["ticker"] for i in items}
    for d in (deterioration or []):
        if d.get("tier") not in ("TRIM", "EXIT"):
            continue
        if d["ticker"] in _existing:
            continue
        _is_exit = d["tier"] == "EXIT"
        _pnl = d.get("pnl_pct") or 0.0
        _wt = d.get("weight_pct") or 0.0
        # d["shares"] is int()-truncated upstream (exit_advisor.py) — a genuine
        # sub-1-share holding reports 0, which would otherwise combine with the
        # max(1, ...) floor below to read "1 of 0 shares," or "exit most/all of
        # 0 shares" on the EXIT side. Fall back to a share-count-free phrasing
        # rather than show a self-contradictory quantity on either branch.
        _det_n = _trim_sizing.fraction_trim_shares(d["shares"], DETERIORATION_TRIM_SUGGESTED_PCT)
        if d["shares"] > 0 and _det_n >= 1:
            _trim_qty_clause = f" ({_det_n} of {d['shares']} shares)"
        elif d["shares"] > 0:
            _trim_qty_clause = (
                f" (at {d['shares']} shares a {DETERIORATION_TRIM_SUGGESTED_PCT:.0f}% "
                f"trim is under one share; the smallest real trim is 1 share, "
                f"{100.0 / d['shares']:.0f}% of the position, or exit)"
            )
        else:
            _trim_qty_clause = ""
        _exit_qty_clause = f"of {d['shares']} shares" if d["shares"] > 0 else "of the position"
        items.append({
            "priority": "high",
            "icon":     "📉" if _is_exit else "✂️",
            "ticker":   d["ticker"],
            "kind":     "deterioration_exit" if _is_exit else "deterioration_trim",
            "action":   "REDUCE — Deterioration Exit" if _is_exit else "TRIM — Deterioration",
            "directive": (
                f"Reduce aggressively — exit most/all {_exit_qty_clause}. Down "
                f"{d['dd_from_peak_pct']:.1f}% from its ${d['peak']:.2f} peak and below "
                f"the {d['trend_ma']}-day trend."
                if _is_exit else
                f"Trim into the weakness — an approximate {DETERIORATION_TRIM_SUGGESTED_PCT:.0f}% "
                f"reduction{_trim_qty_clause} is a reasonable floor. Down {d['dd_from_peak_pct']:.1f}% "
                f"from its ${d['peak']:.2f} peak, below the {d['trend_ma']}-day trend, and "
                f"lagging the market."
            ),
            "why": (
                f"Drawdown {d['dd_from_peak_pct']:.1f}% (trigger "
                f"{d['trim_floor']:.0f}%/{d['exit_floor']:.0f}%), {d['below_ma_count']}/3 "
                f"sessions below SMA{d['trend_ma']}, rel-strength {d['rel_strength']:+.1f}pp. "
                f"P&L {_pnl:+.1f}%, weight {_wt:.1f}%."
            ),
            "trigger": "If it keeps closing below the trend and breaks support, exit the rest.",
            "weight":  d.get("weight_pct"),
            "pnl_pct": d.get("pnl_pct"),
            "dollar_risk": d.get("dollar_risk"),
            # Carried forward so the exit-signal capture block (app.py) can
            # persist them to exit_signals — previously dropped here, which
            # meant every deterioration_exit/deterioration_trim row was
            # written with these columns NULL despite exit_advisor.py already
            # computing them.
            "price": d.get("price"),
            "dd_from_peak_pct": d.get("dd_from_peak_pct"),
            "below_ma_count": d.get("below_ma_count"),
            "rel_strength": d.get("rel_strength"),
            # Only meaningful for the EXIT tier — a full-position quantity the
            # directive already names ("exit most/all of N shares"). TRIM's
            # directive text now names an approximate suggested quantity too
            # (DETERIORATION_TRIM_SUGGESTED_PCT), but this structured field
            # stays None for TRIM by design — it's a full-position exit count,
            # not a partial-trim one — so the UI must keep gating any sell-log
            # button on kind == "deterioration_exit", not just this field's presence.
            "shares": d.get("shares") if _is_exit else None,
        })

    # 2.6 — Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md):
    # a fired pre-commitment trigger is a genuinely DIFFERENT reason from the
    # algorithm's own deterioration read above — the investor's own stated
    # condition, not exit_advisor's technical tier — so this section
    # DELIBERATELY does NOT dedupe against tickers already in `items` the way
    # section 2.5 does (2026-08-03 user-confirmed Q4: show both cards on the
    # same ticker, never merge — never a new gate, purely an audit/confront).
    for pmt in (premortem_triggers or []):
        _pmt_ticker = pmt["ticker"]
        _pmt_prow = _port_rows(port_df, _pmt_ticker)
        _pmt_weight = _f(_pmt_prow.iloc[0].get("Weight (%)"), None) if not _pmt_prow.empty else None
        _pmt_pnl    = _f(_pmt_prow.iloc[0].get("P&L (%)"), None) if not _pmt_prow.empty else None
        _pmt_dir_word = "below" if pmt["direction"] == "below" else "above"
        items.append({
            "priority": "high",
            "icon":     "🎯",
            "ticker":   _pmt_ticker,
            "kind":     "premortem_triggered",
            "action":   "YOUR OWN COMMITMENT FIRED",
            "directive": (
                f"You said you'd exit if it broke {_pmt_dir_word} "
                f"${pmt['trigger_price']:.2f}. It happened on "
                f"{pmt['first_breach_date']} — {pmt['days_since']} day(s) ago. "
                f"You're still holding. What's changed?"
            ),
            "why": (
                f"Your Pre-Mortem exit commitment for this position stated a "
                f"{_pmt_dir_word} ${pmt['trigger_price']:.2f} trigger; the price "
                f"is currently ${pmt['current_price']:.2f}."
            ),
            "trigger": (
                "This is your own stated condition, not the algorithm's — "
                "reconsider or explicitly recommit."
            ),
            "weight":  _pmt_weight,
            "pnl_pct": _pmt_pnl,
        })

    # 3 — Critical news on held positions (compound ≤ NEWS_SENTIMENT_CRITICAL, tier ≤ 2,
    #     minimum NEWS_CRITICAL_MIN_HEADLINES qualifying headlines per ticker)
    held_tickers = _held_tickers(port_df)
    if news_window_start_ts is None or news_window_label is None:
        _nw = held_news_check.news_window_start(today)
        news_window_start_ts = _nw.timestamp()
        news_window_label = held_news_check.window_label(_nw)
    _crit_by_ticker = held_news_check.critical_news_by_ticker(
        news_items or [], held_tickers, news_window_start_ts)

    for ticker, _crit_items in _crit_by_ticker.items():
        if ticker not in {i["ticker"] for i in items}:
            pm = _port_rows(port_df, ticker)
            row = pm.iloc[0] if not pm.empty else {}
            _worst = _crit_items[0]
            _n = len(_crit_items)
            items.append({
                "priority": "high",
                "icon":     "🚨",
                "ticker":   ticker,
                "kind":     "critical_news",
                "action":   "Watch — Critical News",
                "directive": (
                    "Hold for now, but tighten your stop and re-evaluate the thesis "
                    "after the news is confirmed."
                ),
                "why": (
                    f"{_n} headline{'s' if _n != 1 else ''} from tier-1/2 sources "
                    f"since {news_window_label} "
                    f"(worst {_worst.get('compound', 0):+.2f}): "
                    f"\"{(_worst.get('title') or _worst.get('headline', 'news alert'))[:80]}\" "
                    "— material, but not a mechanical sell."
                ),
                "trigger": (
                    "Price −3% intraday or further headline deterioration → re-evaluate."
                ),
                "weight":  _f(row.get("Weight (%)") if hasattr(row, "get") else 0),
                "pnl_pct": _f(row.get("P&L (%)") if hasattr(row, "get") else 0),
                "news_headline": str(_worst.get("title") or _worst.get("headline") or "news alert")[:140],
                "news_compound": _f(_worst.get("compound", 0)),
            })

    # 4 — Today's HIGH-impact macro events
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH
    today_macro = [e for e in (macro_events or []) if e.get("date") == today and e.get("impact") == MC_HIGH]
    for ev in today_macro:
        _affected = ", ".join(ev.get("affected_tickers", [])[:5]) or "broad market"
        items.append({
            "priority": "high",
            "icon":     "🌐",
            "ticker":   None,
            "kind":     "macro",
            "action":   f"MACRO — {ev.get('event', 'Economic Event')}",
            "directive": (
                "Hold through the print — no new entries in affected names until it clears."
            ),
            "why": (
                f"{ev.get('category','Economic')} release today. Affected: {_affected}."
            ),
            "trigger": (
                ev.get("playbook_note")
                or "Hawkish surprise → existing stops protect; dovish → reconsider adds."
            ),
            "weight":  None,
            "pnl_pct": None,
            # Raw list + category kept so the outer builder can re-filter "Affected"
            # against tickers already carrying an Act Today reduce call (2026-07-29
            # audit follow-up: this card's own "Affected: GD, CRWD" list otherwise
            # restates names that already have their own SELL/TRIM card, reading
            # like a contradictory second opinion rather than the same call).
            "_affected_all": list(ev.get("affected_tickers", [])),
            "_event_category": ev.get("category", "Economic"),
        })

    # 5 — HIGH-priority risk advisor flags
    for rec in (risk_recs or []):
        if rec.get("priority") != "HIGH":
            continue
        # Slow-moving metric drags (Sharpe/beta/vol/drawdown/tail) are NOT
        # same-day decisions — they go to the Portfolio Tune-up awareness lane
        # (_portfolio_tuneup), not Act Today. (single_name_concentration is
        # MEDIUM + in _TUNEUP_RISK_TYPES, so it never reaches this HIGH loop.)
        if rec.get("type") in _TUNEUP_RISK_TYPES:
            continue
        rt = rec.get("root_tickers", [])
        # risk recs already carry rich directive text in `recommendation`; keep it
        # as a flag entry so multiple flags on one ticker can merge.
        _flag = {
            "title":          rec.get("title", "Risk Alert"),
            "recommendation": rec.get("recommendation", rec.get("problem", "")),
            "problem":        rec.get("problem", ""),
        }
        # Rebalance-plan payload rides ON THE FLAG (not the item top-level) so it
        # survives _consolidate_act_today, which keeps only the primary item's
        # keys but gathers ALL flags across a merged ticker group. The render
        # (app.py _render_act_card) keys off flag["rec_type"].
        if rec.get("type") == "sector_concentration":
            _flag["rec_type"]           = "sector_concentration"
            _flag["trim_candidates"]    = rec.get("trim_candidates", []) or []
            _flag["redeploy_sectors"]   = rec.get("redeploy_sectors", []) or []
            _flag["trim_target_pp"]     = rec.get("trim_target_pp")
            _flag["trim_target_dollar"] = rec.get("trim_target_dollar")
            _flag["trim_target_denom"]  = rec.get("trim_target_denom")
        items.append({
            "priority": "high",
            "icon":     "⚠️",
            "ticker":   rt[0]["ticker"] if rt else None,
            "kind":     "risk",
            "action":   f"RISK — {rec.get('title','Risk Alert')}",
            "risk_flags": [_flag],
            "weight":  None,
            "pnl_pct": None,
        })

    return _consolidate_act_today(items, port_df)


# Kinds that represent a mechanical exit decision — these suppress softer
# advisories (risk trims, news monitors) on the same ticker. Deterioration
# signals are deliberately NOT here: they are the gentler/earlier tripwire and
# get suppressed BY stop_breach/sell_signal (deduped at build time), never the
# other way round.
_MECHANICAL_KINDS = {"stop_breach", "sell_signal"}

# Display ordering within Act Today (lower = higher up). Mirrors the exit
# hierarchy: a breached stop first, then a composite Sell, then the deterioration
# tiers (aggressive EXIT before a TRIM), then everything else.
_KIND_RANK = {
    "stop_breach":         0,
    "sell_signal":         1,
    "deterioration_exit":  2,
    "deterioration_trim":  3,
    "premortem_triggered": 3,  # your own stated exit condition fired — same tier as
                               # deterioration_trim: a real decision due, not a mechanical stop
    "risk_off_derisk":     4,   # lowest-priority reduce (market-wide overlay, not name-specific)
}


def _consolidate_act_today(items: list[dict], port_df) -> list[dict]:
    """Collapse Act Today items so each ticker appears at most once.

    - Ticker-less items (macro) pass through unchanged.
    - `premortem_triggered` items ALSO pass through unchanged despite having a
      ticker (docs/plans/premortem-enforcement.md, user-confirmed Q4): it is a
      genuinely different reason from the algorithm's own reduce/risk read on
      that same ticker, and the merge logic below would otherwise silently
      drop it (picked or not as the ticker's single "primary" card) — the
      exact double-surface bug this exemption exists to prevent.
    - For each remaining ticker: if a mechanical-exit item exists, emit only
      the highest-priority mechanical one (drop risk + news — moot if
      exiting).
    - Otherwise merge: the highest-priority item becomes the card; all
      risk_flags across the group are gathered onto it so a ticker with
      two risk recs (e.g. beta + volatility) renders as one card.
    """
    passthrough = [
        it for it in items
        if not it.get("ticker") or it.get("kind") == "premortem_triggered"
    ]
    by_ticker: dict[str, list[dict]] = {}
    for it in items:
        if it.get("kind") == "premortem_triggered":
            continue  # exempted above — never merged with same-ticker items
        t = it.get("ticker")
        if t:
            by_ticker.setdefault(str(t).upper(), []).append(it)

    _pri_rank = {"critical": 0, "high": 1}
    consolidated: list[dict] = []
    for ticker, group in by_ticker.items():
        # Pull weight/pnl from port_df so merged cards always have them.
        pm = _port_rows(port_df, ticker)
        w  = _f(pm["Weight (%)"].iloc[0]) if pm is not None and not pm.empty else None
        p  = _f(pm["P&L (%)"].iloc[0]) if pm is not None and not pm.empty else None

        mechanical = [it for it in group if it.get("kind") in _MECHANICAL_KINDS]
        if mechanical:
            # Mechanical wins. Highest priority (critical before high), and if
            # tied, stop_breach before sell_signal.
            mechanical.sort(key=lambda x: (
                _pri_rank.get(x["priority"], 9),
                0 if x.get("kind") == "stop_breach" else 1,
            ))
            primary = dict(mechanical[0])
            primary["weight"]  = w if w is not None else primary.get("weight")
            primary["pnl_pct"] = p if p is not None else primary.get("pnl_pct")
            consolidated.append(primary)
            continue

        # No mechanical exit — merge softer advisories into one card.
        group.sort(key=lambda x: _pri_rank.get(x["priority"], 9))
        primary = dict(group[0])
        # Gather all risk flags across the group (could be multiple recs).
        all_flags: list[dict] = []
        for it in group:
            all_flags.extend(it.get("risk_flags", []) or [])
        if all_flags:
            primary["risk_flags"] = all_flags
            # If the primary is a risk card, reflect the flag count in the header.
            if primary.get("kind") == "risk" and len(all_flags) > 1:
                primary["action"] = f"RISK — {len(all_flags)} flags on {ticker}"
        primary["weight"]  = w if w is not None else primary.get("weight")
        primary["pnl_pct"] = p if p is not None else primary.get("pnl_pct")
        consolidated.append(primary)

    # Back-compat: synthesize a flat `reason` string on every item from the
    # structured fields. Older consumers (quick_research _portfolio_bullet,
    # premarket movers tooltip) read item["reason"] directly — keep it
    # populated so they don't KeyError on the new structured shape.
    def _synth_reason(it: dict) -> str:
        if it.get("reason"):
            return it["reason"]
        parts = []
        if it.get("directive"):
            parts.append(it["directive"])
        elif it.get("risk_flags"):
            parts.append(" ".join(f.get("recommendation", "") for f in it["risk_flags"]))
        if it.get("why"):
            parts.append(it["why"])
        return " ".join(p for p in parts if p).strip()

    out = passthrough + consolidated
    for it in out:
        it["reason"] = _synth_reason(it)

    # Critical first; then by the exit hierarchy (stop > Sell > EXIT > TRIM >
    # other); then by dollar risk desc (largest exposure first, weight as
    # fallback). None values sort last within each band.
    out.sort(key=lambda x: (
        0 if x["priority"] == "critical" else 1,
        _KIND_RANK.get(x.get("kind"), 4),
        -(x.get("dollar_risk") or 0),
        -(x.get("weight") or 0),
    ))
    return out


# ── Review Before Close ────────────────────────────────────────────────────────

# Moved to trim_sizing.earnings_overweight_floor (single source, 2026-10-25); the
# alias keeps the old name importable.
_dynamic_overweight_floor = _trim_sizing.earnings_overweight_floor


def _review_list(port_df, news_items, macro_events, held_data, today,
                 portfolio_value: float = 0.0,
                 act_today: list | None = None,
                 deterioration: list | None = None,
                 news_window_start_ts: float | None = None,
                 news_window_label: str | None = None) -> list[dict]:
    """
    Build the Review Before Close list.

    Each item carries:
      headline — one-line description of the trigger
      action   — structured directive dict (type + quantitative fields)
      why      — 1-line rationale (refs numbers from the trigger)
      trigger  — what condition tomorrow escalates this to ACT

    The renderer (app.py) formats action into a display string and, for
    weak-large items, appends "reallocate to" alternatives sourced from
    the brief's new_picks / add_positions (two complementary buckets).

    portfolio_value is required to compute dollar amounts for trim
    directives; passed in by build_daily_briefing.
    """
    items: list[dict] = []
    held_tickers = _held_tickers(port_df)
    # Tickers already surfaced in Act Today — the news-warning block below
    # excludes them so a ticker's negative-news risk shows ONCE, on the
    # higher-priority surface, instead of appearing as a Critical-News ACT here
    # AND a "no action today" WATCH below (the DELL split-brain). Mirrors the
    # act_today dedup that _grow_today already does.
    _act_tickers = {str(a.get("ticker", "")).upper() for a in (act_today or [])}
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH, affected_sectors as _aff_sectors

    # 0 — Deterioration WATCH (awareness only). The early tripwire: a held name
    # down ≥ WATCH_DD% from its peak and below trend, but not yet at the
    # TRIM/EXIT action thresholds. Lives here (not Act Today) so it informs
    # without demanding a same-day trade — the calm-advisor split (§2B). Deduped
    # against Act Today so a name escalated to TRIM/EXIT doesn't also WATCH here.
    for d in (deterioration or []):
        if d.get("tier") != "WATCH":
            continue
        if str(d["ticker"]).upper() in _act_tickers:
            continue
        items.append({
            "priority": "low",
            "icon":     "👁️",
            "ticker":   d["ticker"],
            "headline": (
                f"down {d['dd_from_peak_pct']:.1f}% from ${d['peak']:.2f} peak, "
                f"below SMA{d['trend_ma']}"
            ),
            "action":   {"type": "DETERIORATION_WATCH"},
            "why": (
                f"Early deterioration: drawdown {d['dd_from_peak_pct']:.1f}%, below the "
                f"{d['trend_ma']}-day trend. Not an action yet — watching for follow-through."
            ),
            "trigger": "A 2-of-3 close below the trend with market underperformance → TRIM.",
            "weight":  d.get("weight_pct"),
            "lifecycle": "",
            # Carried forward so the exit-signal capture block (app.py) can
            # persist them to exit_signals for the WATCH signal_type — same
            # gap as the Act Today deterioration items above.
            "pnl_pct": d.get("pnl_pct"),
            "price": d.get("price"),
            "dd_from_peak_pct": d.get("dd_from_peak_pct"),
            "below_ma_count": d.get("below_ma_count"),
            "rel_strength": d.get("rel_strength"),
        })

    # 1 — Approaching stop (0–8% gap)
    # Action policy:
    #   - If gap critical (<3%) AND P&L < profit-lock threshold: just tighten stop.
    #   - If gap critical AND P&L ≥ profit-lock threshold: trim partial AND tighten.
    #   - If gap 3-8%: tighten stop only (still room to run).
    # New stop = price − STOP_TIGHTEN_ATR_MULT × ATR.
    #
    # Suppression: if the current active stop (manual override or ratchet) is
    # already at or tighter than the recommended new_stop, don't re-fire — the
    # user has already actioned (manual_stops table) or the ratchet has already
    # provided equivalent protection. Closes the loop the user's "Mark Done"
    # button opens.
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if not (0 < gap <= APPROACHING_STOP_GAP_PCT):
            continue
        ticker = str(row["Ticker"])
        price  = _f(row.get("Price"))
        shares = int(_f(row.get("Shares")))
        pnl_pct = _f(row.get("P&L (%)"))
        weight  = _f(row.get("Weight (%)"))
        current_stop = _f(row.get("Stop"))
        atr_val = _f((held_data or {}).get(ticker, {}).get("atr")) if held_data else 0.0
        new_stop = round(price - STOP_TIGHTEN_ATR_MULT * atr_val, 2) if (price and atr_val) else None
        # Suppress if current stop already meets the recommendation. A 1-cent
        # buffer absorbs floating-point rounding so a manual stop set at exactly
        # the recommended level doesn't keep re-firing on rounding noise.
        if new_stop is not None and current_stop >= new_stop - 0.01:
            continue
        is_critical = gap <= 3.0
        # Lifecycle / settling grace: a freshly-opened position (held <
        # POSITION_SETTLING_DAYS) sits at its normal entry-to-stop distance by
        # construction — don't nudge it to tighten while it's still finding its
        # feet. Exits and critical-gap (≤3%) items are unaffected (precedence in
        # classify_position_state + the `not is_critical` guard). age None (no
        # journal) never yields "settling" → management is never silenced blind.
        _lifecycle = classify_position_state(
            (held_data or {}).get(ticker, {}).get("position_age_days"),
            pnl_pct, gap, has_exit_signal=False,
        )
        if _lifecycle == "settling" and not is_critical:
            continue
        # Profit-aware gate: a position that still has ROOM (gap > 3%) with no
        # gain to protect yet is NOT nudged to tighten — that's premature
        # micromanagement. A freshly-opened/flat position sits 3–8% above its
        # own ATR stop by construction (the MSFT case: gap 6.2%, P&L -0.0%);
        # tightening toward break-even removes the room the wider entry stop
        # deliberately gave it and reads as day-trading churn. Let the original
        # stop work until there's an actual gain to lock. CRITICAL-gap (≤3%,
        # about to be stopped out) positions still surface regardless of P&L.
        if not is_critical and pnl_pct < STOP_TIGHTEN_MIN_GAIN_PCT:
            continue
        lock_profits = pnl_pct >= STOP_PROFIT_LOCK_PNL_PCT
        if is_critical and lock_profits:
            trim_shares = _trim_sizing.fraction_trim_shares(shares, STOP_PROFIT_LOCK_TRIM_PCT)
        else:
            trim_shares = 0
        if is_critical and lock_profits and trim_shares >= 1:
            trim_dollars = round(trim_shares * price, 0)
            action = {
                "type":         "TRIM_AND_TIGHTEN",
                "trim_shares":  trim_shares,
                "trim_dollars": trim_dollars,
                "trim_pct":     STOP_PROFIT_LOCK_TRIM_PCT,
                "new_stop":     new_stop,
            }
            why = (
                f"Gap critical ({gap:.1f}% < 3%) and P&L +{pnl_pct:.1f}% above "
                f"{STOP_PROFIT_LOCK_PNL_PCT:.0f}% — lock part of the gain, tighten stop on the rest."
            )
        elif is_critical and lock_profits:
            # A profit-lock trim that floors to 0 shares (2026-10-23 rule: no
            # sub-share trim calls): tighten only, and say why without the
            # "still has room" wording, which a critical gap contradicts.
            action = {"type": "TIGHTEN_ONLY", "new_stop": new_stop}
            why = (
                f"Gap critical ({gap:.1f}% < 3%) and P&L +{pnl_pct:.1f}%. A "
                f"{STOP_PROFIT_LOCK_TRIM_PCT:.0f}% trim is under one share, so only "
                "the stop is tightened."
            )
        else:
            action = {"type": "TIGHTEN_ONLY", "new_stop": new_stop}
            why = (
                f"Gap {gap:.1f}% above stop; P&L {pnl_pct:+.1f}%. "
                "Tighten stop to protect downside; position still has room."
            )
        items.append({
            "priority": "medium" if is_critical else "low",
            "icon":     "📍",
            "ticker":   ticker,
            "headline": f"gap {gap:.1f}% above {row.get('Stop Type','ATR')} stop, P&L {pnl_pct:+.1f}%",
            "action":   action,
            "why":      why,
            "trigger":  "Stop break = full exit next open.",
            "weight":   weight,
            "lifecycle": _lifecycle,
        })

    # 2 — Earnings within 7 days
    # Action policy:
    #   - Weight > dynamic overweight floor (position-count-aware — see
    #     _dynamic_overweight_floor) → trim to TRIM_TO_PCT (10%).
    #   - Weight ≤ floor → WATCH; sizing already conservative for the binary risk.
    seen_earn: set = set()
    _n_pos = len(port_df) if port_df is not None else 0
    _overweight_floor = _dynamic_overweight_floor(_n_pos)
    for ticker, data in (held_data or {}).items():
        earn_date = (data or {}).get("earnings")
        if not earn_date or ticker in seen_earn:
            continue
        days = _days_until(earn_date, today)
        if days is None or not (0 <= days <= EARNINGS_IMMINENT_DAYS):
            continue
        seen_earn.add(ticker)
        if ticker in _act_tickers:
            continue
        pm = _port_rows(port_df, ticker)
        if pm.empty:
            continue
        weight = _f(pm["Weight (%)"].iloc[0])
        shares_raw = _f(pm["Shares"].iloc[0])
        price  = _f(pm["Price"].iloc[0])
        label  = "TODAY" if days == 0 else f"in {days}d"
        _etrim = _trim_sizing.earnings_week_trim(weight, _n_pos, shares_raw)
        if _etrim is not None and _etrim["trim_shares"] >= 1:
            trim_shares  = _etrim["trim_shares"]
            trim_dollars = round(trim_shares * price, 0)
            action = {
                "type":           "TRIM_TO_TARGET",
                "trim_shares":    trim_shares,
                "trim_dollars":   trim_dollars,
                "from_weight":    weight,
                "target_weight":  _etrim["target_pct"],
                "reason_key":     "earnings",
            }
            why = (
                f"Weight {weight:.1f}% × binary earnings risk above your "
                f"{_overweight_floor:.1f}% position-count-adjusted threshold "
                f"(N={_n_pos} positions). Trim to size-down the binary event exposure."
            )
            trigger = "Beat → re-enter post-print if composite holds; Miss → existing stop protects rest."
        elif _etrim is not None:
            action = {"type": "WATCH"}
            why = (
                f"Weight {weight:.1f}% is over the {_overweight_floor:.1f}% "
                f"position-count-adjusted line by less than one share at today's "
                f"price, so no trim is sized (N={_n_pos} positions)."
            )
            trigger = "Surprise miss + price gap-down → existing stop protects; no pre-event action needed."
        else:
            action = {"type": "WATCH"}
            why = (
                f"Weight {weight:.1f}% within tolerance "
                f"(≤ {_overweight_floor:.1f}% position-count-adjusted threshold, "
                f"N={_n_pos} positions). Existing sizing already conservative for the binary risk."
            )
            trigger = "Surprise miss + price gap-down → existing stop protects; no pre-event action needed."
        items.append({
            "priority": "medium" if days <= EARNINGS_CRITICAL_DAYS else "low",
            "icon":     "📅",
            "ticker":   ticker,
            "headline": f"earnings {label} ({earn_date}), weight {weight:.1f}%",
            "action":   action,
            "why":      why,
            "trigger":  trigger,
            "weight":   weight,
        })

    # 3 — Weak large positions (weight ≥ LARGE_POSITION_WEIGHT_PCT, Score < WEAK_CONVICTION_SCORE)
    # Action: trim to WEAK_LARGE_TRIM_TO_PCT (below re-flag threshold).
    # Alternatives are wired in by the renderer (app.py has access to new_picks/add_positions).
    for _, row in port_df.iterrows():
        weight = _f(row.get("Weight (%)"))
        score  = _f(row.get("Score"))
        if not bool(row.get("Score Available", True)):
            # Score is fabricated-neutral withheld (fundamentals unavailable
            # from any provider — a fund/ETF or a stock mid-data-outage; see
            # portfolio.build_portfolio_df). _f() coerces the withheld NaN
            # Score to 0.0, which would otherwise ALWAYS satisfy
            # `score < WEAK_CONVICTION_SCORE` below and fire a false
            # "weak conviction, trim" card off a number nobody measured.
            # Fail-open default True: a legacy row missing this column
            # entirely (before this flag existed) must not be skipped.
            continue
        if not (weight >= LARGE_POSITION_WEIGHT_PCT and score < WEAK_CONVICTION_SCORE):
            continue
        ticker = str(row["Ticker"])
        shares = int(_f(row.get("Shares")))
        price  = _f(row.get("Price"))
        if portfolio_value > 0 and price > 0:
            target_value = portfolio_value * (WEAK_LARGE_TRIM_TO_PCT / 100)
            current_value = portfolio_value * (weight / 100)
            trim_dollars = round(current_value - target_value, 0)
            trim_shares  = max(1, int(round(trim_dollars / price)))
        else:
            trim_dollars, trim_shares = 0, 0
        items.append({
            "priority": "medium",
            "icon":     "🔍",
            "ticker":   ticker,
            "headline": f"largest weak holding — {weight:.1f}% weight, score {score:.0f}/100",
            "action":   {
                "type":          "TRIM_TO_TARGET",
                "trim_shares":   trim_shares,
                "trim_dollars":  trim_dollars,
                "from_weight":   weight,
                "target_weight": WEAK_LARGE_TRIM_TO_PCT,
                "reason_key":    "weak_large",
            },
            "why": (
                f"Weight {weight:.1f}% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score "
                f"{score:.0f} < {WEAK_CONVICTION_SCORE} — largest position with weakest "
                "conviction. Free capital for higher-conviction names."
            ),
            "trigger": "Already flagged — act before close, don't wait.",
            "weight":  weight,
        })

    # 4 — Warning news on held positions: any in-window headline ≤ NEWS_SENTIMENT_WARN on a
    #     held name without an Act Today card (held_news_check.warning_news_by_ticker, D-C)
    # Always WATCH — one negative headline below critical threshold doesn't warrant a
    # quantitative action. Trigger condition tells the user what would escalate.
    # Tickers already carrying an ACTIONABLE card — in Act Today, OR a stop /
    # trim ACT in the Review blocks above. A negative-news WATCH for them is
    # redundant: its only escalation is "tighten stop on this position", which
    # is moot when the position is already being acted on (NVDA had a raise-stop
    # ACT and a news WATCH). Earnings WATCH (a distinct scheduled catalyst, e.g.
    # AVGO) is type "WATCH", so it does NOT suppress — genuinely different items
    # still coexist; only same-dimension/redundant info is collapsed.
    _actioned = set(_act_tickers) | {
        str(it.get("ticker", "")).upper() for it in items
        if (it.get("action") or {}).get("type") != "WATCH"
    }
    if news_window_start_ts is None or news_window_label is None:
        _nw = held_news_check.news_window_start(today)
        news_window_start_ts = _nw.timestamp()
        news_window_label = held_news_check.window_label(_nw)
    _warn = held_news_check.warning_news_by_ticker(
        news_items or [], held_tickers, news_window_start_ts, exclude=_actioned)
    for ticker, _w in _warn.items():
        _item = _w["item"]
        sent = _item.get("compound", 0)
        n_neg = _w["n_negative"]
        need = max(1, NEWS_CRITICAL_MIN_HEADLINES - _w["n_act_qualifying"])
        headline_text = (_item.get("title") or _item.get("headline") or "news")[:80]
        _lead = (
            f"One negative headline since {news_window_label} (sentiment {sent:+.2f})."
            if n_neg == 1 else
            f"{n_neg} negative headlines since {news_window_label} (worst {sent:+.2f})."
        )
        items.append({
            "priority": "low",
            "icon":     "📰",
            "ticker":   ticker,
            "watch_kind": "news",   # discriminator: a negative-news WATCH (vs an earnings/scheduled WATCH)
            "headline": (
                f"negative headline{'s' if n_neg != 1 else ''} since "
                f"{news_window_label} (worst {sent:+.2f}): \"{headline_text}\""
            ),
            "action":   {"type": "WATCH"},
            "why":      f"{_lead} Not enough alone for an Act Today card — wait for confirmation.",
            "trigger":  (
                f"{need} more tier-1/2 headline{'s' if need != 1 else ''} at or below "
                f"{NEWS_SENTIMENT_CRITICAL:+.2f} since {news_window_label} "
                "→ Act Today critical news (tighten your stop)."
            ),
            "weight":   0,
            "news_headline": str(_item.get("title") or _item.get("headline") or "news")[:140],
            "news_compound": _f(sent),
            # Structured, so a consumer (the add card's "Also in Review" note)
            # never parses the headline prose.
            "news_n_negative": n_neg,
            "news_window_label": news_window_label,
        })

    # 5 — Upcoming macro events (1–3 days)
    # Action policy:
    #   - Compute user's exposure to affected sectors from port_df.
    #   - If exposure > MACRO_AFFECTED_TRIM_THRESHOLD_PCT → recommend trimming
    #     the lowest-conviction holding in the affected sectors (the largest
    #     one when no affected score is measured -- audit B-M6) by
    #     MACRO_AFFECTED_TRIM_REDUCTION_PP percentage points of portfolio.
    #   - Else → WATCH (exposure within tolerance).
    for ev in (macro_events or []):
        ev_date = ev.get("date")
        if not ev_date or ev.get("impact") != MC_HIGH:
            continue
        days = _days_until(ev_date, today)
        if days is None or not (1 <= days <= MACRO_IMMINENT_DAYS):
            continue

        # Compute user's exposure to the event's affected sectors.
        ev_affected_sectors = set(_aff_sectors(ev.get("category", "")))
        if "__ALL__" in ev_affected_sectors:
            # Event affects everything — exposure check is "all your equity."
            exposure_pct = 100.0
            sector_rows  = port_df.copy()
        else:
            sector_rows  = port_df[port_df["Sector"].isin(ev_affected_sectors)] if not port_df.empty else port_df
            exposure_pct = _f(sector_rows["Weight (%)"].sum()) if not sector_rows.empty else 0.0

        affected_tickers_str = ", ".join(ev.get("affected_tickers", [])[:4]) or "macro-sensitive"
        playbook = ev.get("playbook_note") or ""

        # Don't pre-trim a name that already carries its own Act Today decision
        # (a critical-news hold, a stop breach, a sell signal, a risk trim). That
        # name is already being acted on; a second "trim ahead of the macro event"
        # card on the SAME ticker is the contradictory double-surface §2B kills
        # (AVGO: critical-news "hold & tighten" vs NFP "trim"). Trim the weakest
        # affected name NOT already spoken for; if every affected holding is, the
        # event downgrades to WATCH (awareness), not a conflicting trim.
        _macro_eligible = sector_rows[
            ~sector_rows["Ticker"].astype(str).str.upper().isin(_act_tickers)
        ] if not sector_rows.empty else sector_rows

        # Broad macro print (NFP / CPI / Fed) — the affected sectors cover most
        # of the book, so a bounded single-name trim wouldn't meaningfully cut
        # the exposure; it just reads as pre-event churn (§2B). Downgrade to a
        # "hold through, mind your stops" awareness WATCH. The sized trim is
        # reserved for sector-CONCENTRATED events below.
        _is_broad = exposure_pct >= MACRO_BROAD_EXPOSURE_PCT

        if _is_broad:
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
                "broad":         True,
            }
            why = (
                f"This event touches {exposure_pct:.0f}% of your portfolio — a broad "
                "macro print, not a sector-specific shock. A token single-name trim "
                "wouldn't materially reduce that exposure; hold through and let your "
                "existing stops do the work. Sized de-risking is reserved for events "
                "that hit a concentrated sector you can actually prune."
            )
        elif exposure_pct > MACRO_AFFECTED_TRIM_THRESHOLD_PCT and not _macro_eligible.empty and portfolio_value > 0:
            # Find lowest-conviction-score holding in the affected sectors
            # (or, with none measured, the largest -- see below).
            # 2026-10-11 audit B-M6: rank ONLY measured scores. A withheld
            # score is NaN (fundamentals unavailable -- see
            # portfolio.build_portfolio_df's "Score Available"); sort_values
            # puts NaN last, so a partly-withheld sector already picked a
            # measured name, but an ALL-withheld one picked an arbitrary row
            # and _f(NaN) rendered it as a measured "score 0". With no
            # measured name the trim still fires (protection must not go
            # quiet mid-outage, same posture as the H3 concentration card),
            # on the LARGEST affected position, and says why (owner decision
            # 2026-10-12).
            _measured_mask = _macro_eligible["Score"].notna()
            if "Score Available" in _macro_eligible.columns:
                _measured_mask &= _macro_eligible["Score Available"].fillna(False).astype(bool)
            _macro_measured = _macro_eligible[_measured_mask]
            _conviction_unmeasured = _macro_measured.empty
            if _conviction_unmeasured:
                # Ticker as the tie-break so the same book always gets the
                # same call (weights are rounded to 0.1, so ties are real).
                weakest = _macro_eligible.sort_values(
                    ["Weight (%)", "Ticker"], ascending=[False, True], kind="stable",
                ).iloc[0]
            else:
                weakest = _macro_measured.sort_values(
                    ["Score", "Ticker"], ascending=[True, True], kind="stable",
                ).iloc[0]
            weak_ticker = str(weakest["Ticker"])
            weak_score  = None if _conviction_unmeasured else _f(weakest["Score"])
            weak_weight = _f(weakest["Weight (%)"])
            weak_price  = _f(weakest["Price"])
            # Trim by MACRO_AFFECTED_TRIM_REDUCTION_PP percentage points of portfolio
            # — but cap at the position's own weight so we don't try to trim more
            # than the position holds.
            reduction_pp = min(MACRO_AFFECTED_TRIM_REDUCTION_PP, weak_weight)
            trim_dollars = round(portfolio_value * (reduction_pp / 100), 0)
            trim_shares  = max(1, int(round(trim_dollars / weak_price))) if weak_price > 0 else 0
            # Reconcile the displayed dollars + pp with the ROUNDED whole-share
            # count so "trim N shares (~$X)" is internally consistent (X = N ×
            # price). The pp-target alone diverges badly on small portfolios /
            # high-priced shares — the "1 share (~$571)" false-precision artifact.
            if weak_price > 0 and trim_shares > 0:
                trim_dollars = round(trim_shares * weak_price, 0)
                reduction_pp = (round(trim_dollars / portfolio_value * 100, 1)
                                if portfolio_value > 0 else reduction_pp)
            new_exposure = max(0.0, exposure_pct - reduction_pp)
            action = {
                "type":           "PROTECTIVE_TRIM",
                "trim_ticker":    weak_ticker,
                "trim_shares":    trim_shares,
                "trim_dollars":   trim_dollars,
                "from_exposure":  exposure_pct,
                "to_exposure":    new_exposure,
                "reduction_pp":   reduction_pp,
                "weakest_score":  weak_score,
                "conviction_unmeasured": _conviction_unmeasured,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% > "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold. "
                + (
                    "Conviction is unmeasured for every affected holding not "
                    "already in Act Today (fundamentals unavailable), so the "
                    "weakest name can't be ranked — trimming the largest "
                    "remaining affected position instead."
                    if _conviction_unmeasured else
                    "Trimming weakest holding first preserves higher-conviction names."
                )
            )
        elif (exposure_pct > MACRO_AFFECTED_TRIM_THRESHOLD_PCT and not sector_rows.empty
              and _macro_eligible.empty):
            # Over threshold, but every affected holding already has an Act Today
            # decision — defer to those instead of issuing a contradictory trim.
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% > "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% threshold, but the affected "
                "holdings already carry their own Act Today decision — no separate "
                "pre-event trim (avoids a contradictory double-call on one name)."
            )
        else:
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% ≤ "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold — within tolerance, "
                "no pre-event trim warranted."
            )

        items.append({
            "priority": "low",
            "icon":     "🌐",
            "ticker":   None,
            "event":    ev.get("event", "Macro event"),
            "headline": (
                f"{ev.get('event','Macro event')} in {days}d ({ev_date}) · "
                f"affected: {affected_tickers_str}"
            ),
            "action":   action,
            "why":      why,
            "trigger":  (
                playbook
                if playbook
                else f"Hawkish surprise → existing stops protect; dovish surprise → reconsider add."
            ),
            "weight":   None,
        })

    # Final-pass news-WATCH dedup. The inline check above suppresses a
    # negative-news WATCH for any ticker actioned by an EARLIER block or Act
    # Today. But the macro pre-event trim runs AFTER the news block and carries
    # its target in action.trim_ticker (the item-level ticker is None), so a
    # macro trim of a name that also has mild negative news would slip through
    # as a double-surface (MSFT: NFP "trim" in Act + news WATCH in Awareness).
    # Re-derive the full actioned set (incl. trim_ticker) and drop any redundant
    # negative-news WATCH. Only news-WATCH is removed (watch_kind=="news");
    # earnings/scheduled WATCHes are distinct catalysts and are preserved.
    _actioned_final = set(_act_tickers)
    for _it in items:
        _ia = _it.get("action") or {}
        if _ia.get("type") == "WATCH":
            continue
        _itk = str(_it.get("ticker") or _ia.get("trim_ticker") or "").upper()
        if _itk:
            _actioned_final.add(_itk)
    items = [
        _it for _it in items
        if not (
            _it.get("watch_kind") == "news"
            and str(_it.get("ticker") or "").upper() in _actioned_final
        )
    ]

    pri_order = {"medium": 0, "low": 1}
    items.sort(key=lambda x: (pri_order.get(x.get("priority","low"), 1), -(x.get("weight") or 0)))
    return items


def _rewrite_macro_affected(act: list[dict], reduced: set[str]) -> None:
    """Strip tickers already carrying an Act Today reduce card out of every
    macro item's "Affected" list, in place.

    A macro card's "Affected: GD, CRWD" restates names that already have their
    own SELL/TRIM card elsewhere in Act Today — read together, "hold through
    the print" for a ticker with a same-day mechanical stop-breach SELL looks
    like a contradictory second opinion rather than the same call (2026-07-29
    audit follow-up). `reduced` is the act+review reduce-ticker set computed by
    the caller via `decision_bucket._ticker()`.

    This is a deliberate enumeration-level trim, not a full single-surface
    dedup (memory feedback_single_surface_priority): macro exposure and a
    stock-specific stop-breach/trim are genuinely different dimensions, and a
    partially-trimmed name (e.g. review-origin TRIM_TO_TARGET) still holds a
    residual position that IS exposed to today's print. We drop it from the
    enumeration anyway because the card's own directive — "no new entries" —
    is moot for a name already being sold/trimmed, and the reduce card itself
    already carries the caution; the macro event's existence still surfaces
    via the card itself, only the restated ticker name is removed.
    """
    for it in act:
        if it.get("kind") != "macro":
            continue
        affected_all = it.get("_affected_all", [])
        if not affected_all:
            continue  # broad-market card ("Affected: broad market.") — nothing to filter
        remaining = [t for t in affected_all if str(t).upper() not in reduced]
        category = it.get("_event_category", "Economic")
        if remaining:
            it["why"] = f"{category} release today. Affected: {', '.join(remaining[:5])}."
        else:
            it["why"] = (
                f"{category} release today. "
                "Other affected names already carry today's own Act Today calls."
            )
        # `_act_today()`'s consolidation step already synthesized `reason` from
        # the original (unfiltered) directive+why for back-compat consumers
        # (quick_research, premarket movers) — keep it in sync so it can't
        # silently carry the stale, unfiltered ticker list.
        if it.get("reason"):
            it["reason"] = f"{it.get('directive', '')} {it['why']}".strip()


# ── Public API ─────────────────────────────────────────────────────────────────

def build_daily_briefing(
    port_df,
    alert_list:      list,
    risk_recs:       list,
    news_items:      list,
    macro_events:    list,
    held_data:       dict,
    scanner_results,
    portfolio_value: float,
    today:           date,
    market_context:  dict | None = None,
    grow_composites: dict | None = None,
    movers:          list | None = None,
    spy_df:          object | None = None,
    fragility:       dict | None = None,
    spy_trend_df:    object | None = None,
    vix_level:       float | None = None,
    winner_profile:  dict | None = None,
    trades_df:       object | None = None,
    net_capital:     float | None = None,
    etf_candidates:  list | None = None,
    cluster_add_blocks: dict | None = None,
    pair_add_blocks: dict | None = None,
    corr_unchecked: list | None = None,
) -> dict:
    """
    Build a Start-Your-Day briefing synthesising all available intelligence.

    grow_composites: optional dict {ticker: load_all() result} pre-fetched for top
                     scanner picks so _grow_today can validate conviction using the
                     full composite score, not just the momentum scanner score.
    winner_profile:  optional personalized_discovery.build_winner_profile() output,
                     passed straight through to _grow_today (see its own docstring).
    trades_df:       optional full trade journal (db.load_trades()) — feeds
                     Pre-Commitment Enforcement (docs/plans/premortem-
                     enforcement.md); omitted or None degrades to no
                     premortem_triggered cards, never an error.
    net_capital:     optional (F-255) equity after margin debit, passed straight
                     through to _grow_today's SEPARATE net-capital sizing cap.
                     None (the default, and every caller before F-255) leaves
                     sizing byte-identical to the pre-F-255 output.
    etf_candidates:  optional (ETF-support Phase 2b) — passed straight through
                     to _grow_today's SEPARATE ETF new-pick candidate pool.
                     None (the default, and every caller before Phase 2b)
                     leaves new_picks/every other key byte-identical to the
                     pre-Phase-2b output.
    cluster_add_blocks: optional (G-25, docs/plans/cluster-add-gate.md) —
                     passed straight through to _grow_today's bull-day add
                     loop. None (the default, and every caller before
                     G-25) leaves every existing output byte-identical.
    pair_add_blocks: optional (G-26, owner decisions 2026-10-08) — passed
                     straight through to _grow_today's bull-day add loop,
                     checked BEFORE cluster_add_blocks. None (the default,
                     and every caller before G-26) leaves every existing
                     output byte-identical.
    corr_unchecked:  optional (disclosure-only G-25/G-26 follow-on,
                     docs/plans/pair-add-gate.md) — portfolio.
                     correlation_unchecked() output, passed straight through
                     to _grow_today, which carries it unchanged onto
                     grow_today["corr_unchecked"]. Never read in any
                     skip/suppress decision here or in _grow_today — pure
                     passthrough for a render-layer disclosure caption. None
                     (the default, and every caller before this follow-on)
                     leaves every existing output byte-identical.

    Returns dict with: act_today, review_list, grow_today, portfolio_tuneup.
    Grow Today owns every new-position and add-to-winner suggestion (the
    scanner-only "More Buy Candidates" lane was retired
    2026-10-15 -- docs/plans/grow-today-owns-buys.md).
    """
    ctx    = market_context or {"tone": "flat", "sp500_pct": 0, "nasdaq_pct": 0, "leading_sectors": [],
                              "tone_available": False}

    # Build a unified earnings_lookup from held_data + grow_composites so the
    # earnings-risk check in _cross_reference applies to non-held new picks too.
    earnings_lookup: dict = {}
    for _t, _d in (held_data or {}).items():
        _ed = (_d or {}).get("earnings")
        if _ed:
            earnings_lookup[_t] = _ed
    for _t, _d in (grow_composites or {}).items():
        _ed = (_d or {}).get("earnings")
        if _ed:
            earnings_lookup.setdefault(_t, _ed)

    # Held-position deterioration (exit_advisor) — computed once, fed to both
    # Act Today (TRIM/EXIT) and the Review awareness lane (WATCH).
    deterioration = deterioration_signals(port_df, held_data, spy_df)

    # Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md) — pure
    # Python, zero LLM cost; degrades to [] on any missing/malformed input
    # (no trades_df, no held_data, DDL not yet applied) rather than erroring.
    from stock_analyzer.premortem_monitor import detect_premortem_triggers
    premortem_triggers = detect_premortem_triggers(trades_df, held_data, today)

    # Tickers with a SELL trade today — block from new-pick and add-to-winner so
    # a just-exited position never immediately re-surfaces as a BUY in the same
    # session (the exit advisor stops watching a ticker the moment it leaves the
    # portfolio, so _act_blocked alone has no memory of the exit).
    # The parsing rule is shared with the 📋 Watchlist page and the cron's
    # "Ready to Enter" email (stock_analyzer/sold_today.py) so all three
    # agree on what "sold today" means. This caller keeps its long-standing
    # posture of reading "couldn't check" (None) as "nothing sold".
    from stock_analyzer.sold_today import tickers_sold_on
    _sold_today_or_none = tickers_sold_on(trades_df, today)
    _sold_today: set[str] = set() if _sold_today_or_none is None else _sold_today_or_none

    # ONE recency window for both news checks (the same `news_window_start(today)`
    # Home's LLM rescore uses -- docs/plans/held-news-coverage.md D-A).
    _news_window = held_news_check.news_window_start(today)
    _news_ts     = _news_window.timestamp()
    _news_label  = held_news_check.window_label(_news_window)
    act    = _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today,
                        deterioration=deterioration,
                        premortem_triggers=premortem_triggers,
                        news_window_start_ts=_news_ts,
                        news_window_label=_news_label)
    review = _review_list(port_df, news_items, macro_events, held_data, today,
                          portfolio_value=portfolio_value, act_today=act,
                          deterioration=deterioration,
                          news_window_start_ts=_news_ts,
                          news_window_label=_news_label)

    # Risk-off protective de-risk (exit-discipline Phase 2) — computed AFTER act +
    # review so we can exclude any ticker already carrying a higher-priority reduce
    # (single-surface: never double-reduce a name). Only arms when the book is
    # fragile AND the market is in a risk-off regime; otherwise returns []. These
    # are the lowest-priority reduce, so they append to the end of Act Today.
    # _grow_today runs after this block so it sees the complete act list and
    # cannot surface an ADD for a ticker that risk-off just flagged for TRIM.
    # Exclude every ticker carrying ANY review card (not just the 3 TRIM types) —
    # a WATCH-type review card ("not an action yet") must never coexist with a
    # same-render risk-off "Trim now" Act Today card for the same name (2026-07-29
    # audit H6; the prior narrower TRIM-only filter let that contradiction through).
    # H6 still holds for non-news WATCH kinds. News-only cards (critical_news /
    # news WATCH) are exempt (D-D, docs/plans/held-news-coverage.md): a mild
    # headline must not move the protective TRIM off the biggest beta contributor.
    _reduced = decision_bucket.all_flagged_tickers(act, review)
    _rewrite_macro_affected(act, _reduced)
    _risk_off = exit_advisor.assess_risk_off_derisk(
        port_df, held_data,
        fragility=fragility, spy_trend_df=spy_trend_df, vix_level=vix_level,
        exclude_tickers=decision_bucket.all_flagged_tickers(act, review, ignore_news=True),
    )
    if _risk_off:
        # Where a risk-off TRIM lands, the same name's news WATCH is redundant:
        # drop it and carry its headline on the risk-off card (same `why` note
        # style as decision_bucket._reconcile_act's fold) so the news isn't lost.
        _ro_by_t = {}
        for _ro in _risk_off:
            _rt = decision_bucket._ticker(_ro)
            if _rt and _rt not in _ro_by_t:
                _ro_by_t[_rt] = _ro
        def _fold_news(_ro, _hl):
            _hl = str(_hl or "").strip()
            _note = ("⚠ Also flagged on recent negative headlines"
                     + (f" (\"{_hl}\")" if _hl else "")
                     + " — already factored into this reduce; it isn't a separate 'hold'.")
            _ro["why"] = f"{str(_ro.get('why', '')).strip()}  {_note}".strip()

        _kept_review = []
        for _it in review:
            _rt = decision_bucket._ticker(_it)
            if _it.get("watch_kind") == "news" and _rt in _ro_by_t:
                _fold_news(_ro_by_t[_rt], _it.get("news_headline"))
                continue
            _kept_review.append(_it)
        review = _kept_review
        # The Act critical-news card ("Hold for now") must not sit beside a
        # "Trim - Risk-Off" on the same name, whatever BUCKET_CRITICAL_NEWS_IS_ACT
        # says: fold it into the risk-off card the same way.
        _kept_act = []
        for _it in act:
            _rt = decision_bucket._ticker(_it)
            if str(_it.get("kind", "")) == "critical_news" and _rt in _ro_by_t:
                _fold_news(_ro_by_t[_rt], _it.get("news_headline"))
                continue
            _kept_act.append(_it)
        act = _kept_act
        act = act + _risk_off
    grow   = _grow_today(port_df, scanner_results, news_items, held_data, today, portfolio_value, ctx,
                         act_today=act, review_list=review, composites=grow_composites or {},
                         risk_recs=risk_recs,
                         earnings_lookup=earnings_lookup, macro_events=macro_events,
                         deterioration=deterioration,
                         movers=movers,
                         winner_profile=winner_profile,
                         net_capital=net_capital,
                         sold_today=_sold_today,
                         spy_df=spy_df,
                         etf_candidates=etf_candidates,
                         cluster_add_blocks=cluster_add_blocks,
                         pair_add_blocks=pair_add_blocks,
                         corr_unchecked=corr_unchecked)
    # Tune-up beta/sharpe cards restate a trim; if that name is already carrying
    # an Act Today card (incl. the risk-off TRIM appended above) or a Review
    # card, drop the redundant restatement (2026-08-04 audit — same broad
    # flagged basis as the risk-off exclude set, now that `act` includes risk-off).
    # News-only cards don't count (D-I): a news WATCH isn't a trim, so it must not
    # swallow the beta card.
    _acted_final = decision_bucket.all_flagged_tickers(act, review, ignore_news=True)
    tuneup = _portfolio_tuneup(risk_recs, acted_tickers=_acted_final)
    # News flags for OTHER surfaces (Rebalancer ADD cross-check), from the SAME
    # detectors/inputs/window the cards use but NOT from the cards: a held name
    # whose news card was de-duplicated away (another card/Act item) still has the
    # headline, and must still be flagged.
    _nf_held = _held_tickers(port_df)
    _nf_crit = held_news_check.critical_news_by_ticker(news_items or [], _nf_held, _news_ts)
    _nf_warn = held_news_check.warning_news_by_ticker(
        news_items or [], _nf_held, _news_ts, exclude=set(_nf_crit))
    return {"act_today": act, "review_list": review,
            "grow_today": grow, "portfolio_tuneup": tuneup,
            "news_flags": held_news_check.news_flags_from_detectors(_nf_crit, _nf_warn)}
