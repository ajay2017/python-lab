-- =====================================================================
-- DRISHTA — one-off journal correction: two MRVL fill times (RecTime)
-- Written 2026-11-13 from the owner's read-only checks the same evening
-- (docs/bug-register.md row RecTime).
-- Opus review 2026-11-13: FIX-FIRST (STEP 0 selected a non-existent
-- `created_at`; plus a replay-order guard, NULL-safe guards, a filled-in UNDO,
-- the daily_budget sibling) -> confirming round SHIP, 0 blocking.
--
-- Why:
--   #492 SELL 6 MRVL @ 156.02 and #494 BUY 5 MRVL @ 150.00 were hand-logged
--   on 11-13 with a blank fill time, so the Trade Journal form stored the
--   moment of logging (trade_entry_checks.resolve_traded_at, market open):
--     #492 saved 10:18:23.264124 ET, Robinhood filled 09:56 ET
--     #494 saved 14:30:50.554958 ET, Robinhood filled 10:21 ET
--   The 11-13 broker sync (account_cash.updated_at 11:19:14 ET, cash
--   -10,308.81) already included all three of the day's fills: 11-12 cash
--   -9,069.91 + (+936.12 - 1,425.00 - 750.00) = -10,308.79. Because #494's
--   stored time is after the sync, capital_vs_margin.adjust_anchor_for_late_
--   trades takes its $750 off the anchor a second time, and 💳 Capital vs
--   Margin's cash self-check fails ("Anchor adjusted by $750 for 1 trade(s)").
--   Other readers of the same stored time that this also corrects: the
--   💰 Account cash caption, the Home / cron broker-drift excuse list
--   (broker_sync.tickers_traded_since), and the F-306 new-position budget
--   basis (daily_budget.trades_after -> budget_basis, which today subtracts
--   #494's $750 a second time).
--
-- What the DO block does (any failed check rolls the whole block back):
--   1. checks each row still holds exactly the values above (ticker, action,
--      shares, price, ET date 2026-11-13, the stored ET time to the second)
--      and is unlinked (broker_txn_id NULL); NULL-safe, so a NULL fails;
--   2. checks no other MRVL row sits between each row's new and old time, so
--      the (traded_at, id) replay order is unchanged;
--   3. sets traded_at to the Robinhood fill time on the same ET date
--      (2026-11-13 is EST, UTC-05:00; DST ended 2026-11-01), printing the old
--      value first (a second copy of the undo record).
--   Only the time of day changes. The ET date (and the UTC date), shares,
--   price and the replay order are unchanged, so holdings, average cost,
--   realized P&L and every daily / account snapshot row are untouched. The
--   cron's broker linker keys on date / ticker / action / shares / price, not
--   time, so any Tier-2 link of #492 / #494 is unaffected. #493 SNOW
--   (10:30:00, a typed time) is not touched.
--
-- OWNER STEPS, in order (run each step alone; the Supabase SQL editor shows
-- only the last statement's result):
--   a. Run STEP 0 and SAVE its output (the undo record). Expect 2 rows:
--      #492 10:18:23.264124 and #494 14:30:50.554958 ET, broker_txn_id NULL.
--   b. Run the DO block. Expect "Success. No rows returned" (the notices show
--      the old times).
--   c. Run VERIFY. Expect #492 09:56:00, #494 10:21:00, same shares / price,
--      broker_txn_id NULL.
--   d. Hard-refresh the app (Ctrl+F5) -> 💰 Account -> 💳 Capital vs Margin:
--      no "Anchor adjusted by $750" line. The self-check may still fail on
--      10-14 by about $471 (MRVL #419, bug-register row CvmMRVL419): that is
--      expected and separate.
-- =====================================================================


-- STEP 0 (read-only; run alone and SAVE: the undo record).
select id, ticker, action, shares, price, traded_at,
       traded_at at time zone 'America/New_York' as traded_et, broker_txn_id
from public.trades
where id in (492, 494)
order by id;


-- DO block (run alone).
do $$
declare
    r record;
    n int;
begin
    -- 1. #492 still holds exactly the expected values
    select * into r from public.trades where id = 492;
    if not found then
        raise exception '#492 not found';
    end if;
    if r.ticker is distinct from 'MRVL' or upper(r.action) is distinct from 'SELL'
       or r.shares is distinct from 6 or r.price is distinct from 156.02
       or r.broker_txn_id is not null
       or date_trunc('second', r.traded_at at time zone 'America/New_York')
          is distinct from timestamp '2026-11-13 10:18:23' then
        raise exception '#492 no longer holds the expected values: % % % @ % at % (txn %)',
            r.ticker, r.action, r.shares, r.price,
            r.traded_at at time zone 'America/New_York', r.broker_txn_id;
    end if;

    -- 1. #494 still holds exactly the expected values
    select * into r from public.trades where id = 494;
    if not found then
        raise exception '#494 not found';
    end if;
    if r.ticker is distinct from 'MRVL' or upper(r.action) is distinct from 'BUY'
       or r.shares is distinct from 5 or r.price is distinct from 150.00
       or r.broker_txn_id is not null
       or date_trunc('second', r.traded_at at time zone 'America/New_York')
          is distinct from timestamp '2026-11-13 14:30:50' then
        raise exception '#494 no longer holds the expected values: % % % @ % at % (txn %)',
            r.ticker, r.action, r.shares, r.price,
            r.traded_at at time zone 'America/New_York', r.broker_txn_id;
    end if;

    -- 2. no other MRVL row between either row's new and old time
    select count(*) into n from public.trades
     where upper(ticker) = 'MRVL' and id not in (492, 494)
       and (traded_at between timestamptz '2026-11-13 09:56:00-05:00'
                          and timestamptz '2026-11-13 10:18:23.264124-05:00'
            or traded_at between timestamptz '2026-11-13 10:21:00-05:00'
                             and timestamptz '2026-11-13 14:30:50.554958-05:00');
    if n <> 0 then
        raise exception '% other MRVL row(s) sit between the new and old times: replay order would change, not applying', n;
    end if;

    -- 3. the Robinhood fill times
    select traded_at into r from public.trades where id = 492;
    raise notice 'UNDO RECORD #492 traded_at was %', r.traded_at;
    update public.trades set traded_at = timestamptz '2026-11-13 09:56:00-05:00'
    where id = 492;

    select traded_at into r from public.trades where id = 494;
    raise notice 'UNDO RECORD #494 traded_at was %', r.traded_at;
    update public.trades set traded_at = timestamptz '2026-11-13 10:21:00-05:00'
    where id = 494;

    raise notice 'APPLIED: #492 -> 2026-11-13 09:56:00 ET, #494 -> 2026-11-13 10:21:00 ET';
end
$$;


-- VERIFY (read-only; run alone).
select id, ticker, action, shares, price,
       traded_at at time zone 'America/New_York' as traded_et, broker_txn_id
from public.trades
where id in (492, 494)
order by id;


-- UNDO (only if needed; restores the stored values from before the fix,
-- as also shown in STEP 0's saved output):
-- update public.trades set traded_at = timestamptz '2026-11-13 10:18:23.264124-05:00' where id = 492;
-- update public.trades set traded_at = timestamptz '2026-11-13 14:30:50.554958-05:00' where id = 494;


-- =====================================================================
-- APPLIED 2026-11-13 evening by the owner. STEP 0 (undo record): #492
-- traded_at 2026-11-13 15:18:23.264124+00 (10:18:23.264124 ET), #494
-- 2026-11-13 19:30:50.554958+00 (14:30:50.554958 ET), both broker_txn_id NULL.
-- DO block: "Success. No rows returned". VERIFY: #492 SELL 6 @ 156.02 at
-- 09:56:00 ET, #494 BUY 5 @ 150.0 at 10:21:00 ET, both broker_txn_id NULL.
-- =====================================================================
-- Step d VERIFIED 2026-11-13 (owner screenshot): no "Anchor adjusted" line;
-- Capital vs Margin self-check worst 2026-10-14 reconstructed -14,590 vs
-- recorded -14,119 ($471 drift; was $1,221) = the MRVL #419 part (CvmMRVL419).
