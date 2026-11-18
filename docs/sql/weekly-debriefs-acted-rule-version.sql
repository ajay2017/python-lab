-- =====================================================================
-- DRISHTA — record which "acted on a call" rule wrote each weekly debrief (U15)
-- Written 2026-11-18 (docs/plans/acted-rule-and-report-periods.md, chunk C3).
--
-- Purpose: C3 moves the weekly debrief onto the shared entry-call rule
-- (`stock_analyzer/entry_call_action.py`, `ACTED_RULE_VERSION = 2`): a BUY within
-- `ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS` NYSE sessions of the call, judged only
-- up to the week's end, with "still open" for a call whose window runs past it.
-- Older debriefs used a different rule (any BUY or SELL from the week's start,
-- no upper bound), so their acted / not-acted lines are not comparable. This
-- column says which rule wrote the row:
--     2    = the shared entry-call rule (written by C3 onward)
--     null = written before this column existed (the older rule)
-- No default and no backfill: stored rows are never rewritten (plan U15). Readers
-- treat null as the older rule and label it.
--
-- Owner applies this manually in the Supabase SQL editor. Idempotent: safe to
-- re-run. No RLS change (the table keeps its service_role policy).
--
-- HARD ORDERING RULE: apply this ALTER and run the verify query below BEFORE C3
-- deploys. `db.save_weekly_debrief` has no drop-and-retry, so once C3 sends
-- `acted_rule_version`, a save against a table WITHOUT the column fails: the
-- Sunday debrief row is not saved and the cron lane goes red. Until C3 ships
-- nothing writes the column, so applying it early is harmless.
-- =====================================================================

alter table public.weekly_debriefs
    add column if not exists acted_rule_version smallint;

-- Verify (read-only): expect 1 row, data_type = smallint, is_nullable = YES.
select column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name = 'weekly_debriefs'
  and column_name = 'acted_rule_version';

-- Verify (read-only): RLS still on and the service_role policy unchanged.
-- Expect relrowsecurity = true and one policy for {service_role}, cmd ALL.
select c.relrowsecurity, p.polname, p.polcmd,
       array(select rolname from pg_roles r where r.oid = any(p.polroles)) as roles
from pg_class c
left join pg_policy p on p.polrelid = c.oid
where c.oid = 'public.weekly_debriefs'::regclass;
