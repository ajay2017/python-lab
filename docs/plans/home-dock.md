# 🏠 Home dock: a VS Code-style bottom panel, and the Command Center first

**Status:** DESIGN AGREED 2026-11-19. The owner took every lead leaning on Q1 to Q5 (A collapsible, open by default; "Notices"; the default-tab rule; Markets on Home only; a jump link if the anchor works) and Q6 = about 3 rows (~170 px). Mockup: `docs/mockups/2026-11-19-home-dock-mockup.html`. C0 probe BUILT 2026-11-19 (uncommitted; `stock_analyzer/home_dock.py` + the Markets-only dock on Home). Next: push C0 and run the Railway probe. Nothing else is built. This is the first Home step of InteractiveDetail (CLAUDE.md "Open now"), and it revives HomeLayout (bug register, on hold since 2026-11-07) on a fresh owner ask.

## 1. Owner's ask (2026-11-19)

- Start InteractiveDetail on 🏠 Home, the page used most during the day, beginning with the strips at the top.
- Free that space so the **Portfolio Command Center is the first thing on Home**.
- Move the top strips into a **bottom panel that is always on screen**, "like VS Code's panel": Problems / Output / Terminal stay docked at the bottom while the editor above scrolls.
- **Everything from the Command Center down stays the same** on the page.
- Analysis, plan and an agreed mockup come before any build.

## 2. Today's top of Home (app.py at `51c0e4e`)

Render order, top to bottom:

| # | What | Where | Notes |
|---|---|---|---|
| 0 | Market index strip (DOW / S&P / Nasdaq) | `_index_strip()` ~4631, called at module level ~4674, **on every page** | `@st.fragment(run_every=60)` |
| 1 | 🔴 Act Today pointer (F-218c) | `_act_ptr_ph = st.empty()` ~5122, filled later from `act_today_view` | Three states: N to act, "✅ Nothing to act on today", "NOT checked" (offline, ~5130 and the outage branch ~5863) |
| 2 | ⚠️ Needs a look line (F-218b) | `_notices_summary_ph` ~5142, filled by `_home_notice()` | Rolls up what the Alerts placeholders actually drew |
| 3 | ⚠️ Alerts expander | ~5197, `key="_home_alerts_exp"`, about 20 `st.empty()` placeholders | Filled from fill sites far below (Day Shock, cross-check, split, drift, journal match, System Trust, outage …). AlertsGhost site |
| 4 | 💹 Live Prices expander | ~5550, `key="_home_price_strip_exp"` | `_price_strip` fragment, `run_every=60`, writes `_live_prices` |
| 5 | In-flow protective banners | ~5787 manual stops unreadable (error), ~5793 held-stop basis (warning), ~5797 recalc notice (info), dropped holdings, the outage error + `st.stop()` ~5881 | **Deliberately not foldable** ("a protective input is missing, so this must not be foldable away") |
| 6 | Portfolio Command Center | ~8114 header, ~8129 eight metrics | Its **"Alerts"** tile (~8197) is `n_danger🔴 n_warning🟡` = risk alerts, a different thing from the ⚠️ Alerts expander (data / system notices) |
| 7 | Today's Brief and everything below | unchanged | |

## 3. Proposed layout

```
┌──────────────────────────────────────────────────────────────┐
│ (protective banners, only on a bad day — stay in flow)      │
│ Portfolio Command Center          ← now the first thing     │
│ Today's Brief · Grow Today · …    ← unchanged, scrolls      │
├──────────────────────────────────────────────────────────────┤
│ 🔴 Act Today 3 │ ⚠️ Notices 1+3 │ 💹 Live Prices 10 │ 📈 Markets │ ← docked, always on screen
│ (the selected tab's content, short, scrolls on its own)     │
└──────────────────────────────────────────────────────────────┘
```

- **Dock tabs** (`st.tabs`): Act Today (the pointer's text and its link to the Act Today section), Notices (the Needs a look line plus every Alerts placeholder, unchanged), Live Prices (the `_price_strip` fragment, unchanged), Markets (the index tiles, on Home only).
- **Tab labels carry the counts**, like VS Code's "Problems 3": "🔴 Act Today 3", "✅ Act Today 0", "⚠️ Act Today: not checked"; "⚠️ Notices 1 + 3" or "Notices"; "💹 Live Prices 10"; "📈 Markets".
- **Default tab** (`st.tabs(default=…)`): Act Today when something is to act on or Act Today was not checked; otherwise Notices when a warning or error is showing; otherwise Live Prices.
- **Protective in-flow banners stay where they are**, so on a bad day they sit above the Command Center. "Command Center first" means first on a normal day.
- **Other pages are unchanged**, including their index strip at the top.

## 4. Verified facts (Streamlit 1.57.0, the installed library, `inspect.signature`)

- `st.tabs(tabs, *, width, default=None, key=None, on_change='ignore', …)`: a default tab and a key exist.
- `st.container(*, border, key, width, height, horizontal, …, autoscroll)`: a fixed `height` gives a scrolling box; a `key` gives the CSS class `.st-key-<key>`, already used for styling in app.py (~14118 `.st-key-sm_safety`).
- `st.expander(label, expanded, *, key, icon, width, on_change, …)`: opening and closing is client-side (no rerun) unless `on_change` is set.
- `st.popover(label, *, type, help, icon, disabled, width, key, on_change, …)` and `st.dialog(title, *, width, dismissible, icon, on_dismiss)`: available for the later InteractiveDetail steps.
- Not verified, and can't be without a deploy (Hard Rule #3): how CSS pinning behaves against Streamlit's own scroll container and sidebar, whether a keyed `st.tabs` keeps the picked tab across reruns, and how a `run_every` fragment behaves inside a pinned box. C0 below exists to prove these on Railway first.

## 5. How the dock is pinned (two candidates; C0 decides)

- **P-fixed:** the dock container stays where the placeholders are declared today (top of Home, so every later fill site still works) and CSS gives it `position: fixed; bottom: 0`. Risk: its left edge has to clear the sidebar, whose width the owner can drag and collapse.
- **P-sticky:** same declaration point; CSS moves it to the end of the main column (`order`) and pins it with `position: sticky; bottom: 0`, so it keeps the main column's width automatically. Risk: depends on Streamlit's internal flex layout between the keyed container and the main block.
- Either way the page gets bottom padding equal to the dock's height, so the last Home section isn't hidden under it.

## 6. Risks and how each is handled

1. **AlertsGhost** (faded stale "⚠️ Alerts" bars). The Alerts expander is the ghost site; in a pinned dock a ghost would be glaring. Moving its content into a tab removes that expander. C1's live check looks for ghosts in and around the dock for a week. If they come back the dock waits; it never ships with them.
2. **Protective notices must stay visible.** Notices keep every placeholder and its text; only the container moves. The tab label shows the tier, so a red notice is visible without opening the tab. In-flow banners (row 5) don't move.
3. **The Act Today count keeps one source.** The tab label reads the same `act_today_view` result as the pointer and the Act Today section, including the offline state (never a false 0).
4. **"Alerts" means two things.** The dock tab is renamed "Notices" (Q2) so it can't be confused with the Command Center's risk-alert tile.
5. **Phones.** The dock content caps at a short height and scrolls inside itself; the collapsed state (Q1) is one line.
6. **The outage path** (`st.stop()` ~5881) still fills the Act Today tab with its "NOT checked" text, because the dock is declared before it.
7. **🙈 Hide values and the read-only viewer:** the moved code is unchanged, so its masking and locks are unchanged; checked by screenshot.
8. **Requirements change:** F-218c "the very top of Home" becomes "always on screen in the Home dock"; F-218b's "above the Alerts expander" becomes "in the dock's Notices tab". The User Guide's Home bullet changes with it.

## 7. Owner questions (the mockup shows each)

- **Q1** Collapsible (one click hides the dock down to a one-line bar with the counts, client-side, like VS Code) or always open at a fixed small height? Lead leans collapsible, open by default.
- **Q2** Rename the dock's "⚠️ Alerts" to "⚠️ Notices", so it isn't confused with the Command Center's risk "Alerts" tile? Lead leans yes.
- **Q3** The default-tab rule in §3? Lead leans yes.
- **Q4** Markets (index tiles) inside the dock on Home only, with other pages unchanged? Lead leans yes; an app-wide dock is a later, separate ask.
- **Q5** Should the Act Today tab include a "Jump to Act Today ↓" link (an in-page anchor)? It needs a check that Streamlit's header anchor works there.
- **Q6** Dock height when open: about 3 rows of content (~170 px) or about 5 (~260 px)?

## 8. Build chunks (one deploy each, implementer builds, screenshot is the check)

- **C0 probe:** the dock shell on Home with only the Markets tab, plus the CSS; nothing else moves. Proves pinning, the sidebar edge, phone width, the 60-second fragment inside the dock, and tab memory across reruns. If P-fixed and P-sticky both fail, stop and re-plan; never ship a half-pinned panel.
- **C1:** move Act Today, Notices and Live Prices into the dock; Command Center becomes first. Docs in the same commit: requirements F-218b / F-218c, the User Guide Home bullet, the bug register HomeLayout + AlertsGhost rows. Reviewer: one Opus pass, because it moves where protective notices and the Act Today count appear (display only; no gate or count logic changes).
- **C2 onward:** the Home popover / dialog spots from `docs/mockups/2026-11-19-interactive-detail-mockup.html` (the Grow Today funnel → dialog, the Debate result → dialog, the greedy-trim caption → popover), one per deploy; then Reports and Analysis.

Provenance for the feat commits: `Design = lead — layout-only, no policy content (owner-agreed mockup)`, `Build = implementer (claude-sonnet-5-5)`.
