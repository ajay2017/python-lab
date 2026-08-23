"""
Decision constants — single source of truth for every threshold the app uses
to issue, suppress, or downgrade a recommendation.

The app operates in "decides" mode: thresholds here translate directly into
hard gates on what the user is told to do, so changes here are investment
policy decisions, not code tuning. When changing any value below, update
project_decision_thresholds.md (memory) with the rationale.
"""

from datetime import date

# ── Portfolio beta ───────────────────────────────────────────────────────────
PORTFOLIO_BETA_TARGET   = 1.0    # baseline equity-portfolio target
PORTFOLIO_BETA_ELEVATED = 1.3    # soft warning above this
PORTFOLIO_BETA_CEILING  = 1.4    # hard breach above this — institutional ceiling

# ── Defensive-sector diversifier sizing (risk_advisor beta + volatility recs) ──
# Shared range so the two risk_advisor.py recommendations that both suggest
# adding a Healthcare/Staples/Utilities diversifier can't quote different
# numbers for the same underlying action. The volatility rec's own follow-up
# math already assumes the midpoint (10%), which is why the max is 10, not 12.
DEFENSIVE_DIVERSIFIER_MIN_PCT = 8.0
DEFENSIVE_DIVERSIFIER_MAX_PCT = 10.0

# ── Portfolio Sharpe (risk-adjusted return) ──────────────────────────────────
# Below SHARPE_MEDIUM_RISK_MAX an action recommendation fires (MEDIUM, or HIGH
# if also below SHARPE_HIGH_RISK_MAX); at/above SHARPE_STRONG_MIN a congratulatory
# OK card fires instead. [SHARPE_MEDIUM_RISK_MAX, SHARPE_STRONG_MIN) is a
# deliberate dead zone — "acceptable but not strong" earns no card either way.
# Extracted 2026-07-28 from risk_advisor.py's previously-inline 0.4/0.8/1.0
# literals — a CLAUDE.md hard-rule-#1 gap flagged during the 2026-07-27
# risk.py Sharpe/Sortino Opus review (see project_test_automation memory).
SHARPE_HIGH_RISK_MAX   = 0.4     # below this -> HIGH priority ("risk not rewarded")
SHARPE_MEDIUM_RISK_MAX = 0.8     # below this -> an action fires (HIGH or MEDIUM)
SHARPE_STRONG_MIN      = 1.0     # at/above this -> OK, "strong risk-adjusted returns"

# Selection-only (does NOT gate whether the Sharpe rec above fires at all) —
# a ticker is named as a "Sharpe drag" contributor in the rec's root_cause
# when its own Sharpe trails the portfolio Sharpe by this relative fraction
# AND its weight clears the floor (too small a position isn't worth naming).
# Extracted 2026-07-28 alongside the ladder above — same hard-rule-#1 gap.
SHARPE_DRAG_RELATIVE_MAX   = 0.7   # ticker sharpe < portfolio sharpe * this -> named a drag
SHARPE_DRAG_MIN_WEIGHT_PCT = 3.0   # minimum position weight to be named a drag

# ── Portfolio annualised volatility ──────────────────────────────────────────
# risk_advisor.py's volatility rec: above VOL_HIGH_PCT -> HIGH priority; above
# VOL_MEDIUM_PCT (but not HIGH) -> MEDIUM; at/below VOL_MEDIUM_PCT -> no rec
# (there's no "OK" congratulatory card for volatility, unlike beta/Sharpe/
# drawdown — just silence). Extracted 2026-07-28, same hard-rule-#1 sweep as
# the Sharpe ladder above.
PORTFOLIO_VOL_HIGH_PCT   = 30.0    # above this -> HIGH priority
PORTFOLIO_VOL_MEDIUM_PCT = 25.0    # above this -> MEDIUM priority

# ── Portfolio max drawdown (values are negative %) ───────────────────────────
# risk_advisor.py's drawdown rec: below (more negative than) DRAWDOWN_ACTION_MAX
# an action fires (HIGH if also below DRAWDOWN_HIGH_MAX, else MEDIUM); above
# (less negative than) DRAWDOWN_OK_MIN a congratulatory OK card fires instead.
# (DRAWDOWN_HIGH_MAX, DRAWDOWN_OK_MIN) — i.e. -20% to -10% — is a deliberate
# dead zone, same pattern as the Sharpe ladder. DRAWDOWN_CONTRIB_MAX is
# selection-only: a per-ticker cutoff to be named a drawdown contributor in
# the rec's root_cause, doesn't gate whether the rec fires. Extracted
# 2026-07-28, same hard-rule-#1 sweep.
PORTFOLIO_DRAWDOWN_ACTION_MAX = -20.0   # below this -> an action fires
PORTFOLIO_DRAWDOWN_HIGH_MAX   = -30.0   # below this -> HIGH (else MEDIUM)
PORTFOLIO_DRAWDOWN_OK_MIN     = -10.0   # above this -> OK card
DRAWDOWN_CONTRIB_MAX          = -15.0   # per-ticker cutoff to be named a contributor

# ── Tail risk (CVaR / VaR ratio) ─────────────────────────────────────────────
# risk_advisor.py's tail-risk rec: above TAIL_RATIO_ACTION_MIN an action fires
# (HIGH if also above TAIL_RATIO_HIGH_MIN, else MEDIUM); at/below
# TAIL_RATIO_ACTION_MIN, no rec (no "OK" card for tail risk, same pattern as
# volatility). Extracted 2026-07-28, same hard-rule-#1 sweep.
TAIL_RATIO_ACTION_MIN = 1.7    # above this -> an action fires
TAIL_RATIO_HIGH_MIN   = 2.2    # above this -> HIGH (else MEDIUM)

# Concept D — regime-conditional position targets (Wave 3, 2026-07-17 policy
# conversation with user). Anchored to the existing regime-agnostic
# PORTFOLIO_BETA_TARGET/_ELEVATED/_CEILING baseline above: tightens in
# defensive regimes, loosens slightly in rate-cut optimism. Diagnostic only —
# see stock_analyzer/regime_targets.py — never gates/resizes/suppresses.
REGIME_BETA_CEILING = {
    "rate_cut":         1.25,
    "neutral":          1.10,
    "inflation_fight":  1.00,
    "recession_fear":   0.90,
    "stagflation_risk": 0.85,
}
REGIME_CASH_FLOOR_PCT = {
    "rate_cut":         5.0,
    "neutral":          5.0,
    "inflation_fight":  10.0,
    "recession_fear":   15.0,
    "stagflation_risk": 20.0,
}
# Below this detection confidence, the Regime Fit diagnostic flags the read as
# low-confidence/estimated rather than presenting it as certain. Display-only —
# never gates.
REGIME_CONFIDENCE_MIN_DISPLAY = 40

# ── Ticker beta (combined with portfolio beta for gating) ────────────────────
TICKER_BETA_HIGH     = 1.5       # "high beta" — soft warn when added to elevated port
TICKER_BETA_CRITICAL = 1.8       # "very high beta" — hard breach when added to breached port

# ── Fragility gauge (stress_test.assess_fragility) ───────────────────────────
# How a ROUTINE pullback would hit THIS book, surfaced pre-emptively on Home.
# NOT a forecast of WHEN a pullback comes (no one can reliably predict that) — a
# measure of how exposed the portfolio is IF one does. Reuses the stress-test
# "Mild Correction" engine. Severity reuses the PORTFOLIO_BETA_ELEVATED / _CEILING
# bands above so the gauge agrees with the risk advisor's beta gating — only the
# yardstick magnitude is new here.
FRAGILITY_PULLBACK_PCT = -10.0   # routine-correction yardstick (~1–2×/yr); mirrors the "Mild Correction" stress scenario

# ── Concentration limits ─────────────────────────────────────────────────────
SECTOR_CEILING    = 35.0         # hard sector cap (% of portfolio)
SECTOR_ELEVATED   = 25.0         # soft warn above this
SINGLE_NAME_CEILING = 15.0       # hard single-name cap — no add-to-winner above this
SINGLE_NAME_TRIM_TRIGGER = 18.0  # soft trim trigger — position that grew past ceiling (price appreciation after entry)
SECTOR_REDUCE_TRIGGER = 20.0     # sector diversification reduce trigger — recommend reducing to SINGLE_NAME_CEILING
# diversification_recommendations() literals, named per 2026-07-29 audit
# Medium finding (sat near-but-not-matching the ladder above with no comment).
DIVERSIFY_REDUCE_HIGH_URGENCY_PCT = 30.0  # sector REDUCE rec above this pct = "high" urgency (else "medium")
DIVERSIFY_ADD_SKIP_PCT   = 8.0   # a diversifying sector already at/above this pct is skipped as not-underweight
DIVERSIFY_ADD_TARGET_PCT = 10.0  # target allocation an ADD rec sizes its gap toward
# High-beta cluster share — the standing "correlated exposure" read under the
# fragility gauge: when this fraction of the book sits in high-beta (β ≥
# PORTFOLIO_BETA_ELEVATED) names, they tend to fall TOGETHER on risk-off days, so
# the per-name diversification is illusory. Above this % the cluster line renders
# as a warning rather than neutral info. Operational display threshold (not a
# gate) — tune from observation.
CONCENTRATION_HIGHBETA_SHARE_WARN = 60.0
# Concentration gates use the "tighter-of-both" basis: margin (signed net cash
# < 0) tightens the 15%/35% ceilings to a net-capital denominator; cash never
# loosens them. A manually-entered cash/margin figure older than this many days
# is treated as unknown → the gate degrades to equity-basis (see
# concentration.gating_denominator). OPERATIONAL knob (gate-safety freshness
# window), NOT an investment-decision threshold — tune from observation.
ACCOUNT_CASH_STALE_DAYS = 7

# The catch-all bucket a holding lands in when it has no curated sector mapping
# AND no provider .info sector. It is NOT a real correlated sector — it's a
# grab-bag of unclassified names — so concentration caps must NOT treat it as a
# tradable sector (a "Hard Cap Breach on Other → trim/redeploy" is incoherent
# advice). Gates exclude it; a data-hygiene note surfaces it instead.
UNCLASSIFIED_SECTOR = "Other"

# ── Diversification Advisor candidate sourcing ───────────────────────────────
# The ADD card draws candidates from the broad discovery universe (~200 curated
# liquid names) rather than a fixed 4-name roster, so a better entry outside the
# old list can surface. Each scored name is a cached load_all, so SCAN_CAP bounds
# the per-sector scoring work (prevents a fetch storm when many sectors are
# underweight); DISPLAY_TOP is how many ranked candidates render. The curated
# roster is always unioned in FIRST so known names are never dropped by the cap.
DIVERSIFY_SCAN_CAP    = 10        # max names composite-scored per underweight sector
DIVERSIFY_DISPLAY_TOP = 3         # ranked candidates shown per sector

# ── Rebalance-plan correlation labels (DISPLAY CLASSIFICATION, NOT A GATE) ────
# On the Hard-Cap-Breach rebalance card, each redeploy candidate is annotated
# with its correlation to YOUR ACTUAL BOOK (portfolio.correlation_to_portfolio) —
# a data-driven diversification read that supersedes the hardcoded tech-heavy
# _SECTOR_PROFILES values for this surface. These boundaries only pick the label
# text (🟢 genuine diversifier / 🟡 partial / 🔴 limited benefit); they NEVER
# gate, suppress, or reorder a candidate (the engine composite + COMPOSITE_BUY
# remain the sole ranker/gate). Same status as the analyst-consensus labels.
REDEPLOY_CORR_DIVERSIFIER_MAX = 0.40   # corr below this → "genuine diversifier"
REDEPLOY_CORR_CORRELATED_MIN  = 0.70   # corr at/above this → "limited benefit"

# ── Portfolio correlation pairs (Risk Analysis "High-Correlation Pairs") ─────
# Pairwise correlation among YOUR CURRENT HOLDINGS (portfolio.diversification_score)
# — a different surface from the redeploy-candidate labels above (REDEPLOY_CORR_*).
# AWARENESS ONLY — never gates/suppresses a recommendation; DANGER also seeds
# an advisory "consider trimming one side" note (portfolio.diversification_recommendations).
CORR_HIGH_PAIRS_THRESHOLD   = 0.65   # "warning" tier — pair flagged as meaningfully correlated
CORR_DANGER_PAIRS_THRESHOLD = 0.80   # "danger" tier — pair flagged as near-duplicate exposure

# ── Factor Tilt Heatmap (Concept B panel 3 — next-evolution roadmap Phase 2, ─
# sub-wave 3). Returns-based style analysis proxies (Pearson correlation of
# held-position returns against these factor ETFs) — NOT FMP .info style
# tags summed across positions, which the plan explicitly warns produces
# garbage. 6-month window chosen with the user over a 3-month alternative
# for statistical stability (factor tilt is noisy at small portfolio sizes).
# Diagnostic only — stock_analyzer/portfolio_intelligence.py never gates.
FACTOR_ETF_TICKERS = {
    "Momentum":       "MTUM",
    "Value":          "VLUE",
    "Quality":        "QUAL",
    "Low Volatility": "USMV",
    "Growth":         "VUG",
}
FACTOR_TILT_WINDOW_DAYS = 126   # ~6 trading months

# ── Grow Today candidate funnel ──────────────────────────────────────────────
# max_picks: how many NEW positions the daily brief will recommend. Lower on
# flat/bear days (capital-preservation posture). Investment-policy values —
# changing them is a policy decision, not tuning.
GROW_MAX_PICKS_BULL      = 3      # bull-day new-position cap
GROW_MAX_PICKS_DEFAULT   = 1      # flat-day new-position cap (bear days return
                                   # empty new_picks before this is ever read)
# Over-fetch headroom: composite-score this many × the pick cap so enough
# candidates survive the composite/macro/sector/cap gates to fill the slots.
# Coverage/perf knob (NOT a policy threshold): bigger = wider net scored, but a
# slower Refresh (each candidate is a load_all).
GROW_CANDIDATE_OVERFETCH = 4
# Derived pre-fetch pool = the bull-day maximum candidate window. The app
# pre-fetches composites for this many top non-held scanner picks so every
# candidate the brief could consider gets a real composite verdict. Single
# source of truth — app.py and daily_briefing.py both key off these.
GROW_CANDIDATE_POOL      = GROW_MAX_PICKS_BULL * GROW_CANDIDATE_OVERFETCH  # = 12

# ── Composite scoring boundaries ─────────────────────────────────────────────
# scoring.recommendation() uses these to assign the Strong Buy / Buy / Hold /
# Sell / Strong Sell label that surfaces across the app. Every gate and filter
# that talks about "Buy" or "Strong Buy" must import from here so the label
# the user sees on Analysis matches the gate Grow Today / Brief verdicts use.
COMPOSITE_STRONG_BUY = 75        # Strong Buy boundary
COMPOSITE_BUY        = 65        # Buy boundary — entry + add-to-winner gates
COMPOSITE_HOLD       = 44        # Hold floor — below this = Sell zone
COMPOSITE_SELL       = 30        # Sell floor — below this = Strong Sell

# Firmness badge band — how many composite points above its tier floor a
# new_pick must be before it is shown as "well clear" rather than "at the
# line". Display-only: does NOT gate, suppress, or alter any recommendation.
COMPOSITE_FIRMNESS_MARGIN = 3

# Conviction tiers (Grow Today new-pick label only — not a hard gate).
# A pick that clears COMPOSITE_BUY but doesn't yet reach STRONG_BUY is
# "moderate" conviction; STRONG_BUY+ is "high."
COMPOSITE_HIGH_CONVICTION = COMPOSITE_STRONG_BUY

# Stricter Grow Today bar on flat market days — only the highest-quality
# setups clear when the index isn't providing tailwind.
COMPOSITE_BUY_FLAT_DAY = 78

# Market tone (bull/bear/flat) from the S&P 500's daily % move — selects
# COMPOSITE_BUY (bull) vs COMPOSITE_BUY_FLAT_DAY (flat) vs no new entries
# (bear). Single source for both the interactive app (Home's market-context
# assembly) and the headless cron (headless_alert_engine.py's morning email)
# so the two runtimes can never independently drift on which gate a given
# day's picks were screened against (2026-08-04 audit finding — this was
# duplicated as a bare ±0.5 literal in both files).
MARKET_TONE_BULL_PCT = 0.5
MARKET_TONE_BEAR_PCT = -0.5

# Minimum composite for the #1 pick to be featured with full "Act on" framing
# in the morning action email. Below this it still appears but carries a
# "moderate" label rather than a high-conviction directive.
SCAN_TOP_PICK_MIN_COMPOSITE = 70

# Exit-signal velocity — composite score trend over a rolling window used to
# detect a WATCH position that is accelerating toward TRIM. Fires a section in
# the premarket email BEFORE the TRIM threshold is actually crossed.
EXIT_VELOCITY_LOOKBACK_DAYS  = 5   # rolling window for composite score trend
EXIT_VELOCITY_DROP_THRESHOLD = 8   # composite-point drop over window to alert

# Intraday pullback entry window (cron scan lane, Phase 3). Fires when a
# go-verdict morning pick dips from its open by at least PULLBACK_ENTRY_DIP_PCT
# while SPY is not in freefall (still above -PULLBACK_SPY_MAX_DOWN). Investment-
# policy decisions: tighter dip % = more signals; looser SPY floor = allows
# alerts even during a mild broad-market down day.
PULLBACK_ENTRY_DIP_PCT  = 1.5   # intraday drop from open (%) that triggers alert
PULLBACK_SPY_MAX_DOWN   = 1.0   # SPY intraday drop ceiling — above this = rout, suppress

# ± alpha band (percentage points vs benchmark) for classifying a position's
# relative performance as Outperforming / In Line / Underperforming on the
# Performance + Relative Strength views. Awareness/display only — never a gate.
PERF_ALPHA_BAND_PCT = 5.0

# Minimum reward:risk for an entry to be considered favourable. The composite
# score answers "is this a good STOCK to own?"; R:R answers "is THIS price a
# good ENTRY?" — independent questions, so a Strong-Buy stock can have poor
# entry R:R (target near, stop far). Watchlist ENTER_NOW hard-gates on this
# (G-13); the Analysis Trade Plan surfaces a caveat below it (not a hard block —
# the Analysis page is a research/judgement surface, so the user decides).
RR_ENTRY_MIN = 2.0

# ── Price-target construction heuristics (targets.compute_price_targets) ────
# One level upstream of RR_ENTRY_MIN above (which IS the actual entry gate) —
# these size the bull/base/bear price levels R:R is computed from. Named per
# 2026-07-29 audit Medium finding for tunability/documentation; pure
# extraction, no value changes, not a Hard Rule #1 gate boundary itself.
TARGETS_ENTRY_ZONE_LOW_ATR_FRAC  = 0.25  # entry-zone lower bound = price − this × ATR
TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC = 0.10  # entry-zone upper bound = price + this × ATR
TARGETS_52W_HIGH_FALLBACK_MULT = 1.3   # fallback 52w-high (× current price) when financials data missing
TARGETS_52W_LOW_FALLBACK_MULT  = 0.7   # fallback 52w-low (× current price) when financials data missing
TARGETS_SUPPORT_FALLBACK_MULT  = 0.88  # fallback nearest-support (× current price) when no local low found
TARGETS_MODEST_UPSIDE_MULT = 1.10  # generic "modest 10% upside" placeholder — base-target candidate AND bull-fallback default
TARGETS_BASE_FALLBACK_MULT = 1.08  # base-target fallback (× current price) when no candidate qualifies
TARGETS_BULL_ANALYST_MULT  = 1.20  # bull candidate: extended analyst target
TARGETS_BULL_52W_HIGH_MULT = 1.12  # bull candidate: 52w-high breakout multiple
TARGETS_BULL_FLAT_MULT     = 1.25  # bull candidate: flat upside from current price
TARGETS_BEAR_ATR_MULT = 6.0   # bear floor = price − this × ATR (~1.5 monthly adverse moves)
TARGETS_BEAR_SUPPORT_CUSHION_MULT  = 0.98  # bear floor candidate: nearest support with a 2% cushion
TARGETS_BEAR_52W_LOW_CUSHION_MULT  = 1.03  # bear floor candidate: 52w-low with a 3% cushion

# Watchlist Resurrection (O4, Agentic Intelligence Roadmap v2, 2026-07-26). A
# watchlist ticker added at least this many days ago that is now ENTER_NOW/
# NEAR_ENTRY is flagged as plausibly forgotten — a memory jog, never a gate.
# User-set policy value (not derived from ANALYST_COVERAGE_FRESH_DAYS, which
# means the opposite thing — "still fresh" — for a different, passive surface).
WATCHLIST_STALE_DAYS = 30

# ── Risk per trade (position sizing) ─────────────────────────────────────────
RISK_PCT_PER_TRADE = 0.015       # 1.5% portfolio risk per trade (Moderate)

# Display-only fallback portfolio value when the real total can't be determined
# yet (no holdings priced / session value missing). Feeds position-sizing DISPLAY
# math only — never a gate. Operational/display default, not an investment
# threshold. Hoisted from two duplicated literals in app.py so they can't drift.
DEFAULT_PORTFOLIO_VALUE = 50_000

# ── Add-to-winner / approaching-stop boundaries ──────────────────────────────
# A position must be at least this far above its stop before Grow Today will
# recommend adding to it (same threshold also marks the "Approaching Stop"
# Review-Before-Close bucket).
ADD_WINNER_MIN_GAP_PCT  = 8.0    # ≥ this gap = comfortable enough to add
APPROACHING_STOP_GAP_PCT = 8.0   # ≤ this gap = surface for monitoring (same number, different lens)
# Post-add cooldown: after the user ADDS shares to a position (a recent buy lot),
# don't keep re-recommending "add to this winner" for this many days. Acting on
# an add the user already executed is the same screen-watching churn §2B kills
# (the PATH case: still nudging "ADD" after the user bought 150 shares). Let the
# new shares settle; legitimate pyramiding can resume after the window. Aligned
# with POSITION_SETTLING_DAYS — "don't grow a position you just changed." None
# days-since-last-buy (no trade journal) = no cooldown (calm, not blind).
ADD_WINNER_COOLDOWN_DAYS = 10

# Weak-large-position flag (Review Before Close): a position is "weak" when
# both its weight and its conviction score breach these.
LARGE_POSITION_WEIGHT_PCT = 10.0
WEAK_CONVICTION_SCORE     = 55

# ── Review Before Close — action targets ────────────────────────────────────
# Triggers above define WHEN a position lands in Review. The values below
# define WHAT TO DO once flagged — translating the trigger into a concrete
# directive (trim X shares, raise stop to $Y, reallocate to Z). Without
# these, Review Before Close shows only "Consider X / Reassess" prose; with
# them, every item carries a quantitative action. See CLAUDE.md operating
# posture: the app decides, it does not inform.

# 📍 Approaching stop — partial profit-lock rule.
# When the gap to stop has narrowed (≤ APPROACHING_STOP_GAP_PCT) AND P&L is
# meaningful, recommend a partial trim alongside the stop tighten. A 25%
# P&L threshold avoids banking too early; a 25% trim preserves conviction
# in the remaining 75% while removing asymmetric downside.
STOP_PROFIT_LOCK_PNL_PCT  = 25.0
STOP_PROFIT_LOCK_TRIM_PCT = 25.0
# Initial / trailing stop width = current price − this × ATR. The single source
# for the base stop multiple, consumed by risk.atr_stop_loss, bundle_loader and
# the Analysis stop-ladder explainer so the number can never drift between the
# engine and what the UI explains. Policy value — change = investment-policy decision.
ATR_STOP_MULT             = 2.0
# New stop level when tightening = current price − this × ATR.
# Tighter than the 2.0× used for initial stops because the position is
# already in the danger zone — less room before next stop-out is warranted.
STOP_TIGHTEN_ATR_MULT     = 1.5
# Profit-aware tightening: a position that STILL HAS ROOM (gap 3–8% to stop)
# is only nudged to tighten once it has a real gain to protect (P&L ≥ this).
# A freshly-opened/flat position sits 3–8% above its own ATR stop by
# construction — tightening it toward break-even is premature churn (it removes
# the room the wider entry stop deliberately gave it) and is the kind of
# constant-management noise that makes the app feel like day-trading. Positions
# in the CRITICAL band (≤3% gap, about to be stopped out) still surface
# regardless of P&L. Policy value — change = investment-policy decision.
STOP_TIGHTEN_MIN_GAIN_PCT = 8.0
# Decimal places the Gap-to-Stop % is rounded to BEFORE the breach test
# (gap <= 0). Shared by build_portfolio_df (the stored "Gap to Stop (%)"),
# the Daily Brief's breach loop and the Analysis breach gate so all three
# fire at the exact same price — a single-source for the breach boundary
# precision (was a bare literal 1 in each). Not a stop-width policy value;
# it only controls where the rounding tips a near-zero gap to <=0.
GAP_TO_STOP_ROUND_DECIMALS = 1

# Profit-lock ratchet ladder: as a position's gain grows, floor its
# protective stop at (avg_cost × (1 + floor_pct)) so accumulated profit is
# never fully surrendered back to the ATR stop. Each row is
# (gain_pct_threshold, floor_pct, label); checked in descending-threshold
# order by portfolio.protective_stop()/stop_ladder() — the first tier whose
# gain threshold the position has cleared wins. The single source for both
# the live engine's stop and the Analysis "How your stop is set" explainer's
# ratchet-tier display, so they can never drift. Policy value — change =
# investment-policy decision. (2026-08-04 audit finding: this table lived as
# a portfolio.py module-local list, invisible to check_constants_documented.py,
# for a threshold this consequential — moved here per Hard Rule #1.)
STOP_RATCHET_LEVELS = (
    (75, 0.40, "Protect 40% gain"),
    (50, 0.25, "Protect 25% gain"),
    (25, 0.10, "Protect 10% gain"),
    (10, 0.02, "Breakeven guard"),
)

# ── Position lifecycle (position_lifecycle.classify_position_state) ───────────
# A held position moves through states: settling → established → winning, with
# at_risk / exit overriding on danger. Drives "settling grace" (don't micromanage
# a position you just opened) and lifecycle badges — the calm-advisor layer that
# keeps the app a medium-term advisor, not a day-trading feed (§2B persona).
POSITION_SETTLING_DAYS   = 10    # held < this = "settling": suppress ROUTINE mgmt nudges (not exits/critical)
POSITION_AT_RISK_GAP_PCT = 3.0   # gap-to-stop ≤ this = "at_risk" (same critical band; always surfaces)
POSITION_WINNING_PNL_PCT = 8.0   # P&L ≥ this (and healthy) = "winning"; aligns with STOP_TIGHTEN_MIN_GAIN_PCT

# ── Signals & Advice — portfolio.alerts()/rebalance_actions() thresholds ─────
# Named per 2026-07-29 audit H3 (were bare literals, one already drifted from
# APPROACHING_STOP_GAP_PCT above — see that fix at the alerts() call site).
ALERT_PNL_PROFIT_TAKE_PCT      = 15.0  # bearish signal + P&L above this = "consider partial profits"
ALERT_PNL_STOP_LOSS_PCT        = -8.0  # bearish signal + P&L below this = danger-level alert
REBALANCE_TRIM_PNL_PCT         = 20.0  # oversized position (SINGLE_NAME_TRIM_TRIGGER) + gain above this = trim candidate
REBALANCE_ADD_MIN_SCORE        = 70    # Strong Buy + undersized (<5% weight) + composite above this = add candidate
                                        # (kept as-is; adjacent code already requires "Strong Buy" in signal, which
                                        # implies composite ≥ COMPOSITE_STRONG_BUY=75, making this sub-condition
                                        # likely redundant today — named rather than silently changed, see audit H3)
REBALANCE_ADD_UNDERSIZED_PCT   = 5.0   # "undersized" weight ceiling for the add-candidate check above — was a
                                        # bare `w < 5` literal despite this comment already describing it as if
                                        # extracted (2026-08-04 audit finding)
REBALANCE_ADD_TARGET_WEIGHT_PCT = 8.0  # target weight used to size the "add" action's suggested dollar amount
REBALANCE_REVIEW_GAP_PCT       = 5.0   # bearish signal + profitable + gap below this (or unknown) = high urgency

# ── Brief Act-vs-Awareness split (decision_bucket.classify_bucket) ───────────
# The defensive column is split into "Act Today" (a genuine trade decision today)
# and "Monitoring / Awareness" (FYI). These two flags govern the borderline
# items; defaults are the user's choices. Flipping a flag moves the item between
# buckets with no code change. Unknown/missing kinds default to AWARE (calm).
BUCKET_TIGHTEN_ONLY_IS_ACT  = False  # stop-raise nudges → Awareness (protective housekeeping, not a buy/sell)
BUCKET_CRITICAL_NEWS_IS_ACT = True   # critical-news flags → Act Today (treat news as a same-day decision)

# ── Signal hysteresis (signal_hysteresis.apply_hysteresis) ───────────────────
# A pick whose composite barely moved since yesterday (within this band) and
# whose verdict is unchanged gets a calm "steady vs yesterday" chip — it tells
# the user "this isn't a fresh call, it's the same conviction holding". Damps
# the day-trader instinct to re-evaluate on daily noise. Annotate-only — NEVER
# suppresses a pick. Policy value — change = investment-policy decision.
HYSTERESIS_COMPOSITE_DELTA = 4.0   # |today − yesterday| composite ≤ this = "steady" (absorbs daily wobble)

# ── Held-position deterioration exit (exit_advisor.assess_holding) ────────────
# The missing middle layer between "Hold" and a score-collapse "Sell (<30)": a
# held name can bleed 15–25% while the composite sits inside Hold (44–64) and
# nothing fires. A trade-log review found ~$1,465 of realized loss in positions
# the app never flagged (the user exited them manually, on trend). This is a
# 3-tier drawdown-from-peak + trend-break signal: WATCH (awareness only) →
# TRIM (Act Today) → EXIT (Act Today, reduce aggressively). The thresholds are
# investment-policy decisions — change = policy, not code tuning. See
# docs/plans/exit-discipline.md. The TRIM/EXIT drawdown floors are
# ATR-scaled so a quiet name trips tight and a jumpy one gets room, but CEILINGs
# cap that widening so volatility can never disable the stop on the high-beta
# names that cause the biggest losses.
DETERIORATION_WATCH_DD_PCT     = 6.0    # drawdown-from-peak that arms WATCH (+ close < SMA50)
DETERIORATION_TRIM_DD_PCT      = 8.0    # base TRIM drawdown-from-peak floor
DETERIORATION_EXIT_DD_PCT      = 12.0   # base EXIT drawdown-from-peak floor
DETERIORATION_ATR_MULT_TRIM    = 2.5    # TRIM floor = max(TRIM_DD_PCT, this × ATR%)
DETERIORATION_ATR_MULT_EXIT    = 3.5    # EXIT floor = max(EXIT_DD_PCT, this × ATR%)
DETERIORATION_TRIM_DD_CEILING  = 14.0   # cap on the ATR-scaled TRIM floor (vol can't disable the stop)
DETERIORATION_EXIT_DD_CEILING  = 20.0   # cap on the ATR-scaled EXIT floor
DETERIORATION_EXIT_DOLLAR_LOSS = 250.0  # unrealized $ loss that escalates TRIM → EXIT
DETERIORATION_TREND_MA         = 50     # trend reference moving average (close < SMA50 = trend broken)
DETERIORATION_CONFIRM_DAYS     = 3      # trend-confirmation lookback window (sessions)
DETERIORATION_CONFIRM_REQUIRED = 2      # sessions below the MA required to confirm TRIM (NOT required for a deep EXIT)
DETERIORATION_TRIM_SUGGESTED_PCT = 25.0 # suggested reduction % shown in the idiosyncratic-deterioration TRIM directive (display-only quantity — never changes the TRIM/EXIT tier itself; matches RISK_OFF_TRIM_PCT's convention for a "modest reduction")
REL_STRENGTH_LOOKBACK_DAYS     = 20     # relative-strength-vs-SPY lookback (negative RS = idiosyncratic weakness)
DETERIORATION_PEAK_FALLBACK_BARS = 63   # peak-window lookback (~3mo) when position age is unknown (no journal)
MATERIAL_ADD_RESET_THRESHOLD   = 25.0   # a non-initial lot ≥ this % of the position re-anchors the deterioration PEAK window to "since the add" (averaging-down guard; cost basis stays blended — see exit_advisor.material_add_window_days)

# 🛡️ Risk-off protective de-risk (exit-discipline Phase 2).
# Promotes the Fragility gauge + Protect-Mode tone from awareness to a concrete
# per-holding TRIM directive, but ONLY in a genuine market-wide risk-off REGIME
# (not a single down day — that would sell the dip). Industry-grounded, not
# bespoke: the trigger is a trend OR volatility regime gate and the action is a
# risk-budgeting (β-contribution) trim. A LIGHT overlay — most risk stays managed
# at entry (sizing + concentration). Trigger fires only when the book is also
# fragile (_fragility severity caution/fragile), so these are investment-policy
# thresholds (set with the user), not operational knobs.
RISK_OFF_TREND_MA       = 200    # SPY below its N-day MA = de-risk. Basis: Faber, "A Quantitative Approach to Tactical Asset Allocation" (SSRN 962461) — 10-month/200-day trend rule.
RISK_OFF_VIX_LEVEL      = 25.0   # VIX ≥ this = high-vol regime. Basis: regime literature (<15 complacent, 15–20 normal, 20–30 elevated, 30+ stress); dynamic-allocation studies use ≥25 as the high-vol cut.
RISK_ON_VIX_LEVEL       = 15.0   # VIX ≤ this = complacent/risk-on regime. Same regime-literature basis as RISK_OFF_VIX_LEVEL above (<15 complacent).
RISK_OFF_NAME_MIN_BETA  = 1.2    # only trim genuinely high-beta drivers (β ≥ this); leaves defensives alone.
RISK_OFF_TRIM_TOP_N     = 3      # act on the top-N beta contributors (β × weight), not the whole book.

# ── Legacy ETF-proxy regime classifier (macro.detect_macro_regime_legacy) ────
# Named per 2026-07-29 audit Medium finding (were bare literals). VIX reuses
# RISK_OFF_VIX_LEVEL/RISK_ON_VIX_LEVEL above (same values already, just not
# imported before — this also closes the app.py Macro Signals panel's
# internal inconsistency, where the regime banner used these bare literals
# while the adjacent VIX delta badge already read the real constants).
MACRO_LEGACY_TLT_RET_PCT = 3.0   # |TLT 3mo return| beyond this = rising/falling rates signal
MACRO_LEGACY_SPY_RET_PCT = 5.0   # |SPY 3mo return| beyond this = bull/bear trend signal
RISK_OFF_TRIM_PCT       = 25.0   # suggested modest reduction per named position (or tighten the stop instead).

# Cross-asset regime signals — thresholds for the Cross-Asset Pulse card (Risk tab).
# Each signal is independent; score = count of stressed signals (0–5).
CROSS_ASSET_HYG_TREND_DAYS     = 20    # lookback window (days) for HYG linear trend
CROSS_ASSET_COPPER_TREND_DAYS  = 20    # lookback window (days) for copper trend
CROSS_ASSET_DXY_TREND_DAYS     = 20    # lookback window (days) for DXY trend
CROSS_ASSET_DXY_ROC_DAYS       = 5     # short-window rate-of-change for dollar signal
CROSS_ASSET_DXY_ROC_THRESHOLD  = 1.5  # % 5-day ROC above which dollar is "rapidly rising"
CROSS_ASSET_VIX_TERM_RATIO     = 1.0  # VIX / VIX3M ratio threshold; >1 = term-structure inverted
CROSS_ASSET_CURVE_STRESS_BP    = -50  # 3m10y yield spread (basis pts); below = deeply inverted (^IRX=3m T-bill, ^TNX=10yr)
CROSS_ASSET_STRESS_BRIEF_SCORE = 2    # score >= this triggers a one-liner in Today's Brief

# News sentiment via Finnhub /stock/news-sentiment — thresholds for the sentiment
# awareness layer (Analysis scorecard row + Brief shift alert). Phase 1: display only.
NEWS_SENTIMENT_BULLISH_THRESHOLD    = 0.60  # bullish_pct >= this → green "Bullish" label
NEWS_SENTIMENT_BEARISH_THRESHOLD    = 0.40  # bullish_pct <  this → red   "Bearish" label
NEWS_SENTIMENT_SHIFT_ALERT_BULLISH  = 0.40  # held-position alert fires when bullish_pct < this
NEWS_SENTIMENT_SHIFT_BUZZ_MIN       = 1.0   # alert only when buzz_score > this (active coverage)

# ── Analyst coverage ─────────────────────────────────────────────────────────
# Analyst consensus (avg_pt + rating label aggregated from analyst_coverage)
# feeds the Valuation pillar score.  Individual Ideas Inbox records remain
# display/awareness context only; only aggregated consensus metrics enter scoring.
ANALYST_COVERAGE_FRESH_DAYS = 30   # a report stays in the "recent" Ideas Inbox view this many days
ANALYST_MIN_UPSIDE_PCT      = 15   # Phase-2 Brief-chip threshold (avg-PT upside); UNUSED in Phase 1
# Consensus LABEL boundaries (display only — classify the firm rating
# distribution into a headline label; NOT decision thresholds, never gate/score).
# Fractions of rated firms.
ANALYST_CONSENSUS_STRONG_BUY_FRAC = 0.80
ANALYST_CONSENSUS_BUY_FRAC        = 0.50
ANALYST_CONSENSUS_SELL_FRAC       = 0.50

# Analyst price-target (PT) cut alert — F-169 Phase 2 (analyst_targets.py).
# Fires a "revisions" alert on a consensus target_mean drop even without an
# accompanying rating action (closes the gap at docs/architecture.md §6.23).
# Investment-policy values — Opus review required per CLAUDE.md hard rule #4;
# do not retune without a fresh review.
PT_TARGET_LOOKBACK_DAYS   = 5      # trading-day window for the comparison
PT_TARGET_CUT_WARN_PCT    = -7.0   # consensus target_mean drop over the window = warning
PT_TARGET_CUT_DANGER_PCT  = -15.0  # consensus target_mean drop over the window = danger

# ── Universe-ranking tier bands (ranking.tier_label) ─────────────────────────
# Percentile bands classifying a holding's rank vs the scanned universe.
# Display classification only — never gates or scores. Named per 2026-07-29
# audit Medium finding (were bare literals).
RANK_TIER_TOP_DECILE_PCTL      = 90   # >= this = "Top Decile"
RANK_TIER_TOP_QUARTILE_PCTL    = 75   # >= this (and < TOP_DECILE) = "Top Quartile"
RANK_TIER_ABOVE_MEDIAN_PCTL    = 50   # >= this (and < TOP_QUARTILE) = "Above Median"
RANK_TIER_BELOW_MEDIAN_PCTL    = 25   # >= this (and < ABOVE_MEDIAN) = "Below Median"
RANK_TIER_BOTTOM_QUARTILE_PCTL = 10   # >= this (and < BELOW_MEDIAN) = "Bottom Quartile"; else "Bottom Decile"

# Research Scorecard (accuracy tracking — display-only, never gates/scores).
ANALYST_ACCURACY_DIRECTION_DAYS = 30   # days after article_date to measure Buy/Sell directional accuracy
ANALYST_ACCURACY_PT_HIT_PCT     = 0.75 # fraction of avg_pt the window's intra-period HIGH must reach to count as a PT "hit" (not the endpoint close)
ANALYST_ACCURACY_LEADERBOARD_MIN_CALLS = 2   # min calls for a firm to appear on the Scorecard leaderboard (suppresses single-call noise)
ANALYST_ACCURACY_HIGHLIGHTS_MIN_EVALUABLE = 5   # min evaluable calls before showing best/worst-call highlight cards
# Max LLM OUTPUT tokens for one Ideas-Inbox extraction. A CNBC "biggest analyst
# calls" roundup can carry 20-30 separate calls → the JSON array of that many
# per-stock records overruns a small cap and truncates mid-array (→ JSON parse
# fails → silent "extraction failed"). Sized generously; billed per token
# actually generated, so a high ceiling is free for small single-stock pastes.
ANALYST_EXTRACT_MAX_TOKENS = 8000
# Per-call timeout for one Ideas-Inbox extraction. A 20-30 call roundup makes the
# model generate several thousand output tokens, which takes well past the shared
# 30s LLM_REQUEST_TIMEOUT_SEC → the request times out and looks like a parse
# failure. Given its own generous ceiling (a deliberate one-shot paste action).
ANALYST_EXTRACT_TIMEOUT_SEC = 90

# ── Valuation pillar ──────────────────────────────────────────────────────────
# Constants used by valuation.valuation_score() — the fourth scoring pillar that
# covers Forward P/E, FCF Yield, analyst PT upside, and analyst consensus rating.
VALUATION_COVERAGE_FRESH_DAYS = 90   # analyst_coverage lookback for scoring

VALUATION_PT_UPSIDE_STRONG  = 30    # ≥ this % → 25/25 pts
VALUATION_PT_UPSIDE_GOOD    = 15
VALUATION_PT_UPSIDE_MODEST  = 5
VALUATION_PT_UPSIDE_NEUTRAL = 0
VALUATION_PT_UPSIDE_NEAR    = -5

VALUATION_CONSENSUS_PTS = {
    "Strong Buy": 30,
    "Buy":        24,
    "Hold":       15,
    "Mixed":       9,
    "Sell":        0,
}

# ✉️ Protective-alert cron (exit-discipline Phase 3) — OPERATIONAL knob, not an
# investment-decision threshold. The ET hour the daily email targets; the cron is
# scheduled at two UTC times (to straddle EST/EDT) and a Supabase idempotency
# guard fires only the first run each ET trading day at/after this hour.
ALERT_EMAIL_HOUR_ET = 8

# 📉 Pullback-awareness Phase 2 — reactive drawdown email (EOD cron). OPERATIONAL
# alert-sensitivity knob, not an investment-decision threshold. Email an awareness
# ping when the broad market (SPY) closes down ≥ this % on the day. Kept deep so it
# stays RARE & meaningful (a ~3% index day happens only a few × / year) — calm
# advisor, not a panic feed (§2B).
PULLBACK_ALERT_INDEX_PCT = -3.0
ALERT_EOD_HOUR_ET        = 16   # EOD run fires only after this ET hour (post-close → final price)

# 📅 Earnings overweight — trim-down rule.
# Binary event = asymmetric risk. The trigger is now POSITION-COUNT-AWARE
# (see daily_briefing._dynamic_overweight_floor): floor = clamp(100/N +
# EARNINGS_OVERWEIGHT_TOLERANCE_PP, EARNINGS_OVERWEIGHT_TRIM_PCT,
# EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT). A flat threshold assumed a fixed
# ~10-position book and fired on nearly every name in a more concentrated
# portfolio purely from equal-weight math, not genuine over-concentration
# (found + fixed 2026-07-28 against a real 7-position portfolio where 5/7
# names tripped the old flat 12% despite none exceeding equal-weight+5pp).
# EARNINGS_OVERWEIGHT_TRIM_PCT is now the MIN clamp bound (floor-of-the-
# floor — keeps today's behavior for diversified portfolios where 100/N
# would otherwise dip below it). Trim down to EARNINGS_OVERWEIGHT_TRIM_TO_PCT
# (aligned with LARGE_POSITION_WEIGHT_PCT floor — "large but not overweight");
# the trim TARGET stays flat regardless of N (deliberate — only the trigger
# is dynamic). EARNINGS_OVERWEIGHT_TOLERANCE_PP intentionally duplicates the
# numeric value of rebalancer.TOLERANCE_WATCH (5.0) rather than importing it —
# Opus review flagged that coupling a binary-event risk gate to the
# rebalancer's general drift-monitor band means a retune of one silently
# shifts the other; this constant is deliberately independent so it can be
# tuned on its own investment-policy merits.
EARNINGS_OVERWEIGHT_TRIM_PCT          = 12.0
EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT  = 22.0  # binary-event risk cap even for a
                                               # deliberately concentrated book
EARNINGS_OVERWEIGHT_TOLERANCE_PP      = 5.0   # buffer added to equal-weight (100/N)
EARNINGS_OVERWEIGHT_TRIM_TO_PCT       = 10.0

# 🔍 Weak large position — trim-down rule.
# Target is set below the 10% LARGE_POSITION_WEIGHT_PCT re-flag threshold so
# the position stops appearing in Review next session. Anything lower would
# be excessive cap-throwing-away on a position you still hold.
WEAK_LARGE_TRIM_TO_PCT = 8.0

# 🌐 Macro event — protective-trim rule.
# Pairs with the existing MACRO_EXPOSURE_* tier cutoffs. If sector exposure
# to an affected sector exceeds MACRO_AFFECTED_TRIM_THRESHOLD_PCT AND a
# HIGH-impact event is ≤ MACRO_IMMINENT_DAYS away, recommend trimming the
# lowest-conviction holding in that sector by MACRO_AFFECTED_TRIM_REDUCTION_PP
# percentage points of portfolio.
# - Threshold 30% sits 5pp below MACRO_EXPOSURE_HIGH=35; above this the
#   portfolio is concentrated enough that binary surprises hurt materially.
# - 5pp reduction matches the long-only retail / RIA pre-event de-risk
#   magnitude — meaningful but not so large that a dovish surprise costs
#   the rest of the upside.
# - "Lowest-conviction first" preserves your high-conviction holdings
#   ("press winners, cull weaklings") while reducing gross exposure.
MACRO_AFFECTED_TRIM_THRESHOLD_PCT = 30.0
MACRO_AFFECTED_TRIM_REDUCTION_PP  = 5.0
# Above this affected-sector exposure, the event is effectively portfolio-wide
# (NFP / CPI / Fed hit ~every sector). A bounded single-name trim (≤ REDUCTION_PP)
# is immaterial against it and reads as pre-event churn (§2B). Such broad events
# are downgraded to an awareness WATCH ("hold through, mind your stops") instead
# of an Act-Today trim; the sized trim is reserved for sector-CONCENTRATED events
# where culling one name meaningfully cuts the exposure. Policy value.
MACRO_BROAD_EXPOSURE_PCT          = 60.0

# ── Movers discovery (surface breakouts outside the tracked universe) ────────
# The Movers pipeline scans the broad discovery_universe for today's big 1-day
# gainers, then composite-gates the shortlist so only quality breakouts surface.
MOVER_MIN_DAY_GAIN_PCT = 5.0    # min 1-day % gain to qualify as a "mover"
                                # (below this a move is noise, not a breakout)
MOVER_SHORTLIST_SIZE   = 12     # top N gainers to run the full composite on
                                # (bounds the expensive load_all fan-out)
MOVER_MAX_PICKS        = 3      # max movers surfaced in New Positions, as their
                                # OWN allowance separate from the curated cap.
                                # A composite-Buy breakout is itself "clearer
                                # direction", so movers are exempt from the
                                # flat-day high-conviction suppression — but
                                # still respect bear-day risk-off, the macro
                                # gate, the composite gate, and act-today blocks.

# ── Panic-day classifier (Trade Review behavioural lens) ─────────────────────
# Daily SPY return at or below this = "panic window" — trades executed on
# such days bucketed for retrospective behavioural analysis.
PANIC_DAY_SPY_PCT = -1.5

# ── News-sentiment compound-score cutoffs (VADER scale, -1 to +1) ────────────
# These gate where news shows up in the briefing:
#   compound ≤ NEWS_SENTIMENT_CRITICAL  → critical-news Act Today (held only)
#   compound ≤ NEWS_SENTIMENT_NEGATIVE  → mark as conflict in cross-reference
#   compound ≤ NEWS_SENTIMENT_WARN      → low-priority Review Before Close
#   compound ≥ NEWS_SENTIMENT_POSITIVE  → counted as supporting signal
NEWS_SENTIMENT_CRITICAL =  -0.25   # critical-news Act Today threshold
NEWS_SENTIMENT_NEGATIVE =  -0.15   # cross-reference "negative news" conflict
NEWS_SENTIMENT_WARN     =  -0.05   # warning-news Review Before Close
NEWS_SENTIMENT_POSITIVE =   0.10   # treat as supporting signal in xref
NEWS_CRITICAL_MIN_HEADLINES     = 2     # min qualifying headlines per ticker before firing Critical News Act Today
# Max news tier (1 = highest-quality source) that qualifies for "critical" —
# shared by daily_briefing's Critical News Act Today card AND
# news_intelligence.build_news_intelligence's per-headline alert-level
# classifier. Was a bare `<= 2` literal duplicated in both (2026-08-04 audit
# finding).
NEWS_CRITICAL_MAX_TIER = 2
# Minimum held-position weight (%) for a single critical-compound headline to
# be classified "critical" rather than "warning" in news_intelligence's
# per-headline alert level — a critical-sentiment headline on a 0.5%-weight
# position isn't worth the same urgency as one on an 8%+ position. Was a bare
# `8.0` literal with no constant at all (2026-08-04 audit finding).
NEWS_CRITICAL_MIN_WEIGHT_PCT = 8.0
# LLM bidirectional rescore — max points the LLM can shift any single headline's
# VADER compound score in either direction. Prevents a single LLM outlier from
# dominating the average while still giving the model full authority on genuine
# bear/bull signals. Impact is further bounded by the 10% composite weight.
SENTIMENT_LLM_MAX_SWING = 0.5

# ── News-Intelligence "Opportunity Signals" inclusion (Overview) ─────────────
# A held name surfaces as a positive-news opportunity when BOTH hold: the
# headline is positive AND the position is decent quality. Kept as their own
# named boundaries (house style: one policy value per decision surface) so the
# opportunity feed tunes independently of the xref cutoffs above.
NEWS_OPPORTUNITY_COMPOUND_MIN = 0.10   # positive-headline floor (matches NEWS_SENTIMENT_POSITIVE by design)
NEWS_OPPORTUNITY_SCORE_MIN    = 55     # composite-quality floor for an opportunity card

# ── Evening Debrief "meaningful intraday move" cutoff ────────────────────────
# Picks moving more than this in absolute % today get a verdict
# (Missed / Dodged / Skip validated). Smaller moves are "flat" — no signal.
MEANINGFUL_INTRADAY_PCT = 1.0

# ── Composite-score weights (scoring.combined_score) ─────────────────────────
# How much each layer contributes to the composite score. Tuning these is a
# policy decision — heavier technical = more momentum-driven, heavier
# valuation = more value-driven. Must sum to 1.0.
COMPOSITE_WEIGHTS = {
    "technical":        0.25,
    "business_quality": 0.35,
    "valuation":        0.30,
    "sentiment":        0.10,
}

# ── Earnings / macro proximity windows (days) ────────────────────────────────
EARNINGS_IMMINENT_DAYS      = 7  # any trade within this window = caution (binary-event conflict)
# Tighter "danger" sub-window inside EARNINGS_IMMINENT_DAYS — decide position
# size before the report, vs. the wider window's "review ahead of report."
# Single source for portfolio.py's alert danger/warning split and
# daily_briefing's earnings-overweight priority bump; was duplicated as a
# bare `3` in each independently (2026-08-04 audit finding).
EARNINGS_CRITICAL_DAYS      = 3
EARNINGS_MANAGEABLE_DAYS    = 21 # Brief verdict: imminent < window <= this = "manageable window" (agreed signal, not a conflict)
EARNINGS_URGENCY_SOON_DAYS  = 14 # Catalyst Watch playbook urgency tier: imminent < window <= this = "SOON", beyond = "AHEAD"
MACRO_IMMINENT_DAYS    = 3       # HIGH-impact macro event within this window = suppress new picks in affected sector
# 🌐 Macro page: warn when this much of the portfolio (by weight) sits in
# sectors facing a headwind under the current ETF-proxy regime read. Was a
# bare `> 30` literal (2026-08-04 audit finding).
MACRO_HEADWIND_WARN_PCT = 30.0

# Forward window (days) for the Catalyst Watch panel — upcoming earnings for
# names the app tracks (held + watchlist + sector universe). AWARENESS ONLY: it
# does not recommend initiating into earnings (the proximity gates still
# suppress that); it just removes the blind spot of a tracked name reporting
# without warning. Post-print confirmation still surfaces via the Movers scan.
CATALYST_WATCH_WINDOW_DAYS = 7

# Earnings Playbook — beat-rate and reaction-posture thresholds.
# These are investment-policy values; discuss with the user before changing.
EARNINGS_BEAT_RATE_REDUCE_THRESHOLD      = 60.0  # below this + weak composite → REDUCE pressure
EARNINGS_BEAT_RATE_STRONG_THRESHOLD      = 75.0  # above this + bullish reaction → strengthens HOLD_OR_ADD
EARNINGS_BEARISH_REACTION_COMPOSITE_GATE = 75    # bearish reaction history + composite < this → REDUCE
EARNINGS_MIN_BEAT_RATE_ENTRY             = 70.0  # catalyst scanner — min beat rate to surface a watchlist candidate

# Forward window (days) for macro-event awareness — high-impact events
# (FOMC, CPI, NFP, GDP) shown ahead on the Economic Calendar page and in
# Home's macro-calendar preamble. Display-only window, same pattern as
# CATALYST_WATCH_WINDOW_DAYS above; not a gate.
ECONOMIC_CALENDAR_WINDOW_DAYS = 45

# Catalyst-Specific Stress (D4, Agentic Intelligence Roadmap v2, 2026-07-26).
# How far ahead a HIGH-impact macro event or a held-ticker earnings date must
# fall to count as "upcoming" for the structural-overlap ranking. Deliberately
# a DISTINCT constant from EARNINGS_URGENCY_SOON_DAYS above (that one drives the
# Catalyst Watch earnings-playbook "SOON" display tier — a different feature;
# sharing one knob would silently couple two unrelated decisions). User-set
# policy value, not a gate.
CATALYST_STRESS_WINDOW_DAYS = 14

# ── Macro-event playbook gates (macro_playbook.py) ───────────────────────────
# Pre-event PROTECT / WATCH classification thresholds. Values surfaced here
# so future changes are policy decisions, not hidden literals.
MACRO_PROTECT_PNL_PCT    = -15.0  # already-underwater + bear-move = MEDIUM PROTECT
MACRO_PROTECT_BEAR_MOVE  =  1.5   # min % sector bear-move to flag any PROTECT action
MACRO_WATCH_MED_WEIGHT   =  8.0   # min weight for WATCH-MEDIUM (oversized + meaningful bear)
MACRO_WATCH_BEAR_MOVE    =  1.0   # min sector bear-move for any WATCH tier
MACRO_WATCH_LOW_SCORE    = 55.0   # weak score gating WATCH-LOW
MACRO_WATCH_LOW_WEIGHT   = 12.0   # min weight gating WATCH-LOW
MACRO_OPP_SCORE          = 68.0   # min composite score for OPPORTUNITY classification
MACRO_OPP_BULL_MOVE      =  1.5   # min sector bull-move for OPPORTUNITY

# Portfolio bear-exposure tier cutoffs for the macro-event playbook header
# (% of portfolio sitting in sectors with high bear-move sensitivity).
MACRO_EXPOSURE_CRITICAL_PCT = 55
MACRO_EXPOSURE_HIGH_PCT     = 35
MACRO_EXPOSURE_MEDIUM_PCT   = 15

# ── Regime-classifier CPI YoY thresholds (macro_calendar.detect_macro_regime) ─
# The CPI inflation ladder used by the 7-signal regime classifier. Below the
# CONTROLLED ceiling, inflation is "controlled" and supports the rate-cut
# regime; above ELEVATED it adds inflation-fight pressure; above HOT it is a
# strong inflation-fight / stagflation signal.
#   REGIME_CPI_CONTROLLED_MAX doubles as a HARD GATE: the "Rate-Cut Optimism"
#   regime claims "controlled inflation" in its rationale, so it must NOT be
#   selected when CPI YoY exceeds this value even if risk-on signals push the
#   score that way (else a 3.95% CPI print could land in a regime whose own
#   label contradicts it). Changing these is an investment-policy decision.
REGIME_CPI_CONTROLLED_MAX = 2.5   # ≤ this = controlled inflation (rate-cut supportive + gate ceiling)
REGIME_CPI_ELEVATED_MIN   = 3.0   # ≥ this (and ≤ HOT) = mild inflation-fight pressure
REGIME_CPI_HOT_MIN        = 4.0   # > this = strong inflation-fight / stagflation signal

# ── Regime-classifier remaining 6 signal families (macro_calendar.detect_macro_regime) ─
# Named per 2026-07-29 audit H2 — these were bare literals (only the CPI ladder
# above was already centralized). No value changes; pure extraction so this
# gate boundary is documented and reviewable like every other in this file.
REGIME_FEDFUNDS_TREND_PP   = 0.05   # |3mo Fed Funds change| above this = cutting/hiking; else "holding"
REGIME_2S10S_INVERTED_PP   = -0.25  # 10y-2y spread below this = inverted (strong recession-fear signal)
REGIME_2S10S_FLAT_PP       = 0.0    # spread below this (and above INVERTED) = flat/mild recession-fear signal
REGIME_2S10S_STEEP_PP      = 0.75   # spread above this = steep curve (rate-cut supportive)
REGIME_UNEMP_DELTA_UP_PP   = 0.3    # 3mo unemployment rise above this = recession-fear signal
REGIME_UNEMP_DELTA_DOWN_PP = -0.2   # 3mo unemployment fall below this = rate-cut supportive signal
REGIME_HY_SPREAD_STRESS_BP   = 600  # HY credit spread (bps) above this = strong recession-fear signal
REGIME_HY_SPREAD_ELEVATED_BP = 450  # spread above this (and below STRESS) = mild recession-fear signal
REGIME_HY_SPREAD_CALM_BP     = 300  # spread below this = rate-cut supportive (credit markets calm)
REGIME_SPY_20D_BULL_PCT    = 5.0    # SPY 20-trading-day return above this = rate-cut supportive
REGIME_SPY_20D_BEAR_PCT    = -5.0   # SPY 20-trading-day return below this = recession-fear signal
REGIME_VIX_STRESS          = 30     # VIX above this = strong recession-fear signal
REGIME_VIX_ELEVATED        = 20     # VIX above this (and below STRESS) = mild recession-fear signal
REGIME_VIX_CALM            = 15     # VIX below this = rate-cut supportive (fear gauge calm)
REGIME_WINNING_SCORE_MIN   = 1      # winning regime's score must exceed this, else fall back to "neutral"

# ── Multi-source market-data layer (providers/ + data.py orchestrator) ───────
# The app was historically single-sourced on yfinance (unofficial, no SLA). The
# provider seam + orchestrator add failover + a price cross-check so right-data-
# at-the-right-time is protected. See memory `project_second_data_source`.

# Failover order for the GENERAL data types (history / bundle / indices /
# risk-free): the orchestrator tries these IN ORDER, using the first CONFIGURED
# provider (key present) that advertises the capability. yfinance is primary
# here — it's free, unquota'd, and the only one that serves history/bundle/
# indices on the free tier. Order is a setting, not hardcoded.
DATA_PROVIDER_ORDER = ["yahoo_finance", "finnhub", "fmp"]

# Failover order for the LIVE-PRICE field specifically — DIFFERENT from the
# general order on purpose. Finnhub is primary because its free tier serves
# REAL-TIME US quotes (yfinance is ~15-min delayed), and real-time held-position
# prices/stops/P&L are core to the app's near-real-time intelligence. yfinance
# is the failover (gap-fill) if Finnhub rate-limits or is down, so worst case is
# today's delayed behaviour, never worse. Only the live-price path uses this;
# the broad scanner/movers scans stay on yfinance (they use history, and
# Finnhub's per-symbol /quote would blow the 60/min free limit on ~200 names).
DATA_LIVE_PRICE_ORDER = ["finnhub", "yahoo_finance", "fmp"]

# Master switch. When False, data.py behaves EXACTLY as the single-source
# yfinance code (no failover, no cross-check). Enabled 2026-06-01 after the
# orchestrator was validated live (selftest: all three sources agree to the
# cent; Finnhub-primary real-time live prices; prev_close cross-check ok). With
# it True: live prices come from Finnhub (real-time) with gap-fill to yfinance/
# FMP, and history/bundle/indices fail over yfinance→FMP. Cross-check surfacing
# is wired separately (Phase 5b-ii).
DATA_MULTISOURCE_ENABLED = True

# ── Data-load concurrency (operational tuning — NOT an investment threshold) ──
# The held-data / candidate cold-load fans out load_all() across threads. Yahoo
# (the history/bundle PRIMARY) rate-limits bursty parallel requests, so a wide
# fan-out of many heavy requests at once trips its throttle and cascades to
# "Could not load" across every name (the 2026-06-10 pre-open incident). Keep
# concurrency modest and stagger the thread starts to stay under the burst limit.
# These are operational knobs — safe to tune, not a recommendation/gate decision.
DATA_LOAD_MAX_WORKERS = 2     # was 4 — halve the simultaneous heavy requests to Yahoo
DATA_LOAD_STAGGER_SEC = 0.1   # gap between thread submits so starts aren't synchronized

# ── Reference-data shelf life (observability — NOT an investment threshold) ──
# How long a hand-maintained STATIC reference table stays trustworthy before the
# owner-only 🩺 System Trust page flags it for a human refresh. AWARENESS ONLY:
# nothing here gates a recommendation, suppresses a pick, or changes a score.
# The registry that says what each key refers to (and where the table lives) is
# `stock_analyzer/reference_shelf.py` — a test asserts these key sets and the
# registry stay in sync in BOTH directions, so a new table can't be registered
# without a shelf life and a shelf life can't outlive its table.
#
# Added 2026-08-15 after an audit found three tables drifting with no staleness
# detection and, in two cases, no recorded date at all — `SECTOR_UNIVERSE` (the
# ~70 names Grow Today scans DAILY) had not been refreshed since 2026-05-05.
#
# All four are 90 as of 2026-08-15 (three were tightened from 180 the same day
# they shipped, at the user's call: market leadership turns over fast enough in
# the current tech cycle that a semi-annual cadence lets a roster go visibly
# stale).
#
# On nag fatigue, stated precisely: tightening costs nothing on the HOME CHIP,
# because check ⑤ is excluded from it by design — that protects the chip's
# discriminating power. It does NOT make the check ⑤ page itself free. At 90d
# the page shows 3 of 6 amber today, 4 of 6 from 2026-08-27 and 5 of 6 from
# 2026-09-29 if nothing is refreshed, and a permanently-mostly-amber page is the
# same desensitization mechanism relocated. That cost is accepted deliberately:
# it self-corrects the moment the rosters are actually refreshed, which is the
# behaviour we want. Revisit if ⑤ ever gains a push surface, or if the page sits
# mostly-amber for months (which would mean the cadence is unrealistic, not that
# the tables are fine).
REFERENCE_SHELF_LIFE_DAYS = {
    "sector_universe":       90,   # scanned daily by Grow Today — highest leverage
    "discovery_universe":    90,   # matches its docstring's literal "quarterly is plenty"
    # Deliberately BELOW this table's own documented 6-12 month band (see the
    # source comment at portfolio.py's SP500_SECTOR_WEIGHTS): it's the one table
    # that renders a WRONG NUMBER rather than a silent absence, and sector
    # weights move fast in a concentrated market (Info Tech at 37.4%). Kept the
    # source comment's band as-is — that describes how often the *publisher*
    # revises it; this is how often WE want to re-check.
    "sp500_sector_weights":  90,
    "sector_candidates":     90,   # seeds ADD recs — a delisted name here is visible
}

# Minimum remaining runway (days) on a FORWARD-DATED table before it's flagged.
# Converts a hard expiry cliff into advance notice: the existing
# MARKET_CALENDAR_LAST_YEAR mechanism only warns AFTER the calendar has run out,
# by which point the app has already mis-scored holidays as trading days.
REFERENCE_HORIZON_MIN_DAYS = {
    "macro_event_calendar": 90,    # one quarter to hand-enter a year of releases
    "nyse_calendar":       365,    # extending 3 years of holidays is a rare chore
}

# Minimum share of the reference rosters that must RESOLVE in the weekly liveness
# sweep before its dead-ticker verdict is trusted at all (ticker_liveness.py, run
# from the Saturday maintenance cron lane).
#
# OBSERVABILITY KNOB — NOT an investment threshold. It gates whether a chore email
# is sent, never whether a pick is made or a gate fires.
#
# Why a batch-health floor rather than repeated confirmation over weeks: the false
# positive we're defending against is "the provider was rate-limited / down", and
# that failure hits the WHOLE batch at once, so it's measurable inside a single
# run. Confirming across runs instead would need persistence — coupling a
# roster-rot check to the very DB whose outage F-239 was about — and would delay a
# true finding by a week. Below this floor the sweep reports "inconclusive" and
# says so out loud; it never silently reports a clean bill of health.
#
# Value: with ~230 unique tickers, 90% tolerates ~23 simultaneous misses. Normal
# jitter is far below that (2026-08-16: one genuine dead name = 99.6%), while a
# real rate-limit event drops whole chunks of the batch well under it.
TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT = 90.0

# Minimum seconds between automatic retries of the initial Supabase load after
# it has failed (`db.should_attempt_db_reload`, consumed by app.py's startup
# load block). OPERATIONAL knob, NOT an investment threshold — it gates how
# often a blind app re-probes, never a pick or a gate. Retrying on every rerun
# would cost three Supabase reads with client timeouts on every widget
# interaction during a network outage; never retrying would leave the app
# stopped after Supabase recovered. The in-banner "Retry connection" button
# bypasses this, so the cooldown only shapes the AUTOMATIC path.
DB_RELOAD_RETRY_SEC = 30

# Pages that stay reachable when the initial DB load has failed and the app is
# otherwise hard-stopped (app.py's outage gate).
#
# This is a HARD-SUPPRESSION BOUNDARY, which is why it lives here rather than as
# a module-level tuple in app.py: `tests/` cannot import app.py, so this is the
# only place the "don't strand the user without a diagnostic" invariant can be
# mechanically pinned. Three defects on 2026-08-17 landed precisely where the
# suite can't reach — that is the reason for the placement, not a style
# preference.
#
# Both entries render NO portfolio state, so neither can misrepresent the book
# while the DB is unreadable. 🩺 System Trust is the page that diagnoses this
# exact outage — stopping before it is reachable would make the fix hide its own
# diagnostic. Do NOT add a page here that reads holdings/trades/watchlist.
DB_OUTAGE_SAFE_PAGES = ("🩺 System Trust", "📖 User Guide")

# Per-call wall-clock cap (seconds) on each yfinance request. yfinance exposes no
# request-level timeout, so a TCP-level hang would otherwise block until the OS
# socket timeout (minutes) or — in the headless cron — the 15-min job kill. The
# provider runs each yfinance call in a worker thread and abandons it past this
# cap so the orchestrator can fail over to Finnhub/FMP instead of hanging the
# whole page/run. Operational knob — NOT an investment threshold; set above a
# legitimately slow bundle (history+info+news+earnings) but well under the job
# budget. Tune from observation.
DATA_YF_REQUEST_TIMEOUT_SEC = 20

# Last-known-good bundle cache (data-resilience; bundle_cache table). When the
# history/bundle providers (Yahoo→FMP) ALL fail, load_all serves the last cached
# bundle (real data, aged) so the portfolio still renders WITH a staleness banner
# instead of "Could not load". Bounds how stale a displayed analysis may be —
# beyond this, fail loud rather than pass off very old signals as current. Mild
# policy flavour (stale data drives the shown signals); adjustable.
BUNDLE_CACHE_MAX_AGE_DAYS = 5

# ── Stock-split detection (split_detector.py) — data-integrity tuning, NOT an
# investment threshold. These gate whether an unaccounted split gets flagged
# for the user to apply, not any buy/sell/trim call. Were module-local
# literals in split_detector.py, invisible to check_constants_documented.py
# (2026-08-04 audit finding).
SPLIT_DETECT_LOOKBACK_DAYS    = 730   # 2 years of split history to fetch
SPLIT_DETECT_MIN_DISTORTION   = 0.35  # skip investigating unless |cost vs price| gap exceeds this
SPLIT_DETECT_MAX_ADJ_DISTANCE = 0.60  # adjusted cost must land within this fraction of current price to confirm

# Max fundamentals age (days) allowed for a new-position recommendation.
# Older fundamentals can make a deteriorating ticker appear composite-healthy.
# More conservative than FUNDAMENTALS_CACHE_MAX_AGE_DAYS (7) which governs
# held-position display — new-position recs carry higher trust expectations.
# Investment-policy constant: Opus review before changing.
GROW_TODAY_MAX_FUND_AGE_DAYS = 2

# Price cross-check tolerances. The cross-check compares the live-price primary
# (Finnhub, real-time) against an INDEPENDENT validator (yfinance, ~15-min
# delayed). Because of that latency the two checks have different strictness:
#   • prev_close is a SETTLED value — it must match across sources to within a
#     tight band; a breach means a real data-integrity fault (missed split,
#     wrong-symbol mapping, poisoned feed). This is the high-signal check.
#   • live price legitimately differs during fast intraday moves (delayed vs
#     real-time), so it's checked loosely — only a large gap (frozen/wrong feed)
#     trips it. Both are investment-policy values.
DATA_XCHECK_PREVCLOSE_TOL_PCT = 0.5   # strict — settled close should match
DATA_XCHECK_LIVE_TOL_PCT      = 3.0   # loose — live gaps expected (latency)

# Which fields are cross-checked. Everything else is failover-only — calling two
# sources for fundamentals/news would burn keyed free-tier quotas for little
# gain. Kept as a set for cheap membership tests in the orchestrator.
DATA_XCHECK_FIELDS = {"price"}

# How long FMP fundamentals (the `.info` backfill) are cached per ticker, in
# seconds. FMP's free tier is 250 calls/day and one fundamentals fetch is ~5
# calls; without a cache, every cache-miss reload of a sparse-yfinance ticker
# re-spends that. 1h comfortably covers a session's repeated analyses of the
# same name while staying fresh enough for daily fundamentals (which barely
# move intraday). Process-local cache — cleared on reboot or orchestrator reset.
DATA_FMP_INFO_CACHE_TTL_SEC = 3600

# FMP free-tier daily budget (250 calls/day on the free plan). The soft-cap
# pauses all FMP requests for the rest of the day, leaving a safety buffer
# before the hard limit. Operational knob — NOT an investment threshold.
FMP_DAILY_CALL_CAP = 250   # FMP free-plan hard limit
FMP_DAILY_SOFT_CAP = 220   # pause FMP at this count (30-call buffer)

# Minimum number of CORE fundamental metrics that must be present for the
# Business Quality leg — and therefore the composite verdict — to be trusted. The
# four core BQ metrics are revenue_growth, earnings_growth,
# profit_margins, debt_to_equity (see fundamentals.business_quality_score). When
# yfinance `.info` comes back empty AND no failover source can backfill it,
# zero of these are present, and business_quality_score returns a FABRICATED neutral
# 50 (points/max_points with max_points==0). The composite then emits a
# confident Buy/Hold on data we don't actually have — the exact "recommend
# wrongly" failure the operating posture forbids. Below this threshold the app
# WITHHOLDS the verdict (gates it, with a visible note) rather than guessing.
# Default 1 → gate only when fundamentals are entirely absent (the PINS/HUBS
# fabricated-50 case); raise to 2-3 to also withhold on thin/unreliable data.
# Policy value — changing it is an investment-policy decision.
FUNDAMENTALS_GATE_MIN_METRICS = 1

# Persistent last-known-good fundamentals cache (Supabase `fundamentals_cache`).
# When the live fundamental leg is unavailable (yfinance .info sparse AND FMP
# couldn't backfill), serve the last good copy rather than withholding — real
# data, transparently aged. This is the MAX age that fallback stays valid;
# beyond it the verdict is withheld again (stale-but-real has a limit). Policy
# value: forward_pe drifts daily with price, the rest are quarterly, so a week
# keeps the verdict materially correct. Changing it is an investment-policy call.
FUNDAMENTALS_CACHE_MAX_AGE_DAYS = 7

# ── NYSE market calendar (holiday awareness) ─────────────────────────────────
# Hardcoded NYSE full-day closures + 1pm early-close half-days, 2026–2028, by
# the OBSERVED date (weekend holidays move to the Fri/Mon NYSE actually closes).
# market_status() / is_trading_day() in data.py read these so the app stops
# showing "Market Open" on a holiday (e.g. Juneteenth, 2026-06-19). These are
# calendar FACTS, not decision gates. Source: official NYSE/ICE 2026–2028
# holiday & early-closings calendar (verified against nyse.com 2026-06-19);
# note 2026-07-03 is a 1pm early close — NYSE observes that Saturday-July-4 as a
# half day, not a full close — and 2028 has no New Year's (Jan 1 is a Saturday).
#
# ⚠️ EXTEND BEFORE 2029. When the system year exceeds MARKET_CALENDAR_LAST_YEAR,
# market_status() returns calendar_stale=True so the UI warns rather than
# silently treating future holidays as open trading days.
MARKET_CALENDAR_LAST_YEAR = 2028

# Shelf life: registered in stock_analyzer/reference_shelf.py — its horizon is DERIVED from MARKET_CALENDAR_LAST_YEAR, so extending the list here clears the warning automatically.
NYSE_HOLIDAYS = frozenset({
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    # 2028  (no New Year's Day — Jan 1 2028 is a Saturday, not observed)
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
})

# Observed date → early-close hour (ET, 24h float). NYSE trades until 1:00pm.
NYSE_EARLY_CLOSES = {
    "2026-07-03": 13.0, "2026-11-27": 13.0, "2026-12-24": 13.0,
    "2027-11-26": 13.0,
    "2028-07-03": 13.0, "2028-11-24": 13.0,
}

# ── Rate-limit resilience ────────────────────────────────────────────────────
# Cooldown (seconds) that the heavy refresh buttons (Refresh All Data / Refresh
# Signals / Grow Retry) stay disabled after a press. Each of those does a full
# st.cache_data.clear() + re-fetch across all price providers; hammering them
# exhausts the free-tier API budgets (the 2026-06-05 incident). Operational
# knob, not an investment threshold — safe to tune from observation.
REFRESH_COOLDOWN_SEC = 60

# Provider circuit-breaker (rate-limit-resilience Phase 2). Once a data provider
# trips "red" in api_health (rate_limits ≥ 3 or 5+ consecutive errors), the
# orchestrator SKIPS it for this many seconds rather than re-calling it on every
# ticker's cache miss and eating another 429 — which is what exhausts FMP's free
# tier and starves the last-known-good cache. Auto-recovers after the window; if
# ALL providers are cooled the orchestrator falls through and tries anyway (never
# a permanent hard-block). Operational infra knob — reversible, tune from
# observation; NOT an investment-decision threshold.
PROVIDER_RL_COOLDOWN_SEC = 120

# Today's Brief auto-refresh cadence — the Home freshness chip promises
# "auto-refreshes in N min" / "stale" off this same number (app.py). Ticking
# just re-runs the memoized synthesis against already-cached bundles (no
# st.cache_data.clear(), no scan_sectors() re-scan) so the incremental API
# cost is bounded to the existing 30-min load_all() TTL, not multiplied.
# Operational cadence knob, NOT an investment threshold.
BRIEF_AUTO_REFRESH_MINUTES = 30

# ── Advisory-AI request timeout (operational infra knob, NOT an investment
# threshold) ─────────────────────────────────────────────────────────────────
# Per-request wall-clock cap (seconds) on each Anthropic LLM call in the AI
# Intelligence layer (thesis review/authoring, weekly debrief, monthly report).
# Without it the SDK default (~10 min) can tie up the headless Sunday cron, which
# makes one call per open position. Advisory-only modules — a timeout just yields
# the offline/None fallback. Safe to tune from observation.
LLM_REQUEST_TIMEOUT_SEC = 30

# ── Recommendations-history scorecard ────────────────────────────────────────
# Minimum age (calendar days) before a surfaced rec's OUTCOME is scored on the
# Recommendations History page. A rec measured the day after it surfaces is just
# noise (one session of price wiggle); the aggregates (avg outcome / alpha /
# best / worst) only count recs at least this old. Younger recs still appear in
# the table flagged "maturing." This is a MEASUREMENT window for the retrospective
# scorecard — NOT a decision gate; it never affects what the engine recommends,
# only how long we wait before grading. Safe to tune from observation.
REC_SCORE_MIN_DAYS = 5

# Minimum number of MATURED graded entries before the Monthly Intelligence Report
# (F-4 / F-153) will narrate "entry quality" (question 0 — did the engine pick
# well?). Below this the entry-quality section is suppressed with a "not enough
# matured entries yet" note rather than narrating a trend off 1–2 data points.
# Like REC_SCORE_MIN_DAYS this is a MEASUREMENT floor for the retrospective report,
# NOT a decision gate — it never affects what the engine recommends, only whether
# the report has enough graded history to comment. Safe to tune from observation.
MONTHLY_REPORT_MIN_GRADED = 5

# ── Engine Track Record — 🎯 pointer card (🧾 Summary page) ──────────────────
# DISPLAY-ONLY band thresholds for the Engine Track Record pointer card.
# They control which band-label the card shows: "building" (below MIN),
# "early" (MIN–FIRM-1), or "firm" (at/above FIRM).  These are NOT investment
# decision gates — they never feed into the new-position pipeline, the
# composite score, or any recommendation engine.  Safe to tune from
# observation.
ENGINE_TRACK_MIN_CALLS = 8    # below → "building" band (no verdict shown)
ENGINE_TRACK_FIRM_CALLS = 15  # at/above → "firm" band; 8–14 → "early" band

# ── Engine Track Record — 🛡️ Defense facet (protective EXIT/TRIM calls) ──────
# DISPLAY-ONLY band thresholds for the Defense facet of the same pointer card,
# mirroring ENGINE_TRACK_MIN_CALLS/ENGINE_TRACK_FIRM_CALLS above but scoped to
# distinct flagged tickers (EXIT/TRIM) instead of acted BUY calls. These are
# NOT investment decision gates — they never feed into any alert, the exit
# advisor, or the composite score. Safe to tune from observation.
PROTECT_TRACK_MIN_CALLS = 8    # below → "building" band (no verdict shown)
PROTECT_TRACK_FIRM_CALLS = 15  # at/above → "firm" band; 8–14 → "early" band

# ── Self Track Record ("is my own instinct good?", MEASUREMENT-ONLY) ─────────
# Answers a DIFFERENT question than the Engine Track Record card above ("is
# the engine good?") — this measures the user's own self-initiated BUYs
# against the ones that followed an app recommendation. NEVER gates, sizes, or
# suppresses a recommendation; reuses BEHAVIORAL_MIN_SAMPLE_N (above) for its
# sample-size floor rather than a parallel constant, same reuse precedent as
# Personalized Discovery / The Judge for that exact constant.
#
# A BUY counts as `app_aligned` only if a matching recommendation ("new_pick"
# or "buy_candidate") exists within this many days before (inclusive) the
# trade date — a rec from further back is a distinct, later decision, not
# "following" the rec. Mirrors the same-day-only philosophy of
# recommendations_history.match_recs_to_trades, widened slightly because a
# self-initiated buy naturally lags a day or two behind noticing the rec
# (unlike the RECOMMENDATION trigger_type flow, which is same-day by
# construction).
SELF_TRACK_MATCH_LOOKBACK_DAYS = 3
# Ship date of the cron-side recommendation-logging fix (item 3 of the Self
# Track Record build — cron_runner.py._run_scan now persists today's new_pick
# rows even when no interactive Streamlit session ran that day). Before this
# date, an in-scope ticker (universe or watchlist) bought with no matching rec
# on file is AMBIGUOUS — coverage could be missing rather than the buy
# genuinely being self-initiated — so it's bucketed `coverage_limited`
# (disclosed, never graded either way). On/at this date and after, missing
# coverage is a real absence, not a logging gap, so the same shape is
# bucketed `self_in_scope` and graded. Boundary is inclusive (`>=`).
SELF_TRACK_RELIABLE_LOG_START = date(2026, 8, 6)

# ── Predictive Analytics — Signal Calibration ─────────────────────────────────
# Minimum number of mature outcomes in a composite-score band before the band
# is shown in the Signal Calibration chart. Below this, the band is labelled
# "Too few" to avoid misleading averages on 1-2 data points.
# Measurement floor only — NOT a decision gate.
PREDICTIVE_MIN_BAND_N = 5

# Width of each composite-score band in the calibration chart (5-point gives
# enough granularity to reveal where personal edge starts without producing
# too many empty bands on a small history). Safe to tune from observation.
PREDICTIVE_SCORE_BAND_SIZE = 5

# ── Predictive Analytics — Entry Timing (Phase 1, docs/plans/entry-timing-tab.md) ─
# All three below are PROVISIONAL — fit to a single anecdote (AMD firing as a
# new_pick 5x in 2 weeks in 2026-07, composite 69-71 vs momentum 94-100, every
# firing losing ~9-11% / alpha -7pp to -10pp vs SPY), not yet fit to the real
# divergence-band distribution. Re-check against production data before
# treating as tuned. Diagnostic-only measurement knobs — this tab never feeds
# back into the composite score or the 5-gate new-position pipeline.

# Calendar days a same-ticker new_pick re-firing must fall within a prior kept
# firing to be treated as the same opportunity (collapsed), not a new one —
# the daily scanner re-evaluating the whole universe re-fires unbought names
# repeatedly; that's correct engine behavior, not N independent data points.
ENTRY_TIMING_DEDUP_WINDOW_DAYS = 5

# Divergence = momentum_score - composite_score at the moment a new_pick fires.
# Upper bound (inclusive) of the "Aligned" band — momentum roughly tracks the
# composite consensus.
ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX = 15

# Upper bound (inclusive) of the "Diverging" band — above this is "Extreme"
# (momentum running far ahead of a barely-qualifying composite, the AMD
# pattern this tab was built to surface).
ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX = 25

# ── My Edge — Decision Quality grading (measurement policy, NOT investment gates) ─
# Grade thresholds define how the retrospective decision-quality grades are
# assigned per calendar month. Aligned with Portfolio Health grade bands so
# that "A" means the same thing across both scoring surfaces. Safe to tune
# from observation. These are measurement floors only — they never affect
# what the engine recommends or what gates fire.
DECISION_QUALITY_GRADE_A = 80        # composite ≥ 80 → A (Elite)
DECISION_QUALITY_GRADE_B = 65        # composite ≥ 65 → B (Disciplined)
DECISION_QUALITY_GRADE_C = 50        # composite ≥ 50 → C (Learning)
DECISION_QUALITY_GRADE_D = 35        # composite ≥ 35 → D (Struggling); below = F
DECISION_QUALITY_MIN_TRADES = 2      # min closed trades to compute a period grade
DECISION_QUALITY_ALPHA_SCALE = 5.0   # ±X% realized alpha maps to 100/0 on alpha subscore
# Win-rate subscore: linear map, floor% -> 0, ceiling% -> 100. (2026-08-04
# audit finding: these two plus the profit-factor/overtrading bounds below
# were bare literals in decision_quality.py, unlike the properly-externalized
# grade bands above.)
DECISION_QUALITY_WIN_RATE_FLOOR_PCT   = 30.0
DECISION_QUALITY_WIN_RATE_CEILING_PCT = 70.0
# Profit-factor subscore: linear map, floor -> 0, ceiling -> 100.
DECISION_QUALITY_PF_FLOOR   = 0.5
DECISION_QUALITY_PF_CEILING = 2.0
# Overtrading penalty on the composite score: month's trade count vs its
# rolling 12-month prior average, at 2 severity tiers.
DECISION_QUALITY_OVERTRADE_SEVERE_MULT      = 2.0    # count >= this x the prior average
DECISION_QUALITY_OVERTRADE_MODERATE_MULT    = 1.5
DECISION_QUALITY_OVERTRADE_SEVERE_PENALTY   = 25.0   # points subtracted from composite
DECISION_QUALITY_OVERTRADE_MODERATE_PENALTY = 10.0

# ── My Edge — Workflow ROI prep-tier classification windows ──────────────────
# Lookback/proximity windows used to classify each BUY trade by how much
# in-app research was done before entry. Measurement only — never alters
# what the engine recommends. Safe to tune from observation.
WORKFLOW_ANALYST_LOOKBACK_DAYS = 90  # analyst research saved within N days BEFORE trade counts
WORKFLOW_EARNINGS_WINDOW_DAYS  = 30  # earnings context saved within N days BEFORE trade counts (before-only; post-trade research is not pre-entry prep)
WORKFLOW_MIN_THESIS_LENGTH     = 10  # min chars in user_thesis to count as a prep signal

# ── My Edge — Behavioral Fingerprint (Concept A, F-193 — DISPLAY-ONLY, NEVER gates) ─
# Sample-size floor + a directly-cited window from the plan's own illustrative
# example. Buy-side only for v1 (exit-side TRIM/EXIT signals have no historical
# capture — see docs/plans/next-evolution-strategy.md Concept A). Every pattern
# card is suppressed below BEHAVIORAL_MIN_SAMPLE_N in EITHER compared bucket —
# never present a directional finding at small N. Measurement only; the engine
# never reads these values, and they never re-rank/re-score/gate a recommendation.
BEHAVIORAL_MIN_SAMPLE_N       = 8    # min N per compared bucket before a pattern renders
BEHAVIORAL_OPENING_WINDOW_MIN = 30   # minutes after 9:30 ET considered "the opening window"
# Below these, two buckets render as "little/no difference" rather than a directional
# claim — display-copy wording only, never a threshold that suppresses/gates a card
# (that's BEHAVIORAL_MIN_SAMPLE_N's job). Two separate constants because the two
# patterns compare different units (action-rate pp vs. SPY-adjusted alpha pp).
BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP = 5.0  # momentum-chasing / conviction-tier patterns
BEHAVIORAL_MEANINGFUL_ALPHA_DELTA_PP       = 1.0  # opening-window pattern
# Exit-side: how many calendar days after a signal a SELL counts as "acted on"
EXIT_SIGNAL_ACT_WINDOW_DAYS                = 7

# ── Tax-awareness lens (Concept F — DISPLAY-ONLY policy, NEVER gates) ─────────
# Holding-period / harvest / wash-sale context layered onto EXIT signals and the
# opportunity-cost read. These NEVER suppress, reorder, or size a recommendation
# — the investment signal is unchanged; tax context is a visible annotation only
# (the G-08 HARVEST gate on the Buy side is the one place tax interacts with a
# recommendation, and it lives in tax_advisor, not here). Rates are US
# high-bracket defaults and are estimates — actual tax depends on full-year
# income, state tax, other realized losses, and lot accounting method, so every
# surface frames these as directional, never a precise liability. Single-sourced
# here so the tax_advisor module and the exit-card lens agree.
TAX_RATE_SHORT_TERM       = 0.37   # STCG estimate (top federal bracket default)
TAX_RATE_LONG_TERM        = 0.20   # LTCG estimate (top federal bracket default)
TAX_STCG_THRESHOLD_DAYS   = 366    # IRS: held > 1 year (i.e. ≥ 366 days) = long-term
TAX_HARVEST_MIN_LOSS      = 500    # min unrealized loss ($) before a HARVEST is surfaced
TAX_LTCG_WAIT_WINDOW_DAYS = 60     # STCG gain within N days of LT eligibility → "WAIT" (Tax Advisor page)
TAX_LONGTERM_WINDOW_DAYS  = 30     # EXIT/TRIM within N days of LT eligibility → amber "waiting cuts tax drag" note
TAX_WASH_SALE_DAYS        = 30     # IRS wash-sale window (fixed by law) — SELL within N days of a same-ticker add flags the disallowed-loss risk

# ── Investor Mirror (F-194 — DISPLAY-ONLY policy, NEVER gates) ───────────────
# Conviction Alignment + Behavioral Bias analytics on 🎯 My Edge → 🪞 Investor
# Mirror tab. All ten constants are display-copy thresholds only — they control
# which descriptive sentence renders in a card (same class as
# BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP). None suppress, reorder, or gate
# any recommendation.
INVESTOR_MIRROR_MIN_CLOSED_LOTS = 10    # min matched sell-lots per comparison group (winners AND losers each need ≥ this in disposition_effect; total ≥ this in anchoring/ratio)
INVESTOR_MIRROR_MIN_POSITIONS   = 5     # min held positions with a valid Score for alignment score
CONVICTION_ALIGNMENT_LOW        = 0.30  # Spearman ρ below this → "random" label
CONVICTION_ALIGNMENT_HIGH       = 0.60  # Spearman ρ above this → "disciplined" label
DISPOSITION_CONCERN_RATIO       = 1.5   # holding losers ≥ this × longer than winners → concern note
WINLOSS_CONCERN_RATIO           = 2.0   # closing ≥ this × more winners than losers → loss-aversion note
# Conviction alignment pattern boundaries — mirrors the existing composite-tier
# vocabulary but tuned to the alignment patterns (not entry/exit gates)
CONVICTION_WEAK_SCORE           = 50    # Accidental Overexposure: Score below this + overweight → mis-aligned
CONVICTION_FADED_SCORE          = 60    # Legacy Overhang: top-N position with Score below this → fading conviction
CONVICTION_LEGACY_TOP_N         = 3     # how many largest-weight positions to inspect for legacy overhang
BREAKEVEN_ANCHOR_DWELL_RATIO    = 1.3   # anchoring flag if -2–0% bracket avg_days ≥ this × adjacent-loss-brackets mean
# Premature-Exit Cost (O6, Agentic Intelligence Roadmap v2, 2026-07-26) — sizing_alpha
# (O5) reuses INVESTOR_MIRROR_MIN_CLOSED_LOTS above, no new constant needed there.
PREMATURE_EXIT_RATIO            = 0.5   # winner held < this × own avg winner-hold days → "quick exit" bucket
PREMATURE_EXIT_MIN_LOTS         = 5     # min winning lots per quick/patient bucket — feature-specific, NOT INVESTOR_MIRROR_MIN_CLOSED_LOTS (that floor is sized for a larger, non-winners-only population; reusing it here would likely leave the card permanently dark)

# ── Thesis Red Team Agent ─────────────────────────────────────────────────────
# Awareness-only adversarial layer: never modifies composite score, gate
# decisions, or any recommendation. Phase 1 = quantitative erosion score
# only (no LLM); Phase 2 = Haiku counter-evidence; Phase 3 = Daily Brief
# annotation. See docs/plans/thesis-red-team-agent.md.
THESIS_EROSION_HAIKU_MIN  = 30   # min erosion score to trigger Haiku counter-evidence (Phase 2)
THESIS_EROSION_BRIEF_MIN  = 50   # erosion score threshold for Daily Brief annotation (Phase 3)
THESIS_EROSION_BRIEF_JUMP = 15   # same-day score jump that triggers Brief annotation (Phase 3)
THESIS_EROSION_BASELINE_LOOKBACK_DAYS = 10   # calendar days to walk back for the Phase 3 baseline row before treating a ticker as a first-ever observation

# ── State of the Portfolio standing thesis (portfolio_thesis.py) ─────────────
# Weekly 5-claim stability ledger (HELD/SHIFTED/not_comparable) on 🧾 Summary —
# never a predictive score. See docs/plans/state-of-portfolio-standing-thesis.md.
PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS = 14   # calendar days to walk back for the most recent prior thesis row before grading as "nothing to compare yet" — mirrors THESIS_EROSION_BASELINE_LOOKBACK_DAYS's precedent, sized to cover exactly one missed week of app visits (weekly cadence)

# ── Day Shock awareness banner (Home) ─────────────────────────────────────────
# Flags any held position moving ≥ this % (up or down) same-day, independent of
# classify_deterioration_tier's trend-break condition. AWARENESS ONLY — never
# alters WATCH/TRIM/EXIT tier or any recommendation; exists so a single-day
# shock that stays above the 50-day MA (and so doesn't trip the tier) is still
# visible instead of silently absorbed into the price strip's per-ticker badges.
DAY_SHOCK_PCT = 5.0   # abs same-day % move (up or down) that triggers the banner

# ── Outcome Range simulator (monte_carlo.py) ──────────────────────────────────
# Historical block-bootstrap Monte Carlo on the 🎲 Outcome Range tab (Risk
# Analysis). Resamples REAL historical daily returns (not regime labels — the
# daily_regime table has too little history for a base rate, see F-200's
# retreat from that framing) to produce a percentile-band distribution of
# portfolio outcomes. AWARENESS/DIAGNOSTIC ONLY — same class as Stress Testing
# and Regime Fit; never gates a recommendation. These are simulation-method
# parameters, not investment-policy thresholds, but still live here per the
# no-hardcoded-values rule.
MC_HISTORY_PERIOD      = "5y"   # yfinance/orchestrator period string for the long-history fetch
MC_MIN_HISTORY_DAYS    = 252    # min trading days of history a ticker needs to join the bootstrap (~1yr); shorter-history tickers (e.g. recent IPOs) are excluded and reported, weights renormalized among the rest
MC_TRIALS              = 2000   # number of bootstrap trials
MC_BLOCK_DAYS          = 20     # contiguous block length (trading days, ~1 month) sampled together across ALL tickers to preserve realized cross-ticker correlation
MC_HORIZON_OPTIONS_DAYS = [21, 63, 252]   # selectable simulation horizons (~1mo/1qtr/1yr, trading days)
MC_HORIZON_DEFAULT_DAYS = 63    # default horizon selection

# ── Portfolio Q&A (portfolio_qa.py, 💬 Ask tab on AI Insights) ────────────────
# Retrospective natural-language Q&A over trade history + past recommendations
# (NOT a live session_state reader — see docs/plans/portfolio-qa.md). These are
# query-scoping parameters, not investment-policy thresholds, but still live
# here per the no-hardcoded-values rule.
QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS = 5     # trading days after surfacing to check price outcome, when the question doesn't specify one
QA_MAX_RANGE_DAYS                   = 365   # widest date range a "trades in range" question may query, so an open-ended range can't fan out into an unbounded price-history fetch
QA_REC_OUTCOME_WIDE_FETCH_DAYS      = 330   # rec age (calendar days) past which the price-history fetch widens from 1y to 2y, so an old recommendation gets an honest outcome instead of misreporting "not enough forward history" when the real cause was a too-short fetch window
QA_HISTORY_TURNS                    = 3     # most-recent Q&A exchanges fed back into the parser as conversation context, so a referential follow-up ("what about MSFT?") can resolve — bounded to keep prompt size/cost predictable
QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS = 3    # window (calendar days on/after a recommendation's surface date) to search for the BUY trade it was acted on by, for the Pre-Mortem cross-reference in rec_outcome answers — narrow and explicit rather than guessing across a wider span

# ── Personalized Discovery (personalized_discovery.py, Grow Today + Behavioral
# Fingerprint) ────────────────────────────────────────────────────────────────
# Runs Behavioral Fingerprint's (F-193) backward-looking analysis FORWARD:
# builds a "winner profile" from the user's own REALIZED winning trades
# (build_closed_lots(), is_gain=True, joined to a matched acted-on new_pick/
# add_winner recommendation at entry) and flags which of today's already-
# gated Grow Today picks resemble it. Zero new fetches — entirely a replay of
# already-loaded trades/recommendations. AWARENESS/DIAGNOSTIC ONLY: never
# changes which tickers clear the 5-gate _grow_today() pipeline, never
# re-scores or re-ranks a pick, never suppresses a non-matching one. Reuses
# the EXISTING BEHAVIORAL_MIN_SAMPLE_N (above) for the min-sample withhold
# floor rather than a parallel constant — same module family, same sample-size
# philosophy. These are new policy/method thresholds proposed alongside the
# feature — confirm before tuning, same as any other value in this file.
PERSONALIZED_DISCOVERY_MIN_MATCH_TRAITS   = 2    # of 3 traits (composite band / momentum band / top sector) that must match before the "matches your winning profile" caption renders on a Grow Today pick
PERSONALIZED_DISCOVERY_PROFILE_PCTL_LOW   = 25   # lower percentile of past winners' composite/momentum scores defining the "typical winner" band
PERSONALIZED_DISCOVERY_PROFILE_PCTL_HIGH  = 75   # upper percentile of the same band

# ── Judgment layer ("The Judge", docs/plans/judgment-layer.md) ─────────────────
# Phase 0/1 only: opinion-tagging + a read-only, no-authority reconciliation
# ("🧑‍⚖️ The Judge" page). These are the "-1..+1 signal cutpoints" and the
# veto/contradiction boundaries the Opus design review (2026-08-03) required to
# live here rather than as inline literals — same investment-policy-adjacent
# reasoning as any other threshold in this file, even though Phase 1 has no
# authority to act on them yet. Confirm before tuning.
JUDGMENT_EXIT_SIGNAL_MAP = {
    "WATCH": -0.3, "TRIM": -0.6, "EXIT": -0.9, "RISK_OFF": -0.9,
}  # exit_advisor deterioration tier -> normalized position_health signal
JUDGMENT_FRAGILITY_SIGNAL_MAP = {
    "calm": 0.3, "caution": -0.3, "fragile": -0.8,
}  # fragility_gauge severity -> normalized structural_risk signal
JUDGMENT_VERDICT_SIGNAL_MAP = {
    "go": 0.8, "verify": 0.0, "caution": -0.4, "skip": -0.9,
}  # signal_reconciliation verdict tier -> normalized quality signal
JUDGMENT_CONCENTRATION_BREACH_SIGNAL      = -0.8  # at/above SINGLE_NAME_CEILING or SECTOR_CEILING
JUDGMENT_CONCENTRATION_NEAR_BREACH_SIGNAL = -0.3  # at/above this fraction of either ceiling but not yet breached
JUDGMENT_CONCENTRATION_NEAR_BREACH_RATIO  = 0.8   # "near" = 80% of the hard ceiling
JUDGMENT_CONCENTRATION_CLEAR_SIGNAL       = 0.3   # below the near-breach ratio on both ceilings
JUDGMENT_VETO_PROTECTIVE_THRESHOLD     = -0.4  # a protective-dimension opinion at/below this vetoes EVERY same-ticker positive acquisitive opinion outright (never blended) — the most severe (lowest-signal) protective opinion wins when more than one qualifies
JUDGMENT_CONTRADICTION_MIN_MAGNITUDE   = 0.3   # minimum |signal| for a same-dimension, opposite-sign opinion pair to be flagged as a contradiction — avoids flagging near-neutral noise as a real conflict
JUDGMENT_SCORE_MIDPOINT = 50.0  # 0-100 score-scale midpoint used to normalize composite_score/scanner_momentum to the -1..+1 opinion signal range: (score - midpoint) / midpoint

# Phase 2 (grading harness) — per-dimension horizon (trading days from signal_date
# before an opinion is checked against realized outcome) and the shared min-sample
# gate. Horizons chosen to match each dimension's natural timescale: momentum is a
# short-term technical read (checked fastest, mirrors the existing Entry Timing
# tab's Day+5 precedent); quality/composite is a longer fundamental thesis
# (Day+20, same horizon Entry Timing already uses for composite-band grading);
# position_health/structural_risk are near-term protective reads (Day+10);
# concentration is graded on the same longer horizon as quality since concentration
# risk (a name/sector becoming overweight) plays out over a similar timescale to a
# fundamental thesis, not a single-day shock. Confirm before tuning (2026-08-03
# horizon proposal: user confirmed momentum/quality/position_health explicitly;
# concentration/structural_risk paired to the closest existing precedent — flag if
# these two feel wrong once real data accumulates).
JUDGMENT_HORIZON_MOMENTUM_DAYS         = 5
JUDGMENT_HORIZON_QUALITY_DAYS          = 20
JUDGMENT_HORIZON_POSITION_HEALTH_DAYS  = 10
JUDGMENT_HORIZON_CONCENTRATION_DAYS    = 20
JUDGMENT_HORIZON_STRUCTURAL_RISK_DAYS  = 10
# Reuses BEHAVIORAL_MIN_SAMPLE_N (above) rather than a parallel constant — same
# n-before-a-pattern-counts philosophy, and the same reuse precedent Personalized
# Discovery already established for this exact constant. See judgment-layer.md Q2.

# Phase 3 (evidence-based weighting) — converts a witness's track record
# (track_record_summary(), gated on BEHAVIORAL_MIN_SAMPLE_N same as above) into
# a weight multiplier applied ONLY inside the confidence-weighted blend — the
# protective veto and the contradiction-audit magnitude floor stay hard gates,
# never softened by track record (that would silently re-litigate an existing
# hard suppression into a vote, the exact structural hole the Q1 design review
# caught). multiplier = accuracy / NEUTRAL_ACCURACY, clamped to [FLOOR, CEILING]
# — 50% accuracy (coin-flip) is neutral (1.0x, identical to today's equal-weight
# behavior); until a source×dimension pair clears the min-sample gate it stays
# at 1.0x regardless of its thin observed accuracy. User confirmed the moderate
# 0.25x-2.0x band (2026-08-03): a strong track record can up to double a
# witness's say, a poor one drops it to a quarter, but no witness is ever fully
# silenced or allowed to dominate the blend alone.
JUDGMENT_TRACK_RECORD_NEUTRAL_ACCURACY = 0.5
JUDGMENT_TRACK_RECORD_WEIGHT_FLOOR     = 0.25
JUDGMENT_TRACK_RECORD_WEIGHT_CEILING   = 2.0

# ── Compare page — 2-ticker verdict tie-break sensitivity (comparison.py) ────
# When composite scores are close (COMPARE_TIE_GAP), the verdict defers to
# these sub-factor gaps to cite specific tie-break evidence rather than
# picking arbitrarily. Were bare literals (2026-08-04 audit finding).
COMPARE_TIE_GAP           = 3     # composite-score gap below which scores count as "nearly identical"
COMPARE_FCF_YIELD_GAP_PCT = 0.5   # FCF-yield gap (percentage points) worth citing as a tie-breaker
COMPARE_BETA_GAP          = 0.15  # beta gap worth citing as a tie-breaker
COMPARE_SHARPE_GAP        = 0.2   # Sharpe gap worth citing as a tie-breaker

# ── Sentiment Velocity (sentiment_velocity.py) ────────────────────────────────
# Were module-local literals (2026-08-04 audit finding).
SENTIMENT_VELOCITY_THRESHOLD    = 0.10  # compound-score shift considered meaningful (Improving/Deteriorating label)
SENTIMENT_DIVERGENCE_PRICE_PCT  = 3.0   # 7-day price move % needed to flag a price-sentiment divergence
SENTIMENT_VELOCITY_MIN_ARTICLES = 4     # min articles required to compute a velocity read at all

# ── Pre-market intelligence (premarket.py) ────────────────────────────────────
# Were module-local literals (2026-08-04 audit finding).
PREMARKET_FUTURES_TONE_PCT = 0.4  # ES=F % change cutoff for bull/bear/flat futures_tone()
PREMARKET_MOVER_MIN_PCT    = 0.5  # min |% change| for a held/watchlist ticker to qualify as a pre-market mover

# ── Quick Research entry-timing verdict (quick_research._entry_timing) ──────
# Directly actionable ("High Risk — Avoid Chasing" / "Wait for Pullback" /
# "Oversold — Potential Entry" / "Normal Entry Conditions"). Were bare
# literals despite driving a user-facing verdict (2026-08-04 audit finding).
QUICK_RESEARCH_RSI_SEVERE_OVERBOUGHT = 80   # RSI >= this -> "High Risk — Avoid Chasing"
QUICK_RESEARCH_MOVE_1D_EXTREME_PCT   = 15   # 1-day move % >= this -> same tier
QUICK_RESEARCH_MOVE_5D_EXTREME_PCT   = 25   # 5-day move % >= this -> same tier
QUICK_RESEARCH_RSI_ELEVATED          = 68   # RSI >= this -> "Wait for Pullback"
QUICK_RESEARCH_MOVE_1D_ELEVATED_PCT  = 5    # 1-day move % >= this -> same tier
QUICK_RESEARCH_MOVE_5D_ELEVATED_PCT  = 12   # 5-day move % >= this -> same tier
QUICK_RESEARCH_RSI_OVERSOLD          = 35   # RSI <= this -> "Oversold — Potential Entry"

# ── Trade Journal entry-sanity guards (app.py) — anti-fat-finger, not a
# gate/scoring value. Were bare literals despite the same form correctly
# importing RR_ENTRY_MIN/COMPOSITE_BUY/etc. from here (2026-08-04 audit
# finding).
TRADE_PRICE_SANITY_FLOOR      = 0.10  # entered price below this = probable typo, blocks submit
TRADE_PRICE_SANITY_RATIO_LOW  = 0.5   # entered-price / live-market-price below this = probable typo
TRADE_PRICE_SANITY_RATIO_HIGH = 2.0   # ...above this = probable typo
TRADE_DUP_SUBMIT_WINDOW_SEC   = 15    # identical (ticker, action, shares) resubmit within this window = dedup guard

# ── Alpha Attribution activation gate (app.py, AI Insights panel) — the
# panel is inert until enough daily_snapshots history accumulates to
# decompose realized alpha meaningfully. Was 8 bare literal copies of "180"
# (2026-08-04 audit finding); feature not yet active.
ALPHA_ATTRIBUTION_MIN_SNAPSHOT_DAYS = 180

# ── Predictive Modeling Shadow Layer — Phase 1 (F-234, MEASUREMENT-ONLY) ──────
# Model/scoring PARAMETERS for the quarantined 🔬 Model Lab page — NOT
# investment-decision gates. Nothing in this layer feeds any gate,
# recommendation, or the composite score (see docs/plans/
# predictive-modeling-shadow-layer.md). Still living here per hard rule #1
# (every threshold/parameter lives in constants.py, even a non-gating one)
# and still requiring an Opus review citation before ship, since staging
# constants.py trips that requirement mechanically regardless of the
# non-gating status.

# Forecast target: 20-trading-day forward realized volatility (annualized),
# per held ticker + the portfolio aggregate. Matches the maturation cron's
# "made_at + horizon_days" window and the backfill script's target-window
# length.
VOL_FORECAST_HORIZON_DAYS = 20

# RiskMetrics' fixed EWMA decay factor for the v1 volatility forecaster
# (forecast_vol_ewma). NOT fitted to this app's data — a fixed classical
# constant, so v1 carries no backtest-leakage risk the way a fitted model
# (GARCH-MLE, gradient-boosted trees) would if it were ever backfilled.
VOL_FORECAST_EWMA_LAMBDA = 0.94

# Minimum matured (realized_value populated) model_predictions rows before
# prediction_scoring.score_predictions() will report a real skill_score
# number — below this, skill is withheld (None), same "not yet meaningful"
# discipline as ENGINE_TRACK_MIN_CALLS / BEHAVIORAL_MIN_SAMPLE_N elsewhere.
# Also reused (not a parallel constant) as the floor for skill_score_live_only,
# so the live-only headline can't be inflated by a handful of live rows either.
PREDICTION_MIN_MATURED_N = 20

# Depth of the one-off backfill script's price-history fetch, per-ticker scope
# only (§1.6b — portfolio-scope backfill needs actual historical weights,
# bounded to known `trades` history, not 5 years, and is deliberately NOT
# built in this script). Matches the existing MC_HISTORY_PERIOD constant/
# fetch-path precedent (Outcome Range simulator) rather than inventing a new
# one. v1 (vol_forecast_ewma) has no fitted parameters, so a 5-year backfill
# carries no in-sample leakage risk the way a fitted model would.
PREDICTION_BACKFILL_PERIOD = "5y"

# ── SnapTrade broker integration (Robinhood sync) ─────────────────────────
# See docs/plans/snaptrade-broker-integration.md for full design. Position
# drift, balance sync and transaction import all read these; none of them
# is an investment-decision gate, but they govern data-integrity behaviour
# (staleness banners, dedup tolerance, fetch bounds) so they live here per
# hard rule #1 rather than as module-local literals.

# Assumed ET fill time for an IMPORTED trade that carries a date but no time
# (broker sync / CSV / RH-text). Those writers send a bare date, Postgres casts
# it to midnight UTC in a `timestamptz` column, and midnight UTC is the PRIOR
# EVENING in ET — so every `tz_convert("America/New_York")` reader dated the
# trade a day early. See `stock_analyzer/trade_time.py`.
#
# CONSTRAINED, not free: must be >= 0 and < 19, or the anchor changes the UTC
# calendar day too, silently re-dating every UTC-date reader — including
# tax_advisor's lot dates and broker_sync's dedup key. 16:00 ("market close")
# is honest for an unknown fill time and sorts imports after the regular
# session. `tests/test_trade_time.py` pins the safe band so a future edit past
# it fails loudly instead of quietly moving a tax lot.
IMPORTED_TRADE_ANCHOR_ET_HOUR = 16

# Share-count tolerance before a held ticker counts as a real quantity
# mismatch rather than rounding noise. Absorbs fractional-share noise from
# SnapTrade's position feed vs the app's trades-derived shares — not a real
# drift signal below this size.
# TWO consumers, deliberately sharing one number because it is one question
# ("is this share difference real or float noise"), not two policies:
#   • broker_sync.diff_positions()      — broker feed vs the app's holdings
#   • daily_pnl.reconcile_baseline()    — prior-close baseline vs holdings
# Retuning this for the broker feed also moves the day-P&L integrity guard.
BROKER_DRIFT_SHARE_TOL = 0.001

# Max age of the last successful SnapTrade balance sync before the Account
# page shows a stale-data banner rather than silently trusting an old
# account_cash row. 25h (not 24h) mirrors the existing daily-cron-lane
# staleness convention elsewhere (a one-hour buffer past a strict 24h cycle
# absorbs normal cron-fire jitter without falsely flagging stale).
SNAPTRADE_BALANCE_STALE_HOURS = 25

# Bounds the broker cron's transaction-history fetch window (days back from
# now). Prevents an unbounded historical pull on first connect or after a
# long SnapTrade/cron outage — anything older falls outside sync scope and
# is expected to already be in trades via manual/CSV entry.
SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS = 90

# Per-call wall-clock timeout for the SnapTrade client wrapper. Same
# operational-cap convention as DATA_YF_REQUEST_TIMEOUT_SEC — bounds a
# single hung call so the broker cron lane can fail loud instead of
# blocking the job budget.
SNAPTRADE_REQUEST_TIMEOUT_SEC = 15
