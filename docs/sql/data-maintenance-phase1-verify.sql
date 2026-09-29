-- =====================================================================
-- DRISHTA — Database Maintenance Framework · Phase 1 build verification
-- Plan: docs/plans/data-maintenance-framework.md   ·   Written 2026-09-28
--
-- READ-ONLY. A single SELECT against information_schema. Nothing writes,
-- updates, deletes, or touches an app table. Safe to run against production.
--
-- PURPOSE: the Phase 1 implementation assumed account_flows has a
-- `created_at timestamptz not null default now()` column (based on the
-- original CREATE TABLE comment in stock_analyzer/db.py, ~line 416-423),
-- and built the add_account_flow write-time dedup guard around it. Phase
-- 0's own SQL pack never actually checked this column exists live — it
-- checked indexes (Q3), not the full column list. This confirms it before
-- the build ships.
--
-- READ IT AS: if `created_at` appears in the result with data_type
-- "timestamp with time zone", the guard works exactly as built — no
-- further action needed. If it's ABSENT, the guard is currently a safe
-- no-op (confirmed fail-open, never blocks a legitimate insert) but is
-- NOT actually protecting against the double-click bug it was built for —
-- flag that back so the column can be added (a small additive DDL change)
-- before Phase 1b is considered done.
-- ---------------------------------------------------------------------
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'account_flows'
ORDER BY ordinal_position;
