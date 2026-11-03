-- READ-ONLY. Checks the broker sync's unmatched transactions ("72 pending" in
-- the 2026-11-02 broker log) against the trade journal. Changes nothing.
--
-- For each snaptrade_pending_imports row still 'pending', look for a journal
-- trade with the same ticker, action and share count whose ET date is within
-- 3 days of the broker's trade_date.
--   likely_logged  = a matching journal trade exists (auto-link just missed it)
--   NOT_IN_JOURNAL = no match: a real trade the journal may be missing
with p as (
  select id, ticker, action, shares, price, trade_date
  from snaptrade_pending_imports
  where status = 'pending'
),
m as (
  select p.*,
         (select t.id
            from trades t
           where upper(t.ticker) = upper(p.ticker)
             and upper(t.action) = upper(p.action)
             and abs(t.shares - p.shares) < 0.0001
             and abs((t.traded_at at time zone 'America/New_York')::date - p.trade_date) <= 3
           order by abs((t.traded_at at time zone 'America/New_York')::date - p.trade_date), t.id
           limit 1) as journal_trade_id
  from p
)
select case when journal_trade_id is null then 'NOT_IN_JOURNAL' else 'likely_logged' end as verdict,
       trade_date, ticker, action, shares, price, journal_trade_id
from m
order by verdict, trade_date desc, ticker;

-- 2026-11-02 follow-up, also READ-ONLY. Dismissed imports grouped per
-- (ticker, action, broker date): the broker reports partial fills separately
-- (e.g. MRVL 10-30: BUY 5 @ 200.00 + BUY 3 @ 191.96 = the journal's BUY 8 @
-- 196.99), so an exact per-fill share match under-counts. A group is flagged
-- only when the journal holds FEWER shares for that ticker/action within
-- 3 days either side than the broker filled that day.
with g as (
  select ticker, action, trade_date,
         sum(shares) as broker_shares,
         round(sum(shares * price) / sum(shares), 2) as broker_avg_price,
         count(*) as fills
  from snaptrade_pending_imports
  where status = 'dismissed'
  group by ticker, action, trade_date
)
select g.trade_date, g.ticker, g.action, g.broker_shares, g.broker_avg_price, g.fills,
       coalesce(j.journal_shares, 0) as journal_shares_within_3d
from g
left join lateral (
  select sum(t.shares) as journal_shares
  from trades t
  where upper(t.ticker) = upper(g.ticker)
    and upper(t.action) = upper(g.action)
    and abs((t.traded_at at time zone 'America/New_York')::date - g.trade_date) <= 3
) j on true
where coalesce(j.journal_shares, 0) + 0.0001 < g.broker_shares
order by g.trade_date desc, g.ticker;
