-- READ-ONLY probe for SlidingStop (docs/plans/sliding-stop.md §2, P3). Nothing here writes.
-- Run each query in the Supabase SQL editor and paste the results back.
--
-- Q1: per held ticker, the journal's net shares vs holdings.shares.
--     The journal replay in stock_analyzer/held_stop.py (episode_from_journal) treats a
--     SPLIT row's shares as the adjusted TOTAL, so the net here = the latest SPLIT row's
--     shares (if any) + BUYs - SELLs after it, in (traded_at, id) order. A gap beyond
--     HELD_STOP_SHARES_TOL (1e-4) puts that holding on the day-by-day "Prior-close ATR"
--     stop. no_history = a holding with no trades at all.
with t as (
  select id, upper(ticker) tk, upper(action) act, shares::numeric sh, traded_at
  from trades
),
last_split as (
  select distinct on (tk) tk, id split_id, traded_at split_at, sh split_shares
  from t where act like '%SPLIT%'
  order by tk, traded_at desc, id desc
),
net as (
  -- one row per journal ticker, even when its last row is the SPLIT itself
  select k.tk,
         coalesce(ls.split_shares, 0)
           + coalesce(sum(case when t.act like '%BUY%'  then t.sh end), 0)
           - coalesce(sum(case when t.act like '%SELL%' then t.sh end), 0) journal_net,
         (ls.split_id is not null) has_split
  from (select distinct tk from t) k
  left join last_split ls on ls.tk = k.tk
  left join t on t.tk = k.tk
             and (ls.split_id is null or (t.traded_at, t.id) > (ls.split_at, ls.split_id))
  group by k.tk, ls.split_shares, ls.split_id
)
select upper(h.ticker) ticker, h.shares holdings_shares, n.journal_net,
       round(coalesce(n.journal_net, 0) - h.shares, 6) gap,
       case when n.tk is null then 'no_history'
            when abs(coalesce(n.journal_net, 0) - h.shares) > 0.0001 then 'MISMATCH'
            else 'ok' end status,
       coalesce(n.has_split, false) has_split
from holdings h
left join net n on n.tk = upper(h.ticker)
order by status desc, ticker;

-- Q2: held tickers that were NOT on a full-history stop at the last EOD that wrote floors
--     (no held_stop_floors row at the table's latest as_of). Expect the names Q1 flags,
--     plus young listings (ANT) and any name whose entry the stop engine couldn't anchor.
with latest as (select max(as_of) d from held_stop_floors)
select upper(h.ticker) ticker,
       (select max(f.as_of) from held_stop_floors f where upper(f.ticker) = upper(h.ticker)) last_floor_as_of,
       (select d from latest) table_latest_as_of
from holdings h
where not exists (
  select 1 from held_stop_floors f, latest
  where upper(f.ticker) = upper(h.ticker) and f.as_of = latest.d
)
order by ticker;

-- Q3: floor rows grouped by the date each was LAST written. NB the primary key is
--     (ticker, episode_start) and every EOD upserts, so this is NOT a per-EOD write count:
--     the latest date = names on full basis at the last EOD; older dates = positions since
--     sold, or held names that fell off full basis after that date (Q2 names them).
select as_of, count(*) rows_written
from held_stop_floors
group by as_of
order by as_of desc
limit 20;

-- Q4 (added after Q2 showed ABBV off full since 11-17 and REGN never on full): their
--     journal rows (ET date and time, price) and the last saved floor row, so the reason the
--     stop engine left full basis (entry anchor dropped, an add, a late-dated fill) can be read.
select id, upper(ticker) ticker, upper(action) action, shares, price,
       to_char(traded_at at time zone 'America/New_York', 'YYYY-MM-DD HH24:MI') traded_et
from trades
where upper(ticker) in ('ABBV', 'REGN')
order by ticker, traded_at, id;

select upper(ticker) ticker, as_of, stop, stop_set_on, binding, ratchet_label, episode_start,
       first_buy_price, last_buy, avg_cost
from held_stop_floors
where upper(ticker) = 'ABBV'
order by as_of desc
limit 3;
