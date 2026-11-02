-- =====================================================================
-- DRISHTA — account snapshot provenance (bug register N1), chunk C1 DDL
-- Written 2026-11-01 (docs/plans/account-snapshot-repair.md).
--
-- Purpose: record WHICH journal book each account_daily_snapshots row was
-- built from, so a trade that reaches the journal after the row was written
-- can be detected and the row repaired:
--   journal_max_trade_id  highest trades.id the EOD run had seen (NULL = legacy row)
--   book_positions        {TICKER: {"shares": n, "close": px}} behind gross_book
--   original_gross_book   gross_book before the FIRST repair (set once)
--   repaired_at           when the row was last repaired (NULL = never)
--
-- Owner applies this manually in the Supabase SQL editor. Idempotent: safe
-- to re-run. Until applied, db.save_account_daily_snapshot drops these four
-- keys and retries, so the EOD write still lands (without provenance).
-- =====================================================================

alter table public.account_daily_snapshots
    add column if not exists journal_max_trade_id bigint,
    add column if not exists book_positions       jsonb,
    add column if not exists original_gross_book  numeric,
    add column if not exists repaired_at          timestamptz;

-- Verify: expect 4 rows.
select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'account_daily_snapshots'
  and column_name in ('journal_max_trade_id', 'book_positions',
                      'original_gross_book', 'repaired_at')
order by column_name;
