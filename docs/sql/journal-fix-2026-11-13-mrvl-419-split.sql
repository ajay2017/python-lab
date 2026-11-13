-- =====================================================================
-- DRISHTA — one-off journal correction: split MRVL #419 into its real days
-- Written 2026-11-13 (docs/bug-register.md row CvmMRVL419; owner chose
-- "split it at the source" the same evening).
-- Opus review 2026-11-13: FIX-FIRST (the new row is "late" for every account
-- row from 10-15 on, not just 10-15 … 10-19, so a whole-table stale guard is
-- required; #424's 2 shares were left out of the expected book; STEP 0b's
-- expectation was wrong) -> round 2 FIX-FIRST (6b / 0d timed trades by raw
-- traded_at; the app re-anchors midnight-UTC imports to 16:00 ET first) ->
-- fixes applied -> round 3 SHIP, 0 blocking.
--
-- APPLY journal-fix-2026-11-13-fill-times.sql (RecTime) FIRST. Best run both
-- before an EOD run, so that night's account row is written after the new id.
--
-- Why:
--   MRVL #419 is journal BUY 5 @ 250.00 on 10-14 (price-only fix of
--   2026-11-11, journal-fix-2026-11-11-mrvl-fslr.sql). Robinhood filled
--   10-14 2 @ 260.00 (09:35 EDT) + 1 @ 260.00 (09:44 EDT) and 10-15 2 @ 235.00
--   (09:45 EDT), the same 1,250.00 total. The journal therefore spends 470.00
--   a day early. Owner query 2026-11-13, journal net trade cash per ET day vs
--   the change in account_daily_snapshots.cash_balance: 10-14 +2,759.32 vs
--   +3,234.00, 10-15 +14.17 vs -455.91 (journal 470.08 high). 💳 Capital vs
--   Margin's backward cash roll therefore reads every day from 10-14 back
--   ~470 below Robinhood's recorded cash, over its 0.5% tolerance, and its
--   self-check withholds the verdicts spanning that window permanently.
--   After the split the per-day differences are 10-14 +4.68 and 10-15 -0.08;
--   the cumulative roll drift is about -0.17 on 10-14 and about +4.5 on 10-13
--   and earlier, all well under tolerance (0.5% x 14,118.68 = ~70.6).
--
-- Why update-in-place + one INSERT (not the 11-11 DELETE + INSERT pattern):
--   the nightly account-row repair and the Reports "left out" list
--   (account_snapshot_repair._analyse, via plan_repairs and stale_details ->
--   account_row_trust) check a row only for tickers touched by a LATE trade:
--   id > the row's journal_max_trade_id and traded_at <= its cash_as_of.
--   Keeping #419's id and date gives 10-14 no late MRVL trade (provided its
--   journal_max_trade_id >= 424, checked by guard 5), so 10-14 is never
--   flagged (a new id ON 10-14 would read "unexplained", the 11-11 concern).
--   The new 10-15 row IS late for EVERY account row from 10-15 to the latest
--   (no lookback on the Reports side), so MRVL becomes a touched ticker on
--   all of them. Each is fine only if its stored MRVL book already equals the
--   journal's replay at its cutoff. Guard 6 checks exactly that, after the
--   change and inside the block, and rolls everything back if any row would
--   read MRVL-stale. From 10-15 to 10-19 the book should read 7 = #424 2 +
--   #419 3 + the new 2 (MRVL flat from the 10-20 sale #435 on, then the 11-06
--   episode).
--
-- Effects:
--   #419 -> BUY 3 @ 260.00, same traded_at (10-14), unlinked (Robinhood has
--   two 10-14 fills; one row carries one fill id, so the matcher reads it
--   RECONCILED, like FSLR #413). New row BUY 2 @ 235.00 at 10-15 09:45 EDT,
--   linked to Robinhood's 10-15 record; trigger_type / asset_type / notes /
--   situational_category copied from #419; every other owner-written field
--   stays on #419 (the 11-11 U7' rule was for a deleted original).
--   Average cost is unchanged: (2 x 265 + 3 x 260 + 2 x 235) / 7 =
--   1,780 / 7 = 254.2857, so #435's stored basis and realized P&L are
--   unchanged (the Rebuild preview should match step d). Holdings and the
--   held-stop episode are unaffected. The tax ledger's FIFO total is
--   unchanged (the 10-20 sale shows 3 lot rows). The matcher's MRVL #419
--   FORWARD finding and the Home "1 difference" line should clear.
--   Residual, accepted: the 10-14 account row and its daily_snapshots MRVL
--   row keep 7 MRVL while the journal now says 5 (Robinhood's real count);
--   nothing flags it (MRVL is not touched there), 10-14 net equity stays
--   about 2 x that day's MRVL close high, and Capital vs Margin's advisory
--   gross-book caption may newly list 10-14. Self Track Record counts a
--   second MRVL BUY (10-15).
--   Trade timing in STEP 0d and guard 6b mirrors the app: the CTE `mt` moves
--   an imported row stored at exactly midnight UTC (with a broker_txn_id or a
--   notes import marker) to 16:00 ET on its UTC date, as
--   trade_time.normalize_traded_at does before account_snapshot_repair sees
--   it; it skips non-positive shares / price like _prep_trades. One known
--   difference, safe: _replay clamps an oversell to 0 while the SQL sum goes
--   negative; that can only produce a mismatch, i.e. a refusal.
--
-- What the DO block does (any failed check rolls the whole block back):
--   1. #419 still holds BUY 5 @ 250.00, ET date 10-14, unlinked;
--   2. exactly one Robinhood MRVL BUY 2 @ 235.00 record on 10-15 exists in
--      snaptrade_pending_imports, and its id is on no trade;
--   3. no MRVL BUY already sits on 10-15 in the journal (a re-run refuses),
--      and no MRVL SPLIT row exists;
--   4. no account row since 10-15 has provenance but no book;
--   5. the 10-14 account row's journal_max_trade_id is >= 424;
--   6. makes the change, then: no account row with provenance would read
--      MRVL-stale (stored book vs the journal's replay at the row's cutoff,
--      tolerance 0.001 = BROKER_DRIFT_SHARE_TOL) — else rolls back.
--
-- OWNER STEPS, in order (run each step alone; the Supabase SQL editor shows
-- only the last statement's result). After RecTime's file:
--   a. Run STEP 0 and SAVE its output (the undo record of #419). Expect one
--      row: MRVL BUY 5 @ 250.00, ET date 2026-10-14, broker_txn_id NULL.
--   b. Run STEP 0b and SAVE. Expect exactly ONE row: MRVL BUY 2 @ 235.00 on
--      2026-10-15, on_trade = 0. Anything else: stop and tell the lead.
--   c. Run STEP 0c and SAVE (the account rows' before-picture). Expect
--      mrvl_book 7 on 10-14 … 10-19 for rows that have a book, and
--      journal_max_trade_id >= 424 on 10-14. Anything else: stop and tell the
--      lead.
--   d. Run STEP 0d. Expect NO rows (it lists every account row from the
--      10-15 09:45 cutoff on whose MRVL book already differs from the journal;
--      those are the rows guard 6b checks). Rows: stop and tell the lead.
--   e. In 📒 Trade Journal, open "⚠️ Fix drift between trades and holdings",
--      click "Run rebuild now" (preview only, writes nothing), note what it
--      lists, then Cancel.
--   f. Run the DO block (the notice shows the new row's id and txn).
--   g. Run VERIFY 1. Expect #424 2 @ 265.00 (its stored time may read
--      10-12 20:00 ET: a midnight-UTC import the app shows as 10-13), #419 3 @
--      260.00 (10-14), the new row 2 @ 235.00 (10-15 09:45), linked.
--   h. Repeat step e's preview: expect exactly what step e listed. If it
--      differs, Cancel and tell the lead.
--   i. After the next EOD run, the broker cron log's account_snapshot_repair
--      line should show no refusal for any date, and 💰 Account →
--      💳 Capital vs Margin should no longer fail on 10-14.
-- =====================================================================


-- STEP 0 (read-only; run alone and SAVE: the undo record of #419).
select * from public.trades where id = 419;


-- STEP 0b (read-only; run alone and SAVE): Robinhood's 10-15 MRVL fill.
select p.snaptrade_txn_id, p.ticker, p.action, p.shares, p.price, p.trade_date, p.status,
       (select count(*) from public.trades t
         where t.broker_txn_id = p.snaptrade_txn_id) as on_trade
  from public.snaptrade_pending_imports p
 where upper(p.ticker) = 'MRVL' and upper(p.action) = 'BUY'
   and p.trade_date = date '2026-10-15'
 order by p.price;


-- STEP 0c (read-only; run alone and SAVE): account rows' before-picture.
select snapshot_date, journal_max_trade_id, repaired_at is not null as repaired,
       book_positions is null as no_book,
       book_positions->'MRVL'->>'shares' as mrvl_book,
       cash_balance,
       cash_as_of at time zone 'America/New_York' as cash_as_of_et
  from public.account_daily_snapshots
 where snapshot_date between date '2026-10-13' and date '2026-10-20'
 order by snapshot_date;


-- STEP 0d (read-only; run alone): every account row with provenance and a
-- book, whose cutoff is at or after the new row's time (10-15 09:45 EDT; the
-- rows guard 6b will check), and whose stored MRVL book differs from the
-- journal's MRVL replay at that cutoff (today's journal, before the split; the
-- replay there is the same before and after: 2 + 5 = 2 + 3 + 2). Expect no rows.
-- `mt` = MRVL trades timed as the app times them (see the header).
with mt as (
    select id, upper(action) as act, shares,
           case when traded_at = (((traded_at at time zone 'UTC')::date)::timestamp at time zone 'UTC')
                 and (coalesce(btrim(broker_txn_id), '') not in ('', 'None', 'nan')
                      or lower(coalesce(notes, '')) like any (array['%robinhood import%',
                                                                   '%rh text import%',
                                                                   '%rh screenshot%']))
                -- trade_time.IMPORT_NOTE_MARKERS; 16:00 = IMPORTED_TRADE_ANCHOR_ET_HOUR
                then (((traded_at at time zone 'UTC')::date) + time '16:00') at time zone 'America/New_York'
                else traded_at end as ts
      from public.trades
     where upper(ticker) = 'MRVL' and shares > 0 and price > 0
),
rows_ as (
    select a.snapshot_date, a.journal_max_trade_id, a.cash_as_of,
           coalesce((a.book_positions->'MRVL'->>'shares')::numeric, 0) as mrvl_book,
           (select coalesce(sum(case when mt.act like '%BUY%'  then mt.shares
                                     when mt.act like '%SELL%' then -mt.shares
                                     else 0 end), 0)
              from mt
             where case when a.cash_as_of is not null then mt.ts <= a.cash_as_of
                        else (mt.ts at time zone 'America/New_York')::date <= a.snapshot_date end
           ) as mrvl_journal
      from public.account_daily_snapshots a
     where a.journal_max_trade_id is not null
       and a.book_positions is not null
       and case when a.cash_as_of is not null
                then a.cash_as_of >= timestamptz '2026-10-15 09:45:00-04:00'
                else a.snapshot_date >= date '2026-10-15' end
)
select snapshot_date, journal_max_trade_id,
       cash_as_of at time zone 'America/New_York' as cash_as_of_et,
       mrvl_book, mrvl_journal
  from rows_
 where abs(mrvl_book - mrvl_journal) > 0.001
 order by snapshot_date;


-- DO block (run alone).
do $$
declare
    n      int;
    v_txn  text;
    v_new  bigint;
begin
    -- 1. #419 still holds exactly the 11-11 values
    select count(*) into n from public.trades
     where id = 419 and upper(ticker) = 'MRVL' and upper(action) = 'BUY'
       and abs(shares - 5) < 1e-6 and abs(price - 250.00) < 0.005
       and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-14';
    if n <> 1 then
        raise exception 'MRVL #419 BUY 5 @ 250.00 on 10-14 (unlinked): expected 1 row, found %', n;
    end if;

    -- 2. exactly one Robinhood 10-15 MRVL BUY 2 @ 235.00, not on any trade
    select count(*) into n from public.snaptrade_pending_imports
     where upper(ticker) = 'MRVL' and upper(action) = 'BUY' and abs(shares - 2) < 1e-6
       and abs(price - 235.00) < 0.005 and trade_date = date '2026-10-15';
    if n <> 1 then
        raise exception 'expected exactly 1 Robinhood MRVL BUY 2 @ 235.00 on 10-15, found %', n;
    end if;
    select snaptrade_txn_id into v_txn from public.snaptrade_pending_imports
     where upper(ticker) = 'MRVL' and upper(action) = 'BUY' and abs(shares - 2) < 1e-6
       and abs(price - 235.00) < 0.005 and trade_date = date '2026-10-15';
    select count(*) into n from public.trades where broker_txn_id = v_txn;
    if n <> 0 then
        raise exception 'Robinhood record % is already on % trade row(s): not applying', v_txn, n;
    end if;

    -- 3. no MRVL BUY already on 10-15 (a re-run, or a hand log); no MRVL SPLIT
    select count(*) into n from public.trades
     where upper(ticker) = 'MRVL' and upper(action) = 'BUY'
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-15';
    if n <> 0 then
        raise exception 'found % MRVL BUY row(s) already on 10-15: not applying', n;
    end if;
    select count(*) into n from public.trades
     where upper(ticker) = 'MRVL' and upper(action) like '%SPLIT%';
    if n <> 0 then
        raise exception 'found % MRVL SPLIT row(s): the repair refuses on splits, not applying', n;
    end if;

    -- 4. no account row the new 10-15 row is "late" for lacks a book
    select count(*) into n from public.account_daily_snapshots
     where snapshot_date >= date '2026-10-15'
       and journal_max_trade_id is not null and book_positions is null;
    if n <> 0 then
        raise exception '% account row(s) since 10-15 have provenance but no book_positions: not applying', n;
    end if;

    -- 5. no MRVL trade is late for the 10-14 row (assumes #424 is the newest MRVL
    --    id on or before 10-14; guard 6b is what enforces it in general — this
    --    one only gives the clearer message)
    select count(*) into n from public.account_daily_snapshots
     where snapshot_date = date '2026-10-14'
       and journal_max_trade_id is not null and journal_max_trade_id < 424;
    if n <> 0 then
        raise exception 'the 10-14 account row has journal_max_trade_id < 424: MRVL would be touched there, not applying';
    end if;

    -- 6a. the split
    update public.trades set shares = 3, price = 260.00 where id = 419;

    insert into public.trades (ticker, action, shares, price, traded_at, broker_txn_id,
                               trigger_type, asset_type, notes, situational_category)
    select ticker, 'BUY', 2, 235.00, timestamptz '2026-10-15 09:45:00-04:00',
           v_txn, trigger_type, asset_type, notes, situational_category
      from public.trades where id = 419
    returning id into v_new;

    -- 6b. post-change: no account row with provenance may read MRVL-stale
    --     (0.001 = BROKER_DRIFT_SHARE_TOL). A NULL book with a late MRVL trade
    --     also counts (it would refuse "no_book"). `mt` times trades as the app
    --     does (same CTE as STEP 0d; see the header).
    with mt as (
        select id, upper(action) as act, shares,
               case when traded_at = (((traded_at at time zone 'UTC')::date)::timestamp at time zone 'UTC')
                     and (coalesce(btrim(broker_txn_id), '') not in ('', 'None', 'nan')
                          or lower(coalesce(notes, '')) like any (array['%robinhood import%',
                                                                       '%rh text import%',
                                                                       '%rh screenshot%']))
                    -- trade_time.IMPORT_NOTE_MARKERS; 16:00 = IMPORTED_TRADE_ANCHOR_ET_HOUR
                    then (((traded_at at time zone 'UTC')::date) + time '16:00') at time zone 'America/New_York'
                    else traded_at end as ts
          from public.trades
         where upper(ticker) = 'MRVL' and shares > 0 and price > 0
    )
    select count(*) into n from public.account_daily_snapshots a
     where a.journal_max_trade_id is not null
       and exists (select 1 from mt
                    where mt.id > a.journal_max_trade_id
                      and case when a.cash_as_of is not null then mt.ts <= a.cash_as_of
                               else (mt.ts at time zone 'America/New_York')::date <= a.snapshot_date end)
       and (a.book_positions is null
            or abs(coalesce((a.book_positions->'MRVL'->>'shares')::numeric, 0)
                   - (select coalesce(sum(case when mt.act like '%BUY%'  then mt.shares
                                               when mt.act like '%SELL%' then -mt.shares
                                               else 0 end), 0)
                        from mt
                       where case when a.cash_as_of is not null then mt.ts <= a.cash_as_of
                                  else (mt.ts at time zone 'America/New_York')::date <= a.snapshot_date end)
                  ) > 0.001);
    if n <> 0 then
        raise exception '% account row(s) would read MRVL-stale after the split: not applying (run STEP 0d, and check the 10-13 / 10-14 rows in STEP 0c)', n;
    end if;

    raise notice 'APPLIED: #419 -> BUY 3 @ 260.00 (10-14); new #% BUY 2 @ 235.00 (10-15 09:45 EDT, txn %)',
        v_new, v_txn;
end $$;


-- VERIFY 1 (read-only; run alone).
select id, ticker, action, shares, price, round(shares * price, 2) as cost,
       traded_at at time zone 'America/New_York' as et_time, broker_txn_id
  from public.trades
 where upper(ticker) = 'MRVL' and upper(action) = 'BUY'
   and (traded_at at time zone 'America/New_York')::date between date '2026-10-12' and date '2026-10-15'
 order by traded_at, id;


-- UNDO (only if needed; BEFORE the next EOD run — after it, first check
-- repaired_at on account rows >= 10-15, since a repair made in between is not
-- undone by this): put #419 back from STEP 0 and remove the new row.
-- update public.trades set shares = 5, price = 250.00 where id = 419;
-- delete from public.trades where upper(ticker) = 'MRVL' and broker_txn_id = '<txn from the APPLIED notice>';


-- =====================================================================
-- APPLIED 2026-11-13 evening by the owner, after the RecTime file.
-- STEP 0 (undo record): #419 MRVL BUY 5 @ 250.00, traded_at
-- 2026-10-14 13:51:32.206403+00, broker_txn_id NULL, idempotency_key set (not
-- copied). STEP 0b: one record 44be5923-8c41-4c95-980c-1d8440d42efb, MRVL BUY
-- 2 @ 235.0, 2026-10-15, dismissed, on_trade 0. STEP 0c: mrvl_book 2 (10-13),
-- 7 (10-14, 10-15, 10-19), 10-16 legacy row (no id, no book), 10-20 no MRVL;
-- 10-14 journal_max_trade_id 486; every cash_as_of ~17:00 ET. STEP 0d: no
-- rows. DO block: "Success. No rows returned". VERIFY 1: #424 2 @ 265.0
-- (10-13 16:00 ET, linked b7689f0e-…), #419 3 @ 260.00 (10-14 09:51:32),
-- new #495 2 @ 235.00 (10-15 09:45, linked 44be5923-…). Rebuild preview:
-- "No realized P&L corrections needed", "No changes".
-- UNDO, if ever needed (before the next EOD; see above):
--   update public.trades set shares = 5, price = 250.00 where id = 419;
--   delete from public.trades where id = 495 and broker_txn_id = '44be5923-8c41-4c95-980c-1d8440d42efb';
-- Pending: step i (next EOD log: no account_snapshot_repair refusal; Capital
-- vs Margin self-check passes).
-- =====================================================================
