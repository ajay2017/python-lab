-- READ-ONLY probe P (docs/plans/broker-fill-record.md, "Prerequisites").
-- How late do Robinhood fills reach the app through SnapTrade?
--
-- snaptrade_pending_imports.fetched_at is the FIRST time the broker cron saw a
-- fill (the insert uses ignore_duplicates, so a re-fetch never moves it).
-- raw_json->>'trade_date' is SnapTrade's own timestamp for the fill (order
-- placement time; ~= the fill for marketable orders).
--
-- Bias: this table only holds fills that did NOT auto-match a journal row at
-- sync time, so it over-represents trades logged late or never. That is fine
-- for measuring lag; it is not a count of all trades.
--
-- Run each block separately in the Supabase SQL editor. Nothing is written.

-- 1. Per fill: its ET date, when the app first saw it (ET), and the lag bucket.
with f as (
  select id, ticker, action, shares, price, status,
         (raw_json->>'trade_date')::timestamptz                                  as broker_ts,
         ((raw_json->>'trade_date')::timestamptz at time zone 'America/New_York')::date as fill_et_date,
         fetched_at at time zone 'America/New_York'                               as first_seen_et
  from snaptrade_pending_imports
  where raw_json ? 'trade_date'
)
select fill_et_date, ticker, action, shares, price, status,
       broker_ts at time zone 'America/New_York' as broker_et,
       first_seen_et,
       case
         when first_seen_et::date =  fill_et_date      then 'same ET day'
         when first_seen_et::date =  fill_et_date + 1  then 'next ET day'
         else 'later (' || (first_seen_et::date - fill_et_date) || ' days)'
       end as seen
from f
order by fill_et_date desc, broker_ts desc
limit 100;

-- 2. Summary: share of fills first seen the same ET day vs later
--    (only fills since the 2026-08-18 first sync; the first sync back-filled 90 days).
with f as (
  select ((raw_json->>'trade_date')::timestamptz at time zone 'America/New_York')::date as fill_et_date,
         (fetched_at at time zone 'America/New_York')::date                             as seen_et_date
  from snaptrade_pending_imports
  where raw_json ? 'trade_date'
)
select case
         when seen_et_date =  fill_et_date     then '1 same ET day'
         when seen_et_date =  fill_et_date + 1 then '2 next ET day'
         else '3 later'
       end as seen, count(*) as fills
from f
where fill_et_date >= date '2026-08-19'
group by 1 order by 1;

-- 3. Most fills on one ET day (calibrates a Phase 2 circuit breaker).
select ((raw_json->>'trade_date')::timestamptz at time zone 'America/New_York')::date as fill_et_date,
       count(*) as fills
from snaptrade_pending_imports
where raw_json ? 'trade_date'
group by 1 order by 2 desc limit 5;

-- 4. Has today's NVDA buy reached the table yet? (Empty = not seen yet, or it
--    auto-matched a journal row, which skips this table.)
select ticker, action, shares, price, status, raw_json->>'trade_date' as broker_ts,
       fetched_at at time zone 'America/New_York' as first_seen_et
from snaptrade_pending_imports
where ticker = 'NVDA' and fetched_at >= now() - interval '3 days'
order by fetched_at desc;
