-- Weekly data-quality triage for the Saturday 2026-11-07 email (bug register
-- DQ-C7, DQ-TSLA, DQ-Orphan). READ-ONLY: every statement is a SELECT.
-- Run each block alone in the Supabase SQL editor and paste the results back.

-- Q1. C7 (16 rows vs baseline 3): is each gap a day the N1 repair rewrote
-- account gross_book while the per-ticker daily_snapshots rows stayed stale?
-- Expect: the 3 baseline rows (2026-09-22/29/30) with repaired_at NULL, and
-- the 13 new rows with repaired_at set. A NEW row with repaired_at NULL
-- is unexplained and is the one that matters.
SELECT a.snapshot_date,
       a.gross_book,
       ROUND(d.book, 2)               AS snapshots_sum,
       ROUND(a.gross_book - d.book, 2) AS gap,
       a.original_gross_book,
       a.repaired_at,
       d.n_tickers,
       (SELECT COUNT(*) FROM jsonb_object_keys(a.book_positions)) AS n_book_positions
FROM account_daily_snapshots a
JOIN (SELECT snapshot_date, SUM(shares * close_price) AS book, COUNT(*) AS n_tickers
      FROM daily_snapshots GROUP BY 1) d ON d.snapshot_date = a.snapshot_date
WHERE ABS(a.gross_book - d.book) > 5
ORDER BY a.snapshot_date;

-- Q1 RESULT (owner, 2026-11-07): 16 rows. The 3 baseline rows (09-22/29/30)
-- have repaired_at NULL and no book_positions. All 13 new rows have
-- repaired_at, and on every one snapshots_sum = original_gross_book to the
-- cent, so daily_snapshots still carry the pre-repair book. 11 repaired by
-- the C3 backfill (2026-11-02 01:50 UTC), 10-28 by the post-journal-fix
-- re-run (11-02 13:41 UTC), 11-05 by the C5 nightly auto-repair (11-06 21:31
-- UTC). Every new gap is positive (late buys). No unexplained row.

-- Q2. For the C7 gap days, which tickers differ between the account row's
-- book_positions and daily_snapshots? (Corrected 2026-11-07: the C3 backfill
-- also wrote book_positions on the legacy rows it repaired, so this covers
-- all 13 repaired days, not only rows from 2026-11-02 on.)
SELECT a.snapshot_date, k.ticker,
       (a.book_positions -> k.ticker ->> 'shares')::numeric AS book_shares,
       s.shares                                              AS snapshot_shares
FROM account_daily_snapshots a
CROSS JOIN LATERAL jsonb_object_keys(a.book_positions) AS k(ticker)
LEFT JOIN daily_snapshots s ON s.snapshot_date = a.snapshot_date AND s.ticker = k.ticker
WHERE a.book_positions IS NOT NULL
  AND (s.shares IS NULL
       OR ABS(s.shares - (a.book_positions -> k.ticker ->> 'shares')::numeric) >= 1)
ORDER BY a.snapshot_date, k.ticker;

-- Q2b. The reverse: a daily_snapshots row whose ticker the account row's
-- book no longer holds (a late-logged full exit; C7 deletes these).
SELECT s.snapshot_date, s.ticker, s.shares, s.close_price
FROM daily_snapshots s
JOIN account_daily_snapshots a ON a.snapshot_date = s.snapshot_date
WHERE a.book_positions IS NOT NULL
  AND NOT (a.book_positions ? s.ticker)
ORDER BY s.snapshot_date, s.ticker;

-- Q2c. CSCO on Nov 3-6: the duplicate trade deleted 2026-11-06 is invisible
-- to the N1 repair (it only adds late trades), so both tables may still
-- count it. Robinhood held 13 before the 11-05 buy of 2, then 15; sold 15
-- on 11-06.
SELECT a.snapshot_date,
       a.book_positions -> 'CSCO' ->> 'shares' AS book_csco,
       s.shares                                AS snapshot_csco,
       a.repaired_at
FROM account_daily_snapshots a
LEFT JOIN daily_snapshots s ON s.snapshot_date = a.snapshot_date AND s.ticker = 'CSCO'
WHERE a.snapshot_date BETWEEN '2026-11-03' AND '2026-11-06'
ORDER BY a.snapshot_date;

-- Q2 RESULT (owner, 2026-11-07): 26 ticker-days on the 13 repaired dates,
-- every one book_shares > snapshot_shares (12 tickers missing, 14 short).
-- Q2b RESULT: no rows (no late full exits). Q2c RESULT: CSCO 11-03 5/5,
-- 11-04 5/5 (repaired_at NULL), 11-05 book 15 / snapshot 13 (repaired),
-- 11-06 none/none. The deleted duplicate inflates no row. Open: 11-04 reads
-- 5 in both tables, but Robinhood's two 11-04 fills (5 @ 103, 3 @ 102) on top
-- of 5 held make 13 at that close -> Q2d.

-- Q2d. CSCO journal rows around Nov 3-6, with ET dates: are the two 11-04
-- fills dated 11-05 in the journal (a TJDate mis-date, fixed by a journal
-- correction like 10-28's), or dated 11-04 (then the nightly repair refused
-- the 11-04 account row)?
SELECT id, action, shares, price,
       traded_at AT TIME ZONE 'America/New_York' AS traded_et,
       broker_txn_id, trigger_type, LEFT(notes, 60) AS notes_start
       -- (trades has no created_at; a higher id = logged later)
FROM trades
WHERE ticker = 'CSCO'
  AND traded_at >= '2026-10-30' AND traded_at < '2026-11-08'
ORDER BY traded_at, id;

-- Q2d RESULT (owner, 2026-11-07): #453 BUY 5 @ 104 10-30 16:00 ET (linked);
-- #470 BUY 5 @ 103 11-04 19:12:59 ET and #471 BUY 3 @ 102 11-04 19:14:02 ET
-- (both unlinked, hand-logged; Robinhood filled the 3 @ 102 at 12:16 PM EST);
-- #475 BUY 2 @ 98 11-05 16:00 (linked); #478 SELL 15 11-06 11:09. The DATES
-- are right; the TIMES are the log time. The repair counts a trade toward a
-- day only when traded_at <= that row's cash_as_of (~16:15 ET), so #470/#471
-- read as after-hours and 11-04 was never repaired (book 5 CSCO, cash already
-- paid for 8): the plan's documented "logged after EOD" residual, first seen
-- live. 11-04 net equity is understated by ~8 x CSCO.

-- Q2e. The whole class: every trade whose journal time is AFTER that day's
-- broker cash read. A real after-hours fill belongs here legitimately (the
-- cash read didn't include it); a regular-hours fill whose time is the LOG
-- time does not. Check each against Robinhood's "Filled" time. 16:00:00 ET
-- exactly is the importer's date-only anchor and is excluded.
SELECT t.id, t.ticker, t.action, t.shares, t.price,
       t.traded_at AT TIME ZONE 'America/New_York'  AS traded_et,
       a.cash_as_of AT TIME ZONE 'America/New_York' AS cash_read_et,
       t.broker_txn_id IS NOT NULL                  AS linked,
       a.repaired_at
FROM trades t
JOIN account_daily_snapshots a
  ON a.snapshot_date = (t.traded_at AT TIME ZONE 'America/New_York')::date
WHERE a.cash_as_of IS NOT NULL
  AND t.traded_at > a.cash_as_of
  AND (t.traded_at AT TIME ZONE 'America/New_York')::time <> TIME '16:00:00'
ORDER BY t.traded_at, t.id;

-- Q2e RESULT (owner, 2026-11-07): 8 trades, none of their days repaired:
-- #370 ENVA 5 @ 172 09-15 17:11, #371 CRM 2 @ 260 09-15 17:14 (cash read
-- 17:02); #388 ON 10 @ 73 09-22 18:19; #396 META 1 @ 718 09-29 20:27
-- (unlinked; after 20:00, so not a fill time); #398 MU 1 @ 1075.88 09-30
-- 19:52; #434 CRM 5 @ 240 10-19 17:07; #470 / #471 CSCO 11-04 19:12 / 19:14
-- (unlinked). The three C7 BASELINE rows are this class: 09-22 gap -736.01 =
-- ON 10 @ 73, 09-29 -738.79 = META 1, 09-30 -1065.11 = MU 1 (the app's
-- post-close snapshot writer counted the trade; the account row didn't).

-- Q2f. The broker's own time for each Q2e trade. Linked rows join on the txn
-- id; unlinked rows on ticker + action + shares + ET date. SnapTrade's time
-- is the ORDER PLACEMENT time (P0, 2026-11-06): equal to the fill for an
-- order that fills at once, earlier for a resting limit (CSCO 3 @ 102 placed
-- 11:39, filled 12:16 EST). A row linked by the Tier-2 backfill never had a
-- pending import, so broker_et is NULL there: check Robinhood's "Filled".
SELECT t.id, t.ticker, t.action, t.shares, t.price,
       t.traded_at AT TIME ZONE 'America/New_York'                       AS journal_et,
       ((p.raw_json ->> 'trade_date')::timestamptz) AT TIME ZONE 'America/New_York' AS broker_et,
       p.price  AS broker_price,
       p.status AS pending_status,
       (t.broker_txn_id IS NOT NULL AND p.snaptrade_txn_id = t.broker_txn_id) AS matched_by_id
FROM trades t
LEFT JOIN snaptrade_pending_imports p
  ON (t.broker_txn_id IS NOT NULL AND p.snaptrade_txn_id = t.broker_txn_id)
  OR (t.broker_txn_id IS NULL AND p.ticker = t.ticker AND p.action = t.action
      AND p.shares = t.shares
      AND p.trade_date::date = (t.traded_at AT TIME ZONE 'America/New_York')::date)
WHERE t.id IN (370, 371, 388, 396, 398, 434, 470, 471)
ORDER BY t.id, broker_et;

-- Q2f RESULT (owner, 2026-11-07): broker time found for 3 (all LOG times in
-- the journal): #396 META placed 09-29 12:43:10 ET @ 718; #470 CSCO 5 placed
-- 11-04 11:01:06 @ 103; #471 CSCO 3 placed 11-04 11:39:32, filled 12:16 EST
-- (owner's Robinhood check). NULL for the 5 linked by the Tier-2 backfill
-- (#370 ENVA, #371 CRM, #388 ON, #398 MU, #434 CRM): the owner reads
-- Robinhood's "Filled" time for each before the journal time fix is written.

-- Q3. The TSLA duplicate thesis review: when were the two rows written?
-- Seconds apart = a double-click; same Sunday evening = cron + manual;
-- a week or more apart = identical inputs re-reviewed (a legitimate repeat).
SELECT id, ticker, reviewed_at AT TIME ZONE 'America/New_York' AS reviewed_et,
       status, inputs_hash, LEFT(summary, 80) AS summary_start
FROM thesis_reviews
WHERE ticker = 'TSLA'
  AND inputs_hash IN (SELECT inputs_hash FROM thesis_reviews
                      WHERE ticker = 'TSLA' AND inputs_hash NOT LIKE 'earnings_%'
                      GROUP BY inputs_hash HAVING COUNT(*) > 1)
ORDER BY reviewed_at;

-- Q4. The orphan cache rows: how many rows and how old.
SELECT 'bundle_cache' AS tbl, ticker, COUNT(*) AS n, MAX(fetched_at)::date AS last_touch
FROM bundle_cache WHERE ticker = 'INTL' GROUP BY ticker
UNION ALL
SELECT 'sentiment_llm_cache', ticker, COUNT(*), MAX(score_date)::date
FROM sentiment_llm_cache WHERE ticker = 'INTL' GROUP BY ticker
UNION ALL
SELECT 'debate_cache', ticker, COUNT(*), MAX(debate_date)::date
FROM debate_cache WHERE ticker = 'DELL' GROUP BY ticker;
