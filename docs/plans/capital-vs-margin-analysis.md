# Capital vs. Margin Analysis — plan

## Problem

The app has never answered "was trading on margin actually worth it?" The
owner runs the account at real leverage (~2.85x measured 2026-09-10, F-266)
and has no way to see: how much interest margin has cost, whether the book's
own return has covered that cost, or what the equity curve would look like
on capital alone. F-266 (2026-09-10) started capturing daily leverage/cushion
history, but forward-only — it doesn't answer the "since I started" question,
and it doesn't compute a counterfactual or an interest total.

Established basis (verified in code):
- `snaptrade_income_events` (`db.py` ~line 765) captures `dividend`/`interest`/
  `fee` events, upserted via broker sync since ~2026-08-18 go-live
  (`broker_sync.py` `_INCOME_TYPES`). SnapTrade files interest earned on cash
  and margin interest charged under the same `interest` event_type — sign is
  the only distinguishing signal. **Not yet verified against real data which
  sign this account's margin charges use** — first thing to confirm at build
  time, before any interest total can be trusted.
- `daily_snapshots` has real per-ticker daily history back to ~2026-06-09 —
  enough to compute the book's own unlevered return over any window.
- `account_daily_snapshots` (F-266) has real leverage/cash/cushion history
  forward from 2026-09-10 only. No debit history exists before that date.
- `account_flows` has the real deposit/withdrawal ledger since broker go-live.

## Decisions (owner, 2026-09-10)

- **Counterfactual model: cost-of-leverage / scale, not a different-decisions
  replay.** Margin is modeled as a scale on the OWNER'S ACTUAL positions —
  levered return = leverage × book return − interest paid; unlevered return =
  book return alone. This is fully computable from real data and never
  invents a trade the owner didn't make. Two alternatives were considered and
  rejected: (a) "different-decisions" (guessing what smaller/different trades
  the owner would have made with $7K alone) — rejected outright, this is
  exactly the fabricated-number risk the app's zero-hallucination posture
  exists to prevent; (b) "cash-constrained replay" (mechanically skip any
  trade that would have required margin) — considered but not chosen: which
  trades get cut is order-dependent and arbitrary, and assumes a full-skip
  rather than a smaller buy, an assumption the owner never actually made.
- **History depth: reconstruct back to broker-integration go-live
  (~2026-08-18), not forward-only from F-266.** The debit trajectory before
  F-266 existed is reconstructed by anchoring at today's known balance and
  rolling backward through every trade + cash flow + interest charge. This
  is the actual defensible floor, not an arbitrary cutoff — it's exactly
  where the transaction ledger becomes complete (broker sync go-live);
  further back, flows/income weren't captured at all, so reconstruction
  would silently degrade into guessing. **Must self-validate**: reconstructed
  days that overlap F-266's own recorded days must match; any drift is
  surfaced, not hidden. Reconstructed days are visually/labelled distinct
  from F-266's recorded (not inferred) days — never presented as equally
  certain.
- **Build-time check before committing to reconstruction:** confirm whether
  SnapTrade's API exposes a real balance-*history* endpoint (today's
  broker_sync only reads the *current* balance). If real historical balances
  exist, prefer them over reconstruction entirely for that span.
- **New surface:** a "💳 Capital vs Margin" section under 💰 Account, same
  period-selector UX pattern (Weekly/Monthly/All-data or similar) as the
  existing Capital Trend / Leverage & Margin Cushion charts.
- **Stays awareness-only — no gate, no policy constant.** This never
  recommends deleveraging as an action the app enforces; it's a decision
  input for the owner, same posture as the existing margin/leverage surfaces.

## v1 scope (this plan — send to `planner` next)

1. **Interest paid, to date** — summed from `snaptrade_income_events`
   (`event_type='interest'`), sign-isolated to margin interest charged (not
   interest earned on cash) once the sign convention is confirmed at build
   time.
2. **Two equity curves over the selected window:** actual (levered) equity —
   recorded from F-266 forward, reconstructed backward to broker go-live —
   vs. synthetic unlevered equity (capital alone, at the book's own realized
   return rate). The gap between them is margin's cumulative contribution.
3. **The verdict, decomposed:** extra-exposure P&L (what amplification
   earned/cost) − interest paid = net value margin added or destroyed, since
   broker integration.
4. **Break-even rate:** the annualized book return rate at which margin is
   exactly neutral (equals the effective interest rate) — "your book needs
   to return ≥X%/yr just to cover the leverage."
5. **Margin's share of a specific drawdown** — decompose a selected loss
   episode (e.g. the week that motivated this feature) into "amplification
   portion" vs. "would have lost anyway unlevered."
6. **Projected annual interest** at the current debit/rate, extrapolated
   forward.
7. **Deleverage scenario:** "if you paid down $X of debit: interest drops to
   $Y, and a −N% book move costs $Z less" — composes the existing `margin.py`
   functions, no new formula.
8. **Regime-split verdict:** margin's net contribution broken out by up-weeks
   vs. down-weeks in the window, rather than one blended number that can hide
   the real story (margin can net "roughly even" while actually being "great
   in rallies, brutal in selloffs").
9. **Cross-reference, do not rebuild:** margin-call distance (F-253) and
   shock modeling (F-263) get linked/shown alongside, not reimplemented.

## Explicitly rejected for v1 or later — do not re-propose without new reasoning

- **"Optimal leverage" / Kelly-criterion-style sizing recommendation.**
  Rejected, not deferred. On a ~3-4 week real data sample this is statistically
  unstable enough that it could recommend *increasing* leverage off pure
  noise — exactly the "confident but dangerous number" class this app's
  operating posture exists to refuse (CLAUDE.md: "recommend nothing rather
  than recommend wrongly"). Would need a much longer, regime-diverse sample
  before this could ever be responsibly built, and even then it's a policy
  decision requiring `planner`+owner sign-off, not an inferred output.
- **Different-decisions counterfactual** (see Decisions above) — rejected on
  fabrication grounds, not a maturity/data gap. Do not revisit this unless
  the owner explicitly wants to trade honesty for narrative completeness.

## Genuinely open, not yet decided (candidates for a later pass, not v1)

- Whether to fold a real per-security margin maintenance rate (still the
  flat `MARGIN_MAINTENANCE_RATE` assumption everywhere, per F-266's own
  deferred list) into the break-even/deleverage math for more precision —
  tracked once as a broader gap in F-266's CLAUDE.md queue entry, not
  duplicated here.
- Whether the SnapTrade balance-history-endpoint check (build-time item
  above) turns out to make reconstruction unnecessary for some or all of the
  window — resolve at build time, update this doc if it changes the scope.

## Status

**2026-09-10 — SHIPPED. `planner` PROCEED WITH CHANGES → `implementer` built →
`reviewer` SHIP (0 blocking, 3 non-blocking, all three fixed pre-ship).**
No new DDL/table — `planner` confirmed everything is derivable live from
existing tables (`daily_snapshots`, `trades`, `account_flows`,
`snaptrade_income_events`, `account_daily_snapshots`) plus a within-session
memo, so this stayed out of `_GATE_FILES` entirely. New pure module
`stock_analyzer/capital_vs_margin.py` (36 tests), new "💳 Capital vs Margin"
section on 💰 Account below the F-266 Leverage & Margin Cushion chart.

**Two build-time verifications flagged by `implementer`, still genuinely
open — not guessed, not blocking ship (both fail safely to a disclosed
"unconfirmed" state):**
1. **Interest sign convention** — `snaptrade_income_events`'s `interest` type
   lumps margin interest charged and interest earned under one sign
   convention, unconfirmed against real data. Ships with `charged_sign=None`
   — the UI shows both raw magnitudes, never nets them, and discloses
   "unverified." Resolve once real `interest`-type rows exist to inspect.
2. **SnapTrade balance-history endpoint** — `snaptrade_client.py` only wraps
   current-balance/current-positions/activity-lookback calls; whether
   SnapTrade's API offers real historical balances (which would obsolete
   some/all of the backward reconstruction) is unconfirmed. Worth a look if
   ever revisiting the reconstruction's reliability.

**Post-review fixes (non-blocking per `reviewer`, fixed anyway before commit):**
`margin_debit` decoupled from the `gross_book is not None` gate (it only
ever depended on `cash_balance`, which is always populated — a
`daily_snapshots` gap was silently hiding a real, known debit from every
"current debit" readout); `app.py`'s "current debit/gross" now walk backward
to the latest non-None point instead of blindly trusting `series[-1]`
(same gap-day root cause); added an explicit "not yet cross-checked" caption
for the `overlap_days == 0` case (previously silent — absence of both the
pass and fail captions read as "fine" when it actually meant "unvalidated").
