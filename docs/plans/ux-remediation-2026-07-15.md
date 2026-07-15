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

### Tier 1 — Batch (display / copy / safety, one commit, no structural change)

Items that are pure copy, styling, or lightweight form-safety changes. No tabs reorganised, no decision logic touched. Can all ship in one commit.

**I10 — Standardize "Recommended Action" header** *(~10 min)*
- `app.py:9955` Rebalancer trim card: `'Action'` → `'Recommended Action'`
- `app.py:10052` Rebalancer add card: `'Action'` → `'Recommended Action'`
- `app.py:15155` Watchlist card: `f"{_a_icon} Action: {_a_label}"` → `f"{_a_icon} Recommended Action: {_a_label}"`

**I5 — Cash balance: save requires second confirm after sanity warning** *(~20 min)*
- `app.py:20023-20037`: when either sanity check fires (`_new_cash > max(equity × 10, $1M)` or margin debit exceeds equity), set `st.session_state["_acct_implausible_pending"]` instead of calling `save_account_cash`; on next run render "Save anyway / Cancel" and clear the flag on either branch.
- Same two-step session_state pattern as C6.

**I1 — Economic Calendar vocabulary: pre-event OPPORTUNITY → ADD** *(~30 min, confirm label origin first)*
- Need to verify whether `OPPORTUNITY` is a literal string produced by `economic_calendar_advisor.build_event_playbooks()` (display-label swap) or is routed through conditional logic in the advisor (broader change).
- If display-label only: rename `OPPORTUNITY → ADD` so Pre-Event and Post-Event both use ADD for "entry signal"; leave `PROTECT` / `REDUCE` as intentionally distinct (different contexts).
- Location: `app.py:20707-20716` (Pre-Event rendering).

**I4 — Catalyst Watch Entry Candidates: de-escalate visual weight** *(~30 min)*
- `app.py:20348-20380`: per-candidate body currently renders 4 `st.metric` tiles (same grid as a Buy card), with a per-ticker ▶ Analyse button.
- Replace: `st.metric` grid → compact `st.caption` info row (beat rate, score, reaction inline as text); keep the expander header and the Analyse link but style it as secondary.
- The "Awareness only" section disclaimer and page-level caption stay unchanged.

---

### Tier 2 — One per deploy + live review

Per `feedback_phased_ux_rollout_cadence`: execute one structural tab change at a time, push to Streamlit Cloud, review the live app before starting the next. User catches real IA issues (nav-naming collisions) via hands-on QA, not code review.

**I7 — Recommendations History → 3 tabs** *(~1.5 hrs)*
Proposed split at natural seams:
- **Summary** — filters + 4 headline metrics + best/worst banner + Sankey chart + Missed Opportunity block (chart + table + expander)
- **Trends** — 4 analytics expanders: By Verdict / By Composite Band / By Rec Type / Trend chart
- **Full Table** — detail table + ticker-jump control

Rationale for order: lowest-density restructure of the three; safe first structural change.

**I3 — Risk Analysis → 3 tabs** *(~1.5 hrs)*
Proposed split:
- **Dashboard** — Portfolio Risk metrics (7) + Market-Risk Posture gauge + Cross-Asset Pulse + Rate Sensitivity table
- **Action Plan** — Risk Action Plan (3 headline metrics + N recommendation cards)
- **Stress Testing** — scenario selector + shock slider + results chart + 2 tables + 2 expanders

Rationale: users navigate to Risk Analysis when something needs attention; tabs make the action plan immediately reachable without scrolling past metrics.

**I2 — Portfolio Allocation Tab 1 → 4 sub-tabs** *(~2.5 hrs, highest complexity)*
Proposed split:
- **Overview** — Allocation pie + P&L bar + Sector Exposure chart + Composition Sankey + Position Detail table (full drill-down)
- **Rebalancing** — Rebalancing Advisor (radio + editable table + chart + trim/add cards) + Sentiment Momentum + News Intelligence expander
- **Tax** — Tax Efficiency Advisor (radio + 5 metrics + table + 3 card types)
- **Performance** — Performance vs SPY + Performance Diagnostics + P&L Waterfall

Rationale: densest page in the app; do last because any naming or IA issue is most likely to surface here.

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
