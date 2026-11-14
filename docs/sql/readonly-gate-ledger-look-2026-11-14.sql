-- READ-ONLY. Gate Suppression Ledger "next look" (was due ~2026-10-26; CLAUDE.md
-- "Waiting for a date or data"). Nothing here writes. Run in the Supabase SQL editor.
--
-- What it answers:
--   Q1  per gate / source / counterfactual: rows, CALLS (one per distinct
--       ticker + rec_date, the F-259b counting rule of 2026-10-18), distinct
--       tickers, first / last date, and calls old enough to have matured.
--   Q2  has G-20's counterfactual = true in-place upgrade EVER been observed?
--       (still unobserved at the last look; plan §5a / line ~268)
--   Q3  is capture still alive? rows per source over the last 10 rec_dates.
--
-- "Matured" here is an APPROXIMATION for scanning only: rec_date at least 45
-- calendar days ago, standing in for GATE_LEDGER_HORIZON_TRADING_DAYS = 30
-- trading days. The 🛑 The Road Not Taken page is the authoritative readout
-- (it uses the trading-day horizon and the GATE_LEDGER_MIN_CALLS = 8 /
-- GATE_LEDGER_FIRM_CALLS = 15 / GATE_LEDGER_MIN_TICKERS = 5 floors).

-- Q1
select gate_id,
       source,
       counterfactual,
       count(*)                                              as rows,
       count(distinct (ticker, rec_date))                    as calls,
       count(distinct ticker)                                as tickers,
       min(rec_date)                                         as first_date,
       max(rec_date)                                         as last_date,
       count(distinct (ticker, rec_date))
         filter (where rec_date <= current_date - 45)        as calls_matured_approx
from public.gate_suppressions
group by gate_id, source, counterfactual
order by gate_id, source, counterfactual;

-- Q2
select rec_date, ticker, source, lane, tone, composite_score, gate_value, gate_threshold
from public.gate_suppressions
where gate_id = 'G-20' and counterfactual is true
order by rec_date desc
limit 20;

-- Q3
select rec_date, source, count(*) as rows, count(distinct gate_id) as gates
from public.gate_suppressions
where rec_date >= current_date - 14
group by rec_date, source
order by rec_date desc, source;
