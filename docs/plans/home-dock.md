# 🏠 Home dock: a VS Code-style bottom panel, and the Command Center first

**Status:** **Title bar 2026-11-19 late (owner, after C1c `ed595f4` verified live: top gap gone, Summary fine, Notices re-opens after a Refresh):** the fold bar duplicated the tabs → one muted word "Dock" in a slim bar (option A; option B, no bar / no folding, declined). **C1 PUSHED `0b2dbde` 2026-11-19 19:11 ET; owner verified live (screenshot) and asked for C1c the same evening:** (1) blank band above the Command Center = Streamlit 1.57's 6rem main-block top padding (header 3.75rem) plus two style-only `st.markdown` gaps → padding 4.25rem (`home_dock._TOP_PAD_REM`) and the theme + dock CSS via style-only `st.html` (event container, no gap); (2) the Act Today pointer and tab duplicate the Brief's Today's Actions card / Act Today section now that the Command Center is first → RETIRED (F-218c; `act_today_pointer.py` deleted, `_GATE_FILES` 98); Home dock = ⚠️ Notices (opens every full run, Needs-a-look line on top) · 💹 Live Prices · 📈 Markets; the outage path keeps an in-page "Act Today was NOT checked" error. C1b must decide whether other pages (no Brief there) want an "as of Home" Act Today count. Earlier: **C1 layout chosen 2026-11-19 evening (owner, mockup `docs/mockups/2026-11-19-home-dock-c1-layout-mockup.html`, panel B plus one lead change = "B+"):** Streamlit fixes a tab's label and its starting tab when `st.tabs` is called, at module level before Home computes anything, so §3's "labels carry the counts" and the count-driven default tab can't use this run's numbers. B+ = four fixed-label tabs on Home (🔴 Act Today · ⚠️ Notices · 💹 Live Prices · 📈 Markets), each tab's count on its own first line, the dock always opening on Act Today, and the Needs-a-look line shown in BOTH the Act Today tab (under the pointer, so a red notice is visible on the opening tab) and the Notices tab. Option A (both summary lines above three tabs) was the lead's first pick; the owner preferred B's look and the change closes A's reason. Separate tab / expander keys on Home, so a tab picked on Home can't carry onto the one-tab dock elsewhere. Popover / dialog considered for C1: a dialog can't hold the notices (its body runs only when opened, while the fill sites write during the main run); a popover could, so "Open all notices" in an `st.popover` is the fallback if the 170 px Notices pane is cramped on a busy day; not built in C1. C1 BUILT 2026-11-19 (implementer). Opus review FIX-FIRST: the 1.57 frontend DOES restore a keyed tab set's picked tab on every run (read from the frontend bundle, no live check needed), so the Home tab key now carries a per-full-run counter (`home_dock.home_tabs_key`) and the dock re-opens on Act Today at every full Home run; a picked tab holds through the 60 s fragment refreshes. Same round: "below" removed from the resolved-breach wording, an in-page outage error line pointing to the dock, "→ ⚠️ Notices tab" on the Act Today tab's copy of the Needs-a-look line, Notices caption "Notices from this Home run show here.", six new test pins in `tests/test_home_dock.py`. Confirming round: Opus SHIP, 0 blocking (the lead then made the run-counter test assert the +1). Committed + pushed 2026-11-19 evening. Live checks after push: both tabs' content on a clean and a busy day, Notices pane not cramped (else an "Open all notices" popover), Act Today re-opening after a Refresh, no faded ghost bars. Earlier: DESIGN AGREED 2026-11-19. The owner took every lead leaning on Q1 to Q5 (A collapsible, open by default; "Notices"; the default-tab rule; Markets on Home only; a jump link if the anchor works) and Q6 = about 3 rows (~170 px). Mockup: `docs/mockups/2026-11-19-home-dock-mockup.html`. C0 probe PUSHED 2026-11-19 (`05d6ae4`). Owner probe the same day: sticky pinning works while scrolling, collapse / reopen and the 60 s refresh work, other pages keep the strip on top; two findings fixed in a follow-up (a blank band under the dock at the end of the page = Streamlit's bottom padding, now 0 on Home; the faint "auto-refreshes" line, `#444` → `#9ca3af`). Owner re-checked after C0b: flush with the bottom, readable timestamp, all good on the laptop browser. **Phone width NOT tested** (the owner doesn't use the app on a phone at present); the 45vh cap is the only phone safeguard, unverified. `?dock=fixed` not tried; sticky works, so C0c removes the switch. C0c BUILT 2026-11-19 (Markets dock on every page, declared at module level; the switch and fixed CSS removed; F-218d added). C0c PUSHED `1c6ea68` 12:04 ET. Owner live check: Signals OK; 📖 User Guide (short page) showed the dock floating mid-screen → C0d (the main block is at least one screen tall, the dock `margin-top: auto`); on the first move Home → Signals two Markets docks showed once (not on the second move), cause unconfirmed, likely Streamlit's stale elements during a run (AlertsGhost family). Earlier note: live check on a few pages (Home, Signals, 📒 Trade Journal for AlertsGhost, 📖 User Guide for a long page), then C1. This is the first Home step of InteractiveDetail (CLAUDE.md "Open now"), and it revives HomeLayout (bug register, on hold since 2026-11-07) on a fresh owner ask.

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
- **Every page gets the dock** (owner changed Q4, 2026-11-19). Markets is on every page from C0c; Act Today, Notices and Live Prices reach the other pages only in C1b, labelled with the time of the Home build they come from.

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
- **Q4** ~~Markets inside the dock on Home only~~ **Changed by the owner 2026-11-19: the dock is on every page, always.** Staged: Markets everywhere first (C0c); the Home-computed tabs off Home only in C1b, because off Home they show Home's last result, which can be stale or missing (never a guessed 0).
- **Q5** Should the Act Today tab include a "Jump to Act Today ↓" link (an in-page anchor)? It needs a check that Streamlit's header anchor works there.
- **Q6** Dock height when open: about 3 rows of content (~170 px) or about 5 (~260 px)?

## 8. Build chunks (one deploy each, implementer builds, screenshot is the check)

- **C0 probe:** the dock shell on Home with only the Markets tab, plus the CSS; nothing else moves. Proves pinning, the sidebar edge, phone width, the 60-second fragment inside the dock, and tab memory across reruns. If P-fixed and P-sticky both fail, stop and re-plan; never ship a half-pinned panel.
- **C0b (pushed 2026-11-19):** no blank band under the dock at the end of the page (main container padding-bottom 0); the strip's timestamp line readable (`#444` → `#9ca3af`).
- **C0c:** the Markets dock on every page: declare it at module level where `_index_strip()` runs today (before page dispatch, so pages that `st.stop()` early still get it), drop the Home-only special case and the top strip on every page, and remove the `?dock=` switch once the owner has seen `?dock=fixed` (or skips that check, since sticky works). Watch: pages whose own content ends at the bottom (forms, the User Guide) under a sticky dock, and AlertsGhost (seen on 📒 Trade Journal too).
- **C1:** move Act Today, Notices and Live Prices into the dock; Command Center becomes first. Docs in the same commit: requirements F-218b / F-218c, the User Guide Home bullet, the bug register HomeLayout + AlertsGhost rows. Reviewer: one Opus pass, because it moves where protective notices and the Act Today count appear (display only; no gate or count logic changes).
- **C1b:** Act Today, Notices and Live Prices in the dock on the other pages, read from what Home published (`get_or_offline`; registered in `coord_freshness.py` if a new key is needed), each labelled "as of <Home build time> · open 🏠 Home to refresh" or "not checked yet", never a false all-clear. Live Prices off Home only after checking provider load (Finnhub 429s seen 2026-11-17), since it adds a 60-second price refresh on every page. Cross-page coordination → planner-light + Opus reviewer.
- **C2 onward:** the Home popover / dialog spots from `docs/mockups/2026-11-19-interactive-detail-mockup.html` (the Grow Today funnel → dialog, the Debate result → dialog, the greedy-trim caption → popover), one per deploy; then Reports and Analysis.

Provenance for the feat commits: `Design = lead — layout-only, no policy content (owner-agreed mockup)`, `Build = implementer (claude-sonnet-5-5)`.
