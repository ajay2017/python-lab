# Switching the Anthropic key on in Railway — checklist

**STATUS CORRECTED 2026-10-19: the key is ALREADY ON, and has been since 2026-07-14.** `sentiment_llm_cache` has rows
every day from 2026-07-14 — the day the bidirectional LLM rescore shipped (`10edd8f`) — and a row is only written
after a successful Anthropic call (`news_intelligence.rescore_headlines_llm` returns `None` on any error). The owner
confirmed 2026-10-19 that the key is in Railway and is NOT the exposed `sk-ant-api03-x_sV2…` key. So the "NOT DONE"
status below, and the 2026-10-17 belief it rests on ("the key is not set in Railway"), were wrong: there was never a
VADER-only production period after 2026-07-14, and no regime change inside any October measurement. Still worth doing
from the checklist: step 2 (a monthly spend cap), and confirming the old `x_sV2` key shows as disabled in the console.
The "Roll back" section below is the live procedure if the owner ever wants production LLM-free. The original text is
kept below as the design record.

**Status: PREPARED 2026-10-18, NOT DONE — an owner decision.** The key is optional by principle
(`docs/requirements.md` ~734: zero runtime dependency on LLM connectivity; owner 2026-10-17: "if the LLM is there it
can be used, but if not the app should not fail"). Every key-reading path already checks for the key and falls back
(VADER sentiment, "INERT" cron lanes, AI pages unavailable), so switching it on or off never breaks the app. But it
is a deliberate change, not a config tweak: one path feeds the composite score.

## What the key turns on
| Area | Code | Changes a call? |
|---|---|---|
| **Bidirectional LLM news sentiment** — re-scores each ticker's headlines once per UTC day, cached in `sentiment_llm_cache` and shared by the app and every cron lane | `bundle_loader.load_bundle` → `news_intelligence.rescore_headlines_llm` | **Yes** — the sentiment pillar (10% of the composite), about ±1-2 pts; can lower as well as raise; can flip a name near `COMPOSITE_BUY` / `COMPOSITE_STRONG_BUY` |
| **Home news noise filter** — suppress-only re-score of held headlines before Act Today / Review news cards (Home only; the cron Brief stays VADER) | `news_intelligence.rescore_brief_news` (F-308) | **Yes** — fewer false news cards on Home |
| Cron lanes `_run_thesis` (weekly thesis review), `_run_debrief`, `_run_monthly_report` | `cron_runner.py` | No (awareness; today they log "INERT — no ANTHROPIC_API_KEY set") |
| AI pages and helpers: Ask / Portfolio Q&A, Portfolio Investigator, AI Snapshot, Thesis drafting at BUY, Pre-Mortem, Red Team, Debate, Structural Scan, regime / catalyst stress narratives, premarket stance, analyst-research extraction, earnings intel, broker-screenshot ticker fallback | `ai_provider`, `portfolio_qa`, `thesis_*`, `premortem_*`, `debate_agent`, `structural_scanner`, … | No — they narrate, never originate a call |

## Before
1. **Rotate the key** at console.anthropic.com — the Railway migration's "Phase 0a: rotate the Anthropic key" is still
   outstanding (deferred by owner choice; memory `project_railway_migration`). Do not reuse the old key.
2. **Set a monthly spend cap** in the Anthropic console. News costs are small (one cheap Haiku call per ticker per day,
   plus Home's batched filter — an estimate of a few dollars a month, not measured); the on-demand pages are the
   exposure (the Investigator can use Opus).
3. **Decide the score record (Definition-of-Done #8):** composites recorded after the switch use LLM sentiment, so they
   are not like-for-like with earlier rows. Lead recommendation: a dated note (the D-5 precedent), no
   `COMPOSITE_WEIGHTS_VERSION` bump — the weights don't change, the sentiment source does. The owner decides at switch-on.

## Switch on
4. Railway → project `endearing-magic` → **Shared Variables** → `ANTHROPIC_API_KEY` = the new key (shared so the web
   service AND the seven cron services get it; `railway_start.sh` already maps it into `[anthropic] api_key`).
   Redeploy the web service.

## Verify (first day)
5. Railway logs: the next `cron-thesis` run no longer prints "thesis: INERT"; debrief / monthly likewise when due.
6. Supabase `sentiment_llm_cache` gains today's rows with non-empty `headlines` (the cache is keyed by UTC date).
7. 🏠 Home: news cards still render cleanly; the "news noise filter couldn't check N negative headlines" caption appears
   only if a batch failed. First Home load of the day may be slower (cache misses wait on the API, 8 s timeouts).
8. 🧠 AI Insights: one Ask question and one AI Snapshot work.
9. After a week: check the console spend vs the cap, and how many Review news cards a day you see (D-G).

## Docs on switch-on day (lead)
- Dated note in `docs/requirements.md` (F-309 / sentiment pillar) and `docs/architecture.md`: "from <date> the sentiment
  pillar uses LLM-scored headlines".
- `docs/shipped-log.md` entry; memory `feedback_no_llm_dependency_in_prod` updated (key now present in prod).
- **G-30 regime marker:** date the G-30 readout footnote (`gate_ledger_readout.readout_footnotes`) — from the switch-on day the app's held headlines are LLM-filtered (suppress-only) while cron rows and new-pick rows stay VADER, so G-30 add-lane rows from the app use a different scorer.

## Roll back
Remove the variable and redeploy. Note: `sentiment_llm_cache` rows already written for today keep being served until
the UTC day ends (they are reused when the headlines still match), so the score returns to VADER from the next day.
