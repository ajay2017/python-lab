-- =====================================================================
-- DRISHTA — remember whether a fill time was typed (bug register RecTime), chunk C1 DDL
-- Written 2026-11-13 (docs/plans/fill-time-typed.md).
--
-- Purpose: when the Trade Journal fill time is left blank on a market-open day, the
-- stored `traded_at` is the moment Record was pressed, not the fill. Nothing on the
-- row says which it is, so a reader comparing the trade with a broker sync can count a
-- trade the sync already included a second time. This column records whether the
-- owner TYPED the fill time:
--     true  = typed by the owner (or set by an owner SQL repair with a real fill time)
--     false = not typed (blank -> record time or 16:00 anchor; any import; broker
--             prefill; SPLIT row)
--     null  = not recorded (a row written before this column existed, or saved before
--             this DDL was applied)
-- No default and no backfill: existing rows stay null and are read by the
-- microsecond fallback in `trade_time.normalize_traded_at`.
--
-- Owner applies this manually in the Supabase SQL editor. Idempotent: safe to
-- re-run. No RLS change (the table keeps its service_role policy).
--
-- HARD ORDERING RULE: apply this ALTER and run the verify query below BEFORE C2
-- (the chunk that makes the Trade Journal and the importers send
-- `fill_time_typed`) ships. C1 sends nothing, so until C2 nothing changes. But
-- once a record carries the field, a save against a table WITHOUT the column
-- fails with an error naming it, and `db.save_trade`'s retry then drops EVERY
-- optional column in the record (broker_txn_id, idempotency_key, asset_type,
-- situational_category, the premortem fields ...), not just this one. It saves
-- the trade but loses the broker dedup / link key and the double-submit guard.
-- `db.load_trades_or_none` reads the column as null when it is absent.
-- =====================================================================

alter table public.trades
    add column if not exists fill_time_typed boolean;

-- Verify (read-only): expect 1 row, data_type = boolean, is_nullable = YES.
select column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name = 'trades'
  and column_name = 'fill_time_typed';
