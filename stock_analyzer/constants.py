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
# High-beta cluster share — the standing "correlated exposure" read under the
# fragility gauge: when this fraction of the book sits in high-beta (β ≥
# PORTFOLIO_BETA_ELEVATED) names, they tend to fall TOGETHER on risk-off days, so
# the per-name diversification is illusory. Above this % the cluster line renders
# as a warning rather than neutral info. Operational display threshold (not a
# gate) — tune from observation.
CONCENTRATION_HIGHBETA_SHARE_WARN = 60.0

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

# ── Grow Today candidate funnel ──────────────────────────────────────────────
# max_picks: how many NEW positions the daily brief will recommend. Lower on
# flat/bear days (capital-preservation posture). Investment-policy values —
# changing them is a policy decision, not tuning.
GROW_MAX_PICKS_BULL      = 3      # bull-day new-position cap
GROW_MAX_PICKS_DEFAULT   = 1      # flat / bear-day new-position cap
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

# Conviction tiers (Grow Today new-pick label only — not a hard gate).
# A pick that clears COMPOSITE_BUY but doesn't yet reach STRONG_BUY is
# "moderate" conviction; STRONG_BUY+ is "high."
COMPOSITE_HIGH_CONVICTION = COMPOSITE_STRONG_BUY

# Stricter Grow Today bar on flat market days — only the highest-quality
# setups clear when the index isn't providing tailwind.
COMPOSITE_BUY_FLAT_DAY = 78

# Minimum reward:risk for an entry to be considered favourable. The composite
# score answers "is this a good STOCK to own?"; R:R answers "is THIS price a
# good ENTRY?" — independent questions, so a Strong-Buy stock can have poor
# entry R:R (target near, stop far). Watchlist ENTER_NOW hard-gates on this
# (G-13); the Analysis Trade Plan surfaces a caveat below it (not a hard block —
# the Analysis page is a research/judgement surface, so the user decides).
RR_ENTRY_MIN = 2.0

# ── Risk per trade (position sizing) ─────────────────────────────────────────
RISK_PCT_PER_TRADE = 0.015       # 1.5% portfolio risk per trade (Moderate)

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

# ── Position lifecycle (position_lifecycle.classify_position_state) ───────────
# A held position moves through states: settling → established → winning, with
# at_risk / exit overriding on danger. Drives "settling grace" (don't micromanage
# a position you just opened) and lifecycle badges — the calm-advisor layer that
# keeps the app a medium-term advisor, not a day-trading feed (§2B persona).
POSITION_SETTLING_DAYS   = 10    # held < this = "settling": suppress ROUTINE mgmt nudges (not exits/critical)
POSITION_AT_RISK_GAP_PCT = 3.0   # gap-to-stop ≤ this = "at_risk" (same critical band; always surfaces)
POSITION_WINNING_PNL_PCT = 8.0   # P&L ≥ this (and healthy) = "winning"; aligns with STOP_TIGHTEN_MIN_GAIN_PCT

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
REL_STRENGTH_LOOKBACK_DAYS     = 20     # relative-strength-vs-SPY lookback (negative RS = idiosyncratic weakness)
DETERIORATION_PEAK_FALLBACK_BARS = 63   # peak-window lookback (~3mo) when position age is unknown (no journal)
MATERIAL_ADD_RESET_THRESHOLD   = 25.0   # a non-initial lot ≥ this % of the position re-anchors the deterioration PEAK window to "since the add" (averaging-down guard; cost basis stays blended — see exit_advisor.material_add_window_days)

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

# Forward window (days) for the Catalyst Watch panel — upcoming earnings for
# names the app tracks (held + watchlist + sector universe). AWARENESS ONLY: it
# does not recommend initiating into earnings (the proximity gates still
# suppress that); it just removes the blind spot of a tracked name reporting
# without warning. Post-print confirmation still surfaces via the Movers scan.
CATALYST_WATCH_WINDOW_DAYS = 7

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

# Last-known-good bundle cache (data-resilience; bundle_cache table). When the
# history/bundle providers (Yahoo→FMP) ALL fail, load_all serves the last cached
# bundle (real data, aged) so the portfolio still renders WITH a staleness banner
# instead of "Could not load". Bounds how stale a displayed analysis may be —
# beyond this, fail loud rather than pass off very old signals as current. Mild
# policy flavour (stale data drives the shown signals); adjustable.
BUNDLE_CACHE_MAX_AGE_DAYS = 5

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

# Minimum number of CORE fundamental metrics that must be present for the
# fundamental leg — and therefore the composite verdict — to be trusted. The
# five core scoreable metrics are forward_pe, revenue_growth, earnings_growth,
# profit_margins, debt_to_equity (see fundamentals.fundamental_score). When
# yfinance `.info` comes back empty AND no failover source can backfill it,
# zero of these are present, and fundamental_score returns a FABRICATED neutral
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

# ── Recommendations-history scorecard ────────────────────────────────────────
# Minimum age (calendar days) before a surfaced rec's OUTCOME is scored on the
# Recommendations History page. A rec measured the day after it surfaces is just
# noise (one session of price wiggle); the aggregates (avg outcome / alpha /
# best / worst) only count recs at least this old. Younger recs still appear in
# the table flagged "maturing." This is a MEASUREMENT window for the retrospective
# scorecard — NOT a decision gate; it never affects what the engine recommends,
# only how long we wait before grading. Safe to tune from observation.
REC_SCORE_MIN_DAYS = 5
