-- new_position_budget_allocations -- daily new-position budget engine, chunk 2
-- (docs/plans/daily-new-position-budget.md, "Cross-surface, cross-time rule").
-- One row per (ET budget date, ticker): "a size was pushed to the owner today".
-- Written by push surfaces (cron emails); the app only READS. FIRST WRITER WINS
-- (db.save_budget_allocations upserts with ignore_duplicates=True).
-- The OWNER applies this in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS new_position_budget_allocations (
    budget_date      DATE        NOT NULL,
    ticker           TEXT        NOT NULL,
    dollars          NUMERIC     NOT NULL CHECK (dollars > 0 AND dollars < 'Infinity'),  -- NaN/Infinity pass a bare > 0
    shares           NUMERIC,
    price            NUMERIC,
    composite_score  NUMERIC,
    source           TEXT        NOT NULL
                     CHECK (source IN ('scan:morning', 'scan:watchlist', 'intraday')),
    basis_dollars    NUMERIC,
    budget_pct       NUMERIC,    -- DAILY_NEW_POSITION_BUDGET_PCT frozen at write time
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (budget_date, ticker)
);

ALTER TABLE new_position_budget_allocations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all (service role)" ON public.new_position_budget_allocations;
CREATE POLICY "Allow all (service role)" ON public.new_position_budget_allocations
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify (expect: relrowsecurity = true, 1 policy row, 0 allocations before first push):
SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'new_position_budget_allocations';
SELECT policyname, roles, cmd FROM pg_policies WHERE tablename = 'new_position_budget_allocations';
SELECT has_table_privilege('service_role', 'public.new_position_budget_allocations', 'INSERT') AS service_role_can_insert;
SELECT COUNT(*) AS allocations FROM new_position_budget_allocations;
