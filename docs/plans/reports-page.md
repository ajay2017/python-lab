# 📄 Reports as its own page (one home for every report)

**Status (2026-11-10): STEP 2 IN BUILD.** Step 1 SHIPPED `ec9ea0a` (F-276d) and VERIFIED LIVE by the owner the same day (nav position, two Account tabs, Last week −$532 / −7.67%, Last month October −$642 / −8.47% on $7,582 with Unexplained +$67 amber; 🙈 Hide values not yet seen). Owner decision for step 2 (2026-11-10): a read-only viewer keeps today's access, so the Reports page is no longer owner-only in the nav; Performance Review and Tax Report stay owner-only inside it, the two debriefs stay viewable. Earlier the same day: PLANNED, owner chose option B. Step 1 next, after TJDate C2a is committed. Step 2 waits for the owner to see step 1 live. Owner decisions 2026-11-10: default view = Performance Review; rename the AI monthly report (step 2).

## Why

The owner's reports sit on two pages:

| Report | Where today | Question it answers |
|---|---|---|
| 📈 Performance Review | 💰 Account → 📄 Reports | What did the account do in a period, and why (live; Last week / Last month / Quarter / Custom) |
| 🧾 Tax Report | 💰 Account → 📄 Reports | Realized short / long-term gains by tax year, wash-sale flags |
| 🗓️ Weekly Debrief | 🧠 AI Insights → 📅 Debriefs | AI narrative of the week, same numbers as Performance Review "Last week" |
| 🧭 Monthly Portfolio Intelligence | 🧠 AI Insights → 📅 Debriefs | Is the engine picking well, and do I act on its picks (AI, frozen once generated) |

There is no Reports entry in the left navigation, and two reports both read as "the monthly report". On 2026-11-09, "regenerate the October monthly report" meant Performance Review → Last month, but the owner reasonably clicked 🧭 Monthly Intelligence.

## Options considered

- **A.** Move only Performance Review and Tax Report to a new page. Least work, but the two "monthly" reports stay on different pages.
- **B (chosen 2026-11-10).** One 📄 Reports page holds all four, each headed by the one-line question it answers. Built in two deploys.

## Step 1: new page with Performance Review and Tax Report (display only)

- **Nav:** add `("Reports", "📄 Reports", <material icon>)` to the MAIN group in `_NAV_GROUPS` (app.py ~3352), between Account and Market Scanner. Add `"📄 Reports"` to `_OWNER_ONLY_PAGES` (~3409); keep the in-page `db.is_readonly()` guard as a backstop.
- **Move:** the `with _acct_tab_reports:` block (app.py ~41955 to the end of the Account page, ~960 lines) becomes `elif page == "📄 Reports":`, dedented one level. It reads none of the Account tab's locals (checked 2026-11-10), so it moves as one piece. Account goes from 3 tabs to 2. Session keys (`_rpt_kind_radio`, `_perf_*`) and day caches keep their names. No calculation, gate or stored data changes.
- **Default view (owner decided 2026-11-10):** Performance Review first (radio order swapped). A short "Which report answers what" header lists all four. Until step 2 it points to 🧠 AI Insights → 📅 Debriefs for the Weekly Debrief and Monthly Intelligence.
- **Pointers to update** (grep "Account → Reports", "📄 Reports", "Account page's Reports"):
  - app.py: Trade Journal ~33419; Account ~41238 and ~41842; AI Insights ~45637 and ~45782.
  - `stock_analyzer/notify.py` ~1587 (debrief email text) and ~1422 (docstring).
  - `stock_analyzer/debrief_advisor.py` ~27 and ~761: LLM prompt / output text. This is a `_GATE_FILES` member, so it needs an Opus review. Re-run `tests/test_llm_request_golden.py` (regen with `LLM_GOLDEN_REGEN=1`).
  - `cron_runner.py` ~2763 (comment only, gate file).
  - `tests/test_debrief_email_money_block.py` (docstrings).
- **Docs:** User Guide (the "8. 📄 Reports — a third tab on this page" section, and the page list), `docs/user-manual.md`, `docs/requirements.md` (new F-ID for the page; the F-276 rows), `docs/architecture.md` page map.
- **Check:** grep every pointer before and after. Screenshot: the nav shows 📄 Reports between Account and Scanner; Account shows 2 tabs; Performance Review "Last month" for October still reads −$642 / −8.47%; Tax Report unchanged; "🙈 Hide values" still masks every figure on both.

## Step 2: bring in the Weekly Debrief and Monthly Intelligence (after step 1 is seen live)

- Move the `with _ai_tab_deb:` block (app.py ~45626–46277) into the Reports page as a sub-view. It depends only on `_ai_db` (`stock_analyzer.db`) and `_ai_api_key = _anthropic_key()`; recreate both on the Reports page. AI Insights keeps a one-line pointer in place of the 📅 Debriefs tab.
- **Rename (owner decided 2026-11-10):** "🧭 Monthly Portfolio Intelligence" becomes "🧭 Monthly Decision Review (AI)", so only one report reads as "monthly". Label only: the stored `monthly_reports` rows, the cron and the email subject logic don't change (grep every user-facing mention, including the User Guide's "🧭 Monthly Intelligence Report" bullet and the 📜 Recommendations History page's reference).
- Re-check the pointers, User Guide and requirements as in step 1.
