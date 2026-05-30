"""
Decision constants — single source of truth for every threshold the app uses
to issue, suppress, or downgrade a recommendation.

The app operates in "decides" mode: thresholds here translate directly into
hard gates on what the user is told to do, so changes here are investment
policy decisions, not code tuning. When changing any value below, update
project_decision_thresholds.md (memory) with the rationale.
"""

# ── Portfolio beta ───────────────────────────────────────────────────────────
PORTFOLIO_BETA_TARGET   = 1.0    # baseline equity-portfolio target
PORTFOLIO_BETA_ELEVATED = 1.3    # soft warning above this
PORTFOLIO_BETA_CEILING  = 1.4    # hard breach above this — institutional ceiling

# ── Ticker beta (combined with portfolio beta for gating) ────────────────────
TICKER_BETA_HIGH     = 1.5       # "high beta" — soft warn when added to elevated port
TICKER_BETA_CRITICAL = 1.8       # "very high beta" — hard breach when added to breached port

# ── Concentration limits ─────────────────────────────────────────────────────
SECTOR_CEILING    = 35.0         # hard sector cap (% of portfolio)
SECTOR_ELEVATED   = 25.0         # soft warn above this
SINGLE_NAME_CEILING = 15.0       # hard single-name cap — no add-to-winner above this

# ── Composite scoring boundaries ─────────────────────────────────────────────
# scoring.recommendation() uses these to assign the Strong Buy / Buy / Hold /
# Sell / Strong Sell label that surfaces across the app. Every gate and filter
# that talks about "Buy" or "Strong Buy" must import from here so the label
# the user sees on Analysis matches the gate Grow Today / Brief verdicts use.
COMPOSITE_STRONG_BUY = 75        # Strong Buy boundary
COMPOSITE_BUY        = 65        # Buy boundary — entry + add-to-winner gates
COMPOSITE_HOLD       = 44        # Hold floor — below this = Sell zone
COMPOSITE_SELL       = 30        # Sell floor — below this = Strong Sell

# Conviction tiers (Grow Today new-pick label only — not a hard gate).
# A pick that clears COMPOSITE_BUY but doesn't yet reach STRONG_BUY is
# "moderate" conviction; STRONG_BUY+ is "high."
COMPOSITE_HIGH_CONVICTION = COMPOSITE_STRONG_BUY

# Stricter Grow Today bar on flat market days — only the highest-quality
# setups clear when the index isn't providing tailwind.
COMPOSITE_BUY_FLAT_DAY = 78

# ── Risk per trade (position sizing) ─────────────────────────────────────────
RISK_PCT_PER_TRADE = 0.015       # 1.5% portfolio risk per trade (Moderate)

# ── Add-to-winner / approaching-stop boundaries ──────────────────────────────
# A position must be at least this far above its stop before Grow Today will
# recommend adding to it (same threshold also marks the "Approaching Stop"
# Review-Before-Close bucket).
ADD_WINNER_MIN_GAP_PCT  = 8.0    # ≥ this gap = comfortable enough to add
APPROACHING_STOP_GAP_PCT = 8.0   # ≤ this gap = surface for monitoring (same number, different lens)

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
# New stop level when tightening = current price − this × ATR.
# Tighter than the 2.0× used for initial stops because the position is
# already in the danger zone — less room before next stop-out is warranted.
STOP_TIGHTEN_ATR_MULT     = 1.5

# 📅 Earnings overweight — trim-down rule.
# Binary event = asymmetric risk. Above EARNINGS_OVERWEIGHT_TRIM_PCT, the
# expected earnings move would breach the per-trade risk budget; trim down
# to EARNINGS_OVERWEIGHT_TRIM_TO_PCT (aligned with LARGE_POSITION_WEIGHT_PCT
# floor — "large but not overweight").
EARNINGS_OVERWEIGHT_TRIM_PCT    = 12.0
EARNINGS_OVERWEIGHT_TRIM_TO_PCT = 10.0

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

# ── Evening Debrief "meaningful intraday move" cutoff ────────────────────────
# Picks moving more than this in absolute % today get a verdict
# (Missed / Dodged / Skip validated). Smaller moves are "flat" — no signal.
MEANINGFUL_INTRADAY_PCT = 1.0

# ── Composite-score weights (scoring.combined_score) ─────────────────────────
# How much each layer contributes to the composite score. Tuning these is a
# policy decision — heavier technical = more momentum-driven, heavier
# fundamental = more value-driven. Must sum to 1.0.
COMPOSITE_WEIGHTS = {
    "technical":   0.45,
    "fundamental": 0.40,
    "sentiment":   0.15,
}

# ── Earnings / macro proximity windows (days) ────────────────────────────────
EARNINGS_IMMINENT_DAYS = 7       # any trade within this window = caution
MACRO_IMMINENT_DAYS    = 3       # HIGH-impact macro event within this window = suppress new picks in affected sector

# ── Macro-event playbook gates (macro_playbook.py) ───────────────────────────
# Pre-event PROTECT / WATCH classification thresholds. Values surfaced here
# so future changes are policy decisions, not hidden literals.
MACRO_PROTECT_PNL_PCT    = -15.0  # already-underwater + bear-move = MEDIUM PROTECT
MACRO_WATCH_LOW_SCORE    = 55.0   # weak score gating WATCH-LOW
MACRO_WATCH_LOW_WEIGHT   = 12.0   # min weight gating WATCH-LOW

# Portfolio bear-exposure tier cutoffs for the macro-event playbook header
# (% of portfolio sitting in sectors with high bear-move sensitivity).
MACRO_EXPOSURE_CRITICAL_PCT = 55
MACRO_EXPOSURE_HIGH_PCT     = 35
MACRO_EXPOSURE_MEDIUM_PCT   = 15

# ── Multi-source market-data layer (providers/ + data.py orchestrator) ───────
# The app was historically single-sourced on yfinance (unofficial, no SLA). The
# provider seam + orchestrator add failover + a price cross-check so right-data-
# at-the-right-time is protected. See memory `project_second_data_source`.

# Failover chain — the orchestrator tries these IN ORDER per data type, using
# the first CONFIGURED provider (key present) that advertises the capability and
# doesn't raise ProviderUnavailable. Order is a setting, not hardcoded: if
# yfinance reliability degrades, move another provider to the front here with no
# code change. Names must match DataProvider.name.
DATA_PROVIDER_ORDER = ["yahoo_finance", "finnhub", "fmp"]

# Master switch. When False, data.py behaves EXACTLY as the single-source
# yfinance code (no failover, no cross-check) — lets the layer ship dormant and
# be flipped on only once the Finnhub/FMP adapters are validated on live data.
DATA_MULTISOURCE_ENABLED = False

# Price cross-check tolerance. After the primary returns a live price, the
# orchestrator fetches the same ticker from the next provider that can serve a
# quote and compares; a gap beyond this % is surfaced loudly ("price
# unverified") rather than silently trusted. Scoped to PRICE only — the one
# field where a plausible-but-wrong value directly drives stops, P&L and movers.
# This is an investment-policy value (PROVISIONAL — confirm with user before
# enabling the layer; 1.0% is a starting point, not a settled call).
DATA_XCHECK_TOLERANCE_PCT = 1.0

# Which fields are cross-checked. Everything else is failover-only — calling two
# sources for fundamentals/news would burn keyed free-tier quotas for little
# gain. Kept as a set for cheap membership tests in the orchestrator.
DATA_XCHECK_FIELDS = {"price"}
