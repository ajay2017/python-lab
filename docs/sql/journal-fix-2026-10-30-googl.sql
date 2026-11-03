-- =====================================================================
-- DRISHTA — one-off journal correction: GOOGL on 2026-10-30
-- Written 2026-11-02 from the broker-vs-journal check
-- (docs/sql/readonly-pending-imports-check.sql).
--
-- Why: Robinhood (snaptrade_pending_imports, all dismissed) shows GOOGL
-- 10-28 BUY 3 @ 323.00 + BUY 1 @ 300.99, 10-29 BUY 1 @ 283.00,
-- 10-30 BUY 1 @ 280.00, 10-30 SELL 6 @ 278.82. The journal has every row
-- except the 10-30 BUY 1 @ 280.00, and its SELL (id 452) says 5 shares.
--
-- Effect: realized P&L on the 10-30 sale moves from about -158.89 to about
-- -160.07 (-1.18). Holdings are unchanged (GOOGL 0 before and after). No
-- account row changes: the share was bought and sold on 10-30, so that
-- day's end-of-day book holds 0 GOOGL either way, and the broker cash
-- already included both fills.
--
-- The buy's real time isn't recorded. It is set to 13:53 ET (17:53 UTC),
-- one minute before the 13:54 ET sale, so every replay (holdings, tax lots,
-- realized P&L) orders the buy first. broker_txn_id stays NULL.
--
-- Owner, in the Supabase SQL editor: run STEP 0 alone and save its output
-- (the undo record). Then highlight the DO block and Run. Then, in the app,
-- 📒 Trade Journal -> "🔄 Rebuild holdings & realized P&L" so the SELL's
-- cost basis and realized P&L are recomputed. Then run the verify query.
-- Best before the 11-03 EOD run (both changes land in one block, so the
-- nightly account-row check sees a consistent journal either way).
-- =====================================================================

-- STEP 0 (read-only; SAVE the output). Expect 1 row: id 452, SELL 5 @ 278.82.
select id, ticker, action, shares, price, cost_basis, realized_pnl, traded_at, trigger_type, notes
  from public.trades
 where id = 452;

do $$
declare
    n int;
begin
    -- the SELL is still the 5-share row
    select count(*) into n from public.trades
     where id = 452 and ticker = 'GOOGL' and action = 'SELL'
       and abs(shares - 5) < 1e-6 and abs(price - 278.82) < 0.005;
    if n <> 1 then raise exception 'id 452 is not GOOGL SELL 5 @ 278.82 any more: not applying'; end if;

    -- the missing buy isn't already there (re-run refuses)
    select count(*) into n from public.trades
     where ticker = 'GOOGL' and action = 'BUY' and abs(shares - 1) < 1e-6
       and abs(price - 280.00) < 0.005
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-30';
    if n <> 0 then raise exception 'GOOGL BUY 1 @ 280.00 on 10-30 already in the journal: not applying'; end if;

    insert into public.trades (ticker, action, shares, price, traded_at, trigger_type, asset_type, notes)
    select 'GOOGL', 'BUY', 1, 280.00, timestamptz '2026-10-30 17:53:00+00', 'MANUAL', asset_type,
           'Backfill 2026-11-02 — Robinhood fill missing from the journal (time not recorded; set just before the 13:54 ET sale)'
      from public.trades where id = 452;

    update public.trades set shares = 6 where id = 452;

    raise notice 'applied: GOOGL BUY 1 @ 280.00 added on 10-30; SELL #452 5 -> 6 shares';
end $$;

-- Verify (after the app's Rebuild): GOOGL since 10-28 should read
-- BUY 3 + 1 (10-28), BUY 1 (10-29), BUY 1 + SELL 6 (10-30): net 0.
select id, action, shares, price, cost_basis, realized_pnl,
       (traded_at at time zone 'America/New_York') as et_time
  from public.trades
 where ticker = 'GOOGL' and traded_at >= timestamptz '2026-10-28 00:00:00+00'
 order by traded_at, id;
