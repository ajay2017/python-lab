# Sell-check-unread batch 3A + 3B: a protective check that couldn't run must not read as clean

**Status:** DESIGNED 2026-11-27 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES; read at HEAD `1b0ea74f`). **Owner took every recommendation 2026-11-27** (U-A … U-H and the regime-unread premarket rule, §7; incl. U-C's reversal of the RiskOffUnchecked C4 spec's "missing regime → silent" pin). **C1 + C2 + C3 BUILT 2026-11-27** (implementer `claude-sonnet-5-5`; lead fixed the both_unread double negative and the `test_held_stop_lanes.py` read of the retired `_REGIME_UNREADABLE_TEXT`); **Opus reviewer `claude-opus-5-5`: SHIP, 0 blocking** (arming invariance probed on a 13 × 21 SPY × VIX grid, 0 mismatches; `_SYNTH_SCHEMA_VER` bump not needed for C2, folded into C6). After the other session's buy-check C4b commit (`f58d7334`) the targeted files passed (956 tests: regime_read, risk_off_check, protective_coverage, held_stop_lanes, home_dock_digest, daily_briefing, git_hook_adapter); C1 + C2 + C3 committed as one commit ("fix(risk): say when the risk-off regime couldn't be read"). C2's docs shipped in `dee46ab4`. **C4 BUILT 2026-11-27** (premarket wiring; implementer `claude-sonnet-5-5`; the C3 carry items done: `_last_bar` DatetimeIndex guard, plurals, "N of M not flagged", new `gap_tickers`; 975 targeted tests passed; **Opus reviewer `claude-opus-5-5`: SHIP, 0 blocking**, no false reds found on Mon / Thanksgiving-week / half-day / forced-after-open / ETF / fractional / young-listing mornings). First scheduled run with it: Mon 11-30 premarket (expect "held coverage: N of N not flagged" and "risk-off regime: state=… (book …)" log lines, no new failure line). C4 review non-blocking notes (follow-up, not yet done): strengthen the AST pin to "every non-`reason` return carries `coverage`"; the "nothing to act on — no email." log still prints beside other red failures (manual_stops_unreadable / held_stops_failed / held_stop_floor / carry / basis) with no alerts: key that branch on them too; `held_cached` says "these names" for one name; "(stops were checked normally)" / "Stop checks are unaffected." can sit under a saved-copy line: say "Stop checks don't use the S&P 500 history."; `_notify_failure` (cron_runner ~338) interpolates `detail` unescaped (HTML escaping Phase 2 class; now carries raw exception text); add a Yahoo-shaped `tz="America/New_York"` index test row. C5 carry: the premarket's `exit_signals` capture (~799-836) writes cached-price rows as today's signals (new row ExitSignalsCachedPrice); flip `test_eod_lane_is_not_wired_in_c4`. **C4 SHIPPED `5b1548d0`** (first commit attempt hit the hook's 900 s pytest timeout under load; retried once, passed). G-36 docs shipped with it the same session. **C5 PAUSED by the owner 2026-11-27** so buy-check C4m goes first in the same cron files: the implementer's code (written, compiled, tests written but never run, no review) is saved LOCALLY at `.git/wip-sellcheck-c5/` (`c5-tracked.patch` + `test_protective_coverage_eod.py` + README with the resume commands and the EOD write-path table: the `held_stop_carry` writer CAN save a stop built from a cached frame, contrary to the C4 reviewer's assumption, so C5 adds a guard; correlation history, rec_events weights, sentiment snapshot and model_predictions can still record saved-copy data and need named rows). Next after C4m: resume C5, then C6 (display), C7 (architecture).

**Reviewer carry items (non-blocking, fold into the named chunk; each gets that chunk's review):**
- C4 first: `protective_coverage._last_bar` (~146) returns None unless `isinstance(df.index, pd.DatetimeIndex)` (a RangeIndex reads 1970-01-01 → "14836 sessions behind" → red; many lane fixtures use RangeIndex frames). Plural "its stop" → "their stops" in `lane_failures` (~294, ~310) when several names. `_UNVERIFIED_LINE` names "the held-ticker list" even for a bad `expected_session` (unreachable from the cron). `log_lines` "N of M fresh" counts `not_dated` names: say "N of M not flagged".
- C6: `regime_read.posture_regime_state` returns "partial" when one leg is TRIPPED and the other unread (pinned `tests/test_regime_read.py` ~187), unlike `display_line` (None when a leg trips): make a tripped leg read "measured" or test that Summary / Risk Analysis still say risk-off. Swap Summary (~15730) and Risk Analysis (~19138) off `risk_off_state` soon: until C6, with VIX unread and SPY above its average, Summary's Risk Posture says "the market regime is calm" right under the new "only partly known" warning (the old RegimeOneLegMeasured bug, now visible beside the true line). Consider aligning Home's header / dock ("de-risk check couldn't run") with regime_unread's "couldn't fully run". `regime_read.signature` joins `_synth_sig` + the `_SYNTH_SCHEMA_VER` bump here.
- C2 follow-ups (a small reviewed commit, any time): the post-exception fallback text says "your portfolio beta couldn't be measured" even for a caution / fragile book (unreachable; pinned `tests/test_risk_off_check.py` ~363) → neutral text; "would have armed a de-risk trim" overclaims (cards also need a holding with beta ≥ `RISK_OFF_NAME_MIN_BETA`) → "would have armed the de-risk check"; `_floor_1dp` duplicated in `risk_off_check` and `regime_read`. Optional: a negative / zero VIX reads CLEAR (arming unchanged per spec).
- C5: confirm the `held_stop_floor` / carry writers write nothing for a name on a cached frame. Lead spot-checked 2026-11-27: history is Yahoo-only (`providers/orchestrator.py` ~150-153), `stale_as_of` is a calendar date (`db.py` ~2227 `fetched.date()`), the RiskOffUnchecked C4 spec pins "regime inputs missing → None" (`docs/plans/risk-off-unchecked-c4-spec.md` ~20, `tests/test_risk_off_check.py` ~86-92), `data.previous_trading_day` / `data.trading_sessions_between` exist (`data.py` ~185 / ~194). Item (3) of the owner's 2026-11-26 order ("a sell check that couldn't run reads as clear"); 3C (ConcTrimsRiskNone, DetSpyFail Home side, YoungUnprotected) is a later batch.

Rows: CronStaleHeld, DetSpyFail (cron leg), RiskOffRegimeUnread, plus two new rows found 2026-11-27 by the lead: **RiskOffUncheckedRegimeBlind** (`risk_off_check.derisk_unchecked` is silent when both regime legs are missing: `risk_off_regime` never raises on missing data, so `_REGIME_UNREADABLE_TEXT` is unreachable) and **RegimeOneLegMeasured** (`exit_advisor.risk_off_state` calls one usable leg "measured", so VIX missing + SPY above its 200-day reads calm on 🧾 Summary / 🔗 Risk Analysis).

## 1. Changes from the brief, and corrected claims

- (a) **Don't change `exit_advisor.risk_off_state`.** `enter_now_readout.py` ~595-609 (the pre-registered EnterNowInProtect readout, `docs/plans/entry-regime-check.md` §4) reads its "measured" verdict; changing it alters a pre-registered measurement. The per-leg read is a NEW module `regime_read.py`; only Summary / Risk Analysis move to it.
- (b) **Fix RiskOffUncheckedRegimeBlind and the app-side RiskOffRegimeUnread inside `risk_off_check.derisk_unchecked`.** It already owns "the de-risk check couldn't run" and already feeds Home's Act Today header (app.py ~13796-13838), 🧾 Summary (~15931), the dock digest (`home_dock_digest.py` ~90) and the premarket footer + red lane (cron_runner.py ~712-729). Widening its decision lights every surface with no new Brief key or cache; a parallel "regime unread" key would be a second opinion on the same dimension.
- (c) **This reverses a pinned owner decision** (C4 spec "regime inputs missing → None (nothing fired)", tests `test_risk_off_check.py` ~86-92 / ~148). The owner accepts it via U-C; the commit says so.
- All history (SPY 6mo, SPY 1y, ^VIX, every bundle) is Yahoo-only: Finnhub advertises only live price, FMP left the history order 2026-10-27. Failures are correlated: one Yahoo history outage gives, at once, a book served from cache, deterioration on depth only, an unread regime and fragility None.
- Today a warm cache + a Yahoo outage gives a **green premarket** with every stop judged on saved prices; the only red is the news-feed outage line (wrong cause, deduped once a day; cron_runner.py ~529-557).
- SPY 6mo None also makes beta None (risk.py ~562-575) → fragility None: a 6mo + 1y failure is silent on risk-off as well.
- `stale_as_of` is a UTC calendar date only (`db.py` ~2227): a copy "1 session old" can't be told from one saved mid-session (an intraday price, not a close). Staleness is keyed on "served from cache"; age is reported from the cached frame's last bar.
- `risk_off_state` also calls a NaN VIX "measured" (`float(nan)` succeeds).
- cron_runner anchors in the 2026-11-27 brief were working-tree numbers; at HEAD the engine-note loop is ~624-626, `_price_outage` ~637-644, the EOD priced/unpriced guard ~1141-1173. Re-check every anchor after each rebase.

## 2. Decision modules

### `stock_analyzer/regime_read.py` (NEW gate module; pure, never raises, no clock)

```python
TRIPPED, CLEAR, UNREAD = "tripped", "clear", "unread"
def read_regime(spy_trend_df, vix_level, *, trend_ma=RISK_OFF_TREND_MA,
                vix_threshold=RISK_OFF_VIX_LEVEL) -> dict
#  {"trend": leg, "vix": leg,
#   "state": "risk_off" | "calm" | "partial" | "unread",
#   "reasons": [...],               # byte-identical to exit_advisor.risk_off_regime(spy, vix)
#   "unread": ["trend"?, "vix"?],   # in that order
#   "trend_unread_why": None | "no_data" | "short_history",
#   "vix_value": float | None}
def posture_regime_state(read) -> "measured" | "partial" | "not_measured"
def display_line(read, *, trend_ma=..., vix_threshold=...) -> str | None
def unread_phrase(read) -> str | None
def log_text(read) -> str
def signature(read) -> tuple[str, str]          # (trend leg, vix leg)
```

Legs reuse exit_advisor's own arithmetic (never copy the threshold math):
- **Trend leg:** UNREAD when `exit_advisor._series_close` gives None or fewer than `trend_ma` closes. Otherwise TRIPPED if `risk_off_regime(spy, None, …)` arms, else CLEAR.
- **VIX leg:** UNREAD when the value is None, not numeric, or NaN. Otherwise TRIPPED if `risk_off_regime(None, vix, …)` arms, else CLEAR (`inf` counts as TRIPPED).
- **State:** `risk_off` if any leg is tripped. Otherwise `calm` with 0 unread legs, `partial` with 1, `unread` with 2.

**Invariant (grid-tested):** `state == "risk_off"` exactly when `risk_off_regime(...)[0]` is true. Arming never changes.

### `risk_off_check.derisk_unchecked` (existing gate file; return shape kept + one key)

The dict gains `"kind"`, one of `"beta_unmeasured"`, `"regime_unread"` or `"both_unread"`; `"reasons"` and `"text"` are unchanged. A dict without a recognised severity (`{}`, `{"severity": "ok"}`) is treated like None, because `assess_risk_off_derisk` can't arm on it either.

| fragility | regime state | result |
|---|---|---|
| calm | any | None (the check can't arm) |
| caution / fragile | risk_off or calm | None (the check ran) |
| caution / fragile | partial / unread | `regime_unread` |
| None / unrecognised | risk_off | `beta_unmeasured` (text byte-identical to today) |
| None / unrecognised | calm | None (moot) |
| None / unrecognised | partial / unread | `both_unread` (+ coverage note when coverage is unmeasured) |

`_REGIME_UNREADABLE_TEXT` is retired; its case goes to `both_unread`.

### `stock_analyzer/protective_coverage.py` (NEW gate module; pure, never raises)

```python
def expected_session(today: date, *, lane: str) -> date
#  premarket: data.previous_trading_day(today)
#  eod: today if data.is_trading_day(today) else previous_trading_day(today)
def price_coverage(held_tickers, held_data, port_df, *, expected_session: date) -> dict | None
#  None = held_tickers not a list/tuple (unverifiable; same rule as the EOD guard)
#  {"state": "full" | "gaps", "expected_session": iso,
#   "unpriced": [{"ticker", "reason": "bundle_failed" | "no_price_data" | "invalid_shares_or_cost"}],
#   "cached":   [{"ticker", "last_bar": iso | None, "sessions_behind": int | None}],
#   "behind":   [{"ticker", "last_bar": iso, "sessions_behind": int}],   # fresh read, last bar < expected
#   "not_dated": [ticker]}                                              # log only: no df, or last bar > expected
def deterioration_basis(spy_df, *, lookback=REL_STRENGTH_LOOKBACK_DAYS) -> "full" | "depth_only"
#  must call exit_advisor._pct_return(_series_close(spy_df), lookback) so the boundary can't drift
def lane_failures(coverage, *, lane, deterioration_basis, deterioration_error, risk_off_error) -> list[str]
def email_lines(coverage, *, lane, deterioration_basis) -> list[str]
def alert_note(coverage, ticker) -> str | None
def eod_withheld(coverage) -> list[str]          # cached tickers the EOD treats as unpriced
def log_lines(coverage) -> list[str]
```

- **"Priced"** uses the EOD guard's rule: `port_df` has the row with `Price > 0`.
- **Unpriced reasons:** a ticker missing from `held_data` is `bundle_failed`; otherwise the reason comes from `port_df.attrs["dropped_holdings"]`, or `no_price_data` if it isn't there.
- **Dating bars:** the last bar is dated with `held_stop._et_dates(bundle["df"].index)`; sessions are counted with `data.trading_sessions_between`.
- **Cached names** appear only in `cached`, never also in `behind`.
- **`behind`** covers plain US symbols only (`^[A-Z]{1,5}(-[A-Z]{1,2})?$`, the finnhub_provider.py ~29 rule), so a foreign-listed holding's own holidays never read red.

**No new constant anywhere** (any one name, no tolerance). `RISK_OFF_TREND_MA` / `RISK_OFF_VIX_LEVEL` are unchanged.

## 3. Chunks (build order)

- **C1, `regime_read.py` + `tests/test_regime_read.py`** (decision-bearing; implementer).
  - Joins `_GATE_FILES` + review-policy line: "regime_read.py — per-leg risk-off regime read; decides 'partly read' vs calm for every regime display and the de-risk unchecked check."
  - Can start now: new files only.
- **C2, `derisk_unchecked` widened** (implementer → Opus reviewer together with C1).
  - Files: `risk_off_check.py` (HEAD ~29-74), `tests/test_risk_off_check.py`. Flip ~53 / ~86-92 / ~148 deliberately and name them in the commit body.
  - No call-site change: daily_briefing.py ~4731 and headless ~556 already pass `spy_trend_df` / `vix`.
  - `_SYNTH_SCHEMA_VER` 37 → 38 (the cached Brief's `risk_off_unchecked` meaning changes); wait for C4b's commit.
  - Live effect: Home's "⚠️ Act Today — de-risk check couldn't run", Summary, the dock and a red premarket lane fire on a regime-unread day when the book is caution / fragile or unmeasured.
- **C3, `protective_coverage.py` + `tests/test_protective_coverage.py`** (implementer).
  - Joins `_GATE_FILES` + review-policy line: "protective_coverage.py — decides which held names / checks the cron protective and EOD lanes did not really check (unpriced, cached, behind, depth-only, raised) and turns them into lane failures."
  - Can start now.
- **C4, premarket wiring** (implementer → Opus reviewer together with C3; waits for C4b / C4m in `headless_alert_engine.py`).
  - `headless_alert_engine.py`:
    - ~165-168: append "SPY 1y fetch failed".
    - In `compute_protective_alerts`: `coverage = price_coverage(ctx["held_tickers"], held_data, port_df, expected_session=expected_session(today, lane="premarket"))`, `det_basis = deterioration_basis(spy_6mo)`, `regime = read_regime(spy_1y, vix)`.
    - ~436-440 / ~536-543: keep the raised message in `deterioration_error` / `risk_off_error` as well as the engine note.
    - Append `alert_note(coverage, t)` to each alert's `why`, risk-off cards included.
    - Return keys `coverage`, `deterioration_basis`, `deterioration_error`, `risk_off_error`, `regime_read`.
  - `cron_runner._run_premarket` (HEAD ~597-903):
    - Log `log_lines(coverage)` and `regime_read.log_text(...)` on every run.
    - `failures.extend(lane_failures(..., lane="premarket"))` and `_footer_lines.extend(email_lines(...))`, after the `_price_outage` block and only when `not _price_outage` (a total outage keeps its own line).
    - The "nothing to act on — no email" log (~868-874) never prints with coverage failures; use "no protective alert — a check didn't run, see the failure above".
    - Dedup state is saved; a later EDT slot that re-prices the name still sends (the fingerprint changes).
- **C5, EOD leg** (DB-write path; implementer → reviewer).
  - `compute_eod` (headless ~1870-1950): coverage with `lane="eod"`; drop `eod_withheld(coverage)` names from `snapshot_rows` and `book_positions` / `gross_book`.
  - `cron_runner._run_eod` (HEAD ~1141-1173): `_unpriced |= eod_withheld`, each printed "ABC (saved copy only)" inside the existing `partial_price_outage` text. The existing records-withhold rule then skips the account row and blanks the risk row; `held_tickers` keeps the name from being swept.
  - EOD `behind` is log only: "eod price basis: GHI last bar 2026-11-24, expected 2026-11-25 (shadow, not a failure)".
  - Reviewer to confirm: `_held_stop_floor.rows_to_write` and the carry writer write nothing for a name whose held stop fell back to a cached frame (the carry seed already needs a bar dated today).
- **C6, 3B display** (implementer → reviewer; waits for C4b's app.py region).
  - `exit_advisor.market_risk_posture` (HEAD ~583-590): a `regime_state == "partial"` branch, "the market regime is only partly known"; score / armed / risk_off unchanged.
  - app.py Summary (~15692-15712): `risk_off_state` → `regime_read.posture_regime_state(read_regime(_cached_spy("1y"), _cached_vix()))`. The caption at ~16503-16507 becomes `display_line(...)`; the ❔ icon logic stays.
  - Risk Analysis (~19100-19170): the same swap; the two hard-coded `not_measured` / `unusable` captions → `display_line`.
  - `regime_read.signature(read)` joins `_synth_sig` (~7575) (U-G).
  - app.py stays render-only, with no comparison against a constant.
- **C7, docs** (lead: they carry policy values).
  - requirements: new **G-36** "A protective check that couldn't run is loud" (cron coverage, depth-only, raised engines); **F-271a** "Regime partly read"; F-321 RiskOffUnchecked text gains the regime_unread / both_unread kinds.
  - architecture: a section for both modules; no new constant row (still run `scripts/check_constants_documented.py`).
  - coordination-caches: no new key; document `risk_off_unchecked`'s `kind` and that it can now be set on a calm-regime-unread day.
  - User Guide: the Act Today paragraph (app.py ~47344) and the Risk Posture text.
  - bug-register: close RiskOffRegimeUnread, CronStaleHeld and DetSpyFail's cron leg; add + close RiskOffUncheckedRegimeBlind and RegimeOneLegMeasured; add the §5 rows.
  - Also: review-policy (two members; re-print the count), shipped-log, this Status line, the CLAUDE.md queue line.

### Tests the build must include

- **regime_read:**
  - Grid SPY {None, no Close, 199 bars, 200 bars above / below / last == SMA} × VIX {None, "x", NaN, 24.99, 25.0, inf, 30}. On every cell: `state == "risk_off"` ⇔ `risk_off_regime` arms, and the reasons are identical.
  - NaN VIX → unread. Exactly 200 bars is readable; 199 is unread with `short_history`.
  - `display_line` / `unread_phrase` take their numbers from the parameters (monkeypatch `trend_ma=150` → "150-day").
  - A below-threshold VIX never prints as ≥ threshold: floor to one decimal, so 24.96 / 24.999 → "24.9".
  - No `$` or `**`; never raises.
  - Pin `risk_off_state`'s outputs unchanged on the grid, and that `enter_now_readout` still calls it.
- **derisk_unchecked:**
  - Every table row.
  - Arming unchanged: on a fragility × regime grid, `assess_risk_off_derisk` returns cards exactly as before, and `derisk_unchecked` is None whenever cards exist.
  - The `beta_unmeasured` text stays byte-identical.
- **Brief:** caution book + SPY above trend + VIX None → `risk_off_unchecked.kind == "regime_unread"` with `act` unchanged; a calm book with the same inputs → None.
- **protective_coverage, time-context matrix:**

  | Case | Today / now | Last bar | Expected result |
  |---|---|---|---|
  | Monday premarket | Mon 2026-11-30 | Fri 11-27 (half day) | none behind |
  | Day after Thanksgiving | Fri 11-27 premarket, expected Wed 11-25 | cache bar Wed | sessions_behind 0 |
  | Day after Thanksgiving | same | cache bar Tue | sessions_behind 1 |
  | Day after a Monday holiday | Tue 2027-01-19, expected Fri 01-15 | — | — |
  | DST Monday | 2027-03-15 07:00 EDT, tz-aware bars | — | correct ET dates |
  | Forced Saturday run | — | — | expected = Friday |
  | Old cache, Monday premarket | — | Wed bar | cached, sessions_behind 2 |
  | Cache past `BUNDLE_CACHE_MAX_AGE_DAYS` | — | — | `bundle_failed` |
  | EOD (expected = today) | — | cached bar yesterday | withheld |
  | EOD fresh read | — | bar yesterday | log only, rc unchanged |
  | Fresh read | — | bar after expected, or a tz-naive index | not flagged |

  Also: SHOP.TO behind → not flagged; `held_tickers` None → `held_coverage_unverified`; `deterioration_basis` with 20 bars → depth_only, 21 → full.
- **Lane tests** (invariant: an unchecked held name never reads as a clean scan):
  - One cached name and no alerts → rc 1, failure email, no "nothing to act on" log.
  - All-cached book → red, every name listed, computed alerts still sent with the note.
  - A cached breached name's alert `why` carries the note.
  - SPY 6mo None → red with the depth-only line.
  - Each raised engine → red.
  - Clean day → no new line (regression).
  - Total outage → only the old `price_outage` line.
- **AST pins, both ends:**
  - `compute_protective_alerts` returns `"coverage"` built by `price_coverage(`.
  - `_run_premarket` extends `failures` with `lane_failures(`.
  - app.py has zero `risk_off_state` uses and two `regime_read.display_line(` uses.
  - `_synth_sig` contains `regime_read.signature(`.

## 4. Sequencing against buy-check C4

- **Can start now (no file overlap):** C1 and C3, new files plus their tests. Stage by exact path.
- **Wait for C4b's commit:**
  - C2: only its `_SYNTH_SCHEMA_VER` line (an app.py ~7571-7600 C4b hunk).
  - C4: `headless_alert_engine.py` / `cron_runner.py` are dirty. The regions (`_build_context` / `compute_protective_alerts` ~125-590, `_run_premarket` ~597-903) don't overlap C4b's hunks (cron ~103 / ~469 / ~2580 / ~2649 / ~2793, headless ~913-925), but the commits must not interleave.
  - C6: overlaps app.py ~7571-7600.
- **C4m** (headless ~735-750, daily_briefing ~1064): different regions; commit C4m and this C4 one after the other, in either order.

## 5. Sibling sites

**Fixed here:**
- headless ~160-164 (SPY 6mo), ~165-168 (1y, no line), ~94-102 (`_vix_level` swallow, now named by `regime_read`), ~176-180 (dropped bundle), ~382 (stop gap on a cached price), ~436-440 / ~536-543 (raise legs), ~1870-1950 (EOD snapshot / gross_book).
- cron_runner ~624-626 (engine notes only), ~1141-1173 (EOD priced set), ~868-874 (false "nothing to act on" log).
- risk_off_check ~44-55; exit_advisor ~583-590.
- app.py ~15692-15712, ~16503-16507, ~19100-19170, ~7575.

**Left to named rows:**
- **DetSpyFail-Home** (3C): daily_briefing `deterioration_signals` gets an app `_cached_spy("6mo")` None. Interface: `protective_coverage.deterioration_basis(spy_df)` + its `email_lines` sentence.
- **ConcTrimsRiskNone** (3C): needs nothing from this batch.
- **YoungUnprotected** (3C): exit_advisor ~339-341 (NaN SMA) and ~368-369 (`name_ret` None under 21 bars, so RS reads 0). Interface: a per-name `basis` entry on `price_coverage`'s output, same shape.
- **HeldStopFooterNoAlert:** held-stop young-name footer lines (cron ~690-700) still ride only inside an alert email.
- **ForwardSimRegimePartial:** forward_sim.py ~514-535, the replay reads a missing leg as calm. Display, low.
- **RegimeReadoutPartial:** enter_now_readout.py ~604, one leg counts as "measured". Pre-registered: decide at the ~12-15 read, never before.
- **BenchmarkBarAge:** the age of the VIX / SPY last bar is never checked (headless ~94-102, app ~4379-4387, the spy_1y last bar). One freshness per number.
- **VelocityUnread:** cron ~792-797, a failed exit_signals read means no velocity alerts, logged only. Awareness, not a sell.
- **YahooNaN** stays open for the re-price; C4 / C5 only detect its premarket leg (red) and its EOD leg (shadow).
- **Not raised as rows:** the split-check failure (headless ~420-425) is a documented accepted fail direction; a forced run after the open reads intraday bars (forced-only).

## 6. Wording, email and heartbeat

All lines are plain text with no `$` and no `**`. Numbers come from the parameters (`{trend_ma}`-day, VIX `{vix_threshold:g}`). Dates use `f"{d:%a %b} {d.day}"` ("Wed Nov 25"; `%-d` fails on Windows). "1 session" / "2 sessions" agree in number.

### Premarket failure lines (red heartbeat + failure email on every run that has one)

- `held_unchecked -- not checked this morning: XYZ (no price from any provider); its stop, deterioration and risk-off checks did not run (protective alerts that were computed were still sent)`
- `held_cached -- checked against a saved price copy, not a fresh read: ABC (last price Wed Nov 25), DEF (Mon Nov 23, 2 sessions old); stop and exit calls for these names may be wrong (protective alerts that were computed were still sent)`
- `held_price_behind -- price history ends before the Wed Nov 25 close: GHI (ends Mon Nov 23, 2 sessions behind); its stop was checked against an old close (protective alerts that were computed were still sent)`
- `held_coverage_unverified -- the held-ticker list was unavailable, so it can't be confirmed every holding was checked`
- `deterioration_depth_only -- the S&P 500 6-month history couldn't be read, so deterioration exits were checked on drawdown depth alone; an exit that needs the stock to lag the market couldn't fire (stops were checked normally)`
- `deterioration_failed -- the deterioration check raised an error (<msg[:80]>); no deterioration exit was checked today`
- `risk_off_failed -- the risk-off de-risk check raised an error (<msg[:80]>); no de-risk trim was considered today`

### Alert-email footer lines

- "Not checked this morning: XYZ. No provider returned a price, so its stop and exit checks did not run. Look at it in your broker before the open."
- "ABC was checked against a saved copy of its Wed Nov 25 price, not a fresh read. The copy may be from before that day's close, so check the live price before acting on any call for ABC."
- "ABC was checked against a saved copy of its Mon Nov 23 price (2 sessions old), not a fresh read. Its stop and exit checks may be wrong: check the live price before acting."
- "GHI's price history ends Mon Nov 23, 2 sessions before the Wed Nov 25 close this check needs, so its stop was checked against an old close. Check the live price before acting."
- "Deterioration exits were checked on drawdown depth only today: the S&P 500 history couldn't be read, so an exit that needs the stock to be lagging the market couldn't fire. Stop checks are unaffected."

### Per-alert note (appended to `why`)

- Cached: "Priced from a saved copy (last price Wed Nov 25), not a fresh read: check the live price before acting."
- Behind: "Its price history ends Mon Nov 23, not at the Wed Nov 25 close: check the live price before acting."

### `derisk_unchecked` texts

These reach the Home warning, Summary, the dock, the footer and the failure email. `<book>` is "more volatile than the market" for caution and "would fall outsized in a routine pullback" for fragile.

- **regime_unread, VIX unread:** "Risk-off de-risk check couldn't fully run: VIX couldn't be read today, so the market regime is only partly known (the S&P 500 is not below its 200-day average). Your book is <book>, so a VIX reading of 25 or more would have armed a de-risk trim. Review your highest-beta holdings yourself before adding risk."
- **regime_unread, trend unread:** "Risk-off de-risk check couldn't fully run: the S&P 500's 1-year history couldn't be read today, so the market regime is only partly known (VIX is 18.4, below 25). Your book is <book>, so the S&P 500 closing below its 200-day average would have armed a de-risk trim. Review your highest-beta holdings yourself before adding risk."
- **regime_unread, both legs unread:** "Risk-off de-risk check couldn't run: neither VIX nor the S&P 500's 1-year history could be read today, so the market regime is unknown. Your book is <book>, so either signal (VIX at 25 or more, or the S&P 500 below its 200-day average) would have armed a de-risk trim. Review your highest-beta holdings yourself before adding risk."
- **both_unread:** "Risk-off de-risk check couldn't run: your portfolio beta couldn't be measured, and <VIX | the S&P 500's 1-year history> couldn't be read either (both legs: "and neither VIX nor the S&P 500's 1-year history could be read"; no double negative, lead fix 2026-11-27), so the check had nothing to go on today. Review your highest-beta holdings yourself before adding risk." Plus the coverage note when coverage is unmeasured.

(The 200 / 25 / 18.4 above are examples; the build formats them from `trend_ma`, `vix_threshold` and the read VIX.)

### `display_line` captions (Summary Risk Posture, Risk Analysis dial)

- "⚠ VIX couldn't be read this session, so the market regime is only partly known: the S&P 500 is not below its 200-day average, but a VIX reading of 25 or more would not show here. Treat the regime as unknown, not calm."
- "⚠ The S&P 500's 1-year history couldn't be read this session, so the market regime is only partly known: VIX is 18.4 (below 25), but a close below the 200-day average would not show here. Treat the regime as unknown, not calm." With `short_history` the opening reads "The S&P 500's history was too short for the 200-day check this session".
- "⚠ Neither VIX nor the S&P 500's 1-year history could be read this session, so the market regime is unknown. The book half of this posture is shown; no risk-off action is armed on missing data."

### Cron log lines (every run)

- "risk-off regime: state=partial trend=clear vix=unread (book caution)"
- "held coverage: 7 of 8 fresh; cached=ABC(last 2026-11-25, 0 behind); unpriced=XYZ(bundle_failed)"

### Behaviour per case

| Case | Heartbeat / email | Alerts |
|---|---|---|
| Name unpriced, cached or behind (premarket) | red + failure email; footer if an alert email goes | still sent, with the note |
| SPY 6mo unread | red + failure email | still sent |
| Engine raised | red + failure email | still sent |
| Regime partial / unread, caution / fragile / unmeasured book | red + failure email (existing `risk_off_unchecked` path) | still sent |
| Regime partial / unread, calm book | log only | unchanged |
| EOD cached name | red (existing `partial_price_outage`, records withheld) | — |
| EOD fresh but behind | log only | — |

## 7. Owner decisions (recommendation first)

- **U-A:** any one held name unpriced or cache-served in the premarket scan → red + failure email. No tolerance, no new constant.
- **U-B:** SPY unknown → disclose only, never fabricate a TRIM / EXIT, but the lane goes red (the escalated EXIT is in the premarket scope). A later option, not in this batch: name the holdings whose EXIT hinged only on the unread relative strength.
- **U-C:** a "partly read" regime state on every regime surface. This reverses the RiskOffUnchecked C4 spec's pinned "missing regime → silent" for a caution / fragile / unmeasured book.
- **Regime-unread premarket:** red + failure email when the book could arm or is unmeasured; log only on a calm book. Arming unchanged (grid test).
- **U-D:** a fresh read whose last bar is behind the expected session (YahooNaN's detection) → red in the premarket, US symbols only. At the EOD, log-only for ~10 sessions, then decide. Partly lifts YahooNaN's "only if it recurs".
- **U-E:** at the EOD, a cache-served name counts as unpriced: red, account row skipped, kept out of `snapshot_rows` / `gross_book`, not swept.
- **U-F:** a deterioration or risk-off engine that raises → red.
- **U-G:** the regime legs join the Brief memo signature, so a VIX recovery clears "couldn't run" (or a VIX spike arms risk-off trims) on the next Home run, not at the next 30-minute refresh.
- **U-H:** a stop breach computed from a cached price is still emailed, with the "check the live price" note. Never hide a sell.

## 8. Risks / coordination

- **Correlated Yahoo outage:** one bad morning can give up to five failure lines in one email; honest, and each names its own cause. Watch how often ^VIX alone fails (the new regime log line counts it): if often, a red lane trains the owner to ignore it.
- **Freshness split:** 🧾 Summary's Risk Posture reads the live regime while its Act line reads Home's last Brief; they can disagree until Home rebuilds. U-G narrows the gap; the dock digest names Home's build time.
- **No new `session_state` cache**, so nothing to register in `coord_freshness`.
- **Review:** C1 + C2, C3 + C4, C5 and C6 each need an Opus `reviewer` SHIP 0 blocking (gate files, a gate decision or a DB-write path).
