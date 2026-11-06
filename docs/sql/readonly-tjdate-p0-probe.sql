-- TJDate Phase 1, P0 probe (docs/plans/trade-entry-guard.md). READ-ONLY.
-- Written by the confirming planner pass 2026-11-06 (Opus 5.5). Run each block
-- separately in the Supabase SQL editor and paste the results back.

-- Q1 keys SnapTrade sends (time on trade_date? amount/units/fee? an order-grouping id?)
select k as json_key, count(*) from snaptrade_pending_imports p, jsonb_object_keys(p.raw_json) k
where jsonb_typeof(p.raw_json)='object' group by k order by k;

-- Q2 does trade_date carry a time, and does its ET date differ from the stored (UTC) date?
select count(*) n,
  count(*) filter (where raw_json->>'trade_date' ~ 'T\d{2}:\d{2}') has_time,
  count(*) filter (where raw_json->>'trade_date' ~ 'T00:00:00(\.0+)?(Z|\+00:?00)?$') midnight_utc,
  count(*) filter (where (case when raw_json->>'trade_date' ~ 'T\d{2}:\d{2}'
       then ((raw_json->>'trade_date')::timestamptz at time zone 'America/New_York')::date end) <> trade_date) et_date_differs
from snaptrade_pending_imports where raw_json ? 'trade_date';

-- Q2b the latest 15 rows, raw dates side by side
select id,status,ticker,action,shares,price,trade_date,raw_json->>'trade_date' raw_trade_date,
       raw_json->>'settlement_date' raw_settle
from snaptrade_pending_imports order by fetched_at desc,id desc limit 15;

-- Q3 is amount/units more exact than the 2-dp price?
select id,ticker,action,shares,price,trade_date,raw_json->>'price' raw_price,raw_json->>'units' raw_units,
  raw_json->>'amount' raw_amount,raw_json->>'fee' raw_fee,
  case when raw_json->>'amount' ~ '^-?\d+(\.\d+)?$' and raw_json->>'units' ~ '^-?\d+(\.\d+)?$'
        and (raw_json->>'units')::numeric<>0
       then round(abs((raw_json->>'amount')::numeric)/abs((raw_json->>'units')::numeric),6) end amount_per_unit
from snaptrade_pending_imports order by trade_date desc,id desc limit 30;

-- Q4 order grouping (swap the key for whatever Q1 shows, if not external_reference_id)
select raw_json->>'external_reference_id' ext_ref,ticker,action,trade_date,count(*) fills,sum(shares) shares,
       array_agg(id order by id) pending_ids
from snaptrade_pending_imports where raw_json ? 'external_reference_id'
group by 1,2,3,4 having count(*)>1 order by trade_date desc limit 30;

-- Q5 does every 'logged' row have its trade?
select p.status,count(*) n_rows,count(t.id) with_trade,count(*)-count(t.id) no_trade
from snaptrade_pending_imports p left join trades t on t.broker_txn_id=p.snaptrade_txn_id
group by p.status order by p.status;

-- Q6 still-'pending' rows already linked to a trade (the HoldDup trigger)
select p.id pending_id,p.ticker,p.action,p.shares,p.price,p.trade_date,t.id trade_id
from snaptrade_pending_imports p join trades t on t.broker_txn_id=p.snaptrade_txn_id
where p.status='pending' order by p.trade_date desc;

-- Q7 TJDup calibration: same ticker/action/shares within 3 calendar days (about 1 session over a weekend)
with t as (select id,upper(ticker) tk,upper(action) act,shares,price,trigger_type,broker_txn_id,traded_at,
  case when to_char(traded_at at time zone 'UTC','HH24:MI:SS.US')='00:00:00.000000'
        and (broker_txn_id is not null or notes ilike '%Robinhood import%' or notes ilike '%RH text import%'
             or notes ilike '%RH screenshot%')
       then (traded_at at time zone 'UTC')::date else (traded_at at time zone 'America/New_York')::date end et_date
  from trades)
select a.id id_a,b.id id_b,a.tk,a.act,a.shares,a.price price_a,b.price price_b,abs(a.price-b.price) price_gap,
  a.et_date date_a,b.et_date date_b,b.et_date-a.et_date day_gap,
  to_char(a.traded_at at time zone 'America/New_York','YYYY-MM-DD HH24:MI') et_a,
  to_char(b.traded_at at time zone 'America/New_York','YYYY-MM-DD HH24:MI') et_b,
  a.trigger_type reason_a,b.trigger_type reason_b,a.broker_txn_id is not null linked_a,b.broker_txn_id is not null linked_b
from t a join t b on a.id<b.id and a.tk=b.tk and a.act=b.act and abs(a.shares-b.shares)<=0.001
  and abs(b.et_date-a.et_date)<=3
order by greatest(a.et_date,b.et_date) desc,a.tk;
