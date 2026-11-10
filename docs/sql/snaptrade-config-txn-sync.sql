-- =====================================================================
-- DRISHTA — broker transaction-sync freshness (bug register TJDate), chunk C2b DDL
-- Written 2026-11-10 (docs/plans/trade-entry-guard.md).
--
-- Purpose: record WHEN the `broker` cron last COMPLETED its transaction sync
-- (step 2: activities fetched, pending imports saved, linked ones closed), so the
-- Trade Journal can say whether an entry was compared with Robinhood's fills that
-- were synced AFTER the trade date, or only against a sync that may predate that
-- day's fills ("one freshness per number"). Unlike last_full_sync_at, which the
-- cron stamps on every pass whatever happened, this one is written ONLY when the
-- transaction sync finished cleanly.
--
-- Owner applies this manually in the Supabase SQL editor. Idempotent: safe to
-- re-run. No RLS change (the table keeps its service_role policy). Until it is
-- applied, the app reads "the time of the last completed broker sync couldn't be read" and
-- the cron's stamp fails quietly (a log line, never a lane failure).
-- =====================================================================

alter table public.snaptrade_config
    add column if not exists last_txn_sync_at timestamptz;

-- Verify: expect 1 row.
select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'snaptrade_config'
  and column_name = 'last_txn_sync_at';
