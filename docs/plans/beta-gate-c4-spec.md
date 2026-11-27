# Beta gate C4 build spec: the 11:30 email never sends a pick below its stop (IntradayBelowStop, G-35)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/beta-gate-scan-age-intraday.md` §2c / §3 C4 / §4). Owner 2026-11-26: **U9 = (a)**: the stop check only, NO new constant (no `PULLBACK_ENTRY_MAX_DROP_ATR`, no breakdown / atr_unknown exclusion), plus a log of each candidate's drop from the open in ATRs so a cap can be set from data later. U10 = yes (a broken-stop pick is a footer line in an email that goes out anyway, never a standalone email). U11 = composite first. U13 = G-35 is a documentation id only (no ledger rows). Build = implementer, after C3 is committed; Opus review; commit `feat(intraday)`.

## Goal
The 11:30 intraday pullback email lists only picks whose live price is still above the stop this morning's pick set. A pick that broke its stop, has no stop, or has no live price is excluded and named (log always; email footer when an email goes out anyway). The footer's "cleared all gates" sentence becomes a true list.

## 1. `stock_analyzer/intraday_entry.py` (joins `_GATE_FILES`)
Add to `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`) with a reason line in `docs/review-policy.md` (suggested: "the 11:30 pullback-entry decision: which morning go picks are emailed as an entry window (the dip trigger, the SPY guard and, from G-35, the pick's own stop); a wrong call emails a buy on a name already below the stop its own pick set").

`compute_intraday_entries(go_picks, price_data, spy_data, dip_pct, spy_max_down) -> dict` (signature unchanged; return type changes):
```python
{"entries": [...],            # as today, plus "stop_distance_pct" and "drop_atr"
 "excluded": [{"ticker", "why": "below_stop"|"stop_unknown"|"no_price",
               "current_price", "open_price", "stop", "intraday_drop_pct", "drop_atr"}],
 "spy_guard": None | "unavailable" | "freefall"}
```
Rule order:
1. SPY guard, unchanged in behaviour. SPY missing / unreadable → `{"entries": [], "excluded": [], "spy_guard": "unavailable"}`; down ≤ −`spy_max_down` → `"freefall"`.
2. Per pick (ticker blank → skip silently as today):
   a. No live price (`price_data` has no entry) → excluded `no_price` (today a silent skip).
   b. `drop = (current − open) / open × 100`. Not a dip (`drop > −dip_pct`) → not an entry and NOT excluded (unchanged: most picks simply didn't pull back).
   c. Stop = `_safe_float((pick.get("sizing") or {}).get("stop"))`. Missing or ≤ 0 → excluded `stop_unknown`.
   d. `current <= stop` → excluded `below_stop` (strict: stop + 0.01 passes).
   e. Else an entry: `{**pick, "intraday_drop_pct", "current_price", "open_price" (as today), "stop_distance_pct": round((current − stop) / current × 100, 1)}`.
   `drop_atr` (both entries and excluded dip names): `round((open − current) / atr, 2)` where `atr = _safe_float(pick.get("atr"))`, None when the ATR is missing / ≤ 0. **Log only; it decides nothing** (U9 a).
3. Sort entries composite first (U11): key `(-(composite_score or 0), intraday_drop_pct)`. Excluded keep input order.
Note: the dip check (b) runs before the stop check, so a pick that didn't dip is never "excluded", even if it is below its stop (it isn't emailed anyway). Never raises on a bad pick: a pick that throws reads as excluded `stop_unknown`.
Module docstring: state the rule order and that `drop_atr` is measurement only.

## 2. `stock_analyzer/daily_briefing.py`: pick carries its ATR
Add the additive key `"atr"` to the new-pick dict (`_pick_atr`, ~1876 pick construction) and the ETF pick dict (`_etf_atr`, ~2222). Value: the float used for the stop, or None when ≤ 0. Regenerate `tests/fixtures/grow_today_golden.json` (`GOLDEN_REGEN=1`); the only diff must be the `"atr"` key on picks. `_SYNTH_SCHEMA_VER` (app.py) +1 with the "bumped: … new picks carry atr (beta-gate C4); was N: …" clause (C3 sets 35, so this is 36; check the current value).

## 3. `cron_runner.py` intraday lane (~2773-2835)
- Read `res = compute_intraday_entries(...)`; `entries = res["entries"]`, `excluded = res["excluded"]`.
- Always log one line per run:
  `intraday candidates: T (drop −2.1%, 1.35 ATR, stop 101.20 vs now 103.40) · …` for every dip name (entries and excluded), and `intraday excluded: T below_stop (now 99.80 ≤ stop 101.20); U no_price; …` (or `intraday excluded: none`). ATR shown as "n/a ATR" when None.
- The "no tickers qualify" log becomes precise: `spy_guard` unavailable / freefall named, else "none pulled back enough" (plus the excluded count when > 0).
- Dedup fingerprint, budget and email unchanged in shape; pass `excluded=excluded` to the renderer.
- No email when `entries` is empty, even if `excluded` isn't (U10).

## 4. `stock_analyzer/notify.py` `render_intraday_entry_email(..., excluded: list | None = None)`
- Card: the existing `stop $X` entry bit becomes `stop $X ({stop_distance_pct}% below now)` when `stop_distance_pct` is present.
- Footer block (only when `excluded` is non-empty, U10), one line each:
  - below_stop: "Not an entry: {T} is now ${cur:.2f}, below the ${stop:.2f} stop this morning's pick set."
  - stop_unknown: "Not an entry: {T} has no stop from this morning's pick, so no entry is sent."
  - no_price: "Not checked: {T} had no live price at this run."
  Escape tickers with `_html.escape`.
- Replace the "cleared all gates this morning (composite + tone + sector + macro)" sentence with: "These names passed this morning's buy checks (composite score, market tone, sector cap, macro events, news, earnings, portfolio beta) and are still above the stop each pick set." Keep the rest of the footer text.
- Subject: unchanged (uses `entries[0]`, now the highest composite).

## 5. Tests
- `tests/test_intraday_entry.py`: update the existing tests to the dict return (assert the same entries); add:
  - drop exactly −`PULLBACK_ENTRY_DIP_PCT` → an entry (read the constant);
  - current = stop → excluded `below_stop`; stop + 0.01 → entry; stop None / 0 / missing sizing → `stop_unknown`;
  - no price → `no_price` in excluded (not silently dropped);
  - not-dipped pick below its stop → neither entries nor excluded;
  - composite-first order (a higher composite with a smaller drop sorts first);
  - `drop_atr` computed and None on missing / zero ATR, and it never changes entries / excluded (same result with atr None vs a huge drop in ATRs);
  - SPY guard kinds.
- `tests/test_cron_daily_budget.py` (~412 monkeypatches `compute_intraday_entries` returning a list): return the dict shape.
- Renderer: no card has `current_price <= stop`; the footer lists exclusions only when given; the old "cleared all gates this morning (composite + tone + sector + macro)" string is gone; no `$` inside an unescaped user string.
- A cron intraday-lane test (follow the existing intraday tests): a pick below its stop is not emailed and the "intraday excluded:" log line names it; only-excluded → no email.
- After any copy edit, grep tests/ for old strings (memory `grep_tests_after_text_edit`).

## 6. Report back
Files changed, tests added and results, the antipattern / constants-doc / py_compile results, how many existing go picks / fixtures have `sizing` without a `stop` (the `stop_unknown` population), and anything in this spec that didn't match the code. Affected test files only (`-n 4` max). Do not commit.
