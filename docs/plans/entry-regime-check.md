# Entry regime check: a market-trend hold on new entries (EnterNowInProtect step 2)

**Status:** DESIGNED 2026-11-22 (Opus planner PROCEED WITH CHANGES). Owner decisions taken 2026-11-22: U1 = rule A primary, U2 = B and C shadowed as named secondaries, go-live margin = 15 pp (U5), build C1 + C2 now and C3 after the owner has read C2. **Nothing is wired into any decision.** The go-live criteria below are pre-registered (committed `1510d2d8`) before C2 is run over the past calls. **C1 BUILT 2026-11-22** (`stock_analyzer/entry_regime.py`, `_GATE_FILES` 102, three constants; Opus FIX-FIRST: the no-importer pin walks every module with an allowlist, a tz-aware time converts to its ET date, the fingerprint is keyed by rule version → confirming round). C2 (the readout over every logged call) built alongside; next: its review, the owner's run (checkpoint), then C3 (live daily shadow; the owner applies its DDL first). Bug-register row: EnterNowInProtect.

## 1. Why

On Sun 2026-11-22 the weekly debrief listed ~33 📋 Watchlist Enter Now calls in a week the account fell 20.1% and SPY 8.6%, while 🏠 Home showed "🛡️ Protect Mode". The lead's trace (bug-register EnterNowInProtect) found four unconnected pieces:

1. Protect Mode, Grow Today's bear branch (`daily_briefing.py` ~1146) and the shared entry check's tone hold (`entry_eligibility.check_tone`) are a ONE-DAY S&P move (`classify_tone`, `MARKET_TONE_BEAR_PCT` = -0.5). Any flat or up day in a falling market re-opens entries. The tone hold fails open when the tone can't be read.
2. `exit_advisor.risk_off_regime` (SPY below its `RISK_OFF_TREND_MA` = 200-day average, or VIX ≥ `RISK_OFF_VIX_LEVEL` = 25) arms only the held-position de-risk cards, never an entry.
3. The F-319 trend labels (`market_trend.py`) gate nothing.
4. The Watchlist entry zone is built around today's price (`targets.entry_zone`), so Enter Now in practice = composite ≥ the buy bar + R:R ≥ `RR_ENTRY_MIN` + the shared checks.

## 2. Evidence so far (step 1, `scripts/enter_now_regime_readout.py`, run by the owner 2026-11-22)

1,016 Enter Now calls 2026-09-10..11-20 = 142 episodes. Full figures are in the EnterNowInProtect row. In short: at 30 sessions (pre-registered horizon) 57.4% of episodes beat SPY (N 47), all from a rising market; at 10 sessions 35.0% (N 120). Per call, the S&P trend label separated best (Rising 66.1% vs Weakening 38.5% at 30 sessions; 54.3% vs 33.7% at 10); the one-day tone barely separated at 30 sessions (bear 62.4% vs bull 64.9%); candidates in a Falling sector did best (78.9%), so no sector-Falling block. One market cycle; per-call cells are clustered.

**Honest limit:** an entry hold limits ADDING in a downtrend. It would not have prevented most of a −20% week, which came from leverage on positions already held (strategic review 2026-11-15 §4.1; memory `loss_attribution_2026_10_19`). Leverage by regime (deferred to ~2027-02-15) is the item for that; when built it reads this same regime (U11).

## 3. The rules (pre-registered 2026-11-22)

One verdict per ET date, read from the **last completed session's close** (`market_trend.as_of_session`), the same at 09:45, mid-day and evening; never Home's intraday risk-off read or a live VIX. Three-valued: hold / clear / unknown; unknown is never "clear".

| Rule | Definition | Role |
|---|---|---|
| **A** | S&P 500 F-319 label ∈ {Weakening, Falling} | **Primary** (owner U1) |
| B | S&P 500 label ≠ Rising (adds Steady) | Named secondary |
| C | Risk-off at the prior close: the S&P 500 below its 200-day average (the trend row's `ma200_side`) OR the VIX close dated that session ≥ `RISK_OFF_VIX_LEVEL` | Named secondary |

The Nasdaq label is recorded as an input only. Any other combination, or a split not named here, is **exploratory** and must be marked post-hoc in every readout.

Constants (C1; investment-policy values, owner-approved 2026-11-22 with the rules): `ENTRY_REGIME_RULE_VERSION = 1` (bump on any change to a definition, or to a TREND / RISK_OFF value it depends on; precedent `COMPOSITE_WEIGHTS_VERSION`), `ENTRY_REGIME_HOLD_SPX_LABELS = ("weakening", "falling")`, `ENTRY_REGIME_ALT_HOLD_SPX_LABELS = ("weakening", "falling", "steady")`. Rule C reuses `RISK_OFF_VIX_LEVEL` / `RISK_OFF_TREND_MA`. No hysteresis constant until go-live (U10).

## 4. Go-live criteria (pre-registered 2026-11-22, owner U5; set BEFORE C2 is run)

Judged on **rule A only**, per **episode** (an episode = a run of calls on one ticker no more than 3 NYSE sessions apart, graded from its first call; `enter_now_readout.EPISODE_GAP_SESSIONS`), at the **30-session** horizon (`GATE_LEDGER_HORIZON_TRADING_DAYS`), comparing "A would hold" with "A would pass". All must hold:

1. Each cell has ≥ `GATE_LEDGER_FIRM_CALLS` (15) episodes and ≥ `GATE_LEDGER_MIN_TICKERS` (5) distinct tickers.
2. The hold cell's beat-SPY share is **at least 15 percentage points below** the pass cell's.
3. The hold cell's median alpha is below 0.
4. The 10-session read shows the same sign.

Reported alongside, not decisive: absolute return (a held entry stays in cash or unborrowed margin), the share of sessions and calls held, the winners the hold would have missed, and the verdict's flip count per 20 sessions. Timing: a first read ~2026-12-15; a confirming read ~2027-01-07, when the week of 11-16 has matured. Go live at 12-15 only if all four criteria are clearly met. The rule only tightens, so it can be retired if a later read reverses it.

## 5. Build chunks

1. **C1, `stock_analyzer/entry_regime.py` + constants + tests** (new `_GATE_FILES` member; reviewer). Pure: `vix_close_asof`, `read`, `decide`, `candidates`, `shadow_row`; no streamlit / db / `exit_advisor` imports. Not wired anywhere; a test pins that no decision module imports it. Docs: `docs/review-policy.md` reason line, architecture constants rows, the `market_trend.py` docstring ("display only" no longer true: one measure-only consumer).
2. **C2, readout extension** (reviewer: it produces the go-live evidence). `enter_now_readout.classify_call` gains rule A / B / C verdicts computed **through `entry_regime`**; new splits "Would hold under rule A / B / C" by call and by episode; the Nasdaq label (exploratory); absolute return beside alpha; hold rate and flip counts; `--rec-types` for new_pick / overflow_pick. **Owner checkpoint:** run it; the lead records the numbers here and in the bug register.
3. **C3, live daily shadow** (DB write; reviewer; the owner applies `docs/sql/entry-regime-shadow.sql` first). One market-level row per ET date in `entry_regime_shadow` (inputs, rule A / B / C verdicts, read state, rule version; unique per date and source, first writer wins; RLS `FOR ALL TO service_role`), written by the cron scan lane after the emails in its own try/except (a failed read still writes an "unknown" row). The trend fetch moves from app.py into a shared helper. System Trust store row; `_PROTECTED_TABLES`.
4. **C4, docs + read-only SQL** (the readout joining the shadow rows to the logged calls by date).
5. **Go-live (G1–G4):** a fresh planner pass after the readouts; not designed here.

## 6. Open owner decisions (planner recommendations; decide at go-live)

- U3 as-of basis = last completed session (taken with the design). U4 storage = one market-level row per day; the held calls come from a join (taken with the design).
- U6 live behaviour: hold (no size, a visible card), not a higher composite or R:R bar (A2: the composite doesn't rank alpha; the G-32 analysis found alpha doesn't rise with R:R); smaller size belongs to leverage by regime.
- U7 scope: Watchlist Enter Now (page + 09:45 email), 📈 Analysis not-held Buy sizing, Grow Today picks / overflow / ETF lane, adds to held names, the intraday pullback lane.
- U8 unknown read at go-live: fail closed ("Not an entry today: the market-trend check couldn't run").
- U9 Grow Today's one-day bear branch: unchanged.
- U10 hysteresis: decide from the shadow's flip count.
- U11 leverage by regime reads this same `entry_regime` read when built.
- U12 reports: shadow invisible in the debrief / monthly / emails; at go-live, counterfactual gate-ledger rows (verify free G-ids) and the go-live date recorded (the enter_now population changes).
- U13 card wording at go-live, e.g. "Held: market trend" · "Not an entry today: the S&P 500 is Weakening (below its 50-day average, as of the Fri Nov 20 close)" · clears on "a close that puts the S&P 500 back to Steady or Rising". No beat-rate or probability figure on any surface (A2 rule, docs/queue.md).
- U14 naming: "🛡️ Protect Mode" stays the one-day tone; the trend hold gets its own name (e.g. "📉 Trend hold").

## 7. Not doing

No change to the one-day tone gate (`MARKET_TONE_BEAR_PCT`, `classify_tone`, the Grow Today bear branch); no sector-Falling block (and TrendVsLeading stays a separate owner call); no Home intraday risk-off read or live VIX in the rule; no silent skip on a failed read; no probability figure anywhere.
