# Account Baseline & Cash/Flows — plan

## Problem

The app reasons only about **held equity positions** (`shares × current price`).
It has **no account-level model**: no cash balance, no deposits/withdrawals
(flows), no total-account baseline. Consequences:

- "Concentration" is a % of *invested equity*, not the *whole account* — a name
  at 23% of equity may be 11% of an account that's half cash.
- There is no honest **total account value** (equity + cash).
- **Growth/return cannot be computed correctly** — without flows you can't
  separate "I deposited more" from "it performed".

Established basis (verified in code): `portfolio.build_portfolio_df` →
`Market Value = current_price × shares`; `Weight (%) = MV ÷ Σ MV`
(`portfolio.py:126,188-190`). Rebalancer target = equal weight `100/n`
(`rebalancer.py:36`). No cash/buying-power/account-value concept exists anywhere.

## Decisions (user, 2026-06-25)

- **Seed manually now** (not wait for the Robinhood MCP auto-path). The manual
  model uses the SAME Supabase tables the broker sync would later auto-populate —
  so it's a drop-in upgrade, not a rebuild. (Robinhood path: HOLD until beta
  matures — see memory `project_today_pnl_scope`.)
- **v1 = account-total awareness (minimal).** Cash + total account value + true
  (account-level) concentration. NOT growth/return (needs flows = v2).
- **Gates stay on equity-basis in v1** (display-only true concentration). Moving
  the 15%/35% ceilings to account-basis is an investment-policy decision deferred
  to its own explicit discussion — it must not ride in silently on this build.

## Roadmap

- **v1 — account-total awareness (this plan).** Cash balance entry + total
  account value + cash% + true concentration (displayed alongside equity weight).
- **v2 — contributions & growth. ✅ SHIPPED (15b87b1).** `account_flows` ledger
  (baseline + deposit/withdrawal); `stock_analyzer/account.py` pure calc
  (net_contributed_capital, account_growth, has_baseline); Growth$/Growth%/NCC
  on the 💰 Account page. Display-only, feeds no gate. Inert until the
  `account_flows` DDL is run. (Dividend/fee rows deferred — internal events are
  already captured in the value delta; not needed for growth-vs-contributions.)
- **v3 — money-weighted return. ✅ SHIPPED (0488cdc).** `account.money_weighted_return`
  (Modified Dietz) + `baseline_anchor`; Return% (timing-corrected) + Annualized
  (period ≥ 30d) on the Account page. Reuses `account_flows` (no new DDL).
  **Chose Modified Dietz over daily TWR:** true TWR needs total account value at
  each sub-period boundary, but we have daily EQUITY (snapshots) and no daily CASH
  history — Modified Dietz needs only endpoints + dated flows. FUTURE refinement
  (when the snapshot series is long AND daily cash is reconstructable): a daily-
  linked TWR on the equity bucket using snapshots + the trades ledger.
- **v4 — margin / liability awareness. ✅ SHIPPED (f0abdf7).** The `account_cash`
  value is now SIGNED — negative = a margin debit. Because Total = equity + cash
  and Growth/Return/account-concentration all derive from Total, one signed field
  makes them all net out the loan (no new subsystem, no new DDL). UI: "Net cash /
  margin" input (no min), leverage caption, debit>equity soft-warn. Gates still
  equity-weight. Motivated by the user trading on margin often.
- **Gate-basis decision — Phase 1 ✅ SHIPPED (2026-06-26).** The deferred
  question ("move the 15%/35% concentration GATES off equity-basis?") was decided:
  **"tighter-of-both"** — gates compare a margin-aware `Gate Weight (%)` =
  `MV ÷ min(equity, total_account_value)`. Margin tightens the ceilings; cash on
  hand never loosens them; unknown/stale (> `ACCOUNT_CASH_STALE_DAYS`=7) cash
  degrades to equity-basis. Pure `concentration.gating_denominator`; `Gate Weight
  (%)` column injected at the boundary; display weights stay equity-basis.
  **Phase 1 ✅ = hard gates (Grow Today single-name + sector suppressions) + the
  Trade Journal entry nudge.** **Phase 2 ✅ SHIPPED = `risk_advisor` trim recs
  (`single_name_concentration` / `sector_concentration`) re-based with consistent
  account-basis weight AND trim-dollar math (every weight scales by
  `equity/gate_denom`; "$ at risk" = unchanged market value, only the trim pp/$
  grow), plus the peripheral entry-advice surfaces (`watchlist_advisor`,
  `quick_research`, `comparison` — each sums the `Gate Weight (%)` column).**
  Beta/Sharpe recs stay equity-basis (different risk dimension). See
  requirements.md G-19 / F-12b and architecture.md known-behaviours.
- **Broker sync (parked).** Robinhood MCP auto-fills `account_cash` (and later
  `account_flows`) — same schema, no rework.

## v1 design

### Data model — `account_cash` (single row, mirrors `alert_state`)
```
create table if not exists public.account_cash (
    id            integer primary key,        -- always 1 (single user)
    cash_balance  numeric not null default 0,
    note          text,
    updated_at    timestamptz not null default now()
);
-- RLS: enable + "Allow all (service role)" FOR ALL, matching every other table.
```
- DDL lives in the `db.py` module docstring (same place as `alert_state`); until
  created, `load_account_cash()` returns None and the app behaves exactly as today
  (cash unknown → equity-only, with a one-line nudge to set it).
- `db.load_account_cash() -> dict | None` and `db.save_account_cash(balance, note)
  -> bool`. The **writer gets the `_READONLY` guard** (it's user data, unlike the
  system caches). Reads/writes are best-effort and never raise (house pattern).

### Entry UI (dedicated **💰 Account** nav page, between Home and Market Scanner)
Own page (not on Home) so it stays out of the Home hot path — no per-rerun cost on
Home — and has room to grow (v2 flows, v3 returns). Reads the portfolio the Home
brief already built (`_last_port_df` in session); does NOT recompute `load_all`
(mirrors the Catalyst Watch pattern). If Home hasn't been opened this session, the
totals/concentration prompt "open Home", but the cash-entry form always works.
- Input to set/update cash balance, with an "as of <updated_at>" badge.
- **Data-sanity validation** (per `feedback_data_sanity_validation`): reject
  negatives; show current **equity** as a reference value; soft-confirm an
  implausible entry (e.g. cash > 10× equity) with an override, never hard-swallow.
- When cash is unset: render today's behavior + a one-line nudge
  ("Set your cash balance to see total-account value and true concentration").
  Transparent, never silent.

### Derived / display (v1)
- **Total account value** = `Σ Market Value + cash_balance`.
- **Cash %** of the account.
- **True concentration** = name MV ÷ total account value — shown ALONGSIDE the
  existing equity-based `Weight (%)`, clearly labelled (equity-weight vs
  account-weight). Gates unchanged (still equity-weight).
- Coordination: publish total-account-value / cash to a session cache so any
  consumer reads one consistent number (per CLAUDE.md coordination pattern).

### Explicitly OUT of v1
- Growth / return of any kind (needs flows = v2).
- Any change to the 15% / 35% concentration GATES (policy decision, separate).
- Flows, dividends, fees, TWR/IRR.

## Routing & review
- db table + load/save + entry UI: Sonnet-buildable from this spec.
- The true-concentration **display wiring** sits next to gate inputs → Opus lead;
  **Opus review before commit** (concentration-adjacent surface). The review must
  confirm v1 changed NO gate behavior (ceilings still fire on equity-weight).
- Docs: sync `requirements.md` (new F-rows under §3.1 My Portfolio) + the
  constants table if any new constant is introduced (none expected in v1).

## Verification (Streamlit Cloud only — push, ~2 min, Ctrl+F5)
1. Cash unset → app behaves exactly as today + the set-cash nudge appears.
2. Set cash → Total account value = equity + cash; cash% correct; true
   concentration appears next to equity weight.
3. A name over 15% of equity but under 15% of the total account: the **gate still
   fires** (equity-basis), while the displayed account-weight shows the lower true
   number — proving v1 is display-only, no silent policy change.
4. Bad input (negative / absurd) is caught at the form per the data-sanity rule.
