-- held_stop_floors -- persisted held-stop floor (#26 C3c, chunk C3c-3)
-- (docs/plans/held-stop-floor-c3c-spec.md, section 1 "Storage").
-- One row per (ticker, episode_start): the last FULL-basis auto stop, so a day on
-- the short fallback price frame can never LOWER a stop (C1's "no trade in between,
-- stop(D+1) >= stop(D)" invariant). Written by the EOD lane ONLY
-- (db.save_held_stop_floors upserts on ticker,episode_start); the app and the
-- premarket lane only READ (db.load_held_stop_floors_or_none).
-- written_at is set by db.save_held_stop_floors on EVERY upsert (a merge-duplicates
-- upsert only updates the columns it sends), so it reads the last write time.
-- as_of = the ET session the stop was in force for (built from sessions < as_of).
-- policy_sig is the stop-rule regime version for this history table. Old-episode
-- rows are kept as an audit trail.
-- Until this is applied the reader returns None (behaviour = today's) and the EOD
-- writer fails the lane red with "apply docs/sql/held-stop-floors.sql", so apply
-- it BEFORE the wiring push.
-- The OWNER applies this in the Supabase SQL editor. Safe to re-run.

CREATE TABLE IF NOT EXISTS held_stop_floors (
    ticker           TEXT        NOT NULL
                     CHECK (ticker = upper(btrim(ticker)) AND ticker <> ''),
    episode_start    DATE        NOT NULL,
    as_of            DATE        NOT NULL,
    stop             NUMERIC     NOT NULL CHECK (stop > 0 AND stop < 'Infinity'),  -- NaN/Infinity pass a bare > 0
    stop_set_on      DATE        NOT NULL,  -- the first as_of at which this value was reached
    binding          TEXT        NOT NULL
                     CHECK (binding IN ('entry', 'trailing', 'ratchet')),
    ratchet_label    TEXT,
    first_buy_price  NUMERIC     CHECK (first_buy_price > 0 AND first_buy_price < 'Infinity'),  -- NULL for a SPLIT-seeded episode
    split_ratio      NUMERIC     NOT NULL CHECK (split_ratio > 0 AND split_ratio < 'Infinity'),
    last_buy         DATE,
    avg_cost         NUMERIC     NOT NULL CHECK (avg_cost > 0 AND avg_cost < 'Infinity'),
    ref_session      DATE        NOT NULL,
    ref_close        NUMERIC     NOT NULL CHECK (ref_close > 0 AND ref_close < 'Infinity'),
    bind_session     DATE        NOT NULL,
    bind_close       NUMERIC     NOT NULL CHECK (bind_close > 0 AND bind_close < 'Infinity'),
    policy_sig       TEXT        NOT NULL,
    source           TEXT        NOT NULL
                     CHECK (source IN ('cron:eod')),
    written_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, episode_start),
    CHECK (ref_session < as_of),
    CHECK (episode_start < as_of),
    CHECK (stop_set_on <= as_of)
);

ALTER TABLE held_stop_floors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all (service role)" ON public.held_stop_floors;
CREATE POLICY "Allow all (service role)" ON public.held_stop_floors
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify (expect: relrowsecurity = true, 1 policy row, service_role_can_insert = true,
-- service_role_can_update = true, 0 floors before the first EOD write):
SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'held_stop_floors';
SELECT policyname, roles, cmd FROM pg_policies WHERE tablename = 'held_stop_floors';
SELECT has_table_privilege('service_role', 'public.held_stop_floors', 'INSERT') AS service_role_can_insert;
SELECT has_table_privilege('service_role', 'public.held_stop_floors', 'UPDATE') AS service_role_can_update;  -- the upsert needs UPDATE
SELECT COUNT(*) AS floors FROM held_stop_floors;
