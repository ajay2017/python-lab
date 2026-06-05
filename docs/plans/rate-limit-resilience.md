# Plan: Rate-Limit Resilience

**Status:** approved 2026-06-05. Phase 1 in progress.

## Problem (from the 2026-06-05 pre-market incident)
A transient provider hiccup escalated to near-exhaustion of all three data
providers (Finnhub / Yahoo / FMP all returning 429) because three independent
gaps stacked:

1. **No refresh cooldown.** "Refresh All Data" / "Refresh Signals" / Grow "Retry"
   each do `st.cache_data.clear()` (nuclear) then re-fetch everything. Three
   clicks in 7 min = three full fan-out storms.
2. **No provider backoff.** `api_health` *detects* 429s (goes red at
   `rate_limits ≥ 3`) but the orchestrator never consults it — it re-calls a
   dead provider on the next cache miss and eats another 429.
3. **No FMP daily-budget guard.** FMP free tier = 250 calls/day and it's the
   *last* failover; nothing tracks the count, so a couple of refreshes can burn
   the day's budget by mid-morning.

## Design principle
Keep **failing loudly** — the breaker only stops *wasteful* calls; it must never
silently serve stale/fabricated data. "Could not load" banners stay; the app
degrades visibly to offline/cached.

> "429-backoff" = a **circuit-breaker (back *off* the provider)**, NOT
> retry-with-sleep. Retrying into a 429 deepens the limit and blocks the UI
> thread.

## Phases (each independently shippable + validatable)

### Phase 1 — Refresh cooldown · low risk · IN PROGRESS
Gate the three heavy price-path buttons behind a shared cooldown
(`REFRESH_COOLDOWN_SEC`); disabled state shows "available in Ns" + why.
- Buttons: Refresh All Data (`app.py:825`), Refresh Signals (`app.py:3041`),
  Grow Retry (`app.py:3354`) — shared "data" bucket (all hit price providers +
  full cache clear).
- Out of Phase 1: the two calendar refreshes (FRED / earnings — different
  providers, not part of the incident). Trivial to add later if wanted.

### Phase 2 — Provider circuit-breaker · medium risk · mandatory Opus review
Orchestrator skips a provider that's tripped (reuse `api_health` red:
`rate_limits ≥ 3`) for `PROVIDER_RL_COOLDOWN_SEC`; auto-recovers after the
window. Guard: if ALL providers are in cooldown, fall through and try anyway
(degrade loud, never hard-block forever).
- Touches: `api_health.py` (add `in_cooldown(provider)` helper),
  `orchestrator.py:201-219` + `:115-171`, `constants.py`.

### Phase 3 — FMP daily-budget guard · medium risk
Track FMP calls per ET day; stop calling FMP at `FMP_DAILY_SOFT_CAP` to
preserve last-resort headroom.
- Touches: `fmp_provider.py` (ET-day counter + soft-cap), `constants.py`.
- Known limitation: process-local counter resets on Streamlit reboot — bounds
  in-session hammering (the actual incident), not a hard cross-reboot ceiling.
  Persistent counter (Supabase) is a future upgrade.

## Policy values (operational infra knobs — reversible one-line changes, tune from observation; NOT investment-decision thresholds)
| Constant | Default | Controls |
|---|---|---|
| `REFRESH_COOLDOWN_SEC` | 60 | Refresh-button lockout after a press |
| `PROVIDER_RL_COOLDOWN_SEC` | 120 | How long a tripped provider is skipped |
| `PROVIDER_RL_TRIP_COUNT` | reuse api_health red (≥3) | 429s before breaker opens |
| `FMP_DAILY_SOFT_CAP` | 220 (of 250) | Stop calling FMP at this count |

## Out of scope (deferred)
Selective cache-clear (refresh prices without nuking fundamentals); request
coalescing; per-provider Finnhub/FMP backoff; persistent FMP counter.

## Routing
P1 → implementer + Opus review. P2 → implementer + **mandatory** Opus reviewer
(touches failover). P3 → implementer + Opus review. Each phase: push → Streamlit
Cloud → validate → next. Logged in `docs/cost-routing.md`.
