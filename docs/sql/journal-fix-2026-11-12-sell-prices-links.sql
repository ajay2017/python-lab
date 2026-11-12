-- =====================================================================
-- DRISHTA — one-off journal correction: three SELL prices + four links
-- Written 2026-11-12 from the owner's journal-vs-Robinhood dry run
-- (scripts/journal_broker_match_dry_run.py, 16:53 ET, after the 4:16 PM ET
-- sync). Owner decision UE-3 (b), docs/plans/journal-vs-robinhood-u8.md: fix
-- by hand now, because the matcher only compares the last
-- JOURNAL_MATCH_LOOKBACK_DAYS (35) days and ORCL #412 leaves it after 11-16.
-- Opus review 2026-11-12: FIX-FIRST (no Rebuild before-picture; the #484 /
-- #478 price check tighter than the matcher's LINK rule) -> SHIP, 0 blocking
-- on the confirming round.
--
-- Why:
--   ORCL #412  journal SELL 10 @ 139.22 on 10-12. Robinhood d2f4477f…: 139.12.
--   TSLA #420  journal SELL 5  @ 366.54 on 10-14. Robinhood a6156fe2…: 366.24.
--   SPCX #451  journal SELL 10 @ 136.25 on 10-30. Robinhood: 2 fills that day
--              (0487b8db…, eafc8d6b…) averaging 136.38 (dry run, 2 dp).
--   SPCX #484  journal BUY 5 on 11-10 matches Robinhood 4e0f9931… (matcher LINK:
--              same session, price within JOURNAL_MATCH_PRICE_TOL_USD 0.01), not linked.
--   CSCO #478  journal SELL 15 on 11-06 matches Robinhood 2b659de3… the same way.
--              Both stayed unlinked through several clean syncs, so their 2-dp
--              prices are likely a cent apart (the cron's Tier-2 linker needs
--              them equal); link only, the journal price is kept.
--   All six Robinhood records are "dismissed" in Pending Imports (hand-logged
--   at the time). Net realized P&L effect of the three prices, before the
--   Rebuild: ORCL -1.00, TSLA -1.50, SPCX about +1.30.
--
-- What the DO block does (any failed check rolls the whole block back):
--   1. checks each journal row still holds exactly the values above, unlinked;
--      each Robinhood record exists with the matching ticker / action / date /
--      shares (SPCX #451: exactly 2 SPCX SELL records on 10-30, summing to 10,
--      average within a cent of 136.38; #484 / #478: the matcher's own rule,
--      the record's raw price, else its stored price, within 0.01 of the
--      journal's); that no row being linked has a traded_at of exactly
--      00:00:00 UTC (a linked row's midnight time is re-anchored to 16:00 ET
--      by trade_time.normalize_traded_at, which would move its ET date); and
--      that none of the six ids is on a trade already;
--   2. ORCL #412 / TSLA #420: price := the Robinhood record's price, and link;
--   3. SPCX #451: price := the 2 fills' share-weighted average (4 dp). Not
--      linked: one journal row can carry one fill id only, so the matcher keeps
--      reading it as RECONCILED (two fills, one row), like FSLR #413;
--   4. SPCX #484 / CSCO #478: link only.
--   Shares and dates are unchanged on every row, so account rows (which value
--   shares at the close) and holdings share counts are untouched. A SELL's
--   price does not enter average cost, so no holding's avg_cost changes; only
--   the three SELLs' stored realized P&L is stale until the app's Rebuild.
--   MRVL #419 (FORWARD) is deliberately NOT changed: see
--   journal-fix-2026-11-11-mrvl-fslr.sql (moving its 2 shares to 10-15 would
--   leave the 10-14 account row refused as "unexplained" every night).
--   The Robinhood records stay "dismissed": the matcher decides "linked" by
--   broker_txn_id alone.
--
-- OWNER STEPS, in order (the Supabase SQL editor shows only the LAST
-- statement's result when several run at once, so run each step alone):
--   a. In 📒 Trade Journal, open "⚠️ Fix drift between trades and holdings",
--      click "Run rebuild now" (preview only, writes nothing), note any SELL
--      corrections it already lists, then Cancel. If #412, #420 or #451 is
--      already listed, stop and tell the lead.
--   b. Run STEP 0, STEP 0b, STEP 0c and STEP 0d, each alone; SAVE all four
--      outputs (the undo record and the before-picture of everything the
--      Rebuild can rewrite). Check STEP 0 / 0b against their expectations; if
--      any row differs, stop and tell the lead.
--   c. Highlight the DO block and Run (expect "applied: ...").
--   d. Run VERIFY 1 alone.
--   e. 📒 Trade Journal -> "⚠️ Fix drift between trades and holdings" ->
--      "Run rebuild now" (preview), then check, BEFORE "✅ Apply rebuild":
--        - beyond whatever step a showed, exactly THREE new SELL corrections:
--          #412 ORCL (realized 1.00 lower), #420 TSLA (1.50 lower),
--          #451 SPCX (about 1.30 higher), each to within a cent, with its
--          corrected basis equal to STEP 0's cost_basis to the cent;
--        - no Removed, Added or Shares-changed lines;
--        - the "Recomputed holdings" table's avg cost for EVERY ticker equals
--          STEP 0c's holdings (k = 'H') avg_cost to the cent (the preview's
--          change list compares shares only, so check the table by eye).
--      If anything else appears, press Cancel and tell the lead. Otherwise
--      "✅ Apply rebuild".
--   f. Run VERIFY 2 alone.
--   g. Re-run python scripts/journal_broker_match_dry_run.py: expect no
--      PRICE_FIX and no LINK findings; SPCX #451 RECONCILED (2 fills); ORCL
--      #412 / TSLA #420 / SPCX #484 / CSCO #478 no finding; MRVL #419 still
--      FORWARD (accepted, display only); every other finding unchanged from
--      the 11-12 run (e.g. TSLA #433, MRVL #454, WAL #436, CRWD #417, FSLR
--      #413 still RECONCILED).
--   Note: Monthly Decision Review / debrief text already generated keeps the
--   old figures until it is regenerated; Tax, Performance Review and Self
--   Track Record read trades live.
-- =====================================================================

-- STEP 0 (read-only; run alone and SAVE: the undo record). Expect 5 rows:
-- 412 ORCL SELL 10 @ 139.22 (10-12), 420 TSLA SELL 5 @ 366.54 (10-14),
-- 451 SPCX SELL 10 @ 136.25 (10-30), 478 CSCO SELL 15 (11-06),
-- 484 SPCX BUY 5 (11-10); broker_txn_id NULL on all five; no et_time of
-- exactly 20:00:00 / 19:00:00 (midnight UTC). Note #478's and #484's prices
-- for the STEP 0b comparison.
select id, ticker, action, shares, price, cost_basis, realized_pnl,
       traded_at at time zone 'America/New_York' as et_time, broker_txn_id
  from public.trades where id in (412, 420, 451, 478, 484) order by id;

-- STEP 0b (read-only; run alone and SAVE). Expect 6 rows, one per id below,
-- with the ticker / action / date / price the header names; the two SPCX
-- 10-30 rows' shares sum to 10; status 'dismissed' on all six (if any reads
-- 'pending', tell the lead: the next sync would flip it to 'logged'); the
-- 4e0f9931 / 2b659de3 raw_price within a cent of #484 / #478's STEP 0 price.
select snaptrade_txn_id, ticker, action, shares, price,
       raw_json->>'price' as raw_price, trade_date, status
  from public.snaptrade_pending_imports
 where snaptrade_txn_id in ('d2f4477f-7948-492d-9ca8-d8969e03b4af',
                            'a6156fe2-8057-4c7a-b861-93f9c7ec4fa9',
                            '0487b8db-bc14-4f33-88e7-eb6d6a51c9a3',
                            'eafc8d6b-90be-467e-b9c7-ebab65816e19',
                            '4e0f9931-18af-4c84-8bf8-5858c4a519b4',
                            '2b659de3-a9ef-4ca6-93dd-934213e67b77')
 order by trade_date, ticker, snaptrade_txn_id;

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

-- STEP 0d (read-only; run alone and SAVE): the earliest linked trade today.
-- capital_vs_margin.golive_floor reads it; linking ORCL #412 (10-12) moves it
-- only if this is later than 2026-10-12 (if so, note it for the lead; not a stop).
select min(traded_at) at time zone 'America/New_York' as earliest_linked_et
  from public.trades where broker_txn_id is not null;

do $$
declare
    n       int;
    v_orcl  numeric;
    v_tsla  numeric;
    v_spcx  numeric;
    v_sh    numeric;
begin
    -- 1a. the five journal rows still hold exactly what the dry run showed
    select count(*) into n from public.trades
     where id = 412 and ticker = 'ORCL' and action = 'SELL' and abs(shares - 10) < 1e-6
       and abs(price - 139.22) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-12';
    if n <> 1 then raise exception 'ORCL #412 SELL 10 @ 139.22 on 10-12 (unlinked): expected 1 row, found %', n; end if;

    select count(*) into n from public.trades
     where id = 420 and ticker = 'TSLA' and action = 'SELL' and abs(shares - 5) < 1e-6
       and abs(price - 366.54) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-14';
    if n <> 1 then raise exception 'TSLA #420 SELL 5 @ 366.54 on 10-14 (unlinked): expected 1 row, found %', n; end if;

    select count(*) into n from public.trades
     where id = 451 and ticker = 'SPCX' and action = 'SELL' and abs(shares - 10) < 1e-6
       and abs(price - 136.25) < 0.005 and broker_txn_id is null
       and (traded_at at time zone 'America/New_York')::date = date '2026-10-30';
    if n <> 1 then raise exception 'SPCX #451 SELL 10 @ 136.25 on 10-30 (unlinked): expected 1 row, found %', n; end if;

    -- 1b. ORCL / TSLA: one Robinhood record each, same ticker / action / shares / date
    select max(price), count(*) into v_orcl, n from public.snaptrade_pending_imports
     where snaptrade_txn_id = 'd2f4477f-7948-492d-9ca8-d8969e03b4af'
       and ticker = 'ORCL' and action = 'SELL' and abs(shares - 10) < 1e-6
       and trade_date = date '2026-10-12';
    if n <> 1 or abs(v_orcl - 139.12) > 0.01 then
        raise exception 'ORCL Robinhood record d2f4477f (SELL 10 @ 139.12, 10-12): found % row(s), price %', n, v_orcl; end if;

    select max(price), count(*) into v_tsla, n from public.snaptrade_pending_imports
     where snaptrade_txn_id = 'a6156fe2-8057-4c7a-b861-93f9c7ec4fa9'
       and ticker = 'TSLA' and action = 'SELL' and abs(shares - 5) < 1e-6
       and trade_date = date '2026-10-14';
    if n <> 1 or abs(v_tsla - 366.24) > 0.01 then
        raise exception 'TSLA Robinhood record a6156fe2 (SELL 5 @ 366.24, 10-14): found % row(s), price %', n, v_tsla; end if;

    -- 1c. SPCX #451: the two named records are the ONLY SPCX SELL records on
    --     10-30 (the matcher's RECONCILED uses the whole day), 10 shares,
    --     average within a cent of 136.38
    select count(*) into n from public.snaptrade_pending_imports
     where ticker = 'SPCX' and action = 'SELL' and trade_date = date '2026-10-30';
    if n <> 2 then raise exception 'expected exactly 2 SPCX SELL records on 10-30, found %', n; end if;

    select count(*), sum(shares), sum(shares * price) / nullif(sum(shares), 0)
      into n, v_sh, v_spcx
      from public.snaptrade_pending_imports
     where snaptrade_txn_id in ('0487b8db-bc14-4f33-88e7-eb6d6a51c9a3',
                                'eafc8d6b-90be-467e-b9c7-ebab65816e19')
       and ticker = 'SPCX' and action = 'SELL' and trade_date = date '2026-10-30';
    if n <> 2 or abs(v_sh - 10) >= 1e-6 or abs(v_spcx - 136.38) > 0.01 then
        raise exception 'SPCX 10-30 Robinhood records: found % row(s), % shares, average %', n, v_sh, v_spcx; end if;

    -- 1d. SPCX #484 / CSCO #478: the journal row matches its record (date, shares; raw price, else stored, within 0.01)
    select count(*) into n
      from public.trades t join public.snaptrade_pending_imports p
        on p.snaptrade_txn_id = '4e0f9931-18af-4c84-8bf8-5858c4a519b4'
     where t.id = 484 and t.ticker = 'SPCX' and t.action = 'BUY' and abs(t.shares - 5) < 1e-6
       and t.broker_txn_id is null
       and p.ticker = t.ticker and p.action = t.action and abs(p.shares - t.shares) < 1e-6
       and abs(coalesce((p.raw_json->>'price')::numeric, p.price) - t.price) <= 0.01
       and p.trade_date = (t.traded_at at time zone 'America/New_York')::date
       and p.trade_date = date '2026-11-10';
    if n <> 1 then raise exception 'SPCX #484 BUY 5 on 11-10 vs record 4e0f9931: expected 1 match, found %', n; end if;

    select count(*) into n
      from public.trades t join public.snaptrade_pending_imports p
        on p.snaptrade_txn_id = '2b659de3-a9ef-4ca6-93dd-934213e67b77'
     where t.id = 478 and t.ticker = 'CSCO' and t.action = 'SELL' and abs(t.shares - 15) < 1e-6
       and t.broker_txn_id is null
       and p.ticker = t.ticker and p.action = t.action and abs(p.shares - t.shares) < 1e-6
       and abs(coalesce((p.raw_json->>'price')::numeric, p.price) - t.price) <= 0.01
       and p.trade_date = (t.traded_at at time zone 'America/New_York')::date
       and p.trade_date = date '2026-11-06';
    if n <> 1 then raise exception 'CSCO #478 SELL 15 on 11-06 vs record 2b659de3: expected 1 match, found %', n; end if;

    -- 1d2. no row being linked carries a traded_at of exactly 00:00:00 UTC
    --      (normalize_traded_at re-anchors a linked row's midnight to 16:00 ET)
    select count(*) into n from public.trades
     where id in (412, 420, 478, 484)
       and (traded_at at time zone 'UTC')::time = time '00:00:00';
    if n <> 0 then raise exception '% row(s) to link have traded_at exactly 00:00:00 UTC: not applying', n; end if;

    -- 1e. none of the six Robinhood ids is on a trade already (a re-run refuses here)
    select count(*) into n from public.trades
     where broker_txn_id in ('d2f4477f-7948-492d-9ca8-d8969e03b4af',
                             'a6156fe2-8057-4c7a-b861-93f9c7ec4fa9',
                             '0487b8db-bc14-4f33-88e7-eb6d6a51c9a3',
                             'eafc8d6b-90be-467e-b9c7-ebab65816e19',
                             '4e0f9931-18af-4c84-8bf8-5858c4a519b4',
                             '2b659de3-a9ef-4ca6-93dd-934213e67b77');
    if n <> 0 then raise exception 'a Robinhood id below is already on % trade row(s): not applying', n; end if;

    -- 2. ORCL / TSLA: Robinhood's price, and link
    update public.trades set price = v_orcl,
           broker_txn_id = 'd2f4477f-7948-492d-9ca8-d8969e03b4af' where id = 412;
    update public.trades set price = v_tsla,
           broker_txn_id = 'a6156fe2-8057-4c7a-b861-93f9c7ec4fa9' where id = 420;

    -- 3. SPCX #451: the two fills' average (not linked: one row, two fills)
    update public.trades set price = round(v_spcx, 4) where id = 451;

    -- 4. links only
    update public.trades set broker_txn_id = '4e0f9931-18af-4c84-8bf8-5858c4a519b4' where id = 484;
    update public.trades set broker_txn_id = '2b659de3-a9ef-4ca6-93dd-934213e67b77' where id = 478;

    raise notice 'applied: #412 -> %, #420 -> %, #451 -> % (avg of 2 fills), #412/#420/#478/#484 linked',
        v_orcl, v_tsla, round(v_spcx, 4);
end $$;

-- VERIFY 1 (run alone, right after the DO block): expect #412 ORCL 139.12 and
-- #420 TSLA 366.24, both linked; #451 SPCX at the 2-fill average (~136.38),
-- unlinked; #478 / #484 linked, prices unchanged; shares and dates as STEP 0.
select id, ticker, action, shares, price,
       traded_at at time zone 'America/New_York' as et_time, broker_txn_id
  from public.trades where id in (412, 420, 451, 478, 484) order by id;

-- VERIFY 2 (run alone, AFTER "✅ Apply rebuild"): expect cost_basis equal to
-- STEP 0's on all three (to the cent); realized_pnl = STEP 0's minus 1.00
-- (#412), minus 1.50 (#420), plus 10 x (new price - 136.25) (#451, about
-- +1.30), each to within a cent. Holdings avg_cost = STEP 0c's to the cent.
select id, ticker, action, shares, price, cost_basis, realized_pnl
  from public.trades where id in (412, 420, 451) order by id;
