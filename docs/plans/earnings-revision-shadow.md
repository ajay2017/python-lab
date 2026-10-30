# Pre-earnings Watch flags and the rating-change shadow (finding #22)

**Status:** C1 SHIPPED 2026-10-30 (`bd7f197`, `stock_analyzer/revision_flag.py`, constant renamed
`EARNINGS_REVISION_WATCH_NET`). C2+C3 (playbook demotion + share wording), C4 (🏠 Brief + 📡 Signals wiring) and C5
(`earnings_revision_shadow` table, EOD cron) built and in review 2026-10-30; they ship in ONE push, after the owner
applies the C5 DDL (`docs/architecture.md` §6.52). Retro study (U5) not started. Readout: not before the trigger below.

## Why

On 2026-10-29, four days before AAPL's report, the 🔔 Catalyst Watch Pre-Earnings Playbook said "REDUCE … Trim 35%:
sell 1 shares" (net analyst revisions −2 over 90 days, `earnings_advisor._recommend`) while the 🏠 Brief's AAPL card
said "Watch: no action today". Two surfaces gave opposite calls on a real holding (CLAUDE.md 10-29 finding #22).

The rule had never been measured in this app, yet its card called negative revisions "one of the strongest miss-risk
signals". And the data isn't estimate revisions at all: `bundle["revisions"]` is built from Yahoo
`upgrades_downgrades` and counts analyst **rating changes** (upgrades + initiations − downgrades, 90 days;
`providers/yfinance_provider.py`).

## Owner decisions (2026-10-30; memory `project_owner_calls_2026_10_30`)

- One rule on every surface: the rating-change signal is a **Watch flag** that sizes nothing, shadow-measured before
  it may ever size a trim again.
- **U1:** the beat-history and bearish-reaction REDUCEs (pasted CNBC context, also never measured) are demoted to
  Watch flags too.
- **U2 (lead took the planner default):** `EARNINGS_REVISION_REDUCE_NET` → `EARNINGS_REVISION_WATCH_NET`, value −2.
- **U3 (default):** no weight floor on the rating-change flag.
- **U4:** first readout at **20 flagged + 20 unflagged matured events**; date backstop: look after two earnings
  seasons, ~May 2027, whatever the count.
- **U5:** run a read-only retro study (below) alongside the forward record.
- **U6:** a withheld composite (read as 0 before) never fires a composite-conditioned rule or prints "0/100".
- **U7 (default):** Grow Today's revision conflict layer (`daily_briefing._cross_reference` Layer 5) stays as is.
- **U8 (default):** the 📡 Signals Active Alerts "revisions" rule (`portfolio.get_alerts`) is queued, not built.

## Chunks

| Chunk | What | State |
|---|---|---|
| C1 | `revision_flag.py` (read / is_flagged / flag_line / revisions_phrase / checklist_line), rename, FMP `basis` tag, yfinance `window_days`, `_GATE_FILES` | SHIPPED `bd7f197` |
| C2+C3 | 🔔 playbook: revision / beat / reaction REDUCEs → flags (never override EXIT, the earnings-week / ceiling REDUCE, weak REDUCE, stop MONITORs; replace only the general HOLD); U6; truthful copy; "Sell N share(s) (about X% of your S shares)" | in review |
| C4 | 🏠 Brief 📅 earnings card `why` + 📡 Signals checklist use the same flag line | in review |
| C5 | `earnings_revision_shadow` + EOD step 7b + System Trust ② store + `_PROTECTED_TABLES` | Opus SHIP; small fixes in progress |
| C6 | this plan + the retro study script | plan written; script not started |

## Readout (pre-registered 2026-10-30; don't change after seeing data)

- **Decision snapshot:** the last row per (ticker, report) with `days_until >= 1`. Day-0 rows may be written after an
  after-close report (EOD runs at or after 16:00 ET) and are never the snapshot.
- **Group by the ACTUAL report date**, not the stored `earnings_date`: a rescheduled or estimated Yahoo date creates a
  group whose event never happens. Don't require the name to still be held at report time.
- **Window covered:** rows exist only for days 0 to `EARNINGS_IMMINENT_DAYS` before a report (the playbook may show the
  flag earlier, up to its look-ahead).
- **Outcomes:** R1 = close on the first trading day strictly after the report date vs close on the last trading day
  strictly before it (spans before-open and after-close timing); R5 = the same to +5 trading days; both as excess
  return over SPY. Trim counterfactual in $ = −`would_trim_shares` × snapshot price × return to the horizon.
- **Comparison:** flagged vs unflagged: mean, median and share with negative excess return; unknown rows reported
  separately, never pooled.
- **Trigger:** 20 flagged + 20 unflagged matured events, or ~May 2027, whichever comes first (U4).
- **Re-enable rule:** the flag may size a trim again only if, at the trigger, flagged events' R1 excess is worse than
  unflagged by a margin the owner sets **before** the readout (OPEN: owner to set the margin), the $ counterfactual is
  positive, and the retro study agrees. Even then it needs planner + reviewer + fresh owner approval, and it can only
  tighten. The beat-history and reaction flags need their own evidence (CNBC context is rarely pasted, so in practice
  they stay Watch-only).

Known limits: first-writer-wins means a row frozen as `rev_state = "unknown"` during a provider outage isn't repaired
by a same-evening forced re-run (same as G-32); `saved` counts include rows `ignore_duplicates` skipped on a re-run.

## Retro study (U5, read-only, not started)

Rebuild the net rating change at T−1 before each past report from Yahoo's dated `upgrades_downgrades` history and
past earnings dates, for held names plus the scan roster over about 8 quarters, and compute the same R1 / R5 excess
returns. Caveats to print with the result: survivorship bias in the roster, Yahoo may revise its history, and
before-open / after-close timing is unknown (hence the window above). Output only to the console / a scratch file;
no writes to the app's tables.
