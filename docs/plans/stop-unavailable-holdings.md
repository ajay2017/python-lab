# Holdings with no stop yet (new listings): honest notices + a manual-stop entry

**Status:** SHIPPED 2026-11-24 (implementer Sonnet 5.5; Opus SHIP 0 blocking on C1 and C2, then a confirming round SHIP over the lead's fixes for the reviewer's non-blocking notes 2/3/4/6/8; C1 + C2 pushed together because C1's text points to C2's form). Live checks in §8 open. NB5 taken by the owner the same day: "↩️ Clear my stop" opens a popover with the consequence and a "Yes, remove my stop" button (Opus confirming round SHIP). Owner call open: NB7 the cron log prefix "held stop on a limited basis:" also labels the "manual stop in force" lines (cosmetic). Spec approved 2026-11-24 (owner: A + B agreed; §3 wording OK as written; the "No stop yet" part stays amber). No new constant; no threshold changes. Build variants for cases §3 did not word are listed in the build report and the bug-register row (StopUnavailableNoEntry).

## 1. The problem (seen live 2026-11-24, ANT)

ANT listed on 2026-11-19 and has 3 completed sessions. Two Home notices disagree:

- The composite-withheld notice (`util.score_withheld_banner_text`, [util.py:299-307](../../stock_analyzer/util.py)) says "… the trend-break exit ladder can't assess it either — only its **stop** and concentration limits protect it."
- The stop warning (`_held_stops_banner_text`, app.py ~2864) says "ANT (Stop Unavailable): No stop could be built: only 3 completed bars (need 15)."

The first claim is false: `held_stop.compute_held_stop` returns no stop below `HELD_STOP_MIN_BARS` (15) completed bars ([held_stop.py:321-324](../../stock_analyzer/held_stop.py)). The bar count is the stock's whole price history, not the time held, so this hits **every holding bought within 15 sessions of its listing** (IPO, spin-off, de-SPAC, direct listing, a restarted Yahoo history). Between 15 and 49 sessions the stop exists and the sentence is true; below 15 nothing in the app tells the owner to exit.

The manual-stop path already covers this case (`held_stop.compose_with_manual`: a valid manual price applies when the automatic stop is None, [held_stop.py:453-469](../../stock_analyzer/held_stop.py)), but its only entry is the Brief's "Mark Done — log the stop I placed at brokerage" form, which renders only on TIGHTEN_ONLY / TRIM_AND_TIGHTEN cards (app.py ~13020). A holding with no stop never gets one of those cards, so the owner can't record a stop for it in the app.

## 2. Scope

**A. Notices tell the truth about the stop.** Each notice reads the holding's real stop state: `auto` (the app built one), `manual` (the owner's stop is in force) or `none`.

**B. A manual-stop entry for a holding with no stop.** On 🏠 Home, under the stop warning, one small form per `none` holding; the saved stop flows through the existing `manual_stops` table to every reader (Home, Brief, Analysis, the pre-market protective cron).

**Not in scope:** an automatic fallback stop (e.g. entry minus a fixed %). `constants.py` says "no stop is invented"; changing that is an investment-policy decision (planner + owner). The Analysis page's own stop display for such a holding (sibling 5 below, check only).

## 3. Wording (owner to OK; ANT at 3 sessions as the example)

`$` in Streamlit markdown is written `\$`; the email footer goes through `held_stop_wiring.plain_text_note`.

**3a. Composite-withheld notice, the too-short-history sentence** (util.py ~299), by stop state:

- `auto` (15–49 sessions): unchanged — "XYZ: price history too short (20 sessions; 50 needed) to measure the technical trend, so the composite is withheld, and the trend-break exit ladder can't assess it either — only its stop and concentration limits protect it."
- `manual`: "ANT: price history too short (3 sessions; 50 needed) to measure the technical trend, so the composite is withheld, and the trend-break exit ladder can't assess it either — only your manual stop ($X.XX) and concentration limits protect it."
- `none`: "ANT: price history too short (3 sessions; 50 needed) to measure the technical trend, so the composite is withheld, and the trend-break exit ladder can't assess it either. No stop can be built yet (15 sessions needed), so only concentration limits protect it: set your own stop under the stop warning on 🏠 Home, or place one at your broker."
- Plural forms follow the existing grammar; tickers are grouped by stop state so every sentence is true for every ticker it names (the module's existing rule).

**3b. Composite-withheld notice, the fundamentals sentence** (util.py ~348, "Price, weight, stops and risk metrics still apply."): for a ticker whose stop state is `none`, it reads "Price, weight and risk metrics still apply; no stop could be built for it yet (see the stop warning)." Unchanged otherwise.

**3c. The stop warning on 🏠 Home** splits into up to three parts, each shown only when it has rows:

1. `none` rows (st.warning, amber; it is expected for a new listing, not a failure):
   > ⚠️ **No stop yet for 1 holding.** Nothing in the app will tell you to exit it until a stop exists:
   > - **ANT**: only 3 completed sessions (15 needed, 12 more). Set your own stop below, or place one at your broker.
2. Unavailable rows covered by a manual stop (st.info, calm):
   > ℹ️ **ANT**: your manual stop $X.XX is in force. The app builds its own stop after 12 more sessions; yours stays in force only while it is the higher of the two.
3. The existing partial / prior-close rows, wording unchanged ("⚠️ **Some stops are not on a full price history.** Treat these stops as approximate and check them against your broker: …"). An unavailable row no longer appears here (today it does, under "treat these stops as approximate", which is wrong for a stop that doesn't exist).

An unavailable row whose note is NOT the too-few-bars reason (e.g. "Stop could not be computed (KeyError).", app.py ~2858) keeps its own note text in part 1 instead of the sessions sentence.

**3d. The form** (one per `none` holding, inside a collapsed expander under part 1):

- Expander label: "✋ Set your own stop for ANT"
- Caption: "The app advises; your broker executes. Enter the stop you placed (or want the app to watch). From session 15 the app builds its own stop; yours stays in force only while it is the higher of the two."
- Inputs: "Stop price ($)" (blank by default: no pre-filled guess), "Note (optional)".
- Button "💾 Save stop"; disabled for the read-only viewer (same `help` text as the Brief form).
- Refusals (pure validator, §4): blank / zero / negative → "Enter a stop price above $0."; at or above today's price → "A stop at or above today's price ($P) would trigger at once. Enter a price below $P." No other bound (no new threshold).
- On a valid entry, before saving, the form shows "That is N.N% below today's price ($P)." as information only.
- Saved → "Stop $X.XX saved for ANT." then `st.rerun()`.
- For a `manual` holding, part 2 carries a small "↩️ Clear my stop" button (same `db.clear_manual_stop`), after which the holding is back to `none`.

**3e. The pre-market email footer** (cron, via `headless_alert_engine` `held_stop_notes`): the same three-way text as 3c in plain text, one line per ticker: "ANT: no stop yet (3 of 15 sessions). Nothing will flag an exit until one exists; set one on 🏠 Home or at your broker." / "ANT: your manual stop $X.XX is in force (no automatic stop for 12 more sessions)."

## 4. Design

- **New pure module `stock_analyzer/young_holding_stop.py`** (new `_GATE_FILES` member: it decides protective text and validates a stop write; add its one-line reason to `docs/review-policy.md` in the same commit). Prefer it over growing `util.py` / `held_stop_wiring.py`.
  - `stop_state(held_stop_result, manual_row) -> dict`: `{"state": "auto" | "manual" | "none", "manual_price", "bars", "need", "more"}`; uses `held_stop.compose_with_manual` (never re-derives the win rule) and parses the bar count from the result, not from the note text. If the result carries no bar count, add one at the source: `compute_held_stop`'s unavailable result gains an additive `bars` field (held_stop.py, gate file).
  - `split_stop_rows(held_stops, manual_stops) -> {"none": [...], "manual": [...], "approx": [...]}` replacing the app.py decision in `_held_stops_banner_text` (app.py keeps only rendering).
  - `validate_manual_stop(price, current_price) -> (ok: bool, message: str, pct_below: float | None)`.
  - Text builders for 3c and 3e (markdown and plain) so app.py and the cron share one wording.
- **`portfolio.build_portfolio_df`** (gate file): each `score_withheld` entry gains `"stop_state"` (`auto` / `manual` / `none`) and `"manual_stop"`, taken from the `_auto` / `_is_manual` / `stop` values already computed for that row (portfolio.py ~620-646). Additive key; a legacy entry without it reads as today's wording (backward compatible).
- **`util.score_withheld_banner_text`**: groups the short-history rows by `stop_state` (3a) and adjusts the fundamentals sentence (3b). A missing `stop_state` → today's text exactly.
- **`headless_alert_engine`** (`held_stop_notes`, ~213) uses the module's plain-text rows with the manual stops it already reads strictly (~142); a failed manual-stop read (None) keeps today's notes (never claims "your manual stop is in force" on an unread table).
- **app.py**: `_held_stops_banner_text` becomes render-only over `split_stop_rows`; the form (3d) renders under part 1 on Home only, using `port_df` "Price" as today's price; `source_action="stop_unavailable"`. No change to how Home reads manual stops: they are read every render (app.py ~6290) and the Home synthesis signature already includes them (~7148), so the rerun after a save rebuilds with the new stop.
- **Offline / unreadable manual stops** (app.py ~6311 already shows `MANUAL_STOPS_UNREADABLE_BANNER`): every row reads as if no manual stop exists, and the form stays usable (the write path is separate); part 2 never shows from an unread table.

## 5. Siblings (fix the class)

1. util.py ~299-307, short-history sentence: fixed (3a).
2. util.py ~348, "stops … still apply": fixed (3b).
3. app.py ~2870, "Treat these stops as approximate" over a stop that doesn't exist: fixed (3c).
4. Pre-market email footer (cron_runner.py ~611 via headless_alert_engine ~213): fixed (3e).
5. app.py ~1843, the Analysis stop explainer ("This stop is not on a full price history …"): CHECK at build what it prints for an unavailable / manual-only holding; fix only if it claims a stop exists; else name it in the commit body.
6. 📈 Analysis page manual stop for ANT (app.py ~26228 reads `_manual_stops`): check that a saved manual stop shows there; no form added there.

## 6. Tests

- `tests/test_young_holding_stop.py`: `stop_state` for auto / manual-only / manual-looser-than-auto (auto wins) / none / non-bar-count reason; `split_stop_rows` with a mix incl. a partial row; `validate_manual_stop` at 0, negative, blank/None, equal to price, a cent below price, NaN price; text builders (markdown escapes `$`, plain text doesn't, plural grammar, "12 more" arithmetic).
- `tests/test_pillar_availability.py` / util tests: each stop state, mixed states in one banner, legacy entry without `stop_state` = today's text byte for byte (the existing `_LADDER` assert at line ~315 stays for `auto`).
- `build_portfolio_df`: the withheld entry carries `stop_state` / `manual_stop` for the three cases.
- headless engine: `held_stop_notes` with manual stops read, and with the read failed (None).
- An AST / source check that app.py's banner no longer branches on basis itself (POLICY_DECISION_IN_RENDER stays clean).

## 7. Docs (same commit, Definition of Done)

- `docs/requirements.md`: a new F-ID for the manual-stop entry on "no stop yet" holdings, and the notice wording; next to the held-stop F-rows.
- In-app User Guide (`elif page == "📖 User Guide":`): one bullet on new listings (no stop for 15 sessions, no composite for 50; set your own stop on Home).
- `docs/bug-register.md`: row **StopUnavailableNoEntry** (this finding), closed by the build.
- `docs/review-policy.md`: the new `_GATE_FILES` member's reason; CLAUDE.md `_GATE_FILES` count.
- `docs/shipped-log.md` entry.

## 8. Build and review

One implementer run, two commits: **C1** = A (pure module, portfolio field, util text, banner split, email footer; display + protective text) and **C2** = B (the form, DB write). Opus reviewer on each (gate files + a DB-write path); commit after hours. Live checks: Home with ANT (part 1 + the form; save a stop well below price; part 2 then shows and the composite notice reads "your manual stop"); the next pre-market email footer; a refused at-or-above-price entry; read-only viewer sees the form disabled. ANT gets its own stop after the Thu 2026-12-10 close, so the `none` → `auto` change is visible then (part 1 / 2 gone for ANT; the composite notice back to the `auto` sentence).
