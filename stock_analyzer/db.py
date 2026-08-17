"""
Persistence layer — reads/writes holdings, watchlist, and trades to Supabase.

SECURITY MODEL (single-user, server-side Streamlit Cloud):
    - Streamlit secret `[supabase] key` MUST be the secret/service-role key
      (sb_secret_* in the new key format, or the legacy service_role JWT),
      NOT the publishable/anon key. The secret key bypasses RLS and stays
      server-side only (Streamlit secrets are never sent to the browser).
    - RLS is ENABLED on all three tables in the public schema with a single
      policy per table that grants ALL operations to the service_role.
      Anon/authenticated roles have no matching policy, so a leaked
      publishable key cannot access data — defense in depth.

One-time SQL to set up RLS (run in Supabase SQL Editor):

    ALTER TABLE public.holdings        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.watchlist       ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.trades          ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.recommendations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.manual_stops    ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.fundamentals_cache ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.sector_cache       ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Allow all (service role)" ON public.holdings;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.watchlist;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.trades;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.recommendations;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.manual_stops;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.fundamentals_cache;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.sector_cache;

    CREATE POLICY "Allow all (service role)" ON public.holdings
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.watchlist
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.trades
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.recommendations
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.manual_stops
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.fundamentals_cache
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.sector_cache
        FOR ALL TO service_role USING (true) WITH CHECK (true);

Table schema (run once if tables don't exist):

    create table if not exists holdings (
        id         bigint primary key generated always as identity,
        ticker     text    not null unique,
        shares     numeric not null check (shares > 0),
        avg_cost   numeric not null check (avg_cost > 0),
        updated_at timestamptz default now()
    );

If the holdings table predates the unique constraint, run this once to
add it (required for save_holdings' upsert path — without the constraint
the atomic upsert + sweep pattern degrades to delete-then-insert):

    ALTER TABLE public.holdings
        ADD CONSTRAINT holdings_ticker_unique UNIQUE (ticker);

    create table if not exists watchlist (
        id       bigint primary key generated always as identity,
        ticker   text not null unique,
        added_at timestamptz default now()
    );

    create table if not exists trades (
        id               bigint primary key generated always as identity,
        ticker           text    not null,
        action           text    not null,
        shares           numeric not null check (shares > 0),
        price            numeric not null check (price > 0),
        cost_basis       numeric,
        realized_pnl     numeric,
        notes            text,
        trigger_type     text default 'MANUAL',
        signal_seen      text,
        followed_signal  text,
        deviation_reason text,
        lesson           text,
        lesson_category  text,
        traded_at        timestamptz default now()
    );

If trades table already exists, run this once to add the decision-journal columns:

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_seen      text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS followed_signal  text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS deviation_reason text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS lesson           text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS lesson_category  text;

Decision-context capture (Concept E, Phase 1 — added 2026-07-17). A frozen,
schema-versioned snapshot of the state of the world at each interactive Buy/Sell
write (composite verdict seen, macro regime, portfolio beta/concentration,
active-recommendation load). Optional: until the column exists, save_trade drops
it and retries, so trade logging runs exactly as before (the snapshot ships
inert until DDL is applied). Never written by broker/screenshot/split imports or
the recalculate_from_trades replay — retroactive/batch writes carry no live
decision context.

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS decision_context jsonb;

Pre-Mortem Protocol (Concept C, Phase 1 — added 2026-07-17). Before a
prospective LIVE Buy writes, the investor is shown an app-generated case
against the trade (composite pillar concern, portfolio-concentration impact,
macro/earnings context) and must write a falsifiable pre-commitment before the
trade is recorded. premortem_case_against stores the 3 generated
counterarguments (jsonb; None if the LLM call failed — the pre-commitment
field, not the LLM output, is the hard requirement). premortem_commitment
stores the investor's own required text. Optional: until these columns exist,
save_trade drops them and retries, so trade logging runs exactly as before
(inert until DDL is applied). Never written by broker/screenshot/split imports,
the recalculate_from_trades replay, or any SELL — scoped to prospective LIVE
Buy decisions only (exit friction is bad; entry friction is the point).

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS premortem_case_against jsonb;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS premortem_commitment   text;

Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md — added
2026-08-03). Extracts a structured, checkable price trigger from the free-text
premortem_commitment above (LLM, ONE-SHOT at BUY-submission time —
stock_analyzer/premortem_monitor.py::extract_trigger()) so a daily,
zero-LLM-cost check (detect_premortem_triggers()) can confront the investor
with a dedicated Act Today card when the stated condition has actually fired
and they're still holding. premortem_trigger_direction is 'below', 'above', or
'not_checkable' (the commitment stated no explicit numeric price — a
qualitative condition like "if guidance disappoints" can never be
mechanically checked, so this is a genuine, permanent state, not "not yet
attempted"; distinguished from the pre-extraction state where BOTH columns
are NULL). premortem_trigger_price is the RAW (pre-split) price the investor
stated — detect_premortem_triggers() divides it by the ticker's cumulative
split ratio since the governing lot's buy_date (tax_advisor._build_open_lots)
before comparing to current price history. Optional: until these columns
exist, save_trade drops them and retries (inert until DDL is applied). Same
scope as premortem_commitment — LIVE Buy only, never broker/screenshot/split
imports, recalculate_from_trades, or any SELL. Extraction is one-shot and
never retried (the trades grid is delete-only — app.py:21225-21248 — so a
failed extraction stays unmonitored for that trade, same fail-open posture as
every other best-effort AI surface in this app).

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS premortem_trigger_price     numeric;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS premortem_trigger_direction text;

Idempotency key (2026-08-04 audit finding — added same day). A retried/
double-submitted interactive save (e.g. an impatient double-click on
"Confirm" past the session-state dedup guard) could create a duplicate
trade row with no DB-level backstop, unlike recommendations/daily_snapshots
which use real unique-constraint upserts. idempotency_key is a UUID
generated ONCE at record-staging time (app.py, when the review card is
built) and reused verbatim if the same staged record is submitted twice —
a UNIQUE index rejects the second insert as a no-op instead of creating a
second row. Deliberately NOT a blanket unique constraint on business
columns (ticker/action/shares/price/traded_at): broker_import.py's
classify_against_existing already correctly allows multiple identical-
content rows for legitimate same-day multi-fills via its own
existing_count allowance, and a table-wide constraint would break that.
Broker/screenshot/split-import writes never set this column — Postgres
unique indexes don't enforce uniqueness across NULLs, so those paths are
unaffected. Optional: until the column/index exist, save_trade drops the
key and retries, so trade logging runs exactly as before (the backstop
ships inert until DDL is applied).

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS idempotency_key text;
    CREATE UNIQUE INDEX IF NOT EXISTS trades_idempotency_key_unique
        ON trades (idempotency_key) NULLS DISTINCT;
    -- NULLS DISTINCT is Postgres's default (multiple NULLs never collide) —
    -- stated explicitly so a future Postgres default change or a copy-paste
    -- into a NULLS NOT DISTINCT context can't silently break the
    -- broker/screenshot/split-import exemption this design depends on.

Recommendations log (added 2026-05-26 — first-seen capture of every pick
surfaced by Today's Brief so you can audit the App's recommendation
history over time):

    create table if not exists recommendations (
        id               bigint primary key generated always as identity,
        ticker           text not null,
        rec_date         date not null,
        rec_type         text not null,          -- 'new_pick' | 'add_winner' | 'buy_candidate'
        surfaced_at      timestamptz default now(),
        price_at_surface numeric,                -- price snapshot at first-surface (for would-have-gained math)
        composite_score  numeric,
        momentum_score   numeric,
        sector           text,
        conviction       text,                   -- 'high' | 'moderate' | 'unverified' | NULL
        verdict          text,                   -- 'confirmed' | 'mixed' | 'caution' | 'unverified' | NULL
        thesis           text,
        constraint recommendations_unique_per_day
            unique (ticker, rec_date, rec_type)
    );

    create index if not exists recommendations_rec_date_idx
        on public.recommendations (rec_date desc);

If recommendations table already exists (created before 2026-05-26), run
this once to add the price_at_surface column for the History page:

    alter table public.recommendations
        add column if not exists price_at_surface numeric;

Pillar-score capture (added 2026-08-01, feeds Portfolio Q&A's rec-outcome
"why" answers — see docs/plans/portfolio-qa.md). Before this, only the bare
composite_score was persisted; the 4-pillar breakdown that explains WHY a
score landed where it did was computed in memory at brief-build time
(app.py's t_score/bq_score/val_score/s_score) and discarded. Optional: until
these columns exist, save_recommendations drops them and retries, so the
recommendation log keeps working exactly as before (inert until DDL is
applied). Forward-only — existing rows stay NULL, nothing is backfilled.

    ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS t_score  numeric;
    ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS bq_score numeric;
    ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS val_score numeric;

Manual stops (added 2026-05-29 — user-set stop overrides recorded when
the Brief's "raise stop" recommendation is actioned. Without this the
recommendation re-fires every render because the system has no record
the user acted on it):

    create table if not exists manual_stops (
        id            bigint primary key generated always as identity,
        ticker        text not null unique,
        stop_price    numeric not null check (stop_price > 0),
        set_at        timestamptz default now(),
        note          text,
        source_action text                            -- e.g. 'review_tighten_only' / 'review_trim_and_tighten' / 'manual'
    );

    -- Last-known-good fundamentals fallback (load_all serves this when the live
    -- .info leg is sparse and FMP can't backfill, bounded by
    -- FUNDAMENTALS_CACHE_MAX_AGE_DAYS). Optional: until created, the app runs
    -- live-only exactly as before (load/save degrade to no-ops).
    create table if not exists fundamentals_cache (
        ticker      text primary key,
        financials  jsonb not null,
        fetched_at  timestamptz not null default now()
    );

    -- Last-known sector fallback (bundle_loader write-throughs a live .info
    -- sector and reads this when .info comes back sparse). Sector is near-static
    -- so a cached value never goes stale. Optional: until created, load/save
    -- no-op and an unmapped holding on a thin-.info day falls to "Other" as before.
    create table if not exists sector_cache (
        ticker     text primary key,
        sector     text not null,
        updated_at timestamptz not null default now()
    );

    -- Daily snapshots — Tier B day-over-day P&L baseline (added 2026-06-09).
    -- Prior-close snapshot of held positions; the positions day-P&L =
    --   current marked value − this baseline value + today's trade cash.
    -- Optional: until created, load_recent_snapshots returns empty and
    -- save_daily_snapshot no-ops, so the app runs exactly as before (the
    -- held-only "Today's P&L (held)" mark). Written once per trading day when
    -- the market is CLOSED, so close_price is the final settled close.
    create table if not exists public.daily_snapshots (
        snapshot_date date    not null,
        ticker        text    not null,
        shares        numeric not null check (shares > 0),
        close_price   numeric not null check (close_price > 0),
        created_at    timestamptz default now(),
        primary key (snapshot_date, ticker)
    );
    alter table public.daily_snapshots enable row level security;
    drop policy if exists "Allow all (service role)" on public.daily_snapshots;
    create policy "Allow all (service role)" on public.daily_snapshots
        for all to service_role using (true) with check (true);

    -- Last-known-good bundle cache — data-resilience fallback (added 2026-06-10).
    -- load_all write-throughs the raw history + info here on every successful
    -- fetch; when the history/bundle providers (Yahoo→FMP) ALL fail it serves
    -- this aged copy (with a staleness banner) instead of "Could not load".
    -- Optional: until created, load returns None / save no-ops, so the app keeps
    -- today's fail-loud behaviour. System cache (not user data) — but gated by
    -- the read-only guard anyway since it's a write.
    create table if not exists public.bundle_cache (
        ticker       text primary key,
        history_json text not null,
        info         jsonb,
        fetched_at   timestamptz not null default now()
    );
    alter table public.bundle_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.bundle_cache;
    create policy "Allow all (service role)" on public.bundle_cache
        for all to service_role using (true) with check (true);

    -- scanner_cache: single-row (id=1) snapshot of the LATEST sector scan, so the
    -- Home buy-candidate / Grow-Today new-pick lists populate on a COLD load
    -- WITHOUT the user running the ~20s scanner. The GitHub Actions cron writes it
    -- post-open each trading day (mode=scan); a manual full scan refreshes it too.
    -- The whole scan DataFrame is stored as JSON so Home reconstructs
    -- scanner_results EXACTLY (every column preserved). Until created, load returns
    -- None and the app behaves exactly as today (candidates empty until a manual
    -- scan). System cache (not user data) — gated by the read-only guard anyway.
    create table if not exists public.scanner_cache (
        id           integer primary key,        -- always 1 (single user)
        results_json text not null,
        scan_date    date,
        source       text,                        -- 'cron' | 'app'
        scanned_at   timestamptz not null default now()
    );
    alter table public.scanner_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.scanner_cache;
    create policy "Allow all (service role)" on public.scanner_cache
        for all to service_role using (true) with check (true);

    -- alert_state: single-row (id=1) dedup state for the protective-alert cron
    -- (exit-discipline Phase 3). Until created, load returns None / save no-ops,
    -- so the cron degrades to "always send" (no double-send guard) but still works.
    create table if not exists public.alert_state (
        id                 integer primary key,
        last_emailed_date  text,
        last_fingerprint   text,
        updated_at         timestamptz not null default now()
    );
    alter table public.alert_state enable row level security;
    drop policy if exists "Allow all (service role)" on public.alert_state;
    create policy "Allow all (service role)" on public.alert_state
        for all to service_role using (true) with check (true);

    -- account_cash: single-row (id=1) NET cash balance for account-level views
    -- (account-baseline v1; v4 allows NEGATIVE = a margin debit, so the column is
    -- signed numeric). Until created, load returns None and the app behaves exactly
    -- as today (equity-only, with a nudge to set cash). The same table the
    -- Robinhood MCP sync would later auto-populate.
    create table if not exists public.account_cash (
        id            integer primary key,
        cash_balance  numeric not null default 0,
        note          text,
        updated_at    timestamptz not null default now()
    );
    alter table public.account_cash enable row level security;
    drop policy if exists "Allow all (service role)" on public.account_cash;
    create policy "Allow all (service role)" on public.account_cash
        for all to service_role using (true) with check (true);

    -- account_flows: external cash-flow ledger for growth-vs-contributions
    -- (account-baseline v2). Rows: 'baseline' (opening contributed-capital
    -- anchor), 'deposit' (+), 'withdrawal' (-). amount is always POSITIVE; the
    -- type carries the sign. Until created, load returns [] and the growth view
    -- stays hidden (cash + total-value v1 still works). The same ledger a broker
    -- sync would later auto-populate from transfer history.
    create table if not exists public.account_flows (
        id          bigint generated always as identity primary key,
        flow_date   date not null,
        flow_type   text not null,
        amount      numeric not null,
        note        text,
        created_at  timestamptz not null default now()
    );
    alter table public.account_flows enable row level security;
    drop policy if exists "Allow all (service role)" on public.account_flows;
    create policy "Allow all (service role)" on public.account_flows
        for all to service_role using (true) with check (true);

    -- analyst_coverage: Ideas Inbox — one row per analyst research article
    -- (Phase 1: capture + review; awareness context only, never a gate).
    -- Ships INERT until this DDL is applied; load returns empty DataFrame.
    create table if not exists analyst_coverage (
        id               bigint primary key generated always as identity,
        ticker           text not null,
        company          text,
        article_date     date not null,
        report_type      text,
        analysts         jsonb not null default '[]'::jsonb,
        consensus_rating text,
        avg_pt           numeric,
        high_pt          numeric,
        low_pt           numeric,
        thesis           jsonb default '[]'::jsonb,
        catalysts        jsonb default '[]'::jsonb,
        risks            jsonb default '[]'::jsonb,
        raw_text         text,
        source           text default 'cnbc_pro',
        created_at       timestamptz default now()
    );
    alter table analyst_coverage enable row level security;
    create policy "service_role_all_analyst_coverage" on analyst_coverage
        for all to service_role using (true) with check (true);

    -- api_quota_log: daily API call counter per provider (Option 2 data health).
    -- Ships INERT until this DDL + function are applied; get_daily_quota returns
    -- None and the chip hides the field; soft-cap gate stays open (fail-safe).
    create table if not exists api_quota_log (
        provider   text    not null,
        log_date   date    not null default current_date,
        call_count integer not null default 0,
        primary key (provider, log_date)
    );
    alter table api_quota_log enable row level security;
    create policy "service_role_all_api_quota_log" on api_quota_log
        for all to service_role using (true) with check (true);

    -- Atomic increment (avoids read-modify-write race under concurrent requests)
    create or replace function public.increment_api_quota(p_provider text)
    returns void language plpgsql as $$
    begin
        insert into public.api_quota_log (provider, log_date, call_count)
        values (p_provider, current_date, 1)
        on conflict (provider, log_date)
        do update set call_count = public.api_quota_log.call_count + 1;
    end;
    $$;

    -- thesis_erosion_cache: daily adversarial erosion score per held ticker
    -- (Thesis Red Team Agent, Phase 1 — shipped 2026-07-23). One row per
    -- (ticker, score_date). score_date is ET ISO date from _today_et().
    -- counter_evidence is null in Phase 1; populated with Haiku bear-case
    -- bullets in Phase 2. signals_snapshot MUST include composite_today for
    -- the 5-session-ago composite lookback used in future rows.
    -- Optional: until created, load returns None / save no-ops; the Red Team
    -- tab shows only live-computed (non-cached) scores on trading days.
    create table if not exists public.thesis_erosion_cache (
        ticker           text        NOT NULL,
        score_date       text        NOT NULL,
        erosion_score    numeric     NOT NULL,
        erosion_label    text        NOT NULL,
        counter_evidence jsonb,
        signals_snapshot jsonb       NOT NULL,
        created_at       timestamptz DEFAULT now(),
        PRIMARY KEY (ticker, score_date)
    );
    alter table public.thesis_erosion_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.thesis_erosion_cache;
    create policy "Allow all (service role)" on public.thesis_erosion_cache
        for all to service_role using (true) with check (true);

    -- debate_cache: stores Bull vs Bear structured debate results per (ticker, debate_type, debate_date).
    -- debate_type: 'entry' (from Grow Today) | 'exit' (Phase 2, Exit Advisor).
    -- debate_date: ET ISO date string from _today_et(). transcript = [{round, agent, text}] list.
    -- Until table created: load returns None, save no-ops.
    create table if not exists public.debate_cache (
        ticker          text        NOT NULL,
        debate_type     text        NOT NULL,
        debate_date     text        NOT NULL,
        verdict         text,
        key_dispute     text,
        bull_case_score numeric,
        bear_case_score numeric,
        grounded        boolean,
        transcript      jsonb       NOT NULL,
        corpus_snapshot jsonb       NOT NULL,
        created_at      timestamptz DEFAULT now(),
        PRIMARY KEY (ticker, debate_type, debate_date)
    );
    alter table public.debate_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.debate_cache;
    create policy "Allow all (service role)" on public.debate_cache
        for all to service_role using (true) with check (true);

    -- structural_scan_cache: daily portfolio-level structural vulnerability
    -- narrative (Structural Vulnerability Scanner, Phase 1). ONE row per
    -- scan_date (ET ISO date via _today_et()) — this is a portfolio-wide
    -- synthesis, not per-ticker. blast_radius/cluster_snapshot/risk_budget_snapshot
    -- are the exact evidence used to generate narrative, kept for audit.
    -- narrative is null if the Haiku call failed — a failed/empty result is
    -- NEVER written (the caller only calls save when narrative succeeded), so a
    -- transient failure can be retried immediately rather than caching a
    -- placeholder for the rest of the day.
    -- Until table created: load returns None, save no-ops; the tab still renders
    -- the quantitative Blast Radius panel live and shows the generate button.
    create table if not exists public.structural_scan_cache (
        scan_date            text        NOT NULL,
        narrative            text,
        blast_radius         jsonb       NOT NULL,
        cluster_snapshot     jsonb       NOT NULL,
        risk_budget_snapshot jsonb       NOT NULL,
        created_at           timestamptz DEFAULT now(),
        PRIMARY KEY (scan_date)
    );
    alter table public.structural_scan_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.structural_scan_cache;
    create policy "Allow all (service role)" on public.structural_scan_cache
        for all to service_role using (true) with check (true);

    -- price_xcheck_history: persists the already-shipped price cross-check result
    -- (orchestrator.crosscheck_price/crosscheck_batch) once per Eastern trading
    -- day per held ticker (Information Asymmetry Detector, Phase 1). ONE row per
    -- (ticker, check_date). Written from the interactive Home page path (day-
    -- deduped via a session flag), NOT from cron — the premarket cron path never
    -- calls the cross-provider validator today, so writing from cron would add a
    -- new per-ticker second-provider fetch every run; the interactive path
    -- already computes this for free every 5 minutes via _cached_price_xcheck.
    -- Enables a day-over-day "widened since X" annotation on the existing red
    -- banner — impossible before this table existed (the prior check was a
    -- 5-minute TTL cache with zero history).
    -- Until table created: load returns None, save no-ops; the banner renders
    -- exactly as it does today, with no trend annotation.
    create table if not exists public.price_xcheck_history (
        ticker           text        NOT NULL,
        check_date       text        NOT NULL,
        primary_source   text,
        validator_source text,
        prev_gap_pct     numeric,
        live_gap_pct     numeric,
        ok               boolean     NOT NULL,
        created_at       timestamptz DEFAULT now(),
        PRIMARY KEY (ticker, check_date)
    );
    alter table public.price_xcheck_history enable row level security;
    drop policy if exists "Allow all (service role)" on public.price_xcheck_history;
    create policy "Allow all (service role)" on public.price_xcheck_history
        for all to service_role using (true) with check (true);

    -- regime_scenario_cache: daily portfolio-level regime-aware adversarial
    -- scenario narrative (Regime-Aware Adversarial Stress Testing, Phase 1).
    -- ONE row per scan_date (ET ISO date via _today_et()) — portfolio-wide, not
    -- per-ticker. Composes structural_scanner.blast_radius() + macro_calendar's
    -- FRED regime detector + cross_asset's USD signal into one Haiku-narrated
    -- compound scenario. scenario_narrative is null only if the Haiku call
    -- failed, but a failed/empty result is NEVER written (the caller only calls
    -- save when scenario_narrative succeeded), so a transient failure can be
    -- retried immediately rather than caching a placeholder for the rest of the
    -- day. Until table created: load returns None, save no-ops; the expander
    -- still shows the generate button.
    create table if not exists public.regime_scenario_cache (
        scan_date             text        NOT NULL,
        scenario_narrative    text,
        indicator_watchlist   jsonb,
        blast_radius_snapshot jsonb       NOT NULL,
        regime_snapshot       jsonb       NOT NULL,
        cross_asset_snapshot  jsonb       NOT NULL,
        created_at            timestamptz DEFAULT now(),
        PRIMARY KEY (scan_date)
    );
    alter table public.regime_scenario_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.regime_scenario_cache;
    create policy "Allow all (service role)" on public.regime_scenario_cache
        for all to service_role using (true) with check (true);

    -- catalyst_stress_cache: daily portfolio-level Catalyst-Specific Stress
    -- narrative (D4, Agentic Intelligence Roadmap v2). ONE row per scan_date —
    -- portfolio-wide, not per-ticker. Same shape/degradation contract as
    -- regime_scenario_cache above: narrative is NEVER written on a failed/empty
    -- Haiku call, so a transient failure can be retried immediately. Until
    -- table created: load returns None, save no-ops; the expander still shows
    -- the generate button.
    create table if not exists public.catalyst_stress_cache (
        scan_date             text        NOT NULL,
        narrative             text,
        ranked_snapshot       jsonb       NOT NULL,
        blast_radius_snapshot jsonb       NOT NULL,
        clusters_snapshot     jsonb       NOT NULL,
        created_at            timestamptz DEFAULT now(),
        PRIMARY KEY (scan_date)
    );
    alter table public.catalyst_stress_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.catalyst_stress_cache;
    create policy "Allow all (service role)" on public.catalyst_stress_cache
        for all to service_role using (true) with check (true);

    -- thesis_cluster_cache: daily portfolio-level Hidden Same-Bet Detector
    -- result (Agentic Intelligence Roadmap v2, D1). ONE row per scan_date —
    -- portfolio-wide, not per-ticker. Semantically clusters held positions'
    -- saved buy theses (Haiku) to find groups secretly betting on the same
    -- underlying assumption, then classifies each cluster unverified/
    -- possible/confirmed against correlation_clusters() in pure Python
    -- (never an LLM judgment call). clusters is an empty JSON array when
    -- Haiku found no shared assumption — a VALID result, not a failure; a
    -- genuine failure (no API key, timeout, malformed response) is never
    -- written (the caller only calls save when the Haiku call succeeded),
    -- so a transient failure can be retried immediately rather than caching
    -- a placeholder for the rest of the day. Until table created: load
    -- returns None, save no-ops; the section still shows the generate button.
    create table if not exists public.thesis_cluster_cache (
        scan_date       text        NOT NULL,
        clusters        jsonb       NOT NULL,
        thesis_snapshot jsonb       NOT NULL,
        truncated       boolean     DEFAULT false,
        created_at      timestamptz DEFAULT now(),
        PRIMARY KEY (scan_date)
    );
    alter table public.thesis_cluster_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.thesis_cluster_cache;
    create policy "Allow all (service role)" on public.thesis_cluster_cache
        for all to service_role using (true) with check (true);

    -- missed_opportunity_cache: daily portfolio-level Missed-Opportunity
    -- Pattern result (Agentic Intelligence Roadmap v2, O1). ONE row per
    -- scan_date — portfolio-wide, not per-ticker. Finds a descriptive
    -- pattern across engine "new_pick" recommendations never acted on
    -- (Haiku), verified in pure Python against a closed set of categorical
    -- fields (sector/price_band/composite_band/verdict/outcome_label) — a
    -- non-conforming ticker is dropped from its pattern, never rendered as
    -- if it fit. patterns is an empty JSON array when Haiku found no
    -- coherent pattern — a VALID result, not a failure; a genuine failure
    -- (no API key, timeout, malformed response) is never written (the
    -- caller only calls save when the Haiku call succeeded), so a
    -- transient failure can be retried immediately rather than caching a
    -- placeholder for the rest of the day. Until table created: load
    -- returns None, save no-ops; the section still shows the generate button.
    create table if not exists public.missed_opportunity_cache (
        scan_date       text        NOT NULL,
        patterns        jsonb       NOT NULL,
        missed_snapshot jsonb       NOT NULL,
        created_at      timestamptz DEFAULT now(),
        PRIMARY KEY (scan_date)
    );
    alter table public.missed_opportunity_cache enable row level security;
    drop policy if exists "Allow all (service role)" on public.missed_opportunity_cache;
    create policy "Allow all (service role)" on public.missed_opportunity_cache
        for all to service_role using (true) with check (true);
"""

import os

import streamlit as st
import pandas as pd

_DEFAULT_WATCHLIST = ["NVDA", "AMD", "INTC", "MU"]

# Read-only viewer mode. When enabled, every USER/OWNER-data write function
# below becomes a safe no-op — the security backstop behind the UI's disabled
# controls (a missed UI gate still cannot mutate data). Set once at startup by
# app.py after resolving the viewer's identity. NOTE: save_fundamentals_cache is
# intentionally NOT gated — it's a system cache (not user data), and cache
# warming on a viewer's visit is harmless/desirable.
#
# Stored in st.session_state (per-browser-session), NOT as a bare module
# global — Streamlit runs every active session's script in its own thread
# within the SAME process, so a module global here would be shared/racy
# across concurrent sessions: an owner's rerun setting it False could land
# between a read-only viewer's write call being gated and executed, letting
# a non-owner write succeed (2026-08-04 audit finding). `_READONLY` remains
# only as the fallback for callers outside a Streamlit session (the headless
# cron never calls set_readonly() at all, so it's always False there).
_READONLY = False

def set_readonly(flag: bool) -> None:
    global _READONLY
    _READONLY = bool(flag)
    try:
        import streamlit as _st
        _st.session_state["_db_readonly"] = bool(flag)
    except Exception:
        pass  # no active Streamlit session (e.g. headless cron) — fallback stands

def is_readonly() -> bool:
    try:
        import streamlit as _st
        if "_db_readonly" in _st.session_state:
            return bool(_st.session_state["_db_readonly"])
    except Exception:
        pass
    return _READONLY


def _supabase_creds() -> tuple[str, str]:
    """(url, key) for Supabase — env first, then st.secrets.

    Env (`SUPABASE_URL` / `SUPABASE_KEY`) is checked FIRST so the headless cron
    (GitHub Actions, no Streamlit runtime) works; the Streamlit app falls through
    to `st.secrets`. Mirrors the dual-source pattern in providers/_util. The key
    must be the service-role/secret key in both contexts — RLS stays on (the env
    path does NOT weaken security; it's the same key class)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if url and key:
        return url, key
    try:
        sec = st.secrets.get("supabase", {})
        return sec.get("url", "") or "", sec.get("key", "") or ""
    except Exception:
        return "", ""


def has_db() -> bool:
    """True when usable Supabase credentials are present (env or st.secrets)."""
    url, key = _supabase_creds()
    return bool(url) and bool(key) and not url.startswith("https://your-project")


# ── API quota tracking ────────────────────────────────────────────────────────

def increment_daily_quota(provider: str) -> None:
    """Atomically +1 today's call count for provider in Supabase.
    Silent on failure — quota tracking is observability, never a gate."""
    if not has_db():
        return
    try:
        _client().rpc("increment_api_quota", {"p_provider": provider}).execute()
    except Exception:
        pass


def get_daily_quota(provider: str) -> int | None:
    """Return today's call count for provider from Supabase.
    Returns None when DB is unavailable (chip hides the field); 0 when table
    exists but no row yet for today."""
    if not has_db():
        return None
    try:
        import pytz as _pytz
        import datetime as _dt
        today = _dt.datetime.now(_pytz.timezone("America/New_York")).date()
        rows = (
            _client()
            .table("api_quota_log")
            .select("call_count")
            .eq("provider", provider)
            .eq("log_date", str(today))
            .limit(1)
            .execute()
        ).data or []
        return int(rows[0]["call_count"]) if rows else 0
    except Exception:
        return None


# Process-level singleton (was @st.cache_resource). A plain module global caches
# the client across Streamlit reruns (same process) AND works headless, where the
# Streamlit cache decorators have no runtime. The DB client is genuinely a
# per-process singleton, so this is strictly simpler with identical app behaviour.
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from supabase import create_client
        url, key = _supabase_creds()
        _CLIENT = create_client(url, key)
    return _CLIENT


# ── Holdings ──────────────────────────────────────────────────────────────────

_HOLDINGS_COLS = ["Ticker", "Shares", "Avg Cost ($)"]


def load_holdings_or_none() -> "pd.DataFrame | None":
    """Holdings, or `None` (the offline sentinel) when the table could not be
    read AT ALL — no credentials, or the query raised.

    An EMPTY DataFrame from this function means something specific and
    different: the `holdings` table was read successfully and is genuinely
    empty. Same contract, and same reason, as `load_recommendations_or_none`.

    This distinction is load-bearing for the headless cron. `load_holdings()`
    below collapses both cases to an empty frame, so until this function
    existed a Supabase outage was indistinguishable from "the user owns
    nothing" — every protective lane logged one line, returned success, and
    sent no email while stop-breach and EXIT checks silently did not run. The
    nastiest variant is a PARTIAL outage (only `holdings` unreadable, e.g. a
    dropped RLS policy or a PGRST205 schema-cache miss on that one table):
    there the heartbeat write still succeeds, so 🩺 System Trust shows a fresh,
    genuinely green row over a scan that checked nothing.

    Never raises.
    """
    from stock_analyzer import api_health as _ah
    if not has_db():
        return None
    try:
        rows = _client().table("holdings").select("*").order("ticker").execute().data
        _ah.record("supabase", "success")
        if rows:
            df = pd.DataFrame(rows)[["ticker", "shares", "avg_cost"]]
            df.columns = _HOLDINGS_COLS
            df["Shares"] = df["Shares"].astype(float)
            df["Avg Cost ($)"] = df["Avg Cost ($)"].astype(float)
            return df
        # Read succeeded, table is genuinely empty — NOT the same as unreadable.
        return pd.DataFrame(columns=_HOLDINGS_COLS)
    except Exception as e:
        err = str(e)
        _ah.record("supabase", "error", msg=err[:120])
        if "row-level security" in err.lower() or "rls" in err.lower() or "42501" in err:
            st.error(
                "⛔ Supabase RLS is blocking reads. The `[supabase] key` "
                "secret must be the **service-role / secret key** "
                "(starts with `sb_secret_` or is the legacy service_role JWT) — "
                "not the publishable/anon key. Update `SUPABASE_KEY` in "
                "Railway → Variables, then Redeploy the service."
            )
        else:
            st.error(f"⛔ DB read error: {err}")
        return None


def load_watchlist_or_none() -> "list[str] | None":
    """Watchlist, or `None` (the offline sentinel) when it could not be read.

    An EMPTY list means the `watchlist` table was read successfully and is
    genuinely empty. Same contract as `load_holdings_or_none`.

    **Why this exists is sharper than the holdings case.** `load_watchlist()`
    below returns `list(_DEFAULT_WATCHLIST)` when there are no credentials — so
    a blind app doesn't merely show nothing, it shows the user a watchlist
    **they never created, presented as theirs**. An empty portfolio is a wrong
    absence; a fabricated watchlist is a wrong *assertion*, which is worse and
    is the exact class `project_fundamentals_gate` exists to forbid. Never
    raises.
    """
    from stock_analyzer import api_health as _ah
    if not has_db():
        return None
    try:
        rows = _client().table("watchlist").select("ticker").execute().data
        _ah.record("supabase", "success")
        # Read succeeded — an empty table is a real answer, not an absence.
        return [r["ticker"] for r in rows] if rows else []
    except Exception as e:
        _ah.record("supabase", "error", msg=str(e)[:120])
        return None


def load_trades_or_none() -> "pd.DataFrame | None":
    """Trades, or `None` (the offline sentinel) when the table could not be read.

    An EMPTY frame means the table was read and is genuinely empty — the state
    of a brand-new journal. Same contract and reason as
    `load_holdings_or_none`; collapsing the two would let a Supabase outage
    render My Edge / Prior Trades / Behavioral Fingerprint as "you have no
    history" rather than "we could not read your history". Never raises.

    THIS is the strict implementation and `load_trades()` is the thin lenient
    wrapper — deliberately that way round, matching holdings. The first attempt
    inverted it (this function called `load_trades()` and caught exceptions) and
    was actively harmful: `load_trades()` swallows its own errors and returns an
    empty frame, so the `except` was unreachable AND every failed read recorded
    `api_health "success"`. That resets `consecutive_errors`, which feeds
    `system_health.check_providers`' recovered re-grade — i.e. a broken `trades`
    table would have downgraded a genuine "down" to "warn" on the very provider
    row this change exists to harden. Don't re-invert it.
    """
    from stock_analyzer import api_health as _ah
    if not has_db():
        return None
    try:
        rows = (
            _client().table("trades")
            .select("*")
            .order("traded_at", desc=True)
            .execute().data
        )
        _ah.record("supabase", "success")
        if not rows:
            # Read succeeded, journal is genuinely empty — NOT unreadable.
            return pd.DataFrame(columns=_TRADE_COLS)
        df = pd.DataFrame(rows)
        # Backfill columns for rows pre-dating each feature addition
        for col in ("signal_seen", "followed_signal", "deviation_reason",
                    "lesson", "lesson_category", "user_thesis", "thesis_source",
                    "decision_context", "premortem_case_against",
                    "premortem_commitment", "premortem_trigger_price",
                    "premortem_trigger_direction"):
            if col not in df.columns:
                df[col] = None
        return df
    except Exception as e:
        err = str(e)
        _ah.record("supabase", "error", msg=err[:120])
        if "row-level security" in err.lower() or "42501" in err:
            st.error(
                "⛔ Supabase RLS is blocking the trades table. The `[supabase] "
                "key` secret must be the **service-role / secret key** "
                "(bypasses RLS), not the publishable/anon key. Update "
                "`SUPABASE_KEY` in Railway → Variables, then Redeploy."
            )
        else:
            st.error(f"⛔ Trades read error: {err}")
        return None


def unavailable_detail() -> "str | None":
    """Human-readable detail when Supabase can't be read AT ALL, else None.

    Moved here from `cron_runner._db_unavailable_detail` on 2026-08-17 so the
    outage EMAIL and the in-app outage BANNER give the same explanation of the
    same fault — two independent wordings for one condition is how they drift.

    Deliberately re-reads HOLDINGS rather than issuing a synthetic `select 1`:
    holdings is the input every protective decision depends on, so a PARTIAL
    outage that breaks only that table (a dropped RLS policy, a PGRST205
    schema-cache miss) is caught too — a probe against another table would
    happily succeed and report health. Never raises.
    """
    try:
        if not has_db():
            return "no Supabase credentials (SUPABASE_URL / SUPABASE_KEY not set)"
        if load_holdings_or_none() is None:
            return "holdings table could not be read from Supabase"
    except Exception as exc:
        return f"Supabase read raised: {str(exc)[:160]}"
    return None


def should_attempt_db_reload(last_failed_at: "float | None", now_ts: float) -> bool:
    """Should the app retry the initial DB load after a failure?

    Pure — takes `time.time()` epoch seconds, does no I/O and reads no clock, so
    it is testable and can't drift with timezone handling. (Epoch floats, NOT
    `datetime.now()`, which `check_antipatterns.py` flags.)

    Bounds the retry so an outage doesn't cost three Supabase reads with client
    timeouts on EVERY widget interaction. The boundary is INCLUSIVE — at exactly
    `DB_RELOAD_RETRY_SEC` elapsed we retry — and is asserted exactly in tests
    rather than reasoned about; the 2026-08-04 Critical was an off-by-one of
    this shape that a design review had waved through as harmless.
    """
    from stock_analyzer.constants import DB_RELOAD_RETRY_SEC
    if last_failed_at is None:
        return True
    return (now_ts - float(last_failed_at)) >= DB_RELOAD_RETRY_SEC


def load_holdings() -> pd.DataFrame:
    """Holdings, with an empty frame on ANY failure.

    Contract deliberately UNCHANGED — the interactive app assigns this straight
    into `st.session_state.holdings_df`, which is read throughout `app.py`, and
    those call sites legitimately treat "no holdings" and "couldn't load" the
    same way (there is nothing to render either way).

    A caller that must NOT conflate the two — anything that decides whether to
    act, alert, or report success — needs `load_holdings_or_none()`.
    """
    df = load_holdings_or_none()
    return df if df is not None else pd.DataFrame(columns=_HOLDINGS_COLS)


def save_holdings(df: pd.DataFrame) -> bool:
    """Persist the holdings DataFrame to Supabase. Returns True on success.

    Atomic-ish replace: upsert every current row on the ticker unique key,
    then sweep tickers no longer in the DataFrame. Order matters — upsert
    first so a transient failure leaves the prior data intact, never wipes.
    Requires UNIQUE(ticker) on holdings (see one-time SQL at module top).
    """
    if is_readonly(): return False  # read-only viewer: no-op
    from stock_analyzer import api_health as _ah
    if not has_db():
        return False

    # Build + validate records up front. Failure here never touches the DB.
    records = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        ticker   = str(row.get("Ticker", "")).strip().upper()
        try:
            shares   = float(row.get("Shares", 0) or 0)
            avg_cost = float(row.get("Avg Cost ($)", 0) or 0)
        except (TypeError, ValueError):
            continue
        if ticker and shares > 0 and avg_cost > 0 and ticker not in seen:
            records.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
            seen.add(ticker)

    try:
        client = _client()
        if records:
            client.table("holdings").upsert(records, on_conflict="ticker").execute()
        # Sweep tickers no longer present. Idempotent — a partial failure here
        # leaves stale rows (recoverable on next save) but never destroys the
        # current truth.
        kept = list(seen)
        sweep = client.table("holdings").delete()
        if kept:
            sweep = sweep.not_.in_("ticker", kept)
        else:
            sweep = sweep.neq("ticker", "")
        sweep.execute()
        # Symmetric sweep on manual_stops — a stop override for a ticker no
        # longer held is an orphan (the override only applies to active
        # positions). Wrapped in its own try so a missing manual_stops table
        # (user hasn't run the one-time DDL yet) doesn't fail the holdings
        # save that already succeeded.
        try:
            ms_sweep = client.table("manual_stops").delete()
            if kept:
                ms_sweep = ms_sweep.not_.in_("ticker", kept)
            else:
                ms_sweep = ms_sweep.neq("ticker", "")
            ms_sweep.execute()
        except Exception:
            pass
        _ah.record("supabase", "success")
        return True
    except Exception as e:
        err = str(e)
        _ah.record("supabase", "error", msg=err[:120])
        if "row-level security" in err.lower() or "42501" in err:
            st.error(
                "⛔ Supabase RLS is blocking writes. The Streamlit secret "
                "`[supabase] key` must be the service-role / secret key "
                "(bypasses RLS), not the publishable/anon key."
            )
        else:
            st.error("⛔ Failed to save holdings — see Data Health tab for details.")
        return False


# ── Daily snapshots (Tier B day-over-day P&L baseline) ─────────────────────────
# Optional table. Until created, load returns empty and save no-ops, so the
# day-P&L degrades to the held-only mark exactly as before. See DDL at module top.

def load_recent_snapshots(days: int = 10) -> pd.DataFrame:
    """Most-recent daily snapshots (newest first). Empty on any error / missing
    table — the day-P&L then falls back to the held-only mark, never crashes."""
    empty = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])
    if not has_db():
        return empty
    try:
        rows = (
            _client().table("daily_snapshots")
            .select("snapshot_date,ticker,shares,close_price")
            .order("snapshot_date", desc=True)
            .limit(max(1, days) * 200)   # days × generous max plausible positions
            .execute().data
        )
        if not rows:
            return empty
        df = pd.DataFrame(rows)
        df["shares"]      = df["shares"].astype(float)
        df["close_price"] = df["close_price"].astype(float)
        return df
    except Exception:
        # Optional enhancement table — degrade silently (no error banner) so a
        # not-yet-created table doesn't nag; core P&L still renders (held mark).
        return empty


def load_daily_snapshots(start_date=None, end_date=None) -> "pd.DataFrame":
    """Load daily portfolio snapshots for a date range.
    Returns DataFrame with columns: snapshot_date, ticker, shares, close_price."""
    import pandas as pd
    empty = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])
    if not has_db():
        return empty
    try:
        q = _client().table("daily_snapshots").select("*")
        if start_date is not None:
            q = q.gte("snapshot_date", str(start_date)[:10])
        if end_date is not None:
            q = q.lte("snapshot_date", str(end_date)[:10])
        rows = q.order("snapshot_date", desc=False).execute().data
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def save_daily_snapshot(snapshot_date, rows: list[dict]) -> bool:
    """Upsert the snapshot for `snapshot_date` (today's held positions at the
    final, market-closed close price). Sweeps tickers no longer held for that
    date so a same-day exit doesn't linger. Read-only viewers no-op; a missing
    table degrades to a silent no-op (returns False)."""
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    _date = str(snapshot_date)
    records = []
    seen: set[str] = set()
    for r in rows:
        tk = str(r.get("ticker", "")).strip().upper()
        try:
            sh = float(r.get("shares") or 0)
            px = float(r.get("close_price") or 0)
        except (TypeError, ValueError):
            continue
        if tk and sh > 0 and px > 0 and tk not in seen:
            records.append({"snapshot_date": _date, "ticker": tk,
                            "shares": sh, "close_price": px})
            seen.add(tk)
    if not records:
        return False
    try:
        client = _client()
        client.table("daily_snapshots").upsert(
            records, on_conflict="snapshot_date,ticker"
        ).execute()
        # Sweep tickers for THIS date no longer held (idempotent; a partial
        # failure leaves a stale row, recoverable next write, never destructive).
        client.table("daily_snapshots").delete().eq(
            "snapshot_date", _date
        ).not_.in_("ticker", list(seen)).execute()
        return True
    except Exception:
        return False


def save_daily_regime(regime_date, regime: dict) -> bool:
    """Upsert the day's detected macro regime into daily_regime (one row per
    calendar day, portfolio-independent). Read-only viewers no-op; a missing
    table degrades to a silent no-op (returns False). Never fabricates a
    regime — `regime` must come from a real `detect_macro_regime()` call."""
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    if not regime or not regime.get("regime"):
        return False
    record = {
        "regime_date": str(regime_date)[:10],
        "regime": regime.get("regime"),
        "label": regime.get("label"),
        "confidence": regime.get("confidence"),
        "fed_trend": regime.get("fed_trend"),
        "cpi_yoy": regime.get("cpi_yoy"),
        "source": regime.get("source"),
    }
    try:
        _client().table("daily_regime").upsert(
            [record], on_conflict="regime_date"
        ).execute()
        return True
    except Exception:
        return False


def load_daily_regime(days_back: int = 90) -> "pd.DataFrame":
    """Load persisted daily regime detections from the last `days_back` days.
    Returns DataFrame with columns: regime_date, regime, label, confidence,
    fed_trend, cpi_yoy, source. Empty DataFrame on failure/absence."""
    import pandas as pd
    from datetime import date, timedelta
    empty = pd.DataFrame(columns=[
        "regime_date", "regime", "label", "confidence", "fed_trend", "cpi_yoy", "source",
    ])
    if not has_db():
        return empty
    try:
        start = (date.today() - timedelta(days=days_back)).isoformat()
        rows = (
            _client().table("daily_regime").select("*")
            .gte("regime_date", start)
            .order("regime_date", desc=False).execute().data
        )
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def save_sentiment_snapshot(snap_date, rows: list[dict]) -> bool:
    """Upsert daily sentiment readings into sentiment_history.

    rows: list of dicts with keys: ticker, vader_compound, vader_score,
    headline_count, bullish_pct, bearish_pct, buzz_score, company_score,
    vs_sector_pp, source.  Upserts on (ticker, snap_date); last writer wins
    intraday. Silently no-ops in READONLY mode or when DB is offline.
    """
    if is_readonly():
        return False
    if not has_db() or not rows:
        return False
    _date = snap_date.isoformat() if hasattr(snap_date, "isoformat") else str(snap_date)[:10]
    payload = []
    seen: set[str] = set()
    for r in rows:
        tk = str(r.get("ticker", "")).strip().upper()
        if not tk or tk in seen:
            continue
        # Require at least one sentiment reading to be present
        if r.get("vader_compound") is None and r.get("bullish_pct") is None:
            continue
        seen.add(tk)
        payload.append({
            "ticker":          tk,
            "snap_date":       _date,
            "vader_compound":  r.get("vader_compound"),
            "vader_score":     r.get("vader_score"),
            "headline_count":  r.get("headline_count"),
            "bullish_pct":     r.get("bullish_pct"),
            "bearish_pct":     r.get("bearish_pct"),
            "buzz_score":      r.get("buzz_score"),
            "company_score":   r.get("company_score"),
            "vs_sector_pp":    r.get("vs_sector_pp"),
            "source":          str(r.get("source") or "cron"),
        })
    if not payload:
        return False
    try:
        _client().table("sentiment_history").upsert(
            payload, on_conflict="ticker,snap_date"
        ).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=f"sentiment_snapshot_upsert: {str(e)[:100]}")
        return False


# ── Last-known-good bundle cache (data-resilience) ─────────────────────────────
# Optional table. Until created, load returns None and save no-ops, so load_all
# keeps its honest "Could not load" failure exactly as before. See DDL at top.

def _json_safe(obj):
    """Recursively coerce to JSON-serializable: NaN/inf → None, numpy/pandas
    scalars → python native, anything exotic → str. Keeps Supabase's JSON
    encoder from choking on yfinance .info or fundamentals dicts. Never raises."""
    import math
    if obj is None or isinstance(obj, bool) or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "item"):  # numpy / pandas scalar
        try:
            return _json_safe(obj.item())
        except Exception:
            return None
    try:
        return str(obj)
    except Exception:
        return None


def save_bundle_cache(ticker: str, bundle: dict) -> bool:
    """Write-through the last-known-good raw bundle (history + info) so load_all
    can serve aged-but-real data when providers are down. Read-only viewers
    no-op; a missing table degrades to a silent no-op. NEVER raises (callers wrap
    too) — cache I/O must not break the load_all success path."""
    if is_readonly(): return False  # read-only viewer: no-op
    # Instrumented under the "bundle_cache" Data Health source so seeding is
    # finally visible (it was a fully-silent except: pass — a broken table / RLS
    # / serialization / missing-context write was invisible). has_db() is inside
    # the try so a credential/context failure is RECORDED, not swallowed.
    from stock_analyzer import api_health as _ah
    try:
        if not has_db():
            _ah.record("bundle_cache", "error", msg="save: has_db()=False (no creds/context)")
            return False
        hist = bundle.get("history")
        if hist is None or getattr(hist, "empty", True):
            return False  # empty history — nothing worth caching (not an error)
        record = {
            "ticker":       str(ticker).strip().upper(),
            "history_json": hist.to_json(orient="split", date_format="iso"),
            "info":         _json_safe(bundle.get("info") or {}),
            "fetched_at":   pd.Timestamp.now(tz="UTC").isoformat(),
        }
        _client().table("bundle_cache").upsert(record, on_conflict="ticker").execute()
        _ah.record("bundle_cache", "success")
        return True
    except Exception as e:
        _ah.record("bundle_cache", "error", msg=f"save: {str(e)[:100]}")
        return False


def load_bundle_cache(ticker: str, max_age_days: int) -> dict | None:
    """Return the cached raw bundle for `ticker` if present and within
    max_age_days, else None. Reconstructs the history DataFrame. Graceful —
    returns None on missing table / parse error / staleness; news, earnings and
    revisions come back EMPTY (fallback mode degrades those, never live)."""
    # Instrumented under "bundle_cache": "success" = served last-known-good,
    # "empty" = queried but nothing usable (no row / too stale), "error" =
    # has_db/RLS/parse failure. This is what disambiguates "cache empty (seeding
    # starved)" from "cache present but unreadable" when providers are down.
    from stock_analyzer import api_health as _ah
    try:
        if not has_db():
            _ah.record("bundle_cache", "error", msg="load: has_db()=False (no creds/context)")
            return None
        rows = (
            _client().table("bundle_cache")
            .select("history_json,info,fetched_at")
            .eq("ticker", str(ticker).strip().upper())
            .limit(1).execute().data
        )
        if not rows:
            _ah.record("bundle_cache", "empty")   # never seeded for this ticker
            return None
        row = rows[0]
        fetched = pd.to_datetime(row.get("fetched_at"), utc=True, errors="coerce")
        if pd.isna(fetched):
            _ah.record("bundle_cache", "empty")
            return None
        age_days = (pd.Timestamp.now(tz="UTC") - fetched).days
        if age_days > max_age_days:
            _ah.record("bundle_cache", "empty")   # seeded but too stale to trust
            return None
        from io import StringIO
        hist = pd.read_json(StringIO(row["history_json"]), orient="split")
        # Drop any bar with a NaN/missing Close. A trailing partial/placeholder
        # bar serializes to null and round-trips back as NaN, which makes the
        # last close (→ current_price downstream) NaN and crashed the target
        # math (max() of an empty set). A bar with no close isn't a usable price.
        if "Close" in hist.columns:
            hist = hist[hist["Close"].notna()]
        if hist.empty:
            _ah.record("bundle_cache", "empty")
            return None
        hist.index = pd.to_datetime(hist.index)
        _ah.record("bundle_cache", "success")     # served last-known-good
        return {
            "bundle": {
                "history":   hist,
                "info":      row.get("info") or {},
                "news":      [],
                "earnings":  {},
                "revisions": {},
            },
            "fetched_at": fetched.date().isoformat(),
            "age_days":   int(age_days),
        }
    except Exception as e:
        _ah.record("bundle_cache", "error", msg=f"load: {str(e)[:100]}")
        return None


# ── Watchlist ─────────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    if has_db():
        try:
            rows = _client().table("watchlist").select("ticker").execute().data
            return [r["ticker"] for r in rows] if rows else []
        except Exception as e:
            st.warning(f"Could not load watchlist — using empty list until restored. ({e})")
            return []
    return list(_DEFAULT_WATCHLIST)


def load_watchlist_added_dates() -> dict[str, str]:
    """Return {ticker: added_at (ISO date string)} for every watchlist row.
    Separate from load_watchlist() (which returns a flat list and had callers
    throughout app.py expecting that shape) — used only by O4 Watchlist
    Resurrection. Missing/unreadable rows are simply absent from the dict
    (never fabricated as "very stale" or "never stale"). Returns {} on any
    failure — graceful degradation, never raises."""
    if not has_db():
        return {}
    try:
        from datetime import datetime
        import pytz
        _et = pytz.timezone("America/New_York")
        rows = _client().table("watchlist").select("ticker,added_at").execute().data
        result: dict[str, str] = {}
        for r in (rows or []):
            t = str(r.get("ticker") or "").strip().upper()
            added = r.get("added_at")
            if not (t and added):
                continue
            try:
                _dt = datetime.fromisoformat(str(added).replace("Z", "+00:00"))
                if _dt.tzinfo is None:
                    _dt = pytz.utc.localize(_dt)
                result[t] = _dt.astimezone(_et).date().isoformat()
            except (TypeError, ValueError):
                continue
        return result
    except Exception:
        return {}


# ── Trades ───────────────────────────────────────────────────────────────────

_TRADE_COLS = ["id", "ticker", "action", "shares", "price",
               "cost_basis", "realized_pnl", "notes", "trigger_type",
               "signal_seen", "followed_signal", "deviation_reason", "lesson",
               "lesson_category", "traded_at", "user_thesis", "thesis_source",
               "decision_context", "premortem_case_against", "premortem_commitment",
               "premortem_trigger_price", "premortem_trigger_direction"]


def load_trades() -> pd.DataFrame:
    """Trades, with an empty (but correctly-COLUMNED) frame on ANY failure.

    Lenient wrapper over `load_trades_or_none` — the strict version owns the
    read, the api_health recording and the RLS message, so there is exactly one
    implementation and a failed read can never be recorded as a success. Callers
    that must distinguish "no trades" from "couldn't read trades" use the strict
    one; the ~40 call sites that just want a frame to iterate use this.

    The empty frame always carries `_TRADE_COLS`, which downstream consumers
    index into — never a bare `pd.DataFrame()`.
    """
    df = load_trades_or_none()
    return df if df is not None else pd.DataFrame(columns=_TRADE_COLS)


def save_trade(record: dict) -> bool:
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        _client().table("trades").insert(record).execute()
        return True
    except Exception as e:
        _err_str = str(e)
        # DB-level idempotency backstop (2026-08-04 audit finding): a unique-
        # violation naming trades_idempotency_key_unique means this exact
        # staged record (same UUID, generated once at app.py record-staging
        # time) was already inserted — e.g. a double-clicked Confirm past the
        # session-state dedup guard. The first insert already won; treat the
        # retry as an idempotent no-op success, not an error.
        if "trades_idempotency_key_unique" in _err_str or (
            "idempotency_key" in _err_str
            and ("duplicate key" in _err_str or "23505" in _err_str)
        ):
            return True
        # Graceful degradation: additive optional columns (thesis_source, F-5;
        # decision_context, Concept E; premortem_case_against/premortem_commitment,
        # Concept C; idempotency_key, 2026-08-04) may not exist yet in Supabase
        # (DDL not applied). If the error names ANY optional column (or is a
        # PGRST204 schema-cache miss), drop ALL optional columns and retry
        # once — a single retry handles the case where multiple columns are
        # missing simultaneously.
        _optional = ("thesis_source", "decision_context",
                     "premortem_case_against", "premortem_commitment",
                     "premortem_trigger_price", "premortem_trigger_direction",
                     "lesson_category", "idempotency_key")
        _any_optional = any(c in _err_str for c in _optional)
        if _any_optional:
            _to_drop = {c for c in _optional if c in record}
            try:
                _client().table("trades").insert(
                    {k: v for k, v in record.items() if k not in _to_drop}
                ).execute()
                return True
            except Exception as e2:
                e = e2
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error(f"⛔ Failed to save trade: {str(e)[:300]}")
        return False


def delete_trade(trade_id: int) -> bool:
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        _client().table("trades").delete().eq("id", int(trade_id)).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error("⛔ Failed to delete trade — see Data Health tab for details.")
        return False


def update_trade_realized_pnl(trade_id: int, realized_pnl: float,
                               cost_basis: float | None = None) -> bool:
    """
    Update an existing SELL trade's realized_pnl (and optionally cost_basis).
    Used by recalculate_from_trades() to correct stale figures stored on rows
    that were saved when holdings_df was in a corrupted state.
    """
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        update_record = {"realized_pnl": float(realized_pnl)}
        if cost_basis is not None:
            update_record["cost_basis"] = float(cost_basis)
        _client().table("trades").update(update_record).eq("id", int(trade_id)).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error(f"⛔ Failed to update trade {trade_id} — see Data Health tab for details.")
        return False


# ── Thesis Reviews (AI Insights — F-1) ───────────────────────────────────────

_THESIS_REVIEW_COLS = ["id", "ticker", "trade_date", "reviewed_at",
                       "status", "summary", "inputs_hash", "created_at"]


def load_thesis_reviews() -> pd.DataFrame:
    """Return all thesis reviews, most-recent first. Empty DataFrame when table
    does not exist yet (inert until DDL is applied in Supabase)."""
    empty = pd.DataFrame(columns=_THESIS_REVIEW_COLS)
    if not has_db():
        return empty
    try:
        rows = (
            _client().table("thesis_reviews")
            .select("*")
            .order("reviewed_at", desc=True)
            .execute().data
        )
        if rows:
            df = pd.DataFrame(rows)
            for col in _THESIS_REVIEW_COLS:
                if col not in df.columns:
                    df[col] = None
            return df
        return empty
    except Exception:
        return empty


def save_thesis_review(record: dict) -> bool:
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("thesis_reviews").insert(record).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


def update_user_thesis(ticker: str, thesis: str) -> bool:
    """Update user_thesis on the most recent BUY trade for `ticker`."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        resp = (
            _client()
            .table("trades")
            .select("id")
            .eq("ticker", ticker.upper())
            .eq("action", "BUY")
            .order("traded_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data if resp else []
        if not rows:
            return False
        trade_id = rows[0]["id"]
        _client().table("trades").update({"user_thesis": thesis.strip()}).eq("id", trade_id).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


# ── Weekly Debriefs (AI Insights — F-3) ──────────────────────────────────────

_WEEKLY_DEBRIEF_COLS = [
    "id", "week_ending", "generated_at", "performance_pct", "spy_pct",
    "alpha_pct", "section_facts", "section_decisions", "section_patterns",
    "section_watchnext", "email_sent", "email_sent_at",
]


def load_weekly_debriefs(limit: int = 4) -> "pd.DataFrame":
    """Load the most recent weekly debriefs, newest first."""
    import pandas as pd
    empty = pd.DataFrame(columns=_WEEKLY_DEBRIEF_COLS)
    if not has_db():
        return empty
    try:
        rows = (
            _client()
            .table("weekly_debriefs")
            .select("*")
            .order("week_ending", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def save_weekly_debrief(record: dict) -> bool:
    """Upsert a weekly debrief record (unique on week_ending)."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("weekly_debriefs").upsert(
            record, on_conflict="week_ending"
        ).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


# ── Monthly Intelligence Reports (AI Insights — F-4) ─────────────────────────

_MONTHLY_REPORT_COLS = [
    "id", "period_start", "period_end", "generated_at", "engine_alpha_pct",
    "acted_count", "missed_count", "section_entry_quality",
    "section_signal_discipline", "section_thesis", "section_patterns",
    "viz_json", "email_sent", "email_sent_at",
]


def load_monthly_reports(limit: int = 3) -> "pd.DataFrame":
    """Load the most recent monthly intelligence reports, newest first. Empty
    DataFrame when the table does not exist yet (inert until DDL is applied)."""
    import pandas as pd
    empty = pd.DataFrame(columns=_MONTHLY_REPORT_COLS)
    if not has_db():
        return empty
    try:
        rows = (
            _client()
            .table("monthly_reports")
            .select("*")
            .order("period_end", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def save_monthly_report(record: dict) -> bool:
    """Upsert a monthly intelligence report (unique on period_end)."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("monthly_reports").upsert(
            record, on_conflict="period_end"
        ).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


# ── Analyst Coverage (AI Insights — Ideas Inbox) ─────────────────────────────

_ANALYST_COVERAGE_COLS = [
    "id", "ticker", "company", "article_date", "report_type",
    "analysts", "consensus_rating", "avg_pt", "high_pt", "low_pt",
    "thesis", "catalysts", "risks", "raw_text", "source", "created_at",
    "price_at_article_date", "composite_score_at_save",
]


def save_analyst_coverage(record: dict) -> bool:
    """Insert one analyst-coverage record. Append-only (each article is a distinct row)."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("analyst_coverage").insert(record).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


def load_analyst_coverage(
    ticker: str | None = None,
    days: int | None = None,
    limit: int = 100,
) -> "pd.DataFrame":
    """Load analyst coverage rows, newest first. Empty DataFrame on any failure or missing table.

    ticker  — filter to a single ticker (optional).
    days    — restrict to articles with article_date >= today_ET − days (optional).
    limit   — max rows returned (default 100).
    """
    import pandas as pd
    empty = pd.DataFrame(columns=_ANALYST_COVERAGE_COLS)
    if not has_db():
        return empty
    try:
        q = _client().table("analyst_coverage").select("*")
        if ticker:
            q = q.eq("ticker", ticker.strip().upper())
        if days:
            from datetime import datetime, timedelta
            import pytz
            _et = pytz.timezone("America/New_York")
            cutoff = (datetime.now(tz=_et) - timedelta(days=days)).date().isoformat()
            q = q.gte("article_date", cutoff)
        rows = q.order("article_date", desc=True).limit(limit).execute().data
        if not rows:
            return empty
        df = pd.DataFrame(rows)
        for col in _ANALYST_COVERAGE_COLS:
            if col not in df.columns:
                df[col] = None
        return df
    except Exception:
        return empty


def delete_analyst_coverage(row_id) -> bool:
    """Delete a single analyst-coverage record by id."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("analyst_coverage").delete().eq("id", row_id).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


def update_analyst_coverage_price(row_id, price: float) -> bool:
    """Backfill price_at_article_date on one existing analyst-coverage row.
    Used by scripts/backfill_analyst_prices.py; awareness-only, never gates."""
    if is_readonly():
        return False
    if not has_db():
        return False
    try:
        _client().table("analyst_coverage").update({"price_at_article_date": price}).eq("id", row_id).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        return False


# ── Earnings Context (Phase 1 — pre-earnings playbook enrichment) ─────────────

def save_earnings_context(records: list[dict]) -> None:
    """Bulk upsert earnings_context rows on (ticker, article_date).
    Ships inert if the table doesn't exist yet (graceful degradation)."""
    if is_readonly() or not has_db() or not records:
        return
    try:
        _client().table("earnings_context").upsert(
            records, on_conflict="ticker,article_date"
        ).execute()
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])


def load_earnings_context(ticker: str, max_age_days: int = 30) -> dict | None:
    """Return the most recent earnings_context row for ticker within max_age_days.
    Returns None on any failure or missing table (graceful degradation)."""
    if not has_db():
        return None
    try:
        from datetime import datetime, timedelta
        import pytz
        _et = pytz.timezone("America/New_York")
        cutoff = (datetime.now(tz=_et) - timedelta(days=max_age_days)).date().isoformat()
        rows = (
            _client()
            .table("earnings_context")
            .select("*")
            .eq("ticker", ticker.strip().upper())
            .gte("article_date", cutoff)
            .order("article_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def load_all_earnings_context() -> dict[str, list[dict]]:
    """Fetch ALL earnings_context rows with no date cutoff, grouped by ticker.
    Returns {ticker_upper: [row, ...]} oldest-first per ticker. Used by the
    Workflow ROI classifier which needs to match historical BUY trades against
    research saved anywhere in the app's lifetime (not just the recent window).
    Returns {} on any failure (graceful degradation)."""
    if not has_db():
        return {}
    try:
        rows = (
            _client()
            .table("earnings_context")
            .select("*")
            .order("article_date", desc=False)
            .execute()
            .data
        ) or []
        result: dict[str, list[dict]] = {}
        for row in rows:
            t = (row.get("ticker") or "").strip().upper()
            if t:
                result.setdefault(t, []).append(row)
        return result
    except Exception:
        return {}


def load_earnings_context_batch(tickers: list[str], max_age_days: int = 30) -> dict[str, dict]:
    """Fetch earnings_context rows for all tickers in one query.
    Returns {ticker: row} — missing tickers are absent from the dict.
    Returns {} on any failure (graceful degradation)."""
    if not has_db() or not tickers:
        return {}
    try:
        from datetime import datetime, timedelta
        import pytz
        _et = pytz.timezone("America/New_York")
        cutoff = (datetime.now(tz=_et) - timedelta(days=max_age_days)).date().isoformat()
        upper_tickers = [t.strip().upper() for t in tickers]
        rows = (
            _client()
            .table("earnings_context")
            .select("*")
            .in_("ticker", upper_tickers)
            .gte("article_date", cutoff)
            .order("article_date", desc=True)
            .execute()
            .data
        )
        result: dict[str, dict] = {}
        for row in (rows or []):
            t = (row.get("ticker") or "").strip().upper()
            if t and t not in result:
                result[t] = row
        return result
    except Exception:
        return {}


# ── Earnings Results (Phase 2 — post-earnings F-1 thesis checkpoint) ──────────

def save_earnings_results(records: list[dict]) -> None:
    """Bulk upsert earnings_results rows on (ticker, report_date).
    Ships inert if the table doesn't exist yet (graceful degradation)."""
    if is_readonly() or not has_db() or not records:
        return
    try:
        _client().table("earnings_results").upsert(
            records, on_conflict="ticker,report_date"
        ).execute()
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])


def load_earnings_result(ticker: str, lookback_days: int = 90) -> dict | None:
    """Return the most recent earnings_results row for ticker within lookback_days.
    Returns None on any failure or missing table (graceful degradation)."""
    if not has_db():
        return None
    try:
        from datetime import datetime, timedelta
        import pytz
        _et = pytz.timezone("America/New_York")
        cutoff = (datetime.now(tz=_et) - timedelta(days=lookback_days)).date().isoformat()
        rows = (
            _client()
            .table("earnings_results")
            .select("*")
            .eq("ticker", ticker.strip().upper())
            .gte("report_date", cutoff)
            .order("report_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def recalculate_from_trades(trades_df: pd.DataFrame) -> dict:
    """
    Replay every trade chronologically to derive the truthful holdings table
    plus the correct realized_pnl for each SELL row. Use this whenever the
    trades table has been modified in a way that could put holdings out of
    sync — most commonly after deleting trades, since holdings updates are
    incremental and don't auto-revert on delete.

    The bug this catches: bad SELL rows reduce holdings_df.Shares at save
    time. Deleting those SELLs removes the trade row but leaves Shares in
    the corrupted state — and the next legitimate SELL then auto-fills its
    cost_basis from the wrong avg_cost, producing wrong realized_pnl (e.g.
    NFLX +$177 when it should have been negative).

    Returns:
      {
        "holdings_df":             pd.DataFrame with columns
                                    [Ticker, Shares, Avg Cost ($)],
        "realized_pnl_corrections": dict mapping trade_id → corrected
                                    realized_pnl for SELL rows whose stored
                                    value differs from the replay result,
        "warnings":                 list[str] — SELLs encountered with no
                                    prior BUY in the trades table (history
                                    gap, can't compute correct cost basis),
      }
    """
    holdings: dict[str, dict] = {}                # ticker → {shares, avg_cost}
    corrections: dict[int, dict] = {}             # trade_id → {realized_pnl, cost_basis}
    warnings: list[str] = []

    if trades_df is None or trades_df.empty:
        return {
            "holdings_df":              pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"]),
            "realized_pnl_corrections": {},
            "warnings":                 [],
        }

    # Chronological replay — oldest first so cost basis builds up correctly.
    # format='ISO8601' + utc=True handles mixed-precision timestamp strings
    # in the same column: raw-SQL inserts (rebaseline rows, no microseconds)
    # vs Python-SDK inserts (default `now()`, microsecond precision). Without
    # format='ISO8601', pandas infers format from the first row only, and
    # errors='coerce' silently turns the non-matching rows into NaT — which
    # then sort last via na_position='last' and trigger spurious "no prior
    # BUY" drift warnings on later SELLs.
    df = trades_df.copy()
    df["_sort_ts"] = pd.to_datetime(
        df["traded_at"], errors="coerce", utc=True, format="ISO8601"
    )
    df = df.sort_values(["_sort_ts", "id"], ascending=True, na_position="last")

    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        action = str(row.get("action", "")).upper()
        try:
            shares = float(row.get("shares") or 0)
            price  = float(row.get("price")  or 0)
        except (TypeError, ValueError):
            continue
        if not ticker or shares <= 0 or price <= 0:
            continue

        # Stock-split adjustment rows OVERWRITE the holding state rather than
        # accumulating. The Apply-Split handler in the Portfolio page inserts a
        # row with action='SPLIT', shares=adjusted_total_shares, price=
        # adjusted_avg_cost — exactly the new state of the holding after the
        # split. Without this branch, a Rebuild after a split would replay the
        # pre-split BUYs and overwrite the user's approved post-split holdings.
        if "SPLIT" in action:
            if shares > 0 and price > 0:
                holdings[ticker] = {"shares": shares, "avg_cost": price}
            continue

        if "BUY" in action:
            if ticker in holdings:
                h = holdings[ticker]
                new_shares = h["shares"] + shares
                if new_shares > 0:
                    h["avg_cost"] = (h["shares"] * h["avg_cost"] + shares * price) / new_shares
                    h["shares"]   = new_shares
            else:
                holdings[ticker] = {"shares": shares, "avg_cost": price}

        elif "SELL" in action:
            trade_id = row.get("id")
            if ticker in holdings:
                h               = holdings[ticker]
                correct_basis   = h["avg_cost"]
                correct_pnl     = round((price - correct_basis) * shares, 2)
                stored_pnl      = None
                try:
                    stored_pnl  = float(row.get("realized_pnl") or 0)
                except (TypeError, ValueError):
                    stored_pnl  = None
                stored_basis    = None
                try:
                    stored_basis = float(row.get("cost_basis") or 0)
                except (TypeError, ValueError):
                    stored_basis = None
                # Record a correction only when the stored figures actually differ —
                # avoid no-op DB writes on already-correct rows.
                needs_pnl_fix   = stored_pnl   is None or abs(stored_pnl   - correct_pnl)   > 0.01
                needs_basis_fix = stored_basis is None or abs(stored_basis - correct_basis) > 0.01
                if trade_id is not None and (needs_pnl_fix or needs_basis_fix):
                    corrections[int(trade_id)] = {
                        "realized_pnl":     correct_pnl,
                        "cost_basis":       round(correct_basis, 4),
                        "stored_pnl":       stored_pnl,
                        "stored_basis":     stored_basis,
                    }
                new_shares = h["shares"] - shares
                # Over-sell detection: a SELL larger than the open position
                # would silently drive shares negative and delete the row,
                # leaving no breadcrumb that the trade history is internally
                # inconsistent. Surface it as a warning and clamp at 0.
                if new_shares < -1e-6:
                    warnings.append(
                        f"SELL {ticker} {shares:.4f}sh exceeds current {h['shares']:.4f}sh "
                        f"on hand — trade history is internally inconsistent. "
                        f"Position closed; the over-sold {abs(new_shares):.4f}sh delta is unaccounted for."
                    )
                    new_shares = 0.0
                h["shares"] = new_shares
                if h["shares"] <= 1e-6:
                    del holdings[ticker]
            else:
                warnings.append(
                    f"SELL {ticker} {shares:.0f}sh @ ${price:.2f} has no prior BUY "
                    "in the trade history — cost basis can't be computed."
                )

    holdings_list = [
        {"Ticker": tk, "Shares": h["shares"], "Avg Cost ($)": round(h["avg_cost"], 4)}
        for tk, h in holdings.items()
    ]
    holdings_df = pd.DataFrame(holdings_list, columns=["Ticker", "Shares", "Avg Cost ($)"])

    return {
        "holdings_df":              holdings_df,
        "realized_pnl_corrections": corrections,
        "warnings":                 warnings,
    }


# ── Recommendations log ──────────────────────────────────────────────────────
#
# Every pick surfaced by Today's Brief (new_picks, add_positions, buy_candidates)
# is first-seen-captured here so we can answer questions like "how often did the
# App recommend X over the last month" and "which recs got acted on." Stored
# with a unique constraint on (ticker, rec_date, rec_type) so re-renders of the
# Brief during the same day don't create duplicates — only the first surface
# of each (ticker, type) on a given date is recorded.

_REC_COLS = ["id", "ticker", "rec_date", "rec_type", "surfaced_at",
             "price_at_surface", "composite_score", "momentum_score",
             "sector", "conviction", "verdict", "thesis"]


def save_scanner_cache(results_df, scan_date, source: str = "cron") -> bool:
    """Upsert the single-row (id=1) latest-scan snapshot.

    Stores the WHOLE scan DataFrame as JSON (orient="split") so Home can
    reconstruct `scanner_results` exactly — every column preserved, no schema
    mapping to drift. Written by the headless cron (source="cron") post-open and
    by a manual full scan (source="app"). System cache, but honours the read-only
    guard (it's a write). Best-effort; never raises.
    """
    if is_readonly():
        return False
    if not has_db() or results_df is None or getattr(results_df, "empty", True):
        return False
    try:
        _date = (scan_date.isoformat() if hasattr(scan_date, "isoformat")
                 else (str(scan_date)[:10] if scan_date else None))
        record = {
            "id":           1,
            "results_json": results_df.to_json(orient="split", date_format="iso"),
            "scan_date":    _date,
            "source":       str(source)[:16],
            "scanned_at":   pd.Timestamp.now(tz="UTC").isoformat(),
        }
        _client().table("scanner_cache").upsert(record, on_conflict="id").execute()
        return True
    except Exception:
        return False


def load_scanner_cache() -> dict | None:
    """Return {"df": DataFrame, "scan_date": str|None, "source": str|None,
    "scanned_at": str|None} for the latest persisted scan, or None (DB offline /
    table missing / no scan yet / parse error). Never raises — None means "no
    persisted scan", so the app behaves exactly as today (empty until a manual
    scan)."""
    if not has_db():
        return None
    try:
        from io import StringIO
        rows = (
            _client().table("scanner_cache")
            .select("results_json,scan_date,source,scanned_at")
            .eq("id", 1).limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        _json = row.get("results_json")
        if not _json:
            return None
        df = pd.read_json(StringIO(_json), orient="split")
        if df is None or df.empty:
            return None
        return {
            "df":         df,
            "scan_date":  row.get("scan_date"),
            "source":     row.get("source"),
            "scanned_at": row.get("scanned_at"),
        }
    except Exception:
        return None


def save_recommendations(records: list[dict]) -> dict:
    """
    Persist new recommendations.

    Returns a diagnostic dict:
      {"attempted": N, "saved": M, "error": str | None}

    `attempted` counts records that passed normalization (had a ticker, date,
    and rec_type). `saved` is len(attempted) on success — supabase-py with
    ignore_duplicates=True doesn't tell us how many rows were actual inserts
    vs ignored, but a non-zero number here means the request didn't error
    out. `error` is a short string on failure (None on success).

    The DB defaults `surfaced_at` to now() — don't set it client-side so the
    first-seen timestamp is server-authoritative.
    """
    if is_readonly(): return {"attempted": 0, "saved": 0, "error": "read-only"}  # read-only viewer: no-op
    if not records or not has_db():
        return {"attempted": 0, "saved": 0, "error": None}
    payload = []
    for r in records:
        tk = str(r.get("ticker", "")).strip().upper()
        rt = str(r.get("rec_type", "")).strip()
        rd = r.get("rec_date")
        if not tk or not rt or rd is None:
            continue
        rd_str = rd.isoformat() if hasattr(rd, "isoformat") else str(rd)[:10]
        # price_at_surface is what the rec was priced at when first seen.
        # Coerce defensively — a zero or negative price isn't useful, store
        # NULL in that case so downstream "would-have-gained" math can skip it.
        _pas = r.get("price_at_surface")
        try:
            _pas = float(_pas) if _pas is not None else None
            if _pas is not None and _pas <= 0:
                _pas = None
        except (TypeError, ValueError):
            _pas = None
        payload.append({
            "ticker":           tk,
            "rec_date":         rd_str,
            "rec_type":         rt,
            "price_at_surface": _pas,
            "composite_score":  r.get("composite_score"),
            "momentum_score":   r.get("momentum_score"),
            "sector":           r.get("sector"),
            "conviction":       r.get("conviction"),
            "verdict":          r.get("verdict"),
            "thesis":           (str(r.get("thesis") or "")[:600]) or None,
            "s_score":          r.get("s_score"),
            "avg_sent":         r.get("avg_sent"),
            "t_score":          r.get("t_score"),
            "bq_score":         r.get("bq_score"),
            "val_score":        r.get("val_score"),
        })
    if not payload:
        return {"attempted": 0, "saved": 0, "error": None}

    # Columns that require an ALTER TABLE DDL before they exist. Two GENERATIONS,
    # stripped in order — s_score/avg_sent (F-179) already exist in production;
    # only the 2026-08-01 pillar-score cols are actually pending its DDL. A
    # column-missing error must strip ONLY the columns actually still missing —
    # stripping s_score/avg_sent too on a t_score-missing error would silently
    # stop persisting sentiment (already-working, unrelated data) for the
    # entire window until the pillar-score DDL is applied, with no error
    # surfaced (saved=N, error=None) to reveal the loss.
    _QA_PILLAR_COLS = frozenset(("t_score", "bq_score", "val_score"))
    _F179_COLS      = frozenset(("s_score", "avg_sent"))
    _OPTIONAL_COLS  = _QA_PILLAR_COLS | _F179_COLS

    def _upsert(rows):
        try:
            _client().table("recommendations").upsert(
                rows,
                on_conflict="ticker,rec_date,rec_type",
                ignore_duplicates=True,
            ).execute()
            return True, None
        except TypeError:
            return None, "compat"   # ignore_duplicates unsupported — try insert
        except Exception as exc:
            return False, str(exc)

    def _insert(rows):
        try:
            _client().table("recommendations").insert(rows).execute()
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _strip(rows, cols):
        return [{k: v for k, v in row.items() if k not in cols} for row in rows]

    def _col_missing(err_str):
        low = (err_str or "").lower()
        # PostgREST's actual missing-column wording is "Could not find the
        # 'X' column of 'Y' in the schema cache" (code PGRST204) — NOT "does
        # not exist" / "unknown column". Those two were the only patterns
        # checked here until a live PGRST204 on 2026-08-07 slipped past this
        # match entirely, so the strip-and-retry cascade below never
        # triggered and every pillar-score-bearing recommendation row failed
        # outright (saved=0) instead of degrading gracefully.
        # Note: PGRST204 can also fire transiently when a column DOES exist
        # but PostgREST's schema cache hasn't refreshed yet post-DDL — this
        # still strips the pillar scores for that one request (saved=N,
        # error=None). Acceptable: it only ever drops the optional score
        # columns, never the row itself that F-233 depends on.
        return ("does not exist" in low or "unknown column" in low
                or "could not find the" in low or "pgrst204" in low) and \
               any(c in low for c in _OPTIONAL_COLS)

    def _with_retry(call_fn, rows):
        """call_fn(rows); on a column-missing error, strip ONLY the newer
        pillar-score columns and retry — strip the older F-179 columns too
        only if THAT retry itself still reports a missing column (e.g. a
        fresh DB with neither DDL applied). Returns (ok, err, rows_used)."""
        ok, err = call_fn(rows)
        if ok is not False or not _col_missing(err):
            return ok, err, rows
        stage1 = _strip(rows, _QA_PILLAR_COLS)
        ok1, err1 = call_fn(stage1)
        if ok1 is not False or not _col_missing(err1):
            return ok1, err1, stage1
        stage2 = _strip(rows, _OPTIONAL_COLS)
        ok2, err2 = call_fn(stage2)
        return ok2, err2, stage2

    # ignore_duplicates compat (TypeError) is a client-library signature issue,
    # independent of which columns are present — it would raise on the very
    # first attempt below, before any stripping, so it's detected once here.
    ok, err, used = _with_retry(_upsert, payload)
    if ok is True:
        return {"attempted": len(used), "saved": len(used), "error": None}
    if ok is None:                  # TypeError compat path — fall through to insert
        ok2, err2, used2 = _with_retry(_insert, used)
        if ok2 is True:
            return {"attempted": len(used2), "saved": len(used2), "error": None}
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=f"rec_log_insert: {(err2 or '')[:100]}")
        return {"attempted": len(payload), "saved": 0, "error": (err2 or "")[:200]}
    # ok is False — upsert (including the missing-column retry cascade) still errored
    from stock_analyzer import api_health as _ah
    _ah.record("supabase", "error", msg=f"rec_log_upsert: {(err or '')[:100]}")
    return {"attempted": len(payload), "saved": 0, "error": (err or "")[:200]}


def load_recommendations(start_date=None, end_date=None) -> pd.DataFrame:
    """
    Read recommendation history. No date filter applied when start_date/
    end_date are both None (full history) -- callers wanting a bounded
    window must pass explicit dates. Returns a DataFrame ordered by
    surfaced_at descending. Degrades to an EMPTY DataFrame both when no recs
    exist and when the query itself fails -- the two are indistinguishable
    here by design (existing consumers all treat "empty" as a harmless
    "nothing to show" state). A consumer that must NOT conflate "zero recs"
    with "load failed" needs load_recommendations_or_none() instead.
    """
    empty = pd.DataFrame(columns=_REC_COLS)
    if not has_db():
        return empty
    try:
        q = _client().table("recommendations").select("*")
        if start_date is not None:
            sd = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)[:10]
            q = q.gte("rec_date", sd)
        if end_date is not None:
            ed = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)[:10]
            q = q.lte("rec_date", ed)
        rows = q.order("surfaced_at", desc=True).execute().data
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def load_recommendations_or_none(start_date=None, end_date=None) -> pd.DataFrame | None:
    """
    Same query as load_recommendations(), but distinguishes a genuine
    zero-row result (returns an empty DataFrame) from a failed load --
    missing credentials or a raised exception during the query (returns
    None). load_recommendations() itself cannot make this distinction (its
    except branch returns the same empty DataFrame either way), which is
    fine for its existing consumers but unsafe for a consumer where "load
    failed" must never be treated as "zero recommendations exist" (e.g.
    Self Track Record's classify_buys, which would otherwise silently
    misclassify every app-aligned BUY as self-initiated on a transient
    Supabase hiccup -- the offline-sentinel-collapse bug class).
    """
    empty = pd.DataFrame(columns=_REC_COLS)
    if not has_db():
        return None
    try:
        q = _client().table("recommendations").select("*")
        if start_date is not None:
            sd = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)[:10]
            q = q.gte("rec_date", sd)
        if end_date is not None:
            ed = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)[:10]
            q = q.lte("rec_date", ed)
        rows = q.order("surfaced_at", desc=True).execute().data
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return None


# Nullable exit_signals columns eligible for coalesce-on-write (see
# save_exit_signals_batch). Not a policy threshold — just the set of
# optional columns this table happens to have.
_EXIT_SIGNAL_NULLABLE_COLS = (
    "price_at_signal", "dd_from_peak_pct", "pnl_pct", "below_ma_count", "rel_strength",
    "composite_score",
)


def save_exit_signals_batch(signals: list[dict]) -> None:
    """Persist exit signals emitted by the Daily Brief for Behavioral Fingerprint
    exit-side analysis.

    Idempotent: upserts on (ticker, signal_date, signal_type) — repeated same-day
    Brief builds are no-ops.  Never raises — a capture failure must never break
    the Brief itself.

    Each dict in `signals` must have at minimum: ticker, signal_date, signal_type.
    All other columns (composite_score, price_at_signal, dd_from_peak_pct,
    pnl_pct, below_ma_count, rel_strength) are nullable and may be omitted.

    Coalesce-on-write: a same-day rebuild that (for whatever reason) re-derives
    a row with a NULL in one of the nullable columns must never clobber a
    non-null value a prior build already captured for that same (ticker,
    signal_date, signal_type) key — the upsert's "last write wins" default
    would otherwise silently blank a previously-captured value.  Before the
    upsert, read any existing rows matching this batch's keys and fill any
    incoming NULL from the existing non-null value (last-NON-NULL wins: a
    genuine new non-null value still overwrites). The pre-read is best-effort
    — if it fails (DB offline, etc.) the batch is upserted as-is rather than
    dropping the write entirely; losing a write is worse than the rare
    possible clobber in that one failure case.
    """
    if is_readonly():
        return
    if not signals:
        return
    if not has_db():
        return

    try:
        tickers = sorted({str(s["ticker"]) for s in signals if s.get("ticker")})
        dates   = sorted({str(s["signal_date"]) for s in signals if s.get("signal_date")})
        types   = sorted({str(s["signal_type"]) for s in signals if s.get("signal_type")})
        if tickers and dates and types:
            cols = "ticker,signal_date,signal_type," + ",".join(_EXIT_SIGNAL_NULLABLE_COLS)
            existing_rows = (
                _client().table("exit_signals").select(cols)
                .in_("ticker", tickers)
                .in_("signal_date", dates)
                .in_("signal_type", types)
                .execute()
            ).data or []
            existing_by_key = {
                (r.get("ticker"), str(r.get("signal_date")), r.get("signal_type")): r
                for r in existing_rows
            }
            for s in signals:
                key = (s.get("ticker"), str(s.get("signal_date")), s.get("signal_type"))
                existing = existing_by_key.get(key)
                if not existing:
                    continue
                for col in _EXIT_SIGNAL_NULLABLE_COLS:
                    if s.get(col) is None and existing.get(col) is not None:
                        s[col] = existing[col]
    except Exception as e:
        import warnings
        warnings.warn(f"save_exit_signals_batch: pre-read merge skipped ({e}); upserting batch as-is")

    try:
        _client().table("exit_signals").upsert(
            signals,
            on_conflict="ticker,signal_date,signal_type",
        ).execute()
    except Exception as e:
        import warnings
        warnings.warn(f"save_exit_signals_batch: {e}")


def load_exit_signals(days_back: int = 365) -> pd.DataFrame:
    """Read persisted exit signals going back days_back calendar days.

    Returns a DataFrame (column names match the exit_signals table, snake_case)
    on success, or an empty DataFrame on any exception.
    Uses the same date-filter pattern as load_recommendations().
    """
    if not has_db():
        return pd.DataFrame()
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("exit_signals")
            .select("*")
            .gte("signal_date", cutoff)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def save_analyst_target_snapshots_batch(snapshots: list[dict]) -> None:
    """Persist a daily analyst-consensus-target snapshot per held ticker.

    Idempotent: upserts on (ticker, snapshot_date) — repeated same-day cron
    runs are no-ops. Never raises — a capture failure must never break the
    premarket cron. Log-only (Phase 1): no alert reads this table yet.

    Each dict in `snapshots` must have at minimum: ticker, snapshot_date.
    target_mean/num_analysts/info_source are nullable and may be omitted.
    """
    if is_readonly():
        return
    if not snapshots:
        return
    if not has_db():
        return
    try:
        _client().table("analyst_target_snapshots").upsert(
            snapshots,
            on_conflict="ticker,snapshot_date",
        ).execute()
    except Exception as e:
        import warnings
        warnings.warn(f"save_analyst_target_snapshots_batch: {e}")


def load_analyst_target_snapshots(days_back: int = 365) -> pd.DataFrame:
    """Read persisted analyst target snapshots going back days_back calendar days.

    Returns a DataFrame (column names match the analyst_target_snapshots
    table, snake_case) on success, or an empty DataFrame on any exception.
    """
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("analyst_target_snapshots")
            .select("*")
            .gte("snapshot_date", cutoff)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ── Judgment-layer opinions (Phase 0, log-only — see docs/plans/judgment-layer.md) ──
# Ships inert until the DDL below is applied — degrades silently, same convention
# as analyst_target_snapshots. RLS: FOR ALL TO service_role.
#
# CREATE TABLE IF NOT EXISTS judgment_opinions (
#     id           BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
#     source       TEXT NOT NULL,
#     dimension    TEXT NOT NULL,
#     ticker       TEXT NOT NULL,   -- '_PORTFOLIO' sentinel for portfolio-wide opinions
#     signal_date  DATE NOT NULL,
#     signal       NUMERIC NOT NULL,
#     label        TEXT,
#     confidence   NUMERIC NOT NULL,
#     as_of        TIMESTAMPTZ NOT NULL,
#     is_live      BOOLEAN NOT NULL DEFAULT TRUE,
#     evidence     TEXT,
#     advisory     BOOLEAN NOT NULL DEFAULT FALSE,
#     created_at   TIMESTAMPTZ DEFAULT NOW(),
#     CONSTRAINT judgment_opinions_unique UNIQUE (source, dimension, ticker, signal_date)
# );
#
# ALTER TABLE judgment_opinions ENABLE ROW LEVEL SECURITY;
# CREATE POLICY "service_role_all_judgment_opinions" ON judgment_opinions
#     FOR ALL TO service_role USING (true) WITH CHECK (true);
def save_judgment_opinions_batch(opinions: list[dict]) -> None:
    """Persist Phase-0 judgment-layer opinions (log-only; nothing reads this yet).

    Idempotent: upserts on (source, dimension, ticker, signal_date) — repeated
    same-day Home renders are no-ops. Never raises — a capture failure must
    never break Home. Each dict must be shaped by
    stock_analyzer.judgment_opinion.build_opinion(); this function only adds
    signal_date (derived from as_of) and the ticker sentinel for portfolio-wide
    opinions before writing.
    """
    if is_readonly():
        return
    if not opinions:
        return
    if not has_db():
        return
    try:
        rows = []
        for _op in opinions:
            _row = dict(_op)
            _row["ticker"] = _row.get("ticker") or "_PORTFOLIO"
            _row["signal_date"] = str(_row["as_of"])[:10]
            rows.append(_row)
        _client().table("judgment_opinions").upsert(
            rows,
            on_conflict="source,dimension,ticker,signal_date",
        ).execute()
    except Exception as e:
        import warnings
        warnings.warn(f"save_judgment_opinions_batch: {e}")


def load_judgment_opinions(days_back: int = 365) -> pd.DataFrame:
    """Read persisted judgment-layer opinions going back days_back calendar days.

    Returns a DataFrame (column names match the judgment_opinions table,
    snake_case) on success, or an empty DataFrame on any exception. Nothing
    consumes this yet (Phase 0) — it exists so Phase 2's grading harness has
    history to read once it's built.
    """
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("judgment_opinions")
            .select("*")
            .gte("signal_date", cutoff)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ── Judgment-layer grades (Phase 2, see docs/plans/judgment-layer.md) ───────────
# Ships inert until the DDL below is applied — degrades silently, same convention
# as judgment_opinions/analyst_target_snapshots. RLS: FOR ALL TO service_role.
#
# CREATE TABLE IF NOT EXISTS judgment_grades (
#     id             BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
#     source         TEXT NOT NULL,
#     dimension      TEXT NOT NULL,
#     ticker         TEXT NOT NULL,   -- '_PORTFOLIO' sentinel, matches judgment_opinions
#     signal_date    DATE NOT NULL,
#     horizon_days   INT NOT NULL,
#     opinion_signal NUMERIC NOT NULL,
#     realized_pct   NUMERIC,         -- ticker alpha vs SPY, or portfolio alpha vs SPY
#     correct        BOOLEAN,         -- sign(realized_pct) == sign(opinion_signal); NULL if realized_pct is NULL
#     graded_at      TIMESTAMPTZ NOT NULL,
#     created_at     TIMESTAMPTZ DEFAULT NOW(),
#     CONSTRAINT judgment_grades_unique UNIQUE (source, dimension, ticker, signal_date)
# );
#
# ALTER TABLE judgment_grades ENABLE ROW LEVEL SECURITY;
# CREATE POLICY "service_role_all_judgment_grades" ON judgment_grades
#     FOR ALL TO service_role USING (true) WITH CHECK (true);
def save_judgment_grades_batch(grades: list[dict]) -> None:
    """Persist Phase-2 judgment-layer grades (one row per graded opinion).

    Idempotent: upserts on (source, dimension, ticker, signal_date) — matches
    judgment_opinions' natural key, one grade per opinion. Never raises — a
    capture failure must never break the Judge page. Each dict must be shaped
    by stock_analyzer.judgment_grading's grade_ticker_opinion()/
    grade_portfolio_opinion() output.
    """
    if is_readonly():
        return
    if not grades:
        return
    if not has_db():
        return
    try:
        _client().table("judgment_grades").upsert(
            grades,
            on_conflict="source,dimension,ticker,signal_date",
        ).execute()
    except Exception as e:
        import warnings
        warnings.warn(f"save_judgment_grades_batch: {e}")


def load_judgment_grades(days_back: int = 365) -> pd.DataFrame:
    """Read persisted judgment-layer grades going back days_back calendar days.

    Returns a DataFrame (column names match the judgment_grades table,
    snake_case) on success, or an empty DataFrame on any exception. Consumed by
    the Judge page's track-record display and (eventually) Phase 3's
    evidence-based weighting.
    """
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("judgment_grades")
            .select("*")
            .gte("signal_date", cutoff)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ── State of the Portfolio standing thesis (Summary page — see
# docs/plans/state-of-portfolio-standing-thesis.md) ──────────────────────────
# Ships inert until the DDL below is applied — degrades silently, same
# convention as judgment_opinions/analyst_target_snapshots. RLS: FOR ALL TO
# service_role. One row per ISO week; a second write in the same ISO week
# overwrites (idempotent) rather than duplicating.
#
# CREATE TABLE IF NOT EXISTS portfolio_thesis (
#     id             BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
#     thesis_date    DATE NOT NULL,
#     iso_year       INT NOT NULL,
#     iso_week       INT NOT NULL,
#     schema_version INT NOT NULL,
#     claims         JSONB NOT NULL,
#     prose          TEXT NOT NULL,
#     created_at     TIMESTAMPTZ DEFAULT NOW(),
#     CONSTRAINT portfolio_thesis_unique UNIQUE (iso_year, iso_week)
# );
#
# ALTER TABLE portfolio_thesis ENABLE ROW LEVEL SECURITY;
# CREATE POLICY "service_role_all_portfolio_thesis" ON portfolio_thesis
#     FOR ALL TO service_role USING (true) WITH CHECK (true);
def save_portfolio_thesis(record: dict) -> bool:
    """Upsert one standing-thesis row, keyed by (iso_year, iso_week).

    Idempotent: a second write in the same ISO week overwrites rather than
    duplicating — the once-per-ISO-week write guard on the Summary page is a
    coarse app-side check; this UNIQUE(iso_year, iso_week) upsert is the real
    backstop. Never raises — a capture failure must never break the Summary
    page. `record` must be shaped by
    stock_analyzer.portfolio_thesis.compose_thesis()'s output
    ({"v", "thesis_date", "iso_year", "iso_week", "claims", "prose"}).
    """
    if is_readonly():
        return False
    if not record:
        return False
    if not has_db():
        return False
    try:
        import json
        row = {
            "thesis_date":    record["thesis_date"],
            "iso_year":       int(record["iso_year"]),
            "iso_week":       int(record["iso_week"]),
            "schema_version": int(record.get("v", 1)),
            "claims":         json.dumps(record.get("claims", {})),
            "prose":          record.get("prose", ""),
        }
        _client().table("portfolio_thesis").upsert(
            row, on_conflict="iso_year,iso_week",
        ).execute()
        return True
    except Exception as e:
        import warnings
        warnings.warn(f"save_portfolio_thesis: {e}")
        return False


def load_portfolio_thesis(lookback_days: int) -> list[dict]:
    """Read persisted standing-thesis rows within `lookback_days` calendar
    days, most-recent-first.

    Returns [] on any DB failure or table-not-yet-created (this table needs
    the DDL above applied manually) — same "ships inert until DDL" contract
    as judgment_opinions/analyst_target_snapshots, so a pre-DDL session never
    crashes the Summary page.
    """
    if not has_db():
        return []
    try:
        from datetime import timedelta

        from stock_analyzer.market_time import today_et
        cutoff = (today_et() - timedelta(days=lookback_days)).isoformat()
        rows = (
            _client()
            .table("portfolio_thesis")
            .select("*")
            .gte("thesis_date", cutoff)
            .order("thesis_date", desc=True)
            .execute()
            .data
        )
        return rows or []
    except Exception:
        return []


# ── Price cross-check history (ticker × date) ────────────────────────────────
# System cache → NOT _READONLY-gated (mirrors sector_cache / sentiment_llm_cache).
# A viewer-only session still benefits from a warm cross-check history rather
# than silently losing that day's row (2026-07-29 audit Medium finding — this
# was gated with no documented rationale, unlike every sibling recomputable
# cache in this file).

def save_price_xcheck_history_batch(rows: list[dict]) -> None:
    """Persist today's price cross-check result per held ticker.

    Idempotent: upserts on (ticker, check_date) — repeated writes same day
    are no-ops in effect (overwrite with the same day's latest value).
    Never raises.
    """
    if not rows:
        return
    if not has_db():
        return
    try:
        _client().table("price_xcheck_history").upsert(
            rows, on_conflict="ticker,check_date",
        ).execute()
    except Exception:
        pass


def load_price_xcheck_history(ticker: str, before_date: str, days_back: int = 21) -> dict | None:
    """Return the most recent row for `ticker` strictly before `before_date`,
    within the last `days_back` calendar days, or None if no such row exists
    (table absent, DB offline, or genuinely no prior history yet). Never raises.
    """
    if not ticker or not has_db():
        return None
    try:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(before_date) - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("price_xcheck_history")
            .select("*")
            .eq("ticker", ticker)
            .lt("check_date", before_date)
            .gte("check_date", cutoff)
            .order("check_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ── Predictive Modeling Shadow Layer — model_predictions ledger (Phase 1, ──
# F-234, MEASUREMENT-ONLY). Ships inert until the DDL below is applied —
# degrades silently, same "ships inert" convention as
# judgment_opinions/analyst_target_snapshots/portfolio_thesis. RLS: FOR ALL
# TO service_role. See docs/architecture.md §6.31 for the full DDL and
# docs/plans/predictive-modeling-shadow-layer.md for the design. Writers are
# the cron (`cron_runner.py`) and the one-off backfill script
# (`scripts/backfill_vol_predictions.py`) ONLY — there is no interactive
# user-write path for this table, so the `is_readonly()` guards below are
# precautionary defense-in-depth (consistent with every other writer in this
# file), not load-bearing for this particular table.
_MODEL_PREDICTIONS_COLS = [
    "id", "model_name", "model_version", "scope", "ticker", "made_at",
    "horizon_days", "target_metric", "predicted_value", "predicted_low",
    "predicted_high", "baseline_value", "regime_at_make", "features_snapshot",
    "realized_value", "scored_at", "abs_error", "baseline_abs_error",
    "source", "created_at",
]


def save_model_predictions_batch(rows: list[dict]) -> bool:
    """Upsert new prediction rows into `model_predictions`. Idempotent on
    (model_name, model_version, scope, ticker, made_at) — a rerun of the
    backfill script, or the daily cron re-firing, never duplicates. Never
    raises: a pre-DDL "relation does not exist" error is caught identically
    to any other failure — logged and reported via the return value, never
    surfaced as an exception to the caller."""
    if is_readonly():
        return False
    if not rows:
        return False
    if not has_db():
        return False
    try:
        _client().table("model_predictions").upsert(
            rows, on_conflict="model_name,model_version,scope,ticker,made_at",
        ).execute()
        return True
    except Exception as e:
        import warnings
        warnings.warn(f"save_model_predictions_batch: {e}")
        return False


def load_model_predictions(model_name: str | None = None, days_back: int = 400) -> "pd.DataFrame | None":
    """Read `model_predictions` rows with `made_at` within the trailing
    `days_back` calendar days, optionally filtered to one `model_name`.

    Returns `None` (the offline sentinel) on ANY failure — no credentials,
    the table not yet existing (pre-DDL), or a raised query exception — kept
    distinct from a genuinely empty DataFrame (the query succeeded, zero
    rows exist yet). The 🔬 Model Lab page's own offline-state banner
    depends on this distinction: `None` renders "producer offline" (mockup
    state 3), an empty-but-real DataFrame renders "warming up" (mockup
    state 2) via `prediction_scoring.score_predictions()`'s own n=0 read."""
    if not has_db():
        return None
    try:
        from datetime import timedelta

        from stock_analyzer.market_time import today_et
        cutoff = (today_et() - timedelta(days=days_back)).isoformat()
        q = _client().table("model_predictions").select("*").gte("made_at", cutoff)
        if model_name:
            q = q.eq("model_name", model_name)
        rows = q.execute().data
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_MODEL_PREDICTIONS_COLS)
    except Exception:
        return None


def load_unmatured_model_predictions(model_name: str | None = None) -> "pd.DataFrame | None":
    """Read every `model_predictions` row not yet matured (`realized_value
    IS NULL`), optionally filtered to one `model_name` — the maturation
    cron's own input query. Unbounded by date (the table is small by
    construction: one row per held ticker + the portfolio aggregate, per
    day) — the caller decides which of the returned rows are actually due
    for maturation (trading-day-aware; see `cron_runner._trading_days_elapsed`).

    Returns `None` on ANY failure (offline sentinel), matching
    `load_model_predictions` — a transient failure here must never be
    silently treated as "nothing pending"."""
    if not has_db():
        return None
    try:
        q = _client().table("model_predictions").select("*").is_("realized_value", "null")
        if model_name:
            q = q.eq("model_name", model_name)
        rows = q.execute().data
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_MODEL_PREDICTIONS_COLS)
    except Exception:
        return None


def has_backfilled_predictions(model_name: str, model_version: str,
                               ticker: str) -> "bool | None":
    """True if `ticker` already has at least one `source='backfill'` row for
    this (model_name, model_version) at ticker scope.

    Exists so the `maintenance` cron lane can skip tickers whose historical
    backfill is already done. Without it the lane would re-fetch the full
    PREDICTION_BACKFILL_PERIOD (5y) of price history for every held ticker on
    every run and re-upsert near-identical rows — correct, thanks to the
    unique-constraint upsert, but pointlessly expensive against the provider
    quota.

    Returns `None` on ANY failure (offline sentinel — no credentials, pre-DDL
    table, or a raised query exception), kept distinct from a genuine `False`
    ("checked, and this ticker has no backfill rows"). The caller is expected
    to treat `None` as "unknown → do the work anyway": the backfill is
    idempotent, so a redundant run costs API calls, whereas wrongly skipping
    leaves a permanent hole in the ledger."""
    if not has_db():
        return None
    try:
        rows = (
            _client().table("model_predictions")
            .select("id")
            .eq("model_name", model_name)
            .eq("model_version", model_version)
            .eq("scope", "ticker")
            .eq("ticker", ticker)
            .eq("source", "backfill")
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)
    except Exception:
        return None


def mature_model_predictions_batch(updates: list[dict]) -> bool:
    """Write maturation results for already-existing `model_predictions`
    rows, keyed by `id`. Each dict must have at minimum `id` and
    `realized_value`; `scored_at`/`abs_error`/`baseline_abs_error` are
    written when present in the dict, left untouched otherwise. Never
    raises — a capture failure here must never break the cron's other
    steps; the caller (`cron_runner`) also wraps its own call in
    try/except for defense in depth."""
    if is_readonly():
        return False
    if not updates:
        return False
    if not has_db():
        return False
    try:
        for u in updates:
            row_id = u.get("id")
            if row_id is None:
                continue
            patch = {k: v for k, v in u.items() if k != "id"}
            if not patch:
                continue
            _client().table("model_predictions").update(patch).eq("id", row_id).execute()
        return True
    except Exception as e:
        import warnings
        warnings.warn(f"mature_model_predictions_batch: {e}")
        return False


# ── Cron heartbeat (System Proprioception Phase 1 — pipeline liveness) ─────────
# One row per cron lane, upserted at the END of every lane invocation by
# cron_runner.main(). OBSERVABILITY ONLY — nothing reads this for a decision,
# gate, recommendation, or composite; it exists so the owner-only 🩺 System
# Trust page can prove each Railway cron lane is actually firing (the whole
# motivation for the GitHub Actions → Railway migration was execution
# certainty). One-time DDL (apply once via the Supabase dashboard, same
# convention as model_predictions / daily_regime — see docs/architecture.md):
#
#   create table if not exists cron_heartbeat (
#     lane        text primary key,
#     last_run_at timestamptz not null,
#     status      text not null default 'ok',   -- 'ok' | 'failed'
#     detail      text,
#     updated_at  timestamptz not null default now()
#   );
#   alter table cron_heartbeat enable row level security;
#   create policy "cron_heartbeat service_role all" on cron_heartbeat
#     for all to service_role using (true) with check (true);

def save_cron_heartbeat(lane: str, status: str = "ok",
                        detail: str | None = None, ran_at: str | None = None) -> bool:
    """Upsert one heartbeat row for a cron `lane` (one row per lane, last run
    wins). `ran_at` is an ISO-8601 string — cron_runner passes its own ET-aware
    `now_et.isoformat()`; when omitted, an ET-aware timestamp is used (never a
    naive `utcnow()`). Never raises: a pre-DDL "relation does not exist" is
    caught like any other failure and reported via the return value, because a
    heartbeat write must never break the lane's real work. Read-only viewers
    no-op (only the headless cron writes heartbeats; the app only reads them)."""
    if is_readonly():
        return False
    if not has_db() or not lane:
        return False
    try:
        if ran_at is None:
            import pytz as _pytz
            from datetime import datetime as _dt
            ran_at = _dt.now(_pytz.timezone("America/New_York")).isoformat()
        row = {
            "lane":        str(lane),
            "last_run_at": ran_at,
            "status":      str(status or "ok"),
            "detail":      (str(detail)[:200] if detail else None),
        }
        _client().table("cron_heartbeat").upsert(row, on_conflict="lane").execute()
        return True
    except Exception:
        return False


def load_cron_heartbeats() -> "list[dict] | None":
    """Return every cron-lane heartbeat row, or `None` (the offline sentinel)
    on ANY failure — no credentials, the table not yet created (pre-DDL), or a
    raised query error. `None` = "heartbeat store unavailable" (the 🩺 System
    Trust page shows liveness as unverifiable), kept distinct from `[]` (table
    exists, no lane has written yet)."""
    if not has_db():
        return None
    try:
        rows = _client().table("cron_heartbeat").select("*").execute().data
        return rows or []
    except Exception:
        return None


def save_watchlist(tickers: list[str]) -> bool:
    """Atomic-ish replace via upsert + sweep — same pattern as save_holdings.

    The watchlist table already has UNIQUE(ticker), so the upsert is safe.
    Building the deduped list first means malformed input never reaches the
    DB; the sweep at the end is idempotent.
    """
    if is_readonly(): return False  # read-only viewer: no-op
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = t.strip().upper()
        if u and u not in seen:
            cleaned.append(u)
            seen.add(u)
    if not has_db():
        return False
    try:
        client = _client()
        if cleaned:
            client.table("watchlist").upsert(
                [{"ticker": t} for t in cleaned], on_conflict="ticker"
            ).execute()
        sweep = client.table("watchlist").delete()
        if cleaned:
            sweep = sweep.not_.in_("ticker", cleaned)
        else:
            sweep = sweep.neq("ticker", "")
        sweep.execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error("⛔ Failed to save watchlist — see Data Health tab for details.")
        return False


# ── Manual stops ──────────────────────────────────────────────────────────────
# User-set stop overrides recorded when the Brief's "raise stop" recommendation
# is actioned. build_portfolio_df merges these on top of the ATR-derived stop
# so all downstream consumers (Brief, Analysis, Scorecard, risk advisor) see
# the user's chosen value rather than the computed default. Without this,
# the same recommendation re-fires every render because the system has no
# record the user acted on it.

def load_manual_stops() -> dict:
    """Return {ticker: {"stop_price", "set_at", "note", "source_action"}}.

    Empty dict when DB is offline or table empty. Failure to read returns
    empty rather than raising so the rest of the app keeps working with
    ATR-derived stops — graceful degradation, not silent gate disable.
    """
    if not has_db():
        return {}
    try:
        rows = (
            _client().table("manual_stops")
            .select("ticker,stop_price,set_at,note,source_action")
            .execute().data
        )
        out: dict = {}
        for r in (rows or []):
            t = str(r.get("ticker", "")).upper().strip()
            try:
                sp = float(r.get("stop_price") or 0)
            except (TypeError, ValueError):
                continue
            if not t or sp <= 0:
                continue
            out[t] = {
                "stop_price":    sp,
                "set_at":        r.get("set_at"),
                "note":          r.get("note") or "",
                "source_action": r.get("source_action") or "",
            }
        return out
    except Exception:
        return {}


def save_manual_stop(ticker: str, stop_price: float,
                     note: str | None = None,
                     source_action: str | None = None) -> bool:
    """Upsert a manual stop for the ticker. Returns True on success."""
    if is_readonly(): return False  # read-only viewer: no-op
    t = str(ticker or "").upper().strip()
    try:
        sp = float(stop_price)
    except (TypeError, ValueError):
        return False
    if not t or sp <= 0 or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "ticker":        t,
            "stop_price":    sp,
            "set_at":        datetime.now(timezone.utc).isoformat(),
            "note":          (note or "").strip() or None,
            "source_action": (source_action or "").strip() or None,
        }
        _client().table("manual_stops").upsert(record, on_conflict="ticker").execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error(f"⛔ Failed to save manual stop for {t} — see Data Health tab for details.")
        return False


def clear_manual_stop(ticker: str) -> bool:
    """Remove the manual stop override for the ticker (revert to ATR)."""
    if is_readonly(): return False  # read-only viewer: no-op
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return False
    try:
        _client().table("manual_stops").delete().eq("ticker", t).execute()
        return True
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=str(e)[:120])
        st.error(f"⛔ Failed to clear manual stop for {t} — see Data Health tab for details.")
        return False


def load_fundamentals_cache(ticker: str) -> dict | None:
    """Return the last-known-good fundamentals for a ticker, or None.

    Shape: {"financials": {...}, "fetched_at": "<iso>"}. Returns None when the
    DB is offline, the table doesn't exist yet, or there's no row — so the
    feature degrades to current behaviour (live-only) until the table is
    created. Never raises: a cache miss must not break the data path.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client().table("fundamentals_cache")
            .select("financials,fetched_at")
            .eq("ticker", t).limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        fin = row.get("financials")
        if not isinstance(fin, dict) or not fin:
            return None
        return {"financials": fin, "fetched_at": row.get("fetched_at")}
    except Exception:
        return None



def save_fundamentals_cache(ticker: str, financials: dict) -> bool:
    """Upsert the last-known-good fundamentals for a ticker (write-through on a
    successful live fetch). Best-effort: a failure (e.g. table not created yet)
    is swallowed so it never disrupts the data path."""
    t = str(ticker or "").upper().strip()
    if not t or not financials or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "ticker":     t,
            "financials": _json_safe(financials),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _client().table("fundamentals_cache").upsert(record, on_conflict="ticker").execute()
        return True
    except Exception:
        return False


def load_sector_cache(ticker: str) -> str | None:
    """Return the last-known sector for a ticker, or None.

    Sector is a near-static attribute, so a cached value is always safe to reuse
    as the fallback when a live yfinance `.info` fetch comes back sparse (Yahoo's
    recurring thin-.info days). Without it, an unmapped holding collapses to the
    "Other" catch-all AND drops out of the sector-concentration gate (which
    excludes "Other" by design). Returns None when the DB is offline, the table
    doesn't exist yet, or there's no row — so the app degrades to live-only
    behaviour until the table is created. Never raises."""
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client().table("sector_cache")
            .select("sector")
            .eq("ticker", t).limit(1).execute().data
        )
        if not rows:
            return None
        sec = rows[0].get("sector")
        return sec if (isinstance(sec, str) and sec.strip()) else None
    except Exception:
        return None


def save_sector_cache(ticker: str, sector: str) -> bool:
    """Upsert the last-known sector for a ticker (write-through when a live .info
    fetch returned a real sector). System data, not user data → NOT _READONLY-
    gated (mirrors save_fundamentals_cache — a read-only viewer still benefits
    from a warm cache). Best-effort: a failure (e.g. table not created yet) is
    swallowed so it never disrupts the data path."""
    t   = str(ticker or "").upper().strip()
    sec = str(sector or "").strip()
    if not t or not sec or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "ticker":     t,
            "sector":     sec,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _client().table("sector_cache").upsert(record, on_conflict="ticker").execute()
        return True
    except Exception:
        return False


# ── LLM sentiment day-cache (ticker × date) ─────────────────────────────────
# Persists the bidirectional LLM-rescored headlines and their avg_sent so the
# Streamlit app and the headless cron runner always read the same composite
# for a given ticker on a given UTC day. System cache → NOT _READONLY-gated
# (mirrors sector_cache). One row per (ticker, score_date).

def load_sentiment_llm_cache(ticker: str, score_date: str) -> dict | None:
    """Return {"headlines": [...], "avg_sent": float} for ticker/date, or None.

    None means cache miss (table not created yet, DB offline, or no row for today)
    — caller should run the LLM and then call save_sentiment_llm_cache. Never raises.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client().table("sentiment_llm_cache")
            .select("headlines,avg_sent")
            .eq("ticker", t)
            .eq("score_date", score_date)
            .limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row.get("headlines"), list):
            return None
        return {"headlines": row["headlines"], "avg_sent": float(row["avg_sent"])}
    except Exception:
        return None


def save_sentiment_llm_cache(
    ticker: str, score_date: str, headlines: list, avg_sent: float
) -> bool:
    """Upsert LLM-rescored headlines + avg_sent for ticker/date.

    Best-effort — a failure (table not created yet, RLS issue) is swallowed so
    it never disrupts the load_bundle success path. The feature degrades to
    per-request VADER on every failure; the next run retries the DB write.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        import json as _json
        _client().table("sentiment_llm_cache").upsert(
            {
                "ticker":     t,
                "score_date": score_date,
                "headlines":  _json.loads(_json.dumps(headlines)),  # ensure JSON-safe
                "avg_sent":   round(float(avg_sent), 6),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # no on_conflict: PostgREST uses PRIMARY KEY (ticker, score_date) automatically
        ).execute()
        return True
    except Exception:
        return False


# ── Thesis Red Team Agent — erosion score day-cache (ticker × date) ──────────
# Persists the daily quantitative erosion score (Phase 1) and — in Phase 2 —
# optional Haiku counter-evidence bullets. System cache → NOT _READONLY-gated
# (same classification as sentiment_llm_cache / sector_cache). One row per
# (ticker, score_date); score_date is the ET date string from _today_et().
# signals_snapshot MUST include composite_today for the 5-session-ago delta
# lookback used in future rows. Degrades gracefully when table is absent.

def load_thesis_erosion_cache(ticker: str, score_date: str) -> dict | None:
    """Return cached erosion result for (ticker, score_date) or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and call save_thesis_erosion_cache.
    Never raises.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("thesis_erosion_cache")
            .select("erosion_score,erosion_label,counter_evidence,signals_snapshot")
            .eq("ticker", t)
            .eq("score_date", score_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def load_thesis_erosion_cache_batch(tickers: list[str], score_date: str) -> dict[str, dict]:
    """Return {ticker: cached_row} for every ticker with a scored row on score_date.

    Batched sibling of load_thesis_erosion_cache() — one round-trip instead of one
    per ticker. Added 2026-07-29 (audit H9) to close an N-Supabase-calls-per-Home-
    rerun loop in the "Thesis Under Pressure" Daily Brief annotation (F-196 Phase 3).
    Tickers with no scored row today are simply absent from the returned dict.
    Never raises; returns {} on any failure or if the DB is offline.
    """
    ts = sorted({str(t or "").upper().strip() for t in (tickers or []) if str(t or "").strip()})
    if not ts or not has_db():
        return {}
    try:
        rows = (
            _client()
            .table("thesis_erosion_cache")
            .select("ticker,erosion_score,erosion_label,counter_evidence,signals_snapshot")
            .in_("ticker", ts)
            .eq("score_date", score_date)
            .execute()
            .data
        )
        return {r["ticker"]: r for r in (rows or []) if r.get("ticker")}
    except Exception:
        return {}


def save_thesis_erosion_cache(
    ticker, score_date, erosion_score, erosion_label, signals_snapshot,
    counter_evidence=None
):
    """Upsert erosion score row. Best-effort — never raises.

    counter_evidence is None in Phase 1; populated with validated Haiku
    bear-case bullets in Phase 2. signals_snapshot must include
    composite_today for the 5-session lookback used by future rows.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return
    try:
        _client().table("thesis_erosion_cache").upsert({
            "ticker":           t,
            "score_date":       score_date,
            "erosion_score":    erosion_score,
            "erosion_label":    erosion_label,
            "counter_evidence": counter_evidence,
            "signals_snapshot": signals_snapshot,
        }).execute()
    except Exception:
        pass


# ── Alert-cron dedup state (single row, id=1) ────────────────────────────────
# Used ONLY by the headless email-alerts cron (exit-discipline Phase 3) to (a)
# fire at most once per ET trading day and (b) skip an email whose protective set
# is unchanged since the last send. System state, NOT user data → not _READONLY-
# gated (the cron runs outside the app anyway). Degrades to "always send" if the
# table is absent — the feature works before the DDL, just without dedup.

def load_alert_state(row_id: int = 1) -> dict | None:
    """Return {"last_emailed_date": "<YYYY-MM-DD>", "last_fingerprint": "<hex>"}
    or None (DB offline / table missing / no row). `row_id` selects the cron lane:
    1 = pre-market protective run, 2 = EOD pullback run (independent dedup, same
    table — no extra DDL). Never raises."""
    if not has_db():
        return None
    try:
        rows = (
            _client().table("alert_state")
            .select("last_emailed_date,last_fingerprint")
            .eq("id", row_id).limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "last_emailed_date": row.get("last_emailed_date"),
            "last_fingerprint":  row.get("last_fingerprint"),
        }
    except Exception:
        return None


def save_alert_state(emailed_date: str, fingerprint: str, row_id: int = 1) -> bool:
    """Upsert a cron lane's dedup state (row_id 1=protective, 2=EOD pullback).
    Best-effort; swallows failures."""
    if not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "id":                row_id,
            "last_emailed_date": str(emailed_date),
            "last_fingerprint":  str(fingerprint),
            "updated_at":        datetime.now(timezone.utc).isoformat(),
        }
        _client().table("alert_state").upsert(record, on_conflict="id").execute()
        return True
    except Exception:
        return False


def load_account_cash() -> dict | None:
    """Return {"cash_balance": float, "note": str|None, "updated_at": str} for the
    single account-cash row, or None (DB offline / table missing / no row set yet).
    None means "cash unknown" — the app then behaves exactly as today (equity-only).
    Never raises."""
    if not has_db():
        return None
    try:
        rows = (
            _client().table("account_cash")
            .select("cash_balance,note,updated_at")
            .eq("id", 1).limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "cash_balance": float(row.get("cash_balance") or 0.0),
            "note":         row.get("note"),
            "updated_at":   row.get("updated_at"),
        }
    except Exception:
        return None


def save_account_cash(cash_balance: float, note: str | None = None) -> bool:
    """Upsert the single account-cash row (id=1). USER data → honours the
    read-only viewer guard. Best-effort; swallows failures."""
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "id":           1,
            "cash_balance": float(cash_balance),
            "note":         (str(note) if note else None),
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }
        _client().table("account_cash").upsert(record, on_conflict="id").execute()
        return True
    except Exception:
        return False


def load_account_flows() -> list[dict]:
    """Return the external cash-flow ledger [{id, flow_date, flow_type, amount,
    note}], oldest-first, or [] (DB offline / table missing / no rows). [] means
    "no flows yet" → the growth view stays hidden. Never raises."""
    if not has_db():
        return []
    try:
        rows = (
            _client().table("account_flows")
            .select("id,flow_date,flow_type,amount,note")
            .order("flow_date", desc=False).order("id", desc=False)
            .execute().data
        ) or []
        out = []
        for r in rows:
            out.append({
                "id":        r.get("id"),
                "flow_date": r.get("flow_date"),
                "flow_type": r.get("flow_type"),
                "amount":    float(r.get("amount") or 0.0),
                "note":      r.get("note"),
            })
        return out
    except Exception:
        return []


def add_account_flow(flow_date: str, flow_type: str, amount: float,
                     note: str | None = None) -> bool:
    """Insert one cash-flow row (baseline / deposit / withdrawal). `amount` is
    stored POSITIVE (the type carries the sign). USER data → honours the
    read-only viewer guard. Best-effort; swallows failures."""
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        record = {
            "flow_date": str(flow_date),
            "flow_type": str(flow_type),
            "amount":    abs(float(amount)),
            "note":      (str(note) if note else None),
        }
        _client().table("account_flows").insert(record).execute()
        return True
    except Exception:
        return False


def delete_account_flow(flow_id) -> bool:
    """Delete one cash-flow row by id. USER data → honours the read-only viewer
    guard. Best-effort; swallows failures."""
    if is_readonly(): return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        _client().table("account_flows").delete().eq("id", flow_id).execute()
        return True
    except Exception:
        return False


# ── Multi-Agent Debate — Bull vs Bear day-cache (ticker × debate_type × date) ─
# Persists structured debate results (transcript + Judge verdict) per
# (ticker, debate_type, debate_date). debate_type = 'entry' (Phase 1, Grow
# Today) | 'exit' (Phase 2, Exit Advisor). debate_date is the ET ISO date
# string from _today_et(). System cache → NOT _READONLY-gated (same
# classification as sentiment_llm_cache / sector_cache). Degrades gracefully
# when table is absent: load returns None, save no-ops.

def load_debate_cache(ticker: str, debate_type: str, debate_date: str) -> dict | None:
    """Return cached debate result for (ticker, debate_type, debate_date) or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date/type) — caller should run the debate and call save_debate_cache.
    Never raises.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("debate_cache")
            .select("verdict,key_dispute,bull_case_score,bear_case_score,grounded,transcript")
            .eq("ticker", t)
            .eq("debate_type", debate_type)
            .eq("debate_date", debate_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_debate_cache(
    ticker, debate_type, debate_date,
    verdict, key_dispute,
    bull_case_score, bear_case_score,
    grounded, transcript, corpus_snapshot,
):
    """Upsert debate result row. Best-effort — never raises.

    transcript is the [{round, agent, text}] list from run_debate().
    corpus_snapshot is the build_entry_corpus() dict persisted for audit.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return
    try:
        _client().table("debate_cache").upsert({
            "ticker":          t,
            "debate_type":     debate_type,
            "debate_date":     debate_date,
            "verdict":         verdict,
            "key_dispute":     key_dispute,
            "bull_case_score": bull_case_score,
            "bear_case_score": bear_case_score,
            "grounded":        grounded,
            "transcript":      transcript,
            "corpus_snapshot": corpus_snapshot,
        }).execute()
    except Exception:
        pass


def load_debate_verdicts(tickers: list[str]) -> pd.DataFrame:
    """Return every debate_cache row for the given tickers (both debate_types,
    all dates) — used by D3 Signal Coherence Auditor to pick the most recent
    verdict per ticker across entry/exit debates. Empty DataFrame on any
    failure or empty input (graceful degradation, never raises)."""
    cols = ["ticker", "debate_type", "debate_date", "verdict"]
    empty = pd.DataFrame(columns=cols)
    if not has_db() or not tickers:
        return empty
    try:
        upper_tickers = [t.strip().upper() for t in tickers]
        rows = (
            _client()
            .table("debate_cache")
            .select("ticker,debate_type,debate_date,verdict")
            .in_("ticker", upper_tickers)
            .order("debate_date", desc=True)
            .execute()
            .data
        )
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def load_all_debates(limit: int = 200) -> list[dict]:
    """Return up to `limit` most recent debate_cache rows, most recent first
    (debate_date, then created_at as a tiebreak within the same date), for
    the AI Insights Debate Log tab (Multi-Agent Debate Phase 3).

    Excludes corpus_snapshot (large audit-only payload, not needed for
    display — same exclusion load_debate_cache already makes for its own
    single-row read). Never raises — degrades to [] on any failure (table
    absent, DB offline), which the tab renders as "no debates yet."
    """
    if not has_db():
        return []
    try:
        rows = (
            _client()
            .table("debate_cache")
            .select("ticker,debate_type,debate_date,verdict,key_dispute,"
                     "bull_case_score,bear_case_score,grounded,transcript")
            .order("debate_date", desc=True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return rows or []
    except Exception:
        return []


# ── Structural Vulnerability Scanner — daily portfolio-level cache ──────────
# Persists the Blast Radius Map + generated narrative for one ET calendar day.
# ONE row per scan_date (no ticker key — portfolio-wide synthesis, not a
# per-position score). System cache → NOT _READONLY-gated (same
# classification as debate_cache / sentiment_llm_cache). Degrades gracefully
# when table is absent: load returns None, save no-ops.

def load_structural_scan_cache(scan_date: str) -> dict | None:
    """Return cached structural scan result for scan_date or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and show the generate button.
    Never raises.
    """
    if not scan_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("structural_scan_cache")
            .select("narrative,blast_radius,cluster_snapshot,risk_budget_snapshot")
            .eq("scan_date", scan_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_structural_scan_cache(scan_date, narrative, blast_radius, cluster_snapshot, risk_budget_snapshot):
    """Upsert structural scan row. Best-effort — never raises.

    Caller must only invoke this when narrative is non-None (a successful
    Haiku call) — never cache a failed/empty result.
    """
    if not scan_date or not has_db():
        return
    try:
        _client().table("structural_scan_cache").upsert({
            "scan_date":            scan_date,
            "narrative":            narrative,
            "blast_radius":         blast_radius,
            "cluster_snapshot":     cluster_snapshot,
            "risk_budget_snapshot": risk_budget_snapshot,
        }).execute()
    except Exception:
        pass


def load_structural_scan_baseline(as_of_date: str) -> dict | None:
    """Return the most recent structural_scan_cache row with scan_date <=
    as_of_date, or None (no scan has ever run, table absent, or DB offline).

    Deliberately <=, not < -- once today's own narrative has been generated,
    today's own snapshot IS the correct comparison baseline (comparing live
    clusters against themselves correctly yields zero new pairs, clearing the
    Home "Structural alert" banner for the day). Using strict < would keep
    comparing against a stale prior day even after the user has reviewed
    today's scan. See docs/plans/structural-scanner-phase2.md.

    Only cluster_snapshot + scan_date are needed by Phase 2 -- narrative and
    the other JSONB columns from that historical row are not read here.
    Never raises.
    """
    if not as_of_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("structural_scan_cache")
            .select("scan_date,cluster_snapshot")
            .lte("scan_date", as_of_date)
            .order("scan_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ── Regime-Aware Adversarial Stress Testing — daily portfolio-level cache ───
# Persists the compound scenario narrative + indicator watchlist for one ET
# calendar day. ONE row per scan_date (no ticker key — portfolio-wide
# synthesis, not a per-position score). System cache → NOT _READONLY-gated
# (same classification as debate_cache / sentiment_llm_cache /
# structural_scan_cache). Degrades gracefully when table is absent: load
# returns None, save no-ops.

def load_regime_scenario_cache(scan_date: str) -> dict | None:
    """Return cached regime scenario result for scan_date or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and show the generate button.
    Never raises.
    """
    if not scan_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("regime_scenario_cache")
            .select("scenario_narrative,indicator_watchlist,blast_radius_snapshot,regime_snapshot,cross_asset_snapshot")
            .eq("scan_date", scan_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_regime_scenario_cache(scan_date, scenario_narrative, indicator_watchlist,
                                blast_radius_snapshot, regime_snapshot, cross_asset_snapshot):
    """Upsert regime scenario row. Best-effort — never raises.

    Caller must only invoke this when scenario_narrative is non-None (a
    successful Haiku call) — never cache a failed/empty result.
    """
    if not scan_date or not has_db():
        return
    try:
        _client().table("regime_scenario_cache").upsert({
            "scan_date":             scan_date,
            "scenario_narrative":    scenario_narrative,
            "indicator_watchlist":   indicator_watchlist,
            "blast_radius_snapshot": blast_radius_snapshot,
            "regime_snapshot":       regime_snapshot,
            "cross_asset_snapshot":  cross_asset_snapshot,
        }).execute()
    except Exception:
        pass


# ── Catalyst-Specific Stress — daily portfolio-level cache (D4) ────────────
# Persists the catalyst narrative + ranked candidates for one ET calendar day.
# ONE row per scan_date (no ticker key). System cache → NOT _READONLY-gated
# (same classification as regime_scenario_cache / structural_scan_cache).
# Degrades gracefully when table is absent: load returns None, save no-ops.

def load_catalyst_stress_cache(scan_date: str) -> dict | None:
    """Return cached catalyst stress result for scan_date or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and show the generate button.
    Never raises.
    """
    if not scan_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("catalyst_stress_cache")
            .select("narrative,ranked_snapshot,blast_radius_snapshot,clusters_snapshot")
            .eq("scan_date", scan_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_catalyst_stress_cache(scan_date, narrative, ranked_snapshot,
                                blast_radius_snapshot, clusters_snapshot):
    """Upsert catalyst stress row. Best-effort — never raises.

    Caller must only invoke this when narrative is non-None (a successful
    Haiku call) — never cache a failed/empty result.
    """
    if not scan_date or not has_db():
        return
    try:
        _client().table("catalyst_stress_cache").upsert({
            "scan_date":             scan_date,
            "narrative":             narrative,
            "ranked_snapshot":       ranked_snapshot,
            "blast_radius_snapshot": blast_radius_snapshot,
            "clusters_snapshot":     clusters_snapshot,
        }).execute()
    except Exception:
        pass


# ── Hidden Same-Bet Detector — daily portfolio-level cache (D1) ────────────
# Persists the thesis-cluster classification result for one ET calendar day.
# ONE row per scan_date (no ticker key — portfolio-wide synthesis). System
# cache → NOT _READONLY-gated (same classification as debate_cache /
# structural_scan_cache / regime_scenario_cache — a recomputable analytical
# narrative, not user-authored data). Degrades gracefully when table is
# absent: load returns None, save no-ops.

def load_thesis_cluster_cache(scan_date: str) -> dict | None:
    """Return cached thesis-cluster result for scan_date or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and show the generate button.
    Never raises.
    """
    if not scan_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("thesis_cluster_cache")
            .select("clusters,thesis_snapshot,truncated")
            .eq("scan_date", scan_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_thesis_cluster_cache(scan_date, clusters, thesis_snapshot, truncated=False):
    """Upsert thesis-cluster row. Best-effort — never raises.

    Caller must only invoke this when the Haiku call succeeded (clusters is
    a list, possibly empty — "no shared assumption found" is a valid,
    cacheable result) — never cache a genuine failure (None from
    generate_thesis_clusters). truncated records whether any thesis text
    was cut for the prompt, so the render can note it consistently on both
    the generation and cache-hit paths (not just the moment of generation).
    """
    if not scan_date or not has_db():
        return
    try:
        _client().table("thesis_cluster_cache").upsert({
            "scan_date":       scan_date,
            "clusters":        clusters,
            "thesis_snapshot": thesis_snapshot,
            "truncated":       bool(truncated),
        }).execute()
    except Exception:
        pass


# ── Missed-Opportunity Pattern — daily portfolio-level cache (O1) ──────────
# Persists the pattern-detection result for one ET calendar day. ONE row per
# scan_date (no ticker key — portfolio-wide synthesis). System cache → NOT
# _READONLY-gated (same classification as debate_cache / structural_scan_cache
# / thesis_cluster_cache — a recomputable analytical narrative, not
# user-authored data). Degrades gracefully when table is absent: load
# returns None, save no-ops.

def load_missed_opportunity_cache(scan_date: str) -> dict | None:
    """Return cached missed-opportunity-pattern result for scan_date or None.

    None means cache miss (table not created yet, DB offline, or no row for
    this date) — caller should compute and show the generate button.
    Never raises.
    """
    if not scan_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("missed_opportunity_cache")
            .select("patterns,missed_snapshot")
            .eq("scan_date", scan_date)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def save_missed_opportunity_cache(scan_date, patterns, missed_snapshot):
    """Upsert missed-opportunity-pattern row. Best-effort — never raises.

    Caller must only invoke this when the Haiku call succeeded (patterns is
    a list, possibly empty — "no coherent pattern found" is a valid,
    cacheable result) — never cache a genuine failure (None from
    generate_missed_opportunity_patterns).
    """
    if not scan_date or not has_db():
        return
    try:
        _client().table("missed_opportunity_cache").upsert({
            "scan_date":       scan_date,
            "patterns":        patterns,
            "missed_snapshot": missed_snapshot,
        }).execute()
    except Exception:
        pass
