-- =====================================================================
-- DRISHTA — one-off journal correction: MRVL #419, FSLR #413 and #429
-- Written 2026-11-11 from the first journal-vs-Robinhood dry run (TJDate C5,
-- scripts/journal_broker_match_dry_run.py) and the owner's Robinhood order
-- cards (screenshots, 2026-11-11). Opus review 2026-11-11: FIX-FIRST (account-
-- row precondition, the 179.30 arithmetic, the real Rebuild path and its
-- before-snapshot) -> SHIP, 0 blocking on the confirming round.
--
-- Why:
--   MRVL #419  journal BUY 5 @ 260.00 on 10-14. Robinhood: 10-14 2 @ 260.00
--              (09:35 EDT) + 1 @ 260.00 (09:44), 10-15 2 @ 235.00 (09:45).
--              Real cost 1,250.00 (avg 250.00), not 1,300.00. All 7 MRVL
--              (with #424 2 @ 265) were sold on 10-20 (#435), so the SELL's
--              cost basis 261.4286 / realized -236.03 should be 254.2857 /
--              -186.03. MRVL is held again today (6 from 11-06), unaffected.
--   FSLR #413  journal BUY 5 @ 186.21 on 10-12. Robinhood, same day:
--              3 @ 185.00 + 2 @ 180.00 = 915.00 (avg 183.00).
--   FSLR #429  journal BUY 5 @ 180.00 on 10-16 (09:13 ET). Robinhood has no
--              FSLR fill on 10-16: the 5 shares are 10-14 2 @ 180.00
--              (filled 12:14 PM EDT) + 10-15 3 @ 179.30 (filled 09:39 AM EDT).
--              179.30 is the 2-dp price the broker-log path writes (the exact
--              fill is 179.3037, P0 probe Q3): 360.00 + 537.90 = 897.90.
--              FSLR is still held: 10 shares, avg 183.105 today; after the
--              Rebuild (915.00 + 360.00 + 537.90) / 10 = 181.29.
--
-- What the DO block does (any failed check rolls the whole block back):
--   1. checks #419, #413 and #429 still hold exactly the values above with no
--      broker link; that both FSLR Robinhood records exist at the prices used;
--      that neither fill id is on a trade and no FSLR BUY sits on 10-14 / 10-15
--      (re-run refuses); and that no account row since 10-14 has provenance
--      but no book (else a nightly "no_book" refusal and a day left out of
--      Reports for good);
--   2. MRVL #419: price 260.00 -> 250.00 (shares, date unchanged);
--   3. FSLR #413: price 186.21 -> 183.00 (shares, date unchanged);
--   4. FSLR #429: inserts the two real fills linked to their Robinhood records
--      (broker_txn_id), oldest first; every owner-written field of #429 goes on
--      the LATEST fill (10-15), trigger_type / asset_type / notes on both
--      (plan decision U7'); then deletes #429. idempotency_key stays NULL (it
--      is unique; never copy it).
--
-- Why #429 is DELETE + INSERT and #419 is price-only (account rows, bug N1):
-- the nightly account-row repair counts a trade as "late" for a past day only
-- by `id > journal_max_trade_id`, and can't see deleted rows. Moving shares
-- EARLIER with new ids repairs exactly on rows that carry provenance and a
-- book: 10-14 FSLR 5 -> 7, 10-15 5 -> 10, and from 10-16 the book already
-- holds 10, so nothing changes. Moving MRVL's 2 shares LATER (10-14 -> 10-15)
-- would leave 10-14 refused as "unexplained" every night, so MRVL keeps its
-- date; its 10-14 account row stays 2 shares high (accepted). Price changes
-- don't touch account rows (they value shares at the close).
--
-- OWNER STEPS, in order (the Supabase SQL editor shows only the LAST
-- statement's result when several run at once, so run each step alone):
--   Run before 2026-11-17: the nightly repair only looks back
--   ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS (35) days, so 10-14 must still be
--   inside the window when the next EOD runs. Best after a broker sync and
--   before the evening EOD run.
--   a. On 🏠 Home, note FSLR's stop and which leg binds (shown with the stop).
--   b. In 📒 Trade Journal, open "⚠️ Fix drift between trades and holdings",
--      click "Run rebuild now" (preview only, writes nothing), note any SELL
--      corrections it already lists (before the SQL), then Cancel.
--   c. Run STEP 0, STEP 0b and STEP 0c, each alone; SAVE all three outputs
--      (the undo record and the before-picture). Check STEP 0b against its
--      expectations; if 10-14 or 10-15 show journal_max_trade_id NULL, stop and
--      tell the lead (those days then need the backfill script, not the nightly
--      repair). If STEP 0 shows any non-null column on #429 that isn't in the
--      INSERT lists below (e.g. premortem_trigger_model), stop and tell the lead.
--   d. Highlight the DO block and Run (expect "applied: ...").
--   e. Run VERIFY 1 alone.
--   f. In 📒 Trade Journal -> "⚠️ Fix drift between trades and holdings" ->
--      "Run rebuild now" (preview), then check, BEFORE "✅ Apply rebuild":
--        - beyond whatever step b showed, exactly ONE new SELL correction:
--          #435 MRVL realized -236.03 -> -186.03 (cost basis 254.29);
--        - no Removed, Added or Shares-changed lines;
--        - FSLR reads 10 @ 181.29; every other ticker's avg cost equals STEP
--          0c's holdings figure to the cent (the rebuild rounds to 4 dp).
--      If anything else appears, press Cancel and tell the lead. Otherwise
--      "✅ Apply rebuild". (Apply also re-saves every holding's asset_type as
--      "stock", bug RbAsset: latent, no reader today.)
--   g. Run VERIFY 2 and VERIFY 3, each alone.
--   h. On 🏠 Home, compare FSLR's stop with step a. The entry leg can drop by
--      up to ~3.21 (first fill 186.21 -> 183.00) and the ratchet leg can move
--      either way (avg cost 183.105 -> 181.29, last buy 10-16 -> 10-15); the
--      trailing leg is unchanged (episode still starts 10-12). If it fell and
--      you want the old level, enter that as a manual stop (a manual stop wins
--      when it is at or above the auto stop).
--   i. Don't regenerate October or the monthly review until after that night's
--      EOD: until the repair runs, Reports may show 10-14 / 10-15 "left out".
--   j. Re-run python scripts/journal_broker_match_dry_run.py: expect MRVL #419
--      FORWARD (2 shares dated before their 10-15 fill; display only), FSLR
--      #413 RECONCILED, the two new FSLR rows linked (no finding), and no FSLR
--      PRICE_FIX / MERGED_FILLS.
-- The FSLR Robinhood records stay "dismissed": the matcher uses every status
-- and decides "linked" by broker_txn_id alone, so no status change is needed.
-- =====================================================================

-- STEP 0 (read-only; run alone and SAVE: the undo record). Expect 3 rows:
-- 413 FSLR BUY 5 @ 186.21 (10-12), 419 MRVL BUY 5 @ 260 (10-14),
-- 429 FSLR BUY 5 @ 180 (10-16), all with broker_txn_id NULL.
select * from public.trades where id in (413, 419, 429) order by id;

-- STEP 0b (read-only; run alone and SAVE). Expect: journal_max_trade_id set
-- (not NULL) on 10-14 and 10-15, with fslr_book 5 on both; fslr_book 10 from
-- 10-16 on for rows with a book; no_book false on every row whose
-- journal_max_trade_id is set (legacy rows, id NULL, have no book and are
-- skipped by the nightly repair); cash_as_of_et NULL, or on/after 12:14 on 10-14
-- and on/after 09:39 on 10-15. If any expectation fails, stop and tell the lead.
select snapshot_date, journal_max_trade_id, repaired_at is not null as repaired,
       book_positions is null as no_book,
       book_positions->'FSLR'->>'shares' as fslr_book,
       book_positions->'MRVL'->>'shares' as mrvl_book,
       cash_as_of at time zone 'America/New_York' as cash_as_of_et
  from public.account_daily_snapshots
 where snapshot_date between date '2026-10-12' and date '2026-10-20'
 order by snapshot_date;

-- STEP 0c (read-only; run alone and SAVE: the before-picture of everything the
-- app's Rebuild can rewrite: every holding and every SELL's stored cost basis /
-- realized P&L).
select 'H' as k, ticker, shares, avg_cost, asset_type,
       null::bigint as id, null::numeric as cost_basis, null::numeric as realized_pnl
  from public.holdings
union all
select 'S', ticker, shares, price, null, id, cost_basis, realized_pnl
  from public.trades where action = 'SELL'
 order by 1, 2, 6;

do $$
declare
    n       int;
    v_new14 bigint;
    v_new15 bigint;
begin
    -- 1a. the three rows still hold exactly what the dry run and SQL showed
    select count(*) into n from public.trades
     where id = 419 and ticker = 'MRVL' and action = 'BUY' and abs(shares - 5) < 1e-6
       and abs(price - 260.00) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-14';
    if n <> 1 then raise exception 'MRVL #419 BUY 5 @ 260.00 on 10-14 (unlinked): expected 1 row, found %', n; end if;

    select count(*) into n from public.trades
     where id = 413 and ticker = 'FSLR' and action = 'BUY' and abs(shares - 5) < 1e-6
       and abs(price - 186.21) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-12';
    if n <> 1 then raise exception 'FSLR #413 BUY 5 @ 186.21 on 10-12 (unlinked): expected 1 row, found %', n; end if;

    select count(*) into n from public.trades
     where id = 429 and ticker = 'FSLR' and action = 'BUY' and abs(shares - 5) < 1e-6
       and abs(price - 180.00) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-16';
    if n <> 1 then raise exception 'FSLR #429 BUY 5 @ 180.00 on 10-16 (unlinked): expected 1 row, found %', n; end if;

    -- 1b. both FSLR fills are Robinhood's records, at the prices written below
    select count(*) into n from public.snaptrade_pending_imports
     where (snaptrade_txn_id = '5e850588-2ec9-4b87-9b7a-a72e84a60256'
            and ticker = 'FSLR' and action = 'BUY' and abs(shares - 2) < 1e-6
            and abs(price - 180.00) < 0.005 and trade_date = date '2026-10-14')
        or (snaptrade_txn_id = '6fff07c9-82a4-480f-bb23-a7a53e87bcc2'
            and ticker = 'FSLR' and action = 'BUY' and abs(shares - 3) < 1e-6
            and abs(price - 179.30) < 0.005 and trade_date = date '2026-10-15');
    if n <> 2 then raise exception 'expected the 2 FSLR Robinhood records (10-14 2 @ 180.00, 10-15 3 @ 179.30), found %', n; end if;

    select count(*) into n from public.trades
     where broker_txn_id in ('5e850588-2ec9-4b87-9b7a-a72e84a60256',
                             '6fff07c9-82a4-480f-bb23-a7a53e87bcc2');
    if n <> 0 then raise exception 'a FSLR fill id is already on % trade row(s): not applying', n; end if;

    -- 1c. no other FSLR BUY already sits on 10-14 / 10-15 (a re-run, or a hand log)
    select count(*) into n from public.trades
     where ticker = 'FSLR' and action = 'BUY'
       and (traded_at at time zone 'America/New_York')::date in (date '2026-10-14', date '2026-10-15');
    if n <> 0 then raise exception 'found % FSLR BUY row(s) already on 10-14 / 10-15: not applying', n; end if;

    -- 1d. no account row the backdated fills are "late" for lacks a book (else a
    --     nightly no_book refusal alarm and that day left out of Reports for good)
    select count(*) into n from public.account_daily_snapshots
     where snapshot_date >= date '2026-10-14'
       and journal_max_trade_id is not null and book_positions is null;
    if n <> 0 then raise exception '% account row(s) since 10-14 have provenance but no book_positions: not applying', n; end if;

    -- 2-3. price-only corrections
    update public.trades set price = 250.00 where id = 419;
    update public.trades set price = 183.00 where id = 413;

    -- 4. FSLR #429 -> its two real fills, oldest first; owner-written fields on the latest
    insert into public.trades (ticker, action, shares, price, traded_at, broker_txn_id,
                               trigger_type, asset_type, notes)
    select ticker, 'BUY', 2, 180.00, timestamptz '2026-10-14 16:14:00+00',
           '5e850588-2ec9-4b87-9b7a-a72e84a60256', trigger_type, asset_type, notes
      from public.trades where id = 429
    returning id into v_new14;

    insert into public.trades (ticker, action, shares, price, traded_at, broker_txn_id,
                               trigger_type, asset_type, notes, signal_seen, followed_signal,
                               deviation_reason, lesson, lesson_category, user_thesis,
                               thesis_source, decision_context, premortem_case_against,
                               premortem_commitment, premortem_trigger_price,
                               premortem_trigger_direction, situational_category)
    select ticker, 'BUY', 3, 179.30, timestamptz '2026-10-15 13:39:00+00',
           '6fff07c9-82a4-480f-bb23-a7a53e87bcc2', trigger_type, asset_type, notes,
           signal_seen, followed_signal, deviation_reason, lesson, lesson_category,
           user_thesis, thesis_source, decision_context, premortem_case_against,
           premortem_commitment, premortem_trigger_price, premortem_trigger_direction,
           situational_category
      from public.trades where id = 429
    returning id into v_new15;

    delete from public.trades where id = 429;

    raise notice 'applied: #419 -> 250.00, #413 -> 183.00, #429 -> #% (10-14 2 @ 180.00) + #% (10-15 3 @ 179.30), #429 deleted',
        v_new14, v_new15;
end $$;

-- VERIFY 1 (run alone, right after the DO block): expect MRVL #419 5 @ 250.00
-- (10-14); FSLR #413 5 @ 183.00 (10-12), then 2 @ 180.00 (10-14 12:14) and
-- 3 @ 179.30 (10-15 09:39), both linked; FSLR cost 915.00 + 360.00 + 537.90 =
-- 1,812.90 over 10 shares; no #429.
select id, ticker, action, shares, price, round(shares * price, 2) as cost,
       traded_at at time zone 'America/New_York' as et_time, broker_txn_id
  from public.trades
 where (ticker = 'MRVL' and id = 419)
    or (ticker = 'FSLR' and action = 'BUY'
        and (traded_at at time zone 'America/New_York')::date between date '2026-10-12' and date '2026-10-16')
 order by ticker, traded_at, id;

-- VERIFY 2 (run alone, AFTER "✅ Apply rebuild"): expect #435 cost_basis
-- 254.2857 (or 254.29), realized_pnl -186.03.
select id, ticker, action, shares, price, cost_basis, realized_pnl
  from public.trades where id = 435;

-- VERIFY 3 (run alone, AFTER "✅ Apply rebuild"): expect FSLR 10 @ 181.29 and
-- MRVL 6 @ 144.1667 (unchanged).
select ticker, shares, avg_cost from public.holdings where ticker in ('FSLR', 'MRVL');
