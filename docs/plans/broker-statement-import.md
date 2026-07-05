# Broker Statement Import (Robinhood CSV) — Plan

**Goal:** Manually keep the app's `trades` in sync with Robinhood by uploading the broker's activity-CSV (downloaded daily/weekly/monthly). Bridge until Robinhood ships a mature integration (the automated MCP path is deferred — memory `project_today_pnl_scope`).

**Status:** v1 spec locked, pre-build. **Scope v1 = Buy/Sell trades only.** Cash events (dividends / ACH / interest / fees) deferred to v2 (→ 💰 Account `account_flows` ledger).

---

## The input: Robinhood activity CSV
Columns: `Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code, Quantity, Price, Amount`. It's a **transaction log**, not a positions snapshot. Parsing quirks:
- `Description` is multi-line (company name + `CUSIP: …` on a second line inside the quoted field).
- `Price`/`Amount` carry `$`, thousands commas, trailing spaces; `Amount` uses parentheses for outflows (`($518.00)` = buy). `Price` is positive.
- Dates are `M/D/YYYY`. A trailing all-blank row and a tax-disclaimer line must be dropped.
- `Trans Code` here = Buy/Sell; real statements also carry CDIV/ACH/INT/fees → **skipped in v1** (counted in a transparency summary).

## Core problem: no transaction id → dedup for overlapping downloads
The CSV has **no unique row id**, and `trades` has **no external-id / dedup column** (only a 15s in-session double-submit guard — useless for re-imports). A single order also often fills as **multiple identical lines** (same day/price/qty) that are *legitimately distinct*.

**Solution (no schema change): count-based content match.** Match key = `(date, ticker, action, round(shares,4), round(price,2))`. For each key, `new_count = max(0, csv_count − existing_count)`; the first `existing_count` occurrences in the CSV are flagged "already recorded", the rest "new". This correctly handles both identical multi-fills and overlapping daily/weekly/monthly re-downloads, and needs **no new DB column / no DDL**. The **human preview is the final safety net** (source-of-truth table stays behind explicit confirmation).

*(Deferred: a hard `import_fingerprint` column for DB-level idempotency — content-match + preview is sufficient for a single-user manual flow.)*

---

## Module — `stock_analyzer/broker_import.py` (pure; no Streamlit/DB)
- `parse_robinhood_csv(file) -> dict` → `{"trades": DataFrame[ticker, action, shares, price, activity_date, company], "skipped": {trans_code: count}, "invalid": DataFrame, "error": str|None}`. Handles the quirks above; filters Buy/Sell; rows with shares≤0 or price≤0 go to `invalid` (surfaced, never silently dropped — DB has `check (shares>0)`/`check (price>0)`). Returns a clear `error` when the file isn't a Robinhood activity export.
- `classify_against_existing(candidates, trades_df) -> DataFrame` → adds `is_new` (bool) + `match_reason` via the count-based content match above (existing BUY/SELL only; parse `traded_at` with `utc=True` → date).

## UI — 📒 Trade Journal, new expander "📥 Import from broker statement (Robinhood)"
1. `st.file_uploader(type=["csv"])` → parse → summary: "N trades (X Buy / Y Sell) · M non-trade rows skipped (breakdown) · Z invalid (shares/price ≤ 0)". Parse error → `st.error`.
2. `classify_against_existing` vs `st.session_state.trades_df` → **preview** via `st.data_editor`: an editable **Import?** checkbox column (pre-checked for `is_new`, unchecked for "already recorded") + read-only Date/Ticker/Action/Shares/Price/Status.
3. **"📥 Import selected"** button (`disabled` in read-only) → sort selected rows **chronologically (date asc, BUY before SELL within a day)** → loop existing `db.save_trade` per row (record: ticker/action/shares/price/`traded_at`=ISO date, `trigger_type="MANUAL"`, `notes="Robinhood import <date>"`, cost_basis/realized_pnl = None so the replay computes them). Reuses `save_trade`'s tested path + read-only guard + graceful column degradation. **No new db writer, no bulk-insert path (loop is fine for a periodic statement).**
4. **Reconciliation view** (post-import): reload `trades_df`, run `recalculate_from_trades`, `save_holdings(holdings_df)`, apply `realized_pnl_corrections` via `update_trade_realized_pnl` (reuse the existing Rebuild-apply loop), then display resulting holdings + any `warnings` (unmatched SELL / over-sell). Invalidate `_tj_drift_checked` so the drift banner re-runs. Clear uploader state.

## Safety / invariants
- Human-in-the-loop preview → the `trades` source-of-truth is never bulk-written silently.
- Respects DB `shares>0`/`price>0` (invalid rows excluded + shown). Read-only guard on the import button.
- Chronological insert so SELLs replay against prior BUYs (avoids spurious "no prior BUY" drift).
- Imported prices are real broker fills → the interactive form's price-sanity/ticker-existence guards are intentionally **not** applied (this is a backfill path; the recalc warnings are the logical-consistency safety net).
- Imported rows are marked in `notes` ("Robinhood import …") for auditability.

## Honest limitation (v1)
The CSV is a transaction log, not a positions snapshot → v1 reconciles by *showing* the resulting holdings + inconsistencies for the user to eyeball against Robinhood. A SELL whose BUY predates the app's history will surface as a recalc warning (expected; user adds the missing BUY or accepts).

## Deferred
- v2: cash events → `account_flows` (deposits/withdrawals) + dividend handling → 💰 Account.
- Optional RH **positions** CSV to auto-verify the reconciliation.
- Hard `import_fingerprint` DB idempotency column (only if content-match proves insufficient).

## Routing
🔵 implementer (build module + Trade Journal expander) → 🔴 reviewer (source-of-truth table = high-stakes: dedup correctness, chronological replay, read-only guard, no double-count, recalc reuse) → commit + doc sync (requirements F-row + architecture).
