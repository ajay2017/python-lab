-- READ-ONLY check of held_stop_floors (#26 C3c, docs/plans/held-stop-floor-c3c-spec.md,
-- section 11 live check (ii)). Run after the first EOD that carries the writer.
-- Nothing is written. Run each block separately in the Supabase SQL editor.
--
-- Expect: one row per held name (one per open episode) from the latest as_of, binding
-- mostly 'trailing' / 'ratchet' / 'entry', stop_set_on <= as_of, written_at a few
-- minutes after the 4 PM ET close. Blocks 1 and 2 return nothing (and block 2's count
-- is 0) until the first EOD write; an error "relation held_stop_floors does not exist"
-- means docs/sql/held-stop-floors.sql has not been applied.

-- 1. Every saved floor, newest first (written_at shown in ET; written_that_night should be
--    true on every row of the latest as_of: it proves written_at refreshes on a re-write).
select ticker,
       episode_start,
       as_of,
       stop,
       binding,
       stop_set_on,
       written_at at time zone 'America/New_York' as written_at_et,
       (written_at at time zone 'America/New_York')::date = as_of as written_that_night
from held_stop_floors
order by as_of desc, ticker, episode_start;

-- 2. How many rows the latest as_of wrote (compare with the number of held names).
select max(as_of)  as latest_as_of,
       count(*)    as rows_written_by_latest_as_of
from held_stop_floors
where as_of = (select max(as_of) from held_stop_floors);
