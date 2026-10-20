# Headline / feed-text HTML escaping (XSS hardening)

**Status:** Phase 1 commit 2 BUILT 2026-10-20 (not yet committed) — the 12 inventory sites migrated to `util.safe_link`
(headline + URL) and a new `util.safe_text` (other untrusted text: `safe_html` plus whitespace collapse and character
references for `$`, `[`, `]`, `\` and the backtick, so it neither renders as LaTeX nor forms a markdown link; credited in the checker), `safe_link`
gained style whitespace collapse + backtick percent-encoding in the URL, baseline regenerated (sites that still carry
trusted local interpolations re-key rather than drop out — only an all-escaped f-string is credited). NEXT: the
`safe_html` markdown step and the plain-`st.markdown` sweep (Phase 2). **Owner live-check (Phase 3):** Signals & Advice →
Evidence & Sources, Analysis → Deep Dive sentiment (MRVL "$300 From $143" must show the dollar signs), Portfolio
Overview → Recent headlines and All News for Your Holdings, sidebar news, Requires Attention / Opportunity cards,
Analysis → Company Overview, quick research bar, earnings playbook reaction line, analyst actions (both places) —
unchanged look, links open, no literal `&amp;`, no `\$`. Earlier: Phase 1 commit 1 SHIPPED 2026-10-19 (`util.safe_link` + checker credit; Opus FIX-FIRST/1 → SHIP/0).
Owner decisions taken: Phases 1+2, credit `safe_link` in the checker, one Opus round. NEXT: commit 2 (migrate the 12
sites; regenerate the baseline), then the separate `safe_html` markdown step and the plain-`st.markdown` sweep (see
the review section below). Originally QUEUED 2026-10-19.

## Why

A security review on 2026-10-19 (Bandit + pip-audit + a manual trace of untrusted text into
`unsafe_allow_html`) found that text from outside the owner's control — Yahoo news titles/links/
publishers, yfinance `.info` company metadata, analyst firm names, and one LLM extraction of pasted
article text — is interpolated **raw** into `st.markdown(..., unsafe_allow_html=True)` at 12 sites.

What is confirmed: HTML/attribute injection (a crafted headline can overlay the page or plant a
convincing phishing link; a `'` in a URL breaks out of the single-quoted `href`; a `javascript:` URL
is not rejected). What is NOT confirmed: script execution — that depends on Streamlit 1.57's renderer
and was not tested (no local runs). Reachability: an attacker needs a Yahoo-syndicated headline
(e.g. a press-release wire) whose title names the company, to pass `news_relevance`.

The fix already exists: `_safe_link` (`app.py:672`) escapes the title and allows only http/https.
Three headline sites use it; these 12 were never migrated. They sit in the antipattern baseline,
which only blocks NEW instances.

## Inventory (each site read and confirmed 2026-10-19; line numbers at `b55f5a5`)

| Tier | Source | Site | Raw field(s) |
|---|---|---|---|
| A | news feed | `app.py:15665-15683` Signals & Advice → Evidence & Sources | title + URL in 2 hrefs |
| A | news feed | `app.py:26893-26904` Analysis → Deep Dive sentiment | title + URL in href |
| A | news feed | `app.py:21401-21407` Portfolio Overview → Recent headlines | title (via `sentiment_velocity.headline_sample`) |
| A | news feed | `app.py:20776-20791` All News for Your Holdings | URL in href, publisher (title already escaped) |
| B | news feed | `app.py:2860` sidebar news; ~`20672` alert card; ~`20711` opportunity card | publisher (`[:18]`/`[:20]`) |
| C | LLM extraction of pasted article | `app.py:4613-4618` earnings playbook | `recent_reaction_summary` |
| D | yfinance `.info` | `app.py:25419`, `25462-25467` Company Overview | name, sector/industry line, one-liner |
| D | yfinance | ~`app.py:8835` quick research | company name |
| D | yfinance upgrades | `app.py:15783`, `27008` analyst actions | firm, from/to grade |

Checked and clean: emails (`notify.py` escapes; no headlines rendered), LLM narratives rendered with
plain `st.markdown` (Streamlit-sanitized), the CSV import (status messages only), the 3 `_safe_link`
sites, no `components.html`/`st.html` anywhere. **Not yet covered:** the remaining baselined
`UNSAFE_HTML_DYNAMIC` sites (267 total at `b55f5a5`; ~54 sampled) — Phase 2.

**Seen live 2026-10-19 — the same sites also eat `$` as LaTeX.** On 📈 Analysis → Deep Dive (MRVL),
the headline "Arete Adjusts Price Target on Marvell Technology to $300 From $143" rendered as
"…to `300 From` 143" in math font: `st.markdown` treats `$…$` as LaTeX even with
`unsafe_allow_html=True`. `safe_html`/`html.escape` do NOT escape `$`, so the migration must also
neutralise `$` (e.g. `&#36;` inside HTML, or the `\$` escape `_render_grow_flat_held` uses for
plain markdown). Add a `$` case to the `safe_link` tests.

**Found in review 2026-10-19 — markdown links are a live script vector, and the scope is wider.** Streamlit
1.57's markdown renderer is called with `urlTransform` set to an identity function (`function dT(e){return e}` in
`.venv/.../streamlit/static/static/js/src.D9MArGZj.js`, verified by the lead), its link component passes `href`
through, and React 18.3 does not block `javascript:` URLs. So any `[x](javascript:…)` that reaches markdown parsing
renders as a clickable script link (reasoned from the bundle + CommonMark, not browser-tested). Consequences:
- **`safe_link` must neutralise markdown syntax too** (whitespace collapsed; `[ ] \ backtick` → character
  references; URLs with whitespace/control chars refused) — **fixed in Phase 1 commit 1** (Opus review FIX-FIRST/1,
  then SHIP/0). Two notes for commit 2 from the confirming review: (i) `style` is escaped but its whitespace is not
  collapsed — collapse it (`" ".join(str(style).split())`) or document it as a trusted literal (every caller passes a
  literal today); (ii) at an inline site a backtick left by `safe_html` in an earlier field can open a code span that
  closes inside `safe_link`'s `href` — the gap is `safe_html`'s, but percent-encoding `` ` `` in the URL would make
  `safe_link` immune regardless of its neighbours.
- **`safe_html` has the same gap at inline (non-`<div>`-led) sites** — publisher, company name, firm and grade fields
  routed through it can still carry a markdown link. Fixing it changes an already-credited escaper used across the
  app, so it is a **separate reviewed step**, not part of the migration.
- **Plain `st.markdown` / `st.caption` of untrusted text is NOT safe either** (this plan previously listed those as
  "Streamlit-sanitized"). That includes AI narratives (regime scenario, catalyst stress, structural scan,
  investigator report, CNBC what-to-watch, institutional lens) whose prompts include feed headlines, i.e. a prompt-
  injected headline could make the model emit a link. Needs its own sweep + a decision on approach (e.g. neutralise
  `](`/`[`` in untrusted markdown before render). Exploit still needs a click.

## Phase 2 additions from the commit-2 review (2026-10-20, Opus SHIP/0)
- N2: `Sector: {_al['sector']}` / `{_op['sector']}` (alert / opportunity cards) and `_sd['sector']` stay raw beside the now-escaped
  publisher; `resolve_sector` falls back to the raw yfinance `.info` sector for unmapped tickers. Low risk, but escape them.
- N3: `st.caption(f"  ↳ [{_si['ticker']}] {_si['title'][:80]}…")` (sector-pattern line, All News) puts a raw title into plain
  markdown — part of the plain-`st.markdown` sweep; also a `$`-LaTeX risk.
- N5: parametrize the checker's lookalike / no-import / other-module / shadowed-alias / nested-call tests over
  `{safe_link, safe_text}` (the real-repo count can't detect a broken `safe_text` credit — it exempts 0 app.py sites today).
- N6 (pre-existing, all four helpers): re-importing over the alias (`from evil import x as _safe_text`, `import evil as
  _safe_text`) keeps the credit because `_module_bound_names` skips imports — close it in a later checker round.
- The 11 migrated sites stay baselined (re-keyed) because their f-strings also carry trusted locals; getting them out of the
  baseline means wrapping those locals too — revisit here, not as churn now.

## Two constraints that shape the plan

1. **`_safe_link` is untested** — it lives in `app.py`, which no test imports, so the `javascript:`
   rejection has never been tested. Move it to `stock_analyzer/util.py` first.
2. **The antipattern baseline is keyed on each call's source text.** Editing a flagged call either
   re-keys it (read as NEW → pre-commit blocks) or un-flags it (count drops → the exact-equality test
   in `tests/test_check_antipatterns.py` fails). So every fix commit regenerates the baseline with
   `python scripts/check_antipatterns.py --init`, and the baseline diff must be checked to remove
   exactly the fixed sites and nothing else.

## Plan

**Phase 1 — helper + 12 sites (2 commits, implementer-built)**
1. Move `_safe_link` → `util.safe_link`; unit tests: `javascript:`/`data:`, mixed-case scheme,
   leading whitespace, `'`/`"` attribute breakout, `None`, truncate-before-escape (no half-cut
   entity). Add `safe_link` to `_ESCAPING_HELPERS` in `scripts/check_antipatterns.py` with the same
   binding tests `safe_html` has, so a fixed site drops out of the baseline and a regression re-flags.
2. Migrate the 12 sites to `safe_link` / `safe_html`; regenerate the baseline; verify its diff.

**Phase 2 — complete the sweep.** One read-only agent triages every remaining
`UNSAFE_HTML_DYNAMIC` site (`check_antipatterns.py --list`) by text source; fix any new external
source found (expected 0-5).

**Phase 3 — live check (owner, ~15-20 min).** After the push, screenshot: Signals & Advice Evidence,
Analysis Deep Dive sentiment, Portfolio Overview (both news blocks), sidebar news, Company Overview.
Check: unchanged look, links work, no literal `&amp;` (double-escape if Yahoo sends entities — the
existing `_safe_link` sites would already show it).

## Estimate

| | Session time | Owner time | Cost |
|---|---|---|---|
| Phase 1 | ~1.5 h (incl. two ~5-8 min hook suite runs) | — | Sonnet build (+ optional Opus review) |
| Phase 2 | ~1 h + fixes | — | one agent sweep |
| Phase 3 | — | ~15-20 min | — |

## Open decisions

1. Scope: Phase 1 only, or 1+2. Lead recommends both — only the full sweep makes "fixed" a true claim.
2. Credit `safe_link` in the checker. Lead recommends yes (the gate then guards these sites).
3. Opus review: not required (no `_GATE_FILES` member touched). Lead recommends one round on the
   checker-credit change — the last widening of that exemption had 4 bypasses found in review.

## Related findings from the same review (not scheduled)

- **Login lockout is per browser session** (`app.py:2990-3029`): a script opening fresh sessions is
  never locked out; password strength is the only real defence. Fix = server-side attempt record.
- **Cookie signing key = SHA-256 of the owner password** (`app.py:2893`): one stolen cookie allows a
  fast offline password crack. The cookie is JS-set, so not HttpOnly. Fix = separate random secret
  (logs out every session once).
- **Anthropic key exposure:** `docs/plans/railway-migration.md` still records the `sk-ant-api03-x_sV2…`
  key as "pending rotation". Owner to confirm whether it was disabled.
- **No security scanning in CI.** Proposal: pip-audit + Bandit in CI, failing only on new HIGH results.
- **pip-audit on the local `.venv` (95 pkgs):** 27 CVEs in 5 packages — gitpython 3.1.57 (via
  Streamlit), urllib3 2.7.0, multidict 6.7.1, soupsieve 2.8.4, pytest 8.4.2 (dev). None reachable with
  attacker input in this app. Production resolves its own versions from the loose ranges.
- **Bandit:** 194 hits, all false positives or style (SHA-1 dedup fingerprints, an error string
  read as SQL, 189 `try/except: pass`).
