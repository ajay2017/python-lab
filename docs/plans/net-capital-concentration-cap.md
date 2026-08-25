# Net-Capital Concentration Cap — Design & Implementation (F-255)

**Status: SHIPPED 2026-08-25.** Both commits (`af5d4ca`, `a9e920a`) merged to `main` and deployed to Railway. Both passed mandatory Opus review (SHIP, 0 blocking). Test suite: 4236 passed.

**Author:** Ajay Kumar  
**Date:** 2026-08-25  
**Design:** decided directly with the user in conversation (the 3-option tradeoff below); architecture then handed to a `planner` subagent design pass (resolved model: Opus 4.8) for a codebase-grounded implementation plan.  
**Build:** `implementer` subagent (resolved model: Claude Sonnet 5).  
**Review:** `reviewer` subagent, both passes (resolved model: Opus 4.8).

---

## The gap

Real-world trade data (2026-08-23) exposed a conceptual misalignment between the app's position-sizing ceiling (`SINGLE_NAME_CEILING = 15.0`, gross-book-basis) and portfolio leverage. The account runs **3.15× leverage** ($24,502 gross value, $7,802 net capital, $16,701 debit margin):

- **ALB:** $3,581.25 = **14.6%** of gross book → ≈ **45.9%** of net capital  
- **OXY:** $3,545.90 = **14.9%** of gross book → ≈ **45.5%** of net capital

Both positions cleared the gross-basis 15% ceiling at proposal time, yet each consumed nearly half of the owner's real capital cushion. **The gross-book cap is silent on leverage; the net-capital exposure is not.** F-253/F-254 added margin awareness to the risk surfaces; F-255 adds enforcement.

---

## Design: additive, capital-basis cap (Option B — chosen with user)

**User decision (settled 2026-08-25):** three options were scoped by a `planner` (Opus) pass:

1. **(A) Leave awareness-only** — remove the enforcement entirely, rely on the banner tools (F-253/F-254).
2. **(B) Add a separate additive cap** — new `NET_CAPITAL_POSITION_CAP_PCT = 25.0` gate, fires after gross-book cap, only when account is levered and cash balance is fresh.
3. **(C) Recalibrate the existing gate's own denominator** — change `SINGLE_NAME_CEILING`'s input from gross book to net capital; affects ~10 modules' shared weight denominators, re-introduces transient-cash instability equity-basis was chosen to avoid.

**User chose Option B.** It adds a protective floor without re-architecting the denominators. The `planner` design pass had recommended against (C) without the user's explicit approval, since it changes the gate's own meaning across the entry pipeline and concentration disciplines.

---

## Implementation summary

### Constants

`stock_analyzer/constants.py`:
- **`NET_CAPITAL_POSITION_CAP_PCT = 25.0`** — one position can be at most 25% of net capital when levered with a fresh cash balance; inert when unlevered or cash is stale.

### Core engine

`stock_analyzer/margin.py` (new module):
- **`resolve_net_capital(gross_book, account_cash_rec, stale_days_limit, now) → (net_capital: float | None, basis: str)`**
  - Computes net capital from book value and fresh account cash.
  - Returns `None` when cap should be inert (unlevered, or cash balance stale beyond limit).
  - `basis` enum: `"unlevered" | "stale" | "levered" | "called"` (margin call = cap-inert, size-infeasible).

- **`held_over_capital_cap(port_df, net_capital, cap_pct) → list | None`**
  - `None` = can't evaluate. `[]` = all holdings OK. Else list of tickers exceeding cap.
  - Feeds the Account-page awareness banner.

### Sizing integration

`stock_analyzer/risk.py` — `position_sizing()` / `sizing_unavailable_reason()`:
- New optional `net_capital` / `max_capital_pct` kwargs (default-off, backward-compatible).
- Capital ceiling applied **after** gross-book ceiling, so it can only shrink a size further, never grow it.
- Output dict gains `capital_capped: bool` and `capital_pct: float` keys when capital is in scope.

`stock_analyzer/concentration.py` — `assess_add_concentration()`:
- Mirrored `net_capital` / `capital_ceiling` kwargs.
- Output keys: `capital_breach`, `post_name_capital_pct`.

### Daily briefing & Grow Today

`stock_analyzer/daily_briefing.py`:
- **`SIZING_FORMULA_VERSION` bumped 2 → 3** — documents that the formula changed (the capital ceiling applies), even though inert until `net_capital` is passed to the sizing functions.
- `_position_size_for_render()` / `_grow_today()` / `build_daily_briefing()` all thread optional `net_capital` parameter through to new-pick and add-winner sizing.

### Rendering

`app.py`:
- **Grow Today cards (new-pick & add-to-winner):** when capital ceiling binds, caption states the size was reduced and shows what the uncapped size would have been.
- **Analysis page (2 sizing sites + caption branch):** renders capital-cap disclosure alongside gross-cap.
- **Watchlist Advisor (2 sites):** capital-cap disclosure.
- **Pullback-add-to-winner flow (Trade Journal page):** integrated sizing.

### Email / headless alert engine

`stock_analyzer/headless_alert_engine.py` → `stock_analyzer/notify.py`:
- `compute_morning_picks()` passes `net_capital` through to sizing.
- `_sizing_cap_note()` renders **both book-cap and capital-cap disclosure lines together** when a size is capped by both (capital applied after, on top of gross).

### Account page

💰 **Account — new awareness banner:** *"📐 Positions Over Your Net-Capital Cap"*
- Dead-end display only (no gate, no session-state write, no downstream feedback).
- Lists any held position exceeding 25% of net capital (ALB/OXY today).
- Awareness-only, existing holdings never auto-trimmed (bought before the cap existed).

### Tests

Full suite 4236 passed after both commits (up from 4223 before). New tests across `tests/test_risk.py`, `tests/test_margin.py`, `tests/test_concentration.py`, `tests/test_sizing_coherence.py`, `tests/test_notify.py`, and `tests/test_headless_alert_engine.py` cover: the byte-identical/default-off contract when `net_capital` is omitted, the ALB-shaped boundary case, the `resolve_net_capital` 5-branch truth table, the `held_over_capital_cap` None-vs-`[]` contract, and `notify.py`'s both-caps-render-together case. The pre-existing gate-isolation test (`tests/test_margin.py::test_margin_module_not_imported_by_any_gate_module`, added in the 2026-08-24 audit-fix batch, not new to F-255) still passes, confirming `risk.py` continues to avoid importing `stock_analyzer.margin` even with the new capital-cap math (the percentage is re-derived inline in `risk.py` rather than imported).

---

## Genuinely NOT part of this feature — do not conflate

**(1) Option (C) recalibration.** A separate decision, deferred, explicitly not chosen this round. Changing the existing gates' own *denominator* from gross to net is out of scope — remains a future policy decision if ever taken with the user.

**(2) Emailed buy-list partial disclosure.** The morning/pullback buy-list email shows capital-cap disclosures **only when the cap actually binds** (capped / infeasible), not a capital-basis percentage on every pick unconditionally. Completing that disclosure (adding capital % on all picks, not just capped ones) is a separate UX pass.

---

## Review verdicts

**Commit `af5d4ca`** ("feat(risk): add net-capital concentration cap engine")
- **Reviewer (Opus):** SHIP, 0 blocking
- **Note:** One non-blocking `>` / `>=` consistency nit found and fixed in same commit.

**Commit `a9e920a`** ("feat(app): wire the net-capital concentration cap into all sizing surfaces")
- **Reviewer (Opus):** SHIP, 0 blocking
- **Note:** Reviewer specifically verified Account-page banner is a true dead-end with no session_state write and no downstream gate/decision surface wiring.

---

## Known behaviors

- **Inert when unlevered:** if no `account_cash` record exists, or its `cash_balance` is ≥ 0 (no margin debit), `resolve_net_capital` returns `net_capital = None` and the cap does not apply.
- **Inert when cash stale:** if account cash balance is not fresh (older than `ACCOUNT_CASH_STALE_DAYS = 7` days), cap does not apply — same fail-soft as other margin surfaces.
- **Fails closed on margin call:** if net capital ≤ 0 (margin-called), `position_sizing()` returns `None` (no size suggested).
- **Live-activates immediately on deploy:** because the real account reads levered with a fresh cash record, new-position sizing on all wired surfaces already respects the 25%-of-capital cap starting from this deploy.
- **Account-page banner shows ALB/OXY today:** the two real holdings that exceed the new cap are visible as an awareness note, but never trigger a forced trim/exit (existing holdings bought before the cap existed are protected).

