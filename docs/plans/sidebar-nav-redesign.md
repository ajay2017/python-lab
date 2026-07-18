# Plan: Sidebar Navigation Visual Redesign

**Status: SHIPPED 2026-07-18 (commits `acf5d62`→`2dc6473`).** Pure UI/CSS + a small data-shape tweak in the sidebar render loop. No constants, gates, scoring, or session-state coordination touched. **Opus review pass complete: FIX-FIRST → both plan-clarity fixes applied below (§4 active-state mechanism corrected; §5 gained option C and a decision). All code citations verified accurate against HEAD — no factual drift. Zero blocking findings** (nothing gate/scoring/RLS-related, as expected for a cosmetic feature). Ready to hand to an implementer as-is.

**Origin:** user asked for a review of the left-nav's alignment/visual hierarchy (font size, spacing, icons) and options to make it "shine" without changing the underlying nav structure. Three directions were sketched (spacing-only refinement / colored group anchors + badge chips / collapsible accordion). **User picked Option 2 — colored group anchors + badge chips.** This doc is the full spec for that direction.

---

## What exists today (verified against HEAD, not memory)

All values transcribed directly from `app.py`:

| Piece | Location | Current value |
|---|---|---|
| Nav data model | [app.py:1650-1682](../../app.py#L1650-L1682) | `_NAV_GROUPS` — list of `(group_label, [(display, dest, material_icon), ...])`, 5 groups: MAIN(4) / RESEARCH(5) / PORTFOLIO(9) / ALERTS(2) / AI(1) |
| Render loop | [app.py:1684-1717](../../app.py#L1684-L1717) | Iterates groups → `st.markdown` header div → `st.button` per item (Material icon via `icon=`, full-width, `disabled=True` on the active page) |
| Group header CSS | [app.py:321-330](../../app.py#L321-L330) | `.nav-group-header { font-size:0.80rem; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; color:#4b5563; padding:10px 4px 2px; }` |
| Button CSS | [app.py:332-345](../../app.py#L332-L345) | `width:100%; text-align:left; padding:5px 10px; color:#9ca3af; font-size:0.86rem; font-weight:400;` |
| Active-state CSS | [app.py:353-361](../../app.py#L353-L361) | `:disabled` override — `background:rgba(59,130,246,.18); border-left:3px solid #3b82f6; color:#f0f2f5; font-weight:600;` — **always blue, regardless of group** |
| Badges | [app.py:1690-1704](../../app.py#L1690-L1704) | Catalyst Watch / Alerts & Actions get emoji+count appended directly into the button's text label (`f"Catalyst Watch  🔴 {n} risk"`) — plain text, not a styled element |
| Theme base | [.streamlit/config.toml](../../.streamlit/config.toml) | `primaryColor=#3b82f6`, `backgroundColor=#131722`, `secondaryBackgroundColor=#1e2130`, `textColor=#f0f2f5` |
| Brand cyan (loading overlay) | [app.py:255](../../app.py#L255) | `#22d3ee` — used nowhere in the sidebar today |

**Issues this design addresses** (see conversation for full critique): group headers are barely differentiated from item text by size/weight, all 5 groups look visually identical (no per-group identity), the active-state highlight is a fixed blue regardless of which group's page is active, badges are ad-hoc emoji-in-text with no consistent format, and the 9-item PORTFOLIO group is one unbroken column with no internal chunking.

---

## Spec

### 1. Group accent palette (new)

| Group | Accent hex | Rationale |
|---|---|---|
| MAIN | `#3b82f6` | Reuses the app's existing `primaryColor` — MAIN keeps the "default" identity |
| RESEARCH | `#22d3ee` | Reuses the brand-mark cyan already used in the loading overlay — ties research/discovery to the "sight" brand |
| PORTFOLIO | `#22c55e` | Green already reads as "your holdings" throughout the app (P&L, buy-candidate coloring) |
| ALERTS | `#f59e0b` | Matches the existing 🟡 amber severity tier |
| AI | `#a78bfa` | Unused elsewhere — cleanly separates AI-generated surfaces as their own visual language |

### 2. Typography & spacing changes

| Element | Current | Proposed |
|---|---|---|
| Group header font-size | `0.80rem` | `0.72rem` |
| Group header color | flat `#4b5563` for all groups | per-group accent at ~65% opacity (`color-mix(in srgb, {accent} 65%, transparent)`, with a static hex fallback declared just before it for defensiveness — low-risk since this renders in a single user's modern browser, but free to add) |
| Group header letter-spacing | `0.1em` | `0.12em` |
| Margin above header | `padding:10px 4px 2px` (uniform) | `margin-top:18px` between groups (first group: 8px, tied to logo padding below `_render_brand`) |
| Header → first item gap | same block | `margin-bottom:6px` |
| Item font-size/weight | `0.86rem` / `400` | unchanged |
| Item vertical padding | `5px 10px` | unchanged |

Net effect: clear big-gap-between-groups / tight-gap-within-group rhythm — today both gaps are close in size, which is why the sidebar currently reads as one long column rather than distinct sections.

### 3. Group icon (reuse existing data, no new icon system)

Each group already has emoji embedded in its items' `dest` strings ([app.py:1650-1682](../../app.py#L1650-L1682)) — those emoji just aren't surfaced anywhere except as page titles. Reuse the first item's emoji as the group icon instead of introducing a 6th icon convention:

`🏠 MAIN · 📈 RESEARCH · 🧩 PORTFOLIO · 🔔 ALERTS · 🧠 AI`

Rendered inline before the header text, in the group's accent color, ~13px.

### 4. Active-state coloring (logic touch, not pure CSS)

Today [app.py:353-361](../../app.py#L353-L361) hardcodes blue for every active item. Proposed: the active item's left-border + background-tint color comes from **its own group's accent**, not a fixed value.

**Corrected mechanism (per Opus review — `st.button` gives no hook to inject a `style` attribute or a custom class onto the button/wrapper, so "inline style / per-group class" as originally phrased isn't directly achievable):** exactly one sidebar button is ever `disabled` at a time (the active page — the privacy/eye toggle button is never disabled), so no per-button scoping is actually needed. The active page's group is already knowable at [app.py:1644](../../app.py#L1644) (`_cur_page`) before the render loop starts — look up which group contains it, then inject **one dynamic `<style>` block** overriding `[data-testid="stSidebar"] [data-testid="stButton"] > button:disabled { border-left-color: {accent}; background: {accent-tint}; }` with that group's accent substituted in. This block must be emitted **inside the sidebar render block** (it's data-dependent on the current page), not added to the static theme block at 275-370.

*(Alternative if per-button scoping is ever needed for something else: streamlit 1.57.0 — the pinned version — emits a `.st-key-{key}` class on each keyed widget's wrapper (since 1.39), so `.st-key-_nav_home button:disabled {...}` also works. Not needed for this feature since only one button is ever disabled at a time.)*

### 5. Badges — three real options, evaluated

`st.button`'s label is plain text rendered inside a native `<button>` — it does not accept arbitrary HTML, so a hand-built rounded "chip" cannot live *inside* the button itself via raw markup. Three options, in the pinned `streamlit==1.57.0`:

- **(A) CSS overlay:** wrap button + a `position:relative` container, absolutely-position a small `pointer-events:none` `<span>` chip over the button's right edge. Gets a polished rounded-pill look but is a DOM-positioning hack that could drift if Streamlit's internal button markup changes (this app has already been bitten twice by Streamlit-internals churn — `pyarrow` segfault and the `use_container_width` deprecation, see `project_pyarrow_unbounded_dep` memory).
- **(B) Disciplined text badge:** keep the badge as plain text appended to the label, but standardize format — one glyph shape (`●`) instead of mixed 🔴/🟡/⚡, consistent ordering (danger-count before warning-count), consistent single-space separator. No fragility risk, no dependency on Streamlit internals, but visually the least polished of the three.
- **(C, recommended) Markdown badge syntax in the button label:** `st.button` labels render markdown (streamlit 1.57.0), including colored-background badge syntax added in 1.44 — e.g. `:red-background[● 2]` / `:orange-background[⚡ 1]`. This renders a genuine rounded, colored chip **inside** the button with no HTML, no overlay, and no dependency on Streamlit's internal DOM structure — most of option (A)'s visual polish with option (B)'s robustness. (Note: `st.badge`, also added in 1.44, is a separate top-level element and can't be embedded inside a button — not usable here; the markdown-in-label mechanism is the relevant one.)

**Decision: ship (C).** It resolves the original A-vs-B tradeoff — verify the exact glyph/color renders acceptably in the sidebar's dark theme before finalizing, but the mechanism itself is confirmed to work in the pinned version.

### 6. PORTFOLIO internal cluster break

Split the 9-item PORTFOLIO group into two visual clusters — **Analysis** (Portfolio Allocation / Portfolio Health / My Edge / Risk Analysis / Portfolio Intelligence) and **Activity** (Alerts & Actions / Trade Journal / Trade Review / Recommendations History) — without adding a new top-level nav group (which would touch `nav_page` semantics used elsewhere in the app). Mechanism: add a sentinel entry to the `_NAV_GROUPS` items list, e.g. `("__divider__", None, None)`, and special-case it in the render loop ([app.py:1689](../../app.py#L1689)) — `continue` before reaching `st.button` so no `_nav_{...}` key is generated for it and it can't false-match the `_dest == "🔔 Catalyst Watch"` / `"⚠️ Alerts & Actions"` badge lookups — to emit a thin `1px solid rgba(255,255,255,.08)` full-width rule with 8px vertical margin instead of a button. Purely additive to the existing list-of-tuples shape (Opus-verified: no key collision, no badge-lookup false-match). **Implementer note:** if §1/§3's per-group accent+icon end up added to the *group* tuple shape (not the item shape), keep the `__divider__` tuple's arity matched to the item-tuple shape — it doesn't change.

---

## What is explicitly NOT in scope

- No change to `_NAV_GROUPS`' top-level group membership, `nav_page` values, or the `_pending_page` indirection pattern (CLAUDE.md navigation-safety rule stays untouched).
- No collapsible/accordion behavior (that was Option 3, not selected).
- No new session_state keys beyond what (A), if chosen, would need for the overlay hack (none needed for (B)).
- No F-ID / `docs/requirements.md` entry planned — this is a visual/IA refinement of an existing surface, not a new user-facing decision surface (same precedent as the Nav follow-up Phase A/B/C and the 2026-07-12 UX review's Phase 3 tab consolidation, both tracked only in `CLAUDE.md`'s shipped-log, not `requirements.md`).

---

## Implementation footprint (for whoever builds this)

- CSS-only changes: [app.py:319-361](../../app.py#L319-L361) (the `.nav-group-header` / button / active-state block).
- Data-shape + render-loop changes: [app.py:1650-1717](../../app.py#L1650-L1717) — add per-group accent + icon to the `_NAV_GROUPS` tuple shape, add the `__divider__` sentinel handling, wire per-group accent into the active-state class.
- No other file touched. `py_compile` is a sufficient correctness gate (pure display logic, no gates/scoring/thresholds) — **no Opus review required at ship time** per CLAUDE.md hard rule #4's trigger conditions (doesn't touch `constants.py`, a gate, or a scoring/recommendation formula). This design-stage review is being done anyway per explicit user request, ahead of any code being written.
