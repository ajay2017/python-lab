# DRISHTA — Railway Cloud Migration Plan

**Date:** 2026-07-23
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 4.6
**Status:** PILOT LIVE as of 2026-07-24 at `drishta.up.railway.app`. Phases 0b, 1, 2, 3, 4
done (with real deviations from this plan — see "What actually happened" below); Phase 0a
(Anthropic key rotation) deferred by user choice; Phase 5 (parallel-run comparison) is the
current phase; Phase 6 (cutover) not started.

### What actually happened vs. this plan (2026-07-24)

- **No "Secret Files" feature found in Railway's current UI.** Settings only has
  Source/Networking/Scale/Build/Deploy/Config-as-code/Feature-flags/Danger — no file-based
  secrets option as this plan's Phase 3 assumed. Railway's Variables tab (flat env vars,
  auto-suggested by scanning the source for `os.getenv`/`os.environ` calls) is the only
  secrets mechanism available.
- **That surfaced a real architecture gap:** ~50 call sites across `app.py` and
  `stock_analyzer/` call `st.secrets.get(...)` directly. With no `.streamlit/secrets.toml`
  file on disk at all (env-vars-only), Streamlit's lazy secrets loader raises
  `StreamlitSecretNotFoundError` on the *first* access anywhere — not a graceful per-key
  miss — which crashed Home (stuck on the loading radar) on first deploy.
- **Fix:** `railway_start.sh` (new file) writes `.streamlit/secrets.toml` from the Railway
  env vars at container startup, before launching Streamlit — fixes every `st.secrets` call
  site at once, no app-code changes needed elsewhere. `railway.toml`'s `startCommand` now
  runs this script. Full detail: `docs/architecture.md` §9.1b.
- **`app.py::_check_password()`** also got a direct `APP_PASSWORD`/`APP_READONLY_PASSWORD`
  env-var fallback (commit `3310a61`) plus the Phase 1b brute-force lockout — belt-and-
  suspenders alongside the materialized secrets.toml.
- **A bad `FINNHUB_API_KEY` value (pasted with stray quotes) triggered a real, live
  confirmation of the multi-source price failover** — the price strip correctly fell back
  to "Yahoo Finance (15-min delayed)" until the key was fixed, then self-healed to
  "Finnhub (real-time)". Working as designed.
- **Cost:** always-on Hobby usage projected ~$8.30/mo (base $5 + usage overage), not the
  plan's assumed flat $5. Enabled Railway's **Serverless toggle** (Settings → Deploy) since
  the app isn't used overnight — sleeps after 10 min idle, wakes on next request from a
  cached build image. Watch actual next-day cost to confirm it helped; an open browser tab's
  60s auto-refresh may count as traffic and prevent sleep. Full detail + fallback option
  (scheduled external stop/start) in memory `project_railway_migration`.
- **Phase 0a (Anthropic key rotation) — deferred by explicit user choice**, not automated
  away. Note: the exposed key (prefix `sk-ant-api03-x_sV2...`) was printed into a Claude
  session transcript via an over-broad `grep` during Phase 3 troubleshooting — a real
  exposure event, independent of Railway itself. Still pending rotation.

---

## Context

Current deploy: Streamlit Community Cloud (free tier) — auto-deploys from `main`, sleeps after
inactivity (~30s wake-up penalty at market open), secrets stored in the Streamlit Cloud dashboard.

Goal: Pilot Railway (Hobby, $5/mo) as a replacement — always-on, dedicated container, custom
domain support, structured logs. If pilot succeeds, cut over fully and retire Streamlit Cloud.

The headless GitHub Actions cron (`alerts.yml`) is **not affected** — it connects directly to
Supabase and runs regardless of where the UI is hosted.

---

## Security analysis

### Existing risks (platform-independent — fix before migration)

| Risk | Severity | Action |
|---|---|---|
| Live Anthropic API key in `.streamlit/secrets.toml` | **High** | Rotate immediately at `console.anthropic.com` |
| No brute-force protection on login form | Medium | Add 5-line delay in `_check_password()` (Phase 1b below) |
| Plain `==` password comparison | Low | Acceptable for single-user personal app with a strong password |
| Gate fails open when `app.password` secret is missing | Low | Ensure secret is always set in Railway Secret Files |

### Railway-specific risks

| Risk | Severity | Mitigation |
|---|---|---|
| No Streamlit "Private app" OAuth layer | Medium | Password gate + brute-force delay (Phase 1b) + strong password |
| Railway URL is publicly reachable | Low | Use a strong random password; optionally add a custom domain |
| Env vars visible as plaintext in dashboard | Low | Use **Secret Files** (encrypted at rest) for `secrets.toml`, not env vars |
| Module-level Supabase singleton breaks at >1 replica | Low | Pin `replicas = 1` in `railway.toml` |

### What is NOT a risk on Railway

- **HTTPS**: Railway provides automatic TLS for all services — same as Streamlit Cloud.
- **Supabase RLS**: Service-role key stays server-side in both cases; RLS is on for all tables.
- **Session state**: Streamlit's `session_state` is per-WebSocket — no cross-user leakage.
- **GitHub Actions cron**: Connects directly to Supabase; unaffected by which platform hosts the UI.

---

## Architecture compatibility

| Concern | Status |
|---|---|
| `st.secrets` resolution | Works — Railway mounts `.streamlit/secrets.toml` via Secret Files; Streamlit reads it at startup |
| `stock_analyzer/` secret reads | Already Railway-ready — `db._supabase_creds()` and `providers/_util.get_secret()` check env vars first |
| `@st.cache_data` decorators | Same in-process behaviour; cache clears on redeploy (same as Streamlit Cloud) |
| `cron_runner.py` | Stays on GitHub Actions; uses env vars exclusively; zero changes needed |
| Python 3.12 | Railway supports via `runtime.txt` — already present |
| `requirements.txt` | No changes needed |
| `.streamlit/config.toml` (theme) | Works as-is |

---

## Phase 0 — Security hygiene (do BEFORE any Railway work)

**0a. Rotate the Anthropic API key.**

1. Go to `console.anthropic.com` → API Keys.
2. Find the key prefixed `sk-ant-api03-x_sV2` → Disable / Delete it.
3. Generate a new key.
4. Update it in:
   - Streamlit Cloud dashboard (App → Settings → Secrets → `[anthropic] api_key`)
   - Your local `.streamlit/secrets.toml`
   - GitHub Secrets (`ANTHROPIC_API_KEY`) if the cron uses it

**0b. Verify `.gitignore` covers secrets.**

```
git check-ignore -v .streamlit/secrets.toml
```
Should print a line. If it doesn't, add `.streamlit/secrets.toml` to `.gitignore`.

**0c. Choose a strong password.**
Generate a random password ≥ 20 characters — e.g.:

```
openssl rand -base64 24
```

Keep it somewhere safe; you will paste it into Railway Secret Files in Phase 3.

---

## Phase 1 — Add the two config files

### 1a. Add `railway.toml` at the repo root

```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true"
healthcheckPath = "/_stcore/health"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3

[[services]]
replicas = 1
```

The `replicas = 1` line prevents Railway from ever scaling to multiple instances,
which would break the module-level Supabase singleton and in-process cache.

### 1b. Add brute-force protection to `_check_password()` in `app.py`

`_check_password()` currently has no rate limiting. This is the only meaningful new
risk Railway introduces (no Streamlit OAuth layer on top). The fix is ~10 lines added
inside the existing function — no policy constant, no gate, no Opus review required.

Add `import time` near the top of `app.py` if not already present, then replace the
body of `_check_password()` with:

```python
def _check_password():
    try:
        expected    = st.secrets.get("app", {}).get("password", "")
        ro_expected = st.secrets.get("app", {}).get("readonly_password", "")
    except Exception:
        expected = ro_expected = ""
    if not expected or st.session_state.get("auth_ok"):
        return

    _render_brand(large=True)
    st.subheader("Sign In")

    _fails = st.session_state.get("_login_fails", 0)
    _locked_until = st.session_state.get("_login_locked_until", 0.0)
    if time.time() < _locked_until:
        remaining = int(_locked_until - time.time())
        st.error(f"Too many failed attempts. Try again in {remaining}s.")
        st.stop()

    pwd = st.text_input("Password", type="password")
    if st.button("Login", type="primary"):
        if pwd == expected:
            st.session_state.auth_ok = True
            st.session_state["auth_role"] = "owner"
            st.session_state["_login_fails"] = 0
            st.rerun()
        elif ro_expected and pwd == ro_expected:
            st.session_state.auth_ok = True
            st.session_state["auth_role"] = "viewer"
            st.session_state["_login_fails"] = 0
            st.rerun()
        else:
            _fails += 1
            st.session_state["_login_fails"] = _fails
            if _fails >= 10:
                st.session_state["_login_locked_until"] = time.time() + 300  # 5-min lockout
                st.session_state["_login_fails"] = 0
            elif _fails >= 3:
                time.sleep(2)   # 2s delay after 3rd+ failure
            st.error(f"Incorrect password ({_fails} failed attempt{'s' if _fails != 1 else ''}).")
    st.stop()
```

**Commit these two changes together** with a conventional commit message, e.g.:

```
feat(deploy): add railway.toml + login brute-force delay

Adds railway.toml (start command, health check, replicas=1 guard)
to support a Railway Cloud pilot deploy alongside Streamlit Cloud.

Also adds a failed-attempt counter and 5-minute lockout to
_check_password() — Railway has no Streamlit OAuth layer on top,
so the password gate needs its own rate-limit protection.

Co-Authored-By: Ajay with Claude Sonnet 4.6 <ajay.x.ku@accenture.com>
```

---

## Phase 2 — Railway project setup

1. Go to `railway.app` → New Project → Deploy from GitHub repo.
2. Connect your GitHub account; select `ajay2017/python-lab`, branch `main`.
3. Railway auto-detects `requirements.txt` and `runtime.txt` (Python 3.12) via Nixpacks.
4. **Stop before first deploy** — click Configure and pause. You must set secrets first
   (Phase 3) or the app starts without a DB connection and crashes.

---

## Phase 3 — Configure secrets on Railway

Use **Secret Files** (not environment variables) for the `secrets.toml` block.
Secret Files are stored encrypted and not shown in plaintext in the Railway dashboard.

### 3a. Create the Secret File

Railway service → Settings → Secret Files → Add file:

- **Path:** `.streamlit/secrets.toml`
- **Contents:** paste the full TOML below, filling in real values:

```toml
[supabase]
url = "https://<your-project-id>.supabase.co"
key = "sb_secret_***"        # service-role key — NEVER the anon/publishable key

[anthropic]
api_key = "sk-ant-..."       # the NEW rotated key from Phase 0a

[app]
password = "<your-strong-random-password>"   # from Phase 0c
readonly_password = ""                        # leave blank unless you need a viewer account

[fred]
api_key = "..."              # optional — enriches macro calendar

FINNHUB_API_KEY = "..."      # optional
FMP_API_KEY     = "..."      # optional
RESEND_API_KEY  = "..."      # only needed if running cron on Railway (not required for pilot)
```

This is byte-for-byte the same TOML format Streamlit Cloud uses. Streamlit's `st.secrets`
reads it at process startup. Zero code changes needed for this to work.

### 3b. GitHub Actions cron — no changes needed

The `alerts.yml` cron runner uses GitHub Secrets (`SUPABASE_URL`, `SUPABASE_KEY`, etc.)
and calls Supabase directly — it does not call the Streamlit app. It keeps running exactly
as today on GitHub Actions regardless of where the UI is hosted.

---

## Phase 4 — First deploy and smoke test

**4a.** Trigger the deploy (push or click "Deploy" in Railway). Watch the build log.
Nixpacks cold build takes 3–5 min; subsequent deploys are faster with layer caching.

**4b.** Check the health endpoint:
```
https://<your-service>.up.railway.app/_stcore/health
```
Should return `{"status":"ok"}`.

**4c. Smoke test checklist** (keep Streamlit Cloud live in parallel during this phase):

| Test | What to verify |
|---|---|
| Password gate | Wrong password shows error + counter increments; correct password opens app |
| Brute-force lockout | Fail 10 times → 5-min lockout banner appears |
| Home page | Today's Brief renders; no "Could not load" banners |
| Portfolio data | Holdings, watchlist, trades load from Supabase correctly |
| Trade journal — BUY | Submit a test trade; confirm it appears in Supabase |
| Trade journal — SELL | Same; verify `recalculate_from_trades` balances correctly |
| AI Insights | Thesis reviewer loads; confirms new Anthropic key works |
| Analyst Coverage | Paste a short excerpt; LLM extracts tickers correctly |
| Macro calendar | Loads if FRED key set; graceful degradation if not |
| Data Health tab | No red auth errors; provider status shows green |
| GitHub Actions cron | Let a scheduled run fire; confirm email arrives (unchanged) |
| Read-only viewer | Test `readonly_password` if you use it |

---

## Phase 5 — Performance comparison (the pilot value)

Run both platforms in parallel for 1–2 weeks. Key differences to observe:

| Metric | Streamlit Cloud (free) | Railway Hobby ($5/mo) |
|---|---|---|
| Cold start after sleep | 15–30s wake-up | None — always on |
| First page load (warm) | ~3–5s | ~2–3s (dedicated container) |
| Memory | Shared, unconstrained | 8 GB dedicated |
| `@st.cache_data` survival | Cleared on every wake | Cleared only on redeploy |
| Deploy trigger | Auto on `git push main` | Auto on `git push main` (same) |
| Deploy time | 1–3 min | 1–3 min (similar) |
| Custom domain | Subdomain only on free | Full custom domain on Hobby |
| Logs | Streamlit Cloud dashboard | Railway dashboard (structured, searchable) |
| Reboot equivalent | Manage app → Reboot | Railway dashboard → Redeploy |

The biggest practical win is **no sleep** — the 30-second wake-up on Streamlit Cloud free
tier is the most painful daily friction for an app you open at 8 AM market open.

---

## Phase 6 — Cutover (if pilot succeeds)

1. Update your bookmark to the Railway URL, or configure a custom domain.
   - Railway service → Settings → Domains → Add custom domain → CNAME to Railway's DNS.
   - Takes 5 min; Railway auto-provisions TLS.
2. Optionally hibernate or delete the Streamlit Cloud app (App → Settings → Delete app).
   Both platforms point to the same Supabase DB — they don't conflict if you run both
   temporarily during transition.
3. GitHub Actions cron continues unchanged.

---

## Summary — what changes vs what stays

| Item | Change? |
|---|---|
| `railway.toml` | **New file** (Phase 1a) |
| `_check_password()` in `app.py` | **~10-line addition** (Phase 1b) |
| `import time` in `app.py` | **1-line addition** if not already present |
| All `stock_analyzer/` modules | None |
| `requirements.txt` | None |
| `.streamlit/config.toml` | None |
| `cron_runner.py` + GitHub Actions workflows | None |
| Supabase / RLS | None |
| All gates, scoring, constants | None |

**Total code touched:** two spots in `app.py`. Everything else is Railway dashboard configuration.
