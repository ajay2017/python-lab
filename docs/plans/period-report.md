# Weekly / monthly report — where the money went, and who made the call

**Status:** QUEUED 2026-10-19 (owner agreed the approach). Not designed in detail, nothing built. Comes after S-D and the
trust-verification detectors. Next step: a planner design pass + an HTML mockup for owner approval.

## Why
On 2026-10-19 a manual, SQL-driven analysis answered "where do my losses come from?" (memory
`project_loss_attribution_2026_10_19`): the AI sell-off week's losses were one high-beta AI/growth pocket × ~3× leverage
× manual trading inside the drop; app-aligned buys beat self-initiated ones (+4.5 vs −0.3pp alpha); exits are a
trade-off (owner's post-exit timing better, app's realized P&L better); and the stored data passed a recompute check
(`docs/plans/app-trust-verification.md`). The owner wants this as a recurring weekly/monthly report instead of an ad-hoc
SQL session.

## Approach (agreed)
**Extend the existing Performance Review** (💰 Account → Reports, F-276b, `stock_analyzer/performance_review.py`) rather
than build a new page. Add period presets *Last week · Last month · Quarter · Custom* and five sections:

1. **Period result & reconciliation** — change in net equity split into holdings' price moves, leverage effect, flows
   (`account_flows`), margin interest (`snaptrade_income_events`), and the trading-timing remainder.
2. **Where it came from** — top contributors/detractors by ticker and sector (`portfolio.TICKER_SECTORS`, fallback
   chain), top-5 share of losses, the high-beta pocket's share of gains vs losses.
3. **Leverage & margin** — leverage range, the closest margin-call distance in the period, days above a leverage level
   (`account_daily_snapshots`).
4. **Who made the call** — realized P&L app-called vs manual (`trades.trigger_type`), buys into names the app rated
   Hold, turnover vs net equity.
5. **Your edge vs the engine** — buy-side and sell-side Self Track Record alpha, with the sign spelled out (sell side:
   positive = sold early). Note these are all-time measures today; decide period-scoping in design.

Plus a **data-trust strip** (last weekly impossible-states/recompute result, any feed outage in the period). Then a
**weekly email** digest (the "emailed report" v2 item already listed for F-276), and optionally stored weekly
snapshots for trends.

## Design requirements (from the 10-19 analysis)
- All arithmetic in a new pure, tested module (e.g. `stock_analyzer/period_attribution.py`); `app.py` render-only.
- **Reconcile to net equity:** use trade prices for a position's buy day and sell day — close-to-close marks miss
  both (the 10-19 "−$1,314 vs −$683" gap).
- **Don't use `trades.followed_signal`** (text, mostly NULL on manual trades, contradicts `signal_seen` at least once);
  classify from the verdict stored at trade time (`signal_seen` / `decision_context`) — verify what is reliably stored.
- Respect the sentinels: a missing day/cash record is "unknown", never a fabricated zero; disclose coverage gaps.
- Renderer traps: `$` amounts in `st.metric` or escaped (`\\$`); compare like units (the F-276b %-vs-$ lesson).
- Display-only: no gate, threshold or recommendation changes. Mockup first (memory `feedback_mockup_first_ux`).
