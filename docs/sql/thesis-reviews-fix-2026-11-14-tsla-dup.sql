-- Remove the duplicate TSLA thesis review (DQ-TSLA, docs/bug-register.md).
-- Owner-run in the Supabase SQL editor. Found by the Saturday data-quality
-- emails of 2026-11-07 and 2026-11-14 ("1 ticker(s) with a duplicate
-- (ticker, inputs_hash) thesis review: TSLA").
--
-- Evidence (owner ran Q3 of docs/sql/data-quality-triage-2026-11-07.sql,
-- 2026-11-14): two rows, same ticker, same inputs_hash d7aa4433fa6241ca, same
-- status WEAKENING, written 30 seconds apart on Tue 2026-10-27 (10:54:42 and
-- 10:55:12 ET) = a double press of the manual "Re-evaluate {ticker}" button on 🧠 AI Insights (the
-- Sunday cron has thesis_advisor.already_reviewed_today; the button has no
-- such guard). Each press paid for its own LLM call, so the two summaries are
-- worded differently.
--
-- Keep the FIRST row (abf8213d…, 10:54:42); delete the newer (5f4c13b9…).
-- Opus review 2026-11-14: SHIP, 0 blocking (guards, no FK in the repo, every
-- reader takes the newest row by reviewed_at and both rows carry the same
-- status and date, so no surface changes beyond the summary wording).
-- No table references thesis_reviews.id (no foreign key; checked 2026-11-14).
--
-- STEP 1 (read-only): confirm both rows are still there and as described.
select id, ticker, reviewed_at at time zone 'America/New_York' as reviewed_et,
       status, inputs_hash
from public.thesis_reviews
where id in ('abf8213d-caab-4bdd-a0ec-d252a2a69dfb',
             '5f4c13b9-cc03-444d-a3c8-425a46bae110')
order by reviewed_at;
-- Expect 2 rows: abf8213d… 10:54:42 then 5f4c13b9… 10:55:12, both TSLA,
-- WEAKENING, d7aa4433fa6241ca. Anything else: STOP.

-- STEP 1b (read-only, optional): confirm in PRODUCTION that no table has a
-- foreign key to thesis_reviews (the repo has none). Expect 0 rows.
select conrelid::regclass as referencing_table, conname
from pg_constraint
where confrelid = 'public.thesis_reviews'::regclass;

-- STEP 2 (the delete). Deletes nothing unless every guard matches; returns
-- the deleted row. Expect exactly 1 row back.
delete from public.thesis_reviews
where id = '5f4c13b9-cc03-444d-a3c8-425a46bae110'
  and ticker = 'TSLA'
  and inputs_hash = 'd7aa4433fa6241ca'
  and exists (select 1 from public.thesis_reviews k
              where k.id = 'abf8213d-caab-4bdd-a0ec-d252a2a69dfb'
                and k.ticker = 'TSLA'
                and k.inputs_hash = 'd7aa4433fa6241ca')
returning id, ticker, reviewed_at;

-- STEP 3 (read-only): the duplicate check should now be empty for TSLA.
select ticker, inputs_hash, count(*)
from public.thesis_reviews
where ticker = 'TSLA' and inputs_hash not like 'earnings_%'
group by ticker, inputs_hash
having count(*) > 1;
-- Expect no rows.

-- STEP 3b (read-only, optional): the same check for every ticker, i.e. what the
-- next Saturday email's duplicate check should find. Expect no rows.
select ticker, inputs_hash, count(*)
from public.thesis_reviews
where inputs_hash not like 'earnings_%'
group by ticker, inputs_hash
having count(*) > 1;
