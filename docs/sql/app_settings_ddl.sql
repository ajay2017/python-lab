-- App Settings — reference_tables / reference_table_history DDL
-- Commit 1 of 3 (data layer + resolver + seed). See docs/plans/app-settings.md
-- for the full design, the redline, and the content-hash "snooze-button"
-- mechanism `stock_analyzer/db.py::save_reference_table` implements against
-- this schema.
--
-- APPLY BY HAND in the Supabase SQL Editor. This is not run automatically by
-- any migration tool or cron lane — same "ships inert until the DDL is
-- applied" convention as model_predictions / analyst_target_snapshots
-- (docs/architecture.md §6.31 / §6.23). `stock_analyzer/db.py`'s
-- load_reference_table / save_reference_table already degrade to the offline
-- sentinel (`None`) if this DDL has not been applied yet, so applying it is
-- safe at any time and does not require a coordinated deploy.
--
-- After applying, run `python scripts/seed_reference_tables.py` ONCE (by
-- hand, from a shell with the app's Supabase env vars set) to seed the three
-- v1 tables (sector_universe, discovery_universe, sector_candidates) from
-- their current hardcoded code lists. Nothing in the running app reads from
-- these tables yet in this commit — that is Commit 2.

-- ── reference_tables: one CURRENT row per named table ───────────────────────
-- `payload_hash` is the sha256 of the canonicalized payload
-- (stock_analyzer.reference_data.canonicalize) — the mechanism that makes
-- `as_of` a side effect of a REAL delta rather than an independently
-- settable date. `as_of` is stamped by the write on a genuine content
-- change only; it is never user-supplied and never touched on a no-op save.
CREATE TABLE IF NOT EXISTS reference_tables (
    name         TEXT PRIMARY KEY,        -- 'sector_universe' | 'discovery_universe' | 'sector_candidates'
    payload      JSONB NOT NULL,          -- the bucket/sector -> [tickers] mapping
    payload_hash TEXT NOT NULL,           -- sha256 of the canonicalized payload
    as_of        DATE NOT NULL,           -- STAMPED BY THE WRITE on a real change, never user-supplied
    updated_by   TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE reference_tables ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_reference_tables" ON reference_tables
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── reference_table_history: APPEND-ONLY audit trail ────────────────────────
-- Every accepted delta (a save that actually changed the canonicalized
-- payload) inserts one row here. Retention: keep all — a JSON roster is a
-- few KB and edits happen only a handful of times a year per table, so
-- pruning buys nothing for years (docs/plans/app-settings.md Q4). This is
-- what lets a future session answer "who changed what, when" for a mutable
-- scan/discovery/candidate universe the same way git history already does
-- for every investment threshold in constants.py.
CREATE TABLE IF NOT EXISTS reference_table_history (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    payload      JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    as_of        DATE NOT NULL,
    updated_by   TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reference_table_history_name
    ON reference_table_history(name, created_at DESC);

ALTER TABLE reference_table_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_reference_table_history" ON reference_table_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);
