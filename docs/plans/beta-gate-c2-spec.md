# Beta gate C2 build spec: G-06 on 🏠 Grow Today and both cron pick emails

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/beta-gate-scan-age-intraday.md` §2a / §3 C2 / §4). Owner took U1–U4 defaults (G-06 on adds and the ETF lane, ledgered as G-06, hold when the unread leg could bind). Build = implementer; Opus review; the commit is `feat(grow)` (a new hold on a decision surface). C1 (`8fedae5e`) shipped `beta_fit_gate.breached / decide`.

## Goal
A high-beta buy onto a high-beta book (portfolio β > `PORTFOLIO_BETA_CEILING` AND ticker β > `TICKER_BETA_CRITICAL`) is held on 🏠 Grow Today exactly as the 📋 Watchlist already holds it, and a buy whose beta couldn't be checked, where the unread leg could have tipped the gate, is held as "couldn't check". Both cron emails (09:45 scan, 11:30 intraday) inherit it because both go through `compute_morning_picks` → `build_daily_briefing` → `_grow_today`.

## Rules (do not change)
- Never compare against a constant in `app.py` / `cron_runner.py` (the antipattern gate). Every decision lives in `beta_fit_gate`.
- G-06 is checked AFTER every existing policy and data-quality gate on each lane, so the existing buckets and ledger rows stay byte-identical for every name an earlier gate already holds.
- `beta_fit=None` (not wired; tests only) skips every new check. Every pre-existing return key stays byte-identical. The new keys are always present as lists (`[]`), on every return path including the bear early return.
- Plain text in every new string; no `$` pairs; HTML-interpolated values through `safe_html`.

## 1. `stock_analyzer/beta_fit_gate.py`: add `hold_text`
```python
def hold_text(decision: dict, ticker: str, *, lane: str) -> dict | None:
    """{"reason": str, "clears_when": str} for a blocked / unknown decision, None for pass
    (or a non-dict / unrecognised state). lane: "pick" | "add" | "etf". Never raises
    (on any exception: a generic unknown text, never None for a non-pass decision)."""
```
Wording (format the numbers `:.2f`, the constants `:.1f`, read the constants, never literals):
- blocked, lane pick / etf: reason "Not opened today — portfolio beta. Your book's beta is {pb:.2f}, above the {CEIL:.1f} limit, and {T}'s beta is {tb:.2f} (over {CRIT:.1f}), so this buy would add to the book's market sensitivity. The 📋 Watchlist applies the same check." clears_when "when the book's beta is back at or under {CEIL:.1f}".
- blocked, lane add: reason "Add paused — portfolio beta. Your book's beta is {pb:.2f}, above the {CEIL:.1f} limit, and {T}'s beta is {tb:.2f} (over {CRIT:.1f}), so adding would raise the book's market sensitivity further." same clears_when.
- unknown: reason "Held — portfolio beta couldn't be checked: {why}. That's a reason to pause, not a green light." clears_when "at the next Home build or scan that can read it". `{why}` by kind:
  - `beta_unknown`, port leg unread and ticker leg read: "the book's beta couldn't be computed"
  - `beta_unknown`, ticker leg unread and port leg read: "{T}'s beta couldn't be read"
  - `beta_unknown`, both unread: "neither the book's beta nor {T}'s could be read"
  - `fit_unknown`: "your holdings couldn't be loaded and priced"
  - For lane add, the reason starts "Add held — portfolio beta couldn't be checked: …".
Module docstring: update the Callers line (Grow Today and the cron emails now call `decide` + `hold_text`).

## 2. `stock_analyzer/daily_briefing.py`
### 2a. Plumbing
- `build_daily_briefing(..., beta_fit: dict | None = None)` (last kwarg; docstring entry like the others) passes it to `_grow_today(..., beta_fit=beta_fit)`. Shape: `{"portfolio_beta": float | None, "fit_state": dict}` (the `buy_check_state.fit_state` result).
- Inside `_grow_today`, one local helper `_beta_decision(ticker_beta) -> dict | None`: None when `beta_fit is None`; else `beta_fit_gate.decide(port_beta=beta_fit.get("portfolio_beta"), ticker_beta=ticker_beta, fit_state=beta_fit.get("fit_state"))`. A non-dict `beta_fit` reads as `{}` (→ fit_unknown → hold).
- Ticker β source: the bundle's `(bundle.get("risk_metrics") or {}).get("beta")`, the same read as `watchlist_advisor.py:674`. Picks and movers: `_comp_data` (daily_briefing.py:1470). ETFs: `_ebundle`. Adds: `held_data.get(ticker)` (case: use the same key lookup the add loop's sizing uses for `held_data`).

### 2b. New picks (curated + movers), after the F-303 `earnings_waiting` block (~1788-1799), before "Conviction tier"
- `blocked` → append to new list `beta_blocked_picks` and `continue`:
  `{"ticker", "sector", "score": momentum, "momentum_score", "composite_score": _composite_score, "is_mover", "reason", "clears_when", "gate_id": "G-06", "counterfactual": True, "gate_value": decision["gate_value"], "gate_threshold": decision["gate_threshold"], "price": price, "port_beta", "ticker_beta"}`. No sizing, no `_g32_pick_src` entry.
- `unknown` → append to `beta_unknown_picks` (same fields, `gate_id: None`, `gate_value`/`gate_threshold` None, plus `"kind"`) and `continue`. No ledger row.

### 2c. Adds (U1): `_add_block_gate`
Order becomes `… G-25 → ENTRY → G-06 → CORR_UNMEASURED → BETA_UNKNOWN` (G-06 after ENTRY, BETA_UNKNOWN after CORR_UNMEASURED; plan §2a, §6). Update its docstring.
- `"G-06"` → in the add loop, append to new bucket `beta_blocked_adds` (fields like `entry_held_adds` items: ticker, score, pnl_pct, gap, sector, reason, clears_when, gate_id "G-06", counterfactual True, gate_value, gate_threshold, price, composite_score, momentum_score None, plus port_beta / ticker_beta) and `continue`.
- `"BETA_UNKNOWN"` → append to `entry_held_adds` with `"check": "beta_unknown"`, `gate_id: None`, `holds: []`, `unchecked: []`, reason / clears_when from `hold_text(..., lane="add")`, and `continue` (same shape as the CORR_UNMEASURED item).
- `_adds_paused_list` inherits through `_add_block_gate` (no edit).

### 2d. ETF lane (U2), right after the F-303 `earnings_wait` screen-out (~2154-2162)
- blocked → `etf_screened_out.append({"ticker", "group", "kind": "beta", "reason": hold_text(...)["reason"]})`, `continue`.
- unknown → same with `"kind": "beta_unknown"`. No ledger row for either (ETF-lane precedent).
- Check that the app's `etf_screened_out` renderer prints any `kind` it doesn't special-case through its generic `reason` path; if it switches on kind, add the two kinds.

### 2e. Return dicts
Add `"beta_blocked_picks"`, `"beta_unknown_picks"`, `"beta_blocked_adds"` to BOTH the bear early return (as `[]`) and the main return. Do NOT add a key for beta-unknown adds (they ride `entry_held_adds`).

## 3. Every reader of a Grow Today bucket list (same commit, plan §0.4)
- `stock_analyzer/sizing_path.py` `_PAUSE_BUCKETS`: add `"beta_blocked_adds"`. Check `held_add_verdict` words a paused reason from the item's `reason` for this bucket (G-01 is a special case there; the new bucket must read its `reason` sentence).
- `stock_analyzer/gate_ledger.py` `_BUCKET_LANES`: `"beta_blocked_picks": "new_pick"`, `"beta_blocked_adds": "add_winner"` with a comment (G-06 extended to Grow Today, beta-gate C2; `beta_unknown_picks` deliberately NOT listed: no gate id, no ledger row). Check `tests/test_gate_ledger.py` for a pinned bucket list / gate-id set and extend it.
- `stock_analyzer/grow_dropoff.py`: map `beta_blocked_picks` and `beta_unknown_picks` to reasons the way `sector_blocked_picks` is mapped (read the item's `reason`).
- `app.py` `_grow_shown` (~10633-10639): add the three new keys.
- `tests/fixtures/grow_today_golden.json`: add the three empty-list keys wherever the fixture captures the return; no other diff.

## 4. Callers (AST pin both ends)
- **app.py, BOTH calls:** ~8096 (main build) AND ~7644 (the stale-bundle rebuild; the plan named only the first). Pass
  `beta_fit = {"portfolio_beta": buy_check_state.beta_or_none(_get_or_offline(_port_risk, "beta")) if _port_risk else None, "fit_state": buy_check_state.fit_state(st.session_state.get("holdings_df"), port_df)}`
  computed ONCE per build from the same `_port_risk` the Brief is memoised with (check which `_port_risk` is in scope at each call: 7553 hit path, 7834 miss path). Put the construction in one local helper so both calls read the same dict. If `_get_or_offline` doesn't fit a dict here, use the plain `.get` on a dict only (never `or {}` on a possibly-None cache; the antipattern gate flags it).
- `_synth_sig` (~7496-7518): add `(beta_fit["portfolio_beta"] rounded to 4 dp, beta_fit["fit_state"]["state"])` unless already provably covered; bump `_SYNTH_SCHEMA_VER` 33 → 34 with a leading "bumped: Grow Today carries beta_blocked_picks / beta_unknown_picks / beta_blocked_adds and holds a high-beta buy on a high-beta book (G-06, beta-gate C2); was 33: …" clause.
- **`headless_alert_engine.compute_morning_picks` (~837):** mirror the Watchlist lane's read (~1161-1169): `port_risk = ctx.get("port_risk")`; `portfolio_beta = beta_or_none(port_risk.get("beta")) if port_risk else None`; `fit_state = buy_check_state.fit_state(holdings_df.to_dict("records") or [{}], port_df)`. Pass `beta_fit=`.
- AST test: every `build_daily_briefing(` call in app.py and in `compute_morning_picks` passes the `beta_fit` keyword (memory `ast_test_pins_one_end`: pin the producer end too, i.e. `build_daily_briefing` forwards it to `_grow_today`).

## 5. Cron diagnostics
- `headless_alert_engine.py` `diag` (~907-923): `"beta_blocked": len(grow.get("beta_blocked_picks", []))`, `"beta_unknown": len(grow.get("beta_unknown_picks", []))`.
- `cron_runner.py` "dropped:" explainer (~2562-2569): `"{n} portfolio-beta hold"` and `"{n} beta couldn't be checked"`.

## 6. App render (beside the G-16 block, app.py ~12206)
- `beta_blocked_picks` + `beta_blocked_adds`: one section "⛔ Portfolio beta (N)": per item ticker + `reason` + "Clears " + `clears_when`. Follow the G-16 block's markup and escaping exactly.
- `beta_unknown_picks`: "⏸ Beta couldn't be checked (N)": same per-item fields.
- Beta-unknown adds already render through the `entry_held_adds` renderer (~11920); confirm it prints `reason` / `clears_when` for an unrecognised `check`.
- Check the Grow Today funnel / count lines (~10921 `_fn_earnwait`, ~11116 `_news_skip_n`): if they count every hold-out bucket, include the new ones; if not, leave them and say so in the report.
- User Guide: NOT in this chunk (C5 docs).

## 7. Carry from the C1 review
`stock_analyzer/watchlist_advisor.py` ~208-210 comment: a string beta now reaches the soft branch when the unread leg can't bind; mention +inf reads unread. Comment only.

## 8. Tests (boundaries read from constants)
New `tests/test_beta_gate_grow_today.py` (or extend the C1 test file):
- `hold_text`: each kind / lane wording, None on pass, never raises on garbage.
- `_grow_today` via `build_daily_briefing` fixtures the golden / existing Grow Today tests already use:
  - book β CEIL+0.2, pick β CRIT+0.2 → not in `new_picks` / `overflow_picks`; in `beta_blocked_picks` with gate_id G-06, gate_threshold == `TICKER_BETA_CRITICAL`, no `sizing`; `gate_ledger.build_suppression_rows` emits one lane new_pick row. Book β exactly CEIL → picked.
  - Precedence: sector over cap + G-06 → `sector_blocked_picks` only; G-30 skip + G-06 → `news_skipped` only; earnings caution + G-06 → `earnings_waiting` only; sub-Buy composite + G-06 → `composite_skipped` only.
  - book β None, pick β CRIT+0.2 → `beta_unknown_picks`, no ledger row; book β None, pick β CRIT → picked.
  - fit_state unknown → every evaluated pick in `beta_unknown_picks` (kind fit_unknown).
  - Adds: G-01 + G-06 → `risk_blocked_adds` only; ENTRY news + G-06 → `entry_held_adds` with G-30; G-06 alone → `beta_blocked_adds` and `sizing_path.held_add_verdict` reads "paused"; beta-unknown add → `entry_held_adds` check "beta_unknown", no ledger row.
  - ETF β CRIT+1 on a high-beta book → `etf_screened_out` kind "beta".
  - Bear return carries the three keys as `[]`.
  - `beta_fit=None` → every pre-existing key equals the HEAD output (golden).
- Cron: `compute_morning_picks` with a β 2.0 scan name and book β 1.6 (monkeypatched ctx) → not in `picks`, `diag["beta_blocked"] == 1`.
- AST pin (§4).
- After any copy edit, grep tests/ for old strings (memory `grep_tests_after_text_edit`).

## 9. Report back
Files changed, tests added, any reader list you found beyond §3, the funnel decision (§6), and anything in this spec that didn't match the code. Run the affected test files (not the whole suite; the commit hook runs it). Do not commit.
