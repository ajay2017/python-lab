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

### Tier 3 — Documentation only

**I6 — User Guide note on MONITOR item cadence** *(~15 min)*
- Add one sentence to the 🔗 Risk Analysis User Guide section clarifying that MONITOR / Deteriorating ↓ cards are re-evaluated on every data refresh (not on a fixed date schedule) — intentional asymmetry vs. the Analysis Hold tab's explicit recheck date, which is specific to a dated entry signal.
- Zero code change; pure User Guide copy.

---

### Deferred Quick Wins

Three Quick Wins from the audit not yet addressed. Low priority; no functional impact.

| Item | Location | What it is |
|---|---|---|
| QW8 | `app.py:10684, 10998-11007` | Single-source the ±5% outperform/underperform band (used 3 times) into one named constant |
| QW9 | `app.py:8143-8165` vs `app.py:8217-8224` | Risk Analysis "Risk flags" banner uses different beta/vol/Sharpe/drawdown cutoffs than the per-metric labels a few lines above — align to one set |
| QW10 | `app.py:8781, 8801-8802` | Inline `< 45` / `>= 55` composite literals sit next to an already-imported `COMPOSITE_BUY`; source from the same constant |
