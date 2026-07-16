# UX Remediation Plan — 2026-07-15 Audit

*Tracks all remediations from [docs/reviews/2026-07-15-UX-review.md](../reviews/2026-07-15-UX-review.md)*

---

## Done — commit b2e347c (2026-07-15)

All 8 Critical Issues, Improvements I8 & I9, and Quick Wins QW1–QW7 resolved in a single batch commit.

| Finding | What was fixed |
|---|---|
| C1 | Removed raw SQL `DELETE` + hardcoded cutover date from Recommendations History help expander |
| C2 | Added `SINGLE_NAME_TRIM_TRIGGER=18.0` + `SECTOR_REDUCE_TRIGGER=20.0` to `constants.py`; wired `portfolio.py` (rebalance_actions + diversification_recommendations) and `earnings_advisor.py` — values unchanged, now named |
| C3 | Replaced magic number `68` with `COMPOSITE_STRONG_BUY` (75) in `earnings_advisor.py` HOLD_OR_ADD branch |
| C4 | Added `_PILLAR_LABELS` dict in Grow Today caption; "business_quality" → "Business Quality", "technicals" → "Technical" |
| C5 | Added `format_func` to Trade Journal "Reason" selectbox; STOP_HIT → "Stop Hit", WATCHLIST_ENTRY → "Watchlist Entry", etc. |
| C6 | Bulk trade delete is now a two-step confirm (stores pending IDs across rerun; "Yes, delete / Cancel") |
| C7 | Split single "skip all sanity checks" checkbox into three independent guards: `_tj_override_price`, `_tj_override_ticker`, `_tj_override_sell` |
| C8 | Nav badge now shows both 🔴 N + 🟡 M simultaneously; previous `elif` chain silently dropped warning count when danger was also present |
| I8 | Removed backtick-quoted raw DB field names (`followed_signal=True/False`) from Trade Review caption |
| I9 | Added `HOLD_FOR_SIGNAL → "Hold — Signal Pending"` to `_TX_ACTION_LABELS` in Tax Efficiency section |
| QW1 | `app.py:5593` — interpolated `COMPOSITE_BUY` into "below the Buy threshold" copy instead of bare `65` |
| QW2 | `app.py:16909` — caption now matches actual button text for trade delete |
| QW3 | `app.py:12601` — "BQ" → "Business Quality" in Market Scanner composite breakdown |
| QW4 | `app.py:14984` — "Already in Portfolio" banner now has a "Go to Today's Brief →" nav button |
| QW5 | `app.py:15201` — "Remove from watchlist" is now a two-step confirm |
| QW6 | `app.py:5442` — deleted dead `_gate_margin_note` branch (net-capital basis is hardcoded to "equity"; branch could never fire) |
| QW7 | Same as I9 above |

---

## Pending

### ~~Tier 1 — Batch~~ Done — commit e9c288c (2026-07-15)

All 4 Tier 1 items shipped in one batch.

| Finding | What was fixed |
|---|---|
| I10 | `'Action'` → `'Recommended Action'` on Rebalancer trim card, Rebalancer add card, and Watchlist recommendation card |
| I1 | `OPPORTUNITY` routing token display-mapped to `"Add"` at render site only (same pattern as existing `WATCH→"Watch"` alias); expander header updated to match. Internal `macro_playbook.py` token unchanged |
| I5 | Cash balance implausible-value entry now requires explicit confirmation: first submit sets `_acct_implausible_pending`; "Save anyway / Cancel" pair rendered until resolved |
| I4 | Catalyst Watch Entry Candidates: 4-column `st.metric` grid replaced with compact `st.caption` info row; expander header + Analyse button unchanged |

---

### ~~Tier 2 — One per deploy + live review~~ Done (2026-07-15)

All 3 Tier 2 structural changes shipped and live-reviewed.

| Finding | Commit | What was split |
|---|---|---|
| I7 | ef39b4c | Recommendations History → 📊 Summary / 📈 Trends / 📋 Full Table |
| I3 | 6b125ef | Risk Analysis → 📊 Dashboard / 📋 Action Plan / 🔥 Stress Testing |
| I2 | ae04915 | Portfolio Allocation → 📊 Overview / ⚖️ Rebalancing / 💰 Tax / 📈 Performance / 📈 Analytics |

---

### ~~Tier 3 — Documentation only~~ Done — commit b6a5d52 (2026-07-15)

**I6** — Added MONITOR/Watch cadence note to 🔗 Risk Analysis "How it protects a position" User Guide expander: cards re-evaluated on every data refresh; auto-drop if position recovers; intentional asymmetry vs. the Analysis Hold tab recheck date (specific to a dated entry signal, not a general timeout).

---

### Catalyst Watch — 3-Tab Restructure (I11)

**Rationale:** The Catalyst Watch page has three distinct audience questions with different cadences and data sets. Keeping them on a single scroll makes the page dense and buries the actionable tiers. Matches the tab-first UX pattern applied to Recommendations History (I7), Risk Analysis (I3), and Portfolio Allocation (I2) this session.

**Proposed tabs:**

| Tab | Icon | Content |
|---|---|---|
| Holdings | 📋 | Tier 1: Your Holdings — Earnings (per-position detail + Pre-Earnings Playbook) |
| Radar | 📡 | Tier 2: On Your Radar — Watchlist & Universe upcoming earnings, grouped Today/Tomorrow/Next-7d |
| Entry Candidates | 🎯 | Phase 3: Earnings Entry Candidates (composite-gated watchlist names near earnings) |

**Section boundaries (verify line numbers before editing — file has grown since analysis):**
- `elif page == "🔔 Catalyst Watch":` block is currently near line 20210 (pre-session analysis: 20196; add ~18 lines from I3/I2/I7 additions)
- Tab 1 (Holdings): begins at the `st.subheader("Your Holdings — Earnings")` block (~20224)
- Tab 2 (Radar): begins at the `st.subheader("On Your Radar")` block (~20254)
- Tab 3 (Entry Candidates): begins at the Entry Candidates `st.expander` or section header (~20363)
- Page ends at the closing `elif` boundary (~20436)

**Approach:**
- Same flat `with` block pattern as I7/I3/I2 (already proven)
- Insert `_cw_tab_hold, _cw_tab_radar, _cw_tab_entry = st.tabs(["📋 Holdings", "📡 Radar", "🎯 Entry Candidates"])` at the top of the `elif` block, after any page-level setup (page title, cache loads, etc.)
- Wrap each section in its `with` block — content re-indented +4 spaces
- No logic changes; no new cache keys; no new constants
- Implementer must `py_compile` before committing

**Invariants:**
- Holdings tab still sources from `_last_held_data` / `_last_port_df` (same session-state reads, no change)
- Radar tab still reads `_grow_composites` bundle (same source)
- Entry Candidates tab still sources from `_grow_composites` and watchlist (same source)
- No cross-tab coordination needed — tabs are purely display separation

---

### ~~QW9~~ Done (2026-07-15)

Risk Analysis "Risk flags" banner now fires each flag at its metric's worst label band edge (beta 1.4 = red/inverse edge, vol ≥30 = "High", Sharpe <0.5 = "Weak", drawdown <-20 = "Significant"), so the ⚠️ banner and the per-metric labels can no longer drift. Only the volatility cutoff changed (`>25` → `>=30`, aligned to the "High" band per user decision — keeps the ✅ "acceptable for a growth-tilted portfolio" message honest). Display-only awareness banner — not a gate/scoring formula, no constants.py touch, no Opus review required. `app.py:8210`.

### ~~QW8 & QW10~~ Done (2026-07-15)

Both value-preserving single-sourcing cleanups. Opus-reviewed (SHIP, 0 blocking, 1 non-blocking that was then folded in).

- **QW8** — added `PERF_ALPHA_BAND_PCT = 5.0` to `constants.py` (awareness/display classification band, not a gate). Wired all four ±5% sites on the Performance / Relative-Strength views: alpha-attribution bar colors, the Outperforming / In Line / Underperforming counts **and their help-text captions** (now interpolate the constant so prose can't drift from logic), the relative-strength bar colors, and the styled-table `_alpha_col` cell colors (the site the review caught).
- **QW10** — the real decision literal (`_dp_cscore < 45`, the defensive-picks composite skip on 🔗 Risk Analysis) was first extracted to `DEFENSIVE_ADD_MIN_COMPOSITE = 45.0`, then **reconciled to `COMPOSITE_HOLD` (44)** in a follow-up (see below). The adjacent `>= 50` / `>= 55` were **deliberately left inline** — they only pick green/yellow/gray text color on a score line (cosmetic midbands, no matching semantic constant), consistent with how QW9 handled display-only band edges.

**Follow-up — 44-vs-45 reconciled (2026-07-15, user decision):** The extracted `45` sat 1 pt above `COMPOSITE_HOLD` (44), so the skip's comment ("skip Sell-rated") didn't match its code — a candidate at 44.x is *Hold*-rated (and shown with a "Hold" label) yet was silently dropped by `< 45`. Per the user's choice ("skip Sell-rated only"), the filter now reads `_dp_cscore < COMPOSITE_HOLD` and the standalone `DEFENSIVE_ADD_MIN_COMPOSITE` constant was **removed**. The filter now means exactly "not Sell-rated," tracks the one authoritative Sell/Hold boundary (so it can't drift if `COMPOSITE_HOLD` is retuned), and no longer contradicts the card's own label. Tiny correct-direction behavior change: candidates in [44, 45) now surface. Opus-reviewed.

*(No Quick Wins remain from the 2026-07-15 audit — all resolved.)*
