-- held_stop_carry -- carried held stop (SlidingStop C2)
-- (docs/plans/sliding-stop.md, section 6 "Data and DB").
-- One row per held ticker (PRIMARY KEY ticker): the EFFECTIVE auto stop of the name
-- at the last EOD, so a name the journal can't date (basis partial / prior_session)
-- never has its stop lowered by a day that chases a falling price. The EOD overwrites
-- the row every night; a sold position's row is swept by the same lane. Written by
-- the EOD lane ONLY (C3: db.save_held_stop_carry upserts on ticker, db.delete_held_stop_carry
-- sweeps); the app and the premarket lane only READ (db.load_held_stop_carry_or_none).
-- written_at is set by db.save_held_stop_carry on EVERY upsert (a merge-duplicates
-- upsert only updates the columns it sends), so it reads the last write time.
-- as_of = the ET session the stop was in force for (built from sessions < as_of);
-- ref_session = the last completed session before as_of that the stop was built from
-- (always < as_of: the writer skips any row whose reference bar is not before as_of).
-- A row dated as_of is valid to the reader (the stop in force all of that day).
-- source_basis = the stop's basis when written (full / partial / prior_session).
-- journal_buy_count / journal_buy_max_id = the journal fingerprint (BUY + SPLIT rows
-- of the ticker); avg_cost = the holdings average cost; a change in either, or in
-- policy_sig / carry_sig (the stop-rule and carry-rule versions for this history
-- table), lapses the carry.
-- Until this is applied the reader returns None (behaviour = today's) and C3's EOD
-- writer fails the lane red with "apply docs/sql/held-stop-carry.sql". Apply it
-- BEFORE the C2 push: C2 registers the table in 🩺 System Trust ②
-- (system_health._Store), which grades a missing table "down", and Home then shows
-- a false "🔴 pipeline check failing — an expected data store is missing".
-- The OWNER applies this in the Supabase SQL editor. Safe to re-run.

CREATE TABLE IF NOT EXISTS held_stop_carry (
    ticker             TEXT        PRIMARY KEY
                       CHECK (ticker = upper(btrim(ticker)) AND ticker <> ''),
    as_of              DATE        NOT NULL,
    stop               NUMERIC     NOT NULL CHECK (stop > 0 AND stop < 'Infinity'),  -- NaN/Infinity pass a bare > 0
    stop_set_on        DATE        NOT NULL,  -- the first as_of at which this value was reached
    source_basis       TEXT        NOT NULL
                       CHECK (source_basis IN ('full', 'partial', 'prior_session')),
    binding            TEXT        NOT NULL
                       CHECK (binding IN ('entry', 'trailing', 'ratchet', 'prior_session')),
    ratchet_label      TEXT,
    avg_cost           NUMERIC     NOT NULL CHECK (avg_cost > 0 AND avg_cost < 'Infinity'),
    journal_buy_count  BIGINT      NOT NULL CHECK (journal_buy_count >= 0),
    journal_buy_max_id BIGINT      NOT NULL CHECK (journal_buy_max_id >= 0),
    ref_session        DATE        NOT NULL,
    ref_close          NUMERIC     NOT NULL CHECK (ref_close > 0 AND ref_close < 'Infinity'),
    bind_session       DATE        NOT NULL,
    bind_close         NUMERIC     NOT NULL CHECK (bind_close > 0 AND bind_close < 'Infinity'),
    policy_sig         TEXT        NOT NULL,
    carry_sig          TEXT        NOT NULL,
    source             TEXT        NOT NULL
                       CHECK (source IN ('cron:eod')),
    written_at         TIMESTAMPTZ DEFAULT NOW(),
    CHECK (ref_session < as_of),
    CHECK (stop_set_on <= as_of),
    CHECK (ratchet_label IS NULL OR binding = 'ratchet')
);

ALTER TABLE held_stop_carry ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all (service role)" ON public.held_stop_carry;
CREATE POLICY "Allow all (service role)" ON public.held_stop_carry
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify (expect: relrowsecurity = true, 1 policy row, service_role_can_insert = true,
-- service_role_can_update = true, service_role_can_delete = true, 0 carried stops
-- before the first EOD write):
SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'held_stop_carry';
SELECT policyname, roles, cmd FROM pg_policies WHERE tablename = 'held_stop_carry';
SELECT has_table_privilege('service_role', 'public.held_stop_carry', 'INSERT') AS service_role_can_insert;
SELECT has_table_privilege('service_role', 'public.held_stop_carry', 'UPDATE') AS service_role_can_update;  -- the upsert needs UPDATE
SELECT has_table_privilege('service_role', 'public.held_stop_carry', 'DELETE') AS service_role_can_delete;  -- the sold-position sweep needs DELETE
SELECT COUNT(*) AS carried_stops FROM held_stop_carry;
