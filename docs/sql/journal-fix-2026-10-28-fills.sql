-- =====================================================================
-- DRISHTA — one-off journal correction: the 10-28 / 10-29 fills
-- Written 2026-11-02 (bug register N1, the 10-28 account row).
--
-- Why: three journal BUY rows dated 2026-10-29 each merged real fills from
-- two days at an averaged price. Robinhood's history (owner screenshots,
-- 2026-11-02) shows the true fills. Using Robinhood's own dollar totals they
-- reconcile to the broker cash to the cent: 10-28 = 1,054.00 + 969.00 +
-- 1,122.03 + 300.99 + 430.00 = $3,876.02 (cash -7,761.29 -> -11,637.31);
-- 10-29 = 913.26 + 524.00 + 890.00 + 283.00 = $2,610.26 (cash -> -14,247.57).
-- OKTA's market buy shows "5 shares at $224.41" but $1,122.03, so its real
-- price is $224.406 (stored here, so the cost is exact).
--
--   journal row (10-29)   real fills, oldest first (Robinhood lists newest first)
--   MSFT  3 @ 526.00      10-28: 2 @ 527.00                 10-29: 1 @ 524.00
--   OKTA  7 @ 221.72      10-28: 5 @ 224.406, then 2 @ 215.00
--   GOOGL 5 @ 310.60      10-28: 3 @ 323.00, then 1 @ 300.99   10-29: 1 @ 283.00
--   (GOOGL is no longer held; only its history changes.)
--
-- ORDER MATTERS: every 10-28 row gets the same 16:00 ET timestamp, so the
-- replay (held_stop.episode_from_journal, tax lots) orders them by id, and the
-- existing row (lowest id) becomes the episode's first fill = the held stop's
-- entry anchor. Step 2 therefore gives each existing row the EARLIEST fill,
-- and step 3 inserts the rest oldest first.
--
-- What it does, in ONE block (any failed check rolls the whole block back):
--   1. checks each journal row exists exactly once with no broker link, and
--      that none of the fills below were already added (re-run: it refuses);
--   2. prints each row's old shares / price / time (raise notice), then turns
--      each existing row into its EARLIEST real fill, keeping its id, notes,
--      thesis, pre-mortem and reason;
--   3. inserts the remaining fills, copying trigger_type, asset_type and the
--      parent row's notes verbatim (a BUY's notes are shown as its entry
--      thesis: app.py _journal_context reads the most recent BUY's notes, and
--      portfolio_qa trade_notes), nothing else.
-- Total shares per ticker are unchanged; total cost changes by <= $0.01
-- (MSFT 0.00, OKTA -0.01, GOOGL -0.01). Holdings are not touched.
-- Timestamps use the import anchor, 16:00 ET (EDT on these dates = 20:00 UTC),
-- the same as market_time.et_anchor_iso. broker_txn_id stays NULL: the broker
-- lane's content match (UTC date, ticker, action, shares, 2-dp price) links
-- each row to its SnapTrade fill on its next run.
--
-- Owner, in the Supabase SQL editor (it shows only the LAST statement's result
-- when several run at once): highlight only STEP 0 and Run, and save the
-- result (the undo record). Then highlight the DO block and Run. Then run each
-- of the two queries below it on its own.
-- Expected afterwards: 🧠 AI Insights shows MSFT's entry as 10-29 @ 524.00
-- (its most recent BUY); OKTA's is 10-28 at either 224.406 or 215.00.
-- Best before the 16:15 ET broker run, so that run links the rows.
-- =====================================================================

-- STEP 0 (read-only; run and SAVE the output first: it is the undo record).
-- Expect 3 rows: MSFT 3 @ 526.00, OKTA 7 @ 221.72, GOOGL 5 @ 310.60, all 10-29 ET.
select id, ticker, action, shares, price, traded_at, broker_txn_id, trigger_type, notes
  from public.trades
 where action = 'BUY' and broker_txn_id is null
   and (traded_at at time zone 'America/New_York')::date = date '2026-10-29'
   and ((ticker = 'MSFT'  and abs(shares - 3) < 1e-6 and abs(price - 526.00) < 0.005)
     or (ticker = 'OKTA'  and abs(shares - 7) < 1e-6 and abs(price - 221.72) < 0.005)
     or (ticker = 'GOOGL' and abs(shares - 5) < 1e-6 and abs(price - 310.60) < 0.005))
 order by ticker;

do $$
declare
    r       record;
    v_msft  bigint;
    v_okta  bigint;
    v_googl bigint;
    n       int;
begin
    -- 1a. each merged row exists exactly once, hand-logged (no broker link)
    select count(*), min(id) into n, v_msft from public.trades
     where ticker = 'MSFT' and action = 'BUY' and abs(shares - 3) < 1e-6
       and abs(price - 526.00) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-29';
    if n <> 1 then raise exception 'MSFT 3 @ 526.00 on 10-29 (no broker link): expected 1 row, found %', n; end if;

    select count(*), min(id) into n, v_okta from public.trades
     where ticker = 'OKTA' and action = 'BUY' and abs(shares - 7) < 1e-6
       and abs(price - 221.72) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-29';
    if n <> 1 then raise exception 'OKTA 7 @ 221.72 on 10-29 (no broker link): expected 1 row, found %', n; end if;

    select count(*), min(id) into n, v_googl from public.trades
     where ticker = 'GOOGL' and action = 'BUY' and abs(shares - 5) < 1e-6
       and abs(price - 310.60) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-29';
    if n <> 1 then raise exception 'GOOGL 5 @ 310.60 on 10-29 (no broker link): expected 1 row, found %', n; end if;

    -- 1b. none of the fills is in the journal already
    select count(*) into n from public.trades
     where action = 'BUY'
       and (traded_at at time zone 'America/New_York')::date in (date '2026-10-28', date '2026-10-29')
       and ((ticker = 'MSFT'  and abs(shares - 2) < 1e-6 and abs(price - 527.00) < 0.005)
         or (ticker = 'MSFT'  and abs(shares - 1) < 1e-6 and abs(price - 524.00) < 0.005)
         or (ticker = 'OKTA'  and abs(shares - 5) < 1e-6 and abs(price - 224.406) < 0.005)
         or (ticker = 'OKTA'  and abs(shares - 2) < 1e-6 and abs(price - 215.00) < 0.005)
         or (ticker = 'GOOGL' and abs(shares - 3) < 1e-6 and abs(price - 323.00) < 0.005)
         or (ticker = 'GOOGL' and abs(shares - 1) < 1e-6 and abs(price - 300.99) < 0.005)
         or (ticker = 'GOOGL' and abs(shares - 1) < 1e-6 and abs(price - 283.00) < 0.005));
    if n <> 0 then raise exception 'found % of the real fills already in the journal: not applying', n; end if;

    -- 2. print the old values (second copy of the undo record), then each
    --    existing row becomes its earliest real fill (10-28). notes untouched.
    for r in select id, ticker, shares, price, traded_at from public.trades
              where id in (v_msft, v_okta, v_googl) order by id loop
        raise notice 'before: id % % % @ % at %', r.id, r.ticker, r.shares, r.price, r.traded_at;
    end loop;
    update public.trades set shares = 2, price = 527.00,
           traded_at = timestamptz '2026-10-28 20:00:00+00' where id = v_msft;
    update public.trades set shares = 5, price = 224.406,
           traded_at = timestamptz '2026-10-28 20:00:00+00' where id = v_okta;
    update public.trades set shares = 3, price = 323.00,
           traded_at = timestamptz '2026-10-28 20:00:00+00' where id = v_googl;

    -- 3. the remaining fills, oldest first (so ids keep the real order)
    insert into public.trades (ticker, action, shares, price, traded_at, trigger_type, asset_type, notes)
    select ticker, 'BUY', 1, 300.99, timestamptz '2026-10-28 20:00:00+00', trigger_type, asset_type, notes
      from public.trades where id = v_googl;
    insert into public.trades (ticker, action, shares, price, traded_at, trigger_type, asset_type, notes)
    select ticker, 'BUY', 2, 215.00, timestamptz '2026-10-28 20:00:00+00', trigger_type, asset_type, notes
      from public.trades where id = v_okta;
    insert into public.trades (ticker, action, shares, price, traded_at, trigger_type, asset_type, notes)
    select ticker, 'BUY', 1, 524.00, timestamptz '2026-10-29 20:00:00+00', trigger_type, asset_type, notes
      from public.trades where id = v_msft;
    insert into public.trades (ticker, action, shares, price, traded_at, trigger_type, asset_type, notes)
    select ticker, 'BUY', 1, 283.00, timestamptz '2026-10-29 20:00:00+00', trigger_type, asset_type, notes
      from public.trades where id = v_googl;

    raise notice 'applied: MSFT #%, OKTA #%, GOOGL #% re-dated; 4 fills inserted', v_msft, v_okta, v_googl;
end $$;

-- Verify: expect 7 rows (MSFT 2 + 1, OKTA 5 + 2, GOOGL 3 + 1 + 1), dated
-- 10-28 / 10-29 ET; per-ticker shares MSFT 3 / OKTA 7 / GOOGL 5; costs
-- 1,054.00 + 524.00, 1,122.03 + 430.00, 969.00 + 300.99 + 283.00.
select id, ticker, shares, price, round(shares * price, 2) as cost,
       (traded_at at time zone 'America/New_York')::date as et_date, trigger_type, notes
  from public.trades
 where action = 'BUY' and ticker in ('MSFT', 'OKTA', 'GOOGL')
   and (traded_at at time zone 'America/New_York')::date in (date '2026-10-28', date '2026-10-29')
 order by ticker, traded_at, id;

-- Pending imports for these fills (read-only). These fills are now in the
-- journal: for any row still 'pending', click "Already logged" in the app.
-- NEVER "Log This Trade" on them: that would log the fill a second time.
select id, ticker, shares, price, trade_date, status
  from public.snaptrade_pending_imports
 where ticker in ('MSFT', 'OKTA', 'GOOGL')
   and trade_date in (date '2026-10-28', date '2026-10-29')
 order by ticker, trade_date, id;
