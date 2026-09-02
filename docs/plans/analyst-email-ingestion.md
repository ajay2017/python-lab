# Analyst Research Email Ingestion — Plan

**Status: REVISED DESIGN 2026-09-01, then DEPRIORITIZED same day (user call — not a must-have, no urgency).** Opus `planner` verdict: PROCEED WITH CHANGES. No code written. Original design (Gmail/IMAP + true auto-forward + 5-vendor allowlist) is superseded after live testing this session proved two things: (1) Resend's Inbound feature accepts real external mail at a `<id>.resend.app` address with zero DNS/domain setup, and its Received Emails API is explicitly documented as poll-safe without a webhook; (2) a manual Yahoo "Forward" rewrites the MIME `From:` to the user's own address and inlines the original message as quoted text — it does NOT preserve the vendor's real sender or attach a parseable original. Both of the original Phase-0 blockers (auto-forward filter, capturing a vendor `From:`) are gone as a result — the manual-forward model needs neither. Resume Phase 1 build once the one remaining Phase-0 build-time check (§6a) is done — see memory `project_analyst_email_ingestion`.

**Goal:** Let the user *manually forward* analyst newsletters (CNBC Pro, Goldman, JPMorgan, BofA, Morgan Stanley) from Yahoo Mail to a dedicated Resend receiving address and have the app pick them up automatically, run the **existing** `analyst_intel.extract_report()`, and land the result in a **pending** state that surfaces through the **existing** editable preview-card UI. The manual paste textarea stays as-is — this is an additive second input path. **Awareness-only end to end:** promoted rows go to `analyst_coverage`, which per F-154 is NEVER read by the rule-based scoring/gating engine.

**Why this shape:** the paste flow already has the two safety properties we must preserve — the LLM only extracts atomic per-firm facts (aggregates are pure-Python `derive_consensus()`), and *nothing is written to `analyst_coverage` without human review*. Email ingestion changes only *how raw text arrives*, not extraction or the approval gate.

---

## 1. Data model — staging table `analyst_inbox_pending` + cursor table `research_inbox_config`

`analyst_coverage` today is a **clean, approved-facts table**: every row is a human-reviewed fact, read by the Research Scorecard grading (F-154c), watchlist candidates, and the earnings playbook. Injecting unapproved `status='pending'` rows into it would force *every* consumer to add a `review_status='approved'` filter — miss one and un-reviewed LLM output leaks into a decision-adjacent surface. A staging table keeps the promotion boundary explicit and leaves the clean table + all its consumers **untouched** (no DDL on `analyst_coverage`).

Grain = **one row per email** (the extractor already returns a `list[dict]` of per-stock records for one article, matching the preview's `_ac_preview` list).

```sql
create table if not exists analyst_inbox_pending (
    id               bigint primary key generated always as identity,
    resend_email_id  text unique,           -- Resend's per-email id → dedup/idempotency key
    rfc_message_id   text,                  -- RFC-5322 Message-ID parsed from headers, if present (audit only, NOT the dedup key)
    sender           text,                  -- real MIME From addr that passed the trusted-sender check (always the user's own Yahoo addr under the manual-forward model)
    sender_auth      text,                  -- ingest-time SPF/DKIM/DMARC verdict: 'pass'|'fail'|'unknown' (shown on the review card)
    source_label     text,                  -- BEST-EFFORT publisher parsed from the forwarded-block body text; may be null (DISPLAY only, never a security signal)
    subject          text,
    received_at      timestamptz,           -- Resend's created_at (authoritative receive time — NOT the email's own spoofable Date header)
    raw_text         text,                  -- normalized plaintext handed to the LLM (audit + re-extract)
    extracted        jsonb,                 -- list[dict] from extract_report; null if extraction failed
    extract_error    text,                  -- LAST_EXTRACT_ERROR when extraction returned None
    status           text default 'pending', -- pending|reviewed|error|empty|discarded
    truncated        boolean default false,  -- true if raw_text hit the char cap (shown to human)
    created_at       timestamptz default now()
);
alter table analyst_inbox_pending enable row level security;
create policy "service_role_all_analyst_inbox_pending" on analyst_inbox_pending
    for all to service_role using (true) with check (true);

create table if not exists research_inbox_config (
    id               int primary key default 1,
    last_created_at  timestamptz,           -- high-water mark: Resend created_at of the last durably-processed email
    updated_at       timestamptz default now()
);
alter table research_inbox_config enable row level security;
create policy "service_role_all_research_inbox_config" on research_inbox_config
    for all to service_role using (true) with check (true);
```

- **Dedup / idempotency:** `resend_email_id` UNIQUE — Resend assigns a guaranteed-present, guaranteed-unique id per received email, which is the natural idempotency key. (The original plan's IMAP `email_uid` / RFC `message_id` UNIQUE design no longer applies; `rfc_message_id` is retained as an audit-only column, not the dedup key, since it can be missing/malformed on a forward.)
- **Ships INERT** until the DDL is applied: `db.load_analyst_inbox_pending()` returns an empty DataFrame on missing table / DB failure, the cron lane no-ops, the UI section shows nothing.
- **`research_inbox_config` is a single-row config table**, modeled directly on the existing `db.load_snaptrade_config`/`save_snaptrade_config` pattern (`db.py:4237-4290`). **Why a dedicated cursor table and not `select max(received_at) from analyst_inbox_pending`:** Phase 3 explicitly plans retention cleanup of old reviewed/discarded staging rows. A cursor derived from the staging table's own max would regress the moment old rows are deleted, causing an already-reviewed email to be re-ingested as `pending` again — the UNIQUE constraint no longer protects a row that's been deleted. A cursor persisted independently of the rows it describes is immune to that. **Do not shortcut this to `max()`.**
- **No column added to `analyst_coverage`.** The existing `source` column carries the publisher; the staging row IS the email audit trail.

New `db.py` functions (all follow the `is_readonly()` / `has_db()` / try-except → empty-or-False contract of the existing analyst functions): `save_analyst_inbox_pending(record) -> bool` (insert; conflict-on-`resend_email_id` is a benign skip), `load_analyst_inbox_pending(status='pending') -> DataFrame`, `mark_analyst_inbox_status(id, status) -> bool`, `load_research_inbox_config() -> dict | None`, `save_research_inbox_config(last_created_at) -> bool`.

## 2. Resend API polling (replaces IMAP entirely)

Stateless REST polling against Resend's Received Emails API — no mailbox, no IMAP, no remote read/unread flag to manage. Because Resend inbound mail is immutable and nothing ever mutates remote state, correctness lives entirely in the DB's `resend_email_id` UNIQUE constraint; the poll cursor in `research_inbox_config` is a pure efficiency optimization, not a correctness mechanism.

**Poll algorithm** (`_run_research_inbox`):
1. Read `last_created_at` from `research_inbox_config` (`None` on first-ever run).
2. Fetch received emails **newest-first**, walking pages via the API's cursor pagination (`after`/`before`, `has_more`), stopping once `created_at <= last_created_at` (or a hard `ANALYST_INBOX_BATCH_MAX` page cap). Re-fetch **down to and including** `last_created_at` (a deliberate small overlap) so a same-timestamp tie or clock-boundary race can never skip an email — safe because the UNIQUE constraint makes any re-seen email a benign skip.
3. Process each email **oldest-first**: trusted-sender + auth check (§6) → normalize (§3) → `extract_report` → `save_analyst_inbox_pending`. An extraction failure saves a durable `status='error'` row and is NOT a lane failure.
4. **Advance the cursor to the max `created_at` processed this batch only if the whole batch saved without a hard DB write error.** On any hard DB save error, leave the cursor untouched and return 1 → the whole window is re-polled next time, with UNIQUE absorbing rows that already saved.

**Credentials & endpoint:** reuses the existing `RESEND_API_KEY` Railway secret (already used by `notify.py`'s outbound path). **Implementation note — do not hallucinate the endpoint:** `notify.py:19`'s outbound path is a raw `requests.post` against `https://api.resend.com/emails`; there is no `resend` SDK dependency in this repo today. Keep that pattern — the receiving poll should be a raw `requests.get` against Resend's Received-Emails REST endpoint (the REST path underlying the documented `resend.emails.receiving.list()`/`.get(email_id)`), added as a new module constant beside `_RESEND_ENDPOINT`. **The exact REST URL/params must be transcribed from Resend's live API reference at build time.** If the REST path turns out to be undocumented and only the SDK exposes it, adding the `resend` package is an acceptable fallback, but it becomes a dependency decision to call out explicitly in the build.

## 3. Normalization (simplified — MIME parsing eliminated)

Resend returns parsed `text` and `html` fields directly in its API response — **no MIME multipart walk needed**, no `email`-module parsing, no picking a `text/plain` part out of a tree.

- Prefer Resend's **`text`** field when present and non-blank.
- Else strip Resend's **`html`** with a small stdlib `html.parser.HTMLParser` subclass (drop `<script>/<style>`, emit newlines, collapse whitespace, conservatively trim unsubscribe footers). Still no new dependency.
- Cap at `ANALYST_EMAIL_MAX_CHARS` (module constant, ~40k); on truncation set `truncated=true` — **never silently drop content**.

**Forward-specific:** the actual newsletter is inlined as a "----- Forwarded Message -----" quoted block inside the body. The whole normalized body is handed to `extract_report` unchanged — the extractor is already robust to arbitrary prose (that's the paste flow's design). A separate pure `parse_forwarded_publisher(text) -> str | None` best-effort-scans that block's plain-text `From:`/`Subject:` lines for a publisher token, for the `source_label` DISPLAY field only.

## 4. New cron lane `research`

`cron_runner.py::_run_research_inbox(now_et, force) -> int`, modeled on `_run_broker` (`cron_runner.py:1812`):
- INERT return 0 if `RESEARCH_TRUSTED_SENDER` or `RESEND_API_KEY` is unset (normal not-configured state, same posture as no-`RESEND_API_KEY` elsewhere).
- Per-email isolation (one malformed/oversized email can't abort the batch). A single extraction failure → save staging row `status='error'` + `extract_error`, mark processed via the cursor logic in §2, lane still returns 0/ok.
- **Real failures return 1** → `main()` records heartbeat `failed` + dead-man email: the Resend poll itself failing (couldn't fetch — distinct from "no new mail") routes through `_notify_failure("research", detail)`; Supabase outage routes through the existing `_handle_db_unavailable("research", …)`.
- Wire into `main()`: add `"research"` to the `_mode_override` allowed tuple (`cron_runner.py:2096`) and the dispatch dict (`cron_runner.py:2148`); heartbeat recorded by `main()`'s normal path.
- `system_health._LANES` (`system_health.py:190`): add `_Lane("research", "Analyst research inbox poll", "daily")`. Add a `_LANE_OUTAGE_TEXT["research"]` entry (`cron_runner.py:~100`).
- **Railway:** new native Cron Job service `cron-research` with `ALERT_RUN_MODE=research`.
- **Cadence:** once-daily. The poll is idempotent and time-insensitive (no in-lane hour gate to straddle), so it does NOT need the two-slot UTC/DST handling that premarket/scan/intraday required — a single daily UTC cron is fine; accept ±1h seasonal drift. Leave `fire_hours_et=()`.

## 5. UI — reuse the existing preview cards

In the `📰 Stock Research` mode, above the paste textarea, add **"📨 From your research mailbox (N pending)"**, loading `status='pending'` rows via `db.load_analyst_inbox_pending()`. Each pending email shows sender · subject · received date · **the `sender_auth` verdict** ("⚠ sender authentication: unverified" when `'unknown'`/`'fail'`, so the human sees provenance before acting) · "N stocks extracted" (or "extraction failed — retry" / "no coverage found"). A **"Review"** button loads that email's `extracted` list into the **same** `_ac_preview` session state (plus `_ac_pending_id` + `_ac_pending_source`) and reruns → the *existing* editable cards render unchanged.

On **"Save selected to Inbox"**: the existing loop hardcodes `"source": "cnbc_pro"` at **`app.py:36107`** — this becomes `st.session_state.get("_ac_pending_source", "cnbc_pro")`, where `_ac_pending_source` is set from the reviewed email's `source_label` (falls back to `"cnbc_pro"` when null/paste-origin, since paste previews have no `_ac_pending_id`). Then `db.mark_analyst_inbox_status(_ac_pending_id, "reviewed")` so it drops off the pending list. **Discard** → `mark_analyst_inbox_status(id, "discarded")`. `_readonly` guard applies to Review/Save. Offline: loader returns empty on failure → section renders nothing, never crashes.

## 6. Trusted-sender + authentication (the security redesign)

The allowlist collapses from five vendor addresses to **one trusted sender: the user's own Yahoo address**, configured via `RESEARCH_TRUSTED_SENDER` env (empty = inert, matching the "not-yet-configured is a normal state" posture). This is forced by an observed fact, not a preference: a manual Yahoo "Forward" rewrites the MIME `From:` to the user's own address and inlines the original message as quoted plain text — it never attaches a parseable original, so a per-vendor address allowlist cannot work against this flow.

**Why "trust my own address" isn't just a spoofable string check.** A raw `From:` string is trivially forgeable by anyone who knows the user's Yahoo address (not secret) and the Resend receiving address (also not really secret). Two legs close this gap as far as it can be closed:

- **Leg 1 — exact match:** real MIME `From:` equals `RESEARCH_TRUSTED_SENDER` (via `email.utils.parseaddr`, case-insensitive). Necessary, not sufficient.
- **Leg 2 — inbound authentication verdict**, parsed from Resend's `headers` field. When the user hits "Forward" in Yahoo webmail, Yahoo composes a **new** message through Yahoo's own outbound infrastructure — it legitimately passes SPF for yahoo.com, carries a valid `d=yahoo.com` DKIM signature, and is DMARC-aligned. **Yahoo publishes a strict `p=reject` DMARC policy.** A spoofer forging `From: <yahoo addr>` from their own server cannot produce a valid `d=yahoo.com` DKIM signature and fails DMARC, which Yahoo's `p=reject` policy means a compliant receiver treats as untrusted. Keying Leg 2 on **DMARC=pass for the yahoo.com From-domain** (or DKIM-pass with an aligned `d=yahoo.com` signature, if that's what Resend surfaces instead of a rolled-up DMARC verdict) raises the bar from "knows two non-secret strings" to "has compromised the user's Yahoo account or Yahoo's outbound infra."

**Honest caveats (do not overstate this control):**
- Leg 2 only authenticates the *outer forward* — that it really left the user's Yahoo mailbox. It does **not** and cannot cryptographically confirm the original newsletter really came from Goldman/JPMorgan/etc., because Yahoo's inlining destroys the original's own DKIM signature. That's exactly why `source_label` is display-only and human-reviewed, never a security signal.
- **Leg 2 depends on Resend actually performing inbound DMARC/DKIM/SPF evaluation and exposing a parseable verdict in `headers`.** This is unconfirmed as of this design pass.

**§6a — Phase-0 build-time gate (must happen before Leg 2 is wired):** capture one real forwarded email's `headers` via the Resend API/dashboard and confirm a parseable `Authentication-Results` line (or equivalent SPF/DKIM/DMARC signal) actually exists.

**§6b — decided policy (user confirmed 2026-09-01):**
- **Unknown/unparseable auth verdict → ACCEPT into `pending` with a visible "sender authentication: unverified" flag on the review card** (fail-open-with-disclosure, not silent rejection). Never lose a legitimate forward; the human reviews before anything is saved regardless.
- **The Leg-2 auth check is NOT a hard Phase-1 blocker.** If Resend turns out not to expose a usable auth signal, ship Phase 1 with Leg-1 (sender-string match) only — but the review card must then honestly show sender auth as **"unverified"** for every email, never copy that implies validation happened when it didn't.

**The real backstop, regardless of Leg 2's outcome:** even a perfectly-spoofed email that passed every check would land in `pending`, get extracted, and appear as a preview card that the human must explicitly Review → Save before anything reaches `analyst_coverage` — which the scoring/gating engine never reads (F-154). This is already the codebase's standard "text of unknown provenance fed to an LLM" case (the paste textarea accepts literally anything). Human review before promotion + the awareness-only destination is what actually makes this feature safe; Leg 2 is defense-in-depth on top of that, not a substitute for it.

Where the logic lives: `sender_allowed(from_addr, trusted, extra_env=None)` and `sender_authenticated(headers, from_addr, trusted_domain) -> ('pass'|'fail'|'unknown', reason)` are **pure functions in `stock_analyzer/research_inbox.py`** (no Streamlit, no DB, no network), unit-tested against real captured header fixtures — this is the prompt-injection gate and needs a test, not just reasoning. `RESEARCH_ALLOWED_SENDERS` env union is retained for the same low-friction "add an address without a deploy" reason. Operational limits (`ANALYST_EMAIL_MAX_CHARS`, `ANALYST_INBOX_BATCH_MAX`) live in this module too, keeping `constants.py` untouched (matches CLAUDE.md's "prefer a NEW module over growing a `_GATE_FILES` one" — and there is no investment-policy threshold here at all, so Hard Rule #1's review clause does not trigger).

## 7. Phasing

- **Phase 0 — setup (no decision code):** ✅ Resend inbound receiving address created and tested working (`<id>.resend.app`, no custom domain/DNS needed) — confirmed 2026-09-01 with a real forwarded email from Yahoo. `RESEND_API_KEY` already exists as a Railway secret. Remaining: set `RESEARCH_TRUSTED_SENDER` = user's Yahoo address; apply the `analyst_inbox_pending` + `research_inbox_config` DDL; **do the §6a header-capture check**. *(The original Phase-0 blockers — auto-forward filter, capturing a vendor `From:` — no longer apply under the manual-forward model.)*
- **Phase 1 — one forward end-to-end + manual verification:** pure `research_inbox.py` (+tests); `db.py` staging + config functions (**reviewer**); `_run_research_inbox` + `main()` wiring (**reviewer**); `system_health._LANES` entry (**reviewer**); `cron-research` service; UI pending-list + Review wiring. Verify: forward a real newsletter → pending → Review → cards → Save → lands in `analyst_coverage` identically to a paste.
- **Phase 2 — UX:** the "add 4 more vendor addresses" step from the original plan is gone (single-sender model) — replaced by refining the best-effort publisher parser as real samples arrive across the 5 newsletters. In-app "📨 Check mailbox now" button (calls the Resend list API from the app process on demand — no new service).
- **Phase 3 — observability / dedup / cadence hardening:** bump poll cadence (schedule-only); content-hash secondary dedup; a Data-Health / System-Trust readout ("N pending, M ingested this week, last poll at"); retention cleanup of old reviewed/discarded staging rows — **now safe because the cursor lives in `research_inbox_config`, independent of the rows it describes** (see §1/§2).

## 8. Doc-sync (Definition-of-Done)

1. Constants: none in `constants.py` (operational + security-policy limits live in `research_inbox.py`) → nothing for the constants table. New env vars (`RESEARCH_TRUSTED_SENDER`, `RESEARCH_ALLOWED_SENDERS`) → document in `DEVELOPMENT.md` secrets architecture; note `RESEND_API_KEY` is reused, not new.
2. New user-facing surface (pending-list + email lane) → new **F-154d** (or extend F-154) in `docs/requirements.md`.
3. On ship → add to `docs/shipped-log.md`; add this feature to CLAUDE.md "What's queued" with Phase 2/3 gates.
4. In-app User Guide (Ideas Inbox section) → mention the mailbox path and the "unverified sender" disclosure.
5. Memory file `project_analyst_email_ingestion.md` (Resend-vs-Gmail decision, forward-vs-original-sender finding, the auth-leg design + the two 2026-09-01 policy decisions).
6. Phase gates → CLAUDE.md "What's queued".
7. This plan's **Status:** line bumped each phase.
   Plus `docs/architecture.md`: new `research_inbox.py` module section, both new DDLs (`analyst_inbox_pending` + `research_inbox_config`), the new cron-lane row; and update the lane/System-Trust counts in `docs/user-manual.md`.

## 9. Review / governance

- **Mandatory Opus `reviewer` pass** (`_GATE_FILES`), cited in the enforced commit format: `cron_runner.py` (new lane), `db.py` (two DDLs + staging/config functions), `system_health.py` (new `_Lane`). **Bundle `research_inbox.py` into the same review** — it is the security-critical trusted-sender/auth-verdict decision surface, a stronger reason than the original plan's "even though a pure module isn't gate-mandatory."
- **`constants.py` is NOT touched** → no investment-policy review clause triggered.
- **Confirmed awareness-only:** does not touch scoring, gating, or any threshold; `extract_report` output → `analyst_coverage`, which the rule engine never reads (F-154). No composite/gate/formula change anywhere.
- `feat(` commits carry `Design = planner (Opus 4.8)` / `Build = implementer (…)` trailers.

## Decision-bearing vs. mechanical (routing)

- **Decision-bearing (keep on lead / needs review):** the two DDLs + `db.py` writers; the poll-cursor advance-only-on-clean-batch semantics; the trusted-sender + auth-verdict match rule (Leg 1 + Leg 2 + the unknown-case policy). → mandatory reviewer.
- **Mechanical (safe to delegate to `implementer`):** the pure normalization/HTML-strip/publisher-parse in `research_inbox.py`; the UI pending-list wiring + the `app.py:36107` one-liner; the `main()` mode-dispatch entry; doc-sync.

## Tests the build must include

- **Idempotency:** the same `resend_email_id` processed twice inserts exactly ONE staging row (UNIQUE); a hard DB-save error mid-batch leaves `last_created_at` un-advanced so the window is re-polled, and re-polling an already-saved email is a benign UNIQUE skip (exactly-once effect).
- **Cursor / retention safety:** deleting old reviewed rows does NOT cause re-ingestion, because the cursor lives in `research_inbox_config`, not `max(received_at)` on the staging table.
- **Trusted-sender + auth boundary (the prompt-injection gate — needs a test, not reasoning):** `From ≠ RESEARCH_TRUSTED_SENDER` → REJECT before any text reaches `extract_report`; `From = trusted` but auth shows a DMARC/DKIM fail → REJECT; `From = trusted` + auth pass → accept and map `source_label`; a look-alike/near-miss address → REJECT.
- **Auth parsing:** an `Authentication-Results`-style header showing a clean pass → `'pass'`; a clean fail → `'fail'`; header absent/unparseable → `'unknown'` (and the accept-with-flag behavior on `'unknown'` matches the decided §6b policy). Fixtures must be real captured headers, not invented strings.
- **Normalization:** Resend `text` preferred when present; html-only body strips to readable text; oversize body sets `truncated=true` and never silently drops content.
- **Publisher label (non-security):** a forwarded-block body yields the right token best-effort; an unparseable body yields null/`'unknown'` and **never blocks ingest**.
- **Offline contract:** `load_analyst_inbox_pending()` returns empty (not raise) on missing table / DB failure; the lane returns 1 → heartbeat `failed` when the Resend poll itself fails (couldn't poll), but 0 → `ok` on a clean empty poll (distinguish "no new mail" from "couldn't poll").
- **Promotion equivalence:** a Reviewed email's Save produces an `analyst_coverage` row byte-equivalent to the paste flow's, and flips the staging row to `reviewed`.

## Risks / coordination

- **Existing surface owned:** `analyst_coverage` (F-154) already owns the "approved analyst fact" dimension — the staging table exists precisely so email ingestion never becomes a silent second writer into it. No dedup-by-ticker overlap with any other advisor; this is awareness-only and touches no gate.
- **The one real weakening vs. the original design:** a single spoofable trusted-sender string, forced by how Yahoo's manual forward actually behaves (proven by live test, not assumed). Guarded by the DMARC/DKIM auth leg where available, plus the two structural backstops that were already true of the original design: human review before promotion, and an awareness-only destination the scoring engine never reads. **Do not ship UI copy that implies a sender was cryptographically validated when only Leg 1 (string match) ran.**
- **Resend's inbound REST endpoint is not yet in this repo** (only the outbound `/emails` POST exists today). The exact receiving URL/params must be transcribed from Resend's live docs at build time — a hallucinated URL is a silent failure mode, not a loud one.
- **Calm posture:** nothing here issues an "Act Today"; pending items are passive awareness in the Ideas Inbox, surfaced only when the user opens it.
- **Offline/dead-man:** the lane must fail loudly (heartbeat `failed` + email) on a Resend-poll or DB failure, and stay green on an empty poll — the standard producer-failure-is-visible contract.
