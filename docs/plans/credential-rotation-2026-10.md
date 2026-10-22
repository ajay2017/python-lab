# Credential rotation after the Railway security incident — checklist

**Status: ROTATION DONE 2026-10-22 (owner confirmed); verification PENDING until Railway is re-enabled.** Railway
notified its customers of a security incident and asked them to rotate every API key and credential stored with it.
Every credential below has been rotated and its copies handled. What's left is section 5: re-enable the Railway project
(`endearing-magic`: the web service plus the seven cron services), confirm each credential works, and do the one-time
check for signs of misuse.

**Progress:**

| Credential | Rotated at provider | Railway updated | GitHub / Streamlit Cloud copies |
|---|---|---|---|
| `SUPABASE_KEY` | ✅ 2026-10-21: new secret key; old `default` secret key deleted; legacy keys handled (owner, 2026-10-22) | ✅ 2026-10-21 | ✅ GitHub updated · Streamlit Cloud removed (fallback has no DB access until a key is re-added) |
| `SNAPTRADE_CONSUMER_KEY` | ✅ 2026-10-21 | ✅ 2026-10-22 | ✅ (not in GitHub) |
| `APP_PASSWORD` / `APP_READONLY_PASSWORD` | ✅ 2026-10-21/22 | ✅ | ✅ |
| `ANTHROPIC_API_KEY` | ✅ 2026-10-21: new key; previous key deleted; monthly spend cap set (owner, 2026-10-22) | ✅ 2026-10-22 | ✅ |
| `RESEND_API_KEY` | ✅ 2026-10-22 | ✅ | ✅ |
| `FINNHUB_API_KEY`, `FMP_API_KEY`, `FRED_API_KEY` | ✅ 2026-10-22 | ✅ | ✅ |
| Railway account (password, 2FA, tokens, GitHub app) | ✅ 2026-10-22 | n/a | n/a |

**New Supabase key verified working 2026-10-22:** the owner ran a one-off read-only check from their own terminal
(key typed hidden, never shown to Claude). Result: "WORKS - read 1 row from trades", with the key type reported as
`sb_secret_`. So the new key is a secret key and the database rules let it read data. Not yet tested: that the
**old** key is rejected (deleting `default` in Supabase should guarantee it).

**New Anthropic key verified working 2026-10-22,** with the same owner-run method (key hidden, never shown to Claude).
The key was accepted (14 models visible); all four model IDs the app hardcodes are available to it
(`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-opus-5`); and one real message to Haiku
4.5 returned `stop_reason=end_turn`. The key does not start with `sk-ant-api`. The check script's prefix test was
simply wrong; no app code checks the key's prefix (grep 2026-10-22), so this has no effect.

"✅" in the 2026-10-22 rows is the owner's confirmation ("it's all done"), not something checked in a dashboard by
Claude. Section 5's checks after re-enable are what actually confirm each credential works.

**Still to do:**
- [ ] Re-enable Railway and run the section 5 verification table. The new values only take effect on each service's
  next deploy: the web service and all seven cron services.
- [ ] Section 5's one-time misuse checks (Anthropic usage, Resend log, Supabase logs and trades vs broker, SnapTrade).
- [x] Laptop cleanup: `.streamlit/secrets.toml` checked 2026-10-22. `[anthropic] api_key` is empty and no full key
  remains (it had already been cleared). The Supabase and app values there are placeholders. Optional, still open:
  the 6 local Claude Code transcripts that contain the old revoked key.
- [ ] When all of the above is done, set this Status line to DONE and remove the CLAUDE.md queue entry.

**Rule: treat every value that was in Railway → Variables as exposed**, including the Supabase key. Rotating means
create a new value **and** revoke the old one. Creating a new key while the old one still works closes nothing.

---

## 1. What to rotate

The variable list below comes from the code (`os.environ` / `st.secrets` reads in `app.py`, `cron_runner.py`,
`stock_analyzer/`) and from [`railway_start.sh`](../../railway_start.sh), checked 2026-10-21.

### Secrets: rotate all of them

| # | Railway variable | What someone holding it can do | Priority |
|---|---|---|---|
| 1 | `SUPABASE_KEY` | **Read, change and delete every table in the database.** It is the service-role / secret key, so it bypasses RLS. That covers trades, holdings, cash, thesis notes and every history table. | **Critical: rotate first** |
| 2 | `SNAPTRADE_CONSUMER_KEY` (with `SNAPTRADE_CLIENT_ID`) | Call SnapTrade as this app: read the connected Robinhood account's positions, balances and transactions. | **Critical** |
| 3 | `APP_PASSWORD` | Log in to the app as the owner (full write access). | High |
| 4 | `APP_READONLY_PASSWORD` | Log in as the read-only viewer, which shows the whole portfolio. | High (if set) |
| 5 | `ANTHROPIC_API_KEY` | Spend money on your Anthropic account. | High (new key created; old one still to confirm revoked) |
| 6 | `RESEND_API_KEY` | Send email from your Resend sender address (phishing that looks like your alerts). | High |
| 7 | `FINNHUB_API_KEY` | Use your Finnhub quota (live prices). | Medium |
| 8 | `FMP_API_KEY` | Use your FMP quota. | Medium |
| 9 | `FRED_API_KEY` | Use your FRED quota (macro calendar). | Low, but rotate anyway |

### Not secrets: no rotation needed, but check the values weren't changed

`SUPABASE_URL`, `SNAPTRADE_CLIENT_ID` (an identifier; rotate it only if SnapTrade issues a new pair), `ALERT_EMAIL_TO`,
`ALERT_EMAIL_FROM`, `ALERT_RUN_MODE` (per cron service), `ALERT_FORCE`, `ALERT_TEST_EMAIL`. While you're in the
Variables tab, check each one still has the value you expect. An attacker with write access could have pointed
`ALERT_EMAIL_TO` at their own inbox.

---

## 2. Where copies live

The same values are stored in **four** places. Each one must be updated with the new value, or have the old value
deleted.

| Place | Holds | Recommendation |
|---|---|---|
| **Railway → project `endearing-magic` → Shared Variables** (plus each cron service's own `ALERT_RUN_MODE`) | All of section 1 | Update with the new values (step 4 below). |
| **GitHub → repo → Settings → Secrets and variables → Actions** | `SUPABASE_URL`, `SUPABASE_KEY`, `FINNHUB_API_KEY`, `FMP_API_KEY`, `FRED_API_KEY`, `RESEND_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `ANTHROPIC_API_KEY` (read by [`alerts.yml`](../../.github/workflows/alerts.yml) and [`backfill-analyst-prices.yml`](../../.github/workflows/backfill-analyst-prices.yml)) | Both workflows are **manual-run only** (`workflow_dispatch`). **Lead recommendation: delete these secrets rather than update them**, and re-add only on a day you need a manual run. Fewer copies means less to leak. |
| **Streamlit Community Cloud → the dormant app → Settings → Secrets** | Its own `secrets.toml` (Supabase, Anthropic, app password, and whatever else was set there) | It's a cold fallback that still auto-deploys from `main` against the same database. Either paste in the new values, or (simpler) delete its secrets until you actually need the fallback. Once the old Supabase key is revoked, an un-updated copy here just stops working; it doesn't stay a risk. |
| **Your laptop: `.streamlit/secrets.toml`** (gitignored, never committed) | The **old, already-revoked** Anthropic key (`sk-ant-api03-x_sV2…`) plus placeholder Supabase and app values | Delete the old key line. Since the app is never run locally (Hard Rule #3), the file can hold placeholders only. |

Also on your laptop: 6 Claude Code transcript files under `~/.claude/projects/c--Users-ajay-x-ku-python-lab-python-lab/`
contain that same old `x_sV2` key in full (found 2026-10-21). They are local only, and the key is revoked. Deleting
them is optional cleanup.

**Checked 2026-10-21, not a source of exposure:** git history on every branch has no full-length Anthropic key; tracked
files contain placeholders only; the app never prints, logs, emails or displays a key, and `ai_provider.call_llm`
strips the key out of error text.

---

## 3. Rotate at each provider (production is already down, so do it in one sitting)

Railway is disabled, so revoking an old value can't take the live app down. Revoke each old value as soon as its
replacement exists. Keep each new value somewhere safe (a password manager), never in a file in the repo.

- [ ] **Supabase** (dashboard → Project Settings → API Keys). Production uses a new-style secret key (`sb_secret_…`,
  per `docs/plans/railway-migration.md`). Create a new secret key, then **delete the old one**. Also check whether the
  **legacy JWT keys** (`anon` / `service_role`) are still enabled. If they are and nothing uses them, disable them: a
  legacy `service_role` key is a second full-access credential. Do **not** touch RLS (Hard Rule #2).
  **Don't confuse this with Account → Access Tokens** (`sbp_…`). Those are personal tokens for Supabase's management
  API and CLI, and the app never reads one: the database connection uses only `SUPABASE_URL` + `SUPABASE_KEY`
  (`db.py` ~1172). Found 2026-10-21: two such tokens, both "Never used", one expired (`key`, LEGACY) and one that never
  expires (`drisha`). An expired token there does not mean the app's key expired. Lead recommendation: delete both,
  since an unused token that never expires is a credential with no benefit.
- [ ] **SnapTrade** (Dashboard → Personal API Key page). Regenerate the consumer key and revoke the old one. Afterwards,
  check whether the Robinhood connection survived (step 5). If broker sync fails, reconnect from 💰 Account.
- [ ] **Anthropic** (console.anthropic.com → API Keys). New key created 2026-10-21. Still to do: confirm the key that
  was in Railway **before** today is deleted, confirm the old `x_sV2…` key also shows as deleted, and **set a monthly
  spend cap**, which limits the damage from any future leak.
- [ ] **Resend** (dashboard → API Keys). Create a new key and delete the old one. While there, check the sending log
  for emails you didn't send.
- [ ] **Finnhub, FMP, FRED** (each provider's dashboard). Regenerate the key and revoke the old one. If a provider has
  no way to regenerate, write that down here as an accepted risk: these are read-only market-data keys.
- [ ] **App passwords.** Choose a new `APP_PASSWORD` and, if you use it, a new `APP_READONLY_PASSWORD`. Don't reuse
  either old value. They exist only in Railway (and Streamlit Cloud), so "rotating" them just means setting new values.
  Changing `APP_PASSWORD` is **mandatory**, not optional: the 30-day "remember me" cookie is signed with a key derived
  from it (`app.py` `_auth_cookie_sign_key`), so anyone with the old password could forge a valid cookie until it
  changes. Changing it also logs out every saved session, including viewer sessions. Leave `APP_READONLY_PASSWORD` blank
  to turn viewer login off. **Don't put `"` or `\` in either password:** `railway_start.sh` writes them into a quoted
  TOML string, where `"` breaks the file and `\` is read as an escape.
- [ ] **Railway account itself.** Change the Railway login password, turn on 2FA if it isn't already, revoke any
  Railway API tokens you don't use, and check the Railway GitHub app's access to the repo (GitHub → Settings →
  Applications). Follow anything else Railway's own notice asks for.

---

## 4. Update the copies

- [ ] **Railway → Shared Variables:** paste the new values for every row in section 1's secrets table. Check the
  non-secret values while you're there.
- [ ] Deploy the staged variable changes to the **web service and all seven cron services**. The Supabase client is
  created once per process (`db._CLIENT`), so a service only picks up a new key when it restarts.
- [ ] **GitHub Actions secrets:** delete them (recommended) or update them.
- [ ] **Streamlit Cloud secrets:** delete them or update them.
- [ ] **Laptop:** remove the old key from `.streamlit/secrets.toml`.

Put the new `ANTHROPIC_API_KEY` in **before** re-enabling the services. If it's missing, the app still works, but news
sentiment falls back to VADER and that day's composite scores are computed differently
(`docs/plans/anthropic-key-enable.md`).

---

## 5. Re-enable and verify

Re-enable the Railway services, wait for the deploy, hard-refresh `drishta.up.railway.app` (Ctrl+F5).

| Check | What you should see | Proves |
|---|---|---|
| Log in with the **new** owner password; try the old one | New works, old is rejected | `APP_PASSWORD` |
| 🏠 Home loads holdings; no "row-level security" or "portfolio unreadable" banner | Normal page | `SUPABASE_KEY` (new key works) |
| 🩺 System Trust | Cron liveness and the data-source checks are green or amber, not red from auth errors | Supabase, providers |
| Data Health / 🩺 check ③ | Finnhub and FRED rows show successful calls, not auth errors (FMP was already failing with "402 Payment Required" before the incident) | `FINNHUB_API_KEY`, `FRED_API_KEY`, `FMP_API_KEY` |
| Supabase table `sentiment_llm_cache` | Rows with today's UTC date | `ANTHROPIC_API_KEY` |
| 🧠 AI Insights: one Ask question | An answer, not "no key" | `ANTHROPIC_API_KEY` |
| Next cron email (or run `cron-scan` once with `ALERT_TEST_EMAIL=1`, then remove it) | Email arrives at `ALERT_EMAIL_TO` | `RESEND_API_KEY` |
| Next `cron-broker` run log, and 💰 Account broker status | Sync succeeds, no SnapTrade auth error | `SNAPTRADE_*` |
| Supabase (old key) | A request with the **old** secret key is rejected; it no longer appears in the API Keys list | Old Supabase key revoked |

### Look for signs of misuse

Do this once. The window to check is the dates in Railway's notice.

- [ ] **Anthropic** console usage: spend on days or models the app doesn't use.
- [ ] **Resend** logs: emails you didn't send.
- [ ] **Supabase** logs: API requests from unfamiliar IPs or at odd times. Also spot-check `trades` and `holdings`
  against your broker for rows you didn't create. 💰 Account's broker drift banner is a quick first look.
- [ ] **SnapTrade**: connections or API activity you don't recognise.

---

## 6. Afterwards

- [ ] Fill in the date each item was rotated in this file and change the Status line to DONE.
- [ ] Update the `docs/requirements.md` / `DEVELOPMENT.md` secrets notes if any variable was added, removed or renamed.
- [ ] Follow-up (separate, already planned): put the Anthropic key lookup in one place. Today 20 call sites read it in
  4 different orders, which makes a rotation easy to get half-right. That work belongs to the LLM model-routing plan,
  not to this checklist.
