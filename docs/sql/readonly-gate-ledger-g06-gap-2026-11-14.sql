-- READ-ONLY. Why did the app stop writing G-06 rows after 2026-09-23?
-- (Gate ledger look 2026-11-14: app G-06 rows 09-14 -> 09-23, cron G-06 rows only
-- from 11-02, nothing from either source in between.) Nothing here writes.
--
-- Three readings, and what separates them:
--   (a) G-06 simply didn't fire 09-24 -> 11-01 (portfolio beta back under its limit)
--       -> Q1: no app Watchlist rows of ANY gate look different around 09-23;
--          Q3's gate_value / gate_threshold show the beta that fired.
--   (b) the app's capture broke for G-06 only
--       -> Q2: on cron G-06 days the app DID write Watchlist rows (G-13 / G-28 /
--          G-29) for other tickers, but never G-06.
--   (c) another hold takes precedence on the app's card (one suppression_kind per card)
--       -> Q2: on cron G-06 days the app wrote a DIFFERENT gate for the SAME ticker.
--   (d) you didn't open the Watchlist on those days
--       -> Q2: no app Watchlist rows at all on those days.

-- Q1  app Watchlist-gate rows per day, 2026-09-10 onward (G-05 sector, G-06 beta,
--     G-13 R:R, G-28 macro hold, G-29 bear-day hold = the five Watchlist kinds).
select rec_date,
       count(*) filter (where gate_id = 'G-05') as g05,
       count(*) filter (where gate_id = 'G-06') as g06,
       count(*) filter (where gate_id = 'G-13') as g13,
       count(*) filter (where gate_id = 'G-28') as g28,
       count(*) filter (where gate_id = 'G-29') as g29
from public.gate_suppressions
where source = 'app'
  and gate_id in ('G-05', 'G-06', 'G-13', 'G-28', 'G-29')
  and rec_date >= date '2026-09-10'
group by rec_date
order by rec_date;

-- Q2  every cron G-06 call, with what the APP wrote that day: for the same ticker,
--     and for any ticker on the Watchlist gates.
select c.rec_date,
       c.ticker,
       c.gate_value                                        as cron_beta_value,
       c.gate_threshold                                    as cron_beta_threshold,
       (select string_agg(distinct a.gate_id, ', ')
          from public.gate_suppressions a
         where a.source = 'app' and a.rec_date = c.rec_date
           and a.ticker = c.ticker)                        as app_gates_same_ticker,
       (select count(*)
          from public.gate_suppressions a
         where a.source = 'app' and a.rec_date = c.rec_date
           and a.gate_id in ('G-05', 'G-06', 'G-13', 'G-28', 'G-29'))
                                                           as app_watchlist_rows_that_day
from public.gate_suppressions c
where c.gate_id = 'G-06' and c.source = 'cron'
order by c.rec_date, c.ticker;

-- Q3  the last app G-06 rows and the first cron ones: what beta value fired them.
(select 'app' as src, rec_date, ticker, gate_value, gate_threshold, reason
   from public.gate_suppressions
  where gate_id = 'G-06' and source = 'app'
  order by rec_date desc, ticker
  limit 15)
union all
(select 'cron', rec_date, ticker, gate_value, gate_threshold, reason
   from public.gate_suppressions
  where gate_id = 'G-06' and source = 'cron'
  order by rec_date, ticker
  limit 15)
order by src, rec_date, ticker;
