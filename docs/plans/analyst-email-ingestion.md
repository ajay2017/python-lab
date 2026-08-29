# Analyst Research Email Ingestion — Plan

**Status: PARKED 2026-08-29 — design complete, verdict PROCEED, no code written.** Blocked on user-owned physical setup an agent cannot do: (1) create the dedicated mailbox, (2) set up an **auto-forward filter** (not a manual forward — see §2/risks), (3) capture one real sender's `From:` address to seed the allowlist. Resume Phase 1 build once those are ready — see memory `project_analyst_email_ingestion`.

**Goal:** Let the user *forward analyst newsletters* (CNBC Pro, Goldman, JPMorgan, BofA, Morgan Stanley) to a dedicated mailbox and have the app pick them up automatically, run the **existing** `analyst_intel.extract_report()`, and land the result in a **pending** state that surfaces through the **existing** editable preview-card UI. The manual paste textarea stays as-is — this is an additive second input path. **Awareness-only end to end:** promoted rows go to `analyst_coverage`, which per F-154 is NEVER read by the rule-based scoring/gating engine.

**Why this shape:** the paste flow already has the two safety properties we must preserve — the LLM only extracts atomic per-firm facts (aggregates are pure-Python `derive_consensus()`), and *nothing is written to `analyst_coverage` without human review*. Email ingestion changes only *how raw text arrives*, not extraction or the approval gate.

---

## 1. Data model — NEW staging table `analyst_inbox_pending` (recommended over columns on `analyst_coverage`)

`analyst_coverage` today is a **clean, approved-facts table**: every row is a human-reviewed fact, read by the Research Scorecard grading (F-154c), watchlist candidates, and the earnings playbook. Injecting unapproved `status='pending'` rows into it would force *every* consumer to add a `review_status='approved'` filter — miss one and un-reviewed LLM output leaks into a decision-adjacent surface. That is a cross-cutting silent-corruption risk. A staging table keeps the promotion boundary explicit and leaves the clean table + all its consumers **untouched** (no DDL on `analyst_coverage`, so its review blast-radius is unchanged).

Grain = **one row per email** (the extractor already returns a `list[dict]` of per-stock records for one article, matching the preview's `_ac_preview` list).

```
create table if not exists analyst_inbox_pending (
    id            bigint primary key generated always as identity,
    message_id    text unique,          -- RFC-5322 Message-ID of the mailbox item → dedup/idempotency key
    email_uid     text,                 -- IMAP UID (operational; message_id is the durable dedup)
    sender        text,                 -- From addr that passed the allowlist
    source_label  text,                 -- mapped publisher token: cnbc_pro|goldman|jpmorgan|bofa|morgan_stanley
    subject       text,
    received_at   timestamptz,          -- email Date header
    raw_text      text,                 -- normalized plaintext handed to the LLM (audit + re-extract)
    extracted     jsonb,                -- list[dict] from extract_report; null if extraction failed
    extract_error text,                 -- LAST_EXTRACT_ERROR when extraction returned None
    status        text default 'pending', -- pending|reviewed|error|empty|discarded
    truncated     boolean default false,  -- true if raw_text hit the char cap (shown to human)
    created_at    timestamptz default now()
);
alter table analyst_inbox_pending enable row level security;
create policy "service_role_all_analyst_inbox_pending" on analyst_inbox_pending
    for all to service_role using (true) with check (true);
```

- **Dedup / idempotency:** `message_id` UNIQUE. A re-poll insert conflicts and is skipped — a DB-level guarantee independent of IMAP flags. (A content-hash secondary key is deferred; the preview is the human safety net against the same article forwarded twice.)
- **Ships INERT** until the DDL is applied (mirrors `analyst_coverage`'s own comment): `db.load_analyst_inbox_pending()` returns an empty DataFrame on missing table / DB failure, the cron lane no-ops, the UI section shows nothing.
- **No column added to `analyst_coverage`.** The existing `source` column carries the publisher; the staging row IS the email audit trail. An optional `ingest_channel` on the clean table is a trivial backward-compatible add if ever wanted — deferred.

New `db.py` functions (all follow the `is_readonly()` / `has_db()` / try-except → empty-or-False contract of the existing analyst functions):
`save_analyst_inbox_pending(record) -> bool` (insert; conflict-on-message_id is a benign skip), `load_analyst_inbox_pending(status='pending') -> DataFrame`, `mark_analyst_inbox_status(id, status) -> bool`.

## 2. Mailbox & credentials

Dedicated Gmail/Workspace mailbox, 2FA + app password, IMAP SSL (`imap.gmail.com:993`). Railway → Variables (the only secret store): `RESEARCH_IMAP_HOST`, `RESEARCH_IMAP_PORT`, `RESEARCH_IMAP_USER`, `RESEARCH_IMAP_PASSWORD`. Reuse existing `ANTHROPIC_API_KEY` (extraction) and `RESEND_API_KEY`/`ALERT_EMAIL_TO` (failure alerts). Stdlib `imaplib` + `email` — **no new dependency**.

**Idempotency = two layers.** (1) IMAP: `SEARCH UNSEEN`, process, then `STORE +FLAGS \Seen` — but *only after the staging row is durably saved*, so a crash mid-batch re-processes next poll (at-least-once + `message_id` UNIQUE = exactly-once effect). (2) DB: the UNIQUE `message_id`. Never delete mail (keep for audit/re-extract). Non-allowlisted senders → mark `\Seen` + log the rejected address (so the user can add it and re-forward) — never processed, never accumulating.

## 3. HTML normalization — single generic HTML→text pass (NOT per-source parsers)

`extract_report()` is already built to read arbitrarily-structured pasted prose; it needs *readable text*, not clean per-field HTML. Per-source parsers are brittle (newsletters reformat/A-B-test) and can silently mis-scope and truncate — a data-integrity risk. Generic degrades gracefully.

Order: prefer the email's **`text/plain`** MIME part when present (cleanest, zero parsing); else strip `text/html` with a small stdlib `html.parser.HTMLParser` subclass (drop `<script>/<style>`, emit text with newlines, collapse whitespace, conservatively trim unsubscribe footers). No new dependency. Cap input at `ANALYST_EMAIL_MAX_CHARS` (module constant, ~40k) and, if it truncates, set `truncated=true` so the human sees it — **never silently truncate**.

## 4. New cron lane `research`

`cron_runner.py::_run_research_inbox(now_et, force) -> int`, modeled on `_run_maintenance`/`_run_broker`:
- INERT return 0 if IMAP env vars unset (normal not-configured state, same posture as no-`RESEND_API_KEY`).
- Per-message isolation (one malformed/oversized email can't abort the batch — matches maintenance's "a chore, not a lane failure"). A single extraction failure → save staging row `status='error'` + `extract_error`, mark seen, lane still returns 0/ok (don't train the user to ignore a red heartbeat).
- **Real failures return 1** → `main()` records heartbeat `failed` + dead-man email: IMAP login/fetch failure (`_notify_failure("research", detail)`, `_LAST_LANE_FAILURE_DETAIL` set); Supabase outage routes through the existing `_handle_db_unavailable("research", …)`.
- Wire into `main()`: add `"research"` to the `_mode_override` allowed tuple and to the dispatch dict; heartbeat is recorded by `main()`'s normal path.
- `system_health._LANES`: add `_Lane("research", "Analyst research inbox poll", "daily")`. Add a `_LANE_OUTAGE_TEXT["research"]` entry.
- **Railway:** new native Cron Job service `cron-research` with `ALERT_RUN_MODE=research`.
- **Cadence:** Phase 1 once-daily. Because the poll is **idempotent and time-insensitive** (no in-lane hour gate to straddle), it does NOT need the two-slot UTC/DST handling that bit premarket/scan/intraday — a single daily UTC cron is fine; accept the ±1h seasonal drift. Leave `fire_hours_et=()` (age-window grading suffices) or set one conservative hour for dead-man tightening — minor choice.

## 5. UI — reuse the existing preview cards

In the `📰 Stock Research` mode (`app.py` ~34756), above the paste textarea, add **"📨 From your research mailbox (N pending)"**, loading `status='pending'` rows via `db.load_analyst_inbox_pending()`. Each pending email shows sender · subject · received date · "N stocks extracted" (or "extraction failed — retry" / "no coverage found"). A **"Review"** button loads that email's `extracted` list into the **same** `_ac_preview` session state (plus `_ac_pending_id` + `_ac_pending_source`) and reruns → the *existing* editable cards render unchanged.

On **"Save selected to Inbox"**: the existing loop runs `save_analyst_coverage()` per selected stock, with `"source"` sourced from `st.session_state.get("_ac_pending_source", "cnbc_pro")` instead of the current hardcoded literal (one-line change), then `db.mark_analyst_inbox_status(_ac_pending_id, "reviewed")` so it drops off the pending list. **Discard** → `mark_analyst_inbox_status(id, "discarded")`. Paste-origin previews have no `_ac_pending_id` → behave exactly as today. `_readonly` guard applies to Review/Save. Offline: loader returns empty on failure → section renders nothing, never crashes.

## 6. Sender allowlist — pure module, NOT `constants.py`

The allowlist is a **security control** (prompt-injection defense), not a decision threshold, so Hard Rule #1 does not put it in `constants.py`; it belongs in a version-controlled, unit-tested pure module `stock_analyzer/research_inbox.py`. One structure serves double duty: `_SENDER_SOURCES: dict[address-or-domain -> source_label]` — keys are the allowlist, values are the publisher token. `sender_allowed(from_addr, extra_env=None)` matches the baseline set **UNION** an optional `RESEARCH_ALLOWED_SENDERS` env addition (low-friction way to add a newly-discovered address without a code deploy, while the security-critical baseline stays auditable in git). Match on **exact address or a specific vendor subdomain**, case-insensitive, via `email.utils.parseaddr` — **never a bare shared-ESP top-level domain** (a hole). Operational limits (`ANALYST_EMAIL_MAX_CHARS`, `ANALYST_INBOX_BATCH_MAX`) live here too, keeping `constants.py` untouched (matches CLAUDE.md's "prefer a NEW module over growing a `_GATE_FILES` one").

`research_inbox.py` is **pure** (MIME parse, normalize, `sender_allowed`, `source_for_sender`, message→staging-record mapper) — no Streamlit, no DB, no network — so its allowlist and normalization decisions are unit-testable, unlike `app.py`.

## 7. Phasing

- **Phase 0 — setup (no decision code):** dedicated mailbox + auto-forward filter for ONE source; capture that source's real `From:`; set Railway IMAP env vars; apply the `analyst_inbox_pending` DDL.
- **Phase 1 — one source end-to-end + manual verification:** pure `research_inbox.py` (+tests); `db.py` staging functions (**reviewer**); `_run_research_inbox` + `main()` wiring (**reviewer**); `system_health._LANES` entry (**reviewer**); `cron-research` service; UI pending-list + Review wiring. Verify a forwarded CNBC article: pending → Review → cards → Save → lands in `analyst_coverage` identically to a paste.
- **Phase 2 — remaining sources + UX:** add the other 4 verified senders to `_SENDER_SOURCES`; in-app "📨 Check mailbox now" button (runs IMAP from the app process on demand — no new service); optional manual-forward support (parse original sender from body + user's own address allowlisted).
- **Phase 3 — observability / dedup / cadence hardening:** bump poll cadence (schedule-only); content-hash secondary dedup; a Data-Health / System-Trust readout ("N pending, M ingested this week, last poll at"); retention cleanup of old reviewed/discarded staging rows.

## 8. Doc-sync (Definition-of-Done)

1. Constants: none in `constants.py` (operational limits live in the module) → nothing for the constants table. New IMAP env vars → document in `DEVELOPMENT.md` secrets architecture.
2. New user-facing surface (pending-list + email lane) → new **F-154d** (or extend F-154) in `docs/requirements.md`.
3. On ship → add to `docs/shipped-log.md`; add this feature to CLAUDE.md "What's queued" with Phase 2/3 gates.
4. In-app User Guide (Ideas Inbox section) → mention the mailbox path.
5. Memory file `project_analyst_email_ingestion.md` (staging-vs-columns rationale, forward-vs-original-sender decision).
6. Phase gates → CLAUDE.md "What's queued".
7. This plan's **Status:** line bumped each phase.
   Plus `docs/architecture.md`: new `research_inbox.py` module section, the `analyst_inbox_pending` DDL, the new cron-lane row; and update the lane/System-Trust counts in `docs/user-manual.md` (CLAUDE.md notes these counts drift).

## 9. Review / governance

- **Mandatory Opus `reviewer` pass** (`_GATE_FILES`), cited in the enforced commit format: `cron_runner.py` (new lane), `db.py` (DDL + staging functions), `system_health.py` (new `_Lane`). Recommend bundling the security-critical `research_inbox.py` allowlist/normalization into the same review even though a pure module isn't gate-mandatory.
- **`constants.py` is NOT touched** in Phase 1 (operational limits kept in the module) → no investment-policy review clause triggered.
- **Confirmed awareness-only:** does not touch scoring, gating, or any threshold; `extract_report` output → `analyst_coverage`, which the rule engine never reads (F-154). No composite/gate/formula change anywhere.
- `feat(` commits carry `Design = planner (Opus 4.8)` / `Build = implementer (…)` trailers.

## Decision-bearing vs. mechanical (routing)

- **Decision-bearing (keep on lead / needs review):** staging-table schema + the `db.py` writers; the cron lane failure/idempotency semantics; the sender-allowlist match rule. → these carry the mandatory reviewer pass.
- **Mechanical (safe to delegate to `implementer`):** the pure `research_inbox.py` normalization/mapper, the UI pending-list wiring, the `main()` mode dispatch entry, doc-sync.

## Tests the build must include

- **Idempotency invariant:** the same message processed twice inserts ONE staging row (`message_id` UNIQUE) and marks `\Seen` only after a confirmed save — a test that a save-failure leaves the message UNSEEN for retry.
- **Allowlist boundary:** a non-allowlisted sender (and a look-alike sub-domain / bare-ESP domain) is REJECTED before any text reaches `extract_report`; an allowlisted address maps to the correct `source_label`. This is the prompt-injection gate — the invariant needs a test, not just reasoning.
- **Normalization:** `text/plain` preferred when present; HTML-only body strips tags to readable text; oversize body sets `truncated=true` and never silently drops content.
- **Offline contract:** `load_analyst_inbox_pending()` returns empty (not raise) on missing table / DB failure; the lane returns 1 → heartbeat `failed` on IMAP failure, but returns 0 → `ok` on a clean empty poll (distinguish "no new mail" from "couldn't poll").
- **Promotion equivalence:** a Reviewed email's Save produces an `analyst_coverage` row byte-equivalent to the paste flow's, and flips the staging row to `reviewed`.

## Risks / coordination

- **Existing surface owned:** `analyst_coverage` (F-154) already owns the "approved analyst fact" dimension — the staging table exists precisely so email ingestion never becomes a silent second writer into it. No dedup-by-ticker overlap with any other advisor; this is awareness-only and touches no gate.
- **Forward-sender security hole** (the primary risk): a manual forward hides the vendor `From:` — Phase 1 requires auto-forward to keep the allowlist meaningful.
- **Calm posture:** nothing here issues an "Act Today"; pending items are passive awareness in the Ideas Inbox, surfaced only when the user opens it.
- **Offline/dead-man:** the lane must fail loudly (heartbeat `failed` + email) on IMAP/DB failure, and stay green on an empty poll — the standard producer-failure-is-visible contract.
