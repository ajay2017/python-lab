-- =====================================================================
-- DRISHTA — one-off journal correction: hand-logged fills timed at LOG time
-- Written 2026-11-07 (bug register N1-LogTime; triage in
-- docs/sql/data-quality-triage-2026-11-07.sql Q2d-Q2f).
--
-- Why: eight BUY rows carry the right ET DATE but the time they were
-- hand-logged (evening), not the time they filled. The N1 account repair
-- counts a trade toward a day only when traded_at <= that day's broker cash
-- read (cash_as_of, ~16:15-17:05 ET), and the cash read already included each
-- of these fills, so the account row for each day leaves the position out
-- while its cash paid for it (net equity understated). The three frozen C7
-- baseline rows (09-22 / 09-29 / 09-30) are this class, but see AFTERWARDS:
-- this file alone does not repair them.
--
-- Robinhood "Filled" times (owner screenshots, 2026-11-07), all before that
-- day's cash read:
--   id   ticker  fill              journal (log) time   cash read
--   370  ENVA    5 @ 172.00   09-15 13:11 EDT   17:11:01            17:02:28
--   371  CRM     2 @ 260.00   09-15 13:30 EDT   17:14:48            17:02:28
--   388  ON     10 @  73.00   09-22 14:22 EDT   18:19:32            17:02:55
--   396  META    1 @ 718.00   09-29 12:45 EDT   20:27:09            17:00:27
--   398  MU      1 @ 1075.88  09-30 16:05 EDT   19:52:29            17:04:08
--   434  CRM     5 @ 240.00   10-19 11:47 EDT   17:07:54            17:00:50
--   470  CSCO    5 @ 103.00   11-04 11:02 EST   19:12:59            16:19:57
--   471  CSCO    3 @ 102.00   11-04 12:16 EST   19:14:02            16:19:57
-- (#398 filled after the 16:00 close but before that day's 17:04 cash read,
-- so it still belongs to 09-30's book.) Robinhood shows minutes, so seconds
-- are written as :00.
--
-- What it does, in ONE block (any failed check rolls the whole block back):
--   1. checks each of the 8 rows exists with exactly the ticker / action /
--      shares / price / current time shown above (to the second), so a re-run
--      or a changed row refuses;
--   2. refuses if another trade in the same ticker lies between a row's new
--      and old time (the replay order of that ticker would change);
--   3. prints the old values (raise notice), then sets ONLY traded_at.
-- Shares, price, notes, broker_txn_id and every other column are untouched.
-- The ET calendar date of every row is unchanged. Side effect, a correction:
-- the UTC date of #396 (was 09-30 UTC), #470 and #471 (were 11-05 UTC) moves
-- back to the fill day. Readers that take the UTC .date() move with it, all
-- corrections: tax lots (bug register #29 f/u), self_track_record, and the
-- _to_date copies in recommendations_history / trade_review / r_multiple;
-- behavioral_fingerprint's time-of-day bucket changes for all 8 (a correction).
--
-- Owner, in the Supabase SQL editor (it shows only the LAST statement's
-- result when several run at once): highlight STEP 0 and Run, and SAVE the
-- result (the undo record). Then highlight the DO block and Run. Then run the
-- two verification queries, each on its own.
--
-- AFTERWARDS (not done by this file; traced by the Opus review 2026-11-07):
--   - 11-04 (a row with provenance) is repaired by the next auto-repair: the
--     Sunday debrief pre-step (11-08) or the next weekday EOD, whichever runs
--     first. Until then the stale-row check flags 11-04. Its per-ticker rows
--     follow once N1 C7 has shipped (C7 is reviewed separately, not yet live).
--   - 09-15 and 10-19 (legacy rows): `python scripts/account_snapshot_backfill.py`
--     (dry run) should list them as repairs: `missing_ticker` if the ticker
--     wasn't held before, else `partial_quantity` needing
--     `--apply-partial 2026-09-15,2026-10-19` (CRM on 10-19 probably).
--   - 09-22, 09-29 and 09-30 are NOT repaired by any existing tool: their
--     per-ticker rows already hold the trade, so after this fix the backfill
--     moves them from "refused" to "needs review" (gaps ~ -736 / -739 /
--     -1,065) and never applies a review row, and C7 only touches rows with
--     repaired_at. They stay as the C7 baseline until the owner decides on a
--     whole-book repair for them. This file makes nothing worse for them.
-- =====================================================================

-- STEP 0 (read-only; run and SAVE the output first: it is the undo record).
-- Expect the 8 rows above, with the journal (log) times shown.
select id, ticker, action, shares, price, traded_at,
       traded_at at time zone 'America/New_York' as traded_et,
       broker_txn_id, trigger_type
  from public.trades
 where id in (370, 371, 388, 396, 398, 434, 470, 471)
 order by id;

do $$
declare
    fix record;
    cur record;
    n   int;
begin
    for fix in
        select * from (values
            (370::bigint, 'ENVA', 5::numeric,  172.00::numeric,  timestamp '2026-09-15 17:11:01', timestamp '2026-09-15 13:11:00'),
            (371,         'CRM',  2,           260.00,           timestamp '2026-09-15 17:14:48', timestamp '2026-09-15 13:30:00'),
            (388,         'ON',   10,          73.00,            timestamp '2026-09-22 18:19:32', timestamp '2026-09-22 14:22:00'),
            (396,         'META', 1,           718.00,           timestamp '2026-09-29 20:27:09', timestamp '2026-09-29 12:45:00'),
            (398,         'MU',   1,           1075.88,          timestamp '2026-09-30 19:52:29', timestamp '2026-09-30 16:05:00'),
            (434,         'CRM',  5,           240.00,           timestamp '2026-10-19 17:07:54', timestamp '2026-10-19 11:47:00'),
            (470,         'CSCO', 5,           103.00,           timestamp '2026-11-04 19:12:59', timestamp '2026-11-04 11:02:00'),
            (471,         'CSCO', 3,           102.00,           timestamp '2026-11-04 19:14:02', timestamp '2026-11-04 12:16:00')
        ) as v(id, ticker, shares, price, old_et, new_et)
    loop
        -- 1. the row is exactly as the triage saw it (old ET time to the second)
        select count(*) into n from public.trades t
         where t.id = fix.id and t.ticker = fix.ticker and t.action = 'BUY'
           and abs(t.shares - fix.shares) < 1e-6 and abs(t.price - fix.price) < 0.005
           and date_trunc('second', t.traded_at at time zone 'America/New_York') = fix.old_et;
        if n <> 1 then
            raise exception 'trade #% (% BUY % @ %, at % ET): not found as expected (re-run, or the row changed) - nothing applied',
                fix.id, fix.ticker, fix.shares, fix.price, fix.old_et;
        end if;

        -- 2. no other trade in the same ticker between the new and old time
        select count(*) into n from public.trades t
         where t.ticker = fix.ticker and t.id <> fix.id
           and t.traded_at >= (fix.new_et at time zone 'America/New_York')
           and t.traded_at <= (fix.old_et at time zone 'America/New_York') + interval '1 second';
        if n <> 0 then
            raise exception 'trade #% (%): % other % trade(s) between % and % ET would change the replay order - nothing applied',
                fix.id, fix.ticker, n, fix.ticker, fix.new_et, fix.old_et;
        end if;

        -- 3. print the old value (second copy of the undo record), then fix the time
        select id, traded_at into cur from public.trades where id = fix.id;
        raise notice 'before: id % % traded_at % -> % ET', cur.id, fix.ticker, cur.traded_at, fix.new_et;
        update public.trades
           set traded_at = (fix.new_et at time zone 'America/New_York')
         where id = fix.id;
    end loop;
    raise notice 'applied: 8 fill times corrected (traded_at only)';
end $$;

-- Verify 1: expect the 8 rows at the Robinhood fill times above, same ET dates.
select id, ticker, shares, price,
       traded_at at time zone 'America/New_York' as traded_et,
       broker_txn_id is not null as linked
  from public.trades
 where id in (370, 371, 388, 396, 398, 434, 470, 471)
 order by traded_at;

-- Verify 2: the triage query Q2e again (trades timed after their day's cash
-- read, 16:00:00 anchors excluded). Expect 0 rows, unless a new evening-
-- logged trade has landed since the 2026-11-07 triage (then check it the same way).
select t.id, t.ticker, t.traded_at at time zone 'America/New_York' as traded_et,
       a.cash_as_of at time zone 'America/New_York' as cash_read_et
  from public.trades t
  join public.account_daily_snapshots a
    on a.snapshot_date = (t.traded_at at time zone 'America/New_York')::date
 where a.cash_as_of is not null
   and t.traded_at > a.cash_as_of
   and (t.traded_at at time zone 'America/New_York')::time <> time '16:00:00'
 order by t.traded_at, t.id;
